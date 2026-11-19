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


def test_delegate_lock_replaces_a_symlink_without_making_its_target(tmp_path: Path, home: Path) -> None:
    from scout import delegate_mirror as dm

    vault = tmp_path / "vault"
    target = tmp_path / "outside" / "pre-commit"
    _plant(vault / dm.LOCK_REL, target)
    with dm.locked(vault):
        _regular(vault / dm.LOCK_REL)
    assert not target.exists()


def test_delegate_tick_replaces_a_symlink_without_touching_its_target(tmp_path: Path, victim: Path) -> None:
    from scout import delegate_mirror as dm

    vault = tmp_path / "vault"
    _plant(vault / dm.TICK_REL, victim)
    old = victim.stat().st_mtime_ns
    dm.mark_started(vault)
    assert victim.stat().st_mtime_ns == old
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
    from scout.scripts import run_cost

    v = tmp_path / "vault"
    logs = v / ".scout-logs"
    logs.mkdir(parents=True, exist_ok=True)
    return [
        ("changes store", logs / "changes.jsonl", lambda: store._append(logs / "changes.jsonl", {"a": 1})),
        ("kb lint", logs / "kb-lint.jsonl", lambda: lint._append_jsonl(logs / "kb-lint.jsonl", [{"a": 1}])),
        ("run cost", logs / "tokens.jsonl", lambda: run_cost._append(logs / "tokens.jsonl", [{"a": 1}])),
        ("calibration", calibration.log_path(v), lambda: calibration.append_entry({"a": 1}, data_dir=v)),
        ("session tokens", session_tokens._tracker_path(v), lambda: session_tokens._append_row({"a": 1}, v)),
    ]  # fmt: skip


def test_every_append_writer_refuses_a_planted_log_link(
    tmp_path: Path, victim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "vault"))
    for name, log, append in _append_cases(tmp_path):
        _plant(log, victim)
        with contextlib.suppress(OSError):  # a best-effort writer swallows the refusal; the rest raise it
            append()
        _untouched(victim)
        assert log.is_symlink(), name


def test_the_heartbeat_runner_log_replaces_a_planted_link(
    tmp_path: Path, victim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout.scripts import heartbeat

    started: list[Any] = []

    def popen(*a: Any, **k: Any) -> _Pid:
        started.append(k["stdout"].fileno())
        return _Pid()

    monkeypatch.setattr(heartbeat.subprocess, "Popen", popen)
    log = _plant(tmp_path / ".scout-logs" / "heartbeat-runner.log", victim)
    assert heartbeat.launch_runner(tmp_path / "runner.sh", vault=tmp_path, log_path=log) == 1
    assert started
    _untouched(victim)
    _regular(log)


def test_a_lock_with_nowhere_to_record_still_refuses(tmp_path: Path) -> None:
    from scout.scripts import schedule_tick

    target = tmp_path / "outside" / "made-by-the-lock"
    lock = _plant(tmp_path / ".schedule-tick.lock", target)
    with pytest.raises(UnsafeFileError), schedule_tick._try_lock(lock):
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


# ----- every write call in the engine is safe_write, or a reviewed site -------------------------------------------

_WRITE_ATTRS = {"write_text", "write_bytes", "touch"}
_COPIES = {"copy", "copy2", "copyfile", "copytree", "copymode", "copystat"}


_READ_FLAGS = {"os", "O_RDONLY", "O_DIRECTORY", "_DIRECTORY", "O_CLOEXEC", "_CLOEXEC", "O_NONBLOCK", "_NONBLOCK"}


def _names(node: ast.AST) -> set[str]:
    """The names in a flags expression, ``getattr(os, "O_NOFOLLOW", 0)``'s string included."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
    return out - {"getattr"}


def _callee(call: ast.Call) -> str | None:
    """What a write call is, or None when it writes nothing a planted link could redirect."""
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
    owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
    if name in _WRITE_ATTRS and isinstance(f, ast.Attribute):
        return name
    if name in _COPIES and owner == "shutil":
        return f"shutil.{name}"
    if name == "open" and owner == "os":
        flags = _names(call.args[1]) if len(call.args) > 1 else set()
        if any("NOFOLLOW" in n for n in flags) or {"O_CREAT", "O_EXCL"} <= flags:
            return None  # never through a link: O_NOFOLLOW, or O_EXCL, which refuses any name already there
        return None if flags <= _READ_FLAGS else "os.open"
    if name == "open" and owner not in {"os", "tempfile", "webbrowser"}:
        modes = [a for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        modes += [k.value for k in call.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
        if any(isinstance(m, ast.Constant) and set(str(m.value)) & set("wxa+") for m in modes):
            return "open"
        if any(k.arg == "mode" and not isinstance(k.value, ast.Constant) for k in call.keywords):
            return "open"  # a mode chosen at run time: review it
    return None


def flagged_writes(root: Path) -> list[tuple[str, int]]:
    """Every write call under ``root`` as ``path::qualname::callee`` and its line."""
    out: list[tuple[str, int]] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root.parent).as_posix()

        def visit(node: ast.AST, qual: str, rel: str = rel) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    visit(child, f"{qual}.{child.name}" if qual else child.name, rel)
                    continue
                if isinstance(child, ast.Call) and (what := _callee(child)):
                    out.append((f"{rel}::{qual or '<module>'}::{what}", child.lineno))
                visit(child, qual, rel)

        visit(ast.parse(path.read_text(encoding="utf-8")), "")
    return out


# Each reviewed site, by file, function and call: why a planted link can't redirect it. Anything else that writes
# goes through scout.safe_write. A site that stops matching is an error too, so this list stays the truth.
_TMP = "a fresh temp directory of its own (mkdtemp / TemporaryDirectory): nothing else can name it"
_RELEASE = "release tooling: writes this repo's checkout at a maintainer's command, never in a run or the tick"
REVIEWED: dict[str, str] = {
    "scout/agent_runtimes/gate_probe.py::_fixture_vault::write_text": _TMP,
    "scout/agent_runtimes/gate_probe.py::probe::open": _TMP,
    "scout/kb/paths.py::_packaged_schema_path::write_bytes": _TMP,
    "scout/repo_sync.py::_merge3::write_bytes": _TMP,
    "scout/scripts/three_way_merge.py::three_way_merge::write_text": _TMP,
    "scout/sandbox/broker.py::_open_dir::os.open": "_DIR_FLAGS is O_RDONLY | O_DIRECTORY | O_NOFOLLOW",
    "scout/setup_wizard.py::TtyPrompter.open::os.open": "/dev/tty, the controlling terminal",
    "scout/scripts/changelog.py::assemble_changelogs::write_text": _RELEASE,
    "scout/scripts/connectors_snapshot.py::write_snapshot::write_text": _RELEASE,
    "scout/scripts/privacy_snapshot.py::write_snapshot::write_text": _RELEASE,
    "scout/scripts/release_notes.py::main::write_text": _RELEASE,
    "scout/scripts/schedule_snapshot.py::write_snapshot::write_text": _RELEASE,
    "scout/scripts/versioning.py::set_version::write_text": _RELEASE,
    "scout/scripts/versioning.py::_promote::write_text": _RELEASE,
}


def test_every_engine_write_is_safe_write_or_reviewed() -> None:
    found = flagged_writes(ENGINE / "scout")
    keys = {k for k, _ in found}
    unreviewed = sorted(f"{k} (line {line})" for k, line in found if k not in REVIEWED)
    assert not unreviewed, "write through scout.safe_write, or review the site and add it to REVIEWED:\n" + "\n".join(
        unreviewed
    )
    stale = sorted(set(REVIEWED) - keys)
    assert not stale, f"REVIEWED names sites that no longer write: {stale}"


def test_the_guard_sees_each_kind_of_write(tmp_path: Path) -> None:
    pkg = tmp_path / "scout"
    pkg.mkdir()
    (pkg / "m.py").write_text(
        "import os, shutil\n"
        "def f(p, m):\n"
        "    p.write_text('x'); p.write_bytes(b''); p.touch()\n"
        "    open(p, 'a'); p.open('w'); open(p, mode=m); open(p)\n"
        "    shutil.copy2(p, p); os.open(p, os.O_WRONLY | os.O_CREAT); os.open(p, os.O_RDONLY)\n"
        "    os.open(p, os.O_WRONLY | os.O_NOFOLLOW); os.open(p, flags); os.open(p, os.O_CREAT | os.O_EXCL)\n"
        "    os.open(p, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))\n"
        "class C:\n"
        "    def g(self, p):\n"
        "        p.write_text('y')\n",
        encoding="utf-8",
    )
    got = [k.split("::", 1)[1] for k, _ in flagged_writes(pkg)]
    assert got == [
        "f::write_text", "f::write_bytes", "f::touch", "f::open", "f::open", "f::open",
        "f::shutil.copy2", "f::os.open", "f::os.open", "C.g::write_text",
    ]  # fmt: skip


# ----- replacing an unsafe lock or log (a planted link must not stop the tick: review M3) --------------------------


def _recorder() -> tuple[list[tuple[str, str]], Any]:
    seen: list[tuple[str, str]] = []
    return seen, lambda path, why: seen.append((path.name, why))


def test_on_unsafe_replaces_a_symlinked_log_and_says_why(tmp_path: Path, victim: Path) -> None:
    seen, record = _recorder()
    log = _plant(tmp_path / "tick.jsonl", victim)
    with safe_write.open_append(log, on_unsafe=record) as f:
        f.write("row\n")
    _untouched(victim)
    _regular(log)
    assert log.read_text(encoding="utf-8") == "row\n"
    assert seen == [("tick.jsonl", "a symlink")]


def test_on_unsafe_replaces_a_hard_link_keeping_the_other_name(tmp_path: Path, victim: Path) -> None:
    seen, record = _recorder()
    log = tmp_path / "tick.lock"
    os.link(victim, log)
    with safe_write.open_lock(log, on_unsafe=record) as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    _untouched(victim)
    _regular(log)
    assert victim.stat().st_nlink == 1 and seen == [("tick.lock", "a hard link")]


def test_on_unsafe_replaces_a_fifo(tmp_path: Path) -> None:
    seen, record = _recorder()
    log = tmp_path / "events.jsonl"
    os.mkfifo(log)
    with safe_write.open_append(log, on_unsafe=record) as f:
        f.write("x\n")
    _regular(log)
    assert seen == [("events.jsonl", "something that isn't a regular file")]


def test_a_fifo_lock_is_refused_after_opening(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "x.lock")
    with pytest.raises(UnsafeFileError, match="regular file"):
        safe_write.open_lock(tmp_path / "x.lock")


def test_a_taken_temp_name_is_skipped(tmp_path: Path, victim: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = iter(["aaaa", "bbbb"])
    monkeypatch.setattr(safe_write.secrets, "token_hex", lambda n: next(names))
    _plant(tmp_path / ".state.json.aaaa.tmp", victim)
    safe_write.atomic_write_text(tmp_path / "state.json", "x")
    _untouched(victim)
    assert (tmp_path / "state.json").read_text(encoding="utf-8") == "x"


def test_on_unsafe_never_removes_a_folder(tmp_path: Path) -> None:
    d = tmp_path / "x.lock"
    d.mkdir()
    (d / "keep").write_text("k", encoding="utf-8")
    err = UnsafeFileError(d, "a symlink")
    with pytest.raises(UnsafeFileError):
        safe_write._make_again(d, os.O_RDWR | os.O_CREAT, 0o600, err)
    assert (d / "keep").exists()


def test_on_unsafe_refuses_when_the_link_comes_straight_back(
    tmp_path: Path, victim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _plant(tmp_path / "tick.jsonl", victim)
    monkeypatch.setattr(safe_write.os, "unlink", lambda p: None)  # as if the run put it back at once
    with pytest.raises(UnsafeFileError):
        safe_write.open_append(log, on_unsafe=lambda p, w: None)
    _untouched(victim)


def test_on_unsafe_refuses_when_the_name_cannot_be_removed(
    tmp_path: Path, victim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _plant(tmp_path / "tick.lock", victim)

    def refuse(p: Any) -> None:
        raise PermissionError("immutable")

    monkeypatch.setattr(safe_write.os, "unlink", refuse)
    with pytest.raises(UnsafeFileError):
        safe_write.open_lock(log, on_unsafe=lambda p, w: None)
    _untouched(victim)


def test_on_unsafe_copes_with_the_name_vanishing_meanwhile(
    tmp_path: Path, victim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _plant(tmp_path / "tick.jsonl", victim)
    real_unlink = os.unlink

    def gone_already(p: Any) -> None:
        real_unlink(p)
        raise FileNotFoundError(p)

    monkeypatch.setattr(safe_write.os, "unlink", gone_already)
    with safe_write.open_append(log, on_unsafe=lambda p, w: None) as f:
        f.write("x")
    _regular(log)

    monkeypatch.undo()
    link = _plant(tmp_path / "other.jsonl", victim)

    def nothing_there(p: Any) -> os.stat_result:
        raise FileNotFoundError(p)

    monkeypatch.setattr(safe_write.os, "lstat", nothing_there)
    with pytest.raises(UnsafeFileError):  # lstat saw nothing, yet the O_EXCL open finds the link: refuse
        safe_write.open_append(link, on_unsafe=lambda p, w: None)
    _untouched(victim)


def test_a_failing_recorder_does_not_fail_the_write(tmp_path: Path, victim: Path) -> None:
    def boom(path: Path, why: str) -> None:
        raise RuntimeError("log full")

    log = _plant(tmp_path / "tick.jsonl", victim)
    with safe_write.open_append(log, on_unsafe=boom) as f:
        f.write("row\n")
    assert log.read_text(encoding="utf-8") == "row\n"


def test_the_tick_still_fires_through_a_planted_lock_log_and_tracker(
    tmp_path: Path, victim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import patch

    from scout.scripts import schedule_tick as st

    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    state, logs = tmp_path / ".scout-state", tmp_path / ".scout-logs"
    state.mkdir()
    logs.mkdir()
    (state / "schedule.yaml").write_text(
        "schema_version: 1\nslots:\n  smoke-slot:\n"
        "    type: manual\n    runner: run-scout.sh\n    fires_at_local: '00:01'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]\n"
        "    missed_window_hours: 24\n    on_miss: skip\n    cooldown_minutes: 5\n",
        encoding="utf-8",
    )
    lock_target = tmp_path / "outside" / "made-by-the-lock"
    _plant(state / st.LOCK_FILENAME, lock_target)
    _plant(logs / st.TRACKER_FILENAME, victim)
    day = datetime.now(UTC).date()
    for d in (day, day + timedelta(days=1)):  # either side of midnight UTC
        os.link(victim, logs / f"{st.EVENT_LOG_PREFIX}{d.isoformat()}.jsonl")
    with (
        patch("scout.scripts.schedule_tick._network_ready", return_value=True),
        patch("scout.scripts.schedule_tick.subprocess.Popen") as popen,
        patch("scout.triggers.engine.evaluate"),
    ):
        popen.return_value.pid = 4242
        ev = st.run()

    assert ev.kind == "schedule.tick.completed"
    _untouched(victim)
    assert not lock_target.exists()
    _regular(state / st.LOCK_FILENAME)
    rows = [
        json.loads(line)
        for p in logs.glob(f"{st.EVENT_LOG_PREFIX}*.jsonl")
        if p.stat().st_nlink == 1
        for line in p.read_text(encoding="utf-8").splitlines()
    ]
    kinds = {"schedule.tick.unsafe_file_replaced", "events.unsafe_file_replaced"}  # the log replaces itself
    replaced = {r["payload"]["file"] for r in rows if r["kind"] in kinds}
    assert st.LOCK_FILENAME in replaced and any(f.startswith(st.EVENT_LOG_PREFIX) for f in replaced)
    assert popen.called  # the slot fired: its tracker row went to a fresh file, not the victim
    assert st.TRACKER_FILENAME in replaced
    _regular(logs / st.TRACKER_FILENAME)


def test_events_log_replaces_a_planted_link_and_records_it(tmp_path: Path, victim: Path) -> None:
    from scout import events

    day = datetime.now(UTC).date()
    for d in (day, day + timedelta(days=1)):
        _plant(tmp_path / f"{events.EVENT_LOG_PREFIX}{d.isoformat()}.jsonl", victim)
    ev = events.append_event(tmp_path, kind="privacy.check_failed", source="t", payload={"a": 1})
    _untouched(victim)
    log = tmp_path / f"{events.EVENT_LOG_PREFIX}{ev.ts[:10]}.jsonl"
    kinds = [json.loads(line)["kind"] for line in log.read_text(encoding="utf-8").splitlines()]
    assert kinds == [events.UNSAFE_REPLACED, "privacy.check_failed"]


class _Pid:
    pid = 1


def test_scheduled_locks_and_logs_heal_and_record(tmp_path: Path, victim: Path, home: Path) -> None:
    from scout import delegate_mirror as dm
    from scout import health_notify, update_outcomes
    from scout.scripts import heartbeat, self_update_run

    v = tmp_path / "vault"
    logs = v / ".scout-logs"
    logs.mkdir(parents=True)
    target = tmp_path / "outside" / "made-by-the-lock"
    for rel in (health_notify.LOCK_FILE, self_update_run.LOCK_NAME, dm.LOCK_REL, dm.TICK_REL):
        _plant(v / rel, target)
    _plant(logs / "heartbeat.log", victim)
    _plant(v / update_outcomes.LEDGER, victim)
    _plant(tmp_path / "su.log", victim)

    handle = health_notify._acquire_lock(v, timeout=0, sleep=lambda s: None)
    assert handle is not None
    handle.close()
    with self_update_run.run_lock(v) as held:
        assert held is True
    with dm.locked(v):
        pass
    dm.mark_started(v)
    heartbeat._log_line(logs / "heartbeat.log", "why")
    started: list[Any] = []

    def popen(*a: Any, **k: Any) -> _Pid:
        started.append(a)
        return _Pid()

    self_update_run.spawn_detached(["true"], log_path=tmp_path / "su.log", popen=popen)

    assert not target.exists()
    _untouched(victim)
    assert started
    rows = [
        json.loads(line)
        for p in logs.glob("schedule-events-*.jsonl")
        for line in p.read_text(encoding="utf-8").splitlines()
    ]
    assert {
        "health_notify.unsafe_file_replaced",
        "self_update.unsafe_file_replaced",
        "delegate_mirror.unsafe_file_replaced",
        "heartbeat.unsafe_file_replaced",
    } <= {r["kind"] for r in rows}


def test_delegate_lock_still_refuses_when_it_cannot_replace(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout import delegate_mirror as dm
    from scout.repo_sync import RepoSyncError

    vault = tmp_path / "vault"
    _plant(vault / dm.LOCK_REL, tmp_path / "outside" / "x")
    monkeypatch.setattr(safe_write.os, "unlink", lambda p: None)
    with pytest.raises(RepoSyncError) as e, dm.locked(vault):
        pass
    assert e.value.code == "state_unsafe"


# ----- the review's must-fix writers (M1, M2) and the should-fix ones ----------------------------------------------


def test_retitle_writes_never_follow_a_planted_temp_or_file_link(tmp_path: Path, victim: Path) -> None:
    from scout.action_items import retitle

    vault = tmp_path / "vault"
    daily = vault / "action-items" / "action-items-2026-11-19.md"
    daily.parent.mkdir(parents=True)
    daily.write_text("before\n", encoding="utf-8")
    _plant(daily.with_name(f".{daily.name}.retitle-tmp"), victim)
    linked = _plant(vault / "action-items" / "context" / "x.md", victim)

    tx = retitle._Transaction(vault)
    tx.write(daily, "after\n")
    tx.write(linked, "new note\n")
    _untouched(victim)
    _regular(daily)
    _regular(linked)
    assert daily.read_text(encoding="utf-8") == "after\n"

    tx.rollback()
    assert daily.read_text(encoding="utf-8") == "before\n"
    assert linked.is_symlink() and os.readlink(linked) == str(victim)  # put back as the link, never read through
    _untouched(victim)


def test_retitle_worksheet_save_replaces_a_link(tmp_path: Path, victim: Path) -> None:
    from scout.action_items import retitle

    path = _plant(tmp_path / ".scout-cache" / "retitle" / "2026-11-19.json", victim)
    retitle.Worksheet(date=datetime(2026, 11, 19).date(), rows=[], path=path).save()
    _untouched(victim)
    _regular(path)


def test_scout_config_writer_replaces_a_dangling_link_and_keeps_the_mode(tmp_path: Path) -> None:
    from scout.scripts import privacy_migrate

    config = tmp_path / "scout-config.yaml"
    dangling = tmp_path / "outside" / "made-by-the-write"
    _plant(config, dangling)
    privacy_migrate._atomic_write_bytes(config, b"privacy: {}\n")
    assert not dangling.exists()
    _regular(config)
    config.chmod(0o640)
    privacy_migrate._atomic_write_bytes(config, b"privacy: {local_only_add: []}\n")
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
