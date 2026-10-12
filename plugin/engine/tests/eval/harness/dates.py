"""Relative date macros for the eval fixture (spec 2026-10-12 §5.2).

The model sees the real date, so fixture dates are written relative to the
day the vault is built and rendered here:

- ``{{today}}``: the build day;
- ``{{d-N}}`` / ``{{d+N}}``: calendar days;
- ``{{wd-N}}`` / ``{{wd+N}}``: working days (Saturday and Sunday skipped; a
  weekend build day counts from the Friday before it);
- ``{{month}}``: ``YYYY-MM``.

Anything else in double braces is left alone, so ``UNRENDERED`` can catch it.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

UNRENDERED = re.compile(r"\{\{[^}]*\}\}")
_MACRO = re.compile(r"\{\{(today|month|(d|wd)([+-]\d+))\}\}")


def _add_working_days(start: date, n: int) -> date:
    day = start
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    while remaining:
        day += timedelta(days=step)
        if day.weekday() < 5:
            remaining -= 1
    return day


def _expand(match: re.Match[str], today: date) -> str:
    name, kind, offset = match.group(1), match.group(2), match.group(3)
    if name == "today":
        return today.isoformat()
    if name == "month":
        return today.strftime("%Y-%m")
    n = int(offset)
    if kind == "d":
        return (today + timedelta(days=n)).isoformat()
    return _add_working_days(today, n).isoformat()


def render_dates(text: str, today: date) -> str:
    return _MACRO.sub(lambda m: _expand(m, today), text)
