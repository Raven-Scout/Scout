"""The governance-pending folder reader and the owner's decision rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.changes.governance import (
    PENDING_DIR,
    decision,
    decision_ref,
    diff_paths,
    read_pending,
    undo_requested,
)
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
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "--- a/ok dir/../../etc/x\n+++ b/SKILL.md"),
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", '--- "a/b c/../x"\n+++ b/SKILL.md'),
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "diff --git a/x y b/x y\n--- a/SKILL.md\n+++ b/SKILL.md"),
        GOOD.replace(
            "--- a/SKILL.md\n+++ b/SKILL.md", "copy from SKILL.md\ncopy to ../x\n--- a/SKILL.md\n+++ b/SKILL.md"
        ),
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "--- ../x\n+++ SKILL.md"),
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "diff --git a/../x b/SKILL.md\n--- a/SKILL.md\n+++ b/SKILL.md"),
        GOOD.replace(
            "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-old\n+new\n", "@@ -1 +1 @@\n-old\n+new\n"
        ),  # no headers
        "---\n: : :\n---\n```diff\n```\n",
    ],
)
def test_files_that_do_not_fit_are_counted(tmp_path, text):
    _write(tmp_path, "x.md", text)
    assert read_pending(tmp_path) == ([], 1)


def test_a_removed_line_that_reads_like_a_header_is_content(tmp_path):
    body = "@@ -1,2 +1,2 @@\n-- foo\n-old\n+new\n+++ bar\n"
    _write(tmp_path, "x.md", GOOD.replace("@@ -1 +1 @@\n-old\n+new\n", body))
    assert [i.id for i in read_pending(tmp_path)[0]] == ["gov-x"]
    _write(
        tmp_path,
        "y.md",
        GOOD.replace("--- a/SKILL.md\n+++ b/SKILL.md", "--- a/SKILL.md\t2026-01-01 10:00\n+++ b/SKILL.md"),
    )
    assert [i.id for i in read_pending(tmp_path)[0]] == ["gov-x", "gov-y"]


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


H = "0123456789abcdef"


def test_newest_decision_wins():
    sigs = [_sig(f"approve:gov-a@{H}", 0), _sig(f"reject:gov-a@{H}", 5)]
    assert decision(sigs, "gov-a", H) == "rejected"
    assert decision([*sigs, _sig(f"approve:gov-a@{H}", 9)], "gov-a", H) == "approved"
    assert decision(sigs, "gov-b", H) == "awaiting-ok"
    assert decision([_sig(f"approve:gov-a@{H}", 0, kind="dm-reply")], "gov-a", H) == "awaiting-ok"
    # Rows in any order: the newest stands, an older one read after it doesn't.
    assert decision([_sig(f"reject:gov-a@{H}", 5), _sig(f"approve:gov-a@{H}", 0)], "gov-a", H) == "rejected"


def test_a_spent_decision_counts_for_nothing():
    """#747 review: a decision made at or before the file was last acted on is used up."""
    sigs = [_sig(f"approve:gov-a@{H}", 0)]
    spent = datetime(2026, 11, 16, 9, 1, tzinfo=UTC)
    assert decision(sigs, "gov-a", H, spent=spent) == "awaiting-ok"
    assert decision([*sigs, _sig(f"approve:gov-a@{H}", 2)], "gov-a", H, spent=spent) == "approved"


def test_an_unbound_decision_in_the_same_second_as_its_use_is_spent():
    """Git keeps whole seconds: an approval at 09:00:00.7 applied by a commit at 09:00:00 is used up."""
    row = Signal(
        ts=datetime(2026, 11, 16, 9, 0, 0, 700_000, tzinfo=UTC),
        kind="approval",
        surface="app",
        ref=f"approve:gov-a@{H}",
    )
    assert decision([row], "gov-a", H, spent=datetime(2026, 11, 16, 9, 0, 0, tzinfo=UTC)) == "awaiting-ok"
    assert decision([row], "gov-a", H, spent=datetime(2026, 11, 16, 8, 59, 59, tzinfo=UTC)) == "approved"


def test_a_bound_decision_counts_only_for_its_filing_whatever_the_clocks_say():
    """#747 review: a decision names the commit that filed the file; a later filing (a new commit) waits for a new
    one, however the commit dates were set."""
    first, second = "a" * 40, "b" * 40
    assert decision_ref("approve", "gov-a", H, first) == f"approve:gov-a@{H}@{first}"
    rows = [_sig(decision_ref("approve", "gov-a", H, first), 0)]
    assert decision(rows, "gov-a", H, filed=first) == "approved"
    assert decision(rows, "gov-a", H, filed=second) == "awaiting-ok"
    assert decision(rows, "gov-a", H) == "awaiting-ok"  # not committed as it is now: no filing to match
    rows.append(_sig(decision_ref("reject", "gov-a", H, second), 1))
    assert decision(rows, "gov-a", H, filed=second) == "rejected"
    # A bound row ignores `spent`: commit order already said which filing it was for.
    spent = datetime(2026, 11, 17, tzinfo=UTC)
    assert decision(rows[:1], "gov-a", H, filed=first, spent=spent) == "approved"


def test_a_decision_counts_only_for_the_diff_it_was_made_on():
    """I3: a row for another hash (the diff changed after the decision) or with none (the old ref form) is nothing."""
    assert decision([_sig("approve:gov-a", 0)], "gov-a", H) == "awaiting-ok"
    assert decision([_sig("approve:gov-a@ffffffffffffffff", 0)], "gov-a", H) == "awaiting-ok"
    newer_diff = [_sig(f"approve:gov-a@{H}", 0), _sig("reject:gov-a@ffffffffffffffff", 5)]
    assert decision(newer_diff, "gov-a", H) == "approved"
    assert decision_ref("approve", "gov-a", H) == f"approve:gov-a@{H}"


def test_the_hash_is_the_diff_blocks_and_changes_with_it(tmp_path):
    _write(tmp_path, "skip-red.md", GOOD)
    [item] = read_pending(tmp_path)[0]
    assert len(item.hash) == 64
    _write(tmp_path, "skip-red.md", GOOD.replace("+new", "+newer"))
    assert read_pending(tmp_path)[0][0].hash != item.hash
    # Prose outside the diff block doesn't change what was approved.
    _write(tmp_path, "skip-red.md", GOOD.replace("Some prose about it.", "Other prose."))
    assert read_pending(tmp_path)[0][0].hash == item.hash


def test_governance_flag_defaults_true_and_must_be_a_bool(tmp_path):
    _write(tmp_path, "a.md", GOOD)
    _write(tmp_path, "b.md", GOOD.replace("targets:", "governance: false\ntargets:"))
    _write(tmp_path, "c.md", GOOD.replace("targets:", "governance: maybe\ntargets:"))
    items, bad = read_pending(tmp_path)
    assert [(i.id, i.governance) for i in items] == [("gov-a", True), ("gov-b", False)] and bad == 1


def test_diff_paths_reads_every_header_side():
    """m1: both sides of a rename come from the headers (git apply --numstat prints only the new one)."""
    rename = (
        "diff --git a/knowledge-base/a.md b/knowledge-base/b.md\nsimilarity index 100%\n"
        "rename from knowledge-base/a.md\nrename to knowledge-base/b.md\n"
    )
    assert diff_paths(rename) == ["knowledge-base/a.md", "knowledge-base/b.md"]  # a pure rename
    both = rename + "--- a/knowledge-base/a.md\n+++ b/knowledge-base/b.md\n@@ -1 +1 @@\n-x\n+y\n"
    assert diff_paths(both) == ["knowledge-base/a.md", "knowledge-base/b.md"]
    new_file = "--- /dev/null\n+++ b/scripts/new.py\n@@ -0,0 +1 @@\n+print()\n"
    assert diff_paths(new_file) == ["scripts/new.py"]
    # git apply strips one folder (-p1): an unprefixed ---/+++ name would patch another path, so it is refused.
    assert diff_paths("--- scripts/x.py\n+++ scripts/x.py\n@@ -1 +1 @@\n-a\n+b\n") is None


@pytest.mark.parametrize(
    "body",
    [
        "@@ -1,2 +1 @@\n-old\n+new\n",  # the old side ends one line early
        "@@ -1 +1,2 @@\n-old\n+new\n",  # the new side ends one line early
        "@@ -1 +1 @@\n-old\n-older\n+new\n",  # one removed line too many
        "@@ -1 +1 @@\n-old\n?what\n",  # a line no hunk can hold
        "@@ junk @@\n-old\n+new\n",  # an @@ line that isn't a hunk header
    ],
)
def test_hunk_counts_that_do_not_add_up_are_unreadable(tmp_path, body):
    _write(tmp_path, "x.md", GOOD.replace("@@ -1 +1 @@\n-old\n+new\n", body))
    assert read_pending(tmp_path) == ([], 1)


def test_no_newline_markers_frontmatter_shapes_and_empty_targets(tmp_path):
    marker = "\\ No newline at end of file\n"
    no_newline = GOOD.replace("-old\n+new\n", f"-old\n{marker}+new\n{marker}")
    _write(tmp_path, "a.md", no_newline)
    _write(tmp_path, "b.md", GOOD.replace("targets:\n  - briefing\n", "targets:\n"))
    _write(tmp_path, "c.md", "---\n- a list\n- not a mapping\n---\n" + GOOD.split("---\n", 2)[2])
    items, bad = read_pending(tmp_path)
    assert [(i.id, i.targets) for i in items] == [("gov-a", ("briefing",)), ("gov-b", ())] and bad == 1


def test_undo_requested():
    assert undo_requested([_sig("undo:chg-0123456789ab", 0)], "chg-0123456789ab")
    assert not undo_requested([_sig("approve:chg-0123456789ab", 0)], "chg-0123456789ab")
