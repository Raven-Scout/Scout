"""`scoutctl protection status --json` is a contract with the macOS app's Run protection card.

``tests/fixtures/contract/protection-status.json`` is the canonical copy; the app keeps a byte copy at
``apps/macos/ScoutTests/Fixtures/protection-status.json`` (``contract.yml`` compares them). Set
``SCOUT_REGEN_CONTRACT=1`` to rewrite the canonical file from the deterministic vault below, then ``cp`` it over
the app copy and update the pins in ``apps/macos/ScoutTests/Engine/RunProtectionTests.swift``.

The vault exercises every block: the owner turned the write sandbox off, the last privacy-check probe failed, the
last run had its protected files put back by the integrity check and ran without its broker, and two slots run
with an owner time limit and a default one.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import run_protection
from scout.agent_runtimes import limits

# Read at import: tests/conftest.py scrubs SCOUT_* from the environment before each test runs.
REGEN = os.environ.get("SCOUT_REGEN_CONTRACT") == "1"
CANONICAL = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "protection-status.json"

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)

SCHEDULE = """schema_version: 1
slots:
  morning-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 60
  research-weekly:
    type: research
    runner: run-research.sh
    fires_at_local: "14:00"
    weekdays: [Sat]
    missed_window_hours: 6
    on_miss: fire
    cooldown_minutes: 60
    agent_runtime: claude-code
"""


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def build_vault(root: Path) -> tuple[Path, dict[str, str]]:
    """The fixture's vault and the environment its report is taken in (a temp HOME, never the real one)."""
    vault = root / "Scout"
    home = root / "home"
    for d in (".scout-logs", ".scout-state", "knowledge-base"):
        (vault / d).mkdir(parents=True)
    home.mkdir()
    env = {"HOME": str(home), "SCOUT_LOCALTIME": str(home / "no-localtime")}
    (vault / "scout-config.yaml").write_text("user:\n  timezone: America/New_York\n", encoding="utf-8")
    (vault / ".scout-state" / "schedule.yaml").write_text(SCHEDULE, encoding="utf-8")

    # The owner turned the write sandbox off after the last run, and set a 120-minute limit for Claude Code.
    switch = home / ".local" / "state" / "scout" / "run-sandbox.json"
    switch.parent.mkdir(parents=True)
    switch.write_text(json.dumps({"scheduled_runs": False}) + "\n", encoding="utf-8")
    limits.set_owner_limit(vault, "claude-code", run_timeout_minutes="120", env=env)

    # Two runs: a clean one yesterday, and this morning's, whose protected files the integrity check put back.
    rows = []
    for finished, integrity in ((NOW - timedelta(days=1), "clean"), (NOW - timedelta(hours=3), "restored")):
        started = finished - timedelta(minutes=12)
        rows.append(
            {
                "slot": "morning-briefing",
                "mode": "scout",
                "started_at": _ts(started),
                "finished_at": _ts(finished),
                "duration_s": 720,
                "exit_code": 0,
                "failure_class": "ok",
                "outcome": "ok",
                "log": f"scout-{_ts(started)}.log",
                "sandbox": "enforced",
                "integrity": integrity,
            }
        )
        (vault / ".scout-logs" / f"scout-{_ts(started)}.log").write_text("run log\n", encoding="utf-8")
    (vault / ".scout-logs" / "run-outcomes.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    # That run's broker didn't start.
    (vault / ".scout-logs" / ".run-broker.json").write_text(
        json.dumps(
            {
                "at": _ts(NOW - timedelta(hours=3, minutes=12)),
                "status": "unavailable",
                "reason": "git-safe-commit.sh predates the broker",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # And the probe the runners ran an hour ago found claude not loading Scout's hooks.
    (vault / ".scout-state" / "gate-probe.json").write_text(
        json.dumps(
            {
                "ok": False,
                "reason": "claude ran, but Scout's kb-read-gate never fired",
                "claude_version": "2.9.0",
                "hooks_sha256": "0" * 64,
                "flags": ["--permission-mode", "auto"],
                "env_key": "1" * 64,
                "config_sha256": "2" * 64,
                "checked_at": (NOW - timedelta(hours=1)).isoformat(),
                "unsafe": "",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return vault, env


def render(root: Path) -> str:
    vault, env = build_vault(root)
    return json.dumps(run_protection.report(vault, now=NOW, env=env), indent=2) + "\n"


def test_protection_status_matches_the_shared_fixture(tmp_path: Path) -> None:
    text = render(tmp_path)
    if REGEN:
        CANONICAL.write_text(text, encoding="utf-8")
    assert CANONICAL.read_text(encoding="utf-8") == text, (
        "regenerate with SCOUT_REGEN_CONTRACT=1, then cp it to apps/macos/ScoutTests/Fixtures/protection-status.json"
    )


@pytest.fixture
def doc(tmp_path: Path) -> dict:
    return json.loads(render(tmp_path))


def test_the_fixture_exercises_every_block(doc: dict) -> None:
    assert doc["schema_version"] == run_protection.SCHEMA_VERSION == 1
    assert doc["evaluated_at"] == "2026-11-16T12:00:00Z"
    assert doc["severity"] == "red"
    assert doc["sandbox"] == {
        "status": "off",
        "reason": doc["sandbox"]["reason"],
        "switch": "off",
        "switch_source": "owner",
    }
    assert doc["gate_probe"]["status"] == "failed"
    assert doc["gate_probe"]["checked_at"] == "2026-11-16T11:00:00Z"
    assert doc["gate_probe"]["stale"] is False
    assert doc["last_run"]["integrity"] == "restored"
    assert doc["last_run"]["log"].startswith(".scout-logs/scout-")
    assert doc["broker"]["status"] == "unavailable"
    by_key = {s["key"]: s for s in doc["slots"]}
    assert by_key["morning-briefing"]["runtime_source"] != "slot"
    assert by_key["research-weekly"]["runtime_source"] == "slot"
    assert {s["run_timeout_s"] for s in doc["slots"]} == {7200}
    assert {s["timeout_source"] for s in doc["slots"]} == {"owner"}


def test_every_problem_has_a_fix_and_a_known_action(doc: dict) -> None:
    ids = [p["id"] for p in doc["problems"]]
    assert ids == ["run.integrity_restored", "gates.probe_failed", "sandbox.off", "broker.unavailable"]
    for p in doc["problems"]:
        assert set(p) == {"id", "severity", "summary", "fix", "action", "log"}
        assert p["fix"]
        assert p["action"] is None or p["action"] in run_protection.ACTIONS
        assert p["log"] is None or not p["log"].startswith("/")
    actions = {p["id"]: p["action"] for p in doc["problems"]}
    assert actions["sandbox.off"] == "turn_on_sandbox"
    assert actions["gates.probe_failed"] == "check_gates"
    assert actions["run.integrity_restored"] == "show_log"


def test_no_absolute_path_reaches_the_fixture(doc: dict, tmp_path: Path) -> None:
    assert str(tmp_path) not in json.dumps(doc)
