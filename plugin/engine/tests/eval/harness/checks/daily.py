"""Checks over the post-run daily file (spec 2026-10-12 §6.1; rules in phases/core/action-items.md).

- ``context_note``: every open item new today has ``action-items/context/<TAG>.md``
  with ``tag``, ``title``, ``created`` and ``updated`` frontmatter (#325).
- ``waiting_format``: a 🟢 waiting item says ``waiting on <who> since <date>``.
- ``no_collapse``: no open row summarises several items.
- ``body_budget``: the body after `` — `` is short (about 25 words; 40 fails).
- ``title_budget``: a title new or changed today is at most 80 characters.
- ``size_budget``: the file is within its hard limit, and no task new or changed
  today carries more than one prose sub-bullet (*The Backlog and the Budget*).
- ``backlog``: a backlog task with no news stays in the backlog, open, and is
  not copied into the daily file: ``fit`` places tasks, not the run.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from scout.action_items import stale
from scout.action_items.budget import Budget, measure, sub_bullet_groups
from scout.action_items.parser import ActionItem
from scout.action_items.titles import TITLE_MAX, title_of

from . import CheckResult, by_tag
from .placement import _find_by_ref

NOTE_KEYS = ("tag", "title", "created", "updated")
_WAITING = re.compile(r"waiting on \S.*? since \S+", re.IGNORECASE)
_COLLAPSE = re.compile(r"\betc\b\.?|\band \d+ more\b|\+\d+ more\b", re.IGNORECASE)
BODY_LIMIT, BODY_AIM = 40, 25


def body(item: ActionItem) -> str:
    return item.raw_line.split(" — ", 1)[1].strip() if " — " in item.raw_line else ""


TASK_SECTIONS = ("🔴", "🟡", "🟢", "🅿️")


def _open(item: ActionItem) -> bool:
    return item.status != "done" and not item.section.startswith("✅")


def _task_row(item: ActionItem) -> bool:
    """A tagged task in a tier section: not a meeting, digest or run-notes line, not a Focus copy."""
    return bool(item.short_prefix) and item.section.startswith(TASK_SECTIONS)


def _frontmatter_keys(text: str) -> set[str]:
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    return {ln.split(":", 1)[0].strip() for ln in m.group(1).splitlines() if ":" in ln} if m else set()


def context_note(
    today: Sequence[ActionItem] | None, yesterday: Sequence[ActionItem], notes: Mapping[str, str | None]
) -> CheckResult:
    if today is None:
        return CheckResult("context_note", None, ("missing_output",))
    known = set(by_tag(yesterday))
    new = sorted(tag for tag, item in by_tag(today).items() if _open(item) and tag not in known)
    problems = []
    for tag in new:
        text = notes.get(tag)
        if text is None:
            problems.append(f"{tag}: no context note")
            continue
        missing = [k for k in NOTE_KEYS if k not in _frontmatter_keys(text)]
        if missing:
            problems.append(f"{tag}: note missing frontmatter {', '.join(missing)}")
    return CheckResult("context_note", not problems, tuple(problems))


def waiting_format(today: Sequence[ActionItem] | None, expected: Sequence[str]) -> CheckResult:
    if today is None:
        return CheckResult("waiting_format", None, ("missing_output",))
    tags = by_tag(today)
    problems = []
    for key in expected:
        item = _find_by_ref(today, key[4:]) if key.startswith("ref:") else tags.get(key)
        if item is None:
            problems.append(f"{key}: missing")
        elif not _WAITING.search(body(item)):
            problems.append(f"{key}: body doesn't say 'waiting on <who> since <date>'")
    return CheckResult("waiting_format", not problems, tuple(problems))


def no_collapse(today: Sequence[ActionItem] | None) -> CheckResult:
    if today is None:
        return CheckResult("no_collapse", None, ("missing_output",))
    problems = [
        f"{i.short_prefix}: summarises several items"
        for i in today
        if _task_row(i) and _open(i) and _COLLAPSE.search(f"{i.title} {body(i)}")
    ]
    return CheckResult("no_collapse", not problems, tuple(problems))


def body_budget(today: Sequence[ActionItem] | None) -> CheckResult:
    if today is None:
        return CheckResult("body_budget", None, ("missing_output",))
    details, failed = [], False
    for i in (i for i in today if _task_row(i)):
        words = len(body(i).split())
        if words > BODY_LIMIT:
            details.append(f"{i.short_prefix}: body is {words} words (limit {BODY_LIMIT})")
            failed = True
        elif words > BODY_AIM:
            details.append(f"{i.short_prefix}: body is {words} words (aim for {BODY_AIM})")
    return CheckResult("body_budget", not failed, tuple(details))


def title_budget(today: Sequence[ActionItem] | None, yesterday: Sequence[ActionItem]) -> CheckResult:
    """A title new or changed today is at most ``TITLE_MAX`` characters (``action-items check-titles``).

    A title carried verbatim from yesterday is not the run's to answer for: the rule leaves those
    until a run touches the item, so a fixture's long carried title doesn't fail every run.
    """
    if today is None:
        return CheckResult("title_budget", None, ("missing_output",))
    before = {i.short_prefix: title_of(i.raw_line) for i in yesterday if i.short_prefix}
    problems = []
    for i in today:
        if not (_task_row(i) and _open(i)):
            continue
        title = title_of(i.raw_line)
        if len(title) > TITLE_MAX and before.get(i.short_prefix or "") != title:
            problems.append(f"{i.short_prefix}: title is {len(title)} characters (limit {TITLE_MAX})")
    return CheckResult("title_budget", not problems, tuple(problems))


def _blocks(text: str) -> dict[str, list[str]]:
    lines = text.splitlines()
    return {b.tag: lines[b.start : b.end] for b in stale.task_blocks(lines)}


def size_budget(today_text: str | None, yesterday_text: str | None, budget: Budget | None = None) -> CheckResult:
    """Past the hard limit fails, and so does a task this run wrote or changed with extra prose.

    A block carried verbatim from yesterday is ``fit``'s to trim, not the run's."""
    if today_text is None:
        return CheckResult("size_budget", None, ("missing_output",))
    budget = budget or Budget()
    report = measure(today_text, budget)
    problems: list[str] = []
    if report.level(budget) == "hard":
        problems += report.problems(budget)[:2]
    before = _blocks(yesterday_text or "")
    for tag, block in _blocks(today_text).items():
        prose = sum(is_prose for is_prose, _ in sub_bullet_groups(block))
        if prose > budget.prose_sub_bullets and before.get(tag) != block:
            problems.append(
                f"{tag}: {prose} prose sub-bullets (limit {budget.prose_sub_bullets}); context goes in its note"
            )
    return CheckResult("size_budget", not problems, tuple(problems))


def backlog(expected: Sequence[str], today: Sequence[ActionItem] | None, backlog_text: str | None) -> CheckResult:
    if today is None:
        return CheckResult("backlog", None, ("missing_output",))
    in_daily = {tag for tag, item in by_tag(today).items() if _open(item)}
    in_backlog = set(_blocks(backlog_text or ""))
    problems = []
    for tag in expected:
        if tag in in_daily:
            problems.append(f"{tag}: copied into the daily file with no news")
        if tag not in in_backlog:
            problems.append(f"{tag}: no longer open in the backlog")
    return CheckResult("backlog", not problems, tuple(problems))
