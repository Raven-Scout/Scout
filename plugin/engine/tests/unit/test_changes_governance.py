"""The governance-pending folder reader and the owner's decision rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.changes.governance import PENDING_DIR, decision, read_pending, undo_requested
from scout.changes.models import Signal

GOOD = """---
summary: Let the briefing skip a carried red item
why: The same red item was carried for six days with no reply.
targets:
  - briefing
---

Some prose about it.

```diff
--- a/SKILL.md
+++ b/SKILL.md
@@ -1 +1 @@
-old
+new
```
"""


def _write(vault: Path, name: str, text: str) -> None:
    folder = vault / PENDING_DIR
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def test_read_pending_parses_a_good_file_and_counts_a_bad_one(tmp_path):
    _write(tmp_path, "skip-red.md", GOOD)
    _write(tmp_path, "broken.md", "no frontmatter at all\n")
    items, bad = read_pending(tmp_path)
    assert bad == 1
    [item] = items
    assert item.id == "gov-skip-red" and item.slug == "skip-red"
    assert item.summary == "Let the briefing skip a carried red item"
    assert item.targets == ("briefing",) and item.path == "governance-pending/skip-red.md"
    assert "+new" in item.diff


def test_applied_folder_is_not_pending(tmp_path):
    _write(tmp_path, "skip-red.md", GOOD)
    applied = tmp_path / PENDING_DIR / "applied"
    applied.mkdir()
    (applied / "old.md").write_text(GOOD)
    items, bad = read_pending(tmp_path)
    assert [i.id for i in items] == ["gov-skip-red"] and bad == 0


def test_missing_folder_is_no_files(tmp_path):
    assert read_pending(tmp_path) == ([], 0)


@pytest.mark.parametrize(
    "text",
    [
        GOOD.replace("summary: Let the briefing skip a carried red item", "summary: " + "x" * 121),
        GOOD.replace("why: The same", "why2: The same"),
        GOOD.replace("```diff", "```text"),
        GOOD + "\n```diff\n--- a/X\n+++ b/X\n```\n",  # two diff blocks
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "--- a/../outside.md\n+++ b/../outside.md"),
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "--- /etc/passwd\n+++ /etc/passwd"),
        GOOD.replace("targets:\n  - briefing", "targets: briefing"),
        "---\n: : :\n---\n```diff\n```\n",
    ],
)
def test_files_that_do_not_fit_are_counted(tmp_path, text):
    _write(tmp_path, "x.md", text)
    assert read_pending(tmp_path) == ([], 1)


def test_bad_slug_is_counted(tmp_path):
    _write(tmp_path, "Bad Slug.md", GOOD)
    assert read_pending(tmp_path) == ([], 1)


def test_targets_are_optional(tmp_path):
    _write(tmp_path, "t.md", GOOD.replace("targets:\n  - briefing\n", ""))
    [item] = read_pending(tmp_path)[0]
    assert item.targets == ()


def _sig(ref: str, minutes: int, kind: str = "approval") -> Signal:
    return Signal(
        ts=datetime(2026, 11, 16, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes), kind=kind, surface="scoutctl", ref=ref
    )


def test_newest_decision_wins():
    sigs = [_sig("approve:gov-a", 0), _sig("reject:gov-a", 5)]
    assert decision(sigs, "gov-a") == "rejected"
    assert decision([*sigs, _sig("approve:gov-a", 9)], "gov-a") == "approved"
    assert decision(sigs, "gov-b") == "awaiting-ok"
    assert decision([_sig("approve:gov-a", 0, kind="dm-reply")], "gov-a") == "awaiting-ok"


def test_undo_requested():
    assert undo_requested([_sig("undo:chg-0123456789ab", 0)], "chg-0123456789ab")
    assert not undo_requested([_sig("approve:chg-0123456789ab", 0)], "chg-0123456789ab")
