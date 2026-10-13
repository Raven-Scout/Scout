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
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scout import connector_calls
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


# Stored as ``<output>.golden``: a golden named ``*.log`` matched the repo's
# ``*.log`` ignore rule, was never committed, and only failed on clean checkouts.
OUTPUTS = ("connector-alerts-pending.md", "connector-alerts.log", "connector-health.md", "event-payload.json")
SUFFIX = ".golden"


def _golden() -> dict[str, str]:
    found = sorted(p.name for p in GOLDEN.iterdir())
    assert found == sorted(name + SUFFIX for name in OUTPUTS), f"golden set is incomplete: {found}"
    return {name: (GOLDEN / (name + SUFFIX)).read_text(encoding="utf-8") for name in OUTPUTS}


def test_golden_files_are_committable() -> None:
    """Every golden file must survive a clean checkout: none may be git-ignored."""
    repo = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=GOLDEN, capture_output=True, text=True)
    if repo.returncode != 0:
        pytest.skip("not a git checkout")
    names = [str(GOLDEN / (name + SUFFIX)) for name in OUTPUTS]
    ignored = subprocess.run(["git", "check-ignore", *names], cwd=GOLDEN, capture_output=True, text=True)
    assert ignored.stdout == "", f"git-ignored golden files: {ignored.stdout}"


def test_golden_output(vault: Path) -> None:
    out = _outputs(vault)
    if REGEN:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        for name, text in out.items():
            (GOLDEN / (name + SUFFIX)).write_text(text, encoding="utf-8")
    assert out == _golden()
    # The fixture exercises both alert levels, so the alert paths are covered too.
    assert "CRITICAL" in out["connector-alerts.log"]
    assert "WARNING" in out["connector-alerts.log"]


def _index(vault: Path) -> Path:
    return vault / ".scout-cache" / chr_mod.INDEX_FILENAME


def _opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Names of the ledger files load_records opens."""
    names: list[str] = []
    real = open

    def spy(file: Any, *args: Any, **kwargs: Any) -> Any:
        names.append(Path(file).name)
        return real(file, *args, **kwargs)

    monkeypatch.setattr(chr_mod, "open", spy, raising=False)
    return names


def test_warm_run_skips_old_files_and_matches_golden(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _outputs(vault) == _golden()  # cold: builds the index
    assert _index(vault).is_file()
    opened = _opened(monkeypatch)
    assert _outputs(vault) == _golden()  # warm
    # Only files with a row inside the 14-day window are read: 09-17 to 09-30
    # plus the misnamed file. The 17 older days are skipped unopened.
    assert len(opened) == 15
    assert "connector-calls-2026-01-15.jsonl" in opened
    assert "connector-calls-2026-09-01.jsonl" not in opened


def _same_inputs_without_index(vault: Path) -> dict[str, str]:
    _index(vault).unlink()
    return _outputs(vault)


def test_files_changed_behind_the_index_are_reread(vault: Path) -> None:
    _outputs(vault)
    log_dir = vault / ".scout-logs"
    # An old, indexed file gains an in-window row (appended: size, mtime, ctime move).
    old = log_dir / "connector-calls-2026-09-05.jsonl"
    with old.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_row(NOW - timedelta(hours=1), "late", "research", "github", False)) + "\n")
    # Another is rewritten in place at the same size with its mtime restored.
    other = log_dir / "connector-calls-2026-09-06.jsonl"
    st = other.stat()
    text = other.read_text(encoding="utf-8")
    first = text.splitlines()[0]
    moved = first.replace("2026-09-06T", "2026-09-29T", 1)
    assert len(moved) == len(first)
    other.write_text(text.replace(first, moved, 1), encoding="utf-8")
    os.utime(other, ns=(st.st_atime_ns, st.st_mtime_ns))
    # A third disappears.
    (log_dir / "connector-calls-2026-09-07.jsonl").unlink()

    with_index = _outputs(vault)
    assert with_index != _golden()
    assert with_index == _same_inputs_without_index(vault)
    files = json.loads(_index(vault).read_text(encoding="utf-8"))["files"]
    assert "connector-calls-2026-09-07.jsonl" not in files


def test_a_torn_or_foreign_index_is_ignored(vault: Path) -> None:
    _outputs(vault)
    for junk in ("{not json", '{"version": 999, "files": {}}', '["a list"]'):
        _index(vault).write_text(junk, encoding="utf-8")
        assert _outputs(vault) == _golden()
        assert json.loads(_index(vault).read_text(encoding="utf-8"))["version"] == chr_mod.INDEX_VERSION


def test_an_index_entry_with_a_bad_timestamp_is_a_miss(vault: Path) -> None:
    _outputs(vault)
    data = json.loads(_index(vault).read_text(encoding="utf-8"))
    for entry in data["files"].values():
        entry["max_ts"] = "not a timestamp"
    _index(vault).write_text(json.dumps(data), encoding="utf-8")
    assert _outputs(vault) == _golden()


def test_an_unwritable_cache_dir_still_reports(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(chr_mod.os, "replace", fail)
    assert _outputs(vault) == _golden()
    assert not _index(vault).exists()
    assert not list((vault / ".scout-cache").glob(".*.tmp"))


def test_recently_modified_files_are_not_indexed(vault: Path) -> None:
    today = vault / ".scout-logs" / "connector-calls-2026-09-30.jsonl"
    os.utime(today)  # just written, as the live day's file always is
    _outputs(vault)
    files = json.loads(_index(vault).read_text(encoding="utf-8"))["files"]
    assert today.name not in files
    assert "connector-calls-2026-09-29.jsonl" in files


def test_files_with_no_readable_rows_are_skipped_once_indexed(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    junk = vault / ".scout-logs" / "connector-calls-2026-09-29.jsonl"
    junk_text = "not json\n" + json.dumps({"session_id": "no-ts"}) + "\n"
    hidden = vault / ".scout-logs" / "connector-calls-2027-01-01.jsonl"
    hidden.symlink_to(vault / "nowhere.jsonl")  # matches the glob, can't be stat'ed
    junk.write_text(junk_text, encoding="utf-8")
    os.utime(junk, (time.time() - 3_600, time.time() - 3_600))
    cold = _outputs(vault)
    assert json.loads(_index(vault).read_text(encoding="utf-8"))["files"][junk.name] == {
        "key": chr_mod._stat_key(junk.stat()),
        "max_ts": None,
    }
    opened = _opened(monkeypatch)
    assert _outputs(vault) == cold
    assert junk.name not in opened


def _marker(source_row: dict[str, Any]) -> dict[str, Any]:
    marker: dict[str, Any] = json.loads(connector_calls._cap_marker(json.dumps(source_row), 64))
    marker["ts"] = "2026-09-30T13:30:00Z"
    return marker


def test_a_capped_day_is_a_notice_not_a_run(vault: Path) -> None:
    """The cap marker (#66) must not become a session or skew any count: the
    output is the golden one plus the notice line and the payload key."""
    today = vault / ".scout-logs" / "connector-calls-2026-09-30.jsonl"
    with today.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_marker({"session_id": "sess-00-4", "mode": "dreaming"})) + "\n")
    out = _outputs(vault)
    golden = _golden()
    notice = (
        "**⚠️ Call log capped:** the 2026-09-30 log hit its daily size cap at 09:30 EDT; "
        "calls after that were not logged, so that day's counts are low."
    )
    assert out["connector-health.md"] == golden["connector-health.md"].replace(
        "**KB lint overrides (14d):** 0\n", f"**KB lint overrides (14d):** 0\n{notice}\n", 1
    )
    assert out["connector-alerts.log"] == golden["connector-alerts.log"]
    assert out["connector-alerts-pending.md"] == golden["connector-alerts-pending.md"]
    payload = json.loads(out["event-payload.json"])
    assert payload.pop("log_capped_at") == ["2026-09-30T13:30:00Z"]
    assert payload == json.loads(golden["event-payload.json"])


def test_a_window_with_only_a_marker_is_no_report(fake_data_dir: Path) -> None:
    log = fake_data_dir / ".scout-logs" / "connector-calls-2026-09-30.jsonl"
    log.write_text(json.dumps(_marker({})) + "\n", encoding="utf-8")
    assert chr_mod.run(data_dir=fake_data_dir, now=NOW) is None
