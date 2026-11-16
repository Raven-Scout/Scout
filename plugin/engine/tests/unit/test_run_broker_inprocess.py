"""The run brokers in this process (Phase 1d): the server, the clients, a real commit, a Telegram dry run, the
profile check and the CLI, on any OS.

The integration and macOS tests run the same paths as separate processes; these keep them measured where those
can't be (Linux CI). Each test asserts a property the broker keeps: a commit lands with its SHA back, a request
that can't be carried out is answered rather than crashing, a profile that can't start the engine is not used.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scout.sandbox import broker
from scout.sandbox import profile as sbx
from scout.scripts.phase_assembly import render_template

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
GIT_ID = {
    "GIT_AUTHOR_NAME": "Scout Bot",
    "GIT_AUTHOR_EMAIL": "alex@example.com",
    "GIT_COMMITTER_NAME": "Scout Bot",
    "GIT_COMMITTER_EMAIL": "alex@example.com",
}


def _git(vault: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ID, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", "-C", str(vault), *args], env=env, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = (tmp_path / "vault").resolve()
    for d in ("notes", "scripts", ".scout-logs", ".scout-state"):
        (v / d).mkdir(parents=True)
    home = Path(os.environ["HOME"])
    (home / ".gitconfig").write_text("[user]\n\tname = Scout Bot\n\temail = alex@example.com\n")
    _git(v, "init", "-q", "-b", "main")
    helper = v / "scripts" / "git-safe-commit.sh"
    template = (TEMPLATES / "scripts" / "git-safe-commit.sh.tmpl").read_text()
    helper.write_text(render_template(template, {"SCOUT_DIR": str(v)}))
    helper.chmod(0o755)
    (v / ".gitignore").write_text(".scout-cache/\n.scout-logs/\n.scout-state/\n")
    (v / "notes" / "seed.md").write_text("seed\n")
    _git(v, "add", "-A")
    _git(v, "commit", "-qm", "seed", "--no-verify")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


@pytest.fixture
def broker_dir() -> Iterator[Path]:
    d = Path(tempfile.mkdtemp(prefix="scout-broker.", dir="/tmp")).resolve()
    d.chmod(0o700)
    yield d
    import shutil

    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def keep_signals() -> Iterator[None]:
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield
    for s, handler in saved.items():
        signal.signal(s, handler)


def _secrets() -> None:
    secrets = Path(os.environ["HOME"]) / ".scout-secrets"
    secrets.mkdir(exist_ok=True)
    for name, value in (("telegram-bot-token", "123:abc"), ("telegram-chat-id", "4242")):
        (secrets / name).write_text(value + "\n")
        (secrets / name).chmod(0o600)


class Sender:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []

    def __call__(self, fields: Mapping[str, Any]) -> broker.Reply:
        self.calls.append(dict(fields))
        return broker.Reply(0, "id=01TEST\n", "{}")


# -- a real commit, a real dry run -------------------------------------------------------------------------------


def test_a_commit_lands_and_its_sha_comes_back(vault: Path) -> None:
    (vault / "notes" / "a.md").write_text("a\n")
    reply = broker.Broker.for_vault(vault).handle(
        {"v": 1, "verb": "commit", "message": "notes: a", "paths": ["notes/a.md"]}
    )
    assert reply.exit == 0, reply.stderr
    assert f"committed {_git(vault, 'rev-parse', '--short', 'HEAD').strip()}" in reply.stderr


def test_a_commit_that_runs_too_long_is_stopped_with_its_helper(vault: Path) -> None:
    (vault / "scripts" / "git-safe-commit.sh").write_text("#!/bin/bash\nsleep 30\n")
    reply = broker.run_commit(vault, "m", ["notes/a.md"], None, dict(os.environ), timeout=0.5)
    assert reply.exit == 5 and "longer than" in reply.stderr


def test_a_vault_without_the_helper_answers_instead_of_crashing(vault: Path) -> None:
    (vault / "scripts" / "git-safe-commit.sh").unlink()
    assert broker.run_commit(vault, "m", ["notes/a.md"], None, dict(os.environ)).exit == 5


def test_a_telegram_dry_run_reaches_the_owners_chat_id(vault: Path) -> None:
    _secrets()
    fields = {"tier": "info", "parse_mode": "none", "body": "wrap", "allow_duplicate": False, "dry_run": True}
    reply = broker.send_telegram(fields)
    assert reply.exit == 0, reply.stderr
    assert reply.stderr.startswith("id=") and '"chat_id": "4242"' in reply.stderr


# -- the server and its clients, in this process -----------------------------------------------------------------


def test_the_server_answers_its_clients_until_its_parent_is_gone(
    vault: Path,
    broker_dir: Path,
    keep_signals: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(broker, "PARENT_POLL_S", 0.2)
    parent = subprocess.Popen(["/bin/sleep", "60"])
    sender = Sender()
    served = broker.Broker.for_vault(vault, sender=sender, run_id=broker_dir.name)
    sock = str(broker_dir / broker.SOCKET_NAME)
    env = {**os.environ, broker.ENV_SOCKET: sock}
    results: dict[str, Any] = {}

    def client() -> None:
        try:
            assert broker.wait_for(Path(sock), timeout=20)
            (vault / "notes" / "b.md").write_text("b\n")
            results["commit"] = broker.client_commit("notes: b", ["notes/b.md"], env=env)
            fields = {"tier": "info", "parse_mode": "none", "body": "wrap", "allow_duplicate": False}
            results["telegram"] = broker.client_telegram({**fields, "dry_run": False}, env=env)
            results["refused"] = broker.client_commit("notes: c", ["../c.md"], env=env)
            results["bad"] = broker.call(sock, {"v": 9}, timeout=10).exit
        finally:
            parent.kill()
            parent.wait()  # reaped: a zombie still answers kill(pid, 0)

    t = threading.Thread(target=client, daemon=True)
    t.start()
    assert broker.serve(vault, broker_dir, parent=parent.pid, broker=served) == 0
    t.join(30)
    assert results == {"commit": 0, "telegram": 0, "refused": broker.EXIT_REFUSED, "bad": broker.EXIT_REFUSED}
    assert len(sender.calls) == 1
    assert not (broker_dir / broker.SOCKET_NAME).exists()
    assert broker._read_record(os.environ, broker_dir.name)["open"] is True  # the run is on record
    out = capsys.readouterr()
    assert "committed " in out.err and "broker: commit -> exit 0" in out.out


def test_a_server_without_a_private_folder_never_starts(vault: Path, tmp_path: Path) -> None:
    shared = tmp_path / "scout-broker.shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    with pytest.raises(broker.BrokerError):
        broker.serve(vault, shared)


def test_a_client_with_no_broker_and_no_queue_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    blocked = tmp_path / "vault"
    blocked.mkdir()
    (blocked / ".scout-state").write_text("a file where the folder belongs")
    env = {"SCOUT_DATA_DIR": str(blocked), broker.ENV_SOCKET: ""}
    assert broker.client_commit("m", ["notes/a.md"], env=env) == 5
    fields = {"tier": "info", "parse_mode": "none", "body": "x", "allow_duplicate": False, "dry_run": False}
    assert broker.client_telegram(fields, env=env) == 1
    assert broker.client_telegram({**fields, "dry_run": True}, env=env) == 1
    assert broker.client_telegram({**fields, "body": "x" * 9000}, env=env) == broker.EXIT_LIMIT
    assert "can't be written" in capsys.readouterr().err


def test_a_reply_that_isnt_one_counts_as_no_answer() -> None:
    with pytest.raises(broker.Unreachable):
        broker.Reply.from_dict({"exit": True})
    with pytest.raises(broker.Unreachable):
        broker.call("", {}, timeout=1)


# -- the profile check -------------------------------------------------------------------------------------------


@pytest.fixture
def fake_sandbox_exec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for sandbox-exec that runs its command without a profile: only the profile check's logic runs."""
    stub = tmp_path / "sandbox-exec"
    stub.write_text('#!/bin/sh\nshift 2\nexec "$@"\n')
    stub.chmod(0o755)
    monkeypatch.setattr(sbx, "SANDBOX_EXEC", stub)
    return stub


def _scoutctl(vault: Path, *, version_exit: int) -> None:
    launcher = vault / "scripts" / "scoutctl"
    launcher.write_text(f'#!/bin/sh\n[ "$1" = version ] && exit {version_exit}\nexit 0\n')
    launcher.chmod(0o755)


def test_a_profile_is_tightened_only_when_the_engine_starts_under_it(
    vault: Path, broker_dir: Path, fake_sandbox_exec: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(broker, "wait_for", lambda *_a, **_k: True)
    monkeypatch.setattr(sbx, "self_test", lambda _profile: None)
    base = "(version 1)\n(allow default)\n"
    _scoutctl(vault, version_exit=0)
    text = broker.build_profile(vault, broker_dir, base, dict(os.environ))
    assert text.startswith(base) and "Phase 1d" in text
    _scoutctl(vault, version_exit=7)
    with pytest.raises(broker.BrokerError, match="didn't start"):
        broker.build_profile(vault, broker_dir, base, dict(os.environ))


def test_a_profile_is_not_tightened_without_an_answering_broker(
    vault: Path, broker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scoutctl(vault, version_exit=0)
    monkeypatch.setattr(broker, "SOCKET_WAIT_S", 0.2)
    with pytest.raises(broker.BrokerError, match="didn't answer"):
        broker.build_profile(vault, broker_dir, "(version 1)\n", dict(os.environ))
    with pytest.raises(broker.BrokerError, match="no base profile"):
        broker.build_profile(vault, broker_dir, "  ", dict(os.environ))


def test_the_engine_check_reports_a_sandbox_that_wont_run(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sbx, "SANDBOX_EXEC", vault / "no-such-sandbox-exec")
    reason = broker.python_starts("(version 1)", vault, dict(os.environ))
    assert reason is not None and "didn't start" in reason


# -- the CLI -----------------------------------------------------------------------------------------------------


def _cli(*args: str, env: Mapping[str, str] | None = None, stdin: str | None = None) -> Any:
    from scout.cli import app

    return CliRunner().invoke(app, list(args), env=dict(env or {}), input=stdin)


def test_the_broker_commands_answer_from_the_cli(vault: Path, broker_dir: Path, keep_signals: None) -> None:
    profile = _cli("broker", "profile", "--vault", str(vault), "--dir", str(broker_dir), stdin="(version 1)\n")
    assert profile.exit_code == 1  # this vault's helper has the client mode, but no broker answers here
    assert broker.read_status(vault)["status"] == "unavailable"
    assert _cli("broker", "commit").exit_code == 2
    queued = _cli("broker", "commit", "--", "notes: q", "notes/q.md", env={"SCOUT_DATA_DIR": str(vault)})
    assert queued.exit_code == broker.EXIT_QUEUED
    drained = _cli("broker", "drain", "--vault", str(vault), "--run", "scout-broker.none")
    assert drained.exit_code == 0 and "1 done" in drained.output
    assert _cli("broker", "serve", "--vault", str(vault), "--dir", str(vault / "notes")).exit_code == 1


def test_a_successful_profile_is_printed_and_recorded(
    vault: Path, broker_dir: Path, fake_sandbox_exec: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(broker, "wait_for", lambda *_a, **_k: True)
    monkeypatch.setattr(sbx, "self_test", lambda _profile: None)
    _scoutctl(vault, version_exit=0)
    res = _cli("broker", "profile", "--vault", str(vault), "--dir", str(broker_dir), stdin="(version 1)\n")
    assert res.exit_code == 0 and "Phase 1d" in res.output
    assert broker.read_status(vault)["status"] == "on"


def test_notify_telegram_hands_over_to_the_broker_and_queues_when_it_is_gone(vault: Path, broker_dir: Path) -> None:
    env = {"SCOUT_DATA_DIR": str(vault), broker.ENV_SOCKET: str(broker_dir / broker.SOCKET_NAME)}
    res = _cli("notify", "telegram", "--body", "wrap", env=env)
    assert res.exit_code == 0 and "queued" in res.output
    assert broker.pending(vault) == 1


def test_the_replay_command_reports_the_brokers(vault: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    res = _cli("sandbox", "replay", "--transcripts", str(project), "--vault", str(vault), "--brokers")
    assert res.exit_code == 0 and "with the run brokers" in res.output


# -- the tick ----------------------------------------------------------------------------------------------------


def test_the_tick_starts_a_drain_only_when_something_waits(vault: Path, tmp_path: Path) -> None:
    from scout.scripts import schedule_tick

    started: list[Path] = []
    logs = tmp_path / "logs"
    schedule_tick._drain_brokers(vault=vault, log_dir=logs, spawn=lambda v, _l: started.append(v) or 1)
    assert started == []
    broker.enqueue(vault, "commit", {"message": "m", "paths": ["notes/a.md"]})
    schedule_tick._drain_brokers(vault=vault, log_dir=logs, spawn=lambda v, _l: started.append(v) or 1)
    assert started == [vault]

    def boom(_v: Path, _l: Path) -> int:
        raise OSError("no fork")

    schedule_tick._drain_brokers(vault=vault, log_dir=logs, spawn=boom)  # never raises
    events = "".join(p.read_text() for p in logs.glob("*.jsonl"))
    assert "broker.drain_started" in events and "broker.drain_spawn_failed" in events


def test_the_tick_spawns_the_drain_detached(vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import schedule_tick

    seen: dict[str, Any] = {}

    class Proc:
        pid = 4242

    def popen(argv: list[str], **kw: Any) -> Proc:
        seen.update(argv=argv, **kw)
        return Proc()

    monkeypatch.setattr(schedule_tick.subprocess, "Popen", popen)
    monkeypatch.setenv(broker.ENV_SOCKET, "/tmp/x.sock")
    assert schedule_tick._spawn_broker_drain(vault, tmp_path / "logs") == 4242
    assert seen["argv"][-4:] == ["broker", "drain", "--vault", str(vault)]
    assert seen["start_new_session"] is True and broker.ENV_SOCKET not in seen["env"]


# -- replay helpers ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sub", "rest", "kind"),
    [
        ("log", " -1", None),
        ("config", " user.name", None),
        ("config", " user.name Alex", "config (write)"),
        ("stash", " list", None),
        ("stash", " push", "stash"),
        ("branch", " --show-current", None),
        ("worktree", " list", None),
        ("checkout", " -- notes/a.md", "checkout --"),
        ("restore", " -SW notes/a.md", "restore --worktree"),
        ("restore", " -S notes/a.md", "restore --staged"),
        ("rebase", " main", "other rebase"),
    ],
)
def test_the_replay_names_each_git_write(sub: str, rest: str, kind: str | None) -> None:
    from scout.sandbox import replay

    assert replay._git_kind(sub, rest) == kind


def test_the_replay_places_each_git_command(vault: Path) -> None:
    from scout.sandbox import replay

    home = Path.home()
    assert replay._git_where("git add x", "", vault, home) == "vault"
    assert replay._git_where("git add x", " -C /elsewhere", vault, home) == "elsewhere"
    assert replay._git_where("cd notes && git add x", "", vault, home) == "vault"
    assert replay._git_where("cd $UNKNOWN && git add x", "", vault, home) == "unknown"


# -- the edges: each answers, none crashes -------------------------------------------------------------------------


def test_a_limits_file_without_a_telegram_section_means_the_defaults(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path)}
    path = broker.owner_dir(env) / broker.CONFIG_NAME
    path.parent.mkdir(parents=True)
    path.write_text('{"telegram": [4]}')
    assert broker.load_limits(env) == broker.Limits()


@pytest.mark.parametrize(
    "req",
    [
        {"body": ""},
        {"body": "x", "dry_run": "yes"},
        {"body": "x", "parse_mode": "markdown"},
        {"body": "x", "tier": "loud"},
    ],
)
def test_a_telegram_request_outside_the_rules_is_refused(req: dict[str, Any]) -> None:
    with pytest.raises(broker.Refused):
        broker.validate_telegram(req, broker.Limits())


@pytest.mark.parametrize("path", ["", "VAULT"])
def test_an_empty_path_or_the_whole_vault_is_refused(vault: Path, path: str) -> None:
    req = {"message": "m", "paths": [str(vault) if path == "VAULT" else path]}
    with pytest.raises(broker.Refused):
        broker.validate_commit(req, vault, dict(os.environ))


class _Proc:
    """A helper process stand-in: its wait times out once, so the group is killed hard."""

    pid = 2**22 + 12345  # no such group: killpg's error is ignored

    def __init__(self, communicate_raises: BaseException) -> None:
        self._raises = communicate_raises
        self.waits = 0

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        raise self._raises

    def wait(self, timeout: float | None = None) -> int:
        self.waits += 1
        if self.waits == 1:
            raise subprocess.TimeoutExpired("helper", timeout or 0)
        return -9


def test_an_interrupted_commit_takes_its_helper_down_and_passes_the_interruption_on(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _Proc(KeyboardInterrupt())
    monkeypatch.setattr(broker.subprocess, "Popen", lambda *_a, **_k: proc)
    with pytest.raises(KeyboardInterrupt):
        broker.run_commit(vault, "m", ["notes/a.md"], None, dict(os.environ))
    assert proc.waits == 2  # asked to stop, then killed


def test_a_helper_that_cant_start_is_answered(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_fork(*_a: Any, **_k: Any) -> Any:
        raise OSError("no fork")

    monkeypatch.setattr(broker.subprocess, "Popen", no_fork)
    reply = broker.run_commit(vault, "m", ["notes/a.md"], None, dict(os.environ))
    assert reply.exit == 5 and "couldn't start" in reply.stderr


def test_the_send_log_skips_what_it_cant_read(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    env = {"HOME": str(tmp_path)}
    now = datetime.now(UTC)
    log = broker.owner_dir(env) / broker.TELEGRAM_LOG_NAME
    log.parent.mkdir(parents=True)
    log.write_text(
        "not json\n"
        f'{{"at": "{(now - timedelta(hours=72)).isoformat()}", "run": "scout-broker.old"}}\n'
        f'{{"at": "{now.isoformat()}", "run": "scout-broker.new", "rid": "01X"}}\n'
    )
    rows = broker._read_sends(env, now)
    assert [r["run"] for r in rows] == ["scout-broker.new"]


def test_old_run_records_are_pruned_and_bad_ones_read_as_none(tmp_path: Path) -> None:
    import time

    env = {"HOME": str(tmp_path)}
    broker.register_run(env, "scout-broker.old")
    old = broker._run_record(env, "scout-broker.old")
    stale = time.time() - 3 * 86_400
    os.utime(old, (stale, stale))
    broker.register_run(env, "scout-broker.new")
    assert not old.exists() and broker._read_record(env, "scout-broker.new") is not None
    assert broker._read_record(env, "not-a-run") is None
    broker.close_run(env, "scout-broker.never", ["01X"])  # nothing to close: no record is made
    assert broker._read_record(env, "scout-broker.never") is None


def test_small_helpers_answer_at_their_edges(tmp_path: Path) -> None:
    assert broker._alive(1) is True  # someone else's process: alive, not ours to signal
    assert broker.sweep_stale(tmp_path / "missing") == 0
    assert broker.run_id_of("/tmp/elsewhere/b.sock") == "unknown"
    with pytest.raises(ValueError):
        broker.enqueue(tmp_path, "push", {})
    assert "another drain" in broker.DrainResult(busy=True).line()
    assert "isn't safe" in broker.DrainResult(unsafe="x").line()
    assert broker.helper_has_client(tmp_path) is False


def test_reads_on_a_socket_end_cleanly_or_refuse_what_is_too_long() -> None:
    import socket

    a, b = socket.socketpair()
    with a, b:
        b.sendall(b"no newline")
        b.shutdown(socket.SHUT_WR)
        assert broker._read_line(a, 100) == b"no newline"
    a, b = socket.socketpair()
    with a, b:
        b.sendall(b"x" * 200)
        with pytest.raises(ValueError):
            broker._read_line(a, 100)


def test_a_bug_in_the_broker_is_answered_as_a_failure(vault: Path) -> None:
    import socket

    class Broken(broker.Broker):
        def handle(self, req: Any) -> broker.Reply:
            raise RuntimeError("bug")

    a, b = socket.socketpair()
    with a, b:
        b.sendall(b'{"v": 1, "verb": "ping"}\n')
        broker._answer(a, Broken(vault, dict(os.environ)))
        assert b'"exit": 1' in b.recv(4096)


@pytest.mark.parametrize("answer", [b"", b"not json\n", b'{"exit": "zero"}\n'])
def test_a_server_that_doesnt_answer_properly_counts_as_no_answer(broker_dir: Path, answer: bytes) -> None:
    import socket

    path = str(broker_dir / "s.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def serve_once() -> None:
        conn, _ = srv.accept()
        with conn:
            conn.recv(4096)
            if answer:
                conn.sendall(answer)

    t = threading.Thread(target=serve_once, daemon=True)
    t.start()
    try:
        with pytest.raises(broker.Unreachable):
            broker.call(path, {"v": 1, "verb": "ping"}, timeout=5)
    finally:
        t.join(5)
        srv.close()


def test_replies_reach_the_caller_on_the_right_stream(capsys: pytest.CaptureFixture[str]) -> None:
    assert broker._emit(broker.Reply(3, "why", "out")) == 3
    captured = capsys.readouterr()
    assert captured.out == "out\n" and captured.err == "why\n"


def test_queue_entries_that_arent_whole_requests_fail_and_an_empty_queue_is_quick(vault: Path) -> None:
    assert broker.drain(vault).done == 0  # nothing queued
    broker.enqueue(vault, "commit", {"message": "m", "paths": ["notes/a.md"]})
    folder = broker.queue_dir(vault, "commit")
    (folder / "00000000000000000000000001.json").write_text("[1, 2]")
    (folder / "00000000000000000000000002.json").write_text('{"queued_at": "not a time", "message": "m"}')
    result = broker.drain(vault)
    assert result.failed == 2 and result.done == 1


def test_the_replay_names_each_kind_of_secret_read(tmp_path: Path) -> None:
    from scout.sandbox import replay

    env = {"HOME": str(tmp_path)}
    secrets = tmp_path / ".scout-secrets"
    assert replay._secret_read_kind(str(secrets), env).startswith("folder listing")
    assert replay._secret_read_kind(str(secrets / "telegram-bot-token"), env).startswith("Telegram")
    assert replay._secret_read_kind(str(tmp_path / "other"), env) == "another secret"


def test_the_replay_counts_what_only_the_tightened_profile_refuses(vault: Path, tmp_path: Path) -> None:
    import json

    from scout.sandbox import replay

    project = tmp_path / "project"
    project.mkdir()
    token = Path(os.environ["HOME"]) / ".scout-secrets" / "telegram-bot-token"
    calls = [
        ("Write", {"file_path": str(vault / ".git" / "objects" / "ab" / "cd"), "content": "x"}),
        ("Read", {"file_path": str(token)}),
        ("Write", {"file_path": str(vault / "notes" / "fine.md"), "content": "x"}),
    ]
    rows = [json.dumps({"entrypoint": "sdk-cli", "type": "system"})]
    for i, (name, inp) in enumerate(calls):
        block = {"type": "tool_use", "id": f"u{i}", "name": name, "input": inp}
        rows.append(json.dumps({"type": "assistant", "message": {"content": [block]}}))
    (project / "s.jsonl").write_text("\n".join(rows) + "\n")
    b = replay.replay(project, vault, limit=5, brokers=True)["brokers"]
    assert b["newly_denied_writes"] == {"the vault's git dir (the broker commits)": 1}
    assert b["newly_denied_writes_outside_the_git_dir"] == 0
    assert list(b["newly_denied_secret_reads"]) == ["Telegram token or chat id (the broker sends)"]


def test_the_drain_command_names_what_failed_and_stops_on_sigterm(vault: Path, keep_signals: None) -> None:
    folder = broker.queue_dir(vault, "commit")
    broker.enqueue(vault, "commit", {"message": "m", "paths": ["../outside.md"]})
    res = _cli("broker", "drain", "--vault", str(vault))
    assert res.exit_code == 0 and "1 moved to failed/" in res.output and "failed: commit: refused" in res.output
    with pytest.raises(SystemExit) as stopped:
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)  # the handler the drain installed
    assert stopped.value.code == 143
    assert not list(folder.glob("*.json"))


def test_the_commit_command_takes_its_arguments_with_or_without_the_separator(vault: Path) -> None:
    env = {"SCOUT_DATA_DIR": str(vault)}
    assert _cli("broker", "commit", "notes: q", "notes/q.md", env=env).exit_code == broker.EXIT_QUEUED
    assert _cli("broker", "commit", "--", "only-a-message", env=env).exit_code == 2


def test_the_serve_command_returns_when_its_parent_is_already_gone(
    vault: Path, broker_dir: Path, keep_signals: None
) -> None:
    gone = subprocess.Popen(["/usr/bin/true"])
    gone.wait()
    res = _cli("broker", "serve", "--vault", str(vault), "--dir", str(broker_dir), "--parent", str(gone.pid))
    assert res.exit_code == 0 and not (broker_dir / broker.SOCKET_NAME).exists()


def test_a_queue_folder_that_cant_be_opened_reads_as_empty(vault: Path) -> None:
    broker.enqueue(vault, "commit", {"message": "m", "paths": ["notes/a.md"]})
    folder = vault / broker.QUEUE_DIR
    folder.chmod(0o000)
    try:
        assert broker.pending(vault) == 0 and broker.failed_entries(vault) == []
        assert broker.queue_problem(vault) is None
    finally:
        folder.chmod(0o700)
    assert broker.pending(vault) == 1


def test_the_doctor_reports_the_brokers_and_their_queue(vault: Path) -> None:
    from scout.scripts import bootstrap_doctor

    assert bootstrap_doctor._run_broker_notes(vault) == []
    broker.write_status(vault, "on", "commits and Telegram go through the run's broker")
    broker.enqueue(vault, "commit", {"message": "m", "paths": ["notes/a.md"]})
    notes = bootstrap_doctor._run_broker_notes(vault)
    assert notes[0].startswith("run brokers: on") and "1 waiting" in notes[1]
