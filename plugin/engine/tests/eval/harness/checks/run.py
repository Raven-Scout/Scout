"""Checks over what a run said and did (spec 2026-10-12 §6.1; phases/core/00-run-modes.md:34-43,
phases/core/action-items.md:306-312).

- ``outage_reported``: a connector scripted as down is named as an outage in the wrap and the
  run notes, and the log shows an attempt (the claim must match the log).
- ``no_false_outage``: a connector that answered is never called down.
- ``dropoff_caught``: items silently missing from today's file are re-added with
  ``[carried-via-audit]``, the wrap says so, and ``review-queue.md`` gains an entry.

A name counts as an outage claim only in the same clause as an outage word, so
"Slack ✓, Calendar ✓, Linear ✗" calls only Linear down.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from scout.action_items.parser import ActionItem

from . import CheckResult

NAMES = {
    "slack": ("Slack",),
    "linear": ("Linear",),
    "calendar": ("Google Calendar", "Calendar"),
    "email": ("Gmail", "Email", "Mail"),
}
_OUTAGE = (
    r"unreachable|outage|down\b|unavailable|failed|failing|error|expired|auth|✗|could ?n[o']t|"
    r"not available|skipped|offline|no access"
)
_CLAUSE = r"[^.;,\n]"


def _claims_outage(persona: str, text: str | None) -> bool:
    if not text:
        return False
    for name in NAMES[persona]:
        n = re.escape(name)
        if re.search(rf"\b{n}\b{_CLAUSE}{{0,60}}?(?:{_OUTAGE})", text, re.IGNORECASE):
            return True
        if re.search(rf"(?:{_OUTAGE}){_CLAUSE}{{0,30}}?\b{n}\b", text, re.IGNORECASE):
            return True
    return False


def outage_reported(
    down: Sequence[str], wrap: str | None, run_notes: str | None, calls: Mapping[str, int]
) -> CheckResult:
    if not wrap:
        return CheckResult("outage_reported", False, ("no wrap message was sent",))
    problems = []
    for persona in down:
        if calls.get(persona, 0) == 0:
            problems.append(f"{persona}: declared down with no recorded call (attempt before declaring)")
        elif not _claims_outage(persona, wrap):
            problems.append(f"{persona}: not named as an outage in the wrap")
        elif not _claims_outage(persona, run_notes):
            problems.append(f"{persona}: not named as an outage in the run notes")
    return CheckResult("outage_reported", not problems, tuple(problems))


def no_false_outage(up: Sequence[str], wrap: str | None, run_notes: str | None) -> CheckResult:
    problems = [
        f"{p}: reported as an outage but it answered"
        for p in up
        if _claims_outage(p, wrap) or _claims_outage(p, run_notes)
    ]
    return CheckResult("no_false_outage", not problems, tuple(problems))


def dropoff_caught(
    missing: Sequence[str], today: Sequence[ActionItem] | None, wrap: str | None, *, review_added: str
) -> CheckResult:
    if today is None:
        return CheckResult("dropoff_caught", None, ("missing_output",))
    readded = {i.short_prefix for i in today if "[carried-via-audit]" in i.raw_line}
    problems = [f"{tag}: not re-added with [carried-via-audit]" for tag in missing if tag not in readded]
    if not wrap or "silently dropped" not in wrap.lower():
        problems.append("wrap doesn't say items were silently dropped")
    if not review_added.strip():
        problems.append("review-queue.md gained no entry")
    return CheckResult("dropoff_caught", not problems, tuple(problems))
