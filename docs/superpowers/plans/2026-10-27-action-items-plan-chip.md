# Action Items plan chip: implementation plan

**Goal:** `/scout-plan` markers leave the engine's dashboard comments and the Mac app's comment list in one commit, and the task card shows a plan chip (spec: `docs/superpowers/specs/2026-10-27-action-items-plan-chip-design.md`).

**Architecture:** `plan_marks._mark_lines` stays the single definition of a marker. The engine's renderer asks it which lines to skip; the comment lister keeps counting them, so `--index` means the same to every app version. The app's parser mirrors the same grammar and window, and its comment writes add the markers above a comment back into `--index`.

### Task 1: Engine

- [x] Failing tests first: the renderer drops markers while the lister keeps them, unparseable, duplicate, blank-line and child-task cases, and `delete-comment` / `edit-comment --index` with the index the app sends.
- [x] `plan_marks.plan_mark_line_numbers`; `render.parse` skips those lines.
- [x] Docstrings in `plan_marks.py` and `_common.py` (why the lister keeps the markers).

### Task 2: Corpus

- [x] Marker entries with `expected.plan` and `expected.comment_authors`; `_doc` names the new fields.
- [x] `test_parser_contract.py`: `comment_authors` for every entry that has it, `plan` via `read_plan_marks`.
- [x] Both checksums, copy to `apps/macos/ScoutTests/Fixtures/`.

### Task 3: App model and parser

- [x] `Models/TaskPlan.swift` (from the earlier app branch), grammar aligned with the engine: optional raw, first parseable line per key wins, reversed block accepted, marker positions for the engine index.
- [x] `ActionTask.plan`, `replacingPlan`, the plan carried through `replacingDetails`, `replacingFields` and the five rebuilds in `ActionItemsParser.swift`.
- [x] Marker branch ahead of the dash comment branch, with its own sub-list window.

### Task 4: App card and writes

- [x] Chip in `TaskCardView.trailingStatus`, `Done · <actual>`.
- [x] `ActionTask.commentSelector(at:)` sends `TaskPlan.engineCommentIndex`, no engine probe.

### Task 5: Verify

- [x] `pytest` with coverage (also with `FORCE_COLOR=1`), ruff, mypy.
- [x] App tests against `main`: test count, coverage floor, no new warnings.
- [x] `scoutctl` from this branch on a copy of a real daily file: delete and edit by index under markers hit the right line.
- [x] CHANGELOG entries under Unreleased in `plugin/` and `apps/macos/`.
