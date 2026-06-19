# Per-file Wishlist & Research Queue — Plugin Distribution (hand-off plan)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or executing-plans. This is the **distribution** half of the per-file restructure — it makes the migration reach all scout-plugin users on `/scout-update`. Jordan's own vault is already migrated; this plan does NOT re-touch it.

**Goal:** Bake the per-file wishlist/research-queue format into the scout-plugin so every user gets it on upgrade — both the workflow prose (assembled into DREAMING/RESEARCH) and an idempotent data migration of their existing single files.

**Status:** Planned 2026-06-17 (checkpoint). Decisions locked: **engine-step migration** in the upgrade pipeline (not prose-driven).

**Spec:** `docs/superpowers/specs/2026-06-16-wishlist-research-queue-per-file-design.md`
**Prereq context (already done):**
- Jordan's vault migrated to per-file (committed in the vault, ~103 files).
- A working, unit-tested migration prototype is committed in scout-plugin:
  `scripts/migrate_wishlist_research.py` (+ `scripts/test_migrate_wishlist_research.py`, 16 tests) on branch `feat/wishlist-research-per-file`. Its pure helpers (`parse_wishlist_item`, `parse_research_item`, `slugify`, `filename_for`, `render_item` with `_yq` YAML-quoting, `split_bullets`, `split_research_items`, `_heading_area`, `migrate_wishlist_file`, `migrate_research_file`) are the logic to port into the engine.

## Distribution model (how the plugin reaches users)

- The plugin **assembles** each vault's `SKILL.md` / `DREAMING.md` / `RESEARCH.md` from **phase files** under `phases/`, and **3-way-merges** them into the vault on upgrade (`engine/scout/scripts/bootstrap.py:_stage_merge_files_upgrade`, `three_way_merge`). **Therefore workflow-prose edits MUST go in `phases/`, not in a vault's assembled DREAMING.md** (vault edits get merged/clobbered).
- `/scout-update` runs an 8-stage pipeline: pre-flight → **migrations** → cat-1 overwrites → cat-1b runner regen → cat-4 3-way merge → jobs → version stamp → doctor. The **migrations** stage is the hook for the data conversion.

---

## Task 1: Phase prose → per-file (assembles into DREAMING/RESEARCH for all users)

**Files:**
- Modify: `~/scout-plugin/phases/modes/wishlist.md` (Phase 3)
- Modify: `~/scout-plugin/phases/research/research-targets.md` (Phase 1 queue read)

- [ ] **Wishlist (`phases/modes/wishlist.md`)** — replace Step 3a's "Read the three wishlist files (`docs/Wishlist.md` / `-in-progress` / `-done`)" + the `[in progress]`/`[done]` three-file-move model with the per-file model:
  - Read every `*.md` in `docs/wishlist/`; each is one item with frontmatter (`title`, `status` ∈ open|in-progress|done|dropped, `priority` ∈ urgent|high|medium|low, `date`, optional `source`) + body.
  - State is the frontmatter `status:` — no file moving. To start an item set `status: in-progress`; to finish set `status: done` (git is the archive). New item → create `docs/wishlist/<YYYY-MM-DD>-<slug>.md`.
  - Mirror the exact wording from the (already-correct) vault edit guidance in the original plan `2026-06-16-…-plugin-migration.md` Task 5.
- [ ] **Research (`phases/research/research-targets.md`)** — replace the `knowledge-base/research-queue.md` single-file read with: read every `*.md` in `knowledge-base/research-queue/` (the thin `research-queue.md` is the run log); `status: open/in-progress` are the queue, `done/dropped` resolved; **run `priority: urgent` items first** (START-IMMEDIATELY preemption); after a topic, set frontmatter `status` + add findings; write the "Last verified" note to `research-queue.md`. (Mirror original plan Task 6.)
- [ ] **Verify:** assemble locally and confirm the new prose lands —
  `cd ~/scout-plugin && .venv/bin/scoutctl bootstrap assemble --kind DREAMING --out /tmp/DREAMING.md` (confirm the exact assemble subcommand via `scoutctl bootstrap --help`); grep the output for `docs/wishlist/` and absence of `Wishlist-done.md`. Same for RESEARCH.
- [ ] **Commit** (scout-plugin, explicit paths): `git add phases/modes/wishlist.md phases/research/research-targets.md && git commit -m "feat(phases): per-file wishlist + research queue workflow"`

## Task 2: Fresh-install templates (per-file dir seeds)

(Identical to the original plan's Task 7 — repeated here for completeness.)
- [ ] `git rm` `templates/docs/Wishlist.md.tmpl`, `Wishlist-in-progress.md.tmpl`, `Wishlist-done.md.tmpl`; `mkdir -p templates/docs/wishlist && touch templates/docs/wishlist/.gitkeep`; `mkdir -p templates/knowledge-base/research-queue && touch templates/knowledge-base/research-queue/.gitkeep`.
- [ ] Reshape `templates/knowledge-base/research-queue.md.tmpl` to the thin run-log form (title + "items live in [[research-queue/]] … run log" + `_No runs yet._`).
- [ ] Fix `templates/run-research.sh.tmpl` ("check research-queue.md first" → "check the `research-queue/` folder first"); audit `commands/scout-status.md` for `Wishlist`/`research-queue` path reads and update to the new dirs.
- [ ] Confirm no stale refs: `grep -rn "Wishlist.md\|Wishlist-done\|Wishlist-in-progress" ~/scout-plugin --include=*.tmpl --include=*.md --include=*.sh | grep -v /.git/` (only historical changelog entries OK).
- [ ] **Commit** (explicit paths).

## Task 3: Engine migration module (idempotent) + tests — TDD

**Files:**
- Create: `~/scout-plugin/engine/scout/scripts/migrate_perfile.py` (port the prototype logic from `scripts/migrate_wishlist_research.py`)
- Create: `~/scout-plugin/engine/tests/unit/test_migrate_perfile.py`

- [ ] **Step 1 (test first):** in `engine/tests/unit/test_migrate_perfile.py`, build a synthetic legacy vault in `tmp_path` (a `docs/Wishlist.md` with 2 bullets, a `knowledge-base/research-queue.md` with a `## Queue` + 1 item), then:
  - `needs_migration(vault)` is True for the legacy vault; after `migrate_perfile(vault)`, `docs/wishlist/` has 2 files, `knowledge-base/research-queue/` has 1, old `Wishlist*.md` gone, `research-queue.md` reduced to thin log.
  - **Idempotency:** `needs_migration(vault)` is now False and a second `migrate_perfile(vault)` is a no-op (counts unchanged, no exception).
  - A vault with neither old file → `needs_migration` False, `migrate_perfile` no-op.
- [ ] **Step 2:** run, watch fail.
- [ ] **Step 3:** implement `migrate_perfile.py`: port `parse_*`/`render_item`/`split_*`/`migrate_*` from `scripts/migrate_wishlist_research.py` (or import them if the scripts dir is importable from the engine — prefer copying into the engine module so the engine is self-contained). Add:
  - `needs_migration(vault) -> bool`: True iff `docs/Wishlist.md` exists OR `knowledge-base/research-queue.md` contains a `## Queue`/`- [ ]` checklist line (i.e., not already the thin log).
  - `migrate_perfile(vault) -> dict`: if not `needs_migration`, return `{"migrated": False}`; else run the migration (wishlist + research dirs), delete old `Wishlist*.md`, reduce `research-queue.md` to the thin log preserving the latest `**Last verified:**` paragraph, return counts.
- [ ] **Step 4:** run, watch pass.
- [ ] **Step 5:** commit (explicit paths).

## Task 4: Wire into the upgrade "migrations" stage

**Files:**
- Modify: `~/scout-plugin/engine/scout/scripts/bootstrap.py` (the migrations stage within `upgrade()` ~line 579, alongside the existing migration handling)
- Test: `~/scout-plugin/engine/tests/unit/test_bootstrap_upgrade.py` (or `test_migrate_perfile.py`)

- [ ] **Step 1 (test):** extend the upgrade test so that running `upgrade()` on a synthetic legacy-format vault leaves it per-file migrated (calls `migrate_perfile` in the migrations stage). Watch fail.
- [ ] **Step 2:** in `upgrade()`'s migrations stage, call `migrate_perfile(cfg.vault)` (idempotent, so safe every upgrade). Place it before the cat-4 3-way merge so the new assembled DREAMING/RESEARCH land on an already-migrated vault.
- [ ] **Step 3:** run engine unit tests green: `cd ~/scout-plugin && .venv/bin/python -m pytest engine/tests/unit/test_migrate_perfile.py engine/tests/unit/test_bootstrap_upgrade.py -q`
- [ ] **Step 4:** commit.

## Task 5: End-to-end verification

- [ ] Build a synthetic legacy vault, run the full `scoutctl bootstrap upgrade` against it, confirm: per-file dirs created, old files gone, thin log, and the assembled DREAMING.md/RESEARCH.md reference `docs/wishlist/` + `research-queue/`. Run twice → second run is a clean no-op (idempotent).
- [ ] Run the whole engine unit suite to confirm no regressions.
- [ ] Decide branch/merge: the scout-plugin work is on `feat/wishlist-research-per-file` (branched off `perf/batch-4`); confirm the right base with Jordan before opening the plugin PR.

---

## Notes / risks
- **Concurrency:** a scheduled consolidation session committed to Jordan's vault mid-migration (its `git add -A` swept the migration into its commit — data correct, message commingled). When testing engine migration, use throwaway temp vaults, not `~/Scout`.
- **`migrate_legacy` is separate** (pre-config vaults) — do not conflate; the per-file migration is a normal upgrade-stage migration for all current vaults.
- After this, **sub-project 3 (the app Wishlist + Research tabs)** is the remaining piece — a near-copy of the Proposals feature pointed at `docs/wishlist/` and `knowledge-base/research-queue/`, with an "add item" writer. Its own spec→plan→build.
