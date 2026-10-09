# Action Item Context Notes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every action item its own context note at
`action-items/context/<TAG>.md`, written by the engine and read by the app
into Full context, Launch Claude and the card. Add a one-time backfill that
restores the context the 2026-10-05 restructure cut.

**Architecture:** The engine gets a small `scout.action_items.context_notes`
module (snapshot block extraction, note rendering, backfill) behind a new
`scoutctl action-items backfill-context` command. It also gets a
non-blocking kb-lint check and phase-text changes, so sessions write the
notes. The app gets a `TaskContextNote` loader that reads a note by tag on
demand, a `contextNote` input to `ClaudeLauncher`'s prompts, and a collapsed
card section. Nothing reads notes during the parse or the first paint.

**Tech Stack:** Python 3.11+ (typer, pytest, 98% coverage floor) in
`plugin/engine/`; Swift 6 / SwiftUI / Swift Testing in `apps/macos/`.

**Spec:** `docs/superpowers/specs/2026-10-06-action-item-context-notes-design.md`

## Global Constraints

- Note path: `action-items/context/<TAG>.md`. `<TAG>` is 2–8 chars of `[A-Z0-9]` with at least one letter.
- Note frontmatter keys: `tag`, `title` (plain text), `created`, `updated` (`YYYY-MM-DD`).
- Note budget: the existing default `action-items/** → 15360` bytes. No new default budget.
- The backfill never overwrites an existing note. It is a dry run unless `--write` is passed.
- The kb-lint `missing-context-note` finding is `blocking=False` in every mode.
- `.concise` and `.markdownChecklist` prompts do not change. With no note, `.fullContext` is byte-identical to today's.
- The daily-file parser, `parser-corpus.json`, and both checksum guards are untouched.
- The repo is public. Fixtures use the tags `DETX`, `NESTX`, `PLN`, `LBL`, `OLDTG`, `NEWTG`, `PLN2`, `SAM1` (all verified 2026-10-06 at zero vault hits outside `.claude/`), people Alex / Priya / Sam, `PROJ-1234`, `example-org/<repo>`. Never use real vault text, and check any new tag against the vault first (`CLAUDE.md`).
- Engine coverage floor: `fail_under = 98` in `plugin/engine/pyproject.toml`. Every new line needs a test.
- Don't touch `~/scout-plugin` (the live engine) or write to `~/Scout` (the vault). Real-vault checks run on a **copy** in the scratchpad. The real `--write` backfill is a rollout step for Jordan to OK, not part of this plan.
- Chain shell steps across directories with `&&`, never `;`.
- Local engine venv: run `uv venv && uv pip install -e ".[dev]"` inside `plugin/engine/` of this clone. Never run `uv python install`, which leaves a shim that shadows Homebrew Python.
- App tests: `xcodebuild … -only-testing:ScoutTests/<TypeName> -resultBundlePath "$TMPDIR/xcr-$RANDOM"`, and delete the bundle afterwards. Run from `apps/macos/`.

## Review Focus

1. **A tag reused in the snapshot.** The converter's rule is that the first occurrence wins, so the backfill must restore the first block, not the second. Pinned by `test_first_occurrence_wins` (Task 1).
2. **Items re-tagged after the snapshot.** `[#NEW]` whose line says ``re-tagged from `[#OLD]` `` must get `OLD`'s block. Pinned by `test_retag_marker_finds_old_block` (Task 1).
3. **Nested child items.** An open child task under a parent must get its own sub-block. The parent's block must still include it. Pinned by `test_nested_child_gets_its_own_block` (Task 1).
4. **A note a session wrote between dry run and write.** The write must leave it untouched. Pinned by `test_never_overwrites_existing_note` (Task 1).
5. **A crafted tag reaching the app's loader** (`../X`, lowercase, too long). No file outside `action-items/context/` may be read. Pinned by `badTagsReadNothing` (Task 4).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `plugin/engine/scout/action_items/context_notes.py` | Create | Snapshot blocks, re-tag lookup, note rendering, backfill + report |
| `plugin/engine/scout/action_items/cli.py` | Modify | `backfill-context` command |
| `plugin/engine/tests/unit/test_action_items_context_notes.py` | Create | Backfill + CLI tests |
| `plugin/engine/scout/kb/lint.py` | Modify | `missing-context-note` check |
| `plugin/engine/tests/unit/test_kb_lint.py` | Modify | Lint check tests |
| `plugin/phases/core/write-protocol.md` | Modify | "One home" row for action items |
| `plugin/phases/core/action-items.md` | Modify | Item line format + reconciliation step 5 |
| `plugin/engine/tests/unit/test_kb_write_protocol_assembly.py` | Modify | Assembled-brain assertions |
| `apps/macos/Scout/ActionItems/TaskContextNote.swift` | Create | Tag → URL, load + strip frontmatter |
| `apps/macos/ScoutTests/ActionItems/TaskContextNoteTests.swift` | Create | Loader tests |
| `apps/macos/Scout/Utilities/ClaudeLauncher.swift` | Modify | `contextNote` / `contextNotes` inputs |
| `apps/macos/ScoutTests/ActionItems/ClaudeLauncherPromptTests.swift` | Modify | Prompt tests |
| `apps/macos/Scout/ActionItems/Views/{TaskActionsView,LaunchClaudeMenu}.swift`, `apps/macos/Scout/ActionItems/ActionItemsView.swift` | Modify | Load the note at copy/launch time |
| `apps/macos/Scout/ActionItems/Views/TaskContextNoteSection.swift` | Create | Collapsed "Context note" disclosure + Open note |
| `apps/macos/Scout/ActionItems/Views/TaskCardView.swift` | Modify | Embed the section in the expanded detail |
| `apps/macos/ScoutTests/Shell/ComponentSmokeTests.swift` | Modify | Section smoke render |

---

### Task 1: Engine — context-note backfill and `backfill-context` command

**Files:**
- Create: `plugin/engine/scout/action_items/context_notes.py`
- Modify: `plugin/engine/scout/action_items/cli.py` (new command after `backfill-prefixes`)
- Test: `plugin/engine/tests/unit/test_action_items_context_notes.py`

**Interfaces:**
- Produces: `context_note_rel(tag: str) -> str` (returns `"action-items/context/<TAG>.md"`);
  `snapshot_blocks(text: str) -> dict[str, list[str]]`;
  `render_note(tag: str, title: str, day: str, block: list[str]) -> str`;
  `backfill_context(*, data_dir: Path, snapshot: Path, daily: Path, write: bool) -> BackfillContextReport`;
  `BackfillContextReport(to_write: list[str], existing: list[str], missing: list[str], over_budget: list[tuple[str, int]])`.
- Task 2 imports `context_note_rel`.

- [ ] **Step 1: Set up the engine venv in this clone**

Run: `cd plugin/engine && uv venv && uv pip install -e ".[dev]" && .venv/bin/pytest tests/unit/test_action_items_backfill.py -q`
Expected: the existing backfill tests pass.

- [ ] **Step 2: Write the failing tests**

Create `plugin/engine/tests/unit/test_action_items_context_notes.py`:

```python
"""Unit tests for scout.action_items.context_notes — per-item context notes."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from scout.action_items.cli import app
from scout.action_items.context_notes import (
    backfill_context,
    context_note_rel,
    render_note,
    snapshot_blocks,
)

runner = CliRunner()

SNAPSHOT = """# Action Items — Monday, Oct 5, 2026

## 🔴 Urgent

- [ ] [#DETX] **Order the roadmap items before the review**
  - 🗓️ Due Tuesday and it has never left Todo.
  - 🔑 The other three wait on the customer; this one does not.

- [ ] [#NESTX] **Plan the cycle with Priya**
  - parent context
  - [ ] [#LBL] **Book the room**
    - child context
  - more parent context
Section prose that ends the block.

- [ ] [#DETX] **A second row that reused the tag**
  - must not win
- [ ] [#OLDTG] **Item that was re-tagged later**
  - context kept under the old tag
"""

DAILY = """# Action Items — Tuesday, Oct 6, 2026

## 🔴 Urgent

- [ ] [#DETX] **Order the roadmap items before the review** — due today …
- [ ] [#NESTX] **Plan the cycle with Priya** …
  - [ ] [#LBL] **Book the room**
- [ ] [#NEWTG] **Item that was re-tagged later** _(re-tagged from `[#OLDTG]` on 2026-10-05)_
- [ ] [#PLN] **Brand-new item with no snapshot entry**
- [x] [#SAM1] **Done item is ignored**
"""

BACKLOG = """# Backlog

## 🟢 Watching

- [ ] [#PLN2] **Backlog item** — watching
"""


def _vault(tmp_path: Path, *, backlog: str | None = BACKLOG, config: str | None = None) -> tuple[Path, Path, Path]:
    vault = tmp_path / "vault"
    ai = vault / "action-items"
    (ai / "archive").mkdir(parents=True)
    snap = ai / "archive" / "action-items-2026-10-05-pre-restructure.md"
    snap.write_text(SNAPSHOT + "- [ ] [#PLN2] **Backlog item**\n  - backlog context\n", encoding="utf-8")
    daily = ai / "action-items-2026-10-06.md"
    daily.write_text(DAILY, encoding="utf-8")
    if backlog is not None:
        (ai / "backlog.md").write_text(backlog, encoding="utf-8")
    if config is not None:
        (vault / "scout-config.yaml").write_text(config, encoding="utf-8")
    return vault, snap, daily


def test_context_note_rel() -> None:
    assert context_note_rel("DETX") == "action-items/context/DETX.md"


def test_first_occurrence_wins() -> None:
    blocks = snapshot_blocks(SNAPSHOT)
    assert blocks["DETX"][0].startswith("- [ ] [#DETX] **Order the roadmap")
    assert all("must not win" not in ln for ln in blocks["DETX"])


def test_nested_child_gets_its_own_block() -> None:
    blocks = snapshot_blocks(SNAPSHOT)
    assert blocks["LBL"] == ["  - [ ] [#LBL] **Book the room**", "    - child context"]
    assert "  - [ ] [#LBL] **Book the room**" in blocks["NESTX"]
    assert blocks["NESTX"][-1] == "  - more parent context"


def test_block_ends_at_column_zero_and_drops_trailing_blanks() -> None:
    blocks = snapshot_blocks(SNAPSHOT)
    assert blocks["DETX"][-1] == "  - 🔑 The other three wait on the customer; this one does not."
    assert all("Section prose" not in ln for ln in blocks["NESTX"])


def test_render_note_frontmatter_and_verbatim_body() -> None:
    text = render_note("DETX", 'Order "the" items', "2026-10-05", ["- [ ] [#DETX] x", "  - y"])
    assert text.startswith('---\ntag: DETX\ntitle: "Order \\"the\\" items"\ncreated: 2026-10-05\nupdated: 2026-10-05\n---\n')
    assert "Restored verbatim from the 2026-10-05 pre-restructure snapshot." in text
    assert text.endswith("- [ ] [#DETX] x\n  - y\n")


def test_backfill_dry_run_reports_and_writes_nothing(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path)
    report = backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=False)
    assert sorted(report.to_write) == ["DETX", "LBL", "NESTX", "NEWTG", "PLN2"]
    assert report.missing == ["PLN"]
    assert not (vault / "action-items" / "context").exists()


def test_backfill_write_creates_notes(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path)
    backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    note = (vault / context_note_rel("DETX")).read_text(encoding="utf-8")
    assert "title: \"Order the roadmap items before the review\"" in note
    assert "  - 🗓️ Due Tuesday and it has never left Todo." in note
    assert (vault / context_note_rel("PLN2")).exists()  # backlog item


def test_retag_marker_finds_old_block(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path)
    backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    note = (vault / context_note_rel("NEWTG")).read_text(encoding="utf-8")
    assert "tag: NEWTG" in note
    assert "context kept under the old tag" in note


def test_never_overwrites_existing_note(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path)
    existing = vault / context_note_rel("DETX")
    existing.parent.mkdir(parents=True)
    existing.write_text("written by a session\n", encoding="utf-8")
    report = backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    assert report.existing == ["DETX"]
    assert existing.read_text(encoding="utf-8") == "written by a session\n"


def test_second_run_writes_nothing(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path)
    backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    again = backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    assert again.to_write == []
    assert sorted(again.existing) == ["DETX", "LBL", "NESTX", "NEWTG", "PLN2"]


def test_over_budget_written_in_full_and_reported(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path, config="kb_budgets:\n  'action-items/context/**': 200\n")
    report = backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    tags = {t for t, _ in report.over_budget}
    assert "DETX" in tags
    assert "🔑 The other three" in (vault / context_note_rel("DETX")).read_text(encoding="utf-8")


def test_unbolded_title_and_undated_snapshot(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    ai = vault / "action-items"
    ai.mkdir(parents=True)
    snap = ai / "snapshot.md"
    snap.write_text("- [ ] [#PLN] plain line with no bold\n  - ctx\n", encoding="utf-8")
    daily = ai / "action-items-2026-10-06.md"
    daily.write_text("- [ ] [#PLN] plain line with no bold\n", encoding="utf-8")
    backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=True)
    note = (vault / context_note_rel("PLN")).read_text(encoding="utf-8")
    assert 'title: "plain line with no bold"' in note
    assert "created: \n" in note


def test_missing_backlog_is_fine(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path, backlog=None)
    report = backfill_context(data_dir=vault, snapshot=snap, daily=daily, write=False)
    assert "PLN2" not in report.to_write


def test_cli_dry_run_then_write(tmp_path: Path) -> None:
    vault, snap, daily = _vault(tmp_path, config="kb_budgets:\n  'action-items/context/**': 200\n")
    dry = runner.invoke(app, ["backfill-context", "--from", str(snap), "--daily", str(daily)])
    assert dry.exit_code == 0, dry.output
    assert "would write 5 note(s)" in dry.stdout
    assert "no snapshot entry: PLN" in dry.stdout
    assert "'action-items/context/DETX.md': null" in dry.stdout
    assert not (vault / "action-items" / "context").exists()
    wrote = runner.invoke(app, ["backfill-context", "--from", str(snap), "--daily", str(daily), "--write"])
    assert wrote.exit_code == 0, wrote.output
    assert "wrote 5 note(s)" in wrote.stdout
    assert (vault / context_note_rel("LBL")).exists()


def test_cli_missing_snapshot_exits_nonzero(tmp_path: Path) -> None:
    vault, _, daily = _vault(tmp_path)
    result = runner.invoke(app, ["backfill-context", "--from", str(vault / "nope.md"), "--daily", str(daily)])
    assert result.exit_code == 2
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_action_items_context_notes.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'scout.action_items.context_notes'`.

- [ ] **Step 4: Implement the module**

Create `plugin/engine/scout/action_items/context_notes.py`:

```python
"""Per-item context notes: `action-items/context/<TAG>.md`.

An action item in the daily file is one line. Its context — status,
evidence, quotes, links — lives in its own note, which the apps read into
Full context / Launch Claude. `backfill_context` restores notes, verbatim,
from the pre-restructure snapshot of a daily file (the one-time conversion to
one-line items moved everything but the first sub-bullet there).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from scout.ids import leading_prefix_pattern
from scout.kb.lint_config import load_lint_config

_TASK = re.compile(r"^(\s*)- \[([ xX])\] (.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_RETAG = re.compile(r"re-tagged from `?\[#([A-Z0-9]{2,8})\]`?")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def context_note_rel(tag: str) -> str:
    return f"action-items/context/{tag}.md"


def _tag_of(rest: str) -> str | None:
    m = leading_prefix_pattern().match(rest)
    return m.group(1) if m else None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def snapshot_blocks(text: str) -> dict[str, list[str]]:
    """Each tagged task's block: its line plus every following blank or
    more-indented line. Nested tagged tasks get their own sub-block and stay
    in their parent's. First occurrence of a tag wins."""
    blocks: dict[str, list[str]] = {}
    active: list[tuple[str, int]] = []
    for line in text.split("\n"):
        if not line.strip():
            for tag, _ in active:
                blocks[tag].append(line)
            continue
        ind = _indent(line)
        active = [(t, i) for t, i in active if i < ind]
        for tag, _ in active:
            blocks[tag].append(line)
        m = _TASK.match(line)
        if m:
            tag = _tag_of(m.group(3))
            if tag is not None and tag not in blocks:
                blocks[tag] = [line]
                active.append((tag, ind))
    for tag, lines in blocks.items():
        while lines and not lines[-1].strip():
            lines.pop()
    return blocks


def render_note(tag: str, title: str, day: str, block: list[str]) -> str:
    return (
        f"---\ntag: {tag}\ntitle: {json.dumps(title, ensure_ascii=False)}\n"
        f"created: {day}\nupdated: {day}\n---\n\n"
        f"Restored verbatim from the {day} pre-restructure snapshot.\n\n" + "\n".join(block) + "\n"
    )


@dataclass
class BackfillContextReport:
    to_write: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    over_budget: list[tuple[str, int]] = field(default_factory=list)


def _plain_title(rest: str) -> str:
    """The bold title after the `[#TAG]`, or the rest of the line if unbolded."""
    rest = leading_prefix_pattern().sub("", rest, count=1).strip()
    m = _BOLD.search(rest)
    return (m.group(1) if m else rest).strip()


def _open_items(path: Path) -> list[tuple[str, str, str]]:
    """(tag, title, raw line) for every open tagged task line in `path`,
    nested child tasks included. Scans lines directly: `parse_lines` folds an
    indented `- [ ]` child into its parent's details, so it would miss them."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _TASK.match(line)
        if m is None or m.group(2) != " ":
            continue
        tag = _tag_of(m.group(3))
        if tag is not None:
            out.append((tag, _plain_title(m.group(3)), line))
    return out


def backfill_context(*, data_dir: Path, snapshot: Path, daily: Path, write: bool) -> BackfillContextReport:
    blocks = snapshot_blocks(snapshot.read_text(encoding="utf-8"))
    m = _DATE.search(snapshot.name)
    day = m.group(1) if m else ""
    cfg = load_lint_config(data_dir)
    report = BackfillContextReport()
    seen: set[str] = set()
    for tag, title, raw in _open_items(daily) + _open_items(data_dir / "action-items" / "backlog.md"):
        if tag in seen:
            continue
        seen.add(tag)
        rel = context_note_rel(tag)
        path = data_dir / rel
        if path.exists():
            report.existing.append(tag)
            continue
        block = blocks.get(tag)
        if block is None and (rt := _RETAG.search(raw)):
            block = blocks.get(rt.group(1))
        if block is None:
            report.missing.append(tag)
            continue
        text = render_note(tag, title, day, block)
        size = len(text.encode("utf-8"))
        budget = cfg.budget_for(rel)
        if budget is not None and size > budget:
            report.over_budget.append((tag, size))
        report.to_write.append(tag)
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    return report
```

- [ ] **Step 5: Add the command**

In `plugin/engine/scout/action_items/cli.py`, after `cli_backfill_prefixes`:

```python
@app.command("backfill-context")
def cli_backfill_context(
    snapshot: Path = typer.Option(..., "--from", help="Pre-restructure snapshot of a daily file."),
    daily: Path | None = typer.Option(
        None, "--daily", help="Daily file (default: today). Its grandparent is the data dir."
    ),
    write: bool = typer.Option(False, "--write", help="Write the notes. Without it: dry run."),
) -> None:
    """Restore each open item's context note from a pre-restructure snapshot.

    Writes `action-items/context/<TAG>.md` for every open item in the daily
    file and `action-items/backlog.md` that has no note yet, copying its
    snapshot block verbatim. Never overwrites a note; re-running is a no-op.
    """
    from scout import paths
    from scout.action_items.context_notes import backfill_context

    if not snapshot.exists():
        sys.stderr.write(f"snapshot not found: {snapshot}\n")
        raise typer.Exit(code=2)
    target = daily or paths.action_items_daily_path()
    data_dir = daily.parent.parent if daily is not None else paths.data_dir()
    report = backfill_context(data_dir=data_dir, snapshot=snapshot, daily=target, write=write)
    verb = "wrote" if write else "would write"
    sys.stdout.write(f"{verb} {len(report.to_write)} note(s); {len(report.existing)} already had one\n")
    if report.missing:
        sys.stdout.write(f"no snapshot entry: {', '.join(report.missing)}\n")
    if report.over_budget:
        sys.stdout.write("over budget (written in full) — add to scout-config.yaml kb_budgets:\n")
        for tag, size in report.over_budget:
            sys.stdout.write(f"  'action-items/context/{tag}.md': null   # {size} bytes\n")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_action_items_context_notes.py -q`
Expected: 15 passed.

- [ ] **Step 7: Commit**

```bash
git add plugin/engine/scout/action_items/context_notes.py plugin/engine/scout/action_items/cli.py plugin/engine/tests/unit/test_action_items_context_notes.py
git commit -m "feat(action-items): per-item context notes + backfill-context from the restructure snapshot"
```

---

### Task 2: Engine — kb-lint `missing-context-note` warning

**Files:**
- Modify: `plugin/engine/scout/kb/lint.py` (`lint_staged`, new `_check_context_notes`)
- Test: `plugin/engine/tests/unit/test_kb_lint.py`

**Interfaces:**
- Consumes: `context_note_rel(tag)` from Task 1.
- Produces: `Finding(path, "missing-context-note", False, message, line)`.

- [ ] **Step 1: Write the failing tests**

Append to `plugin/engine/tests/unit/test_kb_lint.py`:

```python
DAILY_REL = "action-items/action-items-2026-10-06.md"
DAILY_HEAD = "# Action Items\n\n## 🔴 Urgent\n\n"


def _ctx_findings(kb_repo) -> list[tuple[str, int | None]]:
    res = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    return [(f.check, f.line) for f in res.findings if f.check == "missing-context-note"]


def test_new_open_item_without_note_warns(kb_repo) -> None:
    kb_repo.stage(DAILY_REL, DAILY_HEAD + "- [ ] [#DETX] **Order the items**\n")
    res = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    found = [f for f in res.findings if f.check == "missing-context-note"]
    assert len(found) == 1 and found[0].blocking is False and "action-items/context/DETX.md" in found[0].message


def test_staged_note_silences_warning(kb_repo) -> None:
    kb_repo.stage("action-items/context/DETX.md", "---\ntag: DETX\n---\n\nctx\n")
    kb_repo.stage(DAILY_REL, DAILY_HEAD + "- [ ] [#DETX] **Order the items**\n")
    assert _ctx_findings(kb_repo) == []


def test_committed_note_silences_warning(kb_repo) -> None:
    kb_repo.stage("action-items/context/DETX.md", "ctx\n")
    kb_repo.commit()
    kb_repo.stage(DAILY_REL, DAILY_HEAD + "- [ ] [#DETX] **Order the items**\n")
    assert _ctx_findings(kb_repo) == []


def test_untagged_and_done_lines_are_ignored(kb_repo) -> None:
    kb_repo.stage(DAILY_REL, DAILY_HEAD + "- [ ] **No tag**\n- [x] [#PLN] **Done**\n")
    assert _ctx_findings(kb_repo) == []


def test_carried_forward_line_is_not_rechecked(kb_repo) -> None:
    kb_repo.stage("action-items/action-items-2026-10-05.md", DAILY_HEAD + "- [ ] [#DETX] **Order the items**\n")
    kb_repo.commit()
    kb_repo.stage(DAILY_REL, DAILY_HEAD + "- [ ] [#DETX] **Order the items**\n")
    assert _ctx_findings(kb_repo) == []


def test_missing_note_never_blocks_in_block_mode(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_lint:\n  mode: block\n")
    kb_repo.stage(DAILY_REL, DAILY_HEAD + "- [ ] [#DETX] **Order the items**\n")
    res = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    assert all(f.check != "missing-context-note" for f in res.blocking)


def test_non_daily_files_are_not_checked(kb_repo) -> None:
    kb_repo.stage("action-items/backlog.md", "- [ ] [#DETX] **Backlog item**\n")
    assert _ctx_findings(kb_repo) == []
```

- [ ] **Step 2: Run to verify the first test fails**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_kb_lint.py -q -k "context or carried or untagged or non_daily"`
Expected: `test_new_open_item_without_note_warns` FAILS (no finding). The others pass vacuously. That is expected, because they guard against false positives.

- [ ] **Step 3: Implement**

In `plugin/engine/scout/kb/lint.py`, add the imports:

```python
from scout.action_items.context_notes import context_note_rel
from scout.ids import leading_prefix_pattern
```

Add after `_new_lines`:

```python
_OPEN_TASK = re.compile(r"^\s*- \[ \] (.*)$")


def _check_context_notes(repo: Path, rel: str, added: list[tuple[int, str]], staged: set[str]) -> list[Finding]:
    """Warn (never block) when a newly added open item in a daily file has no
    `action-items/context/<TAG>.md` note in the working tree or staged set."""
    if not _DAILY.match(rel):
        return []
    out: list[Finding] = []
    for n, text in added:
        m = _OPEN_TASK.match(text)
        tm = leading_prefix_pattern().match(m.group(1)) if m else None
        if tm is None:
            continue
        note = context_note_rel(tm.group(1))
        if note in staged or (repo / note).exists():
            continue
        out.append(Finding(rel, "missing-context-note", False, f"line {n}: [#{tm.group(1)}] has no {note}", n))
    return out
```

In `lint_staged`, materialise the staged list once and call the check after `_check_lines`:

```python
    staged_list = staged_paths(repo)
    staged_rels = {sp.rel for sp in staged_list}
    for sp in staged_list:
        ...
        result.findings += _check_lines(sp.rel, added, cfg)
        result.findings += _check_context_notes(repo, sp.rel, added, staged_rels)
```

(Replace the loop header `for sp in staged_paths(repo):` with the two lines above plus `for sp in staged_list:`. Keep the loop body as it is apart from the one added line.)

If importing `scout.action_items.context_notes` from `scout.kb.lint` creates an import cycle (`context_notes` imports `scout.kb.lint_config`, not `lint`, so it shouldn't), define `context_note_rel` inline in `lint.py` instead and leave a `# keep in sync with action_items.context_notes` comment. Record the choice as a ruling.

- [ ] **Step 4: Run the lint suite**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_kb_lint.py tests/unit/test_kb_lint_rules.py tests/unit/test_kb_lint_config.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add plugin/engine/scout/kb/lint.py plugin/engine/tests/unit/test_kb_lint.py
git commit -m "feat(kb-lint): warn when a new action item has no context note (never blocks)"
```

---

### Task 3: Phase text — sessions write the note

**Files:**
- Modify: `plugin/phases/core/write-protocol.md:19`
- Modify: `plugin/phases/core/action-items.md` ("Item line format" section, ~lines 414–420; reconciliation step 5, ~lines 406–410)
- Test: `plugin/engine/tests/unit/test_kb_write_protocol_assembly.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_kb_write_protocol_assembly.py`:

```python
@pytest.mark.parametrize("kind", ["SKILL", "DREAMING", "RESEARCH"])
def test_action_items_have_context_notes(kind: str, tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path), kind)
    assert "action-items/context/<TAG>.md" in text


def test_briefing_writes_context_to_the_note(tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path), "SKILL")
    assert "Context lives in the item's own note" in text
    assert "write or update the topic/project note and link it" not in text
    assert "a re-tag renames the note" in text.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_kb_write_protocol_assembly.py -q`
Expected: the new tests FAIL.

- [ ] **Step 3: Edit the phase text**

`plugin/phases/core/write-protocol.md`, replace the action-item row of the table with:

```markdown
| Something {{USER_NAME}} must do | **One action-item line** in today's daily file (see the action-items phase) **plus its context note** `action-items/context/<TAG>.md`: what and why, status, blocker, evidence, links to project/topic/source notes. Edit the note in place. | line ≤ 300 chars + one sub-bullet; note ≤ 15 KB |
```

`plugin/phases/core/action-items.md`, "Item line format": replace the paragraph *"Context lives in the linked note, not under the item. If an item needs more than a line of explanation, write or update the topic/project note and link it."* with:

```markdown
**Context lives in the item's own note**, `action-items/context/<TAG>.md`, never in extra sub-bullets. Create it in the same commit as the item. Frontmatter: `tag`, `title` (plain text), `created`, `updated` (YYYY-MM-DD). Body: what this is and why, current status, what blocks it, the evidence (quotes, timestamps, ticket and PR state), and links to the project, topic and source notes that hold the durable knowledge. When the item gains context, update the note (and its `updated` date), not the daily file. Carry-forward never touches the note. **A re-tag renames the note** to the new tag in the same commit (`git mv`) — the one exception to "never rename or move existing files". The `→ [[…]]` slot may point at the note as `→ [[action-items/context/<TAG>|context]]`; it is optional, because the apps find the note by tag.
```

Reconciliation step 5, "Write with full context and evidence": change the bullet *"If not started: include the full context from all sources, not just the one that surfaced it"* to:

```markdown
- If not started: write the full context from all sources into the item's note (`action-items/context/<TAG>.md`), not just the one that surfaced it
```

- [ ] **Step 4: Run the assembly and phase suites**

Run: `cd plugin/engine && .venv/bin/pytest tests/unit/test_kb_write_protocol_assembly.py tests/unit/test_phase_assembly.py tests/unit/test_phase_backport.py -q`
Expected: all pass. If `test_phase_backport` compares against a recorded assembled snapshot, regenerate it with the command named in its failure message and commit the regenerated file in the same commit.

- [ ] **Step 5: Commit**

```bash
git add plugin/phases/core/write-protocol.md plugin/phases/core/action-items.md plugin/engine/tests/unit/test_kb_write_protocol_assembly.py
git commit -m "docs(phases): action items keep their context in action-items/context/<TAG>.md"
```

---

### Task 4: App — `TaskContextNote` loader

**Files:**
- Create: `apps/macos/Scout/ActionItems/TaskContextNote.swift`
- Test: `apps/macos/ScoutTests/ActionItems/TaskContextNoteTests.swift`

**Interfaces:**
- Produces: `TaskContextNote.isValidTag(_ tag: String) -> Bool`,
  `TaskContextNote.url(for tag: String, scoutDirectory: URL) -> URL?`,
  `TaskContextNote.load(tag: String?, scoutDirectory: URL) -> String?`,
  `TaskContextNote.stripFrontmatter(_ text: String) -> String`.

- [ ] **Step 1: Write the failing tests**

```swift
import Testing
import Foundation
@testable import Scout

@Suite("Task context note — loader")
struct TaskContextNoteTests {
    private func vault() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("ctx-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: dir.appendingPathComponent("action-items/context"), withIntermediateDirectories: true)
        return dir
    }

    private func write(_ text: String, tag: String, in vault: URL) throws {
        try text.write(to: vault.appendingPathComponent("action-items/context/\(tag).md"),
                       atomically: true, encoding: .utf8)
    }

    @Test func urlForValidTag() throws {
        let v = try vault()
        #expect(TaskContextNote.url(for: "DETX", scoutDirectory: v)?.path
                == v.appendingPathComponent("action-items/context/DETX.md").path)
    }

    @Test(arguments: ["../X", "ab", "TOOLONGTAG", "1234", "DE/TX", "DETX.md", ""])
    func badTagsReadNothing(tag: String) throws {
        let v = try vault()
        try write("secret", tag: "DETX", in: v)
        #expect(TaskContextNote.url(for: tag, scoutDirectory: v) == nil)
        #expect(TaskContextNote.load(tag: tag, scoutDirectory: v) == nil)
    }

    @Test func loadStripsFrontmatter() throws {
        let v = try vault()
        try write("---\ntag: DETX\ntitle: \"Order\"\n---\n\nWaiting on Priya.\n", tag: "DETX", in: v)
        #expect(TaskContextNote.load(tag: "DETX", scoutDirectory: v) == "Waiting on Priya.")
    }

    @Test func noFrontmatterKeepsWholeText() throws {
        let v = try vault()
        try write("Just a body.\n", tag: "PLN", in: v)
        #expect(TaskContextNote.load(tag: "PLN", scoutDirectory: v) == "Just a body.")
    }

    @Test func frontmatterOnlyIsNil() throws {
        let v = try vault()
        try write("---\ntag: PLN\n---\n", tag: "PLN", in: v)
        #expect(TaskContextNote.load(tag: "PLN", scoutDirectory: v) == nil)
    }

    @Test func missingFileAndNilTagAreNil() throws {
        let v = try vault()
        #expect(TaskContextNote.load(tag: "NESTX", scoutDirectory: v) == nil)
        #expect(TaskContextNote.load(tag: nil, scoutDirectory: v) == nil)
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/macos && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/TaskContextNoteTests -resultBundlePath "$TMPDIR/xcr-$RANDOM" 2>&1 | grep -E "error:|TEST (SUCC|FAIL)" | head`
Expected: build fails with `cannot find 'TaskContextNote' in scope`.

- [ ] **Step 3: Implement**

Create `apps/macos/Scout/ActionItems/TaskContextNote.swift`:

```swift
import Foundation

/// An action item's context note, `<vault>/action-items/context/<TAG>.md`.
/// The daily file keeps one line per item; this note holds its status,
/// evidence and links, and goes into Full context / Launch Claude. Read only
/// on an explicit action — never during the parse or the first paint.
nonisolated enum TaskContextNote {
    /// The `[#TAG]` recognition grammar: 2–8 of `[A-Z0-9]`, at least one
    /// letter. Checked here too, independent of the parser, so no other
    /// string can become a path.
    static func isValidTag(_ tag: String) -> Bool {
        guard (2...8).contains(tag.unicodeScalars.count) else { return false }
        var hasLetter = false
        for scalar in tag.unicodeScalars {
            switch scalar {
            case "A"..."Z": hasLetter = true
            case "0"..."9": continue
            default: return false
            }
        }
        return hasLetter
    }

    static func url(for tag: String, scoutDirectory: URL) -> URL? {
        guard isValidTag(tag) else { return nil }
        return scoutDirectory
            .appendingPathComponent("action-items", isDirectory: true)
            .appendingPathComponent("context", isDirectory: true)
            .appendingPathComponent("\(tag).md", isDirectory: false)
    }

    /// The note body without frontmatter, trimmed; nil when there's no tag,
    /// no file, it isn't UTF-8, or nothing is left after the frontmatter.
    static func load(tag: String?, scoutDirectory: URL) -> String? {
        guard let tag, let url = url(for: tag, scoutDirectory: scoutDirectory),
              let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .utf8) else { return nil }
        let body = stripFrontmatter(text).trimmingCharacters(in: .whitespacesAndNewlines)
        return body.isEmpty ? nil : body
    }

    static func stripFrontmatter(_ text: String) -> String {
        let lines = text.components(separatedBy: "\n")
        guard lines.first?.trimmingCharacters(in: .whitespaces) == "---",
              let end = lines.indices.dropFirst().first(where: {
                  lines[$0].trimmingCharacters(in: .whitespaces) == "---"
              })
        else { return text }
        return lines[(end + 1)...].joined(separator: "\n")
    }
}
```

- [ ] **Step 4: Run to verify it passes**

Run: the Step 2 command.
Expected: `** TEST SUCCEEDED **`, 12 test cases (6 tests, 7 arguments for one of them).

- [ ] **Step 5: Commit**

```bash
git add apps/macos/Scout/ActionItems/TaskContextNote.swift apps/macos/ScoutTests/ActionItems/TaskContextNoteTests.swift
git commit -m "feat(action-items): load an item's context note by tag"
```

---

### Task 5: App — prompts include the note

**Files:**
- Modify: `apps/macos/Scout/Utilities/ClaudeLauncher.swift` (the `prompt` overloads and `fullContextBody`)
- Modify: `apps/macos/Scout/ActionItems/Views/TaskActionsView.swift` (`copyTaskPrompt`), `apps/macos/Scout/ActionItems/Views/LaunchClaudeMenu.swift` (`launch`), `apps/macos/Scout/ActionItems/ActionItemsView.swift` (`copySelected`)
- Test: `apps/macos/ScoutTests/ActionItems/ClaudeLauncherPromptTests.swift`

**Interfaces:**
- Consumes: `TaskContextNote.load(tag:scoutDirectory:)` (Task 4).
- Produces: `ClaudeLauncher.prompt(for: ActionTask, contextNote: String? = nil)`,
  `ClaudeLauncher.prompt(for: ActionTask, format: CopyFormat, contextNote: String? = nil)`,
  `ClaudeLauncher.prompt(for: [ActionTask], format: CopyFormat, contextNotes: [UUID: String] = [:])`.

- [ ] **Step 1: Write the failing tests**

Append to `ClaudeLauncherPromptTests` (before `makeTask`):

```swift
    @Test func fullContextPlacesNoteAfterDetailsBeforeComments() throws {
        let task = makeTask(
            plainSubject: "Order the roadmap items",
            comments: [TaskComment(author: "alex", timestamp: "", text: "Agreed.")],
            deepLinks: [.linear(id: "PROJ-1234")],
            details: [TaskDetail(depth: 0, text: "Due Tuesday.")]
        )
        let out = ClaudeLauncher.prompt(for: task, contextNote: "Waiting on Priya.\n\n- Ticket never left Todo.")
        #expect(out.contains("Context note:\nWaiting on Priya.\n\n- Ticket never left Todo."))
        let details = try #require(out.range(of: "Context:\n"))
        let note = try #require(out.range(of: "Context note:"))
        let comments = try #require(out.range(of: "Prior comments:"))
        let links = try #require(out.range(of: "Links:"))
        #expect(details.lowerBound < note.lowerBound)
        #expect(note.lowerBound < comments.lowerBound)
        #expect(comments.lowerBound < links.lowerBound)
    }

    @Test func nilOrBlankNoteLeavesPromptUnchanged() {
        let task = makeTask(plainSubject: "Cut release", body: "Tag by EOD.",
                            details: [TaskDetail(depth: 0, text: "Mobile team waits.")])
        let baseline = ClaudeLauncher.prompt(for: task)
        #expect(ClaudeLauncher.prompt(for: task, contextNote: nil) == baseline)
        #expect(ClaudeLauncher.prompt(for: task, contextNote: "  \n") == baseline)
        #expect(!baseline.contains("Context note:"))
    }

    @Test func conciseAndChecklistIgnoreTheNote() {
        let task = makeTask(plainSubject: "Order the items", body: "Due Tuesday.")
        #expect(ClaudeLauncher.prompt(for: task, format: .concise, contextNote: "long note")
                == ClaudeLauncher.prompt(for: task, format: .concise))
        #expect(ClaudeLauncher.prompt(for: task, format: .markdownChecklist, contextNote: "long note")
                == ClaudeLauncher.prompt(for: task, format: .markdownChecklist))
    }

    @Test func multiTaskMapsNotesPerTask() {
        let a = makeTask(plainSubject: "First item")
        let b = makeTask(plainSubject: "Second item")
        let out = ClaudeLauncher.prompt(for: [a, b], format: .fullContext, contextNotes: [b.id: "Only for B."])
        let first = out.components(separatedBy: "\n\n---\n\n")
        #expect(first.count == 2)
        #expect(!first[0].contains("Context note:"))
        #expect(first[1].contains("Context note:\nOnly for B."))
    }
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd apps/macos && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ClaudeLauncherPromptTests -resultBundlePath "$TMPDIR/xcr-$RANDOM" 2>&1 | grep -E "error:|TEST (SUCC|FAIL)" | head`
Expected: build fails, `extra argument 'contextNote' in call`.

- [ ] **Step 3: Implement in `ClaudeLauncher`**

Replace the three `prompt` functions and `fullContextBody` signature with:

```swift
    static func prompt(for task: ActionTask, contextNote: String? = nil) -> String {
        prompt(for: task, format: .fullContext, contextNote: contextNote)
    }

    static func prompt(for task: ActionTask, format: CopyFormat, contextNote: String? = nil) -> String {
        switch format {
        case .fullContext:
            return "Help me make progress on this action item:\n\n"
                + fullContextBody(for: task, contextNote: contextNote)
        case .concise:
            return conciseBody(for: task)
        case .markdownChecklist:
            return checklistBody(for: task)
        }
    }

    static func prompt(
        for tasks: [ActionTask], format: CopyFormat, contextNotes: [UUID: String] = [:]
    ) -> String {
        guard let first = tasks.first else { return "" }
        guard tasks.count > 1 else { return prompt(for: first, format: format, contextNote: contextNotes[first.id]) }

        switch format {
        case .fullContext:
            let items = tasks.enumerated().map { index, task in
                "## \(index + 1). \(fullContextBody(for: task, contextNote: contextNotes[task.id]))"
            }
            return "Help me make progress on these \(tasks.count) action items:\n\n"
                + items.joined(separator: "\n\n---\n\n")
        case .concise:
            return tasks.enumerated()
                .map {
                    let body = conciseBody(for: $0.element)
                        .replacingOccurrences(of: "\n", with: "\n   ")
                    return "\($0.offset + 1). \(body)"
                }
                .joined(separator: "\n\n")
        case .markdownChecklist:
            return tasks.map { checklistBody(for: $0) }.joined(separator: "\n")
        }
    }
```

(The `.concise` and `.markdownChecklist` bodies above are the existing code, unchanged. Only the signature and the `.fullContext` case are new.)

In `fullContextBody(for task: ActionTask, contextNote: String?)`, after the `details` block:

```swift
        if let note = contextNote?.trimmingCharacters(in: .whitespacesAndNewlines), !note.isEmpty {
            out += "\n\nContext note:\n\(note)"
        }
```

- [ ] **Step 4: Load the note at the three call sites**

`TaskActionsView.copyTaskPrompt`:

```swift
            ClaudeLauncher.prompt(
                for: task, format: format,
                contextNote: TaskContextNote.load(tag: task.shortPrefix, scoutDirectory: scoutDirectory)
            ),
```

`LaunchClaudeMenu.launch`:

```swift
            let note = TaskContextNote.load(tag: task.shortPrefix, scoutDirectory: scoutDirectory)
            try ClaudeLauncher.launch(target: target, prompt: ClaudeLauncher.prompt(for: task, contextNote: note))
```

`ActionItemsView.copySelected`:

```swift
        var notes: [UUID: String] = [:]
        for task in tasks {
            if let note = TaskContextNote.load(tag: task.shortPrefix, scoutDirectory: scoutDirectory) {
                notes[task.id] = note
            }
        }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(
            ClaudeLauncher.prompt(for: tasks, format: format, contextNotes: notes),
            forType: .string
        )
```

- [ ] **Step 5: Run the prompt and URL suites**

Run: `cd apps/macos && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ClaudeLauncherPromptTests -only-testing:ScoutTests/ClaudeDesktopURLTests -resultBundlePath "$TMPDIR/xcr-$RANDOM" 2>&1 | grep -E "error:|TEST (SUCC|FAIL)" | head`
Expected: `** TEST SUCCEEDED **`. Every pre-existing prompt test passes unchanged.

- [ ] **Step 6: Commit**

```bash
git add apps/macos/Scout/Utilities/ClaudeLauncher.swift apps/macos/Scout/ActionItems/Views/TaskActionsView.swift apps/macos/Scout/ActionItems/Views/LaunchClaudeMenu.swift apps/macos/Scout/ActionItems/ActionItemsView.swift apps/macos/ScoutTests/ActionItems/ClaudeLauncherPromptTests.swift
git commit -m "feat(launcher): Full context and Launch Claude include the item's context note"
```

---

### Task 6: App — "Context note" section on the card

**Files:**
- Create: `apps/macos/Scout/ActionItems/Views/TaskContextNoteSection.swift`
- Modify: `apps/macos/Scout/ActionItems/Views/TaskCardView.swift` (`detail`, after the `TaskDetailsView` block)
- Test: `apps/macos/ScoutTests/Shell/ComponentSmokeTests.swift`

**Interfaces:**
- Consumes: `TaskContextNote.url(for:scoutDirectory:)`, `TaskContextNote.load(tag:scoutDirectory:)` (Task 4); `MarkdownBodyView(blocks:)`, `MarkdownBodyBlock.blocks(from:)` (existing).
- Produces: `struct TaskContextNoteSection: View { init(tag: String?, scoutDirectory: URL) }`.

- [ ] **Step 1: Write the failing smoke test**

Add to `ComponentSmokeTests` (after `taskDetailsRender`):

```swift
    @Test("the context note section renders with and without a note")
    func contextNoteSectionRenders() throws {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent("ctx-\(UUID().uuidString)")
        let dir = vault.appendingPathComponent("action-items/context")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try "---\ntag: DETX\n---\n\nWaiting on Priya.\n\n```\nscoutctl digest --batch\n```\n"
            .write(to: dir.appendingPathComponent("DETX.md"), atomically: true, encoding: .utf8)
        ViewHost.render(TaskContextNoteSection(tag: "DETX", scoutDirectory: vault), size: cardSize)
        ViewHost.render(TaskContextNoteSection(tag: "NESTX", scoutDirectory: vault), size: cardSize)
        ViewHost.render(TaskContextNoteSection(tag: nil, scoutDirectory: vault), size: cardSize)
    }
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/macos && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ComponentSmokeTests -resultBundlePath "$TMPDIR/xcr-$RANDOM" 2>&1 | grep -E "error:|TEST (SUCC|FAIL)" | head`
Expected: build fails, `cannot find 'TaskContextNoteSection' in scope`.

- [ ] **Step 3: Implement the section**

Create `apps/macos/Scout/ActionItems/Views/TaskContextNoteSection.swift`:

```swift
import SwiftUI
import AppKit

/// The item's context note, collapsed by default. It checks for the file once
/// the card's detail appears and reads it only when expanded, so the list's
/// first paint never touches the disk for notes.
struct TaskContextNoteSection: View {
    let tag: String?
    let scoutDirectory: URL

    @State private var noteURL: URL?
    @State private var isExpanded = false
    @State private var text: String?

    var body: some View {
        Group {
            if let noteURL {
                DisclosureGroup(isExpanded: $isExpanded) {
                    VStack(alignment: .leading, spacing: 8) {
                        if let text {
                            MarkdownBodyView(blocks: MarkdownBodyBlock.blocks(from: text))
                        }
                        Button("Open note") { NSWorkspace.shared.open(noteURL) }
                            .buttonStyle(.link)
                            .font(DS.sans(11.5))
                    }
                    .padding(.top, 6)
                } label: {
                    Text("Context note")
                        .font(DS.sans(12, weight: .medium))
                        .foregroundStyle(DS.Ink.p2)
                }
                .onChange(of: isExpanded) { _, open in
                    if open && text == nil {
                        text = TaskContextNote.load(tag: tag, scoutDirectory: scoutDirectory)
                    }
                }
            }
        }
        .task(id: tag) {
            guard let tag, let url = TaskContextNote.url(for: tag, scoutDirectory: scoutDirectory),
                  FileManager.default.fileExists(atPath: url.path) else {
                noteURL = nil
                return
            }
            noteURL = url
        }
    }
}
```

In `TaskCardView.detail`, after `if !task.details.isEmpty { TaskDetailsView(details: task.details) }`:

```swift
            TaskContextNoteSection(tag: task.shortPrefix, scoutDirectory: scoutDirectory)
```

- [ ] **Step 4: Run the smoke suites**

Run: `cd apps/macos && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ComponentSmokeTests -only-testing:ScoutTests/LeafSmokeTests -resultBundlePath "$TMPDIR/xcr-$RANDOM" 2>&1 | grep -E "error:|TEST (SUCC|FAIL)" | head`
Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 5: Commit**

```bash
git add apps/macos/Scout/ActionItems/Views/TaskContextNoteSection.swift apps/macos/Scout/ActionItems/Views/TaskCardView.swift apps/macos/ScoutTests/Shell/ComponentSmokeTests.swift
git commit -m "feat(action-items): collapsed Context note section on the card"
```

---

### Task 7: Verification and PR

**Files:** none changed unless a check fails.

- [ ] **Step 1: Full engine suite with coverage**

Run: `cd plugin/engine && .venv/bin/pytest tests/ --cov --cov-report=term-missing -q 2>&1 | tail -15`
Expected: all pass. Total coverage ≥ 98%, and `scout/action_items/context_notes.py` at 100%.

- [ ] **Step 2: Full app suite**

Run: `cd apps/macos && xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests -resultBundlePath "$TMPDIR/xcr-full" 2>&1 | grep -E "TEST (SUCC|FAIL)|Test run with" ; rm -rf "$TMPDIR/xcr-full" "$TMPDIR/xcr-full.xcresult"`
Expected: `** TEST SUCCEEDED **`, ~1,020 tests (1,002 before plus the new ones).

- [ ] **Step 3: Dry run against a COPY of the real vault (read-only)**

```bash
S="$TMPDIR/ctx-dryrun"
shasum -a 256 ~/Scout/action-items/action-items-2026-10-06.md ~/Scout/action-items/backlog.md ~/Scout/action-items/archive/action-items-2026-10-05-pre-restructure.md > "$TMPDIR/ctx-before.sha"
mkdir -p "$S/vault-copy/action-items/archive" && cp ~/Scout/action-items/action-items-2026-10-06.md ~/Scout/action-items/backlog.md "$S/vault-copy/action-items/" && cp ~/Scout/action-items/archive/action-items-2026-10-05-pre-restructure.md "$S/vault-copy/action-items/archive/" && cp ~/Scout/scout-config.yaml "$S/vault-copy/"
cd plugin/engine && .venv/bin/scoutctl action-items backfill-context --from "$S/vault-copy/action-items/archive/action-items-2026-10-05-pre-restructure.md" --daily "$S/vault-copy/action-items/action-items-2026-10-06.md"
```

Expected: about 790–800 notes would be written, a short "no snapshot entry" list (items created after Oct 5), and about 2 over-budget lines. Record the numbers for the PR. Then run with `--write` **on the copy** and open one cut item's note in the copy to check it by eye: frontmatter, provenance line, and the full original block. Finally:

```bash
shasum -a 256 -c "$TMPDIR/ctx-before.sha" && rm -rf "$S" "$TMPDIR/ctx-before.sha"
```

Expected: all three `OK`. The real vault is untouched.

- [ ] **Step 4: Leave the GUI check to the rollout**

The app has no vault override, so the in-app check (expand a cut item, open "Context note", **Copy → Full context** shows `Context note:` after `Context:`) runs after Jordan's real `--write` backfill, in the Debug build. List it in the PR under "Rollout (Jordan)". Don't point the app at the real vault before then.

- [ ] **Step 5: PR**

Push `feat/action-item-context-notes`, update #325's title and body (spec + plan + implementation, the dry-run numbers, and the rollout steps Jordan owns: `/scout-update`, then the real `--write` backfill with the `kb_budgets` lines it prints), and mark it ready for review.
