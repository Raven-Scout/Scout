"""``scoutctl changes digest``: the "What Scout is working on" document, assembled from the two reports."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scout.changes.digest import build_digest, render_text
from scout.changes.models import Change, Signal, Why
from scout.changes.store import append_change, append_signal

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)  # a Wednesday; the local Monday is 2026-11-16
UTC_TZ = ZoneInfo("UTC")


def _vault(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    vault, home = tmp_path / "Scout", tmp_path / "home"
    vault.mkdir()
    home.mkdir()
    return vault, {"HOME": str(home)}


def _change(vault: Path, n: int, days_ago: float, **over) -> Change:
    base: dict = {
        "id": f"chg-{n:012x}",
        "ts": NOW - timedelta(days=days_ago),
        "kind": "brain-edit",
        "summary": f"Change number {n}",
        "why": Why("pattern", "pattern-249", "Why", inferred=False),
        "commit": f"{n:040x}",
        "files": ("SKILL.md",),
        "targets": ("briefing",),
        "author_lane": "dreaming",
    }
    change = Change(**{**base, **over})
    append_change(vault, change)
    return change


def _signals(vault: Path, env: dict[str, str], kind: str, days_ago: list[float], surface: str = "kb") -> None:
    for d in days_ago:
        append_signal(Signal(ts=NOW - timedelta(days=d), kind=kind, surface=surface), vault=vault, env=env)


def _recur(vault: Path, env: dict[str, str], days_ago: list[float]) -> None:
    for d in days_ago:
        when = NOW - timedelta(days=d)
        ref = f"pattern-249@{when.date().isoformat()}"
        append_signal(Signal(ts=when, kind="recurrence", surface="mistake-audit", ref=ref), vault=vault, env=env)


def _wish(vault: Path, slug: str, text: str) -> None:
    folder = vault / "docs" / "wishlist"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{slug}.md").write_text(text, encoding="utf-8")


def _doc(vault: Path, env: dict[str, str], tz: ZoneInfo = UTC_TZ) -> dict:
    return build_digest(vault, env, now=NOW, tz=tz)


def test_digest_counts_this_week_against_last(tmp_path):
    vault, env = _vault(tmp_path)
    _signals(vault, env, "app-edit", [0.1, 0.2, 1, 2, 3, 4], "app")  # six feedback this week
    _signals(vault, env, "session-reliance", [0.3, 1.5, 2.5, 3.5])  # four reliance this week
    _signals(vault, env, "app-edit", [7.5, 8, 9, 10, 11], "app")  # five last week
    _signals(vault, env, "red-carried", [1], "action-items")
    _signals(vault, env, "red-carried", [8, 9], "action-items")
    doc = _doc(vault, env)
    assert doc["schema_version"] == 1 and doc["generated_at"] == NOW.isoformat()
    assert doc["week_of"] == "2026-11-16"
    assert doc["engagement"] == {"this_week": 10, "last_week": 5, "change_pct": 100, "feedback": 6, "reliance": 4}
    assert doc["noise"] == {"this_week": 1, "last_week": 2}
    assert len(doc["days"]) == 7 and doc["days"][-1]["date"] == "2026-11-18" and doc["days"][0]["date"] == "2026-11-12"
    assert sum(d["engagement"] for d in doc["days"]) == 10 and sum(d["noise"] for d in doc["days"]) == 1


def test_digest_change_pct_is_null_without_a_last_week(tmp_path):
    vault, env = _vault(tmp_path)
    _signals(vault, env, "app-edit", [1], "app")
    doc = _doc(vault, env)
    assert doc["engagement"]["change_pct"] is None and doc["engagement"]["this_week"] == 1
    assert "(new this week)" in render_text(doc)


def test_digest_lists_at_most_three_per_section(tmp_path):
    vault, env = _vault(tmp_path)
    for n in range(5):
        _change(vault, n, 10 - n)  # the older, the sooner its review
    doc = _doc(vault, env)
    assert len(doc["testing"]) == 3
    assert [t["id"] for t in doc["testing"]] == ["chg-000000000000", "chg-000000000001", "chg-000000000002"]
    assert all(set(t) == {"id", "summary", "days_left"} for t in doc["testing"])


def test_digest_days_left_counts_to_review_on(tmp_path):
    vault, env = _vault(tmp_path)
    _change(vault, 1, 10)  # reviewed 14 local days after it landed: 4 days ahead
    assert _doc(vault, env)["testing"][0]["days_left"] == 4


def test_digest_local_days_follow_the_zone(tmp_path):
    vault, env = _vault(tmp_path)
    _change(vault, 1, 10)
    late = datetime(2026, 11, 18, 23, 30, tzinfo=UTC)  # already the 19th, and a Thursday, in Prague
    doc = build_digest(vault, env, now=late, tz=ZoneInfo("Europe/Prague"))
    assert doc["week_of"] == "2026-11-16" and doc["days"][-1]["date"] == "2026-11-19"


def test_digest_shipped_is_recent_and_not_in_testing(tmp_path):
    vault, env = _vault(tmp_path)
    _change(vault, 1, 1, kind="driver")
    _change(vault, 2, 3, kind="upgrade")
    _change(vault, 3, 5, kind="phase")
    _change(vault, 4, 6, kind="kb-structure")
    _change(vault, 5, 6.5, kind="wishlist-done")
    doc = _doc(vault, env)
    assert [t["id"] for t in doc["testing"]]  # the brain-edit-like kinds are being tested
    shipped_ids = {s["id"] for s in doc["shipped"]}
    assert not shipped_ids & {t["id"] for t in doc["testing"]}
    assert len(doc["shipped"]) <= 3 and all(set(s) == {"id", "summary"} for s in doc["shipped"])


def test_digest_shipped_skips_changes_older_than_a_week(tmp_path):
    vault, env = _vault(tmp_path)
    _change(vault, 1, 8, kind="upgrade")  # not tested, but older than seven local days
    _change(vault, 2, 2, kind="upgrade")
    assert [s["id"] for s in _doc(vault, env)["shipped"]] == ["chg-000000000002"]


def test_digest_learned_holds_recent_reviews_and_suspects(tmp_path):
    vault, env = _vault(tmp_path)
    # Reviewed on 11-12, the first day of the week: its target recurred twice, so the test says hurt.
    hurt = _change(vault, 1, 20, kind="wishlist-done", targets=("pattern-249",))
    _recur(vault, env, [15, 14])
    _change(vault, 2, 21.5, targets=("other",))  # reviewed on 11-11, the day before the week: not listed
    _change(vault, 3, 17, targets=("third",))  # reviewed 11-15: in the week, no clear verdict
    doc = _doc(vault, env)
    learned = {row["id"]: row for row in doc["learned"]}
    assert learned[hurt.id]["suspect"] is True and learned[hurt.id]["verdict"] == "hurt"
    assert "chg-000000000003" in learned and "chg-000000000002" not in learned
    assert all(set(r) == {"id", "summary", "verdict", "suspect"} for r in doc["learned"])
    assert [r["id"] for r in doc["learned"]] == ["chg-000000000003", hurt.id]  # newest review first


def test_digest_learned_adds_a_suspect_reviewed_before_this_week(tmp_path):
    vault, env = _vault(tmp_path)
    older = _change(vault, 1, 21, kind="wishlist-done", targets=("pattern-249",))  # reviewed 11-11
    _recur(vault, env, [15, 14])
    assert [r["id"] for r in _doc(vault, env)["learned"]] == [older.id]


def test_digest_learned_holds_at_most_three_newest_first(tmp_path):
    vault, env = _vault(tmp_path)
    _change(vault, 1, 21, kind="wishlist-done", targets=("pattern-249",))  # a suspect, reviewed longest ago
    _recur(vault, env, [15, 14])
    for n in range(2, 7):
        _change(vault, n, 20 - n * 0.9, targets=(f"t{n}",))  # reviewed on 11-12 .. 11-17
    ids = [r["id"] for r in _doc(vault, env)["learned"]]
    assert ids == ["chg-000000000006", "chg-000000000005", "chg-000000000004"]


def test_digest_up_next_reads_open_wishlist_items(tmp_path):
    vault, env = _vault(tmp_path)
    _wish(vault, "finished", "---\ntitle: Finished\nstatus: done\npriority: high\ndate: 2026-11-01\n---\nbody\n")
    _wish(vault, "low-open", "---\ntitle: A low one\nstatus: open\npriority: low\ndate: 2026-11-02\n---\n")
    _wish(vault, "high-going", "---\ntitle: A high one\nstatus: in-progress\npriority: high\ndate: 2026-11-03\n---\n")
    _wish(vault, "mid-new", "---\ntitle: Mid, newer\nstatus: open\npriority: medium\ndate: 2026-11-09\n---\n")
    _wish(vault, "mid-old", "---\ntitle: Mid, older\nstatus: open\npriority: medium\ndate: 2026-11-04\n---\n")
    _wish(vault, "odd", "---\ntitle: Odd one\nstatus: open\npriority: urgent\n---\n")
    _wish(vault, "untitled", "---\nstatus: open\npriority: high\ndate: 2026-11-05\n---\n")
    _wish(vault, "broken", "---\ntitle: [unclosed\n---\n")
    _wish(vault, "plain", "no frontmatter here\n")
    _wish(vault, "list-front", "---\n- a\n- b\n---\n")
    _wish(vault, "open-ended", "---\ntitle: Never closed\nstatus: open\n")
    (vault / "docs" / "wishlist" / "notes.txt").write_text("not markdown", encoding="utf-8")
    (vault / "docs" / "wishlist" / "bad.md").write_bytes(b"\xff\xfe")
    doc = _doc(vault, env)
    slugs = [w["slug"] for w in doc["up_next"]]
    assert slugs[:2] == ["high-going", "untitled"] and len(slugs) == 3
    assert doc["up_next"][1] == {"slug": "untitled", "title": "untitled"}
    full = build_digest(vault, env, now=NOW, tz=UTC_TZ)
    assert full["up_next"][0] == {"slug": "high-going", "title": "A high one"}


def test_digest_up_next_orders_priority_then_date(tmp_path):
    vault, env = _vault(tmp_path)
    _wish(vault, "low-open", "---\ntitle: Low\nstatus: open\npriority: low\ndate: 2026-11-02\n---\n")
    _wish(vault, "high-going", "---\ntitle: High\nstatus: in-progress\npriority: high\ndate: 2026-11-03\n---\n")
    _wish(vault, "done", "---\ntitle: Done\nstatus: done\npriority: high\ndate: 2026-11-01\n---\n")
    assert [w["slug"] for w in _doc(vault, env)["up_next"]] == ["high-going", "low-open"]
    _wish(vault, "mid-new", "---\ntitle: M2\nstatus: open\npriority: medium\ndate: 2026-11-09\n---\n")
    _wish(vault, "mid-old", "---\ntitle: M1\nstatus: open\npriority: medium\ndate: 2026-11-04\n---\n")
    assert [w["slug"] for w in _doc(vault, env)["up_next"]] == ["high-going", "mid-old", "mid-new"]


def test_digest_needs_ok_counts_awaiting_items(tmp_path):
    vault, env = _vault(tmp_path)
    pending = vault / "governance-pending"
    pending.mkdir()
    for slug in ("one", "two"):
        (pending / f"{slug}.md").write_text(
            f"---\nsummary: Item {slug}\nwhy: because\ntargets:\n  - briefing\n---\n\n"
            "```diff\n--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-old\n+new\n```\n",
            encoding="utf-8",
        )
    doc = _doc(vault, env)
    assert doc["needs_ok"] == 2
    assert "Needs your OK (2) — open the Changes tab" in render_text(doc)


def test_digest_of_an_empty_vault_has_no_sections(tmp_path):
    vault, env = _vault(tmp_path)
    doc = _doc(vault, env)
    assert doc["testing"] == [] and doc["shipped"] == [] and doc["learned"] == [] and doc["up_next"] == []
    assert doc["needs_ok"] == 0 and isinstance(doc["verified"], bool)
    assert doc["engagement"] == {"this_week": 0, "last_week": 0, "change_pct": None, "feedback": 0, "reliance": 0}
    lines = render_text(doc).splitlines()
    assert len(lines) == 2 and lines[0] == "What Scout is working on — week of Nov 16"
    assert lines[1].startswith("Your engagement: 0 this week")


def _full_doc() -> dict:
    row = {"id": "chg-0123456789ab", "summary": "A plain sentence about a change"}
    return {
        "schema_version": 1,
        "generated_at": NOW.isoformat(),
        "week_of": "2026-11-16",
        "verified": True,
        "engagement": {"this_week": 41, "last_week": 36, "change_pct": 14, "feedback": 18, "reliance": 23},
        "noise": {"this_week": 6, "last_week": 8},
        "days": [{"date": f"2026-11-{12 + i}", "engagement": i * 2, "noise": 1} for i in range(7)],
        "testing": [{**row, "days_left": n} for n in (1, 4, 9)],
        "shipped": [dict(row) for _ in range(3)],
        "learned": [
            {**row, "verdict": "helped", "suspect": False},
            {**row, "verdict": "hurt", "suspect": True},
            {**row, "verdict": "no clear effect", "suspect": False},
        ],
        "up_next": [{"slug": f"w{i}", "title": f"Wish {i}"} for i in range(3)],
        "needs_ok": 2,
    }


def test_render_text_never_exceeds_twelve_lines():
    text = render_text(_full_doc())
    lines = text.splitlines()
    assert len(lines) <= 12 and all("chg-" not in line for line in lines)
    assert lines[0] == "What Scout is working on — week of Nov 16"
    assert "Your engagement: 41 this week (↑ 14% vs last), noise 6 (↓ 2)" in lines[1]
    assert lines[2].endswith("feedback 18 · sessions that used Scout 23")
    assert any(line.startswith("Testing now") for line in lines)
    assert any("(Undo in the Changes tab)" in line and "Suspect" in line for line in lines)
    assert lines[-1] == "Needs your OK (2) — open the Changes tab"


def test_render_text_keeps_every_section_when_trimming():
    lines = render_text(_full_doc()).splitlines()
    for header in ("Testing now", "Just shipped", "What it learned", "Up next"):
        assert any(line.startswith(header) for line in lines)


def test_render_text_shows_a_short_document_in_full():
    doc = _full_doc()
    doc["testing"] = doc["testing"][:1]
    doc["shipped"] = []
    doc["learned"] = doc["learned"][:1]
    doc["up_next"] = []
    doc["needs_ok"] = 0
    doc["noise"] = {"this_week": 3, "last_week": 3}
    doc["engagement"] = {**doc["engagement"], "change_pct": -20}
    lines = render_text(doc).splitlines()
    assert "↓ 20% vs last" in lines[1] and "noise 3 (same)" in lines[1]
    assert "  • A plain sentence about a change — 1 day left" in lines
    assert "  • ✅ Helped: A plain sentence about a change" in lines
    assert not any(line.startswith(("Just shipped", "Up next", "Needs")) for line in lines)


def test_render_text_words_the_other_verdicts_and_a_flat_week():
    doc = _full_doc()
    doc["testing"] = doc["shipped"] = doc["up_next"] = []
    doc["learned"] = [
        {"id": "chg-0123456789ab", "summary": "S1", "verdict": "no clear effect", "suspect": False},
        {"id": "chg-0123456789ac", "summary": "S2", "verdict": "rolled back", "suspect": False},
    ]
    doc["engagement"] = {**doc["engagement"], "change_pct": 0}
    doc["noise"] = {"this_week": 9, "last_week": 3}
    lines = render_text(doc).splitlines()
    assert "  • No clear effect: S1" in lines and "  • Rolled back: S2" in lines
    assert "(→ 0% vs last)" in lines[1] and "noise 9 (↑ 6)" in lines[1]


def test_render_text_draws_a_sparkline_only_when_there_is_engagement():
    doc = _full_doc()
    assert "▁" in render_text(doc).splitlines()[2] and "█" in render_text(doc).splitlines()[2]
    doc["days"] = [{"date": f"2026-11-{12 + i}", "engagement": 0, "noise": 0} for i in range(7)]
    doc["engagement"] = {**doc["engagement"], "feedback": 0, "reliance": 0}
    assert not any("feedback" in line for line in render_text(doc).splitlines())


def test_cli_prints_text_and_json(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from scout.cli import app

    vault, env = _vault(tmp_path)
    _change(vault, 1, 3)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    monkeypatch.setenv("HOME", env["HOME"])
    runner = CliRunner()
    out = runner.invoke(app, ["changes", "digest"])
    assert out.exit_code == 0, out.output
    assert out.stdout.startswith("What Scout is working on") and "chg-" not in out.stdout
    out = runner.invoke(app, ["changes", "digest", "--json"])
    assert out.exit_code == 0, out.output
    doc = json.loads(out.stdout)
    assert doc["schema_version"] == 1 and len(doc["days"]) == 7 and doc["testing"][0]["days_left"] > 0


def test_cli_exits_two_without_a_vault(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from scout.cli import app

    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("HOME", str(tmp_path))
    out = CliRunner().invoke(app, ["changes", "digest"])
    assert out.exit_code == 2
