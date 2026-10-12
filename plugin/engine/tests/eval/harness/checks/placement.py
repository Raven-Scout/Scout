"""``placement``: each seed lands in its expected section (#371, phases/core/action-items.md:37-62).

``expected`` maps a tag (``"RV"``) or a ref (``"ref:example-org/widgets#20"``,
matched in the item's line or sub-lines, for items a run creates) to the
section emojis it may sit under.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from scout.action_items.parser import ActionItem

from . import FOCUS, CheckResult, by_tag, section_emoji


def _find_by_ref(items: Sequence[ActionItem], ref: str) -> ActionItem | None:
    hits = [i for i in items if section_emoji(i) != FOCUS and (ref in i.raw_line or any(ref in d for d in i.details))]
    return hits[0] if hits else None


def placement(items: Sequence[ActionItem] | None, expected: Mapping[str, set[str]]) -> CheckResult:
    if items is None:
        return CheckResult("placement", None, ("missing_output",))
    tags = by_tag(items)
    problems = []
    for key in sorted(expected):
        if key.startswith("ref:"):
            item = _find_by_ref(items, key[4:])
            label = f"{key} ({item.short_prefix})" if item else key
        else:
            item, label = tags.get(key), key
        if item is None:
            problems.append(f"{label}: missing")
        elif section_emoji(item) not in expected[key]:
            allowed = " ".join(sorted(expected[key]))
            problems.append(f"{label}: in {item.section}, expected one of {allowed}")
    return CheckResult("placement", not problems, tuple(problems))
