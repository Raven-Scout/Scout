# Action Items: `/scout-plan` markers stop being comments, and a plan chip on the card

**Date:** 2026-10-27
**Status:** Design, implemented on this branch, for review
**Surface:** engine `scout/action_items/_common.py`, `render.py`, `plan_marks.py`, `scout/manifest.py`; Scout.app `apps/macos/` (parser, `ActionTask`, task card, comment writes); `parser-corpus.json`

## Context

`/scout-plan` (#264, shipped in 0.14.0) writes three machine sub-bullets under a planned task:

```
- [ ] [#PROJ1] **Draft the rollout note**
  - estimate: 45m (raw: 30m, kind: deep)
  - block: 2026-09-30 10:00-10:45 (event: <calendar event id>)
  - actual: 1h (2026-09-30)
```

Every client still reads them as comments from authors `estimate`, `block` and `actual`, and so does the engine's comment lister, on purpose: `delete-comment` and `edit-comment --index N` take the index the app shows, so the engine and the app have to count the same lines. The review of #264 settled that the carve-out lands in the engine and the Mac app together (`docs/superpowers/specs/2026-09-29-scout-plan-design.md`, Out of scope).

## Goal

A planned task shows its block, estimate and actual time as one chip on the card, and the markers never show up as comments, in the app or in the engine. A comment index means the same line to the app and to the engine, also when the app runs against an engine from before this change.

## Design

### One rule for what is a marker

A line is a marker of a task exactly when `plan_marks` reads it as one. That rule already exists in `plan_marks._mark_lines`, and this change only makes the other readers use it:

1. The line sits in the task's sub-list: after the task line, before the first blank line, unindented line or other task line (at any depth, so a parent never claims a child's markers).
2. It matches `- estimate|block|actual: <value>` and the value parses: a positive `h`/`m` duration for estimate and actual, a `YYYY-MM-DD HH:MM-HH:MM` window for block. `(raw: …, kind: …)`, `(kind: …)`, `(event: …)` and `(YYYY-MM-DD)` tails are optional.
3. The first line per key that passes 2 wins. A later duplicate, and anything that does not parse (`- block: waiting on Priya`), stays a comment.

A reversed block (`11:00-10:00`) passes 2: the engine has always accepted it, and the app now does too.

### Engine

- `plan_marks.plan_mark_line_numbers(lines, task_line_number)` returns the marker lines of one task.
- `list_comment_lines` (behind `delete-comment` and `edit-comment`) skips those lines.
- The HTML renderer (`render.parse`) skips them too, so they are no longer comments on the dashboard.
- The manifest advertises `comments_skip_plan_marks_v1`.

### Mac app

- **Model.** `TaskPlan` on `ActionTask.plan`: estimate, raw estimate, kind, block (`PlannedBlock`), actual and its date. Default `.none`, so existing call sites compile unchanged. `replacingPlan(_:)` sits next to `replacingDetails` and `replacingFields`, and every place that rebuilds a task carries the plan.
- **Parser.** A marker branch runs ahead of the dash comment branch, with the grammar and the three rules above. The parser tracks the sub-list window itself (a blank or unindented line closes it, a task line opens a new one), because the app otherwise binds comments to the last task across blank lines.
- **Card.** The trailing status slot shows a clock chip when an open, unsnoozed task has a plan: `10:00-10:45 · 45m`, the weekday first when the block is not today, `· took 1h` once the actual is known, and `45m` alone for an estimate without a block. A done task reads `Done · 1h`. The tooltip spells out date, estimate, raw estimate, kind and actual.
- **Comment index against an older engine.** The parser records, for each marker, how many comments came before it. When a comment has markers above it, the card sends `CommentSelector.indexAfterPlanMarks(n, legacyIndex: m)`, where `m` counts those markers the way an older engine does. The writer asks `scoutctl manifest show` once (a yes is remembered, as `BoardWriter` does for `topic_board_v1`) and passes `--index n` when the engine has `comments_skip_plan_marks_v1`, else `--index m`. A comment with no marker above it has the same index on both engines and skips the probe.

### Parser contract

`parser-corpus.json` gains marker entries with `expected.plan` and `expected.comment_authors`. Python asserts `comment_authors` for every entry that has it (until now only for field entries) and `plan` through `read_plan_marks`. Swift asserts both.

## Decisions

| Fork | Choice |
|---|---|
| Older engine | The app translates the index instead of falling back to showing markers as comments. The chip works with any engine, and the parse does not depend on a subprocess probe. |
| A marker after a blank line | A comment, in both. The engine's scan has always stopped there. |
| Engine renderer | Skips markers, renders no chip. The dashboard is not where planning happens. |
| Board cards and the display profile | Unchanged here. `actionItems.fields.plan` from the display-profile spec arrives as a follow-up once the profile code is on `main`. |

## iOS

scout-iOS-app parses the same sub-bullets and shows the markers as comments. It only adds comments, never deletes or edits one by index, so there is no wrong-line risk there, only noise. Its corpus copy and the same parser carve-out go in a separate scout-iOS-app PR.

## Testing

- Engine: `tests/unit/test_action_items_plan_marks.py` (lister and renderer skip exactly the markers, unparseable values and duplicates stay comments, scope ends at blank lines and child tasks; delete and edit by index hit the right line under markers), manifest flag, corpus.
- App: `ScoutTests/ActionItems/PlanMarksTests.swift` (grammar parity with the engine, plan survives every later sub-bullet, chip label and tooltip, legacy index), writer tests for the probe and both engines, `ParserContractTests`.
