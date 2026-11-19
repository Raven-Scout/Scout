"""``no_drop``: no open item silently disappears (dropoff audit, phases/core/action-items.md:288-295).

An open tag from yesterday must still be in today's file (open, or in
✅ Recently Completed), be open in ``action-items/backlog.md`` (*The Backlog
and the Budget*), be in the stale archive marked today, or be one of the
scenario's expected closures.
"""

from __future__ import annotations

from collections.abc import Sequence

from scout.action_items.parser import ActionItem

from . import CheckResult, by_tag

DONE = "✅"


def _open_tags(items: Sequence[ActionItem]) -> set[str]:
    return {tag for tag, item in by_tag(items).items() if item.status != "done" and not item.section.startswith(DONE)}


def no_drop(
    yesterday: Sequence[ActionItem],
    today: Sequence[ActionItem] | None,
    *,
    archived_tags: set[str],
    expected_closures: set[str],
    backlog_tags: frozenset[str] | set[str] = frozenset(),
) -> CheckResult:
    if today is None:
        return CheckResult("no_drop", None, ("missing_output",))
    present = set(by_tag(today))
    gone = _open_tags(yesterday) - present - backlog_tags - archived_tags - expected_closures
    details = tuple(f"{tag}: open yesterday, gone today" for tag in sorted(gone))
    return CheckResult("no_drop", not gone, details)
