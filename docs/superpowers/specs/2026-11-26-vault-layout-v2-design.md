# Vault layout v2: system, records, output, knowledge and input apart

Status: **draft for David's approval.** Spec only: no code. Decisions taken with David on 2026-11-26, one
question at a time; a Fable 5.2 agent drafted the layout and migration from them, a second Fable 5.2 agent
reviewed that draft adversarially, and every claim that changed the design was checked in code.
Ticket: "Optimise the Scout vault folder structure: separate system files from generated content" on the
Scout Next board (https://github.com/users/davidesner/projects/2, draft item).
Decision record: [ADR 0011](../../adr/0011-vault-layout-v2.md).

Line references are to the post-merge tree of PR #827 (`c2b16135`, main merged into `de-scout-next`), the
code this change lands on. `E/` is `plugin/engine/scout/`.

## 1. Why

A new user opening a vault can't tell Scout's machinery from what Scout made for them. The root holds brain
skills, their merge sidecars, four runners, `scripts/` (107 files in David's vault), `hooks/` and config next
to `inbox.md`, and generated reports used to land there too (David cleared about 15 of them on 2026-11-11).
Inside `knowledge-base/`, Scout's own run records (mistake audit, session log, connector health) sit next to
people and projects. Meetings have two homes because the plugin disagrees with itself: bootstrap seeds root
`meetings/meetings.md` (`E/scripts/bootstrap.py:218`) and the KB pre-filter walks root `meetings/`
(`E/hooks/kb_pre_filter.py:103`), while `plugin/phases/core/meeting-prep.md:15` reads
`knowledge-base/meetings/meetings.md`. Runs have written to both: David's vault has 112 files under root
`meetings/` and 8 under `knowledge-base/meetings/`, with registries of 34 and 25 rows that are not mirrors.

The goal is a layout where every top-level entry is obviously one of five things: Scout's system, Scout's
records, content generated for the user, knowledge, or user input. And, since Feature 1 (spaces) would
otherwise force a second restructure, the KB gets its space folders in the same move.

## 2. Decisions

| # | Question | Decision |
|---|---|---|
| Q1 | Plugin default or this vault only | Plugin default. Setup creates v2; upgrades move existing vaults. David's vault first, tested on a copy. |
| Q2 | Where system files go | One visible `scout/`: `scout/brain/`, `scout/runners/`, `scout/scripts/`, `scout/hooks/`. |
| Q3 | Dot-folders | `.scout-state/`, `.scout-logs/`, `.scout-cache/` stay where they are. |
| Q4 | Config | `scout-config.yaml` stays in the root (it is the vault marker, `E/paths.py:15`). A generated root `README.md` maps the folders. |
| Q5 | Records | David's rule: what is really about the KB stays in the KB. Test: about what Scout knows (stays) or how Scout runs (moves). Stay: review queue, research queue, decisions-log, `channels.md`, `people.md`, `knowledge-base.md`. Move to `scout/records/`: mistake audit, session log, `connector-health.md`, dreaming proposals, wishlist. |
| Q6 | Meetings | One home in the KB. A dated occurrence file holds prep, notes and synthesis, so prep moves with it. Action items stay at the top level. The unused `action-items/meeting-prep/` goes. The reviewer agreed (section 11). |
| Q7 | User input | `inbox/inbox.md` and `inbox/drop/`. `knowledge-base/inbox/` is deleted: nothing reads it (only a lint-config test uses the path as a sample glob) and its own banner says it is not the inbox. |
| Q8 | Unknown root files | Never moved. Run health shows a yellow finding. Phases write one-off reports to `reports/`. |
| Q9 | Spaces | Create the space folders now, in this move. |
| Q10 | KB shape | `knowledge-base/<space>/` holds that space's projects, topics, sources, meetings and role notes. Scout-wide at the KB root: ontology, indexes, decisions-log, the review and research queues, recurring tasks. |
| Q10b | People across spaces | A Scout-wide identity note plus an optional role note per space. Same for organizations. |
| Q11 | Space registry | Minimal `spaces:` in `scout-config.yaml`: key, default flag, visibility (R1). New vaults: `work` (default), `personal`. David's vault adds `svj`. No routing (delivery channels, connector maps): that stays Feature 1. |
| Q12, Q13 | Migration | Two parts. Part 1, the layout move, is a stage of every upgrade and safe unattended. Part 2, the space review, is reviewed and approved by the user afterwards. |
| Q14 | App and paths | The engine owns the layout: `scoutctl vault layout --json` with a contract fixture, now. The app only switches its constants in this change; moving it onto the command is input to the app redesign epic. |
| Q15, R3 | Rewriting old paths | Literal paths are always rewritten. Note links are rewritten in current files only. `action-items/archive/` and dated source files stay as written; their links keep resolving. |
| R1 | Space privacy | Each space has a default visibility: `work` normal, `personal` and `svj` private. A note's own `visibility:` still wins. |
| R2 | Pending brain sidecar | The layout move defers until no brain merge sidecar is pending. `/scout-update` walks the user through resolving them first. |
| R4 | Trims | No `vault spaces` subcommand family, no `--undo` command, no `migration_holds` key. The old-layout path table serves read-only callers only. `connectors.custom.yaml`, `connector-probes.local.yaml` and `action-items/render.py` stay. Recurring tasks stay Scout-wide. `kb-page-standard.md` goes to `scout/`. |
| R5 | The vault's own scripts | `scripts/` and every root `run-*.sh` move whole, the vault's own included. The ones that locate files relative to themselves are listed in the migration report. |
| R6 | Read gate on records | `scout/records/` is not gated. Phases get a rule: a record links to a note and never copies its text. |

OKF (Feature 5) is a separate task. This change only does what Feature 5 would need anyway: records leave the
KB, and every file the move creates carries a `type`.

## 3. Target layout

Categories: **S** system, **R** Scout's records, **G** generated for the user, **K** knowledge, **U** user input.

```
<vault>/
  scout-config.yaml                 S  vault marker and config; gains spaces:
  README.md                         S  generated map of this tree (3.3)
  connectors.custom.yaml            S  unchanged (R4)
  connector-probes.local.yaml       S  unchanged (R4)
  scout/                            S  how Scout runs
    brain/                          S  SKILL.md, DREAMING.md, RESEARCH.md, RESPOND.md and their merge sidecars
    runners/                        S  run-scout.sh, run-dreaming.sh, run-research.sh, run-respond.sh, the vault's own run-*.sh
    scripts/                        S  managed and vault-own scripts
    hooks/                          S  kb-pre-filter.sh
    kb-page-standard.md             S  how Scout writes KB pages (R4)
    records/                        R  what Scout writes about its own runs
      scout-mistake-audit.md, scout-mistake-audit/
      session-log.md, session-log/
      connector-health.md
      dreaming-proposals.md, dreaming-proposals/
      wishlist/                     wishlist items, progress/ included
  knowledge-base/                   K  what Scout knows; still the gated root
    knowledge-base.md               K  index
    people.md, channels.md          K  Scout-wide indexes
    meetings.md                     K  the one meetings registry, with a Space column (3.2)
    decisions-log.md, decisions-log/
    review-queue.md, review-queue/  K  review items
    research-queue.md, research-queue/  K  research items
    recurring-tasks/                K  Scout-wide (R4)
    ontology/                       S+K schema.yaml, parser.py (tooling); entities/ (non-person, non-organization entities)
    people/<name>.md                K  identity notes
    organizations/<name>.md         K  identity notes
    from-agents/, lab/              K  unchanged
    <space>/                        K  one folder per registered space: work/, personal/, svj/
      projects/, topics/, sources/  K  with their index files
      meetings/<slug>/              K  <slug>.md home file and YYYY-MM-DD.md occurrence files
      people/<name>-<space>.md      K  role notes
      organizations/<name>-<space>.md  K  role notes
      (loose notes)                 K  allowed; today's personal/ notes stay where they are
  action-items/                     G  daily action-items files, archive/, render.py, the rest as today
    context/                        G  context notes (still gated, E/kb/raw_read.py:28)
  drafts/                           G  as today
  reports/                          G  one-off reports, migration reports
  inbox/                            U
    inbox.md                        U  the Inbox
    drop/                           U  dropped files (contents ignored, README.md tracked)
  .scout-state/ .scout-logs/ .scout-cache/   S  unchanged (Q3)
  .scout-inbox/ .scout-feedback/ scout-display.json   S  unchanged; app- and delegate-owned, named in the README
```

Gone from the root: the brain skills and sidecars, `run-*.sh`, `scripts/`, `hooks/`, `docs/` as a Scout
folder, `meetings/`, `inbox.md`, `drop/`, `dreaming-proposals*`. Gone from the KB: `session-log*`,
`scout-mistake-audit*`, `connector-health.md`, `inbox/`, `meetings/`, and the top-level `projects/`,
`topics/`, `sources/` (now per space).

### 3.1 Placement notes

- **`knowledge-base/personal/` is the personal space as it stands.** Only the `svj-*` notes leave it. The 734
  `[[personal/...]]` links in David's vault stay valid.
- **Identity notes keep today's path** `knowledge-base/people/<name>.md`, so the 15,081 `[[people/...]]`
  links stay valid with no rewrite.
- **Records keep their file names** (`scout-mistake-audit.md`, not `mistake-audit.md`): moving and renaming
  at once doubles the rewrite for nothing.
- **Space keys** match `[a-z][a-z0-9-]*`, exactly one is the default, and none may equal a Scout-wide KB
  folder or a v1 folder name (`people`, `organizations`, `ontology`, `decisions-log`, `review-queue`,
  `research-queue`, `recurring-tasks`, `from-agents`, `lab`, `projects`, `topics`, `sources`, `meetings`,
  `inbox`).

### 3.2 Identity notes, role notes and the graph

An **identity note** (`knowledge-base/people/<n>.md`, `knowledge-base/organizations/<n>.md`) holds only facts
safe for every space: `name`, `type`, `aliases`, handles (email, Slack ID, GitHub for a person; domain and
website for an organization), `spaces:` and the note's privacy keys. Its body is one line per role note link.
A **role note** (`knowledge-base/<space>/people/<n>-<space>.md`) holds what Scout knows about that person in
that space: properties, `relationships`, body. It carries `type: role`, `role_of: "[[people/<n>]]"` and
`space: <key>`, so it is a valid typed note but never an entity of its own.

When a friend becomes a coworker, nothing moves: a work role note appears.

The graph needs one change in the same commit. Today a note is an entity only with both `name` and `type`
(`E/kb/ontology.py:442`), and two notes with one `name` overwrite each other (`:461`). `KnowledgeGraph.load()`
gains a **role fold**: after the entity pass, each role note the reader may see is folded into its identity
(properties added, relationships attached to the identity's name, each role's own values kept under
`_roles[<space>]`). The folded entity takes **the most restrictive label of its source files**, and each
folded relationship keeps its own file's label. Today an entity carries only the label of the file it was
read from (`ontology.py:457`), which would serve a private role's facts as normal. With the fold,
`kb entity`, `kb query`, name lookup and traversal return what they return today.

kb lint gains a rule: an identity note holds only identity keys and its link lines. A run that writes facts
into one fails its commit, which keeps stale brain habits from defeating Q10b.

### 3.3 The meetings registry

One registry, `knowledge-base/meetings.md`, with a `Space` column. The briefing matches calendar events
against it before it knows the space (`meeting-prep.md:15`), and Q10 puts indexes at the KB root. A row holds
only the slug, the space and the calendar match pattern. Attendees and themes stay in the series' home file
inside its space folder, so a personal series' attendees never sit in a file every space reads.

Dated occurrence files get `type: meeting-occurrence` in the template, and their own class in kb lint
(log-size rules), freshness ranking and search.

### 3.4 The generated README

Written by setup and by every upgrade when absent or when it starts with
`<!-- scout:generated layout-readme v2 -->`. A README the user wrote is never touched. It renders from the
same table as `scoutctl vault layout`, so it can't drift. Sections: where to put things (`inbox/`), what Scout
makes for you (`action-items/`, `drafts/`, `reports/`), what Scout knows (`knowledge-base/`, the registered
spaces with the default marked), how Scout runs (`scout/`, `scout-config.yaml`, the dot-folders and app-owned
root files, one line each), Scout's records, and a footer with the layout and engine versions.

## 4. Part 1: the layout move

### 4.1 Where it runs

A function `migrate_layout(cfg)` in a new `E/layout_migrate.py`, called from `_stage_migrations`
(`E/scripts/bootstrap.py:1365-1372`) **after** `migrate_perfile`, which stays first. It runs inside
`upgrade()` under the session lock (`acquire_lock_with_wait`, `bootstrap.py:1442`), so `/scout-update`,
self-update and Scout.app all reach it the same way. `scoutctl vault migrate --plan` previews it, and
`scoutctl vault migrate --apply` runs it outside an upgrade (for testing on a copy). Setup never calls it:
`install()` writes v2 directly and records `layout-v2` as applied.

It is the first reader of `plugin.applied_migrations` (written as an empty list at `bootstrap.py:1222`, read
nowhere). Which layout a vault has is decided **from disk**: v2 when `scout/brain/` exists and there is no root
`SKILL.md`. The key records that the move finished. A legacy vault (`.scout-state` without config,
`bootstrap.py:1362`) must not read as v2.

### 4.2 The path map

`<S>` is the space the rules in 4.4 pick; `<D>` is the default space.

| # | Old (v1) | New (v2) | Notes |
|---|---|---|---|
| 1 | `SKILL.md`, `DREAMING.md`, `RESEARCH.md`, `RESPOND.md` | `scout/brain/<same>` | |
| 2 | `run-*.sh` (root, any) | `scout/runners/<same>` | vault-own runners too (R5) |
| 3 | `scripts/**` | `scout/scripts/**` | managed and vault-own (R5) |
| 4 | `hooks/**` | `scout/hooks/**` | |
| 5 | `docs/kb-page-standard.md` | `scout/kb-page-standard.md` | |
| 6 | `knowledge-base/session-log.md`, `session-log/**` | `scout/records/<same>` | |
| 7 | `knowledge-base/scout-mistake-audit.md`, `scout-mistake-audit/**` | `scout/records/<same>` | |
| 8 | `knowledge-base/connector-health.md` | `scout/records/connector-health.md` | |
| 9 | `dreaming-proposals.md`, `dreaming-proposals/**` | `scout/records/<same>` | |
| 10 | `docs/wishlist/**` | `scout/records/wishlist/**` | |
| 11 | `inbox.md` | `inbox/inbox.md` | |
| 12 | `drop/**` | `inbox/drop/**` | `.gitignore` first (4.5 step 4) |
| 13 | `meetings/meetings.md` + `knowledge-base/meetings/meetings.md` | `knowledge-base/meetings.md` | merged (4.7) |
| 14 | `meetings/<slug>/**`, `knowledge-base/meetings/<slug>/**` | `knowledge-base/<S>/meetings/<slug>/**` | merged per series (4.7) |
| 15 | `knowledge-base/projects/**` | `knowledge-base/<S>/projects/**` | index to `<D>` |
| 16 | `knowledge-base/topics/**` | `knowledge-base/<S>/topics/**` | index to `<D>` |
| 17 | `knowledge-base/sources/**` | `knowledge-base/<S>/sources/**` | index to `<D>` |
| 18 | `knowledge-base/people/<n>.md` (`type: person`) | role note `knowledge-base/<S>/people/<n>-<S>.md` + identity note at the old path | split (4.6) |
| 19 | `knowledge-base/ontology/entities/<n>.md` (`type: organization`) | role note `knowledge-base/<S>/organizations/<n>-<S>.md` + identity note `knowledge-base/organizations/<n>.md` | split (4.6) |
| 20 | `knowledge-base/personal/svj-*` (and any `<key>-*` of another registered space directly under `personal/`) | `knowledge-base/<key>/` | |
| 21 | `knowledge-base/inbox/**` | deleted | Q7 |
| 22 | `action-items/meeting-prep/` | removed if empty, else left and reported | |
| 23 | `.scout-state/last-rendered/<old>`, `.scout-state/drift/<old>.*` | same under the new path | merge bases (4.8) |
| 24 | everything else | unchanged | unknown root entries are never moved (Q8) |

For David's vault this is roughly 930 of 1,609 tracked files moved, 46 identity notes written and one file
deleted. Largest groups: `knowledge-base/sources` 473, `meetings` 112, `scripts` 107, `scout-mistake-audit`
52, `docs/wishlist` 47.

### 4.3 Preconditions

Checked before anything is written. A failure **defers** the move with a reason, and the upgrade stops before
it renders any managed file (rendering v2 files into a v1 vault would break it).

1. The session lock is held, and **no runner is queued for it**. A runner that finds the lock busy waits up
   to 30 minutes (`plugin/templates/scripts/scout-lock.sh.tmpl:22`, `SCOUT_LOCK_WAIT_SECS=1800`) and would
   wake with v1 paths. The process table check from `E/kb/live_run.py` finds them.
2. The vault is a git repository with a commit; no merge, rebase, cherry-pick or bisect in progress; no
   unmerged paths; no `.git/index.lock`.
3. **No brain merge sidecar is pending** (R2). David's vault has three today (`SKILL.md`, `DREAMING.md` and
   `RESEARCH.md` `.proposed-merge`, written 2026-11-26 16:02).
4. No target path already exists (case-folded: the default macOS disk is case-insensitive), and no two sources
   map to one target except the meetings merge.
5. Every planned path stays inside the vault after resolving symlinks.
6. If a Claude Code plugin is installed for this user, it is at least the layout-v2 release. Hooks run the
   plugin's own engine (`${CLAUDE_PLUGIN_ROOT}/engine/bin/scoutctl` in `plugin/hooks/hooks.json`), so an older
   plugin would protect only v1 runner paths (`E/kb/hook_paths.py:145-169`). How to read the installed plugin
   version is to be confirmed in implementation.

A dirty tree is not a refusal. Tracked edits, staged or not, go into a checkpoint commit first (hooks on,
message `Scout: checkpoint before the layout move`). A file with conflict markers defers instead. In
`/scout-update` the user is asked before the checkpoint. Untracked files are never added.

Other git work trees of the vault (David's has four) are listed in the report: their branches still hold the
v1 layout and will conflict when merged.

A deferral is its own outcome. Self-update must not count it as a failure. Today a refused `bootstrap auto`
counts as failed and a release is abandoned after three (`E/scripts/self_update_run.py:53-54, 373-377`).

### 4.4 Space assignment

Deterministic, no model call:

1. Under `knowledge-base/personal/`: the personal space, unless rule 2 applies.
2. A file or folder directly under `knowledge-base/personal/` whose name starts with `<key>-`, for a
   registered space key other than the default and `personal`, goes to that space. For David's vault this is
   exactly the 11 `svj-*` notes.
3. Everything else: the default space. A meeting series is assigned as a whole. Person and organization role
   notes get the default space.

If `scout-config.yaml` has no `spaces:`, the stage writes `work` (default, normal) and `personal` (private).
`--plan` notes a repeated `<word>-` prefix in `personal/` that isn't a registered space ("11 files are named
`svj-*`; add a space `svj` to file them there"). David adds `svj` before his move.

### 4.5 The algorithm

1. **Skip if done** (v2 on disk and `layout-v2` recorded). If a journal exists from an interrupted attempt,
   recover first (4.9).
2. **Plan.** Built only from `git ls-files`, a walk for untracked entries, the frontmatter heads of person and
   entity notes, both registries and `spaces:`. `--plan` prints this object, and apply runs the same function.
   A test asserts the applied result equals the plan on a fixture vault.
3. **Preconditions** (4.3), then the checkpoint, then save `refs/scout/migrations/layout-v2/pre`. Write the
   journal `.scout-state/migrations/layout-v2/journal.json`, and copy the untracked state a commit can't
   carry (`.scout-state/last-assembled/`, the privacy labels of every file to move) to
   `.scout-state/migrations/layout-v2/pre/`.
4. **`.gitignore` first.** Rewrite the vault's own patterns that start with a moved folder, in place (`drop/*`
   to `inbox/drop/*`, `!drop/README.md` to `!inbox/drop/README.md`; in David's vault these are the vault's own
   lines, not the template's). The append-only merge (`bootstrap.py:317-346`) could never fix them. Then check
   that every ignored file about to move is still ignored at its new path, and abort if one would become
   committable.
5. **Compatibility links.** Create relative symlinks at the old entry points: `scripts`, `hooks`, each root
   `run-*.sh` and each brain skill, pointing into `scout/`. Add them to `.gitignore`. They keep three things
   working until the jobs are re-pointed: the heartbeat (its plist runs `__SCOUT_DIR__/scripts/heartbeat.sh`,
   `E/defaults/com.scout.heartbeat.plist:23`, and it is the only thing that starts self-update,
   `plugin/templates/scripts/heartbeat.sh.tmpl:63-65`), the tick on an old engine (`vault / runner`,
   `E/scripts/schedule_tick.py:552`), and a session that started before the move. The first upgrade that finds
   the jobs, pointer, git hook and installed plugin on v2 paths removes them.
6. **Move.** Tracked files with `git mv`, one call per directory where the whole subtree moves; untracked and
   ignored files with `os.rename`. Each pair goes into the journal as it completes. Row 21 is `git rm`.
7. **Commit 1: renames only.** Subject `scout-update: move the vault to layout v2 (N files)`, trailer
   `Scout-Migration: layout-v2`. Hooks off, as for the first vault commit (`bootstrap.py:977-986`). Pure
   renames keep git's rename detection exact, so `git log --follow` and the changes scanner (`-M`,
   `E/changes/scan.py:194`) see renames, not a delete and an add.
8. **Merge the meetings** (4.7), **split people and organizations** (4.6), **rewrite old paths** (4.10).
   Rewritten files are replaced atomically (write to a temp file, then rename), never edited in place.
9. **Re-key state** (4.8).
10. **Label parity.** Recompute the privacy label of every moved file under the new paths, the new defaults
    and the rewritten overlay. A note that became less private aborts (4.9). One that became more private is
    reported. With R1's space defaults, the `svj-*` notes stay private.
11. **Seeds, README, record.** Create missing v2 folders, `reports/`, the README; write `spaces:` if missing;
    append `layout-v2` to `plugin.applied_migrations` with the comment-keeping writer (`bootstrap.py:1132`).
12. **The report** (4.11), written before the second commit so it's part of it.
13. **Commit 2: content.** Same subject prefix, trailer `Scout-Migration: layout-v2 content`. Save
    `refs/scout/migrations/layout-v2/post`. The two commits are one undo by range.
14. Delete the journal. The upgrade continues with managed files: they render at their new paths against the
    merge bases moved in step 9, the brain skills merge against the rewritten snapshots, and the jobs, shim,
    git hook and pointer are reinstalled on v2 paths.

The subjects use the `scout-update:` prefix so the changes map classes the move as an update, not a
self-change offered for undo (`E/changes/scan.py:29, 54-61`).

### 4.6 The identity and role split

For each note decided by frontmatter `type` (person or organization), not by folder:

1. `git mv` the note to its role path (commit 1), so the history follows the facts.
2. Role note frontmatter (commit 2): drop `name` and `aliases`; set `type: role`, `role_of`, `space`. Every
   other property, `relationships` and the body stay.
3. Write the identity note: name, type, aliases, handles, `spaces: [<S>]`, every privacy key the original
   carried, and one link line. A private person stays private in both files. Which frontmatter keys
   `E/kb/privacy.py` reads must be confirmed in implementation, and so must whether `validate()` rejects
   `spaces` (if it does, the loader strips it before validation; the vault's `schema.yaml` is a seed that is
   never overwritten, `bootstrap.py:238`).
4. `[[people/<n>]]` keeps resolving to the identity note. `[[ontology/entities/<n>]]` for an organization is
   rewritten to `[[organizations/<n>]]` in current files.

### 4.7 The meetings merge

Both sources of a series land in `knowledge-base/<S>/meetings/<slug>/` (two series exist on both sides in
David's vault). A file name present on both sides: identical bytes keep one copy. Otherwise the newer one
keeps the name and the other is placed beside it as `<stem>.from-kb-meetings.md` or
`<stem>.from-root-meetings.md`. Nothing is dropped.

Registry rows are keyed by the slug of their link target, else by the normalised meeting name, and the tables
are unioned. On a same-key clash, the row from the side holding the series' newest dated file wins; the
report names the slug and the blob SHA of the losing registry, never row text (rows can carry attendees).
Rows are reduced to slug, space and match pattern (3.3); anything else in a row moves to the series' home
file. The merged registry is a `git mv` of the larger registry plus an edit.

### 4.8 Merge bases, render history and brain snapshots

The failure to avoid: `_stage_managed_files` ends with `prune_snapshots` (`bootstrap.py:439`), which deletes
the base of any path no longer managed (`E/scripts/vault_drift.py:401-412`). Rename a managed path without
care and its base is pruned, the file is written fresh, and the old one stays behind orphaned.

- Bases under `.scout-state/last-rendered/` and parked copies under `.scout-state/drift/` move with their file
  (row 23), and get the same path rewrite as the live file. Equal before means equal after, so an unedited
  file still fast-forwards and an edited one still merges.
- Render history (`defaults/render-history.json`, keyed by `vault_rel`, used at `bootstrap.py:421`): lookup
  falls back to the v1 key, and `plugin/scripts/gen-render-history.py` writes v2 keys from now on.
- Brain snapshots live at `.scout-state/last-assembled/<KIND>.md`, keyed by kind, so they don't move. They are
  rewritten in place, and their provenance hash is re-recorded, but only when the old hash matched before the
  rewrite. Otherwise `is_plugin_assembly` (`E/scripts/brain_merge.py:117`) would send every brain skill down
  the sidecar path.
- `sidecar_name`, `pending_brain_sidecars` (`brain_merge.py:48-54`), the cat-4 stages and
  `resolve_brain_file` resolve through the layout module.

### 4.9 Failure and recovery

- **Before commit 1:** reverse the journal's renames, restore the `pre/` state, delete the journal, defer with
  the step and the error. Never `git reset --hard`: the session lock doesn't cover the app's own git writes
  (`apps/macos/Scout/PerFileItems/PerFileItemWriter.swift:133`,
  `KnowledgeBase/KnowledgeBaseFileWriter.swift:218-281`), Obsidian or an interactive session, and a reset
  would destroy their edits.
- **Between the commits:** finish forward. The renames are committed and the content step is repeatable.
- **A crash:** the next upgrade or `scoutctl bootstrap doctor` finds the journal. The compatibility links keep
  the heartbeat alive, so self-update can retry. While the journal exists, the tick, the heartbeat and trigger
  runs skip with reason `layout move in progress`.
- **The owner's rollback:** `git revert` the two commits by range, then `scoutctl bootstrap upgrade` on the
  release before layout v2. This is documented in the upgrade notes as manual steps. A revert alone doesn't
  restore the untracked state, the jobs or the pointer, so Q12's "one revert" is that procedure, not a single
  command.

### 4.10 Rewriting old paths (Q15, R3)

One function, `rewrite(text, moves, *, kind)`, driven by the exact move list of this vault. It is also used by
Part 2.

What it rewrites:

1. Literal paths, everywhere a file is rewritten: `<vault abs>/<old>`, `$SCOUT_DIR/<old>`, `${SCOUT_DIR}/<old>`,
   `$SCOUT_DATA_DIR/<old>`, `{{SCOUT_DIR}}/<old>`, and vault-relative `<old>` at a token boundary.
2. In current files only, wikilinks and Markdown links in vault-relative or KB-relative form, with `.md`,
   `|alias` and `#heading` kept: `[[projects/x/x]]` becomes `[[work/projects/x/x]]`.
3. Bare brain-skill names (`SKILL.md`) only inside the brain skills and the vault's own scripts and runners.
   Elsewhere the name often means another repository's skill file (the archive has 2,586 such mentions).

Which files:

| Class | Rewritten |
|---|---|
| Brain skills, vault scripts, runners and hooks | literal paths and the brain-name rule |
| `scout-config.yaml` | glob-aware: `privacy:` overlay, `kb_lint:` globs, `kb_structure:` |
| Current KB notes, records, `inbox/`, today's daily action-items file and context notes | literal paths and links |
| `action-items/archive/`, dated files under `<space>/sources/` | nothing |
| Merge bases and brain snapshots | the same rewrite as their live file |

Glob rewriting: `knowledge-base/projects/**` becomes one entry per registered space
(`knowledge-base/work/projects/**`, ...), not `knowledge-base/*/projects/**`, which would also match
`knowledge-base/lab/` and `from-agents/`. A glob the function can't place is left as written and reported.

Archive links don't break. kb lint accepts a link whose last segment is a known note
(`E/kb/lint.py:389-394`); the KB graph resolves by name; the app matches a path suffix
(`KBGraph.swift`, `KBIndex.resolve`). Of the 9,957 `[[projects/...]]` links in David's vault, 9,617 are in
the archive. Obsidian is expected to resolve the same suffix, and that is checked on a copy before David's
real move (section 9). Only a link that would stop resolving, or become ambiguous because two spaces hold a
note of the same name, goes to the report.

The vault's own scripts get only the literal rewrite. A script that finds the vault as "one folder above me"
is wrong after the move. The stage scans moved scripts for `dirname`, `parent.parent`, `BASH_SOURCE` and
`../` and lists them under "check by hand" (66 of 107 in David's vault).

### 4.11 The migration report

`reports/YYYY-MM-DD-layout-v2.md`, in commit 2. It holds file and folder names, counts and blob SHAs only,
never note content. Sections: summary and the refs; files per space and the unregistered-prefix hint;
meetings clashes; identity notes written; references that won't resolve; config globs rewritten or left;
scripts to check by hand; notes that became more private; other work trees; unknown root entries; kb lint
leftovers after the move; next step (the space review). A fenced JSON appendix holds the full move list.

### 4.12 What each update path does

- **`/scout-update`.** Step 0 runs `scoutctl vault migrate --plan --json`. A pending brain sidecar is resolved
  first (R2), as Step 3 already explains for sidecars. Step 1 says "This version reorganises your vault: N
  files move, M files get path updates", links the plan, asks before a checkpoint commit and waits for a yes.
  Step 2 is the upgrade. Step 3 reports the move and links the report. A new step offers the space review.
- **Self-update.** Unattended, under the lock. The push adds "Scout reorganised your vault (N files moved).
  Report: reports/... Review the spaces with /scout-update." A deferral gets its own line with the reason, and
  doesn't count toward the failure cap. David's engine is `managed_by: dev`, which self-update never touches,
  so his move is always interactive.
- **Scout.app.** It runs the same `bootstrap auto` (`apps/macos/Scout/Engine/EngineInstaller.swift:355`) and
  logs the migration block. Run health shows the user what matters: yellow "space review pending", yellow
  "unknown files in the vault root", red "layout move interrupted".
- **Version skew.** An app or plugin older than the vault's layout can write to v1 record paths. For example,
  an older app's wishlist writer recreates `docs/wishlist/` and commits into it (`PerFileItemWriter.swift:127-133`).
  Every upgrade on a v2 vault therefore re-runs the record rows of the path map: a v1 record folder that
  reappears is moved again and reported. The supported combinations go in the upgrade notes.

## 5. Part 2: the space review

The rules put most knowledge in the default space. Part 2 corrects that with the user.

1. **Candidates.** `scoutctl vault space-candidates --json` (read-only) lists notes that sit in the default
   space only by rule 3 and show a signal against it: frontmatter `domain:` (50 notes in David's vault say
   `domain: personal`), where their links mostly point, relationship types such as `family_of`, for a meeting
   series whether its attendees have work role notes.
2. **Judgement.** An interactive session (the `/scout-update` step, or any later one, never a scheduled run)
   reads the candidates through `scoutctl kb read`, and proposes a space, a reason and a confidence for each.
   The user approves row by row in the conversation.
3. **Moves.** Each approved row runs `scoutctl vault move <path> --space <key>`. That is `git mv`, the same
   `rewrite` with a one-entry move list, and for a role note the `space:`/`spaces:` edits plus the rename to
   `<n>-<key>.md`; for a series, the registry's Space cell. It runs under the session lock and refuses inside a
   run. One commit per batch, hooks on.
4. **Unattended installs.** The wrap says "N notes may belong to another space; review them with
   /scout-update", and run health shows the yellow finding until a review has run or the user dismisses it.

## 6. `scoutctl vault layout --json`

Read-only, no lock, works on v1 and v2 vaults. Every path is vault-relative. It never prints the vault's
absolute path, a note name or a file's content.

```json
{
  "schema_version": 1,
  "layout_version": 2,
  "migration": {"id": "layout-v2", "state": "applied"},
  "paths": {
    "config": "scout-config.yaml",
    "brain_dir": "scout/brain",
    "runners_dir": "scout/runners",
    "scripts_dir": "scout/scripts",
    "hooks_dir": "scout/hooks",
    "page_standard": "scout/kb-page-standard.md",
    "records_dir": "scout/records",
    "mistake_audit": "scout/records/scout-mistake-audit.md",
    "session_log": "scout/records/session-log.md",
    "connector_health": "scout/records/connector-health.md",
    "dreaming_proposals_dir": "scout/records/dreaming-proposals",
    "wishlist_dir": "scout/records/wishlist",
    "knowledge_base": "knowledge-base",
    "kb_index": "knowledge-base/knowledge-base.md",
    "meetings_registry": "knowledge-base/meetings.md",
    "review_queue_dir": "knowledge-base/review-queue",
    "research_queue_dir": "knowledge-base/research-queue",
    "people_dir": "knowledge-base/people",
    "organizations_dir": "knowledge-base/organizations",
    "action_items_dir": "action-items",
    "context_notes_dir": "action-items/context",
    "drafts_dir": "drafts",
    "reports_dir": "reports",
    "inbox": "inbox/inbox.md",
    "drop_dir": "inbox/drop"
  },
  "brain": {"SKILL": "scout/brain/SKILL.md", "DREAMING": "scout/brain/DREAMING.md",
            "RESEARCH": "scout/brain/RESEARCH.md", "RESPOND": "scout/brain/RESPOND.md"},
  "space_paths": {"root": "knowledge-base/{space}", "projects": "knowledge-base/{space}/projects",
                  "topics": "knowledge-base/{space}/topics", "sources": "knowledge-base/{space}/sources",
                  "meetings": "knowledge-base/{space}/meetings", "people": "knowledge-base/{space}/people",
                  "organizations": "knowledge-base/{space}/organizations"},
  "spaces": [
    {"key": "work", "default": true, "visibility": "normal", "root": "knowledge-base/work"},
    {"key": "personal", "default": false, "visibility": "private", "root": "knowledge-base/personal"}
  ],
  "categories": {"system": ["scout-config.yaml", "README.md", "scout"], "records": ["scout/records"],
                 "generated": ["action-items", "drafts", "reports"], "knowledge": ["knowledge-base"],
                 "user_input": ["inbox"]}
}
```

(Abbreviated: the real document lists every path key in the layout table.) `schema_version` bumps on a
breaking change. `layout_version` is the vault's (1 or 2). On a v1 vault the keys carry v1 paths and `spaces`
is empty. Clients ignore unknown keys.

The contract fixture follows the repo pattern:

- Canonical: `plugin/engine/tests/fixtures/contract/vault-layout.json`, generated from a vault built by
  `tests/unit/test_vault_layout_contract.py` (spaces `work`, `personal`, `svj`) with
  `env SCOUT_REGEN_CONTRACT=1 .venv/bin/python -m pytest tests/unit/test_vault_layout_contract.py`. Never
  hand-edited.
- App copy: `apps/macos/ScoutTests/Fixtures/vault-layout.json`, compared by a new block in
  `.github/workflows/contract.yml`.
- Pins: `apps/macos/ScoutTests/Engine/VaultLayoutContractTests.swift` asserts that each path constant the app
  hardcodes equals the fixture's value. The app doesn't call the command yet (Q14), but its constants can't
  drift from the engine without failing CI.
- A new bullet in the root `CLAUDE.md` contract rules.

## 7. Code that changes

The design agent's scan of the post-merge tree, checked by the reviewer, which added the items marked
*(review)*. Counts are files with a hit / hits, and they over-count: `scripts/` also matches the repository's
own `scripts/`, and `SKILL.md` matches plugin skills.

| Area | Files with hits | Hits | Files that change |
|---|---|---|---|
| `plugin/phases` | 27 | 246 | 27 |
| `plugin/templates` | 25 | 197 | 25, plus 2 new |
| `plugin/engine/scout` | 91 | 544 | about 60, plus 2 new modules |
| `plugin/hooks` | 2 | 13 | 2 (texts, write-gate union) |
| `plugin/commands`, `plugin/skills` | 11 | 70 | 6 |
| `plugin/engine/tests` | 299 | 3,226 | most, by cluster |
| `apps/macos/Scout` | 37 | 94 | about 9, constants only |
| `apps/macos/ScoutTests` | 69 | 379 | about 20, plus 2 new |
| living docs | 129 | 2,385 | about 16; dated specs and plans stay as written |

### 7.1 Engine (`plugin/engine/scout`)

- **New:** `layout.py` (the path table: v2 for everything, v1 for read-only callers only; helpers
  `brain_path`, `runner_path`, `script_path`, `record_path`, `space_dir`, `default_space`, `legacy_rel`), and
  `layout_migrate.py` (planner, rewrite, apply, report, recovery). The space registry loader lives in
  `config.py`.
- **Bootstrap:** `scripts/bootstrap.py` tables (`_CAT1_DIR_LAYOUT`, `_CAT1_FILES_FROM_PLUGIN`,
  `_CAT_MERGE_FILES`, `_CAT1_TEMPLATES`, `_INSTALL_ONLY_TEMPLATES`, `_CAT1B_RUNNERS`; the directory table becomes
  a function of the spaces), `_stage_migrations`, `_stage_version_stamp`, the cat-4 stages,
  `resolve_brain_file`, `blocking_sidecars`, the render-history fallback at `:421`, `migrate_legacy` (refuses
  and asks for `upgrade`). `scripts/brain_merge.py`, `scripts/vault_drift.py` (`VAULT_DEVELOPED_FILES`,
  `privacy_drift` root globs). `scripts/bootstrap_doctor.py`: required files, the heartbeat job check (it only
  looks for the substring `heartbeat.sh`, `:285`; it gains "the script the job runs exists"), new checks for
  an interrupted move, unknown root entries and compatibility links left behind. `scripts/bootstrap_auto.py`
  and the `cli.py` bootstrap commands pass the migration block through `--json`.
- **Self-update:** `scripts/self_update_run.py` and `update_outcomes.py`: a deferral outcome outside the
  failure cap, and the push line.
- **Jobs:** `defaults/com.scout.heartbeat.plist`, `scripts/install_heartbeat_plist.py`,
  `scripts/install_cron.py:103` (v2 heartbeat path).
- **Runs:** `scripts/schedule_tick.py:552`, `triggers/actions/run_skill.py:87`, `scripts/heartbeat.py:603-604`
  resolve runners through the layout and skip while a move is in progress. `runner:` in the schedule stays a
  bare name, so `schedule.py`, `defaults/schedule.yaml` and `schedule.snapshot.json` don't change (one comment
  at `schedule.py:90`).
- **Live-run detection** *(review)*: `kb/live_run.py:229` and the pre-commit hook template in `kb/hook.py:94-95`
  match `<vault>/scripts/claude-with-retry.sh`. Both match the v1 and v2 paths during the transition.
- **Records:** `scripts/connector_health_report.py`, `resolve_prompt.py` (then regenerate the resolve-prompt
  fixture), `wishlist/log_move.py`, `wishlist/cli.py`, `changes/scan.py`, `changes/actions.py`,
  `changes/vault.py` (the scanner keeps recognising v1 paths in old commits), `scripts/migrate_perfile.py`
  (stays first and writes v1 paths, which the layout move then moves).
- **KB:** `hooks/kb_pre_filter.py` (`KB_ROOTS` back to `("knowledge-base",)`; `SKIP_KB_SUBDIRS` loses the moved
  records; the meeting rules match `knowledge-base/<space>/meetings/<slug>/`; `/personal/` skipping extends to
  private spaces). `kb/ontology.py` (role fold with the strictest label; `DEFAULT_EXCLUDE` per space).
  `kb/lint.py` (identity-note rule), `kb/lint_config.py`, `kb/structure.py` (`projects_dir` and `linked_dirs`
  become per-space lists from the registry; a single v1 value reads as the default space's),
  `kb/lossless.py:16`, `kb/share.py:529`, `scripts/session_context_kb.py`, `delegate_mirror.py:1104`,
  `kb/read.py` search slices (records now share the `scout/` slice).
- **Gates:** `kb/raw_read.py` unchanged in scope (records ungated, R6); message texts that mention
  `scripts/scoutctl` change to `scout/scripts/scoutctl`. `kb/hook_paths.py` `RUNNER_FILES` takes the union of v1
  and v2 paths. `sandbox/broker.py`, `sandbox/replay.py`, `sandbox/integrity.py`, `sandbox/profile.py`: helper
  paths through the layout.
- **Privacy:** `kb/privacy.py` reads the space visibility defaults from the registry (R1).
- **Other:** `diagnostics.py` (structural names, v1 and v2, in step with the app's scrubber),
  `health.py` (new findings), `doctor_triage.py`, `action_items/retitle.py`, `delegate_apply.py`,
  `kb/locate.py`, `hooks/kb_read_label.py`, `cli.py` (new `vault` group: `layout`, `migrate`,
  `space-candidates`, `move`).

### 7.2 Defaults

- `defaults/privacy.yaml` and `privacy.snapshot.json` (regenerate with `python -m scout.scripts.privacy_snapshot`):
  `visibility_defaults` gain one entry per private space, generated from the registry; `federation_ceilings`
  for topics, projects, sources and organizations become per-space entries, and the v1 globs are kept while a
  v1 vault is readable (otherwise `folder_ceiling` returns none and sharing on a deferred vault switches off
  silently, `E/kb/privacy.py:1139-1141`). `reports/**` is not shared.
- `defaults/scout-config.yaml`: `kb_lint` globs (records to `scout/records/...`; projects per space; a class
  for dated meeting files), `kb_structure`, a commented `spaces:` block.

### 7.3 Phases, templates, commands

- **Phases (27 files):** script paths to `scout/scripts/` in 24 files; brain paths in commit lists
  (`modes/feedback-processing.md:111` and others); records paths; meetings (`core/meeting-prep.md:15,18`,
  `core/inbox-processing.md`); inbox (`core/inbox-processing.md:11,21,25`). The KB-writing phases
  (`core/kb-management.md`, `core/write-protocol.md`, connector phases, `research/research-targets.md`) get two
  short shared rules: which space a new note goes in (the default unless it is personal or belongs to a
  registered space), and the identity/role rule for a new person or organization. New rules: one-off reports
  go to `reports/`; a record links to a note and never copies its text (R6). Edits are produced by running
  the migration's own `rewrite` over `plugin/phases` and reviewed by hand, so the next brain merge sees only
  real phase changes.
- **Templates:** the four runners (every `$SCOUT_DIR/scripts/...`, `$SCOUT_DIR/hooks/kb-pre-filter.sh`, the
  brain path in the prompt; the run-mode phrases `hooks/run_context.py` parses stay word for word); the
  self-locating scripts (`scripts/scoutctl.sh.tmpl:32`, `scripts/scout-tz.sh.tmpl:69-71`,
  `scripts/recurring-task-status.py:85,233-235,623`, `scripts/vault-freshness.py:181`,
  `scripts/session-lane-liveness.py:81`) walk up to the folder holding `scout-config.yaml` instead of assuming
  a depth; other script templates' path texts; `knowledge-base/ontology/parser.py:168-172`; the seeds
  (registry with a Space column and the occurrence `type`, `knowledge-base.md`, `session-log.md`,
  `dreaming-proposals.md`, `kb-page-standard.md`); `.gitignore.tmpl` (`inbox/drop/*`, `!inbox/drop/README.md`,
  `.scout-state/migrations/`); new `README.md.tmpl` and `inbox/drop/README.md`.
- **Commands and skills:** `commands/scout-update.md` (4.12), `commands/scout-setup.md` (spaces question),
  `commands/scout-status.md`, `commands/scout-meta-review.md`, `commands/scout-work.md`,
  `skills/scout-ask/SKILL.md` (KB path table per space), the five launcher skills, and
  `plugin/scripts/vault-repoint-monorepo.py`, `plugin/scripts/gen-render-history.py`.
- **Hooks:** `plugin/hooks/prefilter.sh` and `hooks.json` texts that mention `scripts/scoutctl`. No new KB patterns
  (R6).

### 7.4 App (constants only, Q14)

One new `Shell/VaultLayout.swift` holds the constants, so the contract test has one place to pin. Switches:
the proposals default (`Shell/AppState.swift:328`, `Shell/SettingsView.swift:289`), the wishlist default
(`PerFileItems/PerFileTabConfig.swift:32`), the Projects hub link (`KnowledgeBase/Views/KBOverviewView.swift:24`,
pointing at the default space), folder grouping in `KnowledgeBase/Models/KBGraph.swift` and
`KBMapPayload.swift` (spaces as groups; `svj` with `personal`; a group for meetings),
`Help/DiagnosticsScrubber.swift` (in step with `diagnostics.py`; role-note folders count as people, `:234`
*(review)*), texts that mention `knowledge-base/connector-health.md` (`Models/ConnectorHealthMatrix.swift`,
`Utilities/PlainLanguage/Problem.swift`). A stored `dreamingProposalsPath` or `wishlistPath` override that
equals the old default is cleared on launch. Unchanged: `action-items/` and context-note paths, the KB root,
runner names, the `obsidian://open?vault=Scout` link.

Tests: the new contract test and fixture copy; regenerated copies and pins for `doctor-run-health.json` and
`resolve-prompt.json`; path literals in `PerFile/*Tests.swift`, `Proposals/*Tests.swift`,
`Help/DiagnosticsBundleTests.swift`, `KnowledgeBase/KBEntityGroupTests.swift`, `Support/BigVault.swift`,
`Fixtures/plists/com.scout.heartbeat.plist`.

### 7.5 Tests and fixtures

- **New:** `test_layout.py`; `test_layout_migrate_plan.py` and `test_layout_migrate_apply.py` (a fixture vault
  with every row of the path map, a dirty tree, an ignored `drop/` file, a parked drift copy, a vault without
  recorded bases; apply equals plan); `test_layout_migrate_recovery.py` (kill after the move step: the next
  heartbeat still starts self-update; recovery reverses the journal); `test_layout_rewrite.py` (idempotent,
  boundaries, archive untouched); `test_layout_label_parity.py`; `test_ontology_role_fold.py` (strictest
  label); `test_vault_layout_contract.py`; an end-to-end upgrade of a 0.18-shaped vault that runs each runner
  dry; a test that every moved file shows as a rename in commit 1; a test that a v2 run is detected by
  `live_run` and the pre-commit hook.
- **Existing:** bootstrap, drift and brain-merge tests; hook and gate tests including
  `test_hooks_write_gate.py` and the prefilter differential; kb lint, structure, privacy and share tests;
  runner, tick, heartbeat and trigger tests; wishlist and changes tests; the eval harness and `eval/vault`.
- **Contract fixtures regenerated, each with its app copy and pins in the same commit:**
  `doctor-run-health.json`, `resolve-prompt.json`, `changes.json`, plus the new `vault-layout.json`.
  `privacy-corpus.json` is authored: only cases that rely on a changed glob get new paths. The parser corpus
  holds no vault paths (checked) and doesn't change.

### 7.6 Docs and changelog

`CONTEXT.md` (terms added in this PR), ADR 0011 (this PR), `docs/architecture/` (5 files), `plugin/README.md`,
the root `README.md`, root `CLAUDE.md` (contract bullet), `plugin/CLAUDE.md`, `apps/macos/CLAUDE.md`,
`plugin/engine/scout/docs/connectors/*.md`, `docs/runtimes.md`. Dated specs and plans stay as written.
Changelog: `plugin/changelog.d/<slug>.changed.md`, `plugin/changelog.d/<slug>.upgrade-notes.md` (what moves,
the rollback procedure, supported version combinations), `apps/macos/changelog.d/<slug>.changed.md`.

## 8. Delivery

One cross-cutting commit on a branch from `de-scout-next`, with `contract.yml` as the gate (root `CLAUDE.md`,
"Cross-cutting changes"). The diff is large but mechanical in most places, so the PR description groups it by
section 7's areas and leads with `layout.py`, `layout_migrate.py` and the role fold.

## 9. Rollout on David's vault

David's vault runs from the dogfood checkout `~/Projects/scout-next` with scheduled runs paused, and its engine
is `managed_by: dev`. The move is interactive and goes in this order:

1. Resolve the three pending brain sidecars (R2).
2. Add `svj` (private) to `spaces:`.
3. On a copy of the vault: `scoutctl vault migrate --plan`, then `--apply`. Read the report, run each slot dry
   (`scoutctl schedule fire-now` against the copy with runs paused), check the 66 self-locating scripts, and
   open the copy in Obsidian to confirm that old `[[projects/...]]` links in the archive still resolve.
4. The real move through `/scout-update` from the dogfood checkout, then the space review.
5. Merge or retire the vault's other work trees, which still hold the v1 layout.

## 10. Risks

- **Size.** About 930 moved files and 60 engine modules in one change. The two-commit split, the plan equals
  apply test and the end-to-end upgrade test are the main safeguards.
- **Brain skills edited by hand.** A heavily customised brain gets only the mechanical rewrite of its own
  text, plus whatever the next merge brings. That is why R2 makes resolved sidecars a precondition.
- **The vault's own scripts.** The 66 self-locating scripts are a hand check, and so is anything outside the
  vault that calls a vault script by path.
- **Interactive sessions** open during the move keep old paths in their context. The compatibility links
  cover their tool calls until the next upgrade; the upgrade notes say to restart them.
- **Sync and delegates.** A vault that syncs (`repo_sync.py`) pushes two large commits. Whether
  `.scout-state/delegate-mirrors.json`, the privacy index or `id-map.json` store vault paths is unverified and
  checked first in implementation.
- **`.obsidian/workspace.json`** holds old paths. It is ignored and harmless; Obsidian shows a few missing
  tabs once.

## 11. Review record

The adversarial review found 18 issues, 4 of them blockers. All four were checked in code and changed the
design:

1. Nothing could retry a failed move, because the heartbeat runs a vault script. Fixed by the compatibility
   links (4.5 step 5).
2. Runners queued on the lock wake up with v1 paths. Fixed by precondition 1, the links and atomic replace.
3. The `svj-*` notes would have become normal. Fixed by R1.
4. Pending brain sidecars keep phase changes from reaching the brain. Fixed by R2.

Majors folded in: the role fold takes the strictest label; live-run detection matches both paths; version
skew between engine, plugin and app (precondition 6, record re-homing); deferral is not a failure; v1
ceiling globs kept; rewrite scope (R3); two commits instead of one; recovery by journal, not reset; layout
decided from disk; departures from the decisions put back to David (R4, R5, R6).

On Q6 the reviewer argued both sides and kept the decision: one home fixes a real defect, splitting home
files from dated files would rebuild the two-home bug, and action items differ because they are a work queue
the app re-renders, while a meeting file records what was said. Its conditions are in 3.3: dated files get
their own lint, freshness and search class and a `type`, and the registry keeps the reduced row.

Left for Feature 5 (OKF): 631 of 739 KB Markdown files have no `type` today, and the index files are named
`projects.md`, `topics.md` and `meetings.md`, not `index.md`. If OKF wants `index.md` per folder, that is its
own rename pass.
