# Scout Engine Plan 2: Port existing Python (action_items, kb, tui) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the three existing Python subsystems from `~/Scout` into `~/scout-plugin/engine/scout/`: action-items operations (`mark_done`, `snooze`, `add_comment`, `render`, plus a shared parser/writer), the knowledge-base ontology (`KnowledgeGraph` + schema), and the Textual TUI. Wire the action-items operations behind a `scoutctl action-items {mark-done,snooze,add-comment,render,list}` Typer sub-app and expose `scoutctl tui`. Flip `action_items_cli_v1`, `kb_ontology_v1`, `tui_v1` to `True` in the manifest. Plan 2 ships when CI is green and `scoutctl action-items list` works against a fixture data dir.

**Architecture:** Each subsystem becomes a sub-package under `scout/`. Action-items factors out the markdown parser (currently inside `tui/parser.py`) and atomic-write writer (currently scattered across the three argparse scripts) into shared `scout/action_items/{parser,writer}.py` — TUI then imports those instead of carrying its own copies. Path resolution stops using `Path(__file__).parent` in the source scripts; everything routes through `scout.paths.action_items_dir()`, `scout.paths.kb_dir()`, and helpers added in this plan. KB ontology ships its `schema.yaml` packaged via `importlib.resources` (mirroring Plan 1 polish for `scout-config.yaml`), with `$SCOUT_DATA_DIR/knowledge-base/ontology/schema.yaml` as an optional user override. All heavy third-party imports (`textual`, `rich`, `jinja2`, `watchdog`) remain off `scout.cli`'s startup path — they live inside subcommand bodies in `scout/action_items/cli.py` and `scout/tui/__init__.py:tui()`.

**Tech Stack:** Python 3.11+, Typer, PyYAML, Rich (lazy), Textual (lazy), Jinja2 (lazy), pytest. Same dev tooling as Plan 1: uv + `.venv`, ruff, mypy, GitHub Actions.

---

## Context for the implementer

**Working directory:** All file paths in this plan are relative to `/Users/jordanburger/scout-plugin/`. The plan **lives** in the scout-app repo (`docs/superpowers/plans/`) but is **executed** in scout-plugin. Confirm before starting:

```bash
cd ~/scout-plugin
git status        # working tree clean
git remote -v     # origin: https://github.com/jordanrburger/scout-plugin.git
```

**Source files** are in `~/Scout` (Jordan's personal data dir, no git remote). Do not modify them — Plan 7 deletes the originals after the migration verifies. For this plan you read from `~/Scout/...` and write under `~/scout-plugin/engine/scout/...`.

**Prerequisites:** Plan 1 merged on `scout-plugin/main`. The polish/plan-1-followups PR (#6) should also be merged before starting; if it has not merged yet, rebase this plan's work branch on top once it does.

**Reference docs:**
- `/Users/jordanburger/scout-app/docs/superpowers/specs/2026-04-24-scout-unification-design.md` — see §4 file-migration map, §6 concurrency rules, §9 testing strategy.
- `/Users/jordanburger/scout-app/docs/superpowers/plans/2026-04-24-scout-unification-plan-1-engine-scaffolding.md` — established conventions (TDD per task, commit per task, lazy imports).

**What this plan does NOT touch** (deferred to later plans):
- Shell-script ports (`run-*.sh`, `hooks/*.sh`, `scripts/*.sh`, `action-items/watch.sh`) — Plan 3.
- `scoutctl setup`, plugin-level hook registration, launchd templates — Plan 4.
- Personal-data scrub + skill rewrites + `kb_summary.json` cache — Plan 5.
- scout-app refactor — Plan 6.
- Final cutover and `~/Scout` cleanup — Plan 7.

`scoutctl action-items watch` is intentionally **not implemented in Plan 2** because `~/Scout/action-items/watch.sh` is a shell script. The Plan 2 CLI registers a `watch` placeholder that exits with `ScoutError("watch is implemented in Plan 3")` — keeping the manifest's CLI surface predictable for scout-app consumers.

`scoutctl kb query` is **also not in Plan 2**. The flag this plan flips is `kb_ontology_v1`, which means "engine ships the ontology code and packaged schema" — query CLI lands in Plan 4 alongside other `setup`/CLI work.

## File structure (what Plan 2 creates)

```
~/scout-plugin/engine/
├── scout/
│   ├── action_items/                    NEW
│   │   ├── __init__.py
│   │   ├── parser.py                    (port of tui/parser.py)
│   │   ├── writer.py                    (atomic-write helpers, factored out
│   │   │                                  of mark_done/snooze/add_comment +
│   │   │                                  superseding tui/writer.py)
│   │   ├── mark_done.py                 (refactored as importable module)
│   │   ├── snooze.py                    (refactored as importable module)
│   │   ├── add_comment.py               (refactored as importable module)
│   │   ├── render.py                    (port; rich imported inside)
│   │   ├── list.py                      NEW — enumerate open/all tasks
│   │   └── cli.py                       NEW — Typer sub-app
│   ├── kb/                              NEW
│   │   ├── __init__.py
│   │   ├── ontology.py                  (port of knowledge-base/ontology/parser.py)
│   │   ├── schema.yaml                  (packaged default; via importlib.resources)
│   │   └── paths.py                     NEW — schema resolution (user override → packaged)
│   ├── tui/                             NEW
│   │   ├── __init__.py                  (entry point + tui() Typer command)
│   │   ├── app.py                       (port)
│   │   ├── config.py                    (port)
│   │   └── screens/
│   │       ├── __init__.py
│   │       ├── dashboard.py             (port)
│   │       ├── context.py               (port)
│   │       ├── note_modal.py            (port)
│   │       └── spawn.py                 (port)
│   ├── paths.py                         MODIFIED — add helper for daily filename
│   ├── cli.py                           MODIFIED — register action-items + tui sub-apps
│   └── manifest.py                      MODIFIED — flip three feature flags
├── tests/
│   ├── fixtures/
│   │   ├── action-items-sample.md       NEW
│   │   └── kb-sample/                   NEW
│   │       ├── schema.yaml
│   │       └── people/
│   │           └── jordan.md
│   ├── unit/
│   │   ├── test_action_items_parser.py  NEW
│   │   ├── test_action_items_writer.py  NEW
│   │   ├── test_action_items_mark_done.py NEW
│   │   ├── test_action_items_snooze.py  NEW
│   │   ├── test_action_items_add_comment.py NEW
│   │   ├── test_action_items_render.py  NEW
│   │   ├── test_action_items_list.py    NEW
│   │   ├── test_kb_ontology.py          NEW
│   │   ├── test_tui_smoke.py            NEW
│   │   ├── test_paths.py                MODIFIED — daily-filename helper
│   │   └── test_manifest.py             MODIFIED — feature flag values
│   ├── integration/                     NEW
│   │   ├── __init__.py
│   │   └── test_action_items_cli.py     NEW (subprocess-driven)
│   └── perf/
│       └── test_no_heavy_imports.py     MODIFIED — add scout.action_items.cli, scout.tui to whitelist of files allowed to import heavy modules
```

The shared `parser.py` lives under `action_items/` (not `tui/`) because action-items is the producer of the markdown format; TUI is one consumer among several. Plan 6's `EngineClient` is another consumer. Locating it under `action_items/` keeps imports natural: `from scout.action_items.parser import parse_file`.

---

## Task 0: Branch, conftest fixture, and the daily-filename helper

**Files:**
- Create: `~/scout-plugin/engine/tests/fixtures/action-items-sample.md`
- Modify: `~/scout-plugin/engine/scout/paths.py`
- Modify: `~/scout-plugin/engine/tests/unit/test_paths.py`

- [ ] **Step 1: Create the migration branch**

```bash
cd ~/scout-plugin
git checkout main
git pull --ff-only
git checkout -b migrate/v0.4.0-port-python
```

- [ ] **Step 2: Create the action-items fixture file**

Tests across this plan need a deterministic markdown file in the action-items format. Create `engine/tests/fixtures/action-items-sample.md`:

```markdown
# Action Items — 2026-04-15

## In Progress

- [ ] 🔴 Submit Lever feedback to recruiting
  - Context: https://example.com/lever
  - Notes: waiting on hiring manager confirmation
- [ ] 🟡 Send Scout plugin announcement
- [x] 🟢 Read incident postmortem

## To Do

- [ ] 🔴 Reply to Q2 budget thread
- [ ] Followup with vendor on contract redlines

## Watching

- [ ] Vendor SLA renegotiation (no action yet)

## Completed Today

- [x] 🟢 Submit weekly status
```

This fixture deliberately includes: open + done tasks, all three priority emojis, a sub-bullet with context link + notes, a no-priority task, and the four section headers the parser must recognize.

- [ ] **Step 3: Write the failing test for the daily-filename helper**

Add to `engine/tests/unit/test_paths.py` (append, do not replace):

```python
import datetime as dt


def test_action_items_daily_path_default_today(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = dt.date(2026, 4, 24)
    monkeypatch.setattr(paths, "_today", lambda: today)
    p = paths.action_items_daily_path(data=fake_data_dir)
    assert p.name == "action-items-2026-04-24.md"
    assert p.parent == fake_data_dir / "action-items"


def test_action_items_daily_path_explicit_date(fake_data_dir: Path) -> None:
    p = paths.action_items_daily_path(
        data=fake_data_dir, date=dt.date(2026, 4, 15)
    )
    assert p.name == "action-items-2026-04-15.md"
```

- [ ] **Step 4: Run the test, confirm it fails**

```bash
cd ~/scout-plugin/engine
.venv/bin/pytest tests/unit/test_paths.py -v -k action_items_daily_path
```

Expected: `AttributeError: module 'scout.paths' has no attribute 'action_items_daily_path'`.

- [ ] **Step 5: Implement the helper in `scout/paths.py`**

Append to `engine/scout/paths.py`:

```python
import datetime as _dt


def _today() -> _dt.date:
    """Indirection so tests can monkeypatch the date without freezing time."""
    return _dt.date.today()


def action_items_daily_path(
    data: Path | None = None, date: _dt.date | None = None
) -> Path:
    """Return the daily action-items markdown path for `date` (default today).

    Filename format matches the existing ~/Scout convention:
    `action-items-YYYY-MM-DD.md` under the data dir's `action-items/`.
    """
    d = date or _today()
    return action_items_dir(data) / f"action-items-{d.isoformat()}.md"
```

- [ ] **Step 6: Run the test, confirm it passes**

```bash
.venv/bin/pytest tests/unit/test_paths.py -v -k action_items_daily_path
```

Expected: 2 passed.

- [ ] **Step 7: Run the full unit suite — nothing else regressed**

```bash
.venv/bin/pytest tests/unit/ -v
```

Expected: existing tests still pass plus the 2 new ones.

- [ ] **Step 8: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/paths.py engine/tests/unit/test_paths.py engine/tests/fixtures/action-items-sample.md
git commit -m "feat(engine): add action_items_daily_path + sample fixture"
```

---

## Task 1: Port the markdown parser (shared by action_items + tui)

**Files:**
- Create: `~/scout-plugin/engine/scout/action_items/__init__.py`
- Create: `~/scout-plugin/engine/scout/action_items/parser.py`
- Create: `~/scout-plugin/engine/tests/unit/test_action_items_parser.py`

**Source:** `~/Scout/tui/parser.py` (272 lines). It exposes `ActionItem` dataclass and parsing functions for the four sections (`In Progress`, `To Do`, `Watching`, `Completed Today`) plus the inline-section variants (`### 🔴 URGENT: ...`).

- [ ] **Step 1: Create `engine/scout/action_items/__init__.py`**

```python
"""Action-items operations and shared markdown parser/writer."""
```

- [ ] **Step 2: Write the parser tests first**

Create `engine/tests/unit/test_action_items_parser.py`:

```python
"""Unit tests for scout.action_items.parser.

Drives all assertions off engine/tests/fixtures/action-items-sample.md
so behavior remains anchored to a real, version-controlled document.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.action_items.parser import ActionItem, parse_file

FIXTURE = Path(__file__).parent.parent / "fixtures" / "action-items-sample.md"


@pytest.fixture
def items() -> list[ActionItem]:
    return parse_file(FIXTURE)


def test_parses_all_items(items: list[ActionItem]) -> None:
    assert len(items) == 7  # 3 in progress + 2 to do + 1 watching + 1 completed


def test_open_vs_done_status(items: list[ActionItem]) -> None:
    open_titles = [i.title for i in items if i.status == "open"]
    done_titles = [i.title for i in items if i.status == "done"]
    assert "Submit Lever feedback to recruiting" in open_titles
    assert "Read incident postmortem" in done_titles


def test_priority_extraction(items: list[ActionItem]) -> None:
    by_title = {i.title: i for i in items}
    assert by_title["Submit Lever feedback to recruiting"].priority == "🔴"
    assert by_title["Send Scout plugin announcement"].priority == "🟡"
    assert by_title["Read incident postmortem"].priority == "🟢"
    assert by_title["Followup with vendor on contract redlines"].priority == ""


def test_section_attribution(items: list[ActionItem]) -> None:
    by_title = {i.title: i for i in items}
    assert by_title["Submit Lever feedback to recruiting"].section == "In Progress"
    assert by_title["Reply to Q2 budget thread"].section == "To Do"
    assert by_title["Vendor SLA renegotiation (no action yet)"].section == "Watching"
    assert by_title["Submit weekly status"].section == "Completed Today"


def test_sub_bullets_collected(items: list[ActionItem]) -> None:
    by_title = {i.title: i for i in items}
    lever = by_title["Submit Lever feedback to recruiting"]
    # context_links comes from "Context: <url>" sub-bullet
    assert any("example.com/lever" in link for link in lever.context_links)
    # notes from "Notes: ..." sub-bullet
    assert any("hiring manager" in note for note in lever.notes)


def test_raw_line_preserved_for_substring_lookup(items: list[ActionItem]) -> None:
    """Writer modules locate items by full-line substring match;
    `raw_line` must be the exact original source line."""
    by_title = {i.title: i for i in items}
    raw = by_title["Reply to Q2 budget thread"].raw_line
    assert "[ ]" in raw
    assert "🔴" in raw
    assert "Reply to Q2 budget thread" in raw
```

- [ ] **Step 3: Run tests — confirm RED**

```bash
.venv/bin/pytest tests/unit/test_action_items_parser.py -v
```

Expected: `ModuleNotFoundError: No module named 'scout.action_items.parser'`.

- [ ] **Step 4: Port the parser**

```bash
cp ~/Scout/tui/parser.py ~/scout-plugin/engine/scout/action_items/parser.py
```

The source is self-contained (uses `dataclass`, `re`, `Path`) — no path resolution to retarget. Open the new file and confirm:

- Module docstring is preserved.
- `ActionItem` dataclass has fields: `priority, title, status, section, context_links, notes, details, raw_line` (matching what the test asserts).
- Top-level entry point is `parse_file(path: Path) -> list[ActionItem]` (or rename if needed). If it isn't, add a small wrapper:

```python
def parse_file(path: Path) -> list[ActionItem]:
    """Parse `path` into a list of ActionItem records."""
    return _parse_lines(path.read_text(encoding="utf-8").splitlines())
```

…where `_parse_lines` is whatever the original top-level function was named.

- [ ] **Step 5: Run tests — confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_action_items_parser.py -v
```

Expected: 6 passed.

If a test fails, the source's parser logic differs from the fixture's exact format. Adjust the test fixture (not the parser) so the document matches what the parser was written to handle — *unless* the parser rejects something it should clearly accept (in which case fix the parser and document the change in the commit).

- [ ] **Step 6: Lint**

```bash
.venv/bin/ruff check scout tests
.venv/bin/ruff format --check scout tests
.venv/bin/mypy scout
```

Expected: clean. If mypy complains about untyped fields, add explicit annotations matching the dataclass.

- [ ] **Step 7: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/__init__.py engine/scout/action_items/parser.py engine/tests/unit/test_action_items_parser.py
git commit -m "feat(engine): port action_items parser from tui/parser.py"
```

---

## Task 2: Port the atomic-write writer

**Files:**
- Create: `~/scout-plugin/engine/scout/action_items/writer.py`
- Create: `~/scout-plugin/engine/tests/unit/test_action_items_writer.py`

**Source:** atomic-write helpers are duplicated across `~/Scout/action-items/{mark_done,snooze,add_comment}.py` (search for `tempfile`, `os.replace`). `~/Scout/tui/writer.py` is naive (29 lines, no atomicity). The Plan 2 writer is the canonical atomic version. TUI gets refactored to use it in Task 9.

Per spec §6 concurrency rules: action-item markdown writes must be `tempfile → fsync → os.replace`. Readers don't lock (atomic rename guarantees they see either the old or new full file).

- [ ] **Step 1: Write the failing tests**

Create `engine/tests/unit/test_action_items_writer.py`:

```python
"""Unit tests for scout.action_items.writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.action_items.writer import (
    atomic_write_lines,
    flip_checkbox,
    insert_below,
)


def test_atomic_write_replaces_file_contents(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("old\n")
    atomic_write_lines(target, ["new line 1", "new line 2"])
    assert target.read_text() == "new line 1\nnew line 2\n"


def test_atomic_write_uses_temp_then_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure between tmp write and replace must leave original intact."""
    target = tmp_path / "f.md"
    target.write_text("original\n")
    real_replace = __import__("os").replace

    def boom(_src: str, _dst: str) -> None:
        raise OSError("simulated rename failure")

    import os
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_lines(target, ["new"])
    assert target.read_text() == "original\n"  # untouched
    monkeypatch.setattr(os, "replace", real_replace)


def test_flip_checkbox_open_to_done(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [ ] task A\n- [ ] task B\n")
    flip_checkbox(target, line_number=1, to_done=True)
    assert target.read_text() == "- [x] task A\n- [ ] task B\n"


def test_flip_checkbox_done_to_open(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [x] task A\n")
    flip_checkbox(target, line_number=1, to_done=False)
    assert target.read_text() == "- [ ] task A\n"


def test_insert_below_appends_after_target_line(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("line 1\nline 2\nline 3\n")
    insert_below(target, line_number=2, text="  - inserted note")
    assert target.read_text() == "line 1\nline 2\n  - inserted note\nline 3\n"


def test_flip_checkbox_out_of_range_raises(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [ ] task\n")
    from scout.errors import ActionItemError
    with pytest.raises(ActionItemError, match="line"):
        flip_checkbox(target, line_number=99, to_done=True)
```

- [ ] **Step 2: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_action_items_writer.py -v
```

Expected: `ModuleNotFoundError: No module named 'scout.action_items.writer'`.

- [ ] **Step 3: Implement `engine/scout/action_items/writer.py`**

Use the atomic-write logic from `~/Scout/action-items/mark_done.py` (look for the `_atomic_write` helper or the inline `tempfile.NamedTemporaryFile` block) as the model. Final module:

```python
"""Atomic write-back for action-items markdown files.

POSIX `os.replace` is atomic: readers see either the old complete
file or the new complete file, never a torn state. We write to a
sibling temp file in the same directory, fsync it, then rename.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from scout.errors import ActionItemError


def atomic_write_lines(target: Path, lines: list[str]) -> None:
    """Replace `target`'s contents with `lines` (one per line, trailing newline)."""
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(parent)
    )
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
            if lines:
                f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        # Cleanup on any failure (including the simulated OSError in tests).
        if tmp.exists():
            tmp.unlink()
        raise


def _read_lines(target: Path) -> list[str]:
    return target.read_text(encoding="utf-8").splitlines()


def flip_checkbox(target: Path, *, line_number: int, to_done: bool) -> None:
    """Toggle `[ ]` ⇄ `[x]` on the 1-indexed line. Preserves all other bytes."""
    lines = _read_lines(target)
    idx = line_number - 1
    if not 0 <= idx < len(lines):
        raise ActionItemError(
            f"flip_checkbox: line {line_number} out of range (1..{len(lines)})"
        )
    old = "[ ]" if to_done else "[x]"
    new = "[x]" if to_done else "[ ]"
    if old not in lines[idx]:
        raise ActionItemError(
            f"flip_checkbox: line {line_number} does not contain `{old}`"
        )
    lines[idx] = lines[idx].replace(old, new, 1)
    atomic_write_lines(target, lines)


def insert_below(target: Path, *, line_number: int, text: str) -> None:
    """Insert `text` as a new line directly below the 1-indexed line."""
    lines = _read_lines(target)
    idx = line_number - 1
    if not 0 <= idx < len(lines):
        raise ActionItemError(
            f"insert_below: line {line_number} out of range (1..{len(lines)})"
        )
    lines.insert(idx + 1, text)
    atomic_write_lines(target, lines)
```

- [ ] **Step 4: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_action_items_writer.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/writer.py engine/tests/unit/test_action_items_writer.py
git commit -m "feat(engine): add action_items writer with atomic markdown rewrites"
```

---

## Task 3: Port `mark_done`

**Files:**
- Create: `~/scout-plugin/engine/scout/action_items/mark_done.py`
- Create: `~/scout-plugin/engine/tests/unit/test_action_items_mark_done.py`

**Source:** `~/Scout/action-items/mark_done.py` (213 lines). The original is an argparse CLI script. Plan 2 keeps its behavior but exposes a callable function `mark_done(...)` so `scout.action_items.cli` can wire it; argparse goes away.

Behavior (verify against source):
- Locates today's daily file (or the file passed positionally `YYYY-MM-DD`).
- Substring-matches a task by `--subject`, case-insensitive.
- Refuses on no-match (raise `ActionItemError`).
- Refuses on ambiguous match (>1 line matched) — raise `ActionItemError` with the matches listed.
- `--undo` flips `[x]` back to `[ ]`.

- [ ] **Step 1: Write the failing tests**

Create `engine/tests/unit/test_action_items_mark_done.py`:

```python
"""Unit tests for scout.action_items.mark_done."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from scout.action_items.mark_done import mark_done
from scout.errors import ActionItemError


def _seed(tmp_path: Path, body: str) -> Path:
    f = tmp_path / "action-items-2026-04-15.md"
    f.write_text(body)
    return f


def test_marks_open_task_done_by_subject(tmp_path: Path) -> None:
    f = _seed(tmp_path, "- [ ] Submit Lever feedback\n- [ ] Other task\n")
    mark_done(f, subject="Lever feedback")
    assert "- [x] Submit Lever feedback" in f.read_text()
    assert "- [ ] Other task" in f.read_text()  # unchanged


def test_no_match_raises(tmp_path: Path) -> None:
    f = _seed(tmp_path, "- [ ] Existing task\n")
    with pytest.raises(ActionItemError, match="no match"):
        mark_done(f, subject="missing keyword")


def test_ambiguous_match_raises_listing_candidates(tmp_path: Path) -> None:
    f = _seed(tmp_path, "- [ ] Lever feedback A\n- [ ] Lever feedback B\n")
    with pytest.raises(ActionItemError, match="ambiguous|multiple") as exc:
        mark_done(f, subject="lever feedback")
    msg = str(exc.value)
    assert "Lever feedback A" in msg
    assert "Lever feedback B" in msg


def test_undo_flips_done_back_to_open(tmp_path: Path) -> None:
    f = _seed(tmp_path, "- [x] Done thing\n")
    mark_done(f, subject="Done thing", undo=True)
    assert "- [ ] Done thing" in f.read_text()


def test_resolves_today_when_path_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout import paths

    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / "action-items").mkdir()
    monkeypatch.setattr(paths, "_today", lambda: dt.date(2026, 4, 15))
    f = _seed(tmp_path / "action-items", "- [ ] task X\n")
    # No `path` argument → resolves via paths.action_items_daily_path()
    mark_done(None, subject="task X")
    assert "- [x] task X" in f.read_text()
```

- [ ] **Step 2: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_action_items_mark_done.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `mark_done.py`**

Adapt the source. Replace `ACTION_ITEMS_DIR = Path(__file__).resolve().parent` with a path argument that defaults to `paths.action_items_daily_path()`. Replace the argparse `main()` with a callable signature.

```python
"""Toggle a task's checkbox to done in a daily action-items markdown.

Match is case-insensitive substring on the task title; must be unambiguous.
Undo flips `[x]` back to `[ ]`. Atomic rewrite via scout.action_items.writer.
"""

from __future__ import annotations

import re
from pathlib import Path

from scout import paths
from scout.action_items.writer import flip_checkbox
from scout.errors import ActionItemError

TASK_RE = re.compile(r"^(?P<indent>\s*)- \[(?P<mark>[ xX])\] (?P<rest>.+?)\s*$")


def _matching_lines(
    lines: list[str], subject: str, *, want_mark: str
) -> list[tuple[int, str]]:
    needle = subject.casefold()
    out: list[tuple[int, str]] = []
    for i, line in enumerate(lines, start=1):
        m = TASK_RE.match(line)
        if not m:
            continue
        if m.group("mark") not in want_mark:
            continue
        if needle in m.group("rest").casefold():
            out.append((i, line))
    return out


def mark_done(
    path: Path | None, *, subject: str, undo: bool = False
) -> Path:
    """Mark the unique matching task done (or open if undo=True).

    Args:
        path: Daily markdown file. None resolves to today's file in SCOUT_DATA_DIR.
        subject: Case-insensitive substring of the task title.
        undo: If True, flip `[x]` back to `[ ]`.

    Returns: the file actually modified (useful when `path is None`).

    Raises ActionItemError on no-match or ambiguous match.
    """
    target = path or paths.action_items_daily_path()
    if not target.exists():
        raise ActionItemError(f"no daily file at {target}")

    lines = target.read_text(encoding="utf-8").splitlines()
    want_mark = "x X" if undo else " "
    matches = _matching_lines(lines, subject, want_mark=want_mark)

    if not matches:
        raise ActionItemError(
            f"mark_done: no match for subject '{subject}' in {target.name}"
        )
    if len(matches) > 1:
        listing = "\n".join(f"  {ln}: {ln_text}" for ln, ln_text in matches)
        raise ActionItemError(
            f"mark_done: ambiguous match for '{subject}' "
            f"({len(matches)} candidates):\n{listing}"
        )

    line_number, _ = matches[0]
    flip_checkbox(target, line_number=line_number, to_done=not undo)
    return target
```

- [ ] **Step 4: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_action_items_mark_done.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Run full suite, lint**

```bash
.venv/bin/pytest tests/ && \
  .venv/bin/ruff check scout tests && \
  .venv/bin/ruff format --check scout tests && \
  .venv/bin/mypy scout
```

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/mark_done.py engine/tests/unit/test_action_items_mark_done.py
git commit -m "feat(engine): port action-items mark_done as importable module"
```

---

## Task 4: Port `snooze`

**Files:**
- Create: `~/scout-plugin/engine/scout/action_items/snooze.py`
- Create: `~/scout-plugin/engine/tests/unit/test_action_items_snooze.py`

**Source:** `~/Scout/action-items/snooze.py` (362 lines). Behavior: moves a matching task to a future-dated daily file, leaves a snooze breadcrumb in the source file. Same matching rules as `mark_done`.

- [ ] **Step 1: Read the source and document the contract in tests first**

Read `~/Scout/action-items/snooze.py` to confirm:
- CLI args: `--subject`, `--until YYYY-MM-DD` or `--days N`, optional source-date positional.
- Adds the task line to the destination daily file (creating it if needed) under a section it picks (verify which — likely `## To Do` or `## Watching`).
- Replaces the original line with a snooze marker line (e.g., `- [→] task — snoozed to 2026-05-01`) or removes it entirely. Match what the source actually does.
- Refuses no-match / ambiguous match identically to mark_done.

Write `engine/tests/unit/test_action_items_snooze.py` with these assertions, in the same style as the mark_done tests (one assertion per behavior). Match exact destination-file behavior to what the source produces — read the source, do not invent a different contract.

```python
"""Unit tests for scout.action_items.snooze.

Adapt the assertions to match the exact behavior in
~/Scout/action-items/snooze.py (read it before writing).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from scout.action_items.snooze import snooze
from scout.errors import ActionItemError


def test_moves_task_to_future_daily_file(tmp_path: Path) -> None:
    src = tmp_path / "action-items-2026-04-15.md"
    src.write_text("## To Do\n\n- [ ] Reply to vendor\n- [ ] Other thing\n")
    snooze(src, subject="Reply to vendor", until=dt.date(2026, 4, 22))
    dst = tmp_path / "action-items-2026-04-22.md"
    assert dst.exists()
    assert "Reply to vendor" in dst.read_text()
    # Original line removed (or replaced — match source's exact contract).
    src_after = src.read_text()
    assert "- [ ] Other thing" in src_after  # unrelated task untouched


def test_no_match_raises(tmp_path: Path) -> None:
    src = tmp_path / "action-items-2026-04-15.md"
    src.write_text("- [ ] something\n")
    with pytest.raises(ActionItemError, match="no match"):
        snooze(src, subject="missing", until=dt.date(2026, 4, 22))


def test_ambiguous_match_raises(tmp_path: Path) -> None:
    src = tmp_path / "action-items-2026-04-15.md"
    src.write_text("- [ ] vendor A\n- [ ] vendor B\n")
    with pytest.raises(ActionItemError, match="ambiguous|multiple"):
        snooze(src, subject="vendor", until=dt.date(2026, 4, 22))


def test_until_in_the_past_raises(tmp_path: Path) -> None:
    src = tmp_path / "action-items-2026-04-15.md"
    src.write_text("- [ ] task\n")
    with pytest.raises(ActionItemError, match="past"):
        snooze(src, subject="task", until=dt.date(2026, 4, 14))
```

- [ ] **Step 2: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_action_items_snooze.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Port the source**

```bash
cp ~/Scout/action-items/snooze.py ~/scout-plugin/engine/scout/action_items/snooze.py
```

Then refactor the new file:

1. Drop `argparse` and the `if __name__ == "__main__":` block.
2. Replace `ACTION_ITEMS_DIR = Path(__file__).resolve().parent` with calls to `paths.action_items_daily_path()` / `paths.action_items_dir()`.
3. Replace direct `tempfile`/`os.replace` calls with `scout.action_items.writer.atomic_write_lines`.
4. Surface the public API as a `snooze(src: Path | None, *, subject: str, until: dt.date) -> Path` callable. Validate `until > today` — raise `ActionItemError("until is in the past: ...")`.
5. Match-failure messages must contain the substrings the tests assert (`"no match"`, `"ambiguous"` or `"multiple"`, `"past"`).

- [ ] **Step 4: Run, confirm GREEN; iterate if any test fails because the source's exact destination-file contract differs from what the test asserted**

```bash
.venv/bin/pytest tests/unit/test_action_items_snooze.py -v
```

If a test fails because the source removes the line vs. leaves a marker, **change the test** to reflect what the source actually does. Document the choice in the commit message — Plan 2 ports behavior verbatim, it does not change it.

- [ ] **Step 5: Lint**

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/snooze.py engine/tests/unit/test_action_items_snooze.py
git commit -m "feat(engine): port action-items snooze as importable module"
```

---

## Task 5: Port `add_comment`

**Files:**
- Create: `~/scout-plugin/engine/scout/action_items/add_comment.py`
- Create: `~/scout-plugin/engine/tests/unit/test_action_items_add_comment.py`

**Source:** `~/Scout/action-items/add_comment.py` (270 lines). Inserts a sub-bullet beneath a matched task. Same matching rules as `mark_done`.

- [ ] **Step 1: Read source and write the failing tests**

Confirm in source:
- Sub-bullet format (e.g., `  - **[note, 2026-04-15]:** comment text`).
- Whether comments append immediately below the task (most likely) or below existing sub-bullets.
- Whether `--timestamp` is included (TUI's writer.py already produces a timestamp).

Tests:

```python
"""Unit tests for scout.action_items.add_comment."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.action_items.add_comment import add_comment
from scout.errors import ActionItemError


def test_adds_comment_below_matched_task(tmp_path: Path) -> None:
    f = tmp_path / "action-items-2026-04-15.md"
    f.write_text("- [ ] Task A\n- [ ] Task B\n")
    add_comment(f, subject="Task A", text="checked with vendor")
    body = f.read_text()
    assert "checked with vendor" in body
    # The new line is between Task A and Task B (or sits with Task A's sub-bullets).
    assert body.index("Task A") < body.index("checked with vendor") < body.index("Task B")


def test_no_match_raises(tmp_path: Path) -> None:
    f = tmp_path / "action-items-2026-04-15.md"
    f.write_text("- [ ] Task\n")
    with pytest.raises(ActionItemError, match="no match"):
        add_comment(f, subject="other", text="x")


def test_ambiguous_match_raises(tmp_path: Path) -> None:
    f = tmp_path / "action-items-2026-04-15.md"
    f.write_text("- [ ] vendor A\n- [ ] vendor B\n")
    with pytest.raises(ActionItemError, match="ambiguous|multiple"):
        add_comment(f, subject="vendor", text="x")
```

- [ ] **Step 2: Run, confirm RED**

- [ ] **Step 3: Port and refactor**

`cp ~/Scout/action-items/add_comment.py ~/scout-plugin/engine/scout/action_items/add_comment.py`, then apply the same three changes as Task 4 step 3 (drop argparse, route paths through `scout.paths`, use `scout.action_items.writer.insert_below`). Public surface:

```python
def add_comment(
    path: Path | None, *, subject: str, text: str, timestamp: bool = True
) -> Path:
    ...
```

- [ ] **Step 4: Run, confirm GREEN**

- [ ] **Step 5: Lint, commit**

```bash
git add engine/scout/action_items/add_comment.py engine/tests/unit/test_action_items_add_comment.py
git commit -m "feat(engine): port action-items add_comment as importable module"
```

---

## Task 6: Port `render`

**Files:**
- Create: `~/scout-plugin/engine/scout/action_items/render.py`
- Create: `~/scout-plugin/engine/tests/unit/test_action_items_render.py`
- Modify: `~/scout-plugin/engine/tests/perf/test_no_heavy_imports.py`

**Source:** `~/Scout/action-items/render.py` (1094 lines). Largest port in this plan. Renders the daily action-items markdown into a styled Rich/HTML view.

Goal: copy the file, retarget paths, ensure `rich` is *not* imported at module top (Plan 1 rule), expose `render(...)` as a callable. Behavior is preserved verbatim — Plan 2 does not refactor the renderer's logic.

- [ ] **Step 1: Inspect render.py to identify its public entry point and any Rich top-level imports**

```bash
head -60 ~/Scout/action-items/render.py
grep -n '^import\|^from' ~/Scout/action-items/render.py
grep -n 'def main\|def render\|if __name__' ~/Scout/action-items/render.py
```

Note which functions form the public API and where `rich.*` is imported.

- [ ] **Step 2: Write the failing tests**

Tests for a 1094-line renderer focus on **does it run end-to-end on the fixture without crashing** and **does its output contain the right substrings**, not on byte-exact rendering. Create `engine/tests/unit/test_action_items_render.py`:

```python
"""Unit tests for scout.action_items.render.

Smoke-level: render a fixture file and verify the output references the
tasks the parser extracted. Pixel-perfect Rich output is intentionally
not asserted — that would be brittle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.action_items.render import render

FIXTURE = Path(__file__).parent.parent / "fixtures" / "action-items-sample.md"


def test_render_runs_on_fixture_without_error() -> None:
    out = render(FIXTURE)
    assert isinstance(out, str)
    assert len(out) > 0


def test_render_includes_open_task_titles() -> None:
    out = render(FIXTURE)
    assert "Submit Lever feedback" in out
    assert "Reply to Q2 budget thread" in out


def test_render_missing_file_raises(tmp_path: Path) -> None:
    from scout.errors import ActionItemError
    missing = tmp_path / "no-such-file.md"
    with pytest.raises(ActionItemError, match="not found"):
        render(missing)
```

- [ ] **Step 3: Run, confirm RED**

- [ ] **Step 4: Port the file**

```bash
cp ~/Scout/action-items/render.py ~/scout-plugin/engine/scout/action_items/render.py
```

Apply edits in this order:

1. **Move `import rich`, `from rich.X import ...`, `import jinja2` (if any) inside the function bodies that use them.** This satisfies Plan 1's `BANNED_TOP_LEVEL` perf test (which Task 14 extends to scan `scout.action_items.cli`).
2. Replace any `Path(__file__).resolve().parent` with `scout.paths.*` calls.
3. Replace `argparse` and `if __name__ == "__main__":` with a public `render(path: Path) -> str` (and any other helpers tests reference). If the existing `main()` writes to stdout, factor stdout writing into a thin wrapper; the core function should return a string.
4. Wrap the missing-file path with `raise ActionItemError(f"render: file not found: {path}")` so the test's `match="not found"` regex passes.

- [ ] **Step 5: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_action_items_render.py -v
```

Expected: 3 passed. If the renderer needs additional fixture sections (tables, summary blocks), append them to `engine/tests/fixtures/action-items-sample.md` rather than mocking inside the test.

- [ ] **Step 6: Update the import-discipline whitelist**

`scout.action_items.render` is allowed to import `rich` because:
- It is NOT imported at scoutctl startup (`scout.cli` does not import it).
- It is loaded inside `scout.action_items.cli`'s `render` Typer subcommand.

The static AST check in `engine/tests/perf/test_no_heavy_imports.py` only scans `scout/cli.py`, so no whitelist change is required for `render.py` itself. **But** when Task 9 wires `scout.action_items.cli`, that module is also subject to the rule — it must lazy-import `scout.action_items.render` (which transitively pulls Rich) inside the subcommand body.

Append to `tests/perf/test_no_heavy_imports.py` a second AST scan over the new sub-CLI module (Task 9 does this; it is mentioned here so the writer of Task 6 leaves the lazy-import structure correct).

- [ ] **Step 7: Lint, commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/render.py engine/tests/unit/test_action_items_render.py
git commit -m "feat(engine): port action-items render with rich imports kept lazy"
```

---

## Task 7: Implement `list` (new module)

**Files:**
- Create: `~/scout-plugin/engine/scout/action_items/list.py`
- Create: `~/scout-plugin/engine/tests/unit/test_action_items_list.py`

`scoutctl action-items list` is a new operation: enumerate open (or all) tasks from a daily file as a list of `ActionItem` records. The CLI surfaces this as JSON or a human table.

- [ ] **Step 1: Write the tests**

```python
"""Unit tests for scout.action_items.list."""

from __future__ import annotations

from pathlib import Path

from scout.action_items.list import list_items

FIXTURE = Path(__file__).parent.parent / "fixtures" / "action-items-sample.md"


def test_list_open_only_default() -> None:
    items = list_items(FIXTURE)
    statuses = {i.status for i in items}
    assert statuses == {"open"}


def test_list_all_includes_done() -> None:
    items = list_items(FIXTURE, include_done=True)
    statuses = {i.status for i in items}
    assert "done" in statuses
    assert "open" in statuses


def test_list_filter_priority_high() -> None:
    items = list_items(FIXTURE, priority="high")
    assert all(i.priority == "🔴" for i in items)


def test_list_filter_section() -> None:
    items = list_items(FIXTURE, section="Watching")
    assert all(i.section == "Watching" for i in items)
```

- [ ] **Step 2: Run, confirm RED**

- [ ] **Step 3: Implement**

```python
"""Enumerate action items from a daily markdown file with filters."""

from __future__ import annotations

from pathlib import Path

from scout.action_items.parser import ActionItem, parse_file

PRIORITY_ALIASES = {
    "high": "🔴",
    "medium": "🟡",
    "low": "🟢",
    "🔴": "🔴",
    "🟡": "🟡",
    "🟢": "🟢",
}


def list_items(
    path: Path,
    *,
    include_done: bool = False,
    priority: str | None = None,
    section: str | None = None,
) -> list[ActionItem]:
    """Return ActionItems matching the given filters.

    By default returns only open items (status == 'open').
    """
    items = parse_file(path)
    if not include_done:
        items = [i for i in items if i.status == "open"]
    if priority is not None:
        glyph = PRIORITY_ALIASES.get(priority)
        if glyph is None:
            raise ValueError(
                f"unknown priority {priority!r}; "
                f"expected one of {sorted(PRIORITY_ALIASES)}"
            )
        items = [i for i in items if i.priority == glyph]
    if section is not None:
        items = [i for i in items if i.section == section]
    return items
```

- [ ] **Step 4: Run, confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add engine/scout/action_items/list.py engine/tests/unit/test_action_items_list.py
git commit -m "feat(engine): add action-items list module with filters"
```

---

## Task 8: Port the KB ontology + packaged schema

**Files:**
- Create: `~/scout-plugin/engine/scout/kb/__init__.py`
- Create: `~/scout-plugin/engine/scout/kb/paths.py`
- Create: `~/scout-plugin/engine/scout/kb/ontology.py`
- Create: `~/scout-plugin/engine/scout/kb/schema.yaml`
- Create: `~/scout-plugin/engine/tests/fixtures/kb-sample/schema.yaml`
- Create: `~/scout-plugin/engine/tests/fixtures/kb-sample/people/jordan.md`
- Create: `~/scout-plugin/engine/tests/unit/test_kb_ontology.py`

**Sources:**
- `~/Scout/knowledge-base/ontology/parser.py` (287 lines) — defines `KnowledgeGraph` with `schema_path` + `kb_root` constructor args, a `load()` method, and a `query(...)` method. Already path-injected; minimal refactor.
- `~/Scout/knowledge-base/ontology/schema.yaml` — packaged default.
- `~/Scout/knowledge-base/ontology/entities/` — entity-type schemas. Decision: ship these alongside `schema.yaml` only if `parser.py` actually loads them. Read parser.py first; if entities/ are loaded as supporting files, copy them to `engine/scout/kb/entities/`. If not, omit.

- [ ] **Step 1: Move the schema and create fixtures**

```bash
cp ~/Scout/knowledge-base/ontology/schema.yaml ~/scout-plugin/engine/scout/kb/schema.yaml
mkdir -p ~/scout-plugin/engine/tests/fixtures/kb-sample/people
```

Create a minimal fixture schema at `engine/tests/fixtures/kb-sample/schema.yaml` (copy of the real one is fine, since it has no personal data — schema is metadata only).

Create `engine/tests/fixtures/kb-sample/people/jordan.md`:

```markdown
---
type: person
name: Jordan
team: Engineering
works_on: [scout, plugin-x]
---

# Jordan

Test fixture entity for KB ontology tests.
```

If the schema or parser requires additional entity types, create one fixture per type. Inspect the real schema.yaml first to see required fields per type.

- [ ] **Step 2: Implement `scout/kb/paths.py`**

```python
"""Resolve the KB schema and entity paths.

Engine ships scout/kb/schema.yaml as a default; users may override
by placing their own schema at $SCOUT_DATA_DIR/knowledge-base/ontology/schema.yaml.
"""

from __future__ import annotations

from importlib.resources import as_file, files
from pathlib import Path
from typing import Iterator

from scout import paths


def packaged_schema() -> Iterator[Path]:
    """Context-manager-style accessor for the bundled schema.yaml."""
    resource = files("scout") / "kb" / "schema.yaml"
    with as_file(resource) as p:
        yield p


def resolve_schema_path(data: Path | None = None) -> Path:
    """User override at $SCOUT_DATA_DIR/knowledge-base/ontology/schema.yaml,
    else extract the packaged copy and return that path.

    Caller is responsible for not mutating the returned path — it may
    point inside an importlib.resources extraction directory.
    """
    user = paths.kb_dir(data) / "ontology" / "schema.yaml"
    if user.exists():
        return user
    # No override → extract packaged
    resource = files("scout") / "kb" / "schema.yaml"
    with as_file(resource) as p:
        return Path(p)
```

(Note: `as_file` returns a context manager; for filesystem-installed wheels the path is real and persists. The simpler approach used in scout.config returns inside the context. For long-lived references we accept that the path may be a temp extraction; for ontology-load-once-per-process this is fine.)

- [ ] **Step 3: Write the failing tests**

```python
"""Unit tests for scout.kb.ontology."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.kb.ontology import KnowledgeGraph

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "kb-sample"


def test_knowledge_graph_loads_fixture() -> None:
    g = KnowledgeGraph(
        schema_path=str(FIXTURE_DIR / "schema.yaml"),
        kb_root=str(FIXTURE_DIR),
    )
    g.load()
    # Spec assertions match what KnowledgeGraph exposes —
    # adapt to the actual API after porting parser.py.
    results = g.query(type="person", name="Jordan")
    assert len(results) == 1
    assert results[0]["name"] == "Jordan"


def test_knowledge_graph_query_unknown_type_returns_empty() -> None:
    g = KnowledgeGraph(
        schema_path=str(FIXTURE_DIR / "schema.yaml"),
        kb_root=str(FIXTURE_DIR),
    )
    g.load()
    assert g.query(type="nonexistent") == []
```

(Adjust assertion shape after Step 4 if the source `query` returns objects rather than dicts.)

- [ ] **Step 4: Port `ontology.py`**

```bash
cp ~/Scout/knowledge-base/ontology/parser.py ~/scout-plugin/engine/scout/kb/ontology.py
```

Adjust:
1. Module docstring updated to point at `scoutctl kb query` (Plan 4) as the future CLI.
2. The class name stays `KnowledgeGraph`.
3. If the source imports anything personal (it shouldn't — it's a generic parser), strip.
4. If the source has an `if __name__ == "__main__":` runner block, drop it.
5. Type hints — add where missing if mypy complains.

- [ ] **Step 5: Run, confirm GREEN; iterate against the actual API**

If the source `query()` returns dataclass instances rather than dicts, change the test's assertion shape to match — Plan 2 ports the contract verbatim.

- [ ] **Step 6: Lint, commit**

```bash
cd ~/scout-plugin
git add engine/scout/kb/ engine/tests/fixtures/kb-sample/ engine/tests/unit/test_kb_ontology.py
git commit -m "feat(engine): port kb ontology with packaged schema"
```

---

## Task 9: Wire the action-items Typer sub-app

**Files:**
- Create: `~/scout-plugin/engine/scout/action_items/cli.py`
- Modify: `~/scout-plugin/engine/scout/cli.py`
- Create: `~/scout-plugin/engine/tests/integration/__init__.py`
- Create: `~/scout-plugin/engine/tests/integration/test_action_items_cli.py`

- [ ] **Step 1: Implement `scout/action_items/cli.py`**

The sub-app exposes `mark-done`, `snooze`, `add-comment`, `render`, `list`, and a `watch` placeholder. All heavy imports (`scout.action_items.render` → `rich`) live inside the function bodies.

```python
"""scoutctl action-items sub-app.

Top-level imports stay light. Each subcommand imports its module
inside the function body so scoutctl startup is unaffected.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import sys
from pathlib import Path

import typer

from scout.errors import ActionItemError, ScoutError

app = typer.Typer(help="Action-items operations.", no_args_is_help=True)


@app.command("mark-done")
def cli_mark_done(
    subject: str = typer.Option(..., "--subject", help="Substring of task title."),
    path: Path | None = typer.Argument(None, help="Daily markdown file (default: today)."),
    undo: bool = typer.Option(False, "--undo", help="Flip [x] back to [ ]."),
) -> None:
    from scout.action_items.mark_done import mark_done

    mark_done(path, subject=subject, undo=undo)


@app.command("snooze")
def cli_snooze(
    subject: str = typer.Option(..., "--subject"),
    until: str = typer.Option(..., "--until", help="YYYY-MM-DD"),
    path: Path | None = typer.Argument(None),
) -> None:
    from scout.action_items.snooze import snooze

    try:
        target_date = _dt.date.fromisoformat(until)
    except ValueError as e:
        raise ActionItemError(f"--until: invalid date {until!r}") from e
    snooze(path, subject=subject, until=target_date)


@app.command("add-comment")
def cli_add_comment(
    subject: str = typer.Option(..., "--subject"),
    text: str = typer.Option(..., "--text"),
    path: Path | None = typer.Argument(None),
) -> None:
    from scout.action_items.add_comment import add_comment

    add_comment(path, subject=subject, text=text)


@app.command("render")
def cli_render(
    path: Path | None = typer.Argument(None),
) -> None:
    from scout import paths
    from scout.action_items.render import render

    target = path or paths.action_items_daily_path()
    sys.stdout.write(render(target))


@app.command("list")
def cli_list(
    path: Path | None = typer.Argument(None),
    include_done: bool = typer.Option(False, "--include-done"),
    priority: str | None = typer.Option(None, "--priority"),
    section: str | None = typer.Option(None, "--section"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    from scout import paths
    from scout.action_items.list import list_items

    target = path or paths.action_items_daily_path()
    items = list_items(
        target, include_done=include_done, priority=priority, section=section
    )
    if json_out:
        payload = [
            {
                "title": i.title,
                "priority": i.priority,
                "status": i.status,
                "section": i.section,
            }
            for i in items
        ]
        sys.stdout.write(_json.dumps(payload) + "\n")
    else:
        for i in items:
            sys.stdout.write(f"{i.priority} [{i.status}] {i.title}\n")


@app.command("watch")
def cli_watch() -> None:
    raise ScoutError("scoutctl action-items watch is implemented in Plan 3")
```

- [ ] **Step 2: Wire the sub-app into `scout/cli.py`**

Modify `engine/scout/cli.py` — add **lazy** registration so the action-items module is only imported when the user invokes its subcommand:

```python
# Add near the bottom, before main():
def _add_action_items() -> None:
    from scout.action_items.cli import app as action_items_app

    app.add_typer(action_items_app, name="action-items")


_add_action_items()
```

(Top-level call is fine because `scout.action_items.cli` itself only imports `typer` + stdlib + `scout.errors` at module top. The heavy modules are inside the subcommand bodies.)

- [ ] **Step 3: Update the no-heavy-imports test**

Modify `engine/tests/perf/test_no_heavy_imports.py` to also scan `scout/action_items/cli.py`:

```python
SCANNED_FILES = [
    Path(__file__).parent.parent.parent / "scout" / "cli.py",
    Path(__file__).parent.parent.parent / "scout" / "action_items" / "cli.py",
]


@pytest.mark.perf
@pytest.mark.parametrize("source_file", SCANNED_FILES)
def test_cli_has_no_banned_top_level_imports(source_file: Path) -> None:
    source = source_file.read_text()
    imports = _top_level_imports(source)
    offenders = imports & BANNED_TOP_LEVEL
    assert not offenders, (
        f"{source_file} has banned top-level imports: {offenders}. "
        f"Move them inside subcommand functions to preserve startup latency."
    )
```

(Remove the original single-file test; the parameterized version covers it.)

- [ ] **Step 4: Write the integration test**

Subprocess-driven so it exercises the whole stack including Typer routing and exit-code handling.

```python
"""Integration tests for scoutctl action-items via subprocess."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _scoutctl(*args: str, env: dict[str, str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scout.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def test_action_items_list_open_only(tmp_path: Path) -> None:
    data_dir = tmp_path / "Scout"
    items_dir = data_dir / "action-items"
    items_dir.mkdir(parents=True)
    fixture = (
        Path(__file__).parent.parent / "fixtures" / "action-items-sample.md"
    )
    target = items_dir / "action-items-2026-04-15.md"
    target.write_text(fixture.read_text())

    import os

    env = {**os.environ, "SCOUT_DATA_DIR": str(data_dir)}
    r = _scoutctl(
        "action-items", "list", str(target), "--json", env=env
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    statuses = {row["status"] for row in payload}
    assert statuses == {"open"}


def test_action_items_mark_done_via_cli(tmp_path: Path) -> None:
    data_dir = tmp_path / "Scout"
    items_dir = data_dir / "action-items"
    items_dir.mkdir(parents=True)
    target = items_dir / "action-items-2026-04-15.md"
    target.write_text("- [ ] sample task\n")

    import os

    env = {**os.environ, "SCOUT_DATA_DIR": str(data_dir)}
    r = _scoutctl(
        "action-items", "mark-done",
        "--subject", "sample",
        str(target),
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "- [x] sample task" in target.read_text()


def test_action_items_watch_returns_scout_error_exit_code() -> None:
    """Plan 2 stubs `watch` with a Plan 3 placeholder."""
    import os
    r = _scoutctl(
        "action-items", "watch", env={**os.environ}
    )
    # ScoutError.exit_code == 1
    assert r.returncode == 1
    assert "Plan 3" in r.stderr
```

- [ ] **Step 5: Run all the tests**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all green.

- [ ] **Step 6: Smoke-check via the shim**

```bash
cd ~/scout-plugin
engine/bin/scoutctl --help              # should show action-items group
engine/bin/scoutctl action-items --help # five subcommands listed
```

- [ ] **Step 7: Lint, commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/cli.py engine/scout/cli.py \
    engine/tests/integration/ engine/tests/perf/test_no_heavy_imports.py
git commit -m "feat(engine): wire scoutctl action-items sub-app"
```

---

## Task 10: Port the TUI

**Files:**
- Create: `~/scout-plugin/engine/scout/tui/__init__.py`
- Create: `~/scout-plugin/engine/scout/tui/app.py`
- Create: `~/scout-plugin/engine/scout/tui/config.py`
- Create: `~/scout-plugin/engine/scout/tui/screens/__init__.py`
- Create: `~/scout-plugin/engine/scout/tui/screens/dashboard.py`
- Create: `~/scout-plugin/engine/scout/tui/screens/context.py`
- Create: `~/scout-plugin/engine/scout/tui/screens/note_modal.py`
- Create: `~/scout-plugin/engine/scout/tui/screens/spawn.py`
- Modify: `~/scout-plugin/engine/scout/cli.py`
- Create: `~/scout-plugin/engine/tests/unit/test_tui_smoke.py`
- Modify: `~/scout-plugin/engine/pyproject.toml`

The TUI is hardest to test (Textual is a UI framework). The strategy:
1. Port files verbatim, then change every import of `tui.parser` and `tui.writer` to import from `scout.action_items.parser` and `scout.action_items.writer`.
2. Add a smoke test that imports each TUI module — proves no missing dependency or import-time error.
3. Add `scoutctl tui` subcommand that imports `textual` only inside the function body.
4. Add `textual>=0.63` to `pyproject.toml`'s `[full]` extra (or move to default `dependencies` if scoutctl tui must always work after `pip install scout-engine`). **Decision: keep in `[full]`** so a colleague who wants only the CLI doesn't pay the TUI install cost. `scoutctl tui` raises a friendly `ActionItemError` if `import textual` fails.

- [ ] **Step 1: Copy the TUI tree, retargeting imports**

```bash
mkdir -p ~/scout-plugin/engine/scout/tui/screens
cp ~/Scout/tui/__init__.py ~/scout-plugin/engine/scout/tui/__init__.py
cp ~/Scout/tui/app.py      ~/scout-plugin/engine/scout/tui/app.py
cp ~/Scout/tui/config.py   ~/scout-plugin/engine/scout/tui/config.py
cp ~/Scout/tui/screens/*.py ~/scout-plugin/engine/scout/tui/screens/
```

In each copied file, replace:
- `from tui.parser import ...` → `from scout.action_items.parser import ...`
- `from tui.writer import mark_done, add_note` → `from scout.action_items.writer import flip_checkbox, insert_below`. Update call sites: `mark_done(path, ln)` becomes `flip_checkbox(path, line_number=ln, to_done=True)`; `add_note(path, ln, txt)` becomes `insert_below(path, line_number=ln, text=txt)` (timestamp construction moves into the caller — the new writer is content-agnostic).
- `from tui.config import ...` → `from scout.tui.config import ...`
- `from tui.screens.X import ...` → `from scout.tui.screens.X import ...`

Strip any `if __name__ == "__main__": app.run()` blocks; the entry point becomes `scoutctl tui` (Step 3 below).

- [ ] **Step 2: Write the smoke test**

```python
"""Smoke tests for the TUI port: confirm imports work and the
Textual App class is constructable without actually running the UI.

A full UI test requires Textual's pilot framework. Plan 2 keeps
testing minimal — Plan 6 (scout-app) is where TUI wins matter.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "scout.tui",
        "scout.tui.app",
        "scout.tui.config",
        "scout.tui.screens.dashboard",
        "scout.tui.screens.context",
        "scout.tui.screens.note_modal",
        "scout.tui.screens.spawn",
    ],
)
def test_tui_module_imports(module: str) -> None:
    importlib.import_module(module)


def test_tui_app_class_exists() -> None:
    """Sanity-check that `scout.tui.app` exposes the Textual App subclass
    that scoutctl tui will instantiate."""
    pytest.importorskip("textual")
    mod = importlib.import_module("scout.tui.app")
    # The class name varies — read tui/app.py to confirm. Adjust here.
    assert hasattr(mod, "ScoutTUI") or hasattr(mod, "App"), \
        "expected scout.tui.app to expose a Textual App subclass"
```

(Adjust the `hasattr` check to the actual class name found in `app.py`.)

- [ ] **Step 3: Wire `scoutctl tui` in `scout/cli.py`**

Add after the action-items registration:

```python
@app.command()
def tui() -> None:
    """Launch the Textual action-items TUI."""
    try:
        from scout.tui.app import ScoutTUI  # adjust class name to match port
    except ImportError as e:
        from scout.errors import ActionItemError
        raise ActionItemError(
            "Textual is not installed. Install with: "
            'uv pip install -e ".[full]"'
        ) from e
    ScoutTUI().run()
```

- [ ] **Step 4: Update `pyproject.toml` if needed**

The `[full]` extra in pyproject already lists `textual>=0.63` (per Plan 1). Verify:

```bash
grep -A3 'optional-dependencies' ~/scout-plugin/engine/pyproject.toml
```

If the `full` extra doesn't include `textual`, add it.

- [ ] **Step 5: Run smoke tests**

```bash
.venv/bin/uv pip install -e ".[dev,full]"
.venv/bin/pytest tests/unit/test_tui_smoke.py -v
```

Expected: all parametrized imports pass; `test_tui_app_class_exists` passes (textual installed via `[full]`).

- [ ] **Step 6: Verify scoutctl startup did NOT regress**

```bash
.venv/bin/pytest tests/perf/ -v -m perf
```

Expected: latency tests still pass. If `scoutctl --help` regresses, a TUI module is being imported transitively at top of cli.py — find and lazy-load.

- [ ] **Step 7: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/tui/ engine/scout/cli.py engine/tests/unit/test_tui_smoke.py engine/pyproject.toml
git commit -m "feat(engine): port TUI; wire scoutctl tui with lazy textual import"
```

---

## Task 11: Flip the manifest feature flags

**Files:**
- Modify: `~/scout-plugin/engine/scout/manifest.py`
- Modify: `~/scout-plugin/engine/tests/unit/test_manifest.py`

- [ ] **Step 1: Update the test to assert the three flags are True**

Add to `engine/tests/unit/test_manifest.py`:

```python
def test_action_items_kb_tui_features_enabled() -> None:
    """Plan 2 lights these three. Other Plan 1 placeholders remain False."""
    m = build_manifest()
    assert m.features["action_items_cli_v1"] is True
    assert m.features["kb_ontology_v1"] is True
    assert m.features["tui_v1"] is True
    # Plan 3 lights these — still False after Plan 2.
    assert m.features["session_tokens_v1"] is False
    assert m.features["connector_health_v1"] is False
```

- [ ] **Step 2: Run, confirm RED**

Expected: assertion `m.features["action_items_cli_v1"] is True` fails (currently False).

- [ ] **Step 3: Flip the flags in `scout/manifest.py`**

```python
features={
    "session_tokens_v1": False,        # Plan 3
    "connector_health_v1": False,      # Plan 3
    "action_items_cli_v1": True,       # Plan 2
    "kb_ontology_v1": True,            # Plan 2
    "tui_v1": True,                    # Plan 2
},
```

- [ ] **Step 4: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_manifest.py -v
```

- [ ] **Step 5: Verify the manifest output end-to-end**

```bash
.venv/bin/scoutctl manifest show | grep -E '"(action_items|kb_ontology|tui)_v1"'
```

Expected: each line shows `: true,`.

Also confirm the subcommand list now includes `action-items` and `tui`:

```bash
.venv/bin/scoutctl manifest show | python3 -c 'import json,sys; print(json.load(sys.stdin)["subcommands"])'
```

Expected: `['action-items', 'manifest', 'tui', 'version']` (or similar — sorted).

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/manifest.py engine/tests/unit/test_manifest.py
git commit -m "feat(engine): flip action_items_cli_v1, kb_ontology_v1, tui_v1 manifest flags"
```

---

## Task 12: Full verification + push + open PR

**Files:** none created.

- [ ] **Step 1: Run the full suite**

```bash
cd ~/scout-plugin/engine
.venv/bin/pytest tests/ -v
```

Expected counts (approximate):
- 8 paths + 6 config + 3 errors + 8 manifest + 9 cli (Plan 1 polish) + 6 parser + 6 writer + 5 mark_done + 4 snooze + 3 add_comment + 3 render + 4 list + 2 kb + 7 tui smoke + 3 integration + 3 perf + 2 wheel smoke = ~82 tests, all green.

If any test fails, fix root-cause (do not skip).

- [ ] **Step 2: Lint**

```bash
.venv/bin/ruff check scout tests
.venv/bin/ruff format --check scout tests
.venv/bin/mypy scout
```

Expected: clean.

- [ ] **Step 3: End-to-end smoke from the shim**

```bash
cd ~/scout-plugin
engine/bin/scoutctl version
engine/bin/scoutctl manifest show
engine/bin/scoutctl action-items --help
SCOUT_DATA_DIR=/tmp/scout-fake-`whoami` mkdir -p /tmp/scout-fake-`whoami`/action-items
SCOUT_DATA_DIR=/tmp/scout-fake-`whoami` engine/bin/scoutctl action-items list --json 2>&1 | head
```

The list command should fail gracefully (no daily file) with a helpful ActionItemError, not a traceback. If it tracebacks, that's an exception-policy regression — fix.

- [ ] **Step 4: Review commits**

```bash
cd ~/scout-plugin
git log --oneline main..HEAD
```

Expect ~12 commits.

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin migrate/v0.4.0-port-python
gh pr create --title "v0.4.0 Plan 2: port action_items, kb, tui Python" --body "$(cat <<'EOF'
## Summary

Plan 2 of 7 for Scout unification. Ports the existing Python subsystems
from \`~/Scout\` into \`scout-plugin/engine/scout/\`:

- \`scout.action_items\` — shared parser/writer (factored out of TUI), plus
  \`mark_done\`, \`snooze\`, \`add_comment\`, \`render\`, and a new \`list\`
  module. Wired behind \`scoutctl action-items {mark-done,snooze,add-comment,render,list,watch}\`.
  \`watch\` is stubbed pending Plan 3.
- \`scout.kb\` — \`KnowledgeGraph\` ontology parser; \`schema.yaml\` ships
  packaged via \`importlib.resources\`, with user override at
  \`\$SCOUT_DATA_DIR/knowledge-base/ontology/schema.yaml\`. CLI wiring
  for queries lands in Plan 4.
- \`scout.tui\` — Textual app + screens, ported as-is. \`scoutctl tui\`
  imports \`textual\` lazily so it stays an opt-in (\`pip install -e .[full]\`).
- Manifest flags flipped: \`action_items_cli_v1\`, \`kb_ontology_v1\`,
  \`tui_v1\` → \`true\`.

## Test plan

- [ ] CI test workflow green on macOS + Linux × py3.11/3.12
- [ ] CI lint workflow green
- [ ] Manual: \`scoutctl action-items list --json\` returns the expected
  shape against a fixture daily file
- [ ] Manual: \`scoutctl action-items mark-done --subject "..."\` flips a
  checkbox atomically
- [ ] Manual: \`scoutctl tui\` launches Textual without throwing

Refs: \`docs/superpowers/specs/2026-04-24-scout-unification-design.md\`,
\`docs/superpowers/plans/2026-04-24-scout-unification-plan-2-port-existing-python.md\`
(both in scout-app repo).
EOF
)"
```

---

## Self-review (inline; already applied)

**Spec coverage for Plan 2's scope:**
- §4 file migration map for `action_items/{mark_done,snooze,add_comment,render}.py`, `tui/parser.py` → shared parser, `tui/writer.py` → shared atomic writer, `knowledge-base/ontology/{parser.py,schema.yaml}`, `tui/*` → covered by Tasks 1–10.
- §4 CLI surface for `scoutctl action-items {mark-done,snooze,add-comment,render,list}` and `scoutctl tui` → Task 9 + Task 10.
- §4 startup latency rule (heavy imports stay off CLI top) → Task 6 (render keeps `rich` lazy), Task 9 (sub-CLI imports each module inside the body), Task 10 (`scoutctl tui` lazy-imports textual). Task 9 extends `tests/perf/test_no_heavy_imports.py` to scan the new sub-CLI module.
- §6 atomicity for action-item markdown writes (tempfile → fsync → os.replace) → Task 2 writer module.
- §9 unit tests covering parser, writer, action-item commands, kb ontology, and TUI smoke → Tasks 1–10.
- §9 integration test for action-items CLI subprocess contract → Task 9 step 4.
- §9 perf test discipline → Task 6 + Task 9 step 3.
- Manifest flag flips (`action_items_cli_v1`, `kb_ontology_v1`, `tui_v1`) → Task 11.

**Out of scope (deferred):** `watch.sh` (Plan 3), `scoutctl kb query` (Plan 4), shell ports (Plan 3), personal-data scrub (Plan 5), launchd & setup (Plan 4).

**Placeholder scan:** No "TBD"/"TODO"/"implement later". Tests across Tasks 4, 5, 6, 8 acknowledge they may need to adapt to the source's exact contract — this is honesty about porting verbatim, not deferred work. Each adaptation is a localized edit during the named task, not a follow-up.

**Type consistency:**
- `ActionItem` dataclass fields (`priority, title, status, section, context_links, notes, details, raw_line`) used identically in Tasks 1, 7 (list filters), 9 (CLI list serialization).
- `mark_done(path, *, subject, undo)`, `snooze(src, *, subject, until)`, `add_comment(path, *, subject, text, timestamp)` — all return `Path` and raise `ActionItemError` on no-match / ambiguous match. Used consistently in Task 9 sub-app.
- Writer signatures: `atomic_write_lines(target, lines)`, `flip_checkbox(target, *, line_number, to_done)`, `insert_below(target, *, line_number, text)`. Task 10 retargets TUI's `mark_done` / `add_note` calls onto these.
- `KnowledgeGraph(schema_path, kb_root)` with `.load()` and `.query(...)` — Task 8 ports the existing source contract; tests adapt to whichever exact signature it ships.
- `paths.action_items_daily_path()` introduced in Task 0, consumed by Tasks 3, 4, 5 (default-to-today), and Task 9 CLI's `--path` defaulting.
- `INTERNAL_ERROR_EXIT_CODE` + `ScoutError.exit_code` machinery from Plan 1 polish PR is leaned on in Task 12 step 3 (action-items list against a missing daily file should hit `ActionItemError.exit_code` (21), not the generic 70).

**Risk areas, sequenced by impact:**
1. `render.py` (1094 lines) — biggest port; behavior parity hardest to verify. Mitigation: Task 6 step 2 tests are smoke-level, not byte-exact.
2. `snooze.py` exact destination-file behavior — varies by source implementation. Mitigation: Task 4 step 4 explicitly authorizes adapting tests to source contract, with the change documented in the commit.
3. TUI parser-import retargeting — multiple files import `tui.parser`, easy to miss one. Mitigation: Task 10 step 5 smoke tests every TUI submodule's importability.
4. Latency regression from a transitive import in `scout.cli`. Mitigation: Task 10 step 6 explicitly re-runs the perf suite.

---

## What Plan 3 will build on

Plan 3 ports the 11 shell scripts to Python, including `~/Scout/action-items/watch.sh` (stubbed in Plan 2's CLI at Task 9). After Plan 3, the manifest flips `session_tokens_v1` and `connector_health_v1` to `true` and the action-items CLI's `watch` subcommand becomes real.
