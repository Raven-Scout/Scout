# Control Center & Action Items — bug catalogue

Captured 2026-05-17 from user testing of the 0.4.x dev build. This is the
working list for the next pass of the revamp; treat the depth of the bugs
listed below as the spec, not a polish backlog. Visual changes are *follow-ups*
to data correctness, not a substitute.

## High-impact data correctness bugs

### CC-1. Stale "running" detection fails for non-research/dreaming runs

**Where:** NowStripView hero · SessionsListView · status pulse
**Symptom:** Dreaming session shown as `running · started 4 hours ago` long
after it clearly finished. Sessions list shows Dreaming (4:22 PM) and
Consolidation (3:09 PM) both as `running` simultaneously — impossible.
**Root cause hypothesis:** `SessionLogService.sweepStaleRunningTo…` only
sweeps `research` and `dreaming` running states older than some threshold,
and the threshold is probably too generous; consolidation has no sweep path
at all. The orphan-detection logic also probably needs to look at whether a
newer session of the same `type` has succeeded since this one started
(if a later briefing succeeded, an earlier briefing was definitely not still
running).
**Fix direction:**
  - Generalize the stale-running sweep to **all** run types.
  - Tighten the cutoff to ~15-30 min of inactivity (file mtime on the
    session's jsonl), or use the engine's known max-duration per type.
  - Add the "newer-success-of-same-type wins" rule so a 4 h old "running"
    automatically demotes to `orphaned` when a fresher run of the same
    `type` succeeded.
  - May require a small scoutctl-side change to emit a definitive
    "session ended" line.

### CC-2. "Next up" skips closer runs

**Symptom:** Hero says `Next up: Consolidation in 21 hours · dispatcher armed`.
But morning briefing should fire in ~12 h and another dreaming session should
fire tonight; both are closer than 21 h.
**Root cause hypothesis:** `NowStripView.nextColumn` does
`upcoming.first { $0.type != .manual }`, so the *first* item in the
`upcoming` array wins regardless of chronological position. Tying back to
CC-3, `upcoming` is not sorted by `scheduledAt`.
**Fix direction:**
  - Sort `upcoming` by `scheduledAt` ascending in `ScheduleService` (or
    upstream in scoutctl) before publishing.
  - In `nextColumn`, after sort, take `first` whose `scheduledAt > now`.
  - Display `relative(presentation: .named)` based on `scheduledAt`, not
    on whatever the upcoming order happened to be.

### CC-3. Heartbeat schedule rows are out of chronological order

**Symptom:** Heartbeat table shows 5pm May 18, 6:30pm May 18, **10pm May 17**,
7pm May 18, 1pm May 18, 8am May 18 — random, with the next-occurring
morning briefing at the bottom instead of the top.
**Root cause hypothesis:** Same as CC-2 — `upcoming` array from scoutctl is
not sorted. We render `upcoming.filter { $0.type != .manual }` in
insertion order.
**Fix direction:**
  - Sort by `scheduledAt` ascending at the service boundary.
  - Drop past entries (`scheduledAt < now`) so May 17 10pm doesn't appear
    when we're past 10pm.
  - Limit shown rows to ~4 (currently 6) and shrink row vertical padding
    so the section takes less of the page.
  - The "morning run" / "rollup + tagging" subtitle is fine but the row
    height is too generous; tighten to ~24-26 px and drop the in-row
    "queued" pill (status is implicit when it hasn't fired yet).

### CC-5. Sessions list shows finished sessions as "running"

**Symptom:** Two May 17 sessions shown as `● running` when both definitely
finished. Same root cause as CC-1.
**Note:** When fixed, the stale running entries should re-classify to
`orphaned` (we have a category for this; just need the sweep to find them).
Make sure the "Now" hero re-derives off the *resolved* latest run, not the
raw latest, so the hero doesn't latch onto an orphan.

### CC-7. Connector health percentages disagree with the chart cells

**Symptom:** Gmail/Calendar/Granola/Drive/GitHub all show `100%` on the 7d
column even when the per-run cells (`r1..r5`) don't all have checks
(Calendar has only 3 checks but says 100%, Granola has 2 checks but says
100%). Meanwhile Slack and Linear show `0%` even though the user knows
they connected recently.
**Root cause hypothesis (two distinct bugs):**
  1. The 7d percentage column is computed off a different (or stale) sample
     than the per-run cells. The math is divorced from what's rendered.
  2. The connector-health roster is keyed in a way that's losing Slack and
     Linear's recent successes (probably roster-key vs. log-key mismatch
     after the connector renamed itself — Slack used to be `slack-mcp`,
     now it's `slack`, etc.).
**Fix direction:**
  - Make the per-cell ticks and the 7d% read off the same `ConnectorHealthMatrix`
    record. If r1..r5 only has 3 successes, the column must read 60%, not 100%.
  - Audit `ConnectorHealthService` for roster-key normalization. Make
    connector lookup tolerant of historical aliases.
  - Add **Claude Code sessions** as a tracked source (new row in the matrix);
    likely needs a scoutctl-side change to emit a `cc-session` row in the
    health log on every run.
  - Add a tooltip on each cell showing the actual call timestamp + status.

### CC-4. Activity heatmap defaults to too long a window + no hover

**Symptom:** Defaults to "last 12 months" — too sparse for the actual signal.
Cells aren't hover-interrogatable, so the user can't see *which* day a green
square represents or how many runs.
**Fix direction:**
  - Add a range switcher (1 mo / 3 mo / 6 mo / 12 mo) sized to fit the
    panel; default to **1 mo**.
  - Re-layout the grid as a calendar-style month view at the short ranges
    so individual days are large enough to hover/tap. Keep the GitHub-style
    multi-column grid for the 6 mo and 12 mo views.
  - Implement an NSTooltip / SwiftUI `.help()` overlay showing
    `<date> · <N> runs · <status breakdown>` on each cell.

### CC-6. Usage card is thin — needs more dimensions

**Symptom:** Today's Usage shows only tokens (in/out/cache-r/cache-c) and
weekly total. User wants # file edits, # tool calls, etc.
**Fix direction:**
  - Surface (from session jsonl): tool-call count, distinct tool names,
    file edits (count of unique paths touched by `Edit`/`Write` tools),
    bash commands run, webfetch requests, etc.
  - The session jsonl already has all of this; we just don't extract it.
    Bake it into `UsageTrackerService` (or a new `SessionStatsService`).
  - Surface as a compact stats grid alongside the token total — not a
    separate card.

### CC-8. Run detail navigation is not discoverable + needs side panel mode

**Symptom:** Clicking a session row opens the detail view but it's not
obvious that the row is clickable. User also wants the option to open it
as a side panel from the Control Center (so the list stays visible) **and**
to expand to full-screen on demand.
**Fix direction:**
  - Make row rest state show a faint right-chevron + cursor pointer on
    hover, and a "view details" tooltip.
  - Replace the `NavigationStack` push with a split-pane: when a row is
    selected, the right ~480 px becomes the detail; an "Expand" button in
    the detail header makes it take the full main area.
  - Keyboard: `↵` opens detail, `⌘.` closes, `⌘⇧F` toggles full-screen.

## Round 2 — 2026-05-18 testing

### CC-1.b. Recent sessions show as `orphaned` with no other data — root cause: log marker casing drift

**Symptom:** Every Scout run from ~May 1 onward rendered as `orphaned` in the
Sessions list, with `—` for both commits and cost. Looked like the orphan
sweep was over-aggressive.
**Real cause (deeper than first patch):** scout-plugin renamed the runner
script's terminal log marker from `=== SCOUT … run finished at …` to
`=== Scout … run finished at …` around 2026-05-01. The app's
`parseBody` regex was pinned to all-caps `SCOUT`, so every new log fell
through to the `.running` fallback and then got demoted to `.orphaned` by
the time-based sweep — *correctly*, given the parsed status.
**Fix:** Regex now uses the constructor `.caseInsensitive` option so both
casings match. Two new tests (`parseBody_titlecaseScoutFinishMarker`,
`parseBody_titlecaseScoutWithoutSuffix`) lock the behavior in.
**Lesson:** When the app silently mis-parses runs from a plugin format
change, treat it like an integration contract violation — write fixture
tests covering all known historical formats, not just the current one.

### CC-4.b. Heatmap 1-mo view wastes the card's horizontal space

**Symptom:** 1-mo grid is 5 narrow columns × 7 rows on the left half of the
card, leaving the right half black.
**Fix:** 1-mo range now uses a calendar-month layout — 7 wide columns
labelled M T W T F S S, each day cell stretching to fill the column. Day
numerals visible inside cells. Multi-month ranges (3/6/12 mo) stay on the
GitHub-style "weeks-as-columns" grid because that's where the macro
pattern matters.

### CC-2.b. "No scheduled runs" + heartbeat empty when launched from Finder

**Symptom (regression after CC-2/CC-3 fix shipped):** Heartbeat schedule
strip empty, "Next up: No scheduled runs · check LaunchAgents".
**Real cause (not actually a regression — pre-existing fragility now
exposed):** `ScheduleService` invoked scoutctl via
`/usr/bin/env scoutctl …`. When Scout.app launches from Finder (or
`open`), the LaunchServices-provided PATH is just
`/usr/bin:/bin:/usr/sbin:/sbin` — `scoutctl` (installed via miniconda,
pipx, or homebrew) is not on it, so `env` can't find it and exec
fails. `refresh()` was catching and swallowing the error silently, so
`upcoming` stayed empty with no signal to the user.
**Fix:**
  1. `AppState.resolveScoutctlPath()` now tries known install locations
     in priority order (`~/scout-plugin/bin`, miniconda, `.local/bin`,
     homebrew) and uses the first concrete executable it finds. Falls
     back to `/usr/bin/env scoutctl` only if none exist.
  2. `ScheduleService.lastError` is now a `@Published` string set on
     any exec/decode failure. `UpcomingStripView` renders an explicit
     "Schedule unavailable — scoutctl not found" banner instead of an
     empty list.
**Lesson:** Silent error-swallowing in a service that polls every 60 s
hides config problems for hours. Default to surfacing the failure
unless we have an explicit reason to suppress it.

## Action Items — round 1 (2026-05-18)

### AI-1. Preamble is a wall of text

**Symptom:** Top of the daily file is 2–3 huge bolded paragraphs from
Scout (briefing, consolidation update, headline). All rendered flat,
serif, 720 px wide → drowns out everything below the fold.
**Fix:** Each preamble paragraph now renders as a collapsible
`PreambleCard`. Headline (the leading `**…**` bold) is always visible;
body is hidden behind a chevron and shows a 2-line teaser when collapsed.
Latest paragraph defaults expanded; older ones default collapsed.

## Pending — bigger features (need user-direction first)

### AI-2. Embed terminal / Claude Code session in Scout

User wants to launch and resume Claude Code sessions directly inside
Scout from an action item. Proposed approach: integrate **SwiftTerm** as
an embedded `NSView` terminal emulator (BSD-licensed Swift PTY package).
Bigger architectural decisions involved:
  - SwiftPM dependency on github.com/migueldeicaza/SwiftTerm
  - Process-lifecycle management (start, stop, attach/detach on app
    quit)
  - Pre-pop the prompt with the task context via the existing
    `ClaudeLauncher.prompt(for:)` helper

### AI-3. Sessions page

A top-level "Sessions" sidebar item listing all recent Claude Code
sessions (from `~/.claude/projects/`) — not just the Scout-launched
ones we currently track in the Control Center. Each row shows session
title / first prompt / mode / file count / tool-call count, opening into
the existing tool/edit/file detail surfaces.

### AI-4. Agents page

Distinct from Sessions: lists scheduled Scout agents (briefing,
consolidation, dreaming, research) with their last run + status +
linkable next-fire time. Likely overlaps with the existing Schedules
page; need clarification on what makes "Agents" distinct.

### AI-5. Tie Claude Code sessions to action-item tasks

Workflow: open Scout → click a task → "Resume Claude" should pull up the
last-known Claude Code session for that task. Requires writing a
session→task pointer at launch time, plus a back-reference on the task
side so the action item shows "2 linked sessions · resume".

## Engine-side / scoutctl changes that may be needed

These bugs likely need cooperative changes in the Python plugin (scoutctl),
not just app-side fixes:

- **CC-1 / CC-5:** "session end" signal that the app can trust. Right now the
  app infers `running → success` from log file activity, which is fragile.
  Engine should emit a terminal jsonl line on session exit.
- **CC-2 / CC-3:** `scoutctl schedule list-upcoming --json` should sort
  results chronologically and filter past entries by default.
- **CC-7:** Connector roster needs stable IDs (no rename drift) and the
  engine should emit a Claude Code session row in the health log.

When changing scoutctl, the contract is `scout-plugin` repo — separate
checkout. Keep the JSON shape backward-compatible (the app's decoder is
strict).
