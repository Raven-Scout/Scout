"""The doctor's ``run_health`` JSON is a contract with the macOS app.

``tests/fixtures/contract/doctor-run-health.json`` is the canonical copy; the app keeps a byte copy at
``apps/macos/ScoutTests/Fixtures/doctor-run-health.json`` (``contract.yml`` compares them). Set
``SCOUT_REGEN_CONTRACT=1`` to rewrite the canonical file from the deterministic vault below.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from scout import health
from scout.scripts import bootstrap_doctor
from scout.scripts.bootstrap_auto import doctor_dict

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
# Read at import: tests/conftest.py scrubs SCOUT_* from the environment before each test runs.
REGEN = os.environ.get("SCOUT_REGEN_CONTRACT") == "1"
CANONICAL = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "doctor-run-health.json"
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
  weekly-research:
    type: research
    runner: run-research.sh
    fires_at_local: "14:00"
    weekdays: [Wed]
    missed_window_hours: 4
    on_miss: skip
    cooldown_minutes: 0
"""


def _ts(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _build_vault(root: Path) -> Path:
    v = root / "Scout"
    for rel in (
        "scripts/heartbeat.sh",
        "knowledge-base/ontology/parser.py",
        "action-items/render.py",
        "hooks/kb-pre-filter.sh",
    ):
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        (v / rel).write_text("x\n", encoding="utf-8")
    state, logs = v / ".scout-state", v / ".scout-logs"
    (state / "last-assembled").mkdir(parents=True)
    logs.mkdir()
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        (state / "last-assembled" / f"{name}.md").write_text("x\n", encoding="utf-8")
    (state / "schedule.yaml").write_text(SCHEDULE, encoding="utf-8")
    old = (NOW - timedelta(days=60)).timestamp()
    os.utime(state / "schedule.yaml", (old, old))
    (v / "scout-config.yaml").write_text(
        "plugin:\n  version_at_last_setup: 0.16.0\n  version_at_last_update: 0.16.0\n", encoding="utf-8"
    )
    # The one install warning: a runner backup left by an older engine.
    (v / "run-scout.sh.bak.1").write_text("x\n", encoding="utf-8")
    # morning-briefing last ran 54 h ago (a yellow slot.stale): its last two runs failed on an expired
    # login. Its last fire is that last run's session end, 2 s before the ledger row's finished_at
    # (§13.1). weekly-research fired on its own Wednesday slot and left no outcome (slot.fired_silent).
    starts = (NOW - timedelta(hours=100), NOW - timedelta(hours=78), NOW - timedelta(hours=54))
    fires = {
        "morning-briefing": _ts(starts[-1] + timedelta(seconds=60 - 2)),
        "weekly-research": _ts(datetime(2026, 10, 7, 14, 0, tzinfo=UTC)),
    }
    (state / "last-fire.json").write_text(json.dumps({"schema_version": 1, "last_fire": fires}), encoding="utf-8")
    rows = []
    for started, klass in zip(starts, ("ok", "oauth_expired", "oauth_expired"), strict=True):
        s = int(started.timestamp())
        rows.append(
            {
                "slot": "morning-briefing",
                "mode": "scout",
                "started_at": s,
                "finished_at": s + 60,
                "duration_s": 60,
                "exit_code": 0 if klass == "ok" else 1,
                "failure_class": klass,
                "log": None,
            }
        )
    (logs / "run-outcomes.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    tick = {
        "id": "x",
        "ts": _ts(NOW - timedelta(minutes=5)),
        "kind": "schedule.tick.completed",
        "source": "t",
        "payload": {},
    }
    day = (NOW - timedelta(minutes=5)).date().isoformat()
    (logs / f"schedule-events-{day}.jsonl").write_text(json.dumps(tick) + "\n", encoding="utf-8")
    return v


def _render(tmp_path: Path, monkeypatch) -> str:
    monkeypatch.setattr(health, "vault_birth", lambda _v: NOW - timedelta(days=60))
    monkeypatch.setattr(health, "tick_job_installed", lambda home=None: True)
    monkeypatch.setattr(health, "recent_commits", lambda *a, **k: [])
    v = _build_vault(tmp_path)
    report = bootstrap_doctor.run_doctor(vault=v, check_jobs=False, home=tmp_path / "home", now=NOW)
    text = json.dumps(doctor_dict(report), indent=2, sort_keys=True) + "\n"
    return text.replace(str(v), "/Users/alex/Scout").replace(str(tmp_path), "/Users/alex")


def test_doctor_run_health_matches_the_shared_fixture(tmp_path, monkeypatch):
    text = _render(tmp_path, monkeypatch)
    if REGEN:
        CANONICAL.write_text(text, encoding="utf-8")
    assert CANONICAL.read_text(encoding="utf-8") == text, (
        "regenerate with SCOUT_REGEN_CONTRACT=1, then cp it to apps/macos/ScoutTests/Fixtures/doctor-run-health.json"
    )


def test_the_fixture_exercises_the_contract_shape(tmp_path, monkeypatch):
    d = json.loads(_render(tmp_path, monkeypatch))
    assert d["severity"] == "yellow" and len(d["warnings"]) == 1 and d["errors"] == []
    rh = d["run_health"]
    assert rh["severity"] == "red"
    ids = {f["id"]: f["severity"] for f in rh["findings"]}
    assert ids["auth.expired"] == "red"
    assert any(i.startswith("slot.stale:") and s == "yellow" for i, s in ids.items())
    assert sorted(rh["slots"][0]) == ["key", "last_fire", "last_outcome", "type"]
    outcomes = [s["last_outcome"] for s in rh["slots"]]
    assert len(outcomes) == 2 and sum(o is None for o in outcomes) == 1
    assert "pytest" not in json.dumps(d)


def test_the_fixture_models_a_state_the_engine_can_reach(tmp_path, monkeypatch):
    """Clients learn from this fixture, so it must be coherent: last-fire.json holds the newer of the
    dispatch and the session end (§13.1), so a slot's last_fire is never older than its last outcome's
    finished_at (less the few seconds between session end and the ledger write), and a slot fires
    only on its own weekdays."""
    rh = json.loads(_render(tmp_path, monkeypatch))["run_health"]
    weekdays = {key: slot["weekdays"] for key, slot in yaml.safe_load(SCHEDULE)["slots"].items()}

    def parse(ts: str) -> datetime:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))

    for slot in rh["slots"]:
        fire = parse(slot["last_fire"])
        assert fire.strftime("%a") in weekdays[slot["key"]], slot
        if slot["last_outcome"] is not None:
            assert fire >= parse(slot["last_outcome"]["finished_at"]) - timedelta(seconds=5), slot
    auth = next(f for f in rh["findings"] if f["id"] == "auth.expired")
    assert "2 failed run(s)" in auth["summary"]
    assert any(f["id"] == "slot.fired_silent:weekly-research" for f in rh["findings"])
