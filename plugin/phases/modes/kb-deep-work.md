---
phase: mode
name: kb-deep-work
slot: dreaming-phase-2
mode: [dreaming]
requires: null
---

## Phase 2: KB Deep Work

This is the substantive knowledge-base improvement phase. Score every KB file, pick a work mode, execute deep improvements, and leave the KB meaningfully better than you found it.

***

### Step 2-cache: Read the Pre-Session Caches

Before doing anything else, read the caches that the runner script generated before Claude started. These are already on disk — don't re-query what they already contain.

| Cache file | What's in it | Replaces |
|---|---|---|
| `.scout-cache/kb-filter.md` | Per-file staleness: which KB files are stale, fresh, or undated, with ages | Walking every KB file to check "Last updated" headers |
| `.scout-cache/vault-freshness.md` | The same files ranked by their last git commit against their freshness budget, plus files whose claimed "Last verified" date disagrees with git | Walking the vault's history per file to see what actually changed |
| `.scout-cache/session-context.json` | Recent vault commits (labelled rows, as `scripts/scoutctl kb delta` prints them), open PRs, PR review requests, KB file dates, open personal tasks (each labelled) | Running `scripts/scoutctl kb delta`, `gh pr list`, `gh search prs`, `scripts/scoutctl kb query` |
| `.scout-cache/cc-sessions.md` | Non-{{INSTANCE_NAME}} Claude Code sessions from the last 24h: project paths, first prompts, files touched | Manually discovering + parsing `~/.claude/projects/*/**.jsonl` |

Prefer these caches over live tool calls during scoring. Fall back to live queries only when a cache is missing, obviously stale, or you need deeper detail (e.g., full PR diff, full session transcript).

***

### Step 2-pre: Scan for Inline Comments

Before scoring files, search all `.md` files under `{{SCOUT_DIR}}` for {{USER_NAME}}'s inline comment markers:

```bash
cd {{SCOUT_DIR}} && scripts/scoutctl kb search --fixed '//==<<' --glob '**/*.md'
```

**Comment format:** `//==<< comment >>==//`

{{USER_NAME}} leaves these as embedded feedback, questions, or requests directly in KB files and docs. For each comment found:

1. **Read the comment** — understand what {{USER_NAME}} is asking for or flagging
2. **Act on it if possible** — fix content, reorganize, archive, or update as requested
3. **Remove the marker** once the comment is addressed (the action itself replaces the comment)
4. **If the comment requires research or can't be resolved this run**, leave it in place and note it in the session summary

These inline comments are high-priority — they represent explicit {{USER_NAME}} feedback embedded in the source files. Treat them with the same urgency as messaging reactions/replies.

**Batch-comment triage mode (threshold N=5).** The resolve-each behavior above assumes a handful of markers per run. When a sweep finds **more than N=5 unprocessed `//==<<` markers** (a large batch dropped in one sitting), trying to resolve them all in one run produces shallow work. Switch from *resolve* mode to **triage mode**:

1. **Do not attempt to resolve them all.** Stay high-level.
2. **Inventory** every marker (file, gist, line) into a single dated index: `knowledge-base/comment-triage-YYYY-MM-DD.md`.
3. **Categorize** each: KB-staleness · vault-org/structure · system/self-improvement feature · search-depth/coverage miss · cruft · verify · approval/decision-gated · process-directive.
4. **Route** each to the right queue with a back-link (research-queue/, wishlist/, mistake-audit, review-queue, the vault-reorg plan, the idea-capture page, or `dreaming-proposals/` for governance).
5. **Leave the markers in place** — they mark the spot; strip one only when its underlying request is *resolved* (the REM-cleanup pruning duty then removes processed ones).
6. **Surface** the triage index + any approval-gated or 🔴 items in the wrap notification, so the batch is visibly captured and routed.

Below N=5, keep the resolve-each behavior above.

### Step 2-ontology: Knowledge Graph Integrity Check

Before scoring individual files, run the knowledge graph checks:

```bash
cd {{SCOUT_DIR}} && scripts/scoutctl kb validate
cd {{SCOUT_DIR}} && scripts/scoutctl kb stats
```

**Schema validation errors to act on:**
- **Missing required properties** — fix the entity file (add the missing field)
- **Invalid relationship types** — correct the relationship type to match `schema.yaml`
- **Orphaned entities** — entities with no relationships at all. If they should have relationships, add them. If they're stubs, either enrich or note for future work.
- **Broken wikilink targets** — relationship targets that don't resolve to an entity file. Either create the missing entity file or fix the target name.

**Audits remediate or hand off — never just report.** Applies to this KG-integrity check and to any standing audit (Linear-state, action-item-health, graph-health). After the audit produces findings, split them into `auto-remediable` vs `backlog` and **act** — a report with no downstream actor is a slow leak that reads as noise:
- **Auto-remediable → fix this run** (or hand to the next run as an explicit *must-action* queue, not a passive note): a Linear-state claim the KB has wrong → re-query the issue + flip the row; a personnel/status label past its own dated window (e.g. a "leave imminent" flag whose start date has passed) → correct it + bump the file's `last_verified:`; a dangling relationship target that resolves to an existing entity under a slug → fix the target to the slug/wikilink form.
- **Backlog → leave as backlog, do NOT auto-create:** genuinely-missing entities, judgment calls, structural decisions.
- The audit's notification/report then states **what it fixed**, not only what's wrong.

**Personal task staleness detection:**

```bash
cd {{SCOUT_DIR}} && scripts/scoutctl kb query --type task --exclude-status "completed,cancelled"
```

For each personal task entity (`domain: personal`, `status: open`):
- **Stale task** = open personal task with `created_date` older than 7 days, no activity, AND no `surface_rule` currently muting it. Flag it: "Still open: [task name] — done?" (a `[private]` task only in the daily file or Telegram, never Slack). A task intentionally muted by its `surface_rule` (default `do_not_surface`, no window covering today, no `always_visible_if` firing) is **not** stale — it is deliberately backgrounded; do not flag it.
- **Deadline approaching** = task with `deadline` within 3 days. Escalate priority to 🔴 in the entity file if not already.
- **Completion signal check** = if `completion_signal: gmail_confirmation`, check Gmail for matching confirmation, with a query that passes `scripts/scoutctl kb check-query` (no private names in it). Auto-resolve if found (set `status: completed`, add `completed_date`).

Commit any entity file changes from this step before proceeding to file scoring.

### Step 2a: Score All KB Files

Score every file in `knowledge-base/`. Start from the caches (`.scout-cache/kb-filter.md`, `.scout-cache/vault-freshness.md`); list the rest with `scripts/scoutctl kb files --glob 'knowledge-base/**'` (labelled) and read a file with `scripts/scoutctl kb read <path>`. Score each file on four dimensions (0-3 each). Total possible score: 12. Higher scores = higher priority for work.

#### Dimension 1: Staleness (0-3)

Compare each file's verification date (the frontmatter `last_verified:` or `last_updated:`, else an older file's "Last verified" / "Last updated" line) against the freshness standard for its type. Score based on how far overdue the file is.

| File Type | Freshness Standard | Score 0 | Score 1 | Score 2 | Score 3 |
|---|---|---|---|---|---|
| High priority projects | 3 days | Within 3d | 3-4d overdue | 4-6d overdue | >6d overdue |
| Medium priority projects | 7 days | Within 7d | 7-10d overdue | 10-14d overdue | >14d overdue |
| Low priority projects | 14 days | Within 14d | 14-21d overdue | 21-28d overdue | >28d overdue |
| `people.md` | 7 days | Within 7d | 7-10d overdue | 10-14d overdue | >14d overdue |
| Issue tracker file | Every run | Updated this run | 1 run behind | 2 runs behind | 3+ runs behind |
| `channels.md` | 14 days | Within 14d | 14-21d overdue | 21-28d overdue | >28d overdue |
| `knowledge-base.md` (root) | Every run | Updated this run | 1 run behind | 2 runs behind | 3+ runs behind |

If a file has no verification date at all, assign staleness score 3 automatically.

#### Dimension 2: Gaps (0-3)

Assess missing content relative to what a file of this type should contain (see KB management guidelines for expected sections).

| Score | Meaning |
|---|---|
| 3 | Severely thin — missing multiple expected sections, stub-level content |
| 2 | Missing 1-2 important sections that would materially help {{USER_NAME}} |
| 1 | Reasonably complete — one area could be expanded for better coverage |
| 0 | Comprehensive — all expected sections present with substantive content |

#### Dimension 3: Structural Integrity (0-3)

Assess navigation, cross-references, and formatting.

| Score | Meaning |
|---|---|
| 3 | Multiple broken or missing `[[wikilinks]]`, not linked from parent file, orphaned from the KB graph. For entity files: missing YAML frontmatter, or the parser reports validation errors for this entity. |
| 2 | Missing 2+ expected `[[wikilinks]]`, verification levels not applied to claims. For entity files: entity has no relationships (orphaned in the graph) or has stale relationship targets. |
| 1 | One missing wikilink or minor formatting inconsistency. For entity files: minor frontmatter issues (optional fields that could be populated). |
| 0 | Well-linked, properly formatted, all cross-references intact. Entity files pass parser validation. |

#### Dimension 4: Feedback Signal (0-3)

Based on findings from Phase 1 (feedback processing). If Phase 1 was skipped (no Slack), all files score 0 on this dimension.

| Score | Meaning |
|---|---|
| 3 | Explicit negative feedback about this file's topic (e.g., "the info about project X was wrong") |
| 2 | This file's topic was mentioned in feedback (positive or negative) |
| 1 | Related to a feedback signal (e.g., a person mentioned in feedback has a project file) |
| 0 | No feedback signal related to this file |

#### Scoring Rules

- **Total** = Staleness + Gaps + Structural Integrity + Feedback Signal (max 12).
- **Tiebreaker**: Feedback Signal score wins ties. A file with score 7 (including Feedback 3) ranks above a file with score 7 (Feedback 0).
- **Deprioritize same-day work**: Files that were audited earlier the same calendar day by a consolidation or briefing run get -3 to their total (minimum 0). This creates natural rotation between daytime and nighttime work.

Record the scoring table for the session log.

***

### Step 2a-shrink: Nightly KB pass — rebuild one over-budget project page to the standard

Rebuild **one** over-budget project page a night to [[docs/kb-page-standard]] §"Restructuring an existing over-long page". One page per night, never more: a rebuild re-verifies the project live, and a second one gets done shallowly.

1. **Overrides first.** If `.scout-logs/lint-overrides.log` has entries newer than the last dreaming run, fix each named file first: bring it back under the ratchet by moving the offending content into the right layer.
2. **Pick the page:** the largest project page (`knowledge-base/projects/<name>/<name>.md`) in `scripts/scoutctl kb lint --report --top 20`. Only a project page is a target. **Never rebuild a daily action-items file, the topic board's files, a log, or the legacy `knowledge-base/session-log.md`.** If no project page is over budget, skip to Step 2b. Otherwise this replaces Step 2b's pick, unless an open coverage mandate (e.g. a `surface_rule` window) applies.
3. **Count the baseline:** over-budget rows in `kb lint --report`, `scripts/scoutctl kb validate` errors, and dangling wikilinks.
4. **Rebuild it, in this order:**
   1. **Archive the page verbatim** to `projects/<name>/archive/<name>-YYYY-MM-DD-pre-restructure.md` (a copy).
   2. **Move its knowledge into `knowledge/` topic notes** (cited, ≤ 10 KB each) and its dated decisions into `knowledge/<name>-decisions.md`. `git mv` other loose files into `documents/`, `research/` or `archive/`, repointing path links in the same commit. Drop no facts: superseded ones go in a topic's History or stay in the archive.
   3. **Re-verify the current state live.** The old page is a lead, not a fact.
   4. **Rewrite the page in place** to the standard's template, keeping any heading other notes link to (`[[<name>#<heading>]]`).
   5. **Check nothing got worse:** none of the three counts may rise, and the page leaves the over-budget list. Run `scripts/scoutctl kb lint --lossless-rev HEAD --lossless-path <page>` too.
5. Report: *page, before → after size, notes created, the three counts before → after.* That is this step's deliverable and passes the Step 2d depth gate.

***

### Step 2b: Pick Work Mode

Based on the scoring distribution, select a work mode:

**Deep Dive** (when any file scores 8 or higher):
- Pick the highest-scoring file.
- Perform a full audit: query ALL available connectors for the topic of that file.
- Rewrite sections as needed. Verify every claim against live sources.
- Goal: bring the file from its current state to comprehensive and verified.

**Gap Hunt** (when many files score 4-7, but nothing scores 8+):
- Scan across the KB for systemic gaps: missing projects, people who appear in connectors but not in `people.md`, decisions made but not logged, channels used but not tracked.
- Create new files or sections as needed.
- Goal: fill the biggest holes in KB coverage.

**Structural Pass** (when staleness and gaps are low but structural scores are high):
- Fix broken and missing `[[wikilinks]]` across the KB.
- Verify the KB graph: every file reachable from `knowledge-base.md`, no orphans.
- Apply verification levels (`[single-source]`, `[unverified]`, etc.) to claims that lack them.
- **Knowledge graph integrity:** Run `scripts/scoutctl kb validate` and fix any errors. Create missing entity files for people/projects that have relationships but no entity file. Add missing relationships to existing entity files.
- Goal: make the KB navigable and trustworthy.

**Validation Pass** (when the score distribution is flat — nothing critically stale, no strong feedback signal pointing at a specific gap). **This is the default idle-night mode.** Once the KB is large, the marginal dreaming hour is better spent confirming that what's already in it is still true than adding more — so when in doubt between Gap Hunt and Validation Pass, choose Validation Pass. The deliverable is **existing claims re-grounded against live sources — corrected, downgraded, or confirmed — NOT new sections or entities.**
- Pick a set of the highest-stakes *existing* claims: verification-marker-free assertions in high-priority project files, people roles/titles, issue-tracker states (status/assignee/priority), pricing & cost figures, dated decisions, and any claim a downstream briefing leans on. Prefer claims that are (a) load-bearing for an active decision, (b) older than their file's freshness standard, or (c) single-sourced when first written.
- For each, query the live source and compare. **Correct** the claim where the source disagrees; **downgrade** to `[stale]` / `[contradicted]` / `[single-source]` where you can't fully re-confirm; **confirm** (bump `last_verified:` and add the source to `verified_against:` in the frontmatter) where it still holds.
- Route genuinely-uncertain claims to `review-queue.md` rather than asserting them.
- Do **not** add new entities or expand thin sections in this mode — that's Gap Hunt / research. If you find a real gap, note it for a future run; don't fill it here.
- **Verified-scope guard.** A "confirmed / 0-drift / re-verified" label applies ONLY to the specific claims you queried live *this run*. Any supporting or **derived** claim — especially comparative conclusions like *"X is the sole/only/first/last …"* that depend on the state of **sibling** entities — must either be queried live too, or be explicitly marked `[unverified — not queried this run]`. A derived conclusion inherits the verification status of its **weakest** input, not its strongest. Never re-state a sibling/comparative fact from carried text; re-derive it from the live source. The "N checked / K confirmed" count must equal the number of claims you actually queried.
- Report the count: *N claims checked, M corrected/downgraded, K confirmed.* That count IS the run's deliverable.

**Blended**: You can combine modes within a single run. For example, deep-dive one file and then do a quick structural pass on related files. Use judgment based on the scoring distribution. **Default lean:** on a quiet night with a flat score distribution, blend toward Validation Pass over Gap Hunt — confirm before you grow.

***

### Step 2c: Execute

Perform the selected work. During execution:

- **Query connectors as needed.** You have full access to all connected tools for the purpose of KB improvement. Use them to verify claims, fill gaps, and gather fresh data.
- **Follow all KB management guidelines** from the `kb-management` phase. Apply verification levels, maintain cross-references, use `[[wikilinks]]`, and route uncertain claims to `review-queue.md`.
- **Record the verification in frontmatter** of every file you deeply audit: `last_verified:` and `verified_against:` (the sources checked). Never add a "Last verified" note or blockquote to the body. Leave every page you touch closer to [[docs/kb-page-standard]].
- **Do not duplicate daytime work.** If a consolidation run already updated a file today, focus on areas that run did not cover rather than re-verifying the same data.

***

### Step 2d: Depth Self-Check (HARD GATE)

Before proceeding to commit, answer this question honestly:

> "If {{USER_NAME}} read the KB files I touched tonight, would they learn something they didn't know before?"

**If YES**: proceed to commit.

**If NO**: the work was superficial. Go back to Step 2c and find real work:
- Bump `last_verified:` without actually verifying anything? Not real work.
- Fix a typo and nothing else? Not real work.
- Add a wikilink to a file that already had adequate navigation? Not real work.
- Rephrase existing content without adding information? Not real work.

Real work means: new facts verified from live sources, stale information corrected with current data, missing context filled in, genuinely broken navigation fixed.

**Validation Pass exception:** genuinely re-grounding load-bearing claims against live sources is real work *even when nothing changed* — confirming a high-stakes, aging assertion is still true converts it from a carried claim into a freshly-verified one, which has value. A Validation Pass passes this gate on its honest "N checked / K confirmed" count, not on net-new content. The gate fails only if you neither added or corrected information **nor** genuinely re-verified anything (re-stamping `last_verified:` without actually querying a source is still not real work).

This gate exists because the easiest failure mode of an automated KB system is busywork that looks productive but adds no value. Do not pass this gate unless the work is substantive.

***

### Step 2e: Commit

Explicit paths, never `-A` (see Step 1f):

```bash
{{SCOUT_DIR}}/scripts/git-safe-commit.sh "dreaming [HH:MM]: KB deep work — <summary>" <only the paths THIS run touched>
```

The summary should describe what was improved: e.g., "deep dive on project-alpha (verified 12 claims, updated status, added 3 decisions)" or "gap hunt: added 4 missing people, created channel entries for 2 new channels."

***

### Step 2f: Track Work

Add one row (≤ 500 chars) to this month's shard `knowledge-base/session-log/YYYY-MM.md`, with Edit/Write (never `ls`/`tail`/`grep` the KB to find it):

`| YYYY-MM-DD | HH:MM | Dreaming | <one sentence: the headline result, with [[links]] to the notes changed> | <commit> |`

The detail — which feedback was processed, which KB files were worked on and how, wishlist items completed, and the top-3 scored files with totals — goes in the **commit message**, not the row. The row is a pointer; the commit is the record.

***

### Step 2g: Scout Digest

If today's action items file exists (`action-items/action-items-YYYY-MM-DD.md`), create or update the **`## 📋 Scout Digest — <date> (<time>)` section at the bottom of today's action-items file**, just above `## 🪵 Run notes & connector availability`. There is **one** digest per day, shared across all of today's sessions, so {{USER_NAME}} can catch up on what {{INSTANCE_NAME}} has been doing across runs without reading each one. The companion apps build their Digest view from this `📋` section, so it must stay an H2 in the daily file.

**Cap: ~6 KB.** Scannable bullets only; link to notes for detail. Every line ≤ 500 chars (the daily file's strict line limit). `<time>` is a real `HH:MM` clock time — never a masked run stamp like `8:0x`, which the pre-commit lint reads as a run-diary heading.

**Format:**

```markdown
## 📋 Scout Digest — [Date] ([Time])

**{{INSTANCE_NAME}} ran N sessions today** (breakdown by type). Here's what needs your attention:

### Files to Review
- **[[file]]** — what changed and why {{USER_NAME}} should look at it

### Your Input Needed
| Item | What {{INSTANCE_NAME}} needs | Priority |
|------|-----------------|----------|
| ... | ... | 🔴/🟡/🟢 |

### KB Growth Today
- Ontology stats, new entities, patterns added
```

**Rules:**
- Only include files that changed **substantively** (not just timestamp updates)
- "Your Input Needed" should list ONLY items where {{USER_NAME}}'s action unblocks {{INSTANCE_NAME}} or a project
- Keep it scannable — no walls of text. Link to KB files for details.
- If a digest already exists from an earlier session today, **update it in place** (don't duplicate), trimming older detail so the section stays under ~6 KB

***

### Step 2h: Complement Daytime Runs

Dreaming runs should complement, not duplicate, daytime consolidation and briefing runs. Strategies:

- **Deprioritize same-day files**: Files audited by a consolidation run earlier today get the -3 scoring penalty (see Step 2a).
- **Prefer different dimensions**: If daytime runs focused on staleness (re-verifying current state), nighttime can focus on gaps (adding missing context) or structural integrity (fixing navigation).
- **Go deeper**: Daytime runs often do quick spot-checks. Dreaming runs can do full audits — querying multiple connectors for a single topic, verifying every claim in a file, expanding thin sections.
- **Cover the long tail**: Daytime runs naturally prioritize high-priority, frequently-changing files. Dreaming runs should pick up medium and low priority files that rarely get attention.

This creates a natural rotation where the full KB gets regular coverage without redundant work.
