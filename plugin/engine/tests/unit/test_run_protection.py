"""`scoutctl protection status` (app card C9) and the owner-only `scoutctl sandbox off`.

HOME is a per-test temp dir (tests/conftest.py's _hermetic_env), so no test reads or flips the real switch.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import run_protection
from scout.cli import app
from scout.sandbox import launch

runner = CliRunner()
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    for d in (".scout-logs", ".scout-state"):
        (v / d).mkdir(parents=True)
    return v


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    return {"HOME": str(home), "SCOUT_LOCALTIME": str(home / "no-localtime")}


def _prepared(status: str, reason: str = "why"):
    return lambda _v, _e: launch.Prepared(status, reason)


def _ledger(vault: Path, *rows: dict) -> None:
    lines = []
    for i, extra in enumerate(rows):
        finished = NOW - timedelta(hours=len(rows) - i)
        started = finished - timedelta(minutes=5)
        row = {
            "slot": "morning-briefing",
            "mode": "scout",
            "started_at": int(started.timestamp()),
            "finished_at": int(finished.timestamp()),
            "exit_code": 0,
            "failure_class": "ok",
            "log": f"scout-{int(started.timestamp())}.log",
            **extra,
        }
        lines.append(json.dumps(row))
    (vault / ".scout-logs" / "run-outcomes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _probe(vault: Path, *, ok: bool, unsafe: str = "", at: datetime = NOW - timedelta(hours=1)) -> None:
    data = {
        "ok": ok,
        "reason": "the reason",
        "claude_version": "2.9.0",
        "hooks_sha256": "a",
        "flags": [],
        "env_key": "b",
        "config_sha256": "c",
        "checked_at": at.isoformat(),
        "unsafe": unsafe,
    }
    (vault / ".scout-state" / "gate-probe.json").write_text(json.dumps(data), encoding="utf-8")


def _report(vault: Path, env: dict[str, str], status: str = "enforced") -> dict:
    return run_protection.report(vault, now=NOW, env=env, prepare=_prepared(status))


def _ids(doc: dict) -> list[str]:
    return [p["id"] for p in doc["problems"]]


# -- a clean vault ------------------------------------------------------------------------------------------------


def test_an_enforced_sandbox_and_no_runs_is_green(vault: Path, env: dict[str, str]) -> None:
    doc = _report(vault, env)
    assert doc["severity"] == "green"
    assert doc["problems"] == []
    assert doc["sandbox"]["status"] == "enforced"
    assert doc["sandbox"]["switch"] == "on" and doc["sandbox"]["switch_source"] == "default"
    assert doc["gate_probe"]["status"] == "not_checked"
    assert doc["last_run"] is None
    assert doc["broker"] == {"status": "not_recorded", "reason": "", "recorded_at": None}
    # No vault schedule: the shipped one, every slot on Claude Code.
    assert doc["slots"] and {s["runtime"] for s in doc["slots"]} == {"claude-code"}


def test_the_shells_sandbox_switch_is_left_out(vault: Path, env: dict[str, str]) -> None:
    """Scheduled runs don't inherit SCOUT_RUN_SANDBOX, so the report doesn't read it (as the doctor does)."""
    seen: list[dict] = []

    def prepare(_v, e):
        seen.append(dict(e))
        return launch.Prepared("enforced", "ok")

    run_protection.report(vault, now=NOW, env={**env, "SCOUT_RUN_SANDBOX": "off"}, prepare=prepare)
    assert "SCOUT_RUN_SANDBOX" not in seen[0]


def test_inside_a_sandboxed_run_it_reports_enforced_without_self_testing(vault: Path, env: dict[str, str]) -> None:
    def boom(*_a):
        raise AssertionError("must not nest")

    doc = run_protection.report(vault, now=NOW, env={**env, "SCOUT_SANDBOX_STATUS": "enforced"}, prepare=boom)
    assert doc["sandbox"]["status"] == "enforced"


# -- the sandbox ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "pid", "severity", "action"),
    [
        ("failed", "sandbox.not_ready", "red", "check_sandbox"),
        ("unavailable", "sandbox.unavailable", "yellow", "check_sandbox"),
        ("conflict", "sandbox.conflict", "yellow", "check_sandbox"),
        ("off", "sandbox.off", "yellow", "turn_on_sandbox"),
    ],
)
def test_each_sandbox_status_names_its_problem(
    vault: Path, env: dict[str, str], status: str, pid: str, severity: str, action: str
) -> None:
    doc = _report(vault, env, status)
    (p,) = doc["problems"]
    assert (p["id"], p["severity"], p["action"]) == (pid, severity, action)
    assert doc["severity"] == severity


def test_an_owner_switch_reads_as_owner_and_never_names_its_path(vault: Path, env: dict[str, str]) -> None:
    launch.set_switch(False, env)
    doc = run_protection.report(vault, now=NOW, env=env)  # the real prepare: off decides before any platform check
    assert doc["sandbox"]["status"] == "off"
    assert doc["sandbox"]["switch_source"] == "owner"
    assert env["HOME"] not in json.dumps(doc)


def test_a_check_that_throws_is_a_yellow_problem_not_a_crash(vault: Path, env: dict[str, str]) -> None:
    def boom(*_a):
        raise RuntimeError("kaput")

    doc = run_protection.report(vault, now=NOW, env=env, prepare=boom)
    assert doc["sandbox"]["status"] == "unknown"
    assert _ids(doc) == ["sandbox.unchecked"]


def test_skipped_runs_and_a_failing_sandbox_now_are_one_problem(vault: Path, env: dict[str, str]) -> None:
    _ledger(vault, {"outcome": "skipped_sandbox", "sandbox": "failed", "exit_code": 3})
    assert _ids(_report(vault, env, "failed")) == ["sandbox.failed"]


def test_what_is_wrong_now_replaces_the_last_runs_unsandboxed_note(vault: Path, env: dict[str, str]) -> None:
    _ledger(vault, {"sandbox": "unavailable"})
    assert _ids(_report(vault, env, "unavailable")) == ["sandbox.unavailable"]
    # Fixed since: the last run's word stays.
    assert _ids(_report(vault, env, "enforced")) == ["runs.unsandboxed"]


# -- the privacy-check probe ----------------------------------------------------------------------------------------


def test_a_passed_probe_is_no_problem_and_ages(vault: Path, env: dict[str, str]) -> None:
    _probe(vault, ok=True)
    doc = _report(vault, env)
    assert doc["gate_probe"]["status"] == "passed" and doc["gate_probe"]["stale"] is False
    assert doc["problems"] == []
    _probe(vault, ok=True, at=NOW - timedelta(days=2))
    assert _report(vault, env)["gate_probe"]["stale"] is True


@pytest.mark.parametrize(("unsafe", "status"), [("", "failed"), ("endpoint", "unsafe")])
def test_a_failed_probe_is_red_with_check_again(vault: Path, env: dict[str, str], unsafe: str, status: str) -> None:
    _probe(vault, ok=False, unsafe=unsafe)
    doc = _report(vault, env)
    assert doc["gate_probe"]["status"] == status
    (p,) = doc["problems"]
    assert (p["id"], p["severity"], p["action"]) == ("gates.probe_failed", "red", "check_gates")


def test_runs_skipped_by_the_probe_say_it_once(vault: Path, env: dict[str, str]) -> None:
    _probe(vault, ok=False)
    _ledger(vault, {"outcome": "skipped_gates", "failure_class": "gates_not_loaded", "exit_code": 3})
    assert _ids(_report(vault, env)) == ["gates.not_loaded"]


def test_a_corrupt_probe_cache_reads_as_not_checked(vault: Path, env: dict[str, str]) -> None:
    (vault / ".scout-state" / "gate-probe.json").write_text("{nope", encoding="utf-8")
    assert _report(vault, env)["gate_probe"]["status"] == "not_checked"


# -- the last run and the broker ------------------------------------------------------------------------------------


def test_the_last_run_skips_skipped_runs_and_logs_relative(vault: Path, env: dict[str, str]) -> None:
    _ledger(vault, {"sandbox": "enforced", "integrity": "clean"}, {"outcome": "skipped_budget", "exit_code": 1})
    last = _report(vault, env)["last_run"]
    assert last["integrity"] == "clean" and last["sandbox"] == "enforced"
    assert last["log"].startswith(".scout-logs/scout-")


def test_a_restored_run_is_red_with_its_log(vault: Path, env: dict[str, str]) -> None:
    _ledger(vault, {"sandbox": "enforced", "integrity": "restored"})
    (p,) = _report(vault, env)["problems"]
    assert (p["id"], p["action"]) == ("run.integrity_restored", "show_log")


def _broker(vault: Path, status: str) -> None:
    data = {"at": int((NOW - timedelta(hours=2)).timestamp()), "status": status, "reason": "old helper"}
    (vault / ".scout-logs" / ".run-broker.json").write_text(json.dumps(data), encoding="utf-8")


def test_a_missing_broker_is_a_problem_only_for_a_sandboxed_run(vault: Path, env: dict[str, str]) -> None:
    _broker(vault, "unavailable")
    _ledger(vault, {"sandbox": "enforced", "integrity": "clean"})
    assert _ids(_report(vault, env)) == ["broker.unavailable"]
    _ledger(vault, {"sandbox": "off", "sandbox_source": "switch"})
    assert "broker.unavailable" not in _ids(_report(vault, env))


def test_an_unknown_broker_status_reads_as_not_recorded(vault: Path, env: dict[str, str]) -> None:
    _broker(vault, "sideways")
    assert _report(vault, env)["broker"]["status"] == "not_recorded"


# -- the slots ------------------------------------------------------------------------------------------------------


def test_a_slot_with_an_unknown_runtime_says_so(vault: Path, env: dict[str, str]) -> None:
    (vault / "scout-config.yaml").write_text("agent_runtimes:\n  default: nope\n", encoding="utf-8")
    slots = _report(vault, env)["slots"]
    assert slots and all(s["error"] and "nope" in s["error"] and s["runtime"] is None for s in slots)


def test_a_broken_schedule_gives_no_slots(vault: Path, env: dict[str, str]) -> None:
    (vault / ".scout-state" / "schedule.yaml").write_text("schema_version: 9\nslots: {}\n", encoding="utf-8")
    assert _report(vault, env)["slots"] == []


# -- the CLI --------------------------------------------------------------------------------------------------------


def test_protection_status_json_exits_0(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch, "prepare", _prepared("unavailable", "no sandbox"))
    result = runner.invoke(app, ["protection", "status", "--vault", str(vault), "--json"])
    assert result.exit_code == 0
    doc = json.loads(result.stdout)
    assert doc["schema_version"] == 1 and doc["sandbox"]["status"] == "unavailable"
    text = runner.invoke(app, ["protection", "status", "--vault", str(vault)])
    assert text.exit_code == 0 and text.stdout.startswith("run protection: yellow")


@pytest.mark.parametrize("headless", [{"SCOUT_MODE": "morning-briefing"}, {"SCOUT_READER": "autonomous"}])
def test_sandbox_off_refuses_a_run(monkeypatch: pytest.MonkeyPatch, headless: dict[str, str]) -> None:
    monkeypatch.delenv("SCOUT_RUN_SANDBOX", raising=False)
    for k, v in headless.items():
        monkeypatch.setenv(k, v)
    result = runner.invoke(app, ["sandbox", "off"])
    assert result.exit_code == 2
    assert "Only the owner" in result.output
    assert launch.switch()[0] is True
    assert not launch.switch_path().exists()


def test_sandbox_on_stays_open_to_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turning protection on is never a risk, so a run may do it."""
    monkeypatch.delenv("SCOUT_RUN_SANDBOX", raising=False)
    monkeypatch.setenv("SCOUT_MODE", "morning-briefing")
    assert runner.invoke(app, ["sandbox", "on"]).exit_code == 0
    assert launch.switch()[0] is True
