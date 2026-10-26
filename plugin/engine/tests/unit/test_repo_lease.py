"""The Repository Writer Lease on this computer (Agnes R2, ``scout.repo_lease``).

Concurrency tests (Agnes spec §15 names lease fencing as one): a scheduled run
holds the lease, a manual sync during it must reuse the run's token (Agnes
rotates the token when the same holder acquires again), and a run killed
before it releases must not leave the lease held. ``FakeHost`` follows Agnes's
§5 semantics, rotation included.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import repo_lease as rl
from scout import repo_sync as rs
from scout.cli import app

from .test_repo_sync import FakeHost, _commit, _link
from .test_repo_sync import host as host  # noqa: F401  (fixture)
from .test_repo_sync import vault as vault  # noqa: F401  (fixture)


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


@pytest.fixture
def linked(vault: Path, host: FakeHost) -> Path:  # noqa: F811
    _link(vault, host)
    rs.save_token(vault, "pat-stand-in")
    return vault


@pytest.fixture
def runner_proc():
    """A live process standing in for the runner whose PID the lease file records."""
    proc = subprocess.Popen(["sleep", "60"])
    yield proc
    proc.kill()
    proc.wait()


def test_link_takes_and_gives_back_a_lease_of_its_own(linked: Path, host: FakeHost) -> None:  # noqa: F811
    assert host.lease_calls == ["acquire", "release"]
    assert host.lease is None and host.generation == 1
    assert rl.load(linked) is None


def test_a_run_lease_lives_in_an_owner_only_file_outside_the_vault(
    linked: Path,
    host: FakeHost,  # noqa: F811
    runner_proc: subprocess.Popen,
) -> None:
    lease, notes = rl.acquire_for_run(linked, host, run_id="scout-briefing-1", what="briefing", pid=runner_proc.pid)
    path = rl.lease_path(linked)
    assert notes == []
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    assert not str(path).startswith(str(linked))
    assert host.lease is not None and host.lease["token"] == lease.token
    assert host.lease["holder_ref"] == rl.run_holder_ref("scout-briefing-1")
    assert host.lease["holder_ref"].startswith("laptop:") and host.lease["holder_ref"].endswith(":scout-briefing-1")
    assert "token" not in lease.public() and lease.token not in json.dumps(lease.public())


def test_a_run_is_refused_while_agnes_holds_the_lease(linked: Path, host: FakeHost) -> None:  # noqa: F811
    host.lease = {"holder_ref": "agnes:run-7", "label": "Agnes run", "token": "agnes-token"}
    with pytest.raises(rs.RepoSyncError) as e:
        rl.acquire_for_run(linked, host, run_id="r1", what="briefing", pid=1)
    assert e.value.code == "repository_lease_held"
    assert rl.holder_text(e.value) == "agnes (Agnes run)"
    assert rl.load(linked) is None
    assert host.lease["token"] == "agnes-token"


def test_a_manual_sync_during_a_run_reuses_its_token_and_keeps_its_lease(
    linked: Path,
    host: FakeHost,  # noqa: F811
    runner_proc: subprocess.Popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease, _ = rl.acquire_for_run(linked, host, run_id="r1", what="briefing", pid=runner_proc.pid)
    calls_before = list(host.lease_calls)
    pushed_with: list[object] = []
    real_push = rs.push

    def recording_push(*a: object) -> None:
        pushed_with.append(a[4])
        real_push(*a)  # type: ignore[arg-type]

    monkeypatch.setattr(rs, "push", recording_push)
    _commit(linked, "knowledge-base/people/priya.md", "edited during the run\n", "edit")

    result = rs.sync(linked, host)

    assert result.pushed
    assert pushed_with == [(lease.token, lease.generation)]
    assert host.lease_calls == calls_before
    assert host.lease is not None and host.lease["token"] == lease.token
    assert rl.renew(linked, host, "r1").token == lease.token
    assert rl.release(linked, host, "r1") and host.lease is None and rl.load(linked) is None


def test_a_sync_reusing_a_lost_run_lease_is_refused(
    linked: Path,
    host: FakeHost,  # noqa: F811
    runner_proc: subprocess.Popen,
) -> None:
    rl.acquire_for_run(linked, host, run_id="r1", what="briefing", pid=runner_proc.pid)
    host.lease = {"holder_ref": "agnes:run-8", "label": "Agnes run", "token": "agnes-token"}
    with pytest.raises(rs.RepoSyncError):
        rl.renew(linked, host, "r1")
    assert rl.load(linked).lost  # type: ignore[union-attr]
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(linked, host)
    assert e.value.code == "repository_lease_lost"
    assert host.lease["token"] == "agnes-token"


def test_a_sync_is_refused_while_agnes_holds_the_lease(linked: Path, host: FakeHost) -> None:  # noqa: F811
    host.lease = {"holder_ref": "agnes:run-7", "label": "Agnes run", "token": "agnes-token"}
    _commit(linked, "knowledge-base/people/priya.md", "x\n", "edit")
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(linked, host)
    assert e.value.code == "repository_lease_held"
    assert host.lease["token"] == "agnes-token"


def test_a_second_sync_waits_its_turn(linked: Path, host: FakeHost, runner_proc: subprocess.Popen) -> None:  # noqa: F811
    rl.save(
        linked,
        rl.LocalLease("sync", "sync-1", runner_proc.pid, rl.sync_holder_ref(), "x", "t", 1),
    )
    with pytest.raises(rs.RepoSyncError) as e:
        rs.sync(linked, host)
    assert e.value.code == "repository_lease_held"


def test_a_killed_run_s_lease_is_released_by_the_next_run(
    linked: Path,
    host: FakeHost,  # noqa: F811
    runner_proc: subprocess.Popen,
) -> None:
    killed, _ = rl.acquire_for_run(linked, host, run_id="r1", what="briefing", pid=_dead_pid())
    assert host.lease["token"] == killed.token  # type: ignore[index]

    lease, notes = rl.acquire_for_run(linked, host, run_id="r2", what="briefing", pid=runner_proc.pid)

    assert notes and "r1" in notes[0]
    assert host.lease["holder_ref"].endswith(":r2") and host.lease["token"] == lease.token  # type: ignore[index]
    assert rl.load(linked).run_id == "r2"  # type: ignore[union-attr]


def test_a_killed_run_s_lease_that_expired_meanwhile_is_just_forgotten(linked: Path, host: FakeHost) -> None:  # noqa: F811
    rl.acquire_for_run(linked, host, run_id="r1", what="briefing", pid=_dead_pid())
    host.lease = None
    assert rl.clear_stale(linked, host) is not None
    assert rl.load(linked) is None


def test_a_run_waits_for_a_manual_sync_then_takes_the_lease(
    linked: Path,
    host: FakeHost,  # noqa: F811
    runner_proc: subprocess.Popen,
) -> None:
    sync_proc = subprocess.Popen(["sleep", "60"])
    rl.save(linked, rl.LocalLease("sync", "sync-1", sync_proc.pid, rl.sync_holder_ref(), "x", "t", 1))
    waited: list[float] = []

    def sync_ends(secs: float) -> None:
        waited.append(secs)
        sync_proc.kill()
        sync_proc.wait()
        rl.lease_path(linked).unlink()

    lease, _ = rl.acquire_for_run(linked, host, run_id="r1", what="b", pid=runner_proc.pid, sleep=sync_ends)
    assert waited == [rl.DEFAULT_POLL_SECS] and lease.run_id == "r1"

    other = subprocess.Popen(["sleep", "60"])
    try:
        rl.save(linked, rl.LocalLease("sync", "sync-2", other.pid, rl.sync_holder_ref(), "x", "t2", 1))
        with pytest.raises(rs.RepoSyncError) as e:
            rl.acquire_for_run(linked, host, run_id="r2", what="b", pid=1, wait_s=0)
        assert e.value.code == "repository_lease_held"
    finally:
        other.kill()
        other.wait()


def test_acquiring_again_for_the_same_run_never_rotates_its_token(
    linked: Path,
    host: FakeHost,  # noqa: F811
    runner_proc: subprocess.Popen,
) -> None:
    lease, _ = rl.acquire_for_run(linked, host, run_id="r1", what="b", pid=runner_proc.pid)
    again, _ = rl.acquire_for_run(linked, host, run_id="r1", what="b", pid=runner_proc.pid)
    assert again.token == lease.token == host.lease["token"]  # type: ignore[index]


def test_keepalive_renews_while_the_runner_lives_and_releases_when_it_dies(
    linked: Path,
    host: FakeHost,  # noqa: F811
) -> None:
    runner = subprocess.Popen(["sleep", "60"])
    rl.acquire_for_run(linked, host, run_id="r1", what="b", pid=runner.pid)
    now = [0.0]
    logs: list[str] = []

    def tick(secs: float) -> None:
        now[0] += secs
        if host.lease_calls.count("renew") == 2:
            runner.kill()
            runner.wait()

    why = rl.keepalive(
        linked, host, run_id="r1", parent_pid=runner.pid, renew_s=300, poll_s=5, log=logs.append, sleep=tick,
        clock=lambda: now[0],
    )  # fmt: skip

    assert why == "released"
    assert host.lease_calls.count("renew") == 2 and now[0] >= 600
    assert host.lease is None and rl.load(linked) is None
    assert "released" in logs[-1]


def test_keepalive_stops_when_agnes_ends_the_lease_or_the_runner_released_it(
    linked: Path,
    host: FakeHost,  # noqa: F811
    runner_proc: subprocess.Popen,
) -> None:
    rl.acquire_for_run(linked, host, run_id="r1", what="b", pid=runner_proc.pid)
    host.lease = None
    assert (
        rl.keepalive(linked, host, run_id="r1", parent_pid=runner_proc.pid, renew_s=1, sleep=lambda s: None) == "lost"
    )
    assert rl.load(linked).lost  # type: ignore[union-attr]

    rl.lease_path(linked).unlink()
    assert rl.keepalive(linked, host, run_id="r1", parent_pid=runner_proc.pid) == "gone"
    assert rl.keepalive(linked, host, run_id="r1", parent_pid=_dead_pid()) == "gone"


def test_keepalive_retries_a_failed_renew_and_stops_when_asked(
    linked: Path,
    host: FakeHost,  # noqa: F811
    runner_proc: subprocess.Popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rl.acquire_for_run(linked, host, run_id="r1", what="b", pid=runner_proc.pid)

    def unreachable(token: str) -> dict:
        raise rs.RepoSyncError("host_unreachable", "down")

    monkeypatch.setattr(host, "lease_renew", unreachable)
    now = [0.0]
    logs: list[str] = []
    stops = iter([False, False, True])
    why = rl.keepalive(
        linked, host, run_id="r1", parent_pid=runner_proc.pid, renew_s=1, poll_s=1, log=logs.append,
        stop=lambda: next(stops), sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0],
    )  # fmt: skip
    assert why == "stopped" and logs and "retrying" in logs[0]
    assert host.lease is not None


def test_a_release_agnes_cannot_hear_leaves_the_file_for_the_next_run(
    linked: Path,
    host: FakeHost,  # noqa: F811
    runner_proc: subprocess.Popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease, _ = rl.acquire_for_run(linked, host, run_id="r1", what="b", pid=runner_proc.pid)

    def unreachable(token: str) -> None:
        raise rs.RepoSyncError("host_unreachable", "down")

    with monkeypatch.context() as m:
        m.setattr(host, "lease_release", unreachable)
        with pytest.raises(rs.RepoSyncError):
            rl.release(linked, host, "r1")
    assert rl.load(linked).pid == 0  # type: ignore[union-attr]
    assert rl.release(linked, host, "someone-else") is False

    rl.acquire_for_run(linked, host, run_id="r2", what="b", pid=runner_proc.pid)
    assert host.lease["holder_ref"].endswith(":r2")  # type: ignore[index]
    assert lease.token != host.lease["token"]  # type: ignore[index]


def test_lease_file_errors(linked: Path) -> None:
    path = rl.lease_path(linked)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(rs.RepoSyncError) as e:
        rl.load(linked)
    assert e.value.code == "lease_file_insecure"
    path.chmod(0o600)
    with pytest.raises(rs.RepoSyncError) as e:
        rl.load(linked)
    assert e.value.code == "lease_file_unreadable"
    rl.forget(linked, "any")
    assert path.exists()
    with pytest.raises(rs.RepoSyncError) as e:
        rl.renew(linked, FakeHost(linked), "r1")
    path.unlink()
    with pytest.raises(rs.RepoSyncError) as e:
        rl.renew(linked, FakeHost(linked), "r1")
    assert e.value.code == "lease_not_ours"
    with pytest.raises(rs.RepoSyncError):
        rl._from_grant({}, owner="run", run_id="r", pid=1, ref="r", label="l")
    assert rl.env_secs("X", 7, {"X": "0"}) == 7 and rl.env_secs("X", 7, {"X": "12"}) == 12


# ----- the CLI the runners call --------------------------------------------------------------------


def test_cli_lease_exit_codes_and_no_token_on_stdout(
    linked: Path,
    host: FakeHost,  # noqa: F811
    runner_proc: subprocess.Popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rs, "AgnesHost", lambda url, token: host)
    cli = CliRunner()
    args = ["repo", "lease", "acquire", "--run-id", "scout-briefing-1", "--what", "briefing"]

    out = cli.invoke(app, [*args, "--pid", str(runner_proc.pid), "--json"])
    assert out.exit_code == 0, out.output
    doc = json.loads(out.stdout)
    assert doc["outcome"] == "held" and "token" not in doc
    assert host.lease["token"] not in out.output  # type: ignore[index]

    out = cli.invoke(app, ["repo", "lease", "release", "--run-id", "scout-briefing-1"])
    assert out.exit_code == 0 and "released" in out.output and host.lease is None
    out = cli.invoke(app, ["repo", "lease", "release", "--run-id", "scout-briefing-1"])
    assert out.exit_code == 0 and out.output == ""

    host.lease = {"holder_ref": "agnes:run-7", "label": "Agnes run", "token": "agnes-token"}
    out = cli.invoke(app, args)
    assert out.exit_code == 3 and "lease_held" in out.stderr and "Agnes run" in out.stderr
    out = cli.invoke(app, [*args, "--json"])
    assert out.exit_code == 3 and json.loads(out.stdout)["outcome"] == "lease_held"

    def unreachable(ref: str, label: str) -> dict:
        raise rs.RepoSyncError("host_unreachable", "down")

    monkeypatch.setattr(host, "lease_acquire", unreachable)
    out = cli.invoke(app, args)
    assert out.exit_code == 4 and "lease_unavailable" in out.stderr

    out = cli.invoke(app, ["repo", "lease", "acquire", "--run-id", "bad id;rm"])
    assert out.exit_code == 2


def test_cli_lease_on_an_unlinked_vault_makes_no_call(vault: Path) -> None:  # noqa: F811
    cli = CliRunner()
    out = cli.invoke(app, ["repo", "lease", "acquire", "--run-id", "r1", "--json"])
    assert out.exit_code == 5 and json.loads(out.stdout)["outcome"] == "not_linked"
    assert cli.invoke(app, ["repo", "lease", "release", "--run-id", "r1"]).exit_code == 0
    assert cli.invoke(app, ["repo", "lease", "keepalive", "--run-id", "r1", "--parent-pid", "1"]).exit_code == 4


def test_cli_keepalive_releases_when_the_runner_is_gone(
    linked: Path,
    host: FakeHost,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rs, "AgnesHost", lambda url, token: host)
    rl.acquire_for_run(linked, host, run_id="r1", what="b", pid=_dead_pid())
    out = CliRunner().invoke(app, ["repo", "lease", "keepalive", "--run-id", "r1", "--parent-pid", str(_dead_pid())])
    assert out.exit_code == 0 and "released" in out.stderr
    assert host.lease is None


def test_cli_lease_status_never_shows_the_token(
    linked: Path,
    host: FakeHost,  # noqa: F811
    runner_proc: subprocess.Popen,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease, _ = rl.acquire_for_run(linked, host, run_id="r1", what="b", pid=runner_proc.pid)
    remote = {"held": True, "holder_kind": "laptop", "holder_label": "mac (b)", "generation": 2, "enforced": True}
    monkeypatch.setattr(host, "lease_status", lambda: remote, raising=False)
    monkeypatch.setattr(rs, "AgnesHost", lambda url, token: host)
    cli = CliRunner()
    out = cli.invoke(app, ["repo", "lease", "status"])
    assert out.exit_code == 0 and "held by laptop (mac (b))" in out.output and "This computer: run r1" in out.output
    out = cli.invoke(app, ["repo", "lease", "status", "--json"])
    assert json.loads(out.stdout)["local"]["run_id"] == "r1"
    assert lease.token not in out.output
    remote.update(held=False, expired=True, enforced=False)
    out = cli.invoke(app, ["repo", "lease", "status"])
    assert "Agnes: free (the last lease expired)" in out.output and "not yet" in out.output


# ----- the Agnes client --------------------------------------------------------------------------


class _Resp:
    def __init__(self, status: int, body: object = None, text: str = "") -> None:
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else b"x"
        self.text = text

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def test_agnes_host_speaks_the_lease_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    calls: list[tuple[str, str, object, dict]] = []
    replies = iter(
        [
            _Resp(201, {"token": "lt", "generation": 3, "held": True}),
            _Resp(200, {"generation": 3, "held": True}),
            _Resp(204),
            _Resp(200, {"held": False}),
        ]
    )

    def fake(method: str, url: str, headers: dict, json: object = None, timeout: float = 0) -> _Resp:
        calls.append((method, url, json, headers))
        return next(replies)

    monkeypatch.setattr(requests, "request", fake)
    host = rs.AgnesHost("https://agnes.example.com", "pat-stand-in")
    assert host.lease_acquire("laptop:abc:r1", "mac (briefing)")["token"] == "lt"
    host.lease_renew("lt")
    host.lease_release("lt")
    assert host.lease_status() == {"held": False}
    assert [(c[0], c[1].rsplit("/api/me/", 1)[1]) for c in calls] == [
        ("POST", "knowledge-repository/lease"),
        ("POST", "knowledge-repository/lease/renew"),
        ("DELETE", "knowledge-repository/lease"),
        ("GET", "knowledge-repository/lease"),
    ]
    assert calls[0][2] == {"holder_kind": "laptop", "holder_ref": "laptop:abc:r1", "holder_label": "mac (briefing)"}
    assert rs.LEASE_TOKEN_HEADER not in calls[0][3]
    assert calls[1][3][rs.LEASE_TOKEN_HEADER] == "lt" and calls[2][3][rs.LEASE_TOKEN_HEADER] == "lt"


@pytest.mark.parametrize(
    ("reply", "code", "holder"),
    [
        (
            _Resp(
                409, {"detail": {"code": "repository_lease_held", "message": "m", "holder": {"holder_kind": "agnes"}}}
            ),
            "repository_lease_held",
            {"holder_kind": "agnes"},
        ),
        (_Resp(409, {"detail": {"code": "repository_lease_lost", "message": "m"}}), "repository_lease_lost", {}),
        (_Resp(409, {"detail": "plain"}, text="plain"), "host_error", None),
        (_Resp(409, ValueError("not json"), text="<html>"), "host_error", None),
    ],
)
def test_agnes_host_lease_refusals(monkeypatch: pytest.MonkeyPatch, reply: _Resp, code: str, holder: object) -> None:
    import requests

    monkeypatch.setattr(requests, "request", lambda *a, **k: reply)
    with pytest.raises(rs.RepoSyncError) as e:
        rs.AgnesHost("https://agnes.example.com", "t").lease_acquire("r", "l")
    assert e.value.code == code
    if holder is not None:
        assert e.value.detail["holder"] == holder


def test_push_headers_go_in_git_config_env_never_argv() -> None:
    env = rs.lease_git_env({"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "a", "GIT_CONFIG_KEY_1": "b"}, "lt", 4)
    assert env["GIT_CONFIG_COUNT"] == "4"
    assert env["GIT_CONFIG_KEY_2"] == env["GIT_CONFIG_KEY_3"] == "http.extraHeader"
    assert env["GIT_CONFIG_VALUE_2"] == "X-Agnes-Lease-Token: lt"
    assert env["GIT_CONFIG_VALUE_3"] == "X-Agnes-Lease-Generation: 4"
    assert rs.lease_git_env({}, "lt", 1)["GIT_CONFIG_COUNT"] == "2"


def test_releases_agnes_cannot_hear(
    linked: Path,
    host: FakeHost,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreachable(token: str) -> None:
        raise rs.RepoSyncError("host_unreachable", "down")

    rl.acquire_for_run(linked, host, run_id="r1", what="b", pid=_dead_pid())
    with monkeypatch.context() as m:
        m.setattr(host, "lease_release", unreachable)
        with pytest.raises(rs.RepoSyncError):
            rl.clear_stale(linked, host)
        assert rl.load(linked) is not None
        logs: list[str] = []
        assert rl.keepalive(linked, host, run_id="r1", parent_pid=_dead_pid(), log=logs.append) == "release_failed"
        assert "expires on Agnes" in logs[0]
    rl.clear_stale(linked, host)

    with monkeypatch.context() as m:
        m.setattr(host, "lease_release", unreachable)
        _commit(linked, "knowledge-base/people/priya.md", "y\n", "edit")
        assert rs.sync(linked, host).pushed
    assert rl.load(linked) is None


def test_the_machine_id_survives_a_host_rename(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    monkeypatch.setattr(socket, "gethostname", lambda: "MacBook-Pro-10.local")
    first = rl.machine_ref()
    monkeypatch.setattr(socket, "gethostname", lambda: "Mac")
    assert rl.machine_ref() == first and rl.machine_label() == "Mac"
    path = Path.home() / ".scout-secrets" / "agnes-knowledge-repo" / rl.MACHINE_ID_FILE
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text("not-an-id\n", encoding="utf-8")
    path.unlink()
    assert rl.machine_ref() != first
    assert rl.run_holder_ref("r1") == f"laptop:{rl.machine_ref()}:r1"


def test_two_processes_creating_the_machine_id_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    path = Path.home() / ".scout-secrets" / "agnes-knowledge-repo" / rl.MACHINE_ID_FILE
    real_link = os.link

    def other_process_won(src: str, dst: str) -> None:
        path.write_text("0123456789ab\n", encoding="utf-8")
        monkeypatch.setattr(os, "link", real_link)
        raise FileExistsError(dst)

    monkeypatch.setattr(os, "link", other_process_won)
    assert rl.machine_ref() == "0123456789ab"
    assert not list(path.parent.glob("*.tmp"))
