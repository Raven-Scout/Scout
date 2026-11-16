"""Edge paths of the agent-runtime engine: signals, stdin, escalation, auth, and malformed inputs."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from scout.agent_runtimes import consent, limits
from scout.agent_runtimes import exec as rt_exec
from scout.agent_runtimes.base import Launch, RunRequest
from scout.agent_runtimes.claude_code import ClaudeCode
from tests._agent_runtimes import DummyRuntime, registered

_SIGNALS = (signal.SIGPIPE, signal.SIGXFSZ, signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


@pytest.fixture(autouse=True)
def _keep_the_workers_signals() -> Iterator[None]:
    saved = {sig: signal.getsignal(sig) for sig in _SIGNALS}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def _script(tmp_path: Path, body: str, name: str = "cli.sh") -> Path:
    script = tmp_path / name
    script.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    script.chmod(0o755)
    return script


def _env() -> dict[str, str]:
    return {**os.environ, "SCOUT_RUN_AWAKE_STARTED": "awake 1000"}


def _awake() -> tuple[str, int]:
    return "awake", 1001


# -- exec -----------------------------------------------------------------------------------------------------


def test_a_signal_to_exec_reaches_the_cli(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    script = _script(tmp_path, f"trap 'exit 7' TERM\ntouch {ready}\nwhile :; do sleep 0.05; done\n")

    def send() -> None:
        for _ in range(200):
            if ready.exists():
                os.kill(os.getpid(), signal.SIGTERM)
                return
            time.sleep(0.02)

    threading.Thread(target=send, daemon=True).start()
    code = rt_exec.run_with_timeout(
        Launch(argv=(str(script),)), _env(), timeout_s=60, runtime_name="Q", awake_now=_awake, poll_s=0.05
    )
    assert code == 7


def test_a_signal_before_the_cli_exists_is_passed_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _script(tmp_path, "trap 'exit 9' HUP\nwhile :; do sleep 0.05; done\n")
    real = subprocess.Popen

    def popen(*a: object, **k: object) -> subprocess.Popen[bytes]:
        os.kill(os.getpid(), signal.SIGHUP)  # arrives before the child exists
        proc = real(*a, **k)  # type: ignore[call-overload]
        time.sleep(0.3)  # let the child install its trap before the pending signal is forwarded
        return proc  # type: ignore[no-any-return]

    monkeypatch.setattr(rt_exec.subprocess, "Popen", popen)
    code = rt_exec.run_with_timeout(
        Launch(argv=(str(script),)), _env(), timeout_s=60, runtime_name="Q", awake_now=_awake, poll_s=0.05
    )
    # Either the CLI's trap caught it or the HUP killed it first: either way it was passed on (else it loops on).
    assert code in (9, 128 + signal.SIGHUP)


def test_stdin_reaches_the_cli(tmp_path: Path) -> None:
    out = tmp_path / "stdin.txt"
    script = _script(tmp_path, f"cat > {out}\n")
    code = rt_exec.run_with_timeout(
        Launch(argv=(str(script),), stdin="the prompt\n"), _env(), timeout_s=60, runtime_name="Q", awake_now=_awake
    )
    assert code == 0 and out.read_text() == "the prompt\n"


def test_stdin_to_a_cli_that_doesnt_read_it_is_fine(tmp_path: Path) -> None:
    script = _script(tmp_path, "exit 0\n")
    big = "x" * 1_000_000
    code = rt_exec.run_with_timeout(
        Launch(argv=(str(script),), stdin=big), _env(), timeout_s=60, runtime_name="Q", awake_now=_awake
    )
    assert code == 0


def test_a_cli_that_ignores_term_is_killed(tmp_path: Path) -> None:
    script = _script(tmp_path, "trap '' TERM\nwhile :; do sleep 0.05; done\n")
    ticks = iter(range(1000, 10**6, 30))
    t0 = time.monotonic()
    code = rt_exec.run_with_timeout(
        Launch(argv=(str(script),)),
        _env(),
        timeout_s=60,
        runtime_name="Q",
        awake_now=lambda: ("awake", next(ticks)),
        poll_s=0.05,
        grace_s=0.5,
    )
    assert code == rt_exec.TIMEOUT_EXIT
    assert time.monotonic() - t0 < 15


def test_a_cli_killed_by_a_signal_exits_128_plus_it(tmp_path: Path) -> None:
    script = _script(tmp_path, "kill -TERM $$\n")
    code = rt_exec.run_with_timeout(
        Launch(argv=(str(script),)), _env(), timeout_s=60, runtime_name="Q", awake_now=_awake
    )
    assert code == 128 + signal.SIGTERM


def test_exec_searches_path_for_a_bare_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake(path: str, argv: list[str], env: dict[str, str]) -> None:
        calls.append((path, argv))
        raise RuntimeError("stop")

    monkeypatch.setattr(rt_exec.os, "execvpe", fake)
    with pytest.raises(RuntimeError):
        rt_exec.exec_launch(Launch(argv=("claude", "-p", "x")), {})
    assert calls == [("claude", ["claude", "-p", "x"])]


def test_run_can_return_the_argv_without_running() -> None:
    req = RunRequest(Path("/v"), "m", "r", "p", "dm-x", "1", "autonomous", Path("/bin/dummy"))
    argv = rt_exec.run(DummyRuntime("d"), req, timeout_s=None, execute=False)
    assert argv[0] == "/bin/dummy" and "dm-x" in argv


# -- claude-code auth ------------------------------------------------------------------------------------------


class _Status:
    def __init__(self, mode: str, *, problems: tuple[str, ...] = (), days_left: int | None = 100) -> None:
        self.mode, self.problems, self.days_left, self.location = mode, problems, days_left, "the Keychain"

    def expiry_text(self) -> str | None:
        return None if self.days_left is None else f"expires in {self.days_left} day(s)"


@pytest.mark.parametrize(
    ("status", "signed_in", "mode"),
    [
        (_Status("token"), True, "token"),
        (_Status("token", days_left=-3), False, "token"),
        (_Status("token", problems=("readable by others",)), False, "token"),
        (_Status("token", days_left=None), True, "token"),
        (_Status("login"), None, "login"),
    ],
)
def test_claude_auth_status(
    monkeypatch: pytest.MonkeyPatch, status: _Status, signed_in: bool | None, mode: str
) -> None:
    from scout import claude_auth

    monkeypatch.setattr(claude_auth, "status", lambda *a, **k: status)
    auth = ClaudeCode().auth_status(Path("/bin/claude"))
    assert (auth.signed_in, auth.mode) == (signed_in, mode)
    assert auth.detail


# -- consent ---------------------------------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.mark.parametrize("bad", ["", " ", "open ai", "a/b", "x;y"])
def test_a_provider_must_be_an_id(home: Path, tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        consent.consent(tmp_path, bad)


def test_a_record_of_the_wrong_shape_is_no_consent(home: Path, tmp_path: Path) -> None:
    path = consent.record_path()
    path.parent.mkdir(parents=True)
    vault_key = str(tmp_path.resolve())
    for body in (
        [1, 2],
        {"schema": 2, "vaults": {}},
        {"schema": 1, "vaults": {vault_key: "x"}},
        {"schema": 1, "vaults": {vault_key: {"providers": ["openai"]}}},
        {"schema": 1, "vaults": {vault_key: {"providers": {"openai": {"consented_at": 3}}}}},
    ):
        path.write_text(json.dumps(body), encoding="utf-8")
        assert not consent.is_consented(tmp_path, "openai")


def test_anthropic_needs_no_consent_record(home: Path, tmp_path: Path) -> None:
    assert consent.consent(tmp_path, "Anthropic") == ""
    assert not consent.record_path().exists()


def test_a_failed_write_leaves_no_temp_file(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(consent.os, "replace", boom)
    with pytest.raises(OSError):
        consent.consent(tmp_path, "openai")
    assert list(consent.record_path().parent.iterdir()) == []


# -- limits ----------------------------------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    (v / ".scout-logs").mkdir()
    return v


@pytest.mark.parametrize(
    "config",
    [
        "budget: 3\n",
        "budget:\n  per_runtime: [d]\n",
        "budget:\n  per_runtime:\n    d: 45\n",
    ],
)
def test_a_budget_block_of_the_wrong_shape_keeps_the_defaults(vault: Path, config: str) -> None:
    (vault / "scout-config.yaml").write_text(config, encoding="utf-8")
    lim = limits.limits_for(vault, DummyRuntime("d"))
    assert (lim.run_timeout_s, lim.timeout_source) == (45 * 60, "default")


def test_a_corrupt_owner_file_keeps_the_defaults(vault: Path) -> None:
    path = limits.run_limits_path()
    path.parent.mkdir(parents=True)
    for body in ("{not json", json.dumps({"schema": 9}), json.dumps({"schema": 1, "vaults": {str(vault): "x"}})):
        path.write_text(body, encoding="utf-8")
        assert limits.limits_for(vault, DummyRuntime("d")).timeout_source == "default"


def test_the_owner_can_set_the_ceiling_and_config_can_ask_for_the_formula(vault: Path) -> None:
    limits.set_owner_limit(vault, "d", max_tokens_per_day="auto")
    assert limits.limits_for(vault, DummyRuntime("d")).ceiling_source == "owner"
    limits.set_owner_limit(vault, "d", max_tokens_per_day="999999999")
    lim = limits.limits_for(vault, DummyRuntime("d"))
    assert (lim.max_tokens_per_day, lim.ceiling_source) == (999999999, "owner")
    (vault / "scout-config.yaml").write_text(
        "budget:\n  per_runtime:\n    d:\n      max_tokens_per_day: auto\n", encoding="utf-8"
    )
    lim = limits.limits_for(vault, DummyRuntime("d"))
    assert (lim.max_tokens_per_day, lim.ceiling_source) == (limits.FLOOR_TOKENS, "config")
    with pytest.raises(ValueError):
        limits.set_owner_limit(vault, "d", max_tokens_per_day="lots")


def test_the_formula_skips_rows_it_cant_use(vault: Path) -> None:
    rows = [
        "not json",
        json.dumps([1]),
        json.dumps({"ts": "garbage", "agent_runtime": "d", "tokens": 5}),
        json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "agent_runtime": "d", "tokens": 0}),
    ]
    (vault / ".scout-logs" / "usage-tracker.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    with registered(DummyRuntime("d")):
        (vault / ".scout-state" / "schedule.yaml").write_text(
            "schema_version: 1\nslots:\n  n:\n    type: dreaming\n    runner: run-dreaming.sh\n"
            '    fires_at_local: "02:00"\n    weekdays: [Mon]\n    missed_window_hours: 4\n'
            "    on_miss: skip\n    cooldown_minutes: 60\n    agent_runtime: d\n",
            encoding="utf-8",
        )
        assert limits.default_daily_token_ceiling(vault, "d") == limits.FLOOR_TOKENS


def test_a_failed_owner_write_leaves_no_temp_file(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(limits.os, "replace", boom)
    with pytest.raises(OSError):
        limits.set_owner_limit(vault, "d", run_timeout_minutes="30")
    assert [p.name for p in limits.run_limits_path().parent.iterdir()] == []


def test_an_owner_record_that_cant_be_read_means_the_defaults(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(path: Path) -> dict[str, object]:
        raise RuntimeError("unreadable")

    monkeypatch.setattr(limits, "_read_owner", boom)
    assert limits.owner_limits(vault, "d") == {}


def test_a_broken_schedule_falls_back_to_the_slot_keys_words(vault: Path) -> None:
    (vault / ".scout-state" / "schedule.yaml").write_text("slots: [\n", encoding="utf-8")
    assert limits.slot_kind(vault, "research-weekly") == "research"
    assert limits.slot_kind(vault, "weekly") is None


def test_config_off_for_the_ceiling_is_ignored_where_one_is_on(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text(
        "budget:\n  per_runtime:\n    d:\n      max_tokens_per_day: off\n", encoding="utf-8"
    )
    lim = limits.limits_for(vault, DummyRuntime("d"))
    assert lim.max_tokens_per_day == limits.FLOOR_TOKENS and lim.ignored == ("max_tokens_per_day: off",)
    (vault / "scout-config.yaml").write_text(
        "budget:\n  per_runtime:\n    claude-code:\n      max_tokens_per_day: off\n", encoding="utf-8"
    )
    assert limits.limits_for(vault, ClaudeCode()).ignored == ()
