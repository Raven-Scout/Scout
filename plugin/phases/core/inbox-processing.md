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
3. **Research/idea** — starts with `idea:`, `research:`, or `explore:` → if it clearly matches an `active` or `paused` lab thread (`scripts/scoutctl lab list --json`), add it as an owner comment, in the owner's words: `scripts/scoutctl lab comment <slug> --text 'Q: <entry>' --author {{USER_HANDLE}}`, `<entry>` being the line without its `idea:`/`research:`/`explore:` prefix (each `'` as `'\''`); commit `knowledge-base/lab/<slug>/<slug>.md`. Otherwise, or if `lab comment` exits non-zero, append ` [for research]` to the line instead and keep it; skip lines already marked so.
4. **Project-specific** — references a project name or Linear issue identifier → append as a note to the relevant project file
5. **Default** — treat as an action item candidate for today's action-items file

**After routing each entry:**
- Remove the processed line from `inbox.md` (a `[for research]` line stays)
- If an entry is ambiguous and could be misrouted, keep it in inbox with `[needs context]` appended (git keeps removed ones)

If `inbox.md` is empty or contains only the header, skip silently.
