"""``demotion``: #372's *Past-Due Tasks Step Down* (phases/core/action-items.md:67-78).

``expected`` maps a seed's tag to the branch the rule puts it on:

- ``hold``: under 7 quiet days. It stays in its section and its text is unchanged
  ("age is not a touch": no rewriting to say how overdue it is);
- ``step_down``: 7+ quiet days. At most 🟡; the body is the due date and one question
  (``due Sep 22; still want this?``); the context note gains a dated
  ``stepped down, past due with no news`` line;
- ``park``: 14 days after that line with no news. In 🅿️ Parked, its text unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from scout.action_items.parser import ActionItem

from . import CheckResult, by_tag, section_emoji
from .daily import body

_QUESTION = re.compile(r"\bdue [^;]+; still want this\?", re.IGNORECASE)
_NOTE_LINE = re.compile(r"(\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2} \d{1,2}).*stepped down, past due with no news")
PARKED = "🅿️"


def demotion(
    today: Sequence[ActionItem] | None,
    yesterday: Sequence[ActionItem],
    notes: Mapping[str, str],
    expected: Mapping[str, str],
) -> CheckResult:
    if today is None:
        return CheckResult("demotion", None, ("missing_output",))
    now, before = by_tag(today), by_tag(yesterday)
    problems: list[str] = []
    for tag in sorted(expected):
        branch, item, prior = expected[tag], now.get(tag), before.get(tag)
        if item is None:
            problems.append(f"{tag}: missing")
            continue
        where, label = section_emoji(item), f"{tag} ({branch})"
        unchanged = prior is not None and item.raw_line.strip() == prior.raw_line.strip()
        if branch == "hold":
            if prior is not None and where != section_emoji(prior):
                problems.append(f"{label}: moved from {section_emoji(prior)} to {where}")
            elif not unchanged:
                problems.append(f"{label}: text changed (age is not a touch)")
        elif branch == "step_down":
            if where == "🔴":
                problems.append(f"{label}: still in 🔴")
            if not _QUESTION.search(body(item)):
                problems.append(f"{label}: body doesn't say 'due <date>; still want this?'")
            if not _NOTE_LINE.search(notes.get(tag) or ""):
                problems.append(f"{label}: context note has no dated 'stepped down, past due with no news' line")
        elif branch == "park":
            if where != PARKED:
                problems.append(f"{label}: in {where}, expected {PARKED}")
            if not unchanged:
                problems.append(f"{label}: text changed")
        else:
            raise ValueError(f"unknown demotion branch {branch!r} for {tag}")
    return CheckResult("demotion", not problems, tuple(problems))
