"""Budget test for the connector health report on a mature vault's logs (#80, #66).

CI asserts the work each report does, not how long it takes:
- a cold report (no index) opens every ledger file;
- a warm report opens only the files with a row inside the 14-day window and
  writes the same bytes;
- the zone is resolved once per report, not once per rendered timestamp;
- appends past the daily cap are dropped, and the first row of a new day
  prunes files past retention.

The wall-clock ceilings are generous and only catch a gross slowdown on a slow
runner. Measured on an M-series Mac with this fixture, as `run()` times: before
#80 0.155 s on every report; after, 0.10 s cold and 0.054 s warm.

The fixture is generated stand-in rows shaped like a mature vault's ledger: 30
days kept (the retention), six scheduled runs a day, 1,500 rows a day (~6.8 MB).
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

from scout import config, connector_calls
from scout.scripts import connector_health_report as chr_mod

pytestmark = pytest.mark.perf

NOW = datetime(2026, 9, 30, 21, 0, tzinfo=UTC)
DAYS = 30
ROWS_PER_DAY = 1500
CONNECTORS = [
    "mcp:claude_ai_Slack",
    "mcp:claude_ai_Linear",
    "mcp:claude_ai_Gmail",
    "github",
    "bash:git",
    "read",
    "edit",
    "grep",
]
MODES = ["morning-briefing", "consolidation-1", "consolidation-2", "research", "dreaming", "weekend-briefing"]
COLD_CEILING_S = 10.0
WARM_CEILING_S = 5.0


def _write_ledger(log_dir: Path) -> None:
    rnd = random.Random(80)
    log_dir.mkdir(parents=True, exist_ok=True)
    settled = time.time() - 86_400
    for d in range(DAYS - 1, -1, -1):
        day = NOW - timedelta(days=d)
        lines = []
        for s, mode in enumerate(MODES):
            start = day.replace(hour=1 + 3 * s, minute=0)
            for i in range(ROWS_PER_DAY // len(MODES)):
                error = rnd.random() < 0.05
                row: dict[str, Any] = {
                    "ts": (start + timedelta(seconds=5 * i)).isoformat().replace("+00:00", "Z"),
                    "session_id": f"sess-{d:02d}-{s}",
                    "mode": mode,
                    "tool": "mcp__stand_in__call",
                    "connector": rnd.choice(CONNECTORS),
                    "error": error,
                }
                if error:
                    row["err"] = "stand-in failure: request timed out after 30s"
                lines.append(json.dumps(row))
        path = connector_calls.day_path(log_dir, day.date().isoformat())
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if d:  # past days are settled; today's file is still being written
            os.utime(path, (settled, settled))


def test_connector_health_report_budget(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (fake_data_dir / "scout-config.yaml").write_text("user:\n  timezone: America/New_York\n", encoding="utf-8")
    monkeypatch.setattr(chr_mod, "fire_macos_notification", lambda _alerts: None)
    log_dir = fake_data_dir / ".scout-logs"
    _write_ledger(log_dir)

    opened: list[str] = []
    real_open = open

    def counting_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(Path(file).name)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(chr_mod, "open", counting_open, raising=False)

    zone_reads = 0
    real_resolve = config.resolve_timezone

    def counting_resolve(*args: Any, **kwargs: Any) -> Any:
        nonlocal zone_reads
        zone_reads += 1
        return real_resolve(*args, **kwargs)

    monkeypatch.setattr(chr_mod, "resolve_timezone", counting_resolve)
    health = fake_data_dir / "knowledge-base" / "connector-health.md"

    def report() -> tuple[str, float]:
        opened.clear()
        start = time.perf_counter()
        assert chr_mod.run(data_dir=fake_data_dir, now=NOW) is not None
        return health.read_text(encoding="utf-8"), time.perf_counter() - start

    cold_md, cold_s = report()
    assert len(opened) == DAYS
    assert zone_reads == 1

    warm_md, warm_s = report()
    assert warm_md == cold_md
    in_window = {
        connector_calls.day_path(log_dir, (NOW - timedelta(days=d)).date().isoformat()).name for d in range(14)
    }
    assert set(opened) == in_window  # 14 files read, the 16 older ones skipped unopened

    print(f"\nconnector health report, {DAYS} days x {ROWS_PER_DAY} rows: cold {cold_s:.3f}s, warm {warm_s:.3f}s")
    assert cold_s < COLD_CEILING_S
    assert warm_s < WARM_CEILING_S


def test_ledger_bounds_hold_at_scale(tmp_path: Path) -> None:
    """A runaway day stops at the cap; a new day prunes past retention."""
    row = json.dumps({"ts": "2026-09-30T12:00:00Z", "session_id": "loop", "err": "x" * 200}) + "\n"
    day = tmp_path / "connector-calls-2026-09-30.jsonl"
    cap = 1024 * 1024
    written = sum(connector_calls.append_rows(day, [row] * 500, max_bytes=cap) for _ in range(20))
    assert day.stat().st_size <= cap + 512  # the cap, plus its one marker row
    assert written == cap // len(row)

    ledger = tmp_path / "pruned"
    ledger.mkdir()
    today = datetime.now(UTC)  # a writer prunes against the real clock
    for n in (*range(1, 30), *range(31, 61)):  # skip day 30, which sits on the cutoff
        (ledger / f"connector-calls-{(today - timedelta(days=n)).date().isoformat()}.jsonl").write_text(row)
    start = time.perf_counter()
    connector_calls.append_rows(connector_calls.day_path(ledger, today.date().isoformat()), [row])
    prune_s = time.perf_counter() - start
    # Today and days 1–29 stay; days 31–60 go.
    assert len(list(ledger.glob(connector_calls.GLOB))) == 1 + 29
    assert prune_s < WARM_CEILING_S
