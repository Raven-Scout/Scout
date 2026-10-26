---
phase: connector
name: fathom
slot: inbound-scan
mode: [consolidation, briefing]
requires: fathom
---

## Fathom Inbound Scan — Meeting Transcripts & Summaries

Pull meeting recordings from Fathom since the last run and extract actionable information.

### Retrieve Recent Meetings

Use `mcp__fathom__list_meetings` with `created_after` set to the last run timestamp (ISO 8601) and `include_summary: true`, `include_action_items: true` to get an overview of recent meetings.

For each meeting returned, review the summary and action items first. If more detail is needed (e.g., ambiguous commitments, unclear context), pull the full transcript with `mcp__fathom__get_meeting_transcript` using the `recording_id` and `url` from the listing.

For each meeting, extract:

1. **Action items mentioned** — any commitment like "I'll do X," "{{USER_NAME}} will handle Y," or "we need to Z." Record exactly what was said, by whom, and in what context. Cross-reference with Fathom's own action items from the listing.
2. **Decisions made** — any agreement or conclusion reached during the meeting. Record the decision, who was involved, and what it supersedes (if anything).
3. **Commitments by others** — things other people said they'd do that {{USER_NAME}} should track. These become Watching items.
4. **Deadlines or timelines mentioned** — any dates or timeframes referenced ("by Friday," "next sprint," "end of quarter").
5. **Open questions** — things that were raised but not resolved in the meeting.

### Critical Warning: Transcripts Are Signals, Not Facts

**Meeting transcripts are SIGNALS, not FACTS.** A transcript is a noisy recording of a conversation — people misspeak, context is lost, and not every statement represents a real commitment.

Every action item extracted from a transcript is a **candidate** that must be verified:
- Did {{USER_NAME}} already complete this? (Check outbound messages, code activity, email)
- Was this superseded by a later discussion? (Check more recent transcripts, Slack messages)
- Is this actually assigned to {{USER_NAME}}? (Transcripts often attribute items vaguely — verify against the issue tracker)
- Is this the same item already tracked elsewhere? (Deduplicate against existing action items)

**Never write a transcript-sourced action item directly to the action items file without cross-checking it first.**

### Attendee Extraction

Note all meeting attendees from the `calendar_invitees` field and transcript speaker names. Cross-reference with `people.md` and add any new people with context: "Attendee in [meeting title] on [date]."

### Person Lookup for Context

If a meeting involves someone {{USER_NAME}} is preparing to meet again, use `mcp__fathom__find_person` with `recorded_by: "anyone"` to pull up prior meeting history with that person. This enriches the people KB and helps with meeting prep.

### On a Briefing, Also

- Use the past 24 hours as the window (`created_after` 24 hours ago). Pull at most 3 full transcripts per run, the most actionable by their summaries first; `mcp__fathom__get_meeting_summary` gives more than the listing.
- **Topic search:** for active KB projects, `mcp__fathom__search_meetings` with project keywords, `recorded_by: "anyone"` and `created_after` the last briefing run finds discussions not yet captured.
- **Calendar gaps:** note calendar meetings with no recording (no notes, cancelled, or not recorded) and recordings not on the calendar (ad-hoc calls). When both exist, take attendees and timing from the calendar and the content from the transcript.
- **Today's meetings:** if a meeting today has attendees or topics from recent recordings, say so ("You're meeting with X again today — in yesterday's call, you committed to Y"). `mcp__fathom__find_person` with `recorded_by: "anyone"` pulls the history.
