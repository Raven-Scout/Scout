---
phase: core
name: inbox-processing
slot: inbox
mode: [briefing, consolidation]
requires: null
---

## Process Inbox

Read `inbox.md`. If it has entries (lines starting with `-`), process each one:

**Routing heuristics (apply in order):**
1. **Meeting-related** — mentions a meeting name from `meetings/meetings.md` or a person strongly associated with a specific meeting → append to that meeting's home file under `## Next Session Notes`
2. **Personal task** — starts with `personal:` or mentions clearly non-work topics → create or update an entity in `knowledge-base/personal/`
3. **Research/idea** — starts with `idea:`, `research:`, or `explore:` → if it clearly matches an `active` or `paused` research thread in the lab (`scripts/scoutctl lab list --json`), add it there as an owner comment in the owner's own words: `scripts/scoutctl lab comment <slug> --text "Q: <entry>" --author <handle>`, the handle being {{USER_NAME}} with spaces as `-` and anything but letters, digits, `.`, `_`, `-` dropped (as the app signs comments). Otherwise append ` [for research]` and keep the line for a research run; skip lines already marked so.
4. **Project-specific** — references a project name or Linear issue identifier → append as a note to the relevant project file
5. **Default** — treat as an action item candidate for today's action-items file

**After routing each entry:**
- Remove the processed line from `inbox.md` (a `[for research]` line stays)
- If an entry is ambiguous and could be misrouted, keep it in inbox with `[needs context]` appended
- Git preserves removed entries in history

If `inbox.md` is empty or contains only the header, skip silently.
