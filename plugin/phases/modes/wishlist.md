---
phase: mode
name: wishlist
slot: dreaming-phase-3
mode: [dreaming]
requires: null
---

## Phase 3: Wishlist Processing

Process items from {{USER_NAME}}'s wishlist — feature requests, improvements, and ideas that {{USER_NAME}} wants {{INSTANCE_NAME}} to implement during dreaming runs.

***

### Step 3a: Read the Wishlist

Read every `*.md` file in `docs/wishlist/` (not its `progress/` folder). Each file is one item: YAML frontmatter plus a body describing the feature/improvement.

**Frontmatter schema:** `title`, `status` (`open`|`in-progress`|`done`|`dropped`), `priority` (`urgent`|`high`|`medium`|`low`), `date` (`YYYY-MM-DD`), optional `source`.

**Item states (frontmatter `status:`):**

| `status` | Meaning |
|---|---|
| `open` | Not yet started |
| `in-progress` | Work has begun, may need more runs to complete |
| `done` | Completed — git is the archive; do not move or delete |
| `dropped` | Decided against |

State lives in the frontmatter `status:` — there is no file-moving. Scout.app and this phase both filter by it. Identify all items and their current state before proceeding.

***

### Step 3b: Identify Actionable Items

Scan for items with `status: open`. For each, assess feasibility:

**Implementable in one run:** The item is clear, scoped, and can be fully completed in this dreaming session. Examples: "add a new section to SKILL.md", "create a KB template for architecture decisions", "update the freshness standards table."

**Multi-run effort:** The item is too large for a single session but can be broken into sub-tasks. Examples: "audit all project files for consistency", "redesign the action items format." Break these into concrete sub-tasks that can each be completed independently.

**Too ambiguous:** The item is unclear enough that attempting it risks wasted work or incorrect implementation. Examples: "make Scout smarter", "improve the KB." Skip these — they need clarification from {{USER_NAME}} before work begins. Do not guess at intent.

***

### Step 3c: Maximize Wishlist Progress

Select actionable items and push as far as possible. Don't artificially limit yourself to one sub-task — complete multiple sub-tasks or even multiple items if time allows. The constraint is quality, not count.

**Priority order:**
1. Items {{USER_NAME}} has explicitly flagged as important (check recent feedback)
2. First actionable item on the list (not done, not blocked)
3. In-progress items with remaining sub-tasks

**Execution rules by item type:**

- **Skill or documentation changes**: Edit the target file directly. Follow existing conventions and formatting in the file. SKILL.md changes follow Step 1d (the proposal gate is retired): additive or corrective edits go in directly; large, structural, behavior-removing or governance changes go to `dreaming-proposals.md`.
- **New files**: Create following the naming conventions and structure patterns established in the codebase. Link new files from their parent/index files.
- **Configuration changes**: Edit the relevant config file. Test that the change is syntactically valid.
- **Research items**: Do real research (check docs, test locally, query sources) — don't give superficial answers.
- **Multi-run efforts**: Complete as many sub-tasks as you can per run, not just one.

**Scope guard**: If an item turns out to be larger than expected mid-implementation, stop at a clean checkpoint. Set its frontmatter `status: in-progress` and record what was completed and what remains (Step 3d). Do not leave half-finished work in an inconsistent state.

***

### Step 3d: Update the Wishlist

After executing (or deciding to skip), update each item file's frontmatter `status:` in place — never move or delete the file (git is the archive; Scout.app and this phase filter by `status:`).

**The item file is a card, not a log.** Scout.app shows its whole body, so it holds only the request (a few lines) and a `## Status` section: one or two sentences on where it stands now, then the open sub-tasks. Dated entries — what each run did or delivered, verification, ticked sub-tasks — go in the item's progress note, `docs/wishlist/progress/<item file name>`, newest first, and the card links it once: `[[docs/wishlist/progress/<item stem>|Progress log]]`. Never delete a log: when you touch an item whose body holds one, move it verbatim to the note in the same commit.

```markdown
## Status
Two of four sub-tasks done; the template exists, the index link is next.
- [ ] Link the template from the KB index
- [ ] Backfill the three existing decisions

[[docs/wishlist/progress/2026-01-05-decision-template|Progress log]]
```

**For completed items:** set `status: done`; the status says what was delivered, the dated note goes in the log.

**For newly-started and continuing items:** set `status: in-progress`, rewrite the status, keep the open sub-tasks on the card (add new ones as found), and log the run's work.

**For items skipped as ambiguous:** leave the item untouched (`status: open`) for {{USER_NAME}} to clarify. Optionally log which item was skipped and why in the session summary.

**For a brand-new wishlist item:** create `docs/wishlist/<YYYY-MM-DD>-<slug>.md` with the frontmatter schema above (`status: open`). It has no progress note until a run first logs work on it.

***

### Step 3e: Commit

Explicit paths, never `-A` (see Step 1f):

```bash
{{SCOUT_DIR}}/scripts/git-safe-commit.sh "dreaming [HH:MM]: wishlist — <description of what was done>" <only the paths THIS run touched>
```

The description should name the wishlist item: e.g., "wishlist — added architecture decision KB template" or "wishlist — completed sub-task 2/4 for action items format redesign."

If no wishlist items were actionable (all done, all in progress, all ambiguous), skip the commit and note "No actionable wishlist items" in the session log.
