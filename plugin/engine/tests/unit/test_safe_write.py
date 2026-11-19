"""scout.safe_write and the writers that use it: a symlink or hard link planted where an unsandboxed writer puts
a temp file, a lock or a log is never written through (the #718 security review: a run planted
``.scout-state/delegate-mirrors.json.tmp -> <a protected file>`` and the next owner save overwrote the target)."""

from __future__ import annotations

import ast
import contextlib
import fcntl
import json
import os
import stat
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scout import safe_write
from scout.safe_write import UnsafeFileError

ENGINE = Path(__file__).resolve().parents[2]
KEEP = "protected content\n"


@pytest.fixture
def victim(tmp_path: Path) -> Path:
    """A file the run must not be able to reach (stands in for hooks.json, state.key, a git hook)."""
    v = tmp_path / "outside" / "victim.txt"
    v.parent.mkdir()
    v.write_text(KEEP, encoding="utf-8")
    return v


def _plant(link: Path, target: Path) -> Path:
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return link


def _untouched(victim: Path) -> None:
    assert victim.read_text(encoding="utf-8") == KEEP


def _regular(path: Path) -> None:
    st = path.lstat()
    assert stat.S_ISREG(st.st_mode) and st.st_nlink == 1, path


# ----- the helper ------------------------------------------------------------------------------------------------


def test_atomic_write_replaces_a_symlink_at_the_target_without_following_it(tmp_path: Path, victim: Path) -> None:
    target = _plant(tmp_path / "state.json", victim)
    safe_write.atomic_write_text(target, "new\n")
    _untouched(victim)
    _regular(target)
    assert target.read_text(encoding="utf-8") == "new\n"


def test_atomic_write_replaces_a_hard_link_at_the_target_without_writing_into_it(tmp_path: Path, victim: Path) -> None:
    target = tmp_path / "state.json"
    os.link(victim, target)
    safe_write.atomic_write_bytes(target, b"new")
    _untouched(victim)
    _regular(target)
    assert target.read_bytes() == b"new"


def test_atomic_write_never_uses_a_name_a_run_could_have_planted(tmp_path: Path, victim: Path) -> None:
    """Every fixed temp name the engine used before is a symlink here; none is opened."""
    target = tmp_path / "delegate-mirrors.json"
    for name in (
        "delegate-mirrors.json.tmp",
        "delegate-mirrors.tmp",
        f"delegate-mirrors.json.{os.getpid()}.tmp",
        f".delegate-mirrors.json.{os.getpid()}.tmp",
    ):
        _plant(tmp_path / name, victim)
    safe_write.atomic_write_text(target, "{}\n")
    _untouched(victim)
    assert target.read_text(encoding="utf-8") == "{}\n"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".delegate-mirrors.json.") and not p.is_symlink()]


def test_atomic_write_modes(tmp_path: Path) -> None:
    kept = tmp_path / "kept"
    kept.write_text("old", encoding="utf-8")
    kept.chmod(0o640)
    safe_write.atomic_write_text(kept, "new")
    assert stat.S_IMODE(kept.stat().st_mode) == 0o640

    forced = tmp_path / "forced"
    safe_write.atomic_write_text(forced, "x", mode=0o600)
    assert stat.S_IMODE(forced.stat().st_mode) == 0o600

    old_umask = os.umask(0o027)
    try:
        fresh = tmp_path / "fresh"
        safe_write.atomic_write_text(fresh, "x", fsync=False)
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o640  # as open() would make it: 0666 less the umask


def test_atomic_write_failure_leaves_the_target_and_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")

    def refuse(*a: Any, **k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(safe_write.os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        safe_write.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_atomic_write_survives_a_folder_that_cannot_be_synced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = os.fsync

    def fsync(fd: int) -> None:
        calls.append(fd)
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("not supported")
        real(fd)

    monkeypatch.setattr(safe_write.os, "fsync", fsync)
    safe_write.atomic_write_text(tmp_path / "f", "x")
    assert (tmp_path / "f").read_text(encoding="utf-8") == "x" and len(calls) == 2


def test_atomic_write_needs_an_existing_folder(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        safe_write.atomic_write_text(tmp_path / "missing" / "f", "x")


def test_unsafe_file_error_is_an_oserror_naming_the_path(tmp_path: Path, victim: Path) -> None:
    link = _plant(tmp_path / "log", victim)
    with pytest.raises(OSError) as e:
        safe_write.open_append(link)
    assert isinstance(e.value, UnsafeFileError) and e.value.filename == str(link)
    assert "symlink" in str(e.value)


def test_open_append_refuses_a_symlink_a_dangling_symlink_and_a_hard_link(tmp_path: Path, victim: Path) -> None:
    with pytest.raises(UnsafeFileError):
        safe_write.open_append(_plant(tmp_path / "a.log", victim))
    dangling = tmp_path / "outside" / "made-by-the-write"
    with pytest.raises(UnsafeFileError):
        safe_write.open_append(_plant(tmp_path / "b.log", dangling))
    assert not dangling.exists()
    os.link(victim, tmp_path / "c.log")
    with pytest.raises(UnsafeFileError, match="hard link"):
        safe_write.open_append(tmp_path / "c.log")
    _untouched(victim)


def test_open_append_refuses_a_fifo_without_hanging(tmp_path: Path) -> None:
    fifo = tmp_path / "events.jsonl"
    os.mkfifo(fifo)
    done: list[BaseException] = []

    def attempt() -> None:
        try:
            safe_write.open_append(fifo)
        except BaseException as e:  # noqa: BLE001 - recorded for the assertion
            done.append(e)

    t = threading.Thread(target=attempt, daemon=True)
    t.start()
    t.join(5)
    assert done and isinstance(done[0], OSError)


def test_open_append_appends_text_and_bytes(tmp_path: Path) -> None:
    log = tmp_path / "x.jsonl"
    with safe_write.open_append(log) as f:
        f.write("one\n")
    with safe_write.open_append(log, binary=True, mode=0o600) as f:
        f.write(b"two\n")
    assert log.read_text(encoding="utf-8") == "one\ntwo\n"


def test_open_append_keeps_working_across_log_rotation(tmp_path: Path) -> None:
    """A rotated log (renamed aside, or copied and truncated in place) is a plain one-link file again."""
    log = tmp_path / "heartbeat.log"
    with safe_write.open_append(log) as f:
        f.write("before\n")
    log.rename(tmp_path / "heartbeat.log.1")
    with safe_write.open_append(log) as f:
        f.write("after rename\n")
    assert log.read_text(encoding="utf-8") == "after rename\n"
    assert (tmp_path / "heartbeat.log.1").read_text(encoding="utf-8") == "before\n"

    (tmp_path / "heartbeat.log.2").write_bytes(log.read_bytes())
    os.truncate(log, 0)
    with safe_write.open_append(log) as f:
        f.write("after truncate\n")
    assert log.read_text(encoding="utf-8") == "after truncate\n"


def test_open_lock_refuses_a_symlink_and_never_makes_its_target(tmp_path: Path) -> None:
    target = tmp_path / "outside" / "hooks.json"
    lock = _plant(tmp_path / "x.lock", target)
    with pytest.raises(UnsafeFileError):
        safe_write.open_lock(lock)
    assert not target.exists()


def test_open_lock_refuses_a_folder_and_locks_a_plain_file(tmp_path: Path) -> None:
    (tmp_path / "d.lock").mkdir()
    with pytest.raises(OSError):
        safe_write.open_lock(tmp_path / "d.lock")
    with safe_write.open_lock(tmp_path / "x.lock") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert stat.S_IMODE(os.fstat(fh.fileno()).st_mode) == 0o600
    assert (tmp_path / "x.lock").read_bytes() == b""


def test_open_checked_reraises_other_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        safe_write.open_append(tmp_path / "missing" / "x.log")


# ----- the delegate mirror state (the reported bypass) -----------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def test_save_links_does_not_follow_a_planted_temp_file(tmp_path: Path, home: Path, victim: Path) -> None:
    from scout import delegate_mirror as dm

    vault = tmp_path / "vault"
    _plant(vault / ".scout-state" / "delegate-mirrors.json.tmp", victim)
    dm.save_links(vault, {})
    _untouched(victim)
    _regular(dm.state_path(vault))
    assert dm.load_links(vault) == {}


def test_save_links_does_not_overwrite_the_state_key(tmp_path: Path, home: Path) -> None:
    from scout import delegate_mirror as dm

    vault = tmp_path / "vault"
    dm.save_links(vault, {})  # makes the key
    key = dm.secrets_dir() / dm.STATE_KEY
    before = key.read_bytes()
    (vault / ".scout-state" / "delegate-mirrors.json.tmp").symlink_to(key)
    dm.save_links(vault, {})
    assert key.read_bytes() == before


def test_delegate_lock_refuses_a_symlink(tmp_path: Path, home: Path) -> None:
    from scout import delegate_mirror as dm
    from scout.repo_sync import RepoSyncError

    vault = tmp_path / "vault"
    target = tmp_path / "outside" / "pre-commit"
    _plant(vault / dm.LOCK_REL, target)
    with pytest.raises(RepoSyncError) as e, dm.locked(vault):
        pass
    assert e.value.code == "state_unsafe"
    assert not target.exists()
    (vault / dm.LOCK_REL).unlink()
    with dm.locked(vault):
        _regular(vault / dm.LOCK_REL)


def test_delegate_tick_refuses_a_symlink(tmp_path: Path, victim: Path) -> None:
    from scout import delegate_mirror as dm

    vault = tmp_path / "vault"
    _plant(vault / dm.TICK_REL, victim)
    old = victim.stat().st_mtime_ns
    with pytest.raises(UnsafeFileError):
        dm.mark_started(vault)
    assert victim.stat().st_mtime_ns == old
    (vault / dm.TICK_REL).unlink()
    dm.mark_started(vault)
    _regular(vault / dm.TICK_REL)


def test_delegate_ensure_ignored_replaces_a_symlinked_gitignore(tmp_path: Path, victim: Path) -> None:
    from scout import delegate_mirror as dm

    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    _plant(vault / ".gitignore", victim)
    assert dm.ensure_ignored(vault) is True
    _untouched(victim)
    _regular(vault / ".gitignore")
    assert dm.STATE_REL in (vault / ".gitignore").read_text(encoding="utf-8")


def test_delegate_push_token_does_not_follow_a_symlink(tmp_path: Path, home: Path, victim: Path) -> None:
    from scout import delegate_mirror as dm

    vault = tmp_path / "vault"
    vault.mkdir()
    _plant(dm.push_token_path(vault), victim)
    dm.save_push_token(vault, "tok")
    _untouched(victim)
    _regular(dm.push_token_path(vault))
    assert stat.S_IMODE(dm.push_token_path(vault).stat().st_mode) == 0o600


# ----- the other atomic writers ----------------------------------------------------------------------------------


def _atomic_cases(tmp_path: Path) -> list[tuple[str, Path, list[str], Any]]:
    """(name, the file written, the fixed temp names it used before, a call that writes it)."""
    from scout import health_notify, repo_sync
    from scout.kb import read as kb_read
    from scout.planning import calibration
    from scout.sandbox import broker
    from scout.scripts import (
        bootstrap,
        connector_health_report,
        notify_telegram,
        pre_session_data,
        schedule_tick,
        vault_drift,
    )
    from scout.triggers.dedup import DedupStore

    v = tmp_path / "vault"
    (v / ".scout-state").mkdir(parents=True)
    (v / ".scout-logs").mkdir()
    (v / ".scout-cache").mkdir()
    pid = os.getpid()
    env = {"HOME": str(tmp_path / "home")}
    runs = broker.owner_dir(env) / broker.RUNS_DIR_NAME
    runs.mkdir(parents=True)
    now = datetime(2026, 11, 19, 12, tzinfo=UTC)
    state = v / ".scout-state"
    cal = calibration.log_path(v)
    cal.parent.mkdir(parents=True, exist_ok=True)
    cal.write_text(json.dumps({"tag": "T", "date": "2026-11-18"}) + "\n", encoding="utf-8")
    return [
        ("repo_sync pending", state / repo_sync.PENDING_FILE, [f"{repo_sync.PENDING_FILE}.tmp"],
         lambda: repo_sync.save_pending_pushes(v, [("a", "b")])),
        ("repo_sync host cache", state / repo_sync.HOST_CACHE_FILE, [f"{repo_sync.HOST_CACHE_FILE}.tmp"],
         lambda: repo_sync.save_host_cache(v, ["x"], agnes_url="https://agnes.example")),
        ("health_notify state", v / health_notify.STATE_FILE, ["health-notify.json.tmp"],
         lambda: health_notify.save_state(v, {"a": 1})),
        ("dedup", state / "trigger-dedup.json", ["trigger-dedup.json.tmp"],
         lambda: DedupStore(state / "trigger-dedup.json").mark_cap_notified("t", now)),
        ("kb read index", v / ".scout-cache" / "kb-index.json", [f".kb-index.json.{pid}.tmp"],
         lambda: kb_read._write_index(v / ".scout-cache" / "kb-index.json", "p", {})),
        ("broker status", v / ".scout-logs" / broker.STATUS_FILE, [f".{broker.STATUS_FILE}.{pid}.tmp"],
         lambda: broker.write_status(v, "on", "ok")),
        ("broker run record", runs / "r1.json", [f".r1.json.{pid}.tmp"],
         lambda: broker._write_record(env, "r1", {"open": True})),
        ("broker sends", broker.owner_dir(env) / broker.TELEGRAM_LOG_NAME,
         [f".{broker.TELEGRAM_LOG_NAME}.{pid}.tmp"], lambda: broker._write_sends(env, [])),
        ("telegram sent log", state / "telegram-sent.jsonl", ["telegram-sent.jsonl.tmp"],
         lambda: notify_telegram._record_sent(state / "telegram-sent.jsonl", [], {"key": "k"})),
        ("tick last-fire cache", state / schedule_tick.LAST_FIRE_CACHE_FILENAME, ["last-fire.json.tmp"],
         lambda: schedule_tick._write_last_fire_cache(state, {}, state / "t.jsonl", state / "s.jsonl")),
        ("pre-session kb cache", v / ".scout-cache" / "kb-dates.json", ["kb-dates.json.tmp"],
         lambda: pre_session_data._write_kb_dates_cache(v / ".scout-cache" / "kb-dates.json", {})),
        ("connector index", v / ".scout-cache" / "conn-index.json", [f".conn-index.json.{pid}.tmp"],
         lambda: connector_health_report._write_index(v / ".scout-cache" / "conn-index.json", {})),
        ("bootstrap", v / "scripts" / "run.sh", ["run.sh.tmp"],
         lambda: bootstrap._atomic_write(v / "scripts" / "run.sh", "#!/bin/sh\n")),
        ("vault drift", v / "brain.md", ["brain.md.tmp"], lambda: vault_drift._write(v / "brain.md", "x\n")),
        ("calibration", cal, [f"{cal.name}.tmp"],
         lambda: calibration.record_entry({"tag": "T", "date": "2026-11-18", "actual": 1}, data_dir=v)),
    ]  # fmt: skip


def test_every_atomic_writer_ignores_planted_temp_names_and_targets(
    tmp_path: Path, victim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cases = _atomic_cases(tmp_path)
    for name, target, old_tmps, write in cases:
        target.parent.mkdir(parents=True, exist_ok=True)
        for old in old_tmps:
            _plant(target.parent / old, victim)
        if not target.exists():
            _plant(target, victim)  # a run that can write the folder can put a link at the file itself
        write()
        _untouched(victim)
        _regular(target)
        for old in old_tmps:
            assert (target.parent / old).is_symlink(), f"{name}: the planted link was opened or removed"


def test_repo_lease_machine_ref_removes_a_planted_temp_link(home: Path, victim: Path) -> None:
    from scout import repo_lease

    ref_path = home / ".scout-secrets" / "agnes-knowledge-repo" / repo_lease.MACHINE_ID_FILE
    _plant(ref_path.with_name(f"{ref_path.name}.{os.getpid()}.tmp"), victim)
    ref = repo_lease.machine_ref()
    _untouched(victim)
    assert ref_path.read_text(encoding="utf-8").strip() == ref


# ----- appends and locks -----------------------------------------------------------------------------------------


def _append_cases(tmp_path: Path) -> list[tuple[str, Path, Any]]:
    from scout.changes import store
    from scout.hooks import session_tokens
    from scout.kb import lint
    from scout.planning import calibration
    from scout.scripts import heartbeat, run_cost, schedule_tick

    v = tmp_path / "vault"
    logs = v / ".scout-logs"
    logs.mkdir(parents=True, exist_ok=True)
    day = datetime.now(UTC).date()
    return [
        ("changes store", logs / "changes.jsonl", lambda: store._append(logs / "changes.jsonl", {"a": 1})),
        ("kb lint", logs / "kb-lint.jsonl", lambda: lint._append_jsonl(logs / "kb-lint.jsonl", [{"a": 1}])),
        ("run cost", logs / "tokens.jsonl", lambda: run_cost._append(logs / "tokens.jsonl", [{"a": 1}])),
        ("calibration", calibration.log_path(v), lambda: calibration.append_entry({"a": 1}, data_dir=v)),
        *(
            (f"tick events {d}", logs / f"schedule-events-{d.isoformat()}.jsonl",
             lambda: schedule_tick._emit_event(logs, kind="k", source="s", payload={}))
            for d in (day, day + timedelta(days=1))
        ),
        ("session tokens", session_tokens._tracker_path(v), lambda: session_tokens._append_row({"a": 1}, v)),
        ("heartbeat log", logs / "heartbeat.log", lambda: heartbeat._log_line(logs / "heartbeat.log", "why")),
    ]  # fmt: skip


def test_every_append_writer_refuses_a_planted_log_link(
    tmp_path: Path, victim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "vault"))
    for name, log, append in _append_cases(tmp_path):
        if log.is_symlink():
            continue  # the second tick-events case shares a day with the first near midnight
        _plant(log, victim)
        with contextlib.suppress(OSError):  # a best-effort writer swallows the refusal; the rest raise it
            append()
        _untouched(victim)
        assert log.is_symlink(), name


def test_spawned_logs_refuse_a_planted_link(tmp_path: Path, victim: Path) -> None:
    from scout.scripts import heartbeat, self_update_run

    started: list[Any] = []
    log = _plant(tmp_path / "self-update.log", victim)
    with pytest.raises(UnsafeFileError):
        self_update_run.spawn_detached(["true"], log_path=log, popen=lambda *a, **k: started.append(a))
    with pytest.raises(UnsafeFileError):
        heartbeat.launch_runner(tmp_path / "runner.sh", vault=tmp_path, log_path=log)
    assert not started
    _untouched(victim)


def test_locks_refuse_a_planted_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout import health_notify
    from scout.scripts import schedule_tick, self_update_run

    v = tmp_path / "vault"
    target = tmp_path / "outside" / "made-by-the-lock"
    for rel in (health_notify.LOCK_FILE, self_update_run.LOCK_NAME, ".scout-logs/.schedule-tick.lock"):
        _plant(v / rel, target)
    with pytest.raises(UnsafeFileError):
        health_notify._acquire_lock(v, timeout=0, sleep=lambda s: None)
    with pytest.raises(UnsafeFileError), self_update_run.run_lock(v):
        pass
    with pytest.raises(UnsafeFileError), schedule_tick._try_lock(v / ".scout-logs/.schedule-tick.lock"):
        pass
    assert not target.exists()


def test_tick_lock_still_excludes_a_second_holder(tmp_path: Path) -> None:
    from scout.scripts import schedule_tick

    lock = tmp_path / "tick.lock"
    with schedule_tick._try_lock(lock) as first:
        assert first is True
        with schedule_tick._try_lock(lock) as second:
            assert second is False
    with schedule_tick._try_lock(lock) as again:
        assert again is True


# ----- no fixed temp name or plain append creeps back --------------------------------------------------------------

# Writers that still open a fixed name are allowed only where the name is a lock or file a protected folder holds,
# opened O_NOFOLLOW with dir_fd, as these modules already did before scout.safe_write.
_ALLOWED = {
    "scout/safe_write.py",
    "scout/delegate_inbox.py",
    "scout/delegate_agent.py",
    "scout/events.py",
    "scout/repo_lease.py",  # machine_ref: O_EXCL | O_NOFOLLOW after unlinking its own stale temp file
}


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _is_append_open(call: ast.Call) -> bool:
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
    if name != "open":
        return False
    modes = [a for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    modes += [k.value for k in call.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]  # type: ignore[misc]
    return any(isinstance(m, ast.Constant) and "a" in str(m.value) for m in modes)


def test_no_engine_writer_opens_a_log_or_lock_with_a_plain_append() -> None:
    offenders = []
    for path in sorted((ENGINE / "scout").rglob("*.py")):
        rel = path.relative_to(ENGINE).as_posix()
        if rel in _ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        for call in _calls(ast.parse(text)):
            if _is_append_open(call):
                offenders.append(f"{rel}:{call.lineno}")
        for i, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            if "O_APPEND" in code and "O_NOFOLLOW" not in code:
                offenders.append(f"{rel}:{i}")
    assert not offenders, f"use scout.safe_write.open_append / open_lock: {offenders}"


def test_no_engine_writer_writes_a_fixed_temp_name() -> None:
    """``x.tmp`` then ``os.replace``/``.replace``: the temp file must come from safe_write or mkstemp."""
    offenders = []
    for path in sorted((ENGINE / "scout").rglob("*.py")):
        rel = path.relative_to(ENGINE).as_posix()
        if rel in _ALLOWED:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if ('.tmp"' in code or ".tmp'" in code) and ("with_suffix" in code or "with_name" in code or "/ f" in code):
                offenders.append(f"{rel}:{i}")
    assert not offenders, f"use scout.safe_write.atomic_write_text: {offenders}"
