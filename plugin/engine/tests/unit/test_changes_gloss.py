"""Plain-words glosses for a change's why and targets (I2, I3): every identifier the Changes tab shows is glossed."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scout.changes.gloss import ENGAGEMENT, OWNER_ASK, RESEARCH, TITLE_MAX, VERDICT, Glossary
from scout.changes.models import Why

AUDIT = """# Scout Mistake Audit

## Mistake Log

- 2026-11-02: Pattern #249: recurred again on a Monday (this list line is evidence, not the title)

### Pattern #249: Carried **red** items nobody answered

Details.

## Pattern #288 — Wrap messages too long for [[slack-dm|Slack]]

## Pattern Summary

| Pattern | Occurrences | Status | Last Seen |
|---------|------------|--------|-----------|
| Pattern #12: Stale data from the [cache](https://example.invalid) | 3 | Open | 2026-11-01 |
| #13 | 1 | Open | 2026-11-01 |
| Pattern #249: A table row never beats the heading | 2 | Open | 2026-11-02 |
"""


def _vault(tmp_path: Path, audit: str | None = AUDIT) -> Path:
    vault = tmp_path / "Scout"
    (vault / "knowledge-base").mkdir(parents=True)
    if audit is not None:
        (vault / "knowledge-base" / "scout-mistake-audit.md").write_text(audit, encoding="utf-8")
    wishlist = vault / "docs" / "wishlist"
    wishlist.mkdir(parents=True)
    (wishlist / "gmail-empty-check.md").write_text(
        "---\ntitle: Say so when a Gmail search finds nothing\nstatus: done\n---\nBody.\n", encoding="utf-8"
    )
    (wishlist / "untitled.md").write_text("---\nstatus: open\n---\n", encoding="utf-8")
    (wishlist / "broken.md").write_text("---\ntitle: [unclosed\n---\n", encoding="utf-8")
    (wishlist / "no-frontmatter.md").write_text("Just prose.\n", encoding="utf-8")
    return vault


def test_pattern_titles_come_from_the_mistake_audit_headings_first(tmp_path):
    g = Glossary(_vault(tmp_path))
    assert g.of_value("pattern-249") == "Carried red items nobody answered"
    assert g.of_value("pattern-288") == "Wrap messages too long for Slack"
    assert g.of_value("pattern-12") == "Stale data from the cache"  # a summary-table row, links unwrapped
    assert g.of_value("pattern-13") is None  # a row with no title
    assert g.of_value("pattern-999") is None
    assert g.of_why(Why("pattern", "pattern-288", inferred=True)) == "Wrap messages too long for Slack"


def test_per_pattern_files_win_and_name_their_title(tmp_path):
    vault = _vault(tmp_path)
    folder = vault / "knowledge-base" / "scout-mistake-audit"
    folder.mkdir()
    (folder / "pattern-249.md").write_text("---\ntitle: From its own file\n---\n", encoding="utf-8")
    (folder / "pattern-288-wrap.md").write_text("# Pattern #288: Heading in its own file\n", encoding="utf-8")
    (folder / "pattern-12.md").write_text("no title here\n", encoding="utf-8")
    (folder / "notes.md").write_text("# Not a pattern\n", encoding="utf-8")
    g = Glossary(vault)
    assert g.of_value("pattern-249") == "From its own file"
    assert g.of_value("pattern-288") == "Heading in its own file"
    assert g.of_value("pattern-12") == "Stale data from the cache"  # its file has no title: the audit's


def test_fixed_glosses_and_unknowns(tmp_path):
    g = Glossary(_vault(tmp_path))
    assert g.of_value("owner-ask:1790000000.000100") == OWNER_ASK == "You asked for this"
    assert g.of_value("verdict:chg-0123456789ab") == VERDICT == "A test found the earlier change hurt"
    assert g.of_value("research:interp-notes") == RESEARCH == "From research"
    assert g.of_value("engagement") == ENGAGEMENT
    for value in ("", "briefing", "The same red item was carried for six days.", "unknown"):
        assert g.of_value(value) is None, value
    assert g.of_why(Why("unknown", "", inferred=True)) is None
    assert g.of_why(Why("owner-ask", "owner-ask:1", inferred=False)) == OWNER_ASK


def test_wishlist_titles(tmp_path):
    g = Glossary(_vault(tmp_path))
    assert g.of_value("wishlist:gmail-empty-check") == "Say so when a Gmail search finds nothing"
    assert g.of_value("wishlist-gmail-empty-check") == "Say so when a Gmail search finds nothing"
    # The scanner infers a wishlist why with the bare slug as its ref.
    assert g.of_why(Why("wishlist", "gmail-empty-check", inferred=True)) == "Say so when a Gmail search finds nothing"
    for slug in ("untitled", "broken", "no-frontmatter", "missing", "../etc/passwd", "a/b"):
        assert g.of_value(f"wishlist:{slug}") is None, slug


def test_titles_are_cut_and_odd_audits_are_no_titles(tmp_path):
    long = "x" * (TITLE_MAX + 40)
    g = Glossary(_vault(tmp_path, audit=f"### Pattern #1: {long}\n"))
    title = g.of_value("pattern-1")
    assert title is not None and len(title) == TITLE_MAX and title.endswith("…")
    assert Glossary(_vault(tmp_path / "none", audit=None)).of_value("pattern-1") is None
    bad = _vault(tmp_path / "bad", audit=None)
    (bad / "knowledge-base" / "scout-mistake-audit.md").write_bytes(b"\xff\xfe not utf-8 ### Pattern #1: x\n")
    assert Glossary(bad).of_value("pattern-1") is None


def test_a_symlinked_audit_or_wishlist_item_is_never_read(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntitle: Secret title\n---\n### Pattern #7: Secret title\n", encoding="utf-8")
    vault = _vault(tmp_path, audit=None)
    (vault / "knowledge-base" / "scout-mistake-audit.md").symlink_to(outside)
    (vault / "docs" / "wishlist" / "linked.md").symlink_to(outside)
    g = Glossary(vault)
    assert g.of_value("pattern-7") is None and g.of_value("wishlist:linked") is None


def test_the_glossary_reads_the_audit_once(tmp_path, monkeypatch):
    g = Glossary(_vault(tmp_path))
    assert g.of_value("pattern-249")
    reads = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: reads.append(self) or real(self, *a, **k))
    assert g.of_value("pattern-288") and g.of_value("pattern-12")
    assert reads == []


# --- the report: a row the real scanner produced carries a gloss -------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=os.environ)
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    vault = _vault(tmp_path)
    _git(vault, "init", "-q")
    _git(vault, "config", "user.name", "Scout Test")
    _git(vault, "config", "user.email", "scout@example.invalid")
    _git(vault, "config", "commit.gpgsign", "false")
    (vault / ".gitignore").write_text(".scout-logs/\n")
    (vault / "SKILL.md").write_text("v1\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "-m", "seed")
    return vault


def test_a_scanned_row_carries_its_gloss(repo, tmp_path):
    from scout.changes.report import changes_report
    from scout.changes.scan import scan

    (repo / "SKILL.md").write_text("v2\n")
    _git(repo, "commit", "-qam", "dreaming [22:12]: SKILL.md self-edit — fewer reds\n\nScout-Change-Why: pattern-249")
    (repo / "DREAMING.md").write_text("d\n")
    _git(repo, "add", "DREAMING.md")
    _git(repo, "commit", "-qm", "dreaming [22:14]: DREAMING.md self-edit — no reason given")
    rows = {c.summary: c for c in scan(repo, since=None)}  # the seed commit is a row of its own too
    first, second = rows["SKILL.md self-edit — fewer reds"], rows["DREAMING.md self-edit — no reason given"]
    assert first.why.gloss is None  # the ledger row itself carries none: it is glossed at report time
    now = datetime.now(UTC)
    env = {"HOME": str(tmp_path / "home")}
    doc = changes_report(repo, now=now, since=now - timedelta(days=1), env=env, tz=ZoneInfo("UTC"))
    by_id = {c["id"]: c for c in doc["changes"]}
    assert doc["verified"] is True
    assert by_id[first.id]["why"]["gloss"] == "Carried red items nobody answered"
    assert by_id[second.id]["why"]["gloss"] is None  # unknown: the app says "Reason not recorded"


def test_needs_ok_carries_why_and_target_glosses_and_its_context(repo, tmp_path):
    from scout.changes.report import changes_report

    pending = repo / "governance-pending"
    pending.mkdir()
    (pending / "skip-red.md").write_text(
        "---\nsummary: Let the briefing skip a carried red item\nwhy: pattern-249\n"
        "targets:\n  - pattern-288\n  - engagement\n  - briefing\n---\n\n"
        "A red item carried for six days with no reply\nis noise.\n\n"
        "```diff\n--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-v1\n+v2\n```\n\nAfter the diff.\n",
        encoding="utf-8",
    )
    (pending / "bare.md").write_text(
        "---\nsummary: A bare one\nwhy: Free text the run wrote.\n---\n"
        "```diff\n--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-v1\n+v2\n```\n",
        encoding="utf-8",
    )
    now = datetime.now(UTC)
    doc = changes_report(
        repo, now=now, since=now - timedelta(days=1), env={"HOME": str(tmp_path / "home")}, tz=ZoneInfo("UTC")
    )
    by_id = {n["id"]: n for n in doc["needs_ok"]}
    red = by_id["gov-skip-red"]
    assert red["why"] == "pattern-249" and red["why_gloss"] == "Carried red items nobody answered"
    assert red["targets_gloss"] == ["Wrap messages too long for Slack", ENGAGEMENT, None]
    assert red["context"] == "A red item carried for six days with no reply\nis noise.\n\nAfter the diff."
    bare = by_id["gov-bare"]
    assert bare["why_gloss"] is None and bare["targets_gloss"] == [] and bare["context"] is None
