# Scout Engine Plan 3: `scoutctl action-items watch` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `scoutctl action-items watch [DATE_OR_PATH]` as an ID-aware diff stream over the action-items markdown file. The command's CLI contract is *"stream changes to today's action items as they happen"* — file-watcher mechanics are an implementation detail, not part of the surface (see v0.4 spec §13.3 "projection-consumer contracts"). Output is one human-readable line per change (`[HH:MM:SS] ✓ completed [#A3F7] Submit Lever feedback`); rich color when stdout is a TTY, plain when redirected. Plan 3 ships when CI is green and the smoke test (write a daily file, start watch in a subprocess, mark a task done from another shell, observe the diff line) passes.

**Architecture:** Three modules, each tested in isolation.

1. `scout.action_items.diff` — pure function `diff(prev, curr) → list[ChangeEvent]`. Matches items across snapshots by `short_prefix` first (the §13 stable-ID surface form), falls back to `(title, section)` for legacy unprefixed lines. Emits add / remove / completed / reopened / title_changed events.
2. `scout.action_items.render` (extended — Plan 2's render module gains a `render_changes()` function alongside its existing HTML renderer) — formats `ChangeEvent` lists into TTY-aware strings via Rich. This is the *only* output shape; v0.5's event-store subscriber will reuse it.
3. `scout.action_items.watch` — the subcommand body. Resolves target file, opens it once to seed `prev_state`, registers a `watchdog.observers.Observer` for the file's parent directory, and on each `FileModifiedEvent` re-parses, diffs, renders, prints. Runs until SIGINT. Heavy imports (`watchdog`, `rich`) live inside the function body per spec §4 startup latency rules.

**Tech Stack:** Python 3.11+, Typer, watchdog (already in `pyproject.toml`), rich (already in `pyproject.toml`), pytest. Depends on Plan 2 + Plan 2 supplement having merged (parser exposes `ActionItem.short_prefix`; mutators are ID-aware).

**Position in plan sequence:** Plan 3. Originally bundled with hook/script ports in Plan 2's preamble, but those have been split into their own plan (Plan 4) so Plan 3 stays focused. Plan 4 will cover `hooks/*.sh` and `scripts/*.sh` ports plus flipping `session_tokens_v1` and `connector_health_v1` manifest flags.

---

## Context for the implementer

**Working directory:** `/Users/jordanburger/scout-plugin/`. New branch off the merged Plan 2 + supplement tip:

```bash
cd ~/scout-plugin
git checkout main
git pull --ff-only
git checkout -b plan-3-action-items-watch
.venv/bin/pytest tests/ -q              # green
```

**Reference docs:**
- `/Users/jordanburger/scout-app/docs/superpowers/specs/2026-04-24-scout-unification-design.md` §13.3 — projection-consumer contract.
- `/Users/jordanburger/scout-app/docs/superpowers/specs/2026-04-25-scout-event-architecture-design.md` — v0.5 substitution target.
- `/Users/jordanburger/scout-app/docs/superpowers/plans/2026-04-26-scout-unification-plan-2-supplement-stable-ids-and-events.md` — defines `ActionItem.short_prefix`, `Event`, `IdMap`. Read before starting.

**What this plan does NOT do:**
- Render HTML. The legacy `~/Scout/action-items/watch.sh` re-rendered an HTML dashboard on every change. The user does not use it; v0.4 retires the auto-render. `scout.action_items.render.render_html` (the existing HTML renderer Plan 2 ports) stays available as `scoutctl action-items render` but is no longer wired to file changes.
- Replace `scoutctl tui`. The TUI from Plan 2 is the multi-screen interactive dashboard; `watch` is a one-line-per-change stream for terminal piping and ambient awareness.
- Touch the v0.5 event store. The watcher is the file-watcher implementation of the projection-consumer contract; v0.5 substitutes an event-store subscriber under the same CLI surface.

## File structure

```
~/scout-plugin/engine/
├── scout/
│   └── action_items/
│       ├── diff.py                   NEW — Task 1
│       ├── render.py                 MODIFIED — Task 2 (adds render_changes)
│       ├── watch.py                  NEW — Task 3
│       └── cli.py                    MODIFIED — Task 4 (replaces stub)
├── tests/
│   ├── unit/
│   │   ├── test_action_items_diff.py        NEW — Task 1
│   │   └── test_action_items_render_changes.py  NEW — Task 2
│   ├── integration/
│   │   └── test_action_items_watch.py       NEW — Task 5
│   └── perf/
│       └── test_no_heavy_imports.py         MODIFIED — Task 3 (whitelist watch)
```

---

## Task 1: `scout.action_items.diff` — pure diff over `ActionItem` snapshots

**Files:**
- Create: `~/scout-plugin/engine/scout/action_items/diff.py`
- Create: `~/scout-plugin/engine/tests/unit/test_action_items_diff.py`

**What this builds:** A `ChangeEvent` dataclass and a `diff(prev, curr)` function that takes two ordered lists of `ActionItem` (typically from successive parses of the same daily file) and emits the deltas. Matching strategy: short-prefix first, fall back to `(section, title)` tuple.

- [ ] **Step 1: Write failing tests**

Create `engine/tests/unit/test_action_items_diff.py`:

```python
"""Unit tests for scout.action_items.diff."""

from __future__ import annotations

import pytest

from scout.action_items.diff import ChangeEvent, diff
from scout.action_items.parser import ActionItem


def _item(
    *,
    title: str,
    short_prefix: str | None = None,
    status: str = "open",
    section: str = "In Progress",
    priority: str = "",
) -> ActionItem:
    return ActionItem(
        priority=priority,
        title=title,
        status=status,
        section=section,
        context_links=[],
        notes=[],
        details=[],
        raw_line=f"- [{'x' if status == 'done' else ' '}] {('[#' + short_prefix + '] ') if short_prefix else ''}{title}",
        short_prefix=short_prefix,
    )


def test_no_changes_yields_empty_list() -> None:
    items = [_item(title="task A", short_prefix="A3F7")]
    assert diff(prev=items, curr=items) == []


def test_added_item_emits_added_event() -> None:
    prev: list[ActionItem] = []
    curr = [_item(title="task A", short_prefix="A3F7")]
    events = diff(prev=prev, curr=curr)
    assert len(events) == 1
    assert events[0].kind == "added"
    assert events[0].item_id == "A3F7"
    assert events[0].title == "task A"


def test_removed_item_emits_removed_event() -> None:
    prev = [_item(title="task A", short_prefix="A3F7")]
    curr: list[ActionItem] = []
    events = diff(prev=prev, curr=curr)
    assert len(events) == 1
    assert events[0].kind == "removed"
    assert events[0].item_id == "A3F7"


def test_status_open_to_done_emits_completed() -> None:
    prev = [_item(title="task A", short_prefix="A3F7", status="open")]
    curr = [_item(title="task A", short_prefix="A3F7", status="done")]
    events = diff(prev=prev, curr=curr)
    assert len(events) == 1
    assert events[0].kind == "completed"
    assert events[0].item_id == "A3F7"


def test_status_done_to_open_emits_reopened() -> None:
    prev = [_item(title="task A", short_prefix="A3F7", status="done")]
    curr = [_item(title="task A", short_prefix="A3F7", status="open")]
    events = diff(prev=prev, curr=curr)
    assert events[0].kind == "reopened"


def test_title_changed_emits_title_changed_event() -> None:
    prev = [_item(title="old title", short_prefix="A3F7")]
    curr = [_item(title="new title", short_prefix="A3F7")]
    events = diff(prev=prev, curr=curr)
    assert len(events) == 1
    assert events[0].kind == "title_changed"
    assert events[0].item_id == "A3F7"
    assert events[0].extras == {"old_title": "old title", "new_title": "new title"}


def test_match_falls_back_to_section_and_title_for_unprefixed_lines() -> None:
    """Legacy lines without [#XXXX] match by (section, title) tuple."""
    prev = [_item(title="legacy task", short_prefix=None, status="open")]
    curr = [_item(title="legacy task", short_prefix=None, status="done")]
    events = diff(prev=prev, curr=curr)
    assert events[0].kind == "completed"
    assert events[0].item_id == ""  # no prefix → empty id


def test_unprefixed_line_in_different_sections_treated_as_separate() -> None:
    prev = [_item(title="dup", short_prefix=None, section="In Progress")]
    curr = [_item(title="dup", short_prefix=None, section="To Do")]
    # Same title, different sections → first removed, second added.
    events = diff(prev=prev, curr=curr)
    kinds = {e.kind for e in events}
    assert kinds == {"removed", "added"}


def test_multiple_changes_emitted_in_input_order() -> None:
    prev = [
        _item(title="A", short_prefix="AAAA", status="open"),
        _item(title="B", short_prefix="BBBB", status="open"),
    ]
    curr = [
        _item(title="A", short_prefix="AAAA", status="done"),  # completed
        _item(title="B", short_prefix="BBBB", status="open"),  # unchanged
        _item(title="C", short_prefix="CCCC", status="open"),  # added
    ]
    events = diff(prev=prev, curr=curr)
    assert len(events) == 2
    assert events[0].kind == "completed" and events[0].item_id == "AAAA"
    assert events[1].kind == "added" and events[1].item_id == "CCCC"


def test_prefix_match_wins_over_title_match() -> None:
    """If prefixes match but titles differ, that's a title_changed — not remove+add."""
    prev = [_item(title="original", short_prefix="A3F7")]
    curr = [_item(title="renamed", short_prefix="A3F7")]
    events = diff(prev=prev, curr=curr)
    assert len(events) == 1
    assert events[0].kind == "title_changed"


def test_change_event_has_section_for_display() -> None:
    """Renderers need the section for context — verify it survives diffing."""
    prev: list[ActionItem] = []
    curr = [_item(title="task", short_prefix="A3F7", section="To Do")]
    events = diff(prev=prev, curr=curr)
    assert events[0].section == "To Do"
```

- [ ] **Step 2: Run, confirm RED**

```bash
cd ~/scout-plugin/engine
.venv/bin/pytest tests/unit/test_action_items_diff.py -v
```

Expected: `ModuleNotFoundError: No module named 'scout.action_items.diff'`.

- [ ] **Step 3: Implement `scout/action_items/diff.py`**

```python
"""Pure diff over ActionItem snapshots.

Returns a list of ChangeEvent records describing what changed between
two parses of the same action-items file. Designed to be the v0.5
event-store subscriber's projection target as well — same shape,
different source.

Match priority:
1. By short_prefix (the [#XXXX] surface form from §13.1) — only matches
   when *both* prev and curr items carry the same prefix.
2. By (section, title) tuple — fallback for legacy unprefixed lines.

This means a line that gains a prefix in curr is not matched to its
unprefixed prev — it appears as a `removed` (the unprefixed version
disappeared) plus an `added` (the prefixed version appeared). That's
intentional: the watcher's `prev_state` will be re-seeded on the next
diff cycle, and the v0.5 event store will see the assignment as a
distinct event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scout.action_items.parser import ActionItem


@dataclass(frozen=True)
class ChangeEvent:
    kind: str          # "added" | "removed" | "completed" | "reopened" | "title_changed"
    item_id: str       # short_prefix, or "" for unprefixed lines
    title: str         # display title (curr title for title_changed)
    section: str       # display section
    extras: dict[str, Any] = field(default_factory=dict)


def _index_by_prefix(items: list[ActionItem]) -> dict[str, ActionItem]:
    return {i.short_prefix: i for i in items if i.short_prefix}


def _index_by_section_title(items: list[ActionItem]) -> dict[tuple[str, str], ActionItem]:
    # Skip items that have a prefix — those match by prefix path only.
    return {
        (i.section, i.title): i for i in items if not i.short_prefix
    }


def diff(*, prev: list[ActionItem], curr: list[ActionItem]) -> list[ChangeEvent]:
    """Return the list of changes from `prev` to `curr`."""
    events: list[ChangeEvent] = []

    prev_by_prefix = _index_by_prefix(prev)
    curr_by_prefix = _index_by_prefix(curr)
    prev_by_st = _index_by_section_title(prev)
    curr_by_st = _index_by_section_title(curr)

    matched_prev_prefix: set[str] = set()
    matched_prev_st: set[tuple[str, str]] = set()

    # Walk curr in input order so the emitted events are in display order.
    for item in curr:
        if item.short_prefix:
            prev_match = prev_by_prefix.get(item.short_prefix)
            if prev_match is not None:
                matched_prev_prefix.add(item.short_prefix)
                events.extend(_compare(prev_match, item))
                continue
            # New prefix in curr that wasn't in prev → "added".
            events.append(
                ChangeEvent(
                    kind="added",
                    item_id=item.short_prefix,
                    title=item.title,
                    section=item.section,
                )
            )
            continue

        # Unprefixed line: match by (section, title).
        key = (item.section, item.title)
        prev_match = prev_by_st.get(key)
        if prev_match is not None:
            matched_prev_st.add(key)
            events.extend(_compare(prev_match, item))
            continue

        events.append(
            ChangeEvent(
                kind="added",
                item_id="",
                title=item.title,
                section=item.section,
            )
        )

    # Anything in prev that wasn't matched is removed.
    for prefix, item in prev_by_prefix.items():
        if prefix not in matched_prev_prefix:
            events.append(
                ChangeEvent(
                    kind="removed",
                    item_id=prefix,
                    title=item.title,
                    section=item.section,
                )
            )
    for key, item in prev_by_st.items():
        if key not in matched_prev_st:
            events.append(
                ChangeEvent(
                    kind="removed",
                    item_id="",
                    title=item.title,
                    section=item.section,
                )
            )

    return events


def _compare(prev: ActionItem, curr: ActionItem) -> list[ChangeEvent]:
    """Compare two matched items; emit zero or more events."""
    out: list[ChangeEvent] = []
    item_id = curr.short_prefix or ""
    if prev.status != curr.status:
        kind = "completed" if curr.status == "done" else "reopened"
        out.append(
            ChangeEvent(
                kind=kind,
                item_id=item_id,
                title=curr.title,
                section=curr.section,
            )
        )
    if prev.title != curr.title:
        out.append(
            ChangeEvent(
                kind="title_changed",
                item_id=item_id,
                title=curr.title,
                section=curr.section,
                extras={"old_title": prev.title, "new_title": curr.title},
            )
        )
    return out
```

- [ ] **Step 4: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_action_items_diff.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/diff.py engine/tests/unit/test_action_items_diff.py
git commit -m "feat(engine): action_items.diff — ChangeEvent + ID-aware diff over snapshots"
```

---

## Task 2: Add `render_changes` to `scout.action_items.render`

**Files:**
- Modify: `~/scout-plugin/engine/scout/action_items/render.py`
- Create: `~/scout-plugin/engine/tests/unit/test_action_items_render_changes.py`

**What this builds:** A `render_changes(events: list[ChangeEvent], *, now: dt.datetime, color: bool) -> list[str]` function that formats each `ChangeEvent` as a single line of output. Examples:

```
[14:32:15] ✓ completed [#A3F7] Submit Lever feedback (In Progress)
[14:32:18] + added     [#B5K2] Reply to Q2 budget thread (To Do)
[14:32:22] - removed   [#C9N4] Followup with vendor (To Do)
[14:32:30] ✎ renamed   [#A3F7] "old title" → "new title"
```

When `color=True`, additions/completions go green, removals red, renames yellow. When `color=False`, output is plain text suitable for piping or log capture.

- [ ] **Step 1: Write failing tests**

Create `engine/tests/unit/test_action_items_render_changes.py`:

```python
"""Unit tests for scout.action_items.render.render_changes."""

from __future__ import annotations

import datetime as dt

from scout.action_items.diff import ChangeEvent
from scout.action_items.render import render_changes


_NOW = dt.datetime(2026, 4, 26, 14, 32, 15)


def test_added_line_format() -> None:
    e = ChangeEvent(kind="added", item_id="A3F7", title="task A", section="To Do")
    lines = render_changes([e], now=_NOW, color=False)
    assert len(lines) == 1
    assert lines[0] == "[14:32:15] + added     [#A3F7] task A (To Do)"


def test_removed_line_format() -> None:
    e = ChangeEvent(kind="removed", item_id="A3F7", title="task A", section="To Do")
    lines = render_changes([e], now=_NOW, color=False)
    assert lines[0] == "[14:32:15] - removed   [#A3F7] task A (To Do)"


def test_completed_line_format() -> None:
    e = ChangeEvent(kind="completed", item_id="A3F7", title="task A", section="In Progress")
    lines = render_changes([e], now=_NOW, color=False)
    assert lines[0] == "[14:32:15] ✓ completed [#A3F7] task A (In Progress)"


def test_reopened_line_format() -> None:
    e = ChangeEvent(kind="reopened", item_id="A3F7", title="task A", section="In Progress")
    lines = render_changes([e], now=_NOW, color=False)
    assert lines[0] == "[14:32:15] ↻ reopened  [#A3F7] task A (In Progress)"


def test_title_changed_line_format() -> None:
    e = ChangeEvent(
        kind="title_changed",
        item_id="A3F7",
        title="new title",
        section="In Progress",
        extras={"old_title": "old title", "new_title": "new title"},
    )
    lines = render_changes([e], now=_NOW, color=False)
    assert lines[0] == '[14:32:15] ✎ renamed   [#A3F7] "old title" → "new title"'


def test_unprefixed_item_id_omits_brackets() -> None:
    e = ChangeEvent(kind="added", item_id="", title="legacy task", section="To Do")
    lines = render_changes([e], now=_NOW, color=False)
    assert lines[0] == "[14:32:15] + added     legacy task (To Do)"


def test_color_output_includes_ansi_codes() -> None:
    e = ChangeEvent(kind="completed", item_id="A3F7", title="task", section="In Progress")
    lines = render_changes([e], now=_NOW, color=True)
    # Rich emits ANSI escapes for colored text. We don't pin the exact codes
    # but assert the line contains an ANSI escape introducer.
    assert "\x1b[" in lines[0]


def test_multiple_events_render_in_order() -> None:
    events = [
        ChangeEvent(kind="completed", item_id="A3F7", title="A", section="In Progress"),
        ChangeEvent(kind="added", item_id="B5K2", title="B", section="To Do"),
    ]
    lines = render_changes(events, now=_NOW, color=False)
    assert len(lines) == 2
    assert "completed" in lines[0]
    assert "added" in lines[1]


def test_empty_events_yields_empty_list() -> None:
    assert render_changes([], now=_NOW, color=False) == []
```

- [ ] **Step 2: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_action_items_render_changes.py -v
```

Expected: `ImportError: cannot import name 'render_changes'`.

- [ ] **Step 3: Implement `render_changes` in `scout/action_items/render.py`**

Append to the existing `render.py` (which Plan 2 created for HTML rendering — this adds a sibling function):

```python
# Append at the bottom of engine/scout/action_items/render.py


def render_changes(
    events: "list[ChangeEvent]",
    *,
    now: "dt.datetime",
    color: bool,
) -> list[str]:
    """Format ChangeEvent records as one line each.

    Returns a list of strings; caller is responsible for emitting them
    (typically `for line in lines: print(line)`).

    `color=True` injects ANSI escapes via Rich. `color=False` is plain
    text — use this when stdout is not a TTY or the user passed `--no-color`.
    """
    # Local imports keep `render_html` and the diff-render function from
    # forcing rich/datetime onto callers that don't need them.
    import datetime as _dt  # noqa: F401  — already imported at top, kept local for clarity
    from io import StringIO

    from rich.console import Console
    from rich.text import Text

    from scout.action_items.diff import ChangeEvent  # noqa: F401

    out: list[str] = []
    ts = now.strftime("[%H:%M:%S]")
    # 9-char-wide kind column so all rows align.
    KIND_WIDTH = 9
    for ev in events:
        symbol, kind_label, style = _SYMBOL_STYLE.get(
            ev.kind, ("?", ev.kind, "white")
        )
        prefix_part = f"[#{ev.item_id}] " if ev.item_id else ""
        if ev.kind == "title_changed":
            body = f'{prefix_part}"{ev.extras["old_title"]}" → "{ev.extras["new_title"]}"'
        else:
            body = f"{prefix_part}{ev.title} ({ev.section})"
        plain = f"{ts} {symbol} {kind_label.ljust(KIND_WIDTH)} {body}"
        if not color:
            out.append(plain)
            continue
        # Use a temporary Console capturing into a StringIO so we get
        # ANSI escapes regardless of the surrounding stdout's TTY status.
        buf = StringIO()
        console = Console(
            file=buf, force_terminal=True, color_system="truecolor", legacy_windows=False
        )
        console.print(
            Text(f"{ts} ", style="dim")
            + Text(symbol + " ", style=style)
            + Text(kind_label.ljust(KIND_WIDTH), style=style)
            + Text(" " + body),
            end="",
        )
        out.append(buf.getvalue())
    return out


# Symbol + label + Rich style per kind.
# Defined here (module level) so render_changes doesn't rebuild it per call.
_SYMBOL_STYLE: dict[str, tuple[str, str, str]] = {
    "added":         ("+", "added",     "green"),
    "removed":       ("-", "removed",   "red"),
    "completed":     ("✓", "completed", "green"),
    "reopened":      ("↻", "reopened",  "yellow"),
    "title_changed": ("✎", "renamed",   "yellow"),
}
```

If `render.py` doesn't yet import `datetime as dt`, add it at the top.

- [ ] **Step 4: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_action_items_render_changes.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/render.py engine/tests/unit/test_action_items_render_changes.py
git commit -m "feat(engine): render_changes — TTY-aware one-line-per-change formatter"
```

---

## Task 3: `scout.action_items.watch` — file-watcher driver

**Files:**
- Create: `~/scout-plugin/engine/scout/action_items/watch.py`
- Modify: `~/scout-plugin/engine/tests/perf/test_no_heavy_imports.py`

**What this builds:** The watch module's pure core (`process_change(prev_text, curr_text, now, color)`) is testable without spawning a real watcher; the `run_watch_loop(target, color)` function wraps it in a `watchdog.observers.Observer`. The Typer command (Task 4) calls `run_watch_loop` directly.

`watchdog` and `rich` must NOT be imported at module top of `scout.action_items.watch` (per spec §4 startup latency budget). Imports go inside function bodies.

- [ ] **Step 1: Whitelist `scout.action_items.watch` in the import-discipline test**

Edit `engine/tests/perf/test_no_heavy_imports.py` to add `scout.action_items.watch` to the allowlist of files that may import `watchdog` and `rich` inside function bodies (the test's design intention). The exact mechanism depends on Plan 1's implementation: typically there's an `_ALLOWED_FILES_FOR_HEAVY_LAZY_IMPORT` set that the AST walker checks. Add `"scout/action_items/watch.py"` to that set, or equivalent.

If the test's logic only checks top-level imports, no change is needed — function-body imports are already permitted. Verify by inspection.

- [ ] **Step 2: Write failing tests for `process_change`**

Create `engine/tests/unit/test_action_items_watch.py`:

```python
"""Unit tests for the pure core of scout.action_items.watch.

The watchdog wiring is tested in tests/integration/test_action_items_watch.py.
"""

from __future__ import annotations

import datetime as dt

from scout.action_items.watch import process_change


_NOW = dt.datetime(2026, 4, 26, 14, 32, 15)


def test_process_change_emits_no_lines_when_text_identical() -> None:
    text = (
        "## In Progress\n\n"
        "- [ ] [#A3F7] task A\n"
    )
    lines = process_change(prev_text=text, curr_text=text, now=_NOW, color=False)
    assert lines == []


def test_process_change_emits_completed_line_when_checkbox_flipped() -> None:
    prev = "## In Progress\n\n- [ ] [#A3F7] task A\n"
    curr = "## In Progress\n\n- [x] [#A3F7] task A\n"
    lines = process_change(prev_text=prev, curr_text=curr, now=_NOW, color=False)
    assert len(lines) == 1
    assert "completed" in lines[0]
    assert "[#A3F7]" in lines[0]


def test_process_change_emits_added_line_when_new_item_appears() -> None:
    prev = "## In Progress\n\n- [ ] [#A3F7] task A\n"
    curr = (
        "## In Progress\n\n"
        "- [ ] [#A3F7] task A\n"
        "- [ ] [#B5K2] task B\n"
    )
    lines = process_change(prev_text=prev, curr_text=curr, now=_NOW, color=False)
    assert len(lines) == 1
    assert "added" in lines[0]
    assert "[#B5K2]" in lines[0]


def test_process_change_handles_unparseable_prev_gracefully() -> None:
    """Initial seed (empty file) shouldn't crash the watcher."""
    lines = process_change(
        prev_text="", curr_text="- [ ] [#A3F7] task\n", now=_NOW, color=False
    )
    # Single added event for the new task.
    assert len(lines) == 1
    assert "added" in lines[0]
```

- [ ] **Step 3: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_action_items_watch.py -v
```

Expected: `ModuleNotFoundError: No module named 'scout.action_items.watch'`.

- [ ] **Step 4: Implement `scout/action_items/watch.py`**

```python
"""scoutctl action-items watch — projection-consumer over today's action items.

Public CLI contract per spec §13.3: this command *streams changes to
today's action items as they happen*. The v0.4 implementation watches
the underlying markdown file via `watchdog`; v0.5 will substitute an
event-store subscriber. The CLI surface and stdout shape are stable.

Heavy imports (watchdog, rich) live inside function bodies so
`scoutctl --help` and other subcommands stay under the latency budget
(spec §4).
"""

from __future__ import annotations

import datetime as dt
import sys
from io import StringIO
from pathlib import Path

from scout.action_items.diff import diff
from scout.action_items.parser import ActionItem
from scout.action_items.render import render_changes


def process_change(
    *,
    prev_text: str,
    curr_text: str,
    now: dt.datetime,
    color: bool,
) -> list[str]:
    """Pure core of the watcher: text → text → list of formatted lines.

    Used directly by tests; called from `_handle_modified_event` in the
    real watcher loop.
    """
    prev_items = _parse_text(prev_text)
    curr_items = _parse_text(curr_text)
    events = diff(prev=prev_items, curr=curr_items)
    return render_changes(events, now=now, color=color)


def _parse_text(text: str) -> list[ActionItem]:
    """Run the parser over an in-memory string by writing to a tempfile."""
    # parser.parse_file expects a Path. The watcher always has a real
    # path; this helper exists so process_change() can be tested with
    # arbitrary strings without hitting the real filesystem.
    import tempfile

    from scout.action_items.parser import parse_file

    if not text:
        return []
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(text)
        tmp = Path(f.name)
    try:
        return parse_file(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def run_watch_loop(target: Path, *, color: bool) -> None:
    """Block until SIGINT, emitting one line per detected change.

    Heavy imports inside the body — `scoutctl action-items watch` is
    interactive (a long-running process), so the cost is paid once.
    """
    from watchdog.events import FileModifiedEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    if not target.exists():
        raise FileNotFoundError(target)

    state = {"prev_text": target.read_text(encoding="utf-8")}

    def on_modified(event: FileModifiedEvent) -> None:
        if Path(event.src_path) != target:
            return
        try:
            curr_text = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return  # mid-rename; the next event will deliver the new contents
        if curr_text == state["prev_text"]:
            return
        lines = process_change(
            prev_text=state["prev_text"],
            curr_text=curr_text,
            now=dt.datetime.now(),
            color=color,
        )
        for line in lines:
            print(line, flush=True)
        state["prev_text"] = curr_text

    class _Handler(FileSystemEventHandler):
        def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
            on_modified(event)

    observer = Observer()
    observer.schedule(_Handler(), str(target.parent), recursive=False)
    observer.start()
    print(
        f"Watching {target.name} for changes — Ctrl-C to stop.",
        file=sys.stderr,
    )
    try:
        observer.join()
    except KeyboardInterrupt:
        observer.stop()
        observer.join()
```

- [ ] **Step 5: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_action_items_watch.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Verify import discipline**

```bash
.venv/bin/pytest tests/perf/test_no_heavy_imports.py -v
```

Expected: pass. The test should detect that `scout.action_items.watch` does not import `watchdog` or `rich` at module top.

If it fails, the most likely cause is a stray top-level import — re-check `watch.py` and move any heavy imports inside `run_watch_loop` or `_parse_text`.

- [ ] **Step 7: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 8: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/watch.py engine/tests/unit/test_action_items_watch.py engine/tests/perf/test_no_heavy_imports.py
git commit -m "feat(engine): action_items.watch — process_change + run_watch_loop"
```

---

## Task 4: Wire `scoutctl action-items watch` in the Typer sub-app

**Files:**
- Modify: `~/scout-plugin/engine/scout/action_items/cli.py`

**What this builds:** Replace Plan 2's stub `cli_watch` (which raises `ScoutError("watch is implemented in Plan 3")`) with the real command. CLI shape:

```
scoutctl action-items watch [DATE_OR_PATH] [--no-color]
```

Where `DATE_OR_PATH` is optional and accepts:
- Omitted → today's daily file under `$SCOUT_DATA_DIR/action-items/`.
- `YYYY-MM-DD` → that day's daily file.
- Anything else → treated as a path; if it exists, watched directly.

Help text: *"Stream changes to today's action items as they happen."* (Note: stream of changes, not file watching — projection-consumer contract.)

- [ ] **Step 1: Replace the stub command**

Edit `engine/scout/action_items/cli.py`. Find the existing `cli_watch` stub:

```python
@app.command("watch")
def cli_watch() -> None:
    raise ScoutError("scoutctl action-items watch is implemented in Plan 3")
```

Replace with:

```python
@app.command("watch")
def cli_watch(
    target: str = typer.Argument(
        None,
        metavar="[DATE_OR_PATH]",
        help="YYYY-MM-DD for that day's file, an explicit path, or omit for today.",
    ),
    no_color: bool = typer.Option(
        False, "--no-color", help="Disable ANSI color (auto when stdout is not a TTY)."
    ),
) -> None:
    """Stream changes to today's action items as they happen."""
    import datetime as dt
    import re
    import sys
    from pathlib import Path

    from scout import paths
    from scout.action_items.watch import run_watch_loop
    from scout.errors import ActionItemError

    if target is None:
        target_path = paths.action_items_daily_path()
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", target):
        target_path = paths.action_items_daily_path(
            date=dt.date.fromisoformat(target)
        )
    else:
        target_path = Path(target).expanduser().resolve()

    if not target_path.exists():
        raise ActionItemError(f"target does not exist: {target_path}")

    color = not no_color and sys.stdout.isatty()
    run_watch_loop(target_path, color=color)
```

Imports inside the function body, not at module top — keeps `scoutctl` startup latency under budget when the user doesn't invoke `watch`.

- [ ] **Step 2: Update the per-subapp test for the stub**

The Plan 2 test `test_action_items_watch_returns_scout_error_exit_code` (in `engine/tests/unit/test_action_items_cli.py` per Plan 2 Task 9) should be retired — `watch` is no longer a stub. Replace it with two tests:

```python
def test_cli_watch_help_text_is_projection_consumer_contract() -> None:
    """Per spec §13.3, watch's help text describes a stream of changes,
    not a file-watcher. This test pins that wording."""
    from typer.testing import CliRunner

    from scout.action_items.cli import app

    result = CliRunner().invoke(app, ["watch", "--help"])
    assert result.exit_code == 0
    assert "stream" in result.stdout.lower()
    assert "changes" in result.stdout.lower()


def test_cli_watch_rejects_missing_target(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    from typer.testing import CliRunner

    from scout.action_items.cli import app

    # No daily file exists in tmp_path/action-items/, and we pass no target.
    result = CliRunner().invoke(app, ["watch"], catch_exceptions=False, mix_stderr=True)
    assert result.exit_code != 0
    # CliRunner with mix_stderr=True puts both streams into result.output.
    assert "does not exist" in result.output.lower()
```

- [ ] **Step 3: Run unit tests**

```bash
.venv/bin/pytest tests/unit/test_action_items_cli.py tests/unit/test_action_items_watch.py -v
```

Expected: all pass.

- [ ] **Step 4: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 5: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/cli.py engine/tests/unit/test_action_items_cli.py
git commit -m "feat(engine): wire scoutctl action-items watch (replaces Plan 2 stub)"
```

---

## Task 5: End-to-end integration test

**Files:**
- Create: `~/scout-plugin/engine/tests/integration/test_action_items_watch.py`

**What this builds:** Exercises the full path: spawn `scoutctl action-items watch <path>` as a subprocess, mutate the watched file, observe a single diff line on stdout. This is the smoke test that catches regressions in the watchdog wiring.

- [ ] **Step 1: Write the integration test**

Create `engine/tests/integration/test_action_items_watch.py`:

```python
"""Integration test for scoutctl action-items watch.

Spawns the CLI as a subprocess, mutates the watched file, asserts a
diff line is printed within a small timeout. Marked `slow` so default
unit-test runs skip it; CI runs it explicitly.
"""

from __future__ import annotations

import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


def _scoutctl_path() -> str:
    """Resolve the scoutctl entry point inside the engine venv."""
    venv_bin = Path(__file__).parent.parent.parent / ".venv" / "bin"
    candidate = venv_bin / "scoutctl"
    if candidate.exists():
        return str(candidate)
    return "scoutctl"  # fall back to PATH lookup


def _read_until(proc: subprocess.Popen[str], substring: str, timeout: float) -> str:
    """Read stdout until `substring` appears or `timeout` elapses.

    Returns accumulated stdout. Uses `select` so a wedged subprocess
    doesn't hang the test.
    """
    deadline = time.monotonic() + timeout
    buf: list[str] = []
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], 0.2)
        if proc.stdout in ready:
            line = proc.stdout.readline()
            if not line:
                break
            buf.append(line)
            if substring in line:
                return "".join(buf)
    return "".join(buf)


@pytest.mark.timeout(20)
def test_watch_emits_completed_line_on_checkbox_flip(tmp_path: Path) -> None:
    daily = tmp_path / "action-items-2026-04-26.md"
    daily.write_text(
        "## In Progress\n\n"
        "- [ ] [#A3F7] Submit Lever feedback\n"
    )

    env = {**os.environ, "NO_COLOR": "1"}  # ensure plain output even on TTY-emulating CI
    proc = subprocess.Popen(
        [_scoutctl_path(), "action-items", "watch", str(daily), "--no-color"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    try:
        # Give the watcher ~500ms to register its filesystem hook.
        time.sleep(0.5)

        # Mutate the file: flip the checkbox.
        daily.write_text(
            "## In Progress\n\n"
            "- [x] [#A3F7] Submit Lever feedback\n"
        )

        out = _read_until(proc, "completed", timeout=10.0)
        assert "completed" in out
        assert "[#A3F7]" in out
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
```

- [ ] **Step 2: Run the test**

```bash
cd ~/scout-plugin/engine
.venv/bin/pytest tests/integration/test_action_items_watch.py -v -m slow
```

Expected: 1 passed (in 1-3 seconds).

If it times out, debug:
- Confirm `scoutctl` is on PATH inside the venv (`ls .venv/bin/scoutctl`).
- Run the command manually in another terminal: `.venv/bin/scoutctl action-items watch /path/to/file.md --no-color`, then `echo "..." > file.md` and observe.

- [ ] **Step 3: Lint**

```bash
.venv/bin/ruff check tests && .venv/bin/ruff format --check tests
```

- [ ] **Step 4: Commit**

```bash
cd ~/scout-plugin
git add engine/tests/integration/test_action_items_watch.py
git commit -m "test(engine): integration test for scoutctl action-items watch"
```

---

## Task 6: Final verification + push + open PR

- [ ] **Step 1: Full unit + integration suite**

```bash
cd ~/scout-plugin/engine
.venv/bin/pytest tests/ -v
.venv/bin/pytest tests/integration/ -v -m slow
```

Expected: all pass.

- [ ] **Step 2: Lint + type check**

```bash
.venv/bin/ruff check scout tests
.venv/bin/ruff format --check scout tests
.venv/bin/mypy scout
```

Expected: clean.

- [ ] **Step 3: Manual smoke test against a real data dir**

```bash
export SCOUT_DATA_DIR=$(mktemp -d)
mkdir -p "$SCOUT_DATA_DIR/action-items"
TODAY=$(date '+%Y-%m-%d')
cat > "$SCOUT_DATA_DIR/action-items/action-items-${TODAY}.md" <<EOF
## In Progress

- [ ] [#A3F7] 🔴 Test watch
EOF

# Terminal A:
.venv/bin/scoutctl action-items watch

# Terminal B (separate shell):
export SCOUT_DATA_DIR=<paste from terminal A>
echo "## In Progress

- [x] [#A3F7] 🔴 Test watch" > "$SCOUT_DATA_DIR/action-items/action-items-${TODAY}.md"

# Terminal A should print:
# [HH:MM:SS] ✓ completed [#A3F7] Test watch (In Progress)

# Ctrl-C in Terminal A to exit.
```

Expected: a green `completed` line in Terminal A within ~1s of the file write in Terminal B.

- [ ] **Step 4: Push branch + open PR**

```bash
cd ~/scout-plugin
git push -u origin plan-3-action-items-watch
gh pr create \
    --title "feat(engine): Plan 3 — scoutctl action-items watch" \
    --body "$(cat <<'EOF'
## Summary

Implements `scoutctl action-items watch` per Plan 3.

- New `scout.action_items.diff` — pure ID-aware diff over `ActionItem` snapshots.
- New `render_changes` in `scout.action_items.render` — TTY-aware one-line-per-change formatter.
- New `scout.action_items.watch` — `watchdog`-based file watcher; pure `process_change` core for testability.
- Replaces Plan 2's `watch` stub in `scout.action_items.cli`.
- CLI contract: *"stream changes to today's action items as they happen"* (projection-consumer per v0.4 spec §13.3 — substitutable for an event-store subscriber in v0.5).

## Test plan

- [x] Unit tests for `diff`, `render_changes`, `process_change`
- [x] CLI help-text test pins the projection-consumer wording
- [x] Integration test: subprocess + file mutation + observed stdout
- [x] Manual smoke test confirms ~1s latency from write → diff line
- [x] `test_no_heavy_imports` confirms watchdog/rich stay out of cli startup

## Refs

- v0.4 unification spec §13.3 (projection-consumer contract)
- v0.5+ event architecture spec (substitution target)
- Plan 2 supplement (provides `ActionItem.short_prefix` and ID-aware mutators)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: After PR merges, update FOLLOWUPS.md**

If any review-derived items surface during PR review, capture them in `~/scout-app/docs/superpowers/FOLLOWUPS.md` per its format. None expected from this plan's design but stay alert during review.

---

## What Plan 4 will build on

Plan 3 ships `watch` as a projection-consumer over a file-watcher implementation. Plan 4 (the remaining shell-script ports — hooks and scripts) will:

- Port `~/Scout/hooks/connector-log.sh` → `scout.hooks.connector_log` (writes JSONL via `emit()`, returns Event).
- Port `~/Scout/scripts/sum-session-tokens.sh` → `scout.hooks.session_tokens`.
- Port `~/Scout/scripts/{budget-check,heartbeat,rate-limit-detect,collect-events,connector-health-report,pre-session-data,write-session-cost,cc-session-cache}.sh` → `scout.scripts.*`.
- Flip `session_tokens_v1` and `connector_health_v1` manifest flags to `True`.

Once Plan 4 lands, the v0.5 event store can drop in behind `emit()` and every existing mutation, including hook-driven ones, flows into it for free.
