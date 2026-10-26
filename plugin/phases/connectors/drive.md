---
phase: connector
name: drive
slot: inbound-scan
mode: [consolidation, briefing]
requires: drive
---

## Google Drive Inbound Scan — Document Activity

Check for recently modified documents, meeting notes, and shared files in Google Drive.

### Recent Document Activity

Use `list_recent_files` or `search_files` with date filters to find documents modified since the last run. Focus on:

1. **Meeting notes documents** — shared docs used for meeting agendas and notes. These often contain action items written collaboratively during meetings.
2. **Shared documents** — files shared with {{USER_NAME}} or files in shared folders that were recently updated. New shares or edits may indicate:
   - Someone needs {{USER_NAME}}'s review or input
   - A deliverable was completed and shared
   - Collaborative work is in progress
3. **Documents {{USER_NAME}} edited** — files modified by {{USER_NAME}} are evidence of completed work.

### Mandatory on Weekday Runs

When Drive is a connected source, **a weekday consolidation or briefing run with zero Drive calls is a coverage bug**, not an optimization. Two reasons Drive is non-negotiable:

1. **Meeting-notes cross-check.** Many meetings are captured in Drive (auto-generated notes, hand-written docs, teammates' shared notes) but not in the transcript tool — and vice versa. A run that only reads transcripts loses a large fraction of the meeting record, and the entity-creation gaps that follow (people, decisions, projects that only ever appeared in a Drive doc) compound in the KB.
2. **Decision artifacts.** Specs, RFCs, and post-meeting writeups land in Drive without ever being announced in a channel. They're the source of truth for what was actually decided.

Note coverage explicitly in the run log: `Drive — N files in window, K read in detail`. (Weekend-briefing runs may skip the Drive sweep per the Weekend Scope rule.)

### Cross-Reference with Transcript Tools (hard rule)

**Cross-reference with meeting transcript tools (e.g., Granola, Fathom) — in both directions.** Some meetings appear in Drive but not in transcript tools, and vice versa:

- For every meeting surfaced by the transcript tool this run, search Drive for files modified in the meeting's **±2h window** with names matching the meeting title or attendee names.
- For every Drive file in the window that looks like meeting notes, search the transcript tool for a matching transcript.
- If only one of (transcript tool, Drive) has the meeting, **note the gap explicitly in the action items file** (e.g., "_transcript exists; no Drive notes found_") so future runs — and {{USER_NAME}} — know what coverage looked like.
- If both exist for the same meeting, **synthesize the best information from each** — use the transcript for exact quotes and commitments, use the Drive doc for structured decisions and action items.

### What to Extract

For each relevant document found:
- **Document title and link**
- **Who modified it** and when
- **Action items** written in the document (look for checkbox items, "TODO," "Action:" patterns)
- **Decisions** documented
- **Whether this relates to a known project** (link to KB project file)

### On a Briefing, Also

- Use the past 24 hours as the window. Search for titles with "meeting notes," "agenda" or "minutes," files in shared folders of active projects, and files recently shared with {{USER_NAME}}.
- For yesterday's meetings, look for an associated doc: a doc with no transcript is the primary meeting record.
- Check documents several people have been editing: active collaborative work often carries action items, decisions or context no other connector shows. Read the relevant ones with `read_file_content`.
