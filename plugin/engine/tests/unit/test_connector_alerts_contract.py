"""`.scout-logs/connector-alerts.json` is a contract with the macOS app.

The engine rewrites the file on every connector-health run; the app's
`ConnectorAlert.parseFile` reads it for the alert banner and keys its
acknowledgements on `connector_key|level|first_seen`.

``tests/fixtures/contract/connector-alerts.json`` is the canonical copy; the app
keeps a byte copy at ``apps/macos/ScoutTests/Fixtures/connector-alerts.json``
(``contract.yml`` compares them). Set ``SCOUT_REGEN_CONTRACT=1`` to rewrite the
canonical file from the deterministic runs below, then ``cp`` it over the app copy.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.scripts import connector_health_report as chr_mod

# Read at import: tests/conftest.py scrubs SCOUT_* from the environment before each test runs.
REGEN = os.environ.get("SCOUT_REGEN_CONTRACT") == "1"
CANONICAL = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "connector-alerts.json"

DAY1 = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)
DAY2 = DAY1 + timedelta(days=1)
SLACK = "mcp:claude_ai_Slack"
LINEAR = "mcp:claude_ai_Linear"


def _seed(
    log_dir: Path, *, sid: str, ts: datetime, calls: dict[str, tuple[int, int]], mode: str = "morning-briefing"
) -> None:
    """Append one scheduled run: `calls[connector] = (n_ok, n_err)`."""
    rows = []
    for connector, (n_ok, n_err) in calls.items():
        for i in range(n_ok + n_err):
            row = {
                "ts": (ts + timedelta(seconds=i)).isoformat().replace("+00:00", "Z"),
                "session_id": sid,
                "mode": mode,
                "tool": "mcp__stand_in__call",
                "connector": connector,
                "error": i >= n_ok,
            }
            if i >= n_ok:
                row["err"] = "stand-in failure: request timed out after 30s"
            rows.append(json.dumps(row))
    path = log_dir / f"connector-calls-{ts.date().isoformat()}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")


@pytest.fixture
def vault(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (fake_data_dir / "scout-config.yaml").write_text("user:\n  timezone: America/New_York\n", encoding="utf-8")
    monkeypatch.setattr(chr_mod, "fire_macos_notification", lambda _alerts: None)
    log_dir = fake_data_dir / ".scout-logs"
    # Two healthy runs give Slack a baseline its failures then break.
    for d, sid in ((3, "s_prev1"), (2, "s_prev2")):
        _seed(log_dir, sid=sid, ts=DAY1 - timedelta(days=d), calls={SLACK: (5, 0), LINEAR: (5, 0)})
    return fake_data_dir


def _state(vault: Path) -> dict:
    return json.loads((vault / ".scout-logs" / chr_mod.ALERTS_STATE_FILENAME).read_text(encoding="utf-8"))


def _render(vault: Path) -> str:
    """Day 1: Slack fails (CRITICAL starts). Day 2: Slack still fails, Linear mostly errors (WARNING starts)."""
    log_dir = vault / ".scout-logs"
    _seed(log_dir, sid="s_day1", ts=DAY1, calls={SLACK: (0, 2), LINEAR: (4, 0)})
    chr_mod.run(data_dir=vault, now=DAY1 + timedelta(hours=1))
    _seed(log_dir, sid="s_day2", ts=DAY2, calls={SLACK: (0, 2), LINEAR: (1, 3)})
    chr_mod.run(data_dir=vault, now=DAY2 + timedelta(hours=1))
    return (log_dir / chr_mod.ALERTS_STATE_FILENAME).read_text(encoding="utf-8")


def test_connector_alerts_matches_the_shared_fixture(vault: Path) -> None:
    text = _render(vault)
    if REGEN:
        CANONICAL.write_text(text, encoding="utf-8")
    assert CANONICAL.read_text(encoding="utf-8") == text, (
        "regenerate with SCOUT_REGEN_CONTRACT=1, then cp it to apps/macos/ScoutTests/Fixtures/connector-alerts.json"
    )


def test_the_fixture_exercises_the_contract_shape(vault: Path) -> None:
    doc = json.loads(_render(vault))
    assert doc["schema_version"] == chr_mod.ALERTS_STATE_SCHEMA_VERSION == 1
    assert doc["generated_at"] == "2026-04-28T13:00:00Z"
    assert doc["mode"] == "morning-briefing"
    by_key = {a["connector_key"]: a for a in doc["alerts"]}
    assert set(by_key) == {SLACK, LINEAR}
    for a in doc["alerts"]:
        assert set(a) == {"connector_key", "level", "name", "reason", "ts", "first_seen"}
        assert a["ts"] == doc["generated_at"]
    # A sustained alert keeps the first run's timestamp; a new one starts now.
    assert by_key[SLACK]["level"] == "CRITICAL"
    assert by_key[SLACK]["first_seen"] == "2026-04-27T13:00:00Z"
    assert by_key[LINEAR]["level"] == "WARNING"
    assert by_key[LINEAR]["first_seen"] == by_key[LINEAR]["ts"]
    assert by_key[SLACK]["name"] == "Slack"
    assert "failed call(s)" in by_key[SLACK]["reason"]


def test_a_clean_run_empties_the_state_and_a_recurrence_starts_a_new_streak(vault: Path) -> None:
    _render(vault)
    log_dir = vault / ".scout-logs"
    day3 = DAY2 + timedelta(days=1)
    # Two healthy runs restore Slack's baseline (2 of the last 3 runs), so day 4's failure alerts again.
    for hours, sid in ((0, "s_day3"), (6, "s_day3b")):
        _seed(log_dir, sid=sid, ts=day3 + timedelta(hours=hours), calls={SLACK: (5, 0), LINEAR: (5, 0)})
        chr_mod.run(data_dir=vault, now=day3 + timedelta(hours=hours + 1))
        assert _state(vault)["alerts"] == []
    assert _state(vault)["generated_at"] == "2026-04-29T19:00:00Z"

    day4 = day3 + timedelta(days=1)
    _seed(log_dir, sid="s_day4", ts=day4, calls={SLACK: (0, 2), LINEAR: (5, 0)})
    chr_mod.run(data_dir=vault, now=day4 + timedelta(hours=1))
    (slack,) = _state(vault)["alerts"]
    assert slack["connector_key"] == SLACK
    assert slack["first_seen"] == slack["ts"] == "2026-04-30T13:00:00Z"


def test_a_run_that_skips_the_connector_keeps_its_alert_and_streak(vault: Path) -> None:
    """Since #477 a run that never calls a connector raises no alert for it. That is no evidence
    the connector recovered, so the alert stays, unchanged, and its first_seen (the app's ack key)
    survives until a run that calls the connector clears it."""
    log_dir = vault / ".scout-logs"
    _seed(log_dir, sid="s_day1", ts=DAY1, calls={SLACK: (0, 2), LINEAR: (4, 0)})
    chr_mod.run(data_dir=vault, now=DAY1 + timedelta(hours=1))
    (before,) = _state(vault)["alerts"]

    skip = DAY1 + timedelta(hours=5)
    _seed(log_dir, sid="s_skip", ts=skip, calls={LINEAR: (4, 0)}, mode="consolidation")
    chr_mod.run(data_dir=vault, now=skip + timedelta(hours=1))
    state = _state(vault)
    assert state["generated_at"] == "2026-04-27T18:00:00Z"
    assert state["alerts"] == [before]  # carried verbatim: ts says when a run last raised it

    _seed(log_dir, sid="s_day2", ts=DAY2, calls={SLACK: (0, 2), LINEAR: (4, 0)})
    chr_mod.run(data_dir=vault, now=DAY2 + timedelta(hours=1))
    (again,) = _state(vault)["alerts"]
    assert again["first_seen"] == before["first_seen"] == "2026-04-27T13:00:00Z"
    assert again["ts"] == "2026-04-28T13:00:00Z"


def test_a_run_that_calls_the_connector_clears_an_alert_it_no_longer_raises(vault: Path) -> None:
    """Slack CRITICAL, then a run whose Slack calls mostly fail but one succeeds: that run raises a
    WARNING, a new streak, and the CRITICAL is gone."""
    log_dir = vault / ".scout-logs"
    _seed(log_dir, sid="s_day1", ts=DAY1, calls={SLACK: (0, 2), LINEAR: (4, 0)})
    chr_mod.run(data_dir=vault, now=DAY1 + timedelta(hours=1))
    _seed(log_dir, sid="s_day2", ts=DAY2, calls={SLACK: (1, 3), LINEAR: (4, 0)})
    chr_mod.run(data_dir=vault, now=DAY2 + timedelta(hours=1))
    (warning,) = _state(vault)["alerts"]
    assert (warning["connector_key"], warning["level"]) == (SLACK, "WARNING")
    assert warning["first_seen"] == warning["ts"] == "2026-04-28T13:00:00Z"


@pytest.mark.parametrize(
    "prior",
    [
        "not json",
        '{"schema_version": 2, "alerts": []}',
        '{"schema_version": 1, "alerts": [{"connector_key": "mcp:claude_ai_Slack", "level": "CRITICAL",'
        ' "ts": "2026-04-27T13:00:00Z", "first_seen": "yesterday"}]}',
        '{"schema_version": 1, "alerts": [{"connector_key": "mcp:claude_ai_Slack", "level": "NOTICE",'
        ' "ts": "2026-04-27T13:00:00Z", "first_seen": "2026-04-27T13:00:00Z"}]}',
        '{"schema_version": 1, "alerts": "nope"}',
    ],
)
def test_an_unusable_prior_state_starts_fresh_streaks(prior: str, tmp_path: Path) -> None:
    path = tmp_path / "connector-alerts.json"
    path.write_text(prior, encoding="utf-8")
    assert chr_mod.read_prior_alerts(path) == []


def test_a_prior_entry_reads_back_with_its_fields(tmp_path: Path) -> None:
    path = tmp_path / "connector-alerts.json"
    path.write_text(
        '{"schema_version": 1, "alerts": [{"connector_key": "mcp:claude_ai_Slack", "level": "CRITICAL",'
        ' "ts": "2026-04-28T13:00:00Z", "first_seen": "2026-04-27T13:00:00Z"}]}',
        encoding="utf-8",
    )
    assert chr_mod.read_prior_alerts(path) == [
        {
            "connector_key": SLACK,
            "level": "CRITICAL",
            "name": SLACK,
            "reason": "",
            "ts": "2026-04-28T13:00:00Z",
            "first_seen": "2026-04-27T13:00:00Z",
        }
    ]


def test_a_missing_prior_state_reads_as_empty(tmp_path: Path) -> None:
    assert chr_mod.read_prior_alerts(tmp_path / "absent.json") == []


def test_an_unwritable_state_file_is_removed_and_does_not_fail_the_run(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A state that can't be rewritten is removed rather than left showing alerts this run may have cleared."""
    state = vault / ".scout-logs" / chr_mod.ALERTS_STATE_FILENAME
    state.write_text('{"schema_version": 1, "alerts": []}', encoding="utf-8")

    def boom(_path: Path, _text: str) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(chr_mod, "atomic_write_text", boom)
    _seed(vault / ".scout-logs", sid="s_day1", ts=DAY1, calls={SLACK: (0, 2), LINEAR: (4, 0)})
    event = chr_mod.run(data_dir=vault, now=DAY1 + timedelta(hours=1))
    assert event is not None and event.payload["alerts"]
    assert (vault / ".scout-logs" / "connector-alerts.log").exists()
    assert not state.exists()
