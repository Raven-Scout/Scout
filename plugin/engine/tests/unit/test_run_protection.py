"""`scoutctl protection status` (app card C9) and the owner-only `scoutctl sandbox off`.

HOME is a per-test temp dir (tests/conftest.py's _hermetic_env), so no test reads or flips the real switch.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import run_protection
from scout.cli import app
from scout.sandbox import launch
from tests.unit.test_run_protection_contract import SCHEDULE

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


def _no_precheck(_v, _e):
    return None


def _report(vault: Path, env: dict[str, str], status: str = "enforced") -> dict:
    """The report with the sandbox at ``status``: off/unavailable/conflict as the precheck decides them,
    enforced/failed as the owner's self-test (--check-sandbox) finds them."""
    if status in ("enforced", "failed"):
        return run_protection.report(
            vault, now=NOW, env=env, check_sandbox=True, precheck=_no_precheck, prepare=_prepared(status)
        )
    return run_protection.report(vault, now=NOW, env=env, precheck=_prepared(status))


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

    def precheck(_v, e):
        seen.append(dict(e))

    def prepare(_v, e):
        seen.append(dict(e))
        return launch.Prepared("enforced", "ok")

    run_protection.report(
        vault, now=NOW, env={**env, "SCOUT_RUN_SANDBOX": "off"}, check_sandbox=True, precheck=precheck, prepare=prepare
    )
    assert len(seen) == 2 and all("SCOUT_RUN_SANDBOX" not in e for e in seen)


def test_inside_a_sandboxed_run_it_reports_enforced_without_self_testing(vault: Path, env: dict[str, str]) -> None:
    def boom(*_a):
        raise AssertionError("must not nest")

    doc = run_protection.report(
        vault, now=NOW, env={**env, "SCOUT_SANDBOX_STATUS": "enforced"}, check_sandbox=True, precheck=boom, prepare=boom
    )
    assert doc["sandbox"]["status"] == "enforced" and doc["sandbox"]["checked_by"] == "this_run"


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

    doc = run_protection.report(vault, now=NOW, env=env, precheck=boom)
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


def test_without_a_check_the_newest_run_says_whether_it_works(vault: Path, env: dict[str, str]) -> None:
    """The plain report never builds or self-tests the profile: enforced/failed come from the run ledger."""

    def boom(*_a):
        raise AssertionError("self-tested without --check-sandbox")

    def report() -> dict:
        return run_protection.report(vault, now=NOW, env=env, precheck=_no_precheck, prepare=boom)

    doc = report()
    assert doc["sandbox"]["status"] == "not_checked" and doc["sandbox"]["checked_by"] is None
    assert doc["problems"] == []

    _ledger(vault, {"sandbox": "enforced", "integrity": "clean"})
    sb = report()["sandbox"]
    assert (sb["status"], sb["checked_by"]) == ("enforced", "last_run")
    assert "morning-briefing" in sb["reason"]

    # A sandbox that can't be set up skips the run, and that skipped run is the newest word: one problem.
    _ledger(
        vault,
        {"sandbox": "enforced", "integrity": "clean"},
        {"outcome": "skipped_sandbox", "sandbox": "failed", "exit_code": 3},
    )
    doc = report()
    assert (doc["sandbox"]["status"], doc["sandbox"]["checked_by"]) == ("failed", "last_run")
    assert _ids(doc) == ["sandbox.failed"]

    # A run that went without it (switched off then) says nothing about whether it works now.
    _ledger(vault, {"sandbox": "off", "sandbox_source": "switch"})
    assert report()["sandbox"]["status"] == "not_checked"


def test_the_precheck_never_builds_the_profile(
    vault: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout.sandbox import profile

    def boom(*_a, **_k):
        raise AssertionError("built the profile")

    monkeypatch.setattr(profile, "build_profile", boom)
    monkeypatch.setattr(profile, "self_test", boom)
    assert launch.precheck(vault, env, available=lambda: True) is None
    assert launch.precheck(vault, env, available=lambda: False).status == "unavailable"


def _tree(*roots: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for root in roots:
        for p in [root, *root.rglob("*")]:
            st = p.lstat()
            out[str(p)] = (st.st_size, st.st_mtime_ns)
    return out


def test_status_starts_no_process_and_writes_nothing(
    vault: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app reads it whenever Settings ▸ Engine appears, so it must not run sandbox-exec, git or anything else."""
    _ledger(vault, {"sandbox": "enforced", "integrity": "restored"})
    _probe(vault, ok=False)
    _broker(vault, "unavailable")
    (vault / ".scout-state" / "schedule.yaml").write_text(SCHEDULE, encoding="utf-8")
    real_precheck = launch.precheck

    def no_process(*a, **_k):
        raise AssertionError(f"started a process: {a[:1]}")

    for mod, name in [
        (subprocess, "Popen"),
        (os, "posix_spawn"),
        (os, "posix_spawnp"),
        (os, "fork"),
        (os, "system"),
        (tempfile, "mkdtemp"),
        (tempfile, "mkstemp"),
    ]:
        monkeypatch.setattr(mod, name, no_process)
    # As on a Mac with sandbox-exec, so nothing stops short of where a build would start.
    monkeypatch.setattr(launch, "precheck", lambda v, e: real_precheck(v, e, available=lambda: True))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    before = _tree(vault, Path(env["HOME"]))

    doc = run_protection.report(vault, now=NOW, env=env)
    result = runner.invoke(app, ["protection", "status", "--vault", str(vault), "--json"])

    assert result.exit_code == 0, result.output
    assert (doc["sandbox"]["status"], doc["sandbox"]["checked_by"]) == ("enforced", "last_run")
    assert doc["slots"] and doc["slots"][0]["runtime"] == "claude-code"
    assert _tree(vault, Path(env["HOME"])) == before


def test_only_check_sandbox_self_tests(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def prepare(_v, _e):
        calls.append(1)
        return launch.Prepared("enforced", "sandbox-exec, self-test ok")

    monkeypatch.setattr(launch, "precheck", _no_precheck)
    monkeypatch.setattr(launch, "prepare", prepare)
    plain = json.loads(runner.invoke(app, ["protection", "status", "--vault", str(vault), "--json"]).stdout)
    assert calls == [] and plain["sandbox"]["status"] == "not_checked"
    args = ["protection", "status", "--vault", str(vault), "--json", "--check-sandbox"]
    checked = json.loads(runner.invoke(app, args).stdout)
    assert calls == [1]
    assert (checked["sandbox"]["status"], checked["sandbox"]["checked_by"]) == ("enforced", "self_test")


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


CANARY = "sk-ant-api03-" + "Q7" * 24


def test_text_a_run_can_write_is_shape_checked(vault: Path, env: dict[str, str]) -> None:
    """.scout-logs and .scout-state are writable by a run: nothing from them reaches the app unchecked."""
    broker = {"at": int(NOW.timestamp()), "status": "unavailable", "reason": f"token={CANARY} " + "x " * 500}
    (vault / ".scout-logs" / ".run-broker.json").write_text(json.dumps(broker), encoding="utf-8")
    probe = {
        "ok": False,
        "reason": f"Authorization: Bearer {CANARY}\n/Users/alex/.secrets/{CANARY}",
        "claude_version": "2.9.0; curl evil",
        "hooks_sha256": "a",
        "flags": [],
        "env_key": "b",
        "config_sha256": "c",
        "checked_at": (NOW - timedelta(hours=1)).isoformat(),
        "unsafe": "",
    }
    (vault / ".scout-state" / "gate-probe.json").write_text(json.dumps(probe), encoding="utf-8")
    _ledger(
        vault,
        {
            "slot": "morning\nbriefing <b>" + CANARY,
            "mode": "m" * 200,
            "outcome": "Ok!",
            "sandbox": "enforced",
            "integrity": "sideways",
            "log": "../../etc/passwd",
        },
    )
    doc = _report(vault, env)
    text = json.dumps(doc)
    assert "Q7Q7" not in text and "sk-ant" not in text and "alex" not in text and "\\n" not in text

    assert doc["broker"]["reason"].startswith("token=[redacted] x x")
    assert len(doc["broker"]["reason"]) <= 240
    assert doc["gate_probe"]["claude_version"] is None
    assert doc["gate_probe"]["reason"].startswith("Authorization: [redacted]")
    last = doc["last_run"]
    assert (last["slot"], last["mode"]) == ("unknown", "unknown")
    assert last["integrity"] is None and last["log"] is None
    assert all(p["log"] is None or p["log"].startswith(".scout-logs/") for p in doc["problems"])


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
    monkeypatch.setattr(launch, "precheck", _prepared("unavailable", "no sandbox"))
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


# -- odd input reads as unknown, never as a crash -------------------------------------------------------------------


def test_paths_outside_the_vault_or_oddly_shaped_are_not_shown(vault: Path) -> None:
    assert run_protection._rel(vault, str(vault / ".scout-logs" / "scout-1.log")) == ".scout-logs/scout-1.log"
    assert run_protection._rel(vault, "/elsewhere/scout-2.log") == "scout-2.log"
    assert run_protection._rel(vault, str(vault / "a b" / "scout-3.log")) is None
    assert run_protection._rel(vault, str(vault / "a" / "b" / "c" / "d" / "scout-4.log")) is None
    assert run_protection._rel(vault, None) is None


def test_odd_times_and_text_read_as_unknown(vault: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.agent_runtimes import gate_probe

    cached = gate_probe.ProbeResult(False, "the reason", "2.9.0", "a", (), "b", "c", "not a time", "")
    monkeypatch.setattr(gate_probe, "read_cache", lambda _v: cached)
    for at, reason in ((10**20, 42), ("yesterday", None)):
        broker = {"at": at, "status": "unavailable", "reason": reason}
        (vault / ".scout-logs" / ".run-broker.json").write_text(json.dumps(broker), encoding="utf-8")
        doc = _report(vault, env)
        assert doc["broker"] == {"status": "unavailable", "reason": "", "recorded_at": None}
        assert doc["gate_probe"]["checked_at"] is None and doc["gate_probe"]["stale"] is True


def test_an_unreadable_ledger_reads_as_no_runs(
    vault: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout import health

    def boom(_v):
        raise OSError("unreadable")

    monkeypatch.setattr(health, "read_outcomes", boom)
    doc = _report(vault, env)
    assert doc["last_run"] is None and doc["severity"] == "green"


def test_the_text_form_names_the_last_run_and_a_broken_slot(vault: Path, env: dict[str, str]) -> None:
    _ledger(vault, {"sandbox": "enforced", "integrity": "clean"})
    (vault / "scout-config.yaml").write_text("agent_runtimes:\n  default: nope\n", encoding="utf-8")
    text = run_protection.format_text(_report(vault, env))
    assert "last run: morning-briefing at " in text and "integrity clean" in text
    assert "nope" in text
