# Action Items plan chip: implementation plan

**Goal:** `/scout-plan` markers stop being comments in the engine and the Mac app in one commit, and the task card shows a plan chip (spec: `docs/superpowers/specs/2026-10-27-action-items-plan-chip-design.md`).

**Architecture:** `plan_marks._mark_lines` stays the single definition of a marker. The engine's comment lister and renderer ask it which lines to skip. The app's parser mirrors the same grammar and window, and the app's comment writes translate the index for an engine without `comments_skip_plan_marks_v1`.

### Task 1: Engine

- [x] Failing tests first: flip `test_plan_marks_still_count_as_comments_like_the_apps`, add unparseable, duplicate, blank-line and child-task cases, and `delete-comment` / `edit-comment --index` on a task with markers.
- [x] `plan_marks.plan_mark_line_numbers`; `list_comment_lines` and `render.parse` skip those lines.
- [x] `comments_skip_plan_marks_v1` in `manifest.py`, with its test.
- [x] Docstrings in `plan_marks.py` and `_common.py`.

### Task 2: Corpus

- [x] Marker entries with `expected.plan` and `expected.comment_authors`; `_doc` names the new fields.
- [x] `test_parser_contract.py`: `comment_authors` for every entry that has it, `plan` via `read_plan_marks`.
- [x] Both checksums, copy to `apps/macos/ScoutTests/Fixtures/`.

### Task 3: App model and parser

- [x] `Models/TaskPlan.swift` (from the earlier app branch), grammar aligned with the engine: optional raw, first parseable line per key wins, reversed block accepted, marker positions for the legacy index.
- [x] `ActionTask.plan`, `replacingPlan`, the plan carried through `replacingDetails`, `replacingFields` and the five rebuilds in `ActionItemsParser.swift`.
- [x] Marker branch ahead of the dash comment branch, with its own sub-list window.

### Task 4: App card and writes

- [x] Chip in `TaskCardView.trailingStatus`, `Done · <actual>`.
- [x] `CommentSelector.indexAfterPlanMarks`, chosen by the card; `ActionItemsWriter` probes the manifest through `EngineFeatures` only for that case.

### Task 5: Verify

- [x] `pytest` with coverage (also with `FORCE_COLOR=1`), ruff, mypy.
- [x] App tests against `main`: test count, coverage floor, no new warnings.
- [x] `scoutctl` from this branch on a copy of a real daily file: delete and edit by index under markers hit the right line.
- [x] CHANGELOG entries under Unreleased in `plugin/` and `apps/macos/`.
