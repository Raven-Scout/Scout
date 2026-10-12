"""Output checks on a sample's post-run vault (spec 2026-10-12 §6.1).

Every check is pure: the runner parses files with the engine's own parser and
passes the items in, so the harness and the product agree on what a file
means, and each check is tested against committed golden text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from scout.action_items.parser import ActionItem

FOCUS = "🎯"


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool | None  # None: could not evaluate (e.g. no daily file was written)
    details: tuple[str, ...]


def section_emoji(item: ActionItem) -> str:
    return item.section.split(" ", 1)[0]


def by_tag(items: Sequence[ActionItem]) -> dict[str, ActionItem]:
    """Tag → item, preferring the item's tier section over its Today's Focus entry."""
    out: dict[str, ActionItem] = {}
    for item in items:
        if not item.short_prefix:
            continue
        if item.short_prefix not in out or section_emoji(out[item.short_prefix]) == FOCUS:
            out[item.short_prefix] = item
    return out
