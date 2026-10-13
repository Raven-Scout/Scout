"""Golden test: the connector health report's output, byte for byte (#80).

The report reads `.scout-logs/connector-calls-*.jsonl` through a per-file index
under `.scout-cache/` so it can skip files whose rows are all older than the
window. That must never change what it writes. The golden files were produced
by the report as it was before the index (v0.18.1). The same inputs must
reproduce them on a cold run (no index), a warm run (index present), and after
the files change behind the index's back.

The fixture is generated stand-in data: 30 days of six scheduled runs a day,
plus the awkward rows a live hook leaves (torn lines, rows with no `ts`,
interactive rows, a file whose name says one date and whose rows say another).

Regenerate the golden files only for a deliberate output change:
`SCOUT_REGEN_GOLDEN=1 pytest tests/unit/test_connector_health_golden.py`.
"""

from __future__ import annotations

import json
import os
import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scout.scripts import connector_health_report as chr_mod

# Read at import: the autouse env scrub strips SCOUT_* before the test body runs.
REGEN = os.environ.get("SCOUT_REGEN_GOLDEN") == "1"
GOLDEN = Path(__file__).parent.parent / "fixtures" / "connector_health"
NOW = datetime(2026, 9, 30, 21, 0, tzinfo=UTC)
DAYS = 30
CONNECTORS = [
    "mcp:claude_ai_Slack",
    "mcp:claude_ai_Linear",
    "mcp:claude_ai_Gmail",
    "mcp:claude_ai_Google_Calendar",
    "github",
    "bash:git",
    "read",
    "edit",
]
MODES = ["morning-briefing", "consolidation-1", "consolidation-2", "research", "dreaming", "interactive"]


def _row(ts: datetime, sid: str, mode: str, connector: str, error: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ts": ts.isoformat().replace("+00:00", "Z"),
        "session_id": sid,
        "mode": mode,
        "tool": "mcp__stand_in__call",
        "connector": connector,
        "error": error,
    }
    if error:
        row["err"] = f"stand-in failure from {connector}: request timed out after 30s"
    return row


def write_fixture(log_dir: Path) -> None:
    rnd = random.Random(80)
    log_dir.mkdir(parents=True, exist_ok=True)
    for d in range(DAYS, -1, -1):
        day = NOW - timedelta(days=d)
        lines: list[str] = []
        for s, mode in enumerate(MODES):
            sid = f"sess-{d:02d}-{s}"
            start = day.replace(hour=1 + 3 * s, minute=7)
            for i in range(rnd.randint(30, 90)):
                connector = rnd.choice(CONNECTORS)
                # Today's last scheduled run: every Slack call fails (CRITICAL), most Linear calls too (WARNING).
                error = rnd.random() < 0.04
                if d == 0 and mode == "dreaming" and connector == "mcp:claude_ai_Slack":
                    error = True
                lines.append(json.dumps(_row(start + timedelta(seconds=7 * i), sid, mode, connector, error)))
            if d == 0 and mode == "dreaming":
                for i in range(20):
                    at = start + timedelta(minutes=20, seconds=i)
                    lines.append(json.dumps(_row(at, sid, mode, "mcp:claude_ai_Linear", i != 0)))
        lines.insert(5, '{"ts": "2026-09-01T00:00:00Z", "session_id": "torn", "mo')
        lines.insert(9, json.dumps({"session_id": "no-ts", "mode": "research", "connector": "github"}))
        lines.insert(12, "not json at all")
        (log_dir / f"connector-calls-{day.date().isoformat()}.jsonl").write_text("\n".join(lines) + "\n")
    # Named for a long-gone day, holding rows from inside the window: only the
    # rows' own timestamps decide.
    misnamed = [
        json.dumps(_row(NOW - timedelta(days=2, hours=h), "backfill", "research", "github", False)) for h in range(5)
    ]
    (log_dir / "connector-calls-2026-01-15.jsonl").write_text("\n".join(misnamed) + "\n")
    settled = time.time() - 86_400
    for p in log_dir.glob("connector-calls-*.jsonl"):
        os.utime(p, (settled, settled))


@pytest.fixture
def vault(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (fake_data_dir / "scout-config.yaml").write_text("user:\n  timezone: America/New_York\n", encoding="utf-8")
    monkeypatch.setattr(chr_mod, "fire_macos_notification", lambda _alerts: None)
    write_fixture(fake_data_dir / ".scout-logs")
    return fake_data_dir


def _outputs(vault: Path) -> dict[str, str]:
    event = chr_mod.run(data_dir=vault, now=NOW)
    assert event is not None
    alerts_log = vault / ".scout-logs" / "connector-alerts.log"
    out = {
        "connector-health.md": (vault / "knowledge-base" / "connector-health.md").read_text(encoding="utf-8"),
        "connector-alerts-pending.md": (vault / ".scout-cache" / "connector-alerts-pending.md").read_text(
            encoding="utf-8"
        ),
        "connector-alerts.log": alerts_log.read_text(encoding="utf-8"),
        "event-payload.json": json.dumps(event.payload, indent=2, sort_keys=True) + "\n",
    }
    alerts_log.unlink()  # appended per run; each comparison is of one run's lines
    return out


def _golden() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(GOLDEN.iterdir())}


def test_golden_output(vault: Path) -> None:
    out = _outputs(vault)
    if REGEN:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        for name, text in out.items():
            (GOLDEN / name).write_text(text, encoding="utf-8")
    assert out == _golden()
    # The fixture exercises both alert levels, so the alert paths are covered too.
    assert "CRITICAL" in out["connector-alerts.log"]
    assert "WARNING" in out["connector-alerts.log"]
