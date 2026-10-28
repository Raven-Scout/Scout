"""Mandatory connector rules a briefing or consolidation is scored on.

- ``linear_delta``: the Linear connector phase's own-activity report. Every
  issue the user acted on since the last run is named in today's 📋 Scout
  Digest or in the wrap notification. Either surface counts, so a run built
  from a phase that predates the digest heading is scored on the same footing.
- ``red_poll``: the Slack connector phase's 🔴 Project Channel Poll. Every
  channel a 🔴 project declares was read with ``slack_read_channel``, as the
  stubbed calls show; search alone does not count.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from scout.action_items.prefetched import section, unfenced

from . import CheckResult

DIGEST = "📋 Scout Digest"


def _mentions(identifier: str, text: str) -> bool:
    return bool(re.search(rf"(?<![\w-]){re.escape(identifier)}(?!\d)", text, re.IGNORECASE))


def linear_delta(daily: str | None, wrap: str | None, expected: Sequence[str]) -> CheckResult:
    if daily is None and wrap is None:
        return CheckResult("linear_delta", None, ("no daily file and no wrap",))
    digest = section(unfenced(daily.splitlines()), DIGEST, level=2) if daily is not None else None
    text = "\n".join([*(digest or []), wrap or ""])
    missing = [i for i in expected if not _mentions(i, text)]
    details = tuple(f"missing: {i}" for i in missing) or (f"reported: {', '.join(expected)}",)
    return CheckResult("linear_delta", not missing, details)


def _channel_arg(args: object) -> str:
    if not isinstance(args, Mapping):
        return ""
    return str(args.get("channel_id") or args.get("channel") or "")


def red_poll(calls: Sequence[Mapping[str, Any]], expected: Sequence[str]) -> CheckResult:
    read = {
        _channel_arg(c.get("args"))
        for c in calls
        if c.get("persona") == "slack" and c.get("tool") == "slack_read_channel"
    }
    missing = [ch for ch in expected if ch not in read]
    details = tuple(f"not read: {ch}" for ch in missing) or (f"read: {', '.join(expected)}",)
    return CheckResult("red_poll", not missing, details)
