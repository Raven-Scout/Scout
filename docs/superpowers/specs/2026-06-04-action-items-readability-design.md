# Action Items readability — collapsible task cards

**Date:** 2026-06-04
**Issue/origin:** Adam's idea, inspired by `~/Scout/ai-showcase-triage-2026-06-04.html`.

## Problem

The Action Items List renders every task via `TaskCardView` with all content
expanded at once — title, full body, every comment, deep-link pills, the
actions row, and a comment composer. A full day of tasks reads as a wall of
text; it's hard to scan and decide what to act on.

## Inspiration

The triage artifact is scannable because of:
- discrete **cards** with a colored **left-border priority stripe**;
- small **source/context chips** ("who" pills);
- **issue pills** with status badges;
- an `▸ Evidence` **progressive-disclosure drawer** that hides dense detail by
  default.

## Decision (approved)

Adopt the artifact's *structure*, not its indigo/sans skin. Keep Scout's
editorial design language (serif titles, warm paper tones, `DS` tokens).

Rework **only** `TaskCardView` (List mode). Untouched: Board mode
(`BoardCardView`), Focus/Meetings/Digest/Done sections, the dateline, preamble
cards, filters.

### Card chrome
Each top-level task is a discrete card: `DS.Paper.raised` fill, hairline
border, 8pt corners, a priority-colored left stripe
(`DS.priorityColor(effectiveKind)`), ~10pt inter-card spacing. Replaces the
gutter-dot + hairline-separated flat entries. Nested subtasks
(`indentLevel > 0`) render as lighter indented rows within the parent's
expanded region.

### Collapsed header (always visible)
- left stripe · `#PREFIX` chip (if present) · title (serif medium,
  `lineLimit(2)`)
- trailing: status badge (Done ✓ / snooze moon + date) · compact **quick
  actions** (Done toggle, Snooze) · chevron
- chip row beneath the title derived from `task.deepLinks` + `carriedInFrom`:
  e.g. `2 PRs` · `keboola/mcp-server` · `GitHub`, `Linear`, `Slack thread`,
  `carried Jun 2`

### Expanded body (behind chevron / title tap)
Full `InlineMarkdownText` body, `CommentListView`, `TaskLinksView`, the full
`TaskActionsView` (Done/Snooze/**Launch Claude**), and the comment composer —
today's content minus what the header promotes. Compact quick-actions show only
while collapsed (no duplication when expanded).

### Expansion state
`@State` per card, initialized by kind: `.urgent` → expanded; everything else →
collapsed. Chevron and title-tap toggle. The Done/Snooze/chevron buttons are
siblings of the title's tap gesture so taps don't conflict.

## New code
- `TaskChip` model + `TaskChip.chips(for:)` — pure function mapping a task's
  deep links + carry marker into chip labels. Unit-tested.
- Small chip + quick-action subviews inside `TaskCardView`.

## Out of scope
- Drag/restatus, board changes, producer/markdown changes.
- The right-hand meta rail (carry/until/line) is folded into chips; the raw line
  number is dropped from the collapsed view (kept only as before in code, not
  surfaced).

## Verification
- All writes still go through `runOp` → `onOp`; existing tests still apply.
- New unit test for `TaskChip.chips(for:)`.
- Build + full suite green before release.
- Visual confirmation limited by Screen Recording being disabled on the dev
  machine — to be flagged in the PR.
