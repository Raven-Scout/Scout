"""``scoutctl runs --json`` is a contract with the macOS app's Health panel.

``tests/fixtures/contract/run-ledger.json`` is the canonical copy; the app keeps a byte copy at
``apps/macos/ScoutTests/Fixtures/run-ledger.json`` (``contract.yml`` compares them). Set
``SCOUT_REGEN_CONTRACT=1`` to rewrite the canonical file from the deterministic vault below — never edit
it by hand. The vault holds one row per outcome family the app must name: a clean run, a timeout, a
usage limit with its reset, an integrity restore, and every skip (privacy checks, write protection,
agent runtime, writer lease, lock, budget, redundant).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout import health, run_ledger
from scout.health import SKIP_OUTCOMES

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
# Read at import: tests/conftest.py scrubs SCOUT_* from the environment before each test runs.
REGEN = os.environ.get("SCOUT_REGEN_CONTRACT") == "1"
CANONICAL = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "run-ledger.json"
SCHEDULE = """\
schema_version: 1
slots:
  morning-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 0
  evening-consolidation:
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "18:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 0
  weekly-research:
    type: research
    runner: run-research.sh
    fires_at_local: "14:00"
    weekdays: [Sun]
    missed_window_hours: 4
    on_miss: skip
    cooldown_minutes: 0
"""
PROTECTED = {"sandbox": "enforced", "integrity": "clean", "agent_runtime": "claude-code", "trigger": "schedule"}

# (hours before NOW it finished, minutes it took, slot, runner, row fields). Oldest first, as the ledger is.
RUNS: list[tuple[float, int, str, str, dict]] = [
    (75, 6, "morning-briefing", "scout", {"outcome": "ok", "model": "sonnet", **PROTECTED}),
    (
        70.5,
        90,
        "weekly-research",
        "research",
        {"outcome": "failed", "exit_code": 124, "failure_class": "timeout", "model": "opus", **PROTECTED},
    ),
    (66, 3, "evening-consolidation", "scout", {"outcome": "ok", "model": "sonnet", **PROTECTED}),
    (
        51,
        4,
        "morning-briefing",
        "scout",
        {"outcome": "ok", "model": "sonnet", **PROTECTED, "integrity": "restored", "newest_commit": "3f9c2a1"},
    ),
    (42, 0, "evening-consolidation", "scout", {"outcome": "skipped_lock"}),
    (
        27,
        2,
        "morning-briefing",
        "scout",
        {"outcome": "failed", "exit_code": 1, "failure_class": "rate_limited", "resets_hours": 30, **PROTECTED},
    ),
    (18, 0, "evening-consolidation", "scout", {"outcome": "skipped_budget"}),
    (9, 0, "evening-consolidation", "scout", {"outcome": "skipped_redundant"}),
    (8, 0, "evening-consolidation", "scout", {"outcome": "skipped_lease", "failure_class": "lease_held"}),
    (6, 0, "evening-consolidation", "scout", {"outcome": "skipped_runtime"}),
    (5, 0, "evening-consolidation", "scout", {"outcome": "skipped_sandbox", "sandbox": "failed"}),
    (
        4,
        5,
        "morning-briefing",
        "scout",
        {"outcome": "ok", "model": "sonnet", **PROTECTED, "sandbox": "off", "sandbox_source": "switch"},
    ),
    (3.5, 0, "morning-briefing", "scout", {"outcome": "skipped_gates", "failure_class": "gates_unsafe_endpoint"}),
]


def _build_vault(root: Path) -> Path:
    v = root / "Scout"
    state, logs = v / ".scout-state", v / ".scout-logs"
    state.mkdir(parents=True)
    logs.mkdir()
    (state / "schedule.yaml").write_text(SCHEDULE, encoding="utf-8")
    old = (NOW - timedelta(days=60)).timestamp()
    os.utime(state / "schedule.yaml", (old, old))
    rows, fires = [], {}
    for hours_ago, minutes, slot, runner, fields in RUNS:
        fields = dict(fields)
        finished = NOW - timedelta(hours=hours_ago)
        started = finished - timedelta(minutes=minutes)
        outcome = fields.pop("outcome")
        resets = fields.pop("resets_hours", None)
        log = f"{runner}-{started.strftime('%Y-%m-%d_%H-%M')}.log"
        row = {
            "slot": slot,
            "mode": runner if runner != "scout" else "scout",
            "runner": runner,
            "started_at": int(started.timestamp()),
            "finished_at": int(finished.timestamp()),
            "duration_s": minutes * 60,
            "exit_code": fields.pop("exit_code", 0),
            "failure_class": fields.pop("failure_class", "ok"),
            "outcome": outcome,
            "log": log,
            "commits_in_window": 1 if outcome == "ok" else 0,
            "newest_commit": fields.pop("newest_commit", "a1b2c3d" if outcome == "ok" else None),
            **fields,
        }
        if outcome not in SKIP_OUTCOMES:
            row["awake_s"] = minutes * 60
        if resets is not None:
            row["resets_at"] = int((NOW + timedelta(hours=resets - hours_ago)).timestamp())
        rows.append(row)
        (logs / log).write_text("=== Scout run starting ===\n", encoding="utf-8")
        fires[slot] = finished.isoformat().replace("+00:00", "Z")
    (logs / "run-outcomes.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (state / "last-fire.json").write_text(json.dumps({"schema_version": 1, "last_fire": fires}), encoding="utf-8")
    tick = {"id": "x", "ts": "2026-11-16T11:55:00Z", "kind": "schedule.tick.completed", "source": "t", "payload": {}}
    (logs / "schedule-events-2026-11-16.jsonl").write_text(json.dumps(tick) + "\n", encoding="utf-8")
    return v


def _render(tmp_path: Path, monkeypatch) -> str:
    monkeypatch.setattr(health, "vault_birth", lambda _v: NOW - timedelta(days=60))
    monkeypatch.setattr(health, "tick_job_installed", lambda home=None: True)
    monkeypatch.setattr(health, "recent_commits", lambda *a, **k: [])
    v = _build_vault(tmp_path)
    doc = run_ledger.ledger_report(v, now=NOW, home=tmp_path / "home")
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    return text.replace(str(v), "/Users/alex/Scout").replace(str(tmp_path), "/Users/alex")


def test_run_ledger_matches_the_shared_fixture(tmp_path, monkeypatch):
    text = _render(tmp_path, monkeypatch)
    if REGEN:
        CANONICAL.write_text(text, encoding="utf-8")
    assert CANONICAL.read_text(encoding="utf-8") == text, (
        "regenerate with SCOUT_REGEN_CONTRACT=1, then cp it to apps/macos/ScoutTests/Fixtures/run-ledger.json"
    )


def test_the_fixture_covers_every_outcome_the_app_names(tmp_path, monkeypatch):
    doc = json.loads(_render(tmp_path, monkeypatch))
    assert doc["schema_version"] == 1
    runs = doc["runs"]
    assert {r["outcome"] for r in runs} == {"ok", "failed"} | SKIP_OUTCOMES
    assert {r["failure_class"] for r in runs if r["outcome"] == "failed"} == {"timeout", "rate_limited"}
    assert {r["sandbox"] for r in runs} >= {"enforced", "off", "failed", None}
    assert {r["integrity"] for r in runs} >= {"clean", "restored", None}
    assert any(r["resets_at"] for r in runs) and any(r["awake_s"] for r in runs)
    finished = [r["finished_at"] for r in runs]
    assert finished == sorted(finished, reverse=True)


def test_the_fixture_carries_findings_with_fixes(tmp_path, monkeypatch):
    rh = json.loads(_render(tmp_path, monkeypatch))["health"]
    ids = {f["id"] for f in rh["findings"]}
    assert rh["severity"] == "red"
    # The integrity restore and the privacy-check skip are both on the panel, each with a fix.
    assert any(i.startswith("run.integrity") for i in ids), ids
    assert any(i.startswith("gates.") for i in ids), ids
    assert all(f["fix"] for f in rh["findings"] if f["severity"] != "note")
    assert all({"detail", "log", "until"} <= set(f) for f in rh["findings"])


def test_no_machine_or_vault_content_leaks(tmp_path, monkeypatch):
    text = _render(tmp_path, monkeypatch)
    assert "pytest" not in text and str(Path.home()) not in text
    for run in json.loads(text)["runs"]:
        assert run["log"] is None or "/" not in run["log"]
