"""Unit tests for scout.action_items._common — shared mutator helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.action_items._common import list_comment_lines, resolve_target, select_comment
from scout.action_items.parser import ActionItem
from scout.errors import ActionItemAmbiguous, ActionItemError, ActionItemNotFound
from scout.id_map import IdMap, IdMapEntry


def test_resolve_target_by_id_returns_entry_and_match(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "task X", "today.md", 5))
    m.save()
    items = [
        ActionItem(
            priority="🔴",
            title="task X",
            status="open",
            section="In Progress",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] [#A3F7] 🔴 task X",
            short_prefix="A3F7",
        ),
        ActionItem(
            priority="",
            title="other",
            status="open",
            section="In Progress",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] other",
            short_prefix=None,
        ),
    ]
    target, ulid, via = resolve_target(items=items, data_dir=fake_data_dir, by_id="A3F7", by_subject=None)
    assert target.title == "task X"
    assert ulid == "01HXAAA"
    assert via == "id"


def test_resolve_target_by_subject_substring(fake_data_dir: Path) -> None:
    items = [
        ActionItem(
            priority="🔴",
            title="Reply to vendor on contract",
            status="open",
            section="To Do",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] 🔴 Reply to vendor on contract",
            short_prefix=None,
        ),
    ]
    target, ulid, via = resolve_target(items=items, data_dir=fake_data_dir, by_id=None, by_subject="vendor")
    assert target.title == "Reply to vendor on contract"
    assert ulid == ""
    assert via == "subject"


def test_resolve_target_rejects_both_args_unset(fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemError, match="exactly one"):
        resolve_target(items=[], data_dir=fake_data_dir, by_id=None, by_subject=None)


def test_resolve_target_rejects_both_args_set(fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemError, match="exactly one"):
        resolve_target(items=[], data_dir=fake_data_dir, by_id="A3F7", by_subject="x")


def test_resolve_target_unknown_id_raises(fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemNotFound, match="prefix.*not found"):
        resolve_target(items=[], data_dir=fake_data_dir, by_id="ZZZZ", by_subject=None)


def test_resolve_target_auto_registers_prefix_in_file_but_not_idmap(
    fake_data_dir: Path,
) -> None:
    """The briefing/consolidation skill writes fresh `[#XXXX]` lines directly
    into the markdown without registering them in the id-map. When a mutator
    sees the prefix on a parsed item but the id-map is empty, it should
    register on-the-fly so `mark-done --by-id` keeps working without a manual
    `backfill-prefixes` pass."""
    items = [
        ActionItem(
            priority="🔥",
            title="Rotate vendor API token",
            status="open",
            section="Urgent",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] [#KPTK] 🔥 Rotate vendor API token",
            line_number=12,
            short_prefix="KPTK",
        ),
    ]
    target, ulid, via = resolve_target(items=items, data_dir=fake_data_dir, by_id="KPTK", by_subject=None)
    assert target.short_prefix == "KPTK"
    assert via == "id"
    assert ulid  # a new ULID was minted

    # Persisted: a follow-up call sees the registered entry rather than
    # auto-registering again.
    m2 = IdMap.load(fake_data_dir)
    e2 = m2.lookup_by_prefix("KPTK")
    assert e2 is not None
    assert e2.ulid == ulid
    assert e2.last_title == "Rotate vendor API token"


def test_resolve_target_ambiguous_subject_raises(fake_data_dir: Path) -> None:
    items = [
        ActionItem(
            priority="",
            title="Reply to alice",
            status="open",
            section="To Do",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] Reply to alice",
            short_prefix=None,
        ),
        ActionItem(
            priority="",
            title="Reply to bob",
            status="open",
            section="To Do",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] Reply to bob",
            short_prefix=None,
        ),
    ]
    with pytest.raises(ActionItemAmbiguous, match="ambiguous"):
        resolve_target(items=items, data_dir=fake_data_dir, by_id=None, by_subject="reply")


def test_resolve_target_prefix_in_idmap_but_missing_from_items_raises(
    fake_data_dir: Path,
) -> None:
    """If the IdMap knows a prefix but the parsed items list doesn't include it
    (e.g., user passed `--by-id A3F7` while looking at the wrong day's file),
    raise a clear error rather than silently no-op."""
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "task X", "today.md", 5))
    m.save()
    # Items list is empty — simulates the wrong-file case.
    with pytest.raises(ActionItemNotFound, match="is in id-map but not present"):
        resolve_target(items=[], data_dir=fake_data_dir, by_id="A3F7", by_subject=None)


# Regression: by_subject must match against item.title (cleaned), not
# raw_line (which includes the [#XXXX] prefix marker and the priority
# emoji). Otherwise a search for "A3F7" silently matches the prefix
# token of an unrelated task. Issue #32.


def test_resolve_target_by_subject_does_not_match_prefix_token(fake_data_dir: Path) -> None:
    """A subject substring that only appears inside the [#XXXX] prefix
    marker (and not in the cleaned title) must NOT match — users searching
    for a prefix should use --by-id, not --by-subject."""
    items = [
        ActionItem(
            priority="🔴",
            title="task X",
            status="open",
            section="In Progress",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] [#A3F7] 🔴 task X",
            line_number=5,
            short_prefix="A3F7",
        ),
    ]
    with pytest.raises(ActionItemNotFound, match="no open task matched"):
        resolve_target(items=items, data_dir=fake_data_dir, by_id=None, by_subject="A3F7")


def test_resolve_target_by_subject_matches_title_substring(fake_data_dir: Path) -> None:
    """Sanity: a substring that appears in the cleaned title still matches."""
    items = [
        ActionItem(
            priority="🔴",
            title="task X",
            status="open",
            section="In Progress",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] [#A3F7] 🔴 task X",
            line_number=5,
            short_prefix="A3F7",
        ),
    ]
    target, _, via = resolve_target(items=items, data_dir=fake_data_dir, by_id=None, by_subject="task x")
    assert target.title == "task X"
    assert via == "subject"


def test_resolve_target_ambiguous_id_raises(fake_data_dir: Path) -> None:
    """Two open tasks sharing a [#TAG] is ambiguous for --by-id; raise rather
    than silently acting on the first (reusable human tags can collide)."""
    items = [
        ActionItem(
            priority="🔴",
            title="Team 1:1 follow-through",
            status="open",
            section="To Do",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] [#IOTA] Team 1:1 follow-through",
            line_number=5,
            short_prefix="IOTA",
        ),
        ActionItem(
            priority="🟡",
            title="Design doc review",
            status="open",
            section="To Do",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] [#IOTA] Design doc review",
            line_number=9,
            short_prefix="IOTA",
        ),
    ]
    with pytest.raises(ActionItemAmbiguous, match="ambiguous id"):
        resolve_target(items=items, data_dir=fake_data_dir, by_id="IOTA", by_subject=None)


def test_resolve_target_by_subject_returns_the_registered_ulid(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "task X", "today.md", 5))
    m.save()
    items = [
        ActionItem(
            priority="🔴",
            title="task X",
            status="open",
            section="In Progress",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] [#A3F7] 🔴 task X",
            line_number=5,
            short_prefix="A3F7",
        ),
    ]
    _, ulid, via = resolve_target(items=items, data_dir=fake_data_dir, by_id=None, by_subject="task x")
    assert (ulid, via) == ("01HXAAA", "subject")


# ----- list_comment_lines / select_comment --------------------------------------


_TASK_FILE = """# Action Items

- [ ] [#A3F7] 🔴 Reply to Priya about the demo
  - see [[demo-notes]]
  - alex: drafted a reply
  - snoozed-until: 2026-09-10
  - Sam: waiting on numbers
- [ ] Next task
  - alex: belongs to the next task
"""


def _task_file(tmp_path: Path, text: str = _TASK_FILE) -> Path:
    path = tmp_path / "today.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_list_comment_lines_skips_details_and_snooze_markers(tmp_path: Path) -> None:
    path = _task_file(tmp_path)
    comments = list_comment_lines(path, task_line_number=3)
    assert comments == [(5, "alex", "drafted a reply"), (7, "Sam", "waiting on numbers")]
    # The next task's comment runs to the end of the file.
    assert list_comment_lines(path, task_line_number=8) == [(9, "alex", "belongs to the next task")]


def test_list_comment_lines_keeps_scanning_past_non_comment_sub_bullets(tmp_path: Path) -> None:
    path = _task_file(
        tmp_path,
        "- [ ] [#A3F7] Ship the tracing job\n"
        "  - [ ] sub-task: not a comment\n"  # `[ ]` is not an author
        "  plain continuation text\n"
        "  - alex: after the sub-task\n"
        "\n"
        "  - alex: after a blank line — not attached\n",
    )
    assert list_comment_lines(path, task_line_number=1) == [(4, "alex", "after the sub-task")]


@pytest.mark.parametrize("line", [0, 12])
def test_list_comment_lines_rejects_an_out_of_range_task_line(tmp_path: Path, line: int) -> None:
    path = _task_file(tmp_path)
    with pytest.raises(ActionItemError, match=f"task line {line} out of range"):
        list_comment_lines(path, task_line_number=line)


_COMMENTS = [(4, "alex", "drafted a reply"), (7, "Sam", "waiting on numbers"), (8, "alex", "numbers in")]


def test_select_comment_by_index_and_by_unique_text() -> None:
    assert select_comment(candidates=_COMMENTS, index=2, text=None) == _COMMENTS[1]
    assert select_comment(candidates=_COMMENTS, index=None, text="DRAFTED") == _COMMENTS[0]


@pytest.mark.parametrize(
    ("candidates", "index", "text", "message"),
    [
        (_COMMENTS, None, None, "exactly one of --index or --text"),
        (_COMMENTS, 1, "x", "exactly one of --index or --text"),
        ([], 1, None, "no comments found on this task"),
        (_COMMENTS, 0, None, r"--index 0 out of range; task has 3 comment\(s\)"),
        (_COMMENTS, 4, None, r"--index 4 out of range"),
        (_COMMENTS, None, "nothing like it", "no comment matched text: 'nothing like it'"),
        (_COMMENTS, None, "numbers", "ambiguous comment text 'numbers'; matched 2"),
    ],
)
def test_select_comment_rejects_bad_selectors(
    candidates: list[tuple[int, str, str]], index: int | None, text: str | None, message: str
) -> None:
    with pytest.raises(ActionItemError, match=message):
        select_comment(candidates=candidates, index=index, text=text)
