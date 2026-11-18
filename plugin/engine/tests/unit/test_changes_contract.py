"""``scoutctl changes --json`` and ``scoutctl engagement --json`` are a contract with the macOS app.

``tests/fixtures/contract/changes.json`` and ``engagement.json`` are the canonical copies. Set
``SCOUT_REGEN_CONTRACT=1`` to rewrite them from the deterministic vault below; never edit them by hand."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scout.changes.models import CHANGE_KINDS, Change, Signal, Why
from scout.changes.report import changes_report, engagement_report
from scout.changes.store import (
    CHANGES_NAME,
    ENGAGEMENT_NAME,
    append_change,
    append_signal,
    owner_dir,
    read_changes,
    read_signals,
    vault_key,
)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
PRAGUE = ZoneInfo("Europe/Prague")
# Read at import: tests/conftest.py scrubs SCOUT_* from the environment before each test runs.
REGEN = os.environ.get("SCOUT_REGEN_CONTRACT") == "1"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "contract"
CANONICAL_CHANGES = FIXTURES / "changes.json"
CANONICAL_ENGAGEMENT = FIXTURES / "engagement.json"


def _ts(days_ago: float) -> datetime:
    return NOW - timedelta(days=days_ago)


def _build(root: Path) -> tuple[Path, Path, dict]:
    vault, home = root / "Scout", root / "home"
    vault.mkdir()
    home.mkdir()
    env = {"HOME": str(home)}
    for i, kind in enumerate(sorted(CHANGE_KINDS)):
        append_change(
            vault,
            Change(
                id=f"chg-{i:012x}",
                ts=_ts(1 + i),
                kind=kind,
                summary=f"A {kind} change",
                why=Why(
                    type="unknown" if i == 0 else "pattern",
                    ref="" if i == 0 else "Pattern #249",
                    gloss=None if i == 0 else "Why it happened",
                    inferred=i == 0,
                ),
                commit=f"{i:040x}",
                files=("SKILL.md",) if i % 2 == 0 else ("knowledge-base/a.md", "DREAMING.md"),
                targets=("briefing",),
                governance=i == 1,
                author_lane="dreaming" if i % 2 else "interactive",
                backfilled=i == 2,
            ),
        )
    # Local days in Prague: the 23:30Z reliance row falls on the 11th, not the 10th.
    rows = [
        ("session-reliance", "kb", datetime(2026, 11, 10, 9, 0, tzinfo=UTC), "a" * 16),
        ("session-reliance", "skill", datetime(2026, 11, 10, 10, 0, tzinfo=UTC), "a" * 16),
        ("session-reliance", "skill", datetime(2026, 11, 10, 23, 30, tzinfo=UTC), "b" * 16),
        ("app-edit", "app", datetime(2026, 11, 12, 9, 0, tzinfo=UTC), None),
        ("inline-comment", "action-items", datetime(2026, 11, 12, 10, 0, tzinfo=UTC), None),
        ("approval", "kb", datetime(2026, 11, 13, 9, 0, tzinfo=UTC), None),
        ("dm-reply", "slack-dm", datetime(2026, 11, 14, 9, 0, tzinfo=UTC), None),
        ("dm-reaction", "slack-dm", datetime(2026, 11, 14, 9, 5, tzinfo=UTC), None),
        ("dm-sent", "slack-dm", datetime(2026, 11, 15, 8, 0, tzinfo=UTC), None),
        ("red-carried", "action-items", datetime(2026, 11, 15, 9, 0, tzinfo=UTC), None),
    ]
    for kind, surface, ts, session in rows:
        append_signal(Signal(ts=ts, kind=kind, surface=surface, session=session), vault=vault, env=env)
    # One torn line in each file.
    for path in (vault / ".scout-logs" / CHANGES_NAME, vault / ".scout-logs" / ENGAGEMENT_NAME):
        with path.open("a", encoding="utf-8") as f:
            f.write('{"kind": "brain-ed\n')
    with (owner_dir(env) / ENGAGEMENT_NAME).open("a", encoding="utf-8") as f:
        f.write("not json\n")
    return vault, home, env


def _text(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def _check_or_regen(path: Path, text: str) -> None:
    if REGEN:
        path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text, "regenerate with SCOUT_REGEN_CONTRACT=1"


def test_changes_contract_matches_canonical(tmp_path):
    vault, _, _ = _build(tmp_path)
    doc = changes_report(vault, now=NOW, since=NOW - timedelta(days=30))
    assert doc["skipped_rows"] == 1 and set(doc["counts"]) == CHANGE_KINDS
    assert doc["schema_version"] == 1 and doc["truncated"] is False and len(doc["changes"]) == 7
    stamps = [c["ts"] for c in doc["changes"]]
    assert stamps == sorted(stamps, reverse=True)
    assert any(c["why"]["inferred"] for c in doc["changes"]) and any(c["backfilled"] for c in doc["changes"])
    _check_or_regen(CANONICAL_CHANGES, _text(doc))


def test_engagement_contract_has_every_day(tmp_path):
    vault, _, env = _build(tmp_path)
    doc = engagement_report(vault, env, now=NOW, since=NOW - timedelta(days=14), tz=PRAGUE)
    assert len(doc["days"]) == 15
    assert doc["days"][3] == {"date": "2026-11-05", "feedback": 0, "reliance": 0, "noise": 0, "by_surface": {}}
    assert doc["skipped_rows"] == 2
    # approval and dm-sent are not engagement; a session counts once per surface per local day.
    assert doc["totals"] == {"feedback": 4, "reliance": 3, "noise": 1}
    by_date = {d["date"]: d for d in doc["days"]}
    assert by_date["2026-11-11"]["reliance"] == 1  # 23:30Z is already the 11th in Prague
    _check_or_regen(CANONICAL_ENGAGEMENT, _text(doc))


def test_reports_carry_no_absolute_paths(tmp_path):
    vault, home, env = _build(tmp_path)
    docs = (
        changes_report(vault, now=NOW, since=NOW - timedelta(days=30)),
        engagement_report(vault, env, now=NOW, since=NOW - timedelta(days=14), tz=PRAGUE),
    )
    for doc in docs:
        text = json.dumps(doc)
        assert str(vault) not in text and str(home) not in text and "/Users/" not in text


def test_limit_truncates_but_counts_everything(tmp_path):
    vault, _, _ = _build(tmp_path)
    doc = changes_report(vault, now=NOW, since=NOW - timedelta(days=30), limit=3)
    assert doc["truncated"] is True and len(doc["changes"]) == 3
    assert sum(doc["counts"].values()) == 7


def test_duplicate_reliance_rows_count_once(tmp_path):
    vault, home = tmp_path / "Scout", tmp_path / "home"
    vault.mkdir()
    env = {"HOME": str(home)}
    when = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    for _ in range(2):
        append_signal(Signal(ts=when, kind="session-reliance", surface="kb", session="c" * 16), vault=vault, env=env)
    doc = engagement_report(vault, env, now=NOW, since=NOW - timedelta(days=14), tz=PRAGUE)
    assert doc["totals"]["reliance"] == 1


def test_zoneless_timestamps_are_utc_and_sort_with_aware_rows(tmp_path):
    vault, home = tmp_path / "Scout", tmp_path / "home"
    (vault / ".scout-logs").mkdir(parents=True)
    env = {"HOME": str(home)}
    naive = {"kind": "red-carried", "surface": "action-items", "ts": "2026-11-12T09:00:00"}
    aware = {"kind": "red-carried", "surface": "action-items", "ts": "2026-11-12T08:00:00+00:00"}
    (vault / ".scout-logs" / ENGAGEMENT_NAME).write_text(json.dumps(naive) + "\n" + json.dumps(aware) + "\n")
    signals, skipped = read_signals(vault, env)
    assert skipped == 0 and [s.ts.hour for s in signals] == [8, 9]
    assert all(s.ts.tzinfo is not None for s in signals)
    doc = engagement_report(vault, env, now=NOW, since=NOW - timedelta(days=14), tz=PRAGUE)
    assert doc["totals"]["noise"] == 2


def test_unhashable_kinds_are_skipped_not_raised(tmp_path):
    vault, home = tmp_path / "Scout", tmp_path / "home"
    (vault / ".scout-logs").mkdir(parents=True)
    env = {"HOME": str(home)}
    bad = json.dumps({"kind": ["x"], "surface": "kb", "ts": "2026-11-12T09:00:00Z"}) + "\n"
    (vault / ".scout-logs" / ENGAGEMENT_NAME).write_text(bad)
    (vault / ".scout-logs" / CHANGES_NAME).write_text(json.dumps({"kind": ["x"]}) + "\n")
    owner = owner_dir(env)
    owner.mkdir(parents=True)
    keyed = {"kind": ["x"], "surface": "kb", "ts": "2026-11-12T09:00:00Z", "vault_key": vault_key(vault)}
    (owner / ENGAGEMENT_NAME).write_text(json.dumps(keyed) + "\n")
    assert read_signals(vault, env) == ([], 2)
    assert read_changes(vault) == ([], 1)


def test_cli_emits_both_documents_and_bare_prints_help(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from scout.cli import app

    vault, home, _ = _build(tmp_path)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    monkeypatch.setenv("HOME", str(home))
    runner = CliRunner()
    out = runner.invoke(app, ["changes", "--json", "--since", "3650d", "--limit", "2"])
    assert out.exit_code == 0, out.output
    doc = json.loads(out.stdout)
    assert doc["schema_version"] == 1 and len(doc["changes"]) == 2
    out = runner.invoke(app, ["engagement", "--json", "--since", "3650d"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.stdout)["schema_version"] == 1
    assert "Usage" in runner.invoke(app, ["changes"]).output
    assert runner.invoke(app, ["changes", "--json", "--since", "soon"]).exit_code == 2
