# Per-file Wishlist & Research Queue — design

Restructure the Wishlist (`docs/Wishlist.md`) and Research Queue
(`knowledge-base/research-queue.md`) from single prose-heavy files into
per-file items (mirroring `dreaming-proposals/`), so Scout.app can surface
and add to them cleanly — and so the dreaming/research sessions and the app
share one canonical format instead of drifting.

Status: design approved 2026-06-16 (schema decisions confirmed), pending
spec review.

---

## Motivation

The user wants to add wishlist items and research-queue topics from
Scout.app. The current files are single markdown documents the dreaming
(`docs/Wishlist.md`, Phase 3) and research (`knowledge-base/research-queue.md`)
sessions read and write, with huge "last verified" preambles and long
per-item prose. Parsing them in the app would be brittle.

The clean fix — the user's instinct — is to give each item its own file
with YAML frontmatter, exactly like `dreaming-proposals/`. That makes the
app integration a near-copy of the just-shipped Proposals feature, and the
parser robust.

**Critical constraint (the drift trap):** these files are owned by the
**scout-plugin** repo (`templates/docs/Wishlist.md.tmpl`,
`skills/scout-dream/SKILL.md`, the research skill/runner). The empty
Proposals tab bug came from exactly this — the app parsed a format the
sessions had moved past. So the format change MUST land in lockstep across
the sessions, the vault data, and the app. This is a coordinated,
multi-subsystem project, not a single app feature.

---

## Shared per-file schema

Each item is one markdown file named `YYYY-MM-DD-slug.md` with YAML
frontmatter + body. The schema is **shared** between wishlist and research
queue so the app's parser/service/writer are reusable (parameterized by
directory + labels).

```markdown
---
title: <one-line title>
status: open | in-progress | done | dropped
priority: urgent | high | medium | low
date: YYYY-MM-DD                 # filed date
source: <provenance, optional>   # e.g. "Jordan Slack DM 2026-06-12"
area: <optional grouping>        # research only — e.g. "knowledge-graph"
---

# <title>

<body: description / rationale / notes / findings>
```

- **status** — `open` and `in-progress` are active; `done` and `dropped`
  are resolved. (`dropped` = decided-not-to-do, distinct from `done`.)
- **priority** — `urgent` is the research queue's 🔴 START-IMMEDIATELY;
  preserving it is load-bearing because the research session's
  preemption rule keys on it. Wishlist uses `high`/`medium`/`low`.
- **area** — optional, research only (the queue today has informal
  groupings like the knowledge-graph gap analysis).
- Identity is the file path; the leading `# <title>` H1 in the body is
  stripped on render (the title shows in the card header), matching the
  Proposals parser.

### Directories

- **Wishlist:** `docs/wishlist/` — replaces `docs/Wishlist.md`,
  `docs/Wishlist-in-progress.md`, `docs/Wishlist-done.md`.
- **Research queue:** `knowledge-base/research-queue/` — item files.
- **Thin research log:** `knowledge-base/research-queue.md` is **kept** as a
  thin index/log where the research session continues to write its
  "Last verified …" run-continuity narration (mirroring how
  `dreaming-proposals.md` became an index). It no longer holds items.
  (Wishlist has no equivalent session-log preamble, so no index file is
  kept for it.)

---

## Migration mapping (existing → per-file)

**Wishlist** (`docs/Wishlist.md` + `-in-progress` + `-done`): each `* **…**`
bullet becomes one `docs/wishlist/<date>-<slug>.md`:
- `[in progress]` marker → `status: in-progress`; `[done]`/in the -done file
  → `status: done`; otherwise `status: open`.
- `HIGH` → `priority: high`, `MEDIUM` → `priority: medium` (default `medium`).
- Leading `(YYYY-MM-DD — provenance)` → `date` + `source`.
- Title = the bold lead; body = the rest of the bullet.

**Research queue** (`knowledge-base/research-queue.md` `## Queue` +
sub-sections): each `- [ ] <emoji> **…** — …` becomes one
`knowledge-base/research-queue/<date>-<slug>.md`:
- `[x]` → `status: done`; `[ ]` → `status: open` (a few "in progress"
  notes → `in-progress`).
- 🔴 / START IMMEDIATELY → `priority: urgent`; 🟡 → `medium`; 🟢 → `low`.
- Sub-section heading (e.g. knowledge-graph gap analysis) → `area`.
- The "Last verified" preamble stays in the thin `research-queue.md` log.

Migration is one-time and committed to the vault git. Dates come from the
item's stated filed-date where present, else a best-effort from context.

---

## Sub-project decomposition & sequence

Approved sequence: **schema → plugin + migration → app.**

1. **Schema (this doc).** The shared format above. Foundation for both
   builds below. No code.

2. **scout-plugin + vault migration** (next: own plan).
   - scout-plugin: update `skills/scout-dream/SKILL.md` Phase 3 (and the
     research skill / `run-research.sh.tmpl`, `run-dreaming.sh.tmpl`,
     `templates/docs/Wishlist*.tmpl`) to read/write per-file items in the
     new directories, and to write the "last verified" log to the thin
     `research-queue.md`. Update any `scout-status` / docs references.
   - Vault: migrate existing `Wishlist*.md` and `research-queue.md` items
     into the per-file directories; reduce `research-queue.md` to the thin
     log; commit.
   - These land together so the next scheduled dreaming/research run uses
     the new format.

3. **scout-app** (last: own plan). Two new sidebar tabs — **Wishlist** and
   **Research** — built as a near-copy of the Proposals feature:
   a generic per-file list `DocumentService` + `Parser` (frontmatter + body,
   shared with/generalized from `ProposalsParser`) + a `Writer` that can
   **append a new item** (the "add" capability) and flip `status`, each
   parameterized by directory + priority/status vocab. Awaiting/resolved
   split + collapsible Resolved section reuse the Proposals view shape.
   "Add" writes a new `YYYY-MM-DD-slug.md` with frontmatter and git-commits.

---

## Non-goals

- No new status/priority semantics beyond the enums above.
- No automatic dedup/merge of migrated items (faithful 1:1 migration).
- The app does not run dreaming/research; it only views, adds, and sets
  status — the sessions still do the work.
- Obsidian remains a valid way to view/edit the per-file items (plain
  markdown + frontmatter, backlink-friendly — a benefit of the split).

---

## Revision 2026-06-17 (learned during execution)

Two corrections to sub-project 2, discovered when implementing:
- **Workflow prose is owned by `phases/`, not the vault.** The plugin
  *assembles* each vault's `DREAMING.md`/`RESEARCH.md` from phase files
  (`phases/modes/wishlist.md`, `phases/research/research-targets.md`) and
  3-way-merges them on upgrade. So the per-file prose edits go in `phases/`
  — editing a vault's assembled `DREAMING.md` directly would be clobbered.
- **Existing-user data migration is an engine step.** Decision: port the
  migration into the engine and run it idempotently in the `/scout-update`
  upgrade pipeline's **migrations** stage (so all users convert on upgrade),
  rather than prose-driven self-migration.

Status of the three sub-projects:
1. Schema — done (this doc).
2. Plugin + migration — Jordan's vault migrated (done); the plugin
   distribution (phase prose + templates + engine upgrade-migration) is
   planned in `docs/superpowers/plans/2026-06-17-wishlist-research-per-file-plugin-distribution.md`.
   The migration prototype is committed in scout-plugin
   (`scripts/migrate_wishlist_research.py`, 16 tests).
3. App tabs — not started (follows the distribution work).

## Testing posture

- scout-plugin: the skill/template changes are prose; validate by a dry
  dreaming/research run reading the migrated dirs (or a scoutctl check if
  one exists).
- Migration: a script or careful pass; verify item counts in == out and
  spot-check frontmatter.
- scout-app: TDD the shared per-file parser + the append writer (pure
  functions), as with Proposals; the services/views verified by build +
  the existing FSEvents test double.
