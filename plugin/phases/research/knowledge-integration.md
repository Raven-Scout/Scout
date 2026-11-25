---
phase: research
name: Knowledge Integration
slot: integration
mode: [research]
requires: null
---

# PHASE 3: KNOWLEDGE INTEGRATION

## Step 3a: Update Topic Notes

Write or update the topic notes the findings belong in: expand thin sections, update verification dates, mark unverified web claims `[single-source]`, and cite sources (URLs, dates accessed).

## Step 3b: Extend the Knowledge Graph

- Add relationships found; create an entity file only for a significant new entity (a competitor, a technology, a new team member)
- Run `scripts/scoutctl kb validate` after changes
- A new note built from private material gets `visibility: private` (see the privacy phase)

## Step 3c: Flag Actionable Insights

If research reveals something {{USER_NAME}} should act on, note it in the wrap (Phase 4); if urgent, flag it for the next consolidation run. Do NOT create action items (that's consolidation's job). A finding that suggests changing Scout goes to the research→Scout findings path (the vault's findings log, if it has one), never a Scout edit.

## Step 3d: Update the thread

After the topic notes, for each thread worked:

- `lab understanding set <slug> --file <tmp>`: the whole Current understanding section, rewritten, kept under 2 KB.
- `lab question answer <slug> <QID> --note "…"`, or `drop` (Step 1b's rule) or `add`, as the questions moved.
- `lab evidence add <slug> --target <note> --why "…"` for each topic note used.
- One `lab timeline add <slug> --tag "research run" --did "…" --found "…" --questions "…" [--steers "…"]` per touched thread (it sets `last_run`). Each field is a sentence a cold reader understands, never a commit subject or a file list; `--steers` says which owner comments were handled and how.
