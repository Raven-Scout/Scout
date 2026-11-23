# The Changes map: what Scout changed about itself, and whether it helped

**Status:** draft for owner approval · **Date:** 2026-11-17 · **Replaces:** the app's Proposals and Wishlist pages

## 1. Why

Scout changes its own behavior every night. The dreaming lane edits its brain files (`SKILL.md`,
`DREAMING.md`, `RESEARCH.md`) directly, ships new checking scripts ("drivers") into the
preflight, restructures the KB, and works through its own wishlist. Two governance changes
moved nearly all of this out of sight:

- **2026-05-30, the proposal gate was retired.** Most brain edits became direct, logged
  self-edits; proposals survived only for large or uncertain changes.
- **2026-10-07, the RRSI gate was applied.** Its leakage critic sends single-incident fixes
  to drivers or the KB instead of the brain, and its cost rule makes every brain addition
  pay for itself while the brains are over their line budget (all three are). Fixes now
  land almost entirely as drivers.

Result: the last proposal was written 2026-10-04 and the Proposals page has been empty since
2026-10-07, while dreaming kept shipping 60–120 commits a week. The Wishlist page shows a work
queue that Scout now files almost entirely for itself. Neither page answers the question the
owner actually has: **what has Scout changed about itself, and did it help?**

The owner's framing (2026-11-17): a clear map of every change Scout made and what effect each
had. "Effect" means two things: **fewer mistakes** and **less noise**. And the most important
signal of all is **how much the owner actually uses Scout**: how often they give it
feedback, and how often Claude sessions they control rely on it.

## 2. Goals, non-goals, anti-goals

**Goals**

1. Every self-change Scout makes is recorded, with what changed, why, and when, from the
   moment it lands. History is backfilled from git so the map is not empty on day one.
2. Owner engagement (feedback + reliance) and noise are measured continuously, as the
   headline numbers.
3. Every self-change is treated as a test: baseline recorded, effect checked after a fixed
   window, verdict written to the map. A change that hurt is flagged as suspect, and the
   owner can undo it in one click. Automatic rollback waits for a calibration period (§7a).
4. Scout may change its own behavior to raise engagement, held in check by the noise
   counter-metric and the rollback rule.
5. The map is visual first: trends, timelines, before/after comparisons. Plus a plain-language
   digest, "What Scout is working on", readable in under a minute.
6. Works for every Scout install, not only the author's (engine + app, not vault-only).

**Non-goals**

- A general analytics product. The map serves one owner judging one Scout.
- Measuring the quality of individual briefings by model grading. Engagement is the proxy.
- Replacing the mistake audit. Patterns stay where they are; the map links to them.

**Anti-goals (failure even if it technically works)**

- **Treating replies as noise.** A DM the owner replies to is Scout earning attention. Noise
  is what the owner never touches, not what they respond to.
- **Gaming the number.** Raising engagement by sending more DMs or more 🔴s that go unread.
  The noise metric exists to make this show up as a loss.
- **A ledger that drifts.** Prose Scout rewrites nightly is how the proposals index lied for
  64 days. The map is derived from append-only records, never hand-maintained.
- **Silent rollbacks.** Every rollback is on the map and in the next DM.
- **Content leaving the machine, or transcript content being stored.** Counts and kinds only.
- **A wall of identifiers.** The digest must read cold: no bare pattern numbers, PR numbers or
  slugs without a plain-words gloss.

## 3. Definitions

| Term | Meaning |
|---|---|
| **Change** | A self-made modification to Scout's behavior: a brain-file edit, a new or changed driver/script, a phase change landed in the vault, a KB structure change, a wishlist item marked done, a rollback. Engine releases installed via `/scout-update` are recorded too, as a separate kind (`upgrade`), because they change behavior even though Scout didn't author them in-vault. |
| **Why** | The trigger: a mistake-audit pattern, an owner ask (message ref), a wishlist item, or a research finding. Required for every change. |
| **Feedback** (engagement) | Owner replies and reactions on Scout's Slack DMs; owner inline comments in vault files (`//==<<`, `scout: user:`); owner status edits and other writes made through the app. |
| **Reliance** (engagement) | An owner-controlled Claude session that used Scout: ran in the vault, read a KB note or an action-items file from elsewhere, ran a Scout slash command (`/scout-work`, `/scout-status`, …) or skill (`scout:*`), or ran `scoutctl`. Counted once per session per surface. Scheduled runs never count. |
| **Noise** | Things Scout put in front of the owner that the owner never touched: an outbound DM with no reply or reaction within 48 h; a 🔴 action item carried forward 3+ days with no owner edit, check-off or comment (counted **once per item**, when it crosses 3 days; its age is shown in the noise detail, not re-counted daily). |
| **Owner day** | A calendar day. Rates are per calendar day, never per "day with engagement": dividing by engaged days would hide exactly the drop this exists to catch. |
| **Recurrence** | A new instance logged against a mistake pattern after a change that targeted it. |
| **Test** | The before/after comparison attached to a change, ending in a verdict. |
| **Verdict** | `helped` · `no clear effect` · `hurt` · `can't tell` (owner away, or too little activity) · `rolled back`. |

## 4. Approaches considered

1. **Change-and-engagement ledger in the engine, rendered by a new app tab.** *(Chosen.)*
   Append-only JSONL ledgers under `.scout-logs/`, filled by hooks, a commit-time detector
   and run-recorded Slack signals; dreaming runs the after-checks and rollbacks; a versioned
   `scoutctl … --json` contract feeds the app. Biggest build, but distributable, and the
   before-numbers are recorded at the time of the change rather than reconstructed.
2. **A vault page dreaming maintains.** Fast and needs no app work, but it is prose Scout
   rewrites nightly (the drift failure that hid approved proposals for 64 days), it is
   single-user, and an engine re-render can overwrite it. Rejected.
3. **Compute the map on demand from git.** Nothing to keep in sync, but Slack feedback is not
   in git and a test's baseline must be captured when the change lands. Rejected as the
   primary design; **its git scan is reused for the one-time history backfill (§6.5).**

## 5. Architecture overview

```
           ┌──────────── capture ────────────┐          ┌── judge ──┐      ┌──── show ────┐
 commits ─▶ change detector  ─▶ changes.jsonl ─┐
 owner sessions ─▶ reliance hook ─┐            ├─▶ scoutctl changes / engagement ─▶ app "Changes" tab
 app writes ─▶ app emits row  ────┼▶ engagement.jsonl                │            ─▶ weekly digest DM
 dreaming Slack harvest ─▶ record ┘            │                     │            ─▶ /scout-status
 outbound DMs / 🔴 carries ─▶ noise rows ──────┘   dreaming: tests ──┘            ─▶ HTML report
```

Everything lives on the owner's machine. The engine owns the data and the contract; the vault
phases own the run-time recording and the after-checks; the app only reads (plus emits its own
feedback rows).

## 6. Capture

### 6.1 Ledgers

**Where rows live depends on who can be trusted to write them.** A scheduled run can append to
the vault's `.scout-logs/`, so anything a run could forge to flatter itself is kept out of
it:

- **Owner-sourced rows** (reliance, app edits, owner inline comments, governance approvals) are
  written to an **owner-only store** beside the broker state (`~/.local/state/scout/`), which the
  write gate and the OS sandbox already deny to runs. Only owner sessions' hooks, the app and
  owner-run `scoutctl` write there.
- **Run-recorded rows** (Slack/Telegram signals, `dm-sent`, `red-carried`, change rows from run
  commits) go to `.scout-logs/`. Each carries a verifiable reference (message ts, commit sha), so
  `scoutctl engagement verify` can re-check a sample against the source.
- Readers merge both; the verdict code never counts a run-recorded row as owner reliance or approval.

Files (gitignored, like `run-outcomes.jsonl`), plus one derived state file:

- `changes.jsonl`, one row per change: `id`, `ts`, `kind` (`brain-edit` · `driver` · `phase` ·
  `kb-structure` · `wishlist-done` · `upgrade` · `rollback`), `summary` (one plain-English
  sentence, written at record time, ≤ 120 chars), `why` (`{type: pattern|owner-ask|wishlist|research, ref, gloss}`),
  `commit` (sha), `files` (vault-relative paths), `targets` (pattern refs whose recurrence
  this change should reduce), `governance` (bool), `author_lane` (dreaming/briefing/…/interactive).
- `engagement.jsonl`, one row per signal: `ts`, `kind` (`dm-reply` · `dm-reaction` ·
  `inline-comment` · `app-edit` · `session-reliance` · `dm-sent` · `red-carried`),
  `surface` (`slack-dm` · `kb` · `action-items` · `skill` · `scoutctl` · `vault-session` · `app`),
  `sentiment` (`positive` · `negative` · `neutral` · `null`, only from the existing feedback
  classifier), `ref` (opaque hash or message ts, never content), `session` (hashed id, reliance only).
- `change-tests.json`, derived state: per change, baseline window metrics, review date, verdict, rollback sha.

Rows carry `schema_version`. Values are validated against fixed shapes, the same discipline
as the run ledger (`scoutctl runs --json`, #661).

### 6.2 Change capture

- **Detector at commit time.** The vault already runs a pre-commit hook (kb-lint). A
  post-commit step classifies the paths a commit touched (brain files, `scripts/`, phase
  sidecars, KB structure moves via `kb-move.sh`, wishlist frontmatter flipping to `done`)
  and appends a change row. Commits that touch none of these produce nothing.
- **The why is mandatory for run commits.** Brain and driver commits made by scheduled runs
  must carry a `Scout-Change-Why:` trailer (`pattern-288`, `owner-ask:<ts>`, `wishlist:<slug>`)
  and a `Scout-Change-Summary:` trailer. The detector falls back to parsing the subject line
  and marks the row `why.inferred: true`. The map shows inferred whys with a hollow marker
  so drift is visible rather than hidden.
- **Interactive commits** (owner sessions) are recorded with `author_lane: interactive`.
  They are shown on the map but not tested or rolled back by Scout.
- **Who made a commit is the runner's record, not the subject's.** Nothing enforces a run's
  subject prefix, so `scoutctl sandbox verify` (outside the sandbox, after claude exits, before
  any integrity restore commit) appends every commit the run made to `run-commits.jsonl` in
  the owner store, which a sandboxed run can't write (`scout/changes/run_commits.py`). A commit
  there is in the run's lane whatever its subject: the lane the run's mode names, else its
  prefix's run lane, else the generic tested `run` lane. The owner's own commits from another
  linked worktree during a run are vouched for by the integrity check's rule and stay
  interactive, under an enforced sandbox only. Cutover: commits from before the release that
  added the ledger, and any run whose start snapshot couldn't be taken, are classified by
  subject prefix as before.
  - *Fails closed.* Every moved or new ref under the integrity check's prefixes is a tip (a
    ref at a non-commit is skipped); past 2,000 commits on a tip, or when git can't list them,
    the row records the window itself and all of it is the run's. A run whose commits couldn't
    be recorded at all makes `sandbox verify` exit 12 when it would have been clean, so run
    health shows the check as not run. The scan also reads back the ledger's run commits from
    the last 14 days that are in HEAD's history, so a run that moves the run-writable scan mark
    or truncates `changes.jsonl` still has them judged.
  - *Known limits.* An owner commit made in the vault's own checkout (not a linked worktree)
    while a run goes is counted as the run's, as the integrity check counts it: it lands in a
    tested lane, so a `targeted` auto-rollback (off today) could undo the owner's own edit. A
    commit made after `sandbox verify` by a process the run left behind is in no window (the
    runner doesn't reap the run's process group; the integrity check has the same gap). Rows
    older than the 14-day read-back that a run deletes from `changes.jsonl` stay gone.
    `run-commits.jsonl` is append-only and nothing compacts it yet.
- **Must work under the run brokers and write sandbox** (Phase 1c/1d, v0.18.14–15): runs
  commit through `git-safe-commit` / the commit broker, so the detector hooks in there, not in
  a path a sandboxed run can't write.
- **Upgrades** are recorded by `/scout-update` with the version range as the summary.

### 6.3 Reliance capture (live)

The Scout plugin is enabled user-wide, so its hooks fire in every Claude session the owner
runs, not only in the vault. **Today's PostToolUse `Read` hook can't do this job**: it only
passes `knowledge-base` / `action-items/context` paths (daily files are excluded), it finds the
vault from `$PWD` (so out-of-vault sessions exit early), and there is no matcher for `Skill` or
`scoutctl`. Slash commands produce no `Skill` call at all.

Instead, reliance is derived **once, at session end**, from the Stop hook that already starts
Python in every session (`session-tool-log` / `session-tokens`), which already walks the
session transcript:

- **Who counts:** owner sessions only, as decided by `run_context` (not `SCOUT_MODE` alone: an
  in-vault session whose first prompt names a run mode is a run). Delegated readers
  (`SCOUT_READER` ≠ `owner`) never count.
- **What counts, once per session per surface:** session cwd inside the vault; any `Read`,
  `Grep` or `Glob` touching vault KB or `action-items/` paths; a user message starting with a
  Scout slash command; a `Skill` call to `scout:*`; a Bash call running `scoutctl`.
- **Finding the vault** from config (`SCOUT_DATA_DIR` / the installed vault path), not `$PWD`.
- **Recorded:** surface + hashed session id + timestamp, to the owner-only store. Never the path
  read, the prompt, or the project the session ran in.
- **Consent:** in-vault sessions are counted by default. Out-of-vault capture is asked once on
  every install, new or upgraded, and nothing outside the vault is counted until it's answered.
  The answer is stored in the owner-only store (`~/.local/state/scout/scout-change-ledger-owner/consent.json`),
  not in `scout-config.yaml`: a scheduled run can write `scout-config.yaml`, so a run could grant itself
  consent there. Only an owner session (`scoutctl engagement consent`) writes the answer.

### 6.4 Feedback and noise capture

- **Slack (run-recorded).** `feedback-processing` Step 1a harvests replies and reactions and
  classifies them, but only on bot messages sent since the last dreaming run, so a late reply
  on an older DM is missed. It is extended to **re-read the bot's DMs from the trailing 7 days**,
  and writes one engagement row per new signal (deduplicated by message ts) via
  `scoutctl engagement record`.
- **`dm-sent` is recorded by the send step, not the outbound gate.** The gate is a PreToolUse
  check on every MCP write tool and sees attempts to any destination. After a confirmed send to
  the owner's DM (the existing outbound-draft → `check-outbound` → send flow), the phase records
  `dm-sent` with the returned ts.
- **Channels.** The schema is channel-agnostic (`slack-dm`, `telegram`). Private wrap content
  goes to Telegram through the broker; v1 records Telegram **sends** from the broker's
  `telegram-sent` log, and counts Telegram **replies** once Scout can read them. Until then
  Telegram sends are excluded from the 48 h noise rule, so they don't count as ignored.
- **Inline comments (scripted).** Owner-authored `//==<<` / `scout: user:` additions are
  detected from git diffs of owner commits and app writes.
- **App edits (app-emitted).** The app already routes vault writes through one write model
  (#664); each owner write appends an `app-edit` row.
- **Carried 🔴s (scripted).** The daily-file materializer already knows what it carries; it
  emits one `red-carried` row per 🔴 item on the day it crosses 3 days with no owner touch.

### 6.5 Backfill (one-time)

- **Changes since 2026-06-01** from git: commit prefixes (`dreaming […]: SKILL.md self-edit`,
  `wishlist —`, driver additions under `scripts/`), wishlist files with `done_date`, the proposals
  archive. Rows marked `backfilled: true`, whys inferred where no trailer exists.
- **Feedback since 2026-06-01** from the vault's DM archive and session logs where they record
  reactions/replies. Best effort, marked backfilled.
- **Reliance history** requires reading Claude Code transcripts across all projects. This is
  **opt-in, asked explicitly**, counts only, and runs once (`scoutctl engagement backfill
  --transcripts`). Without consent the reliance trend starts at install time.
- **Backfilled changes get no verdict.** Their baselines weren't captured. They show as history
  only. This is honest, and keeps the test loop from claiming credit retroactively.

## 7. The test loop (Scout acting on the number)

Run by dreaming, as a new step after feedback processing.

1. **On record:** the change's baseline is the trailing 14 days of engagement (feedback + reliance
   per active day), noise (per active day) and recurrences of each `targets` pattern.
2. **Review at +14 days** (configurable). Verdict rules, defaults to be tuned; all rates are
   per calendar day:
   - `can't tell`: the calendar shows the owner away (out-of-office, travel, leave) for more
     than half of either window. **Never inferred from low engagement**: an owner who stopped
     using Scout must show up as `hurt`, not `can't tell`.
   - `hurt`: noise up ≥ 25 % **and** engagement not up; **or** a targeted pattern recurred
     more often than before; **or** a negative-sentiment reply explicitly names the change.
   - `helped`: targeted recurrences fell to zero, or engagement up ≥ 15 % with noise flat or down.
   - otherwise `no clear effect`.
3. **Overlap.** Several changes inside one window share the window; the map says so ("3 changes
   landed this week; effect is shared"). RRSI's annealed edit budget (one coordinated change per
   run) keeps this manageable; the map doesn't pretend to finer attribution than it has.
4. **Flag, don't roll back (v1).** The owner's call (2026-11-17): Scout is likely to be
   over-eager, and a travel week could send it swinging in several directions at once, so the
   loop starts **observe-only**. A `hurt` verdict, whether from a global drop or from the
   change's own target, puts the change in a **"Suspect"** state. It shows on the map with the
   evidence and in the next wrap DM. Nothing is undone automatically. The owner can roll a
   suspect change back with one click ("Undo"), which records an owner approval; the next run
   performs it as in step 5. Overlapping changes in a bad window are all flagged together, with
   the overlap stated.
5. **Rollback mechanics (owner-triggered in v1).** A config switch
   `changes.auto_rollback: off | targeted | one-at-a-time` exists from day one, **default `off`**.
   `targeted` would auto-undo only changes whose own target went bad. `one-at-a-time` would also
   undo the newest change in a globally bad window, wait a week, and re-check. Neither is turned
   on until the calibration review in §7a says the verdicts can be trusted.

   Scheduled runs can't run `git revert` (since v0.18.15, runs commit
   only named paths through `git-safe-commit.sh`, and raw `git add/commit/checkout/reset` fail).
   Scout restores the change's files to their state before the change commit, commits them through
   the helper (or a new broker `revert` operation; builder's call), records a `rollback` change
   pointing at the original, and lists it in the next wrap DM. Exceptions, which are flagged
   instead: governance/safety changes (owner decides), engine code (owner merges PRs), and
   anything a later change has since edited.
6. **Optimizing for engagement is allowed.** Scout may make changes whose stated goal is more
   owner engagement (different DM shape, timing, fewer/more items). They go through the same
   loop, and the noise half of the `hurt` rule is what stops gaming. To limit swings during
   calibration, **at most one engagement-targeted change may be under test at a time**.

### 7a. Calibration period

The numbers, thresholds and verdict rules in this doc are guesses until they meet real data.
The first **4–6 weeks** after the ledgers go live are a calibration period:

- Verdicts are computed and shown, labelled "calibrating", and nothing acts on them.
- The map adds a **"Would have done"** line per verdict: what `targeted` and `one-at-a-time`
  rollback would have undone. This lets the owner judge the rules before trusting them.
- The owner can mark any verdict **"wrong"** from the change detail. That's itself a feedback
  signal, and the main input for tuning.
- At the end, one dreaming run writes a calibration report: verdict counts, how many the owner
  marked wrong, how many travel weeks were caught as `can't tell`, and proposed threshold
  changes. Tuning the thresholds or turning on any auto-rollback mode is a governance change and
  goes through §8.

## 8. Governance changes without a Proposals page

The one thing proposals still did was hold governance/safety changes for explicit owner
approval. Replacement:

- **Engine changes** are opened as a **draft PR** (already within Scout's standing grant). The
  owner approves by merging. The strip links to the PR.
- **Vault-only governance text** is written as a patch file under a tracked
  `governance-pending/` folder in the vault, and a change row with
  `governance: true, state: awaiting-ok`. It is **not applied**.
- The map shows both in a "Needs your OK" strip with Approve / Reject. **The app never edits a
  brain file.** Approve writes an approval row to the owner-only store (§6.1), which a run cannot
  forge. The next dreaming run applies the patch, moves the file to `governance-pending/applied/`,
  and starts the change's test. Reject records the rejection, and the run deletes the patch.
- Expected volume: a handful a year.

## 9. Surfaces: visual options

All views read `scoutctl changes --json` and `scoutctl engagement --json` (one versioned
document each, contract fixture byte-copied to the app tests, as with the run ledger). **v1**
marks the recommended first release; everything else is a follow-up option.

### 9.1 The app's "Changes" tab (replaces Proposals and Wishlist in the sidebar)

| View | What it shows | Release |
|---|---|---|
| **Digest card** | "What Scout is working on" (§10), pinned at the top | **v1** |
| **Needs your OK strip** | Governance changes awaiting approval (§8); hidden when empty | **v1** |
| **Engagement trend** | Weekly stacked bars, feedback vs reliance, with a noise line overlaid on a second axis. Range switcher 1/3/6/12 months, matching the Control Center heatmap | **v1** |
| **Change markers on the trend** | Each change as a dot on the trend's time axis, colored by verdict (green helped, grey no effect, amber testing, red hurt/rolled back, hollow = inferred why). Click → detail | **v1** |
| **Change list** | Cards, newest first: plain summary, why (glossed), kind icon, verdict pill, a before/after sparkline of engagement | **v1** |
| **Change detail** | Before/after small multiples (engagement, noise, targeted recurrences), the commit diff, links to the pattern and wishlist item, rollback status | **v1** |
| Engagement calendar heatmap | One cell per day, intensity = engagement, red dot = noise; reuses `ActivityHeatmapView`'s pattern | follow-up |
| Verdict scoreboard | Counts of helped / no effect / hurt / rolled back / can't tell, this month vs last | follow-up |
| Where Scout's effort went | Stacked bar by change kind (brain rules, checks, KB structure, wishlist) per week | follow-up |
| Mistake burn-down | Open vs closed mistake patterns over time, with recurrences as spikes | follow-up |
| Reliance by surface | Which surfaces the owner uses: KB reads, action items, `/scout-ask`, DMs, app | follow-up |
| Noise detail | Unreplied DMs per week; stale 🔴s with their age | follow-up |
| Work queue | The former wishlist's open/in-progress items, read-only, collapsed under "Up next" | follow-up |
| Menu-bar trend glyph | A tiny engagement sparkline in the menu-bar extra | follow-up |

Charts: the app has no Swift Charts usage yet (the heatmap is hand-drawn SwiftUI). Using Swift
Charts for the trend, sparklines and small multiples is the builder's call; it is available on
the app's deployment target.

### 9.2 Outside the app

| Surface | What | Release |
|---|---|---|
| **Weekly digest DM** | The §10 digest as a Slack DM on Monday morning, with a rendered PNG of the engagement trend + change markers attached | **v1** (text) · chart image follow-up |
| **`/scout-status`** | Replaces its "pending proposals" section with the digest plus a unicode sparkline (`▁▂▄▆█`) of 8 weeks of engagement | **v1** |
| **HTML report** | `scoutctl changes report --html` → one self-contained page with every v1 chart, for users without the app or for sharing | follow-up |
| Obsidian page | A generated, fenced, read-only `scout-changes.md` in the vault for people who live in Obsidian; regenerated, never hand-edited | follow-up |
| Briefing line | One line in the morning briefing only when a change was rolled back or needs an OK | follow-up |

## 10. "What Scout is working on": the digest

Generated deterministically by `scoutctl changes digest` from the ledgers and the work queue.
No model call, so it can't drift from the data. Each change's one-sentence `summary` is written
when the change is recorded, so the digest only assembles.

Rules: at most ~12 lines; every identifier glossed in plain words; nothing older than 7 days
except running tests; empty sections are omitted.

```
What Scout is working on — week of Nov 16

Your engagement: 41 this week (↑ 12% vs last), noise 6 (↓ 2)
  ▁▂▃▅▄▆▇  feedback 18 · sessions that used Scout 23

Testing now (3)
  • Flags people still marked "on leave" after the leave ended — 4 days left
  • Re-checks pull-request status live before reporting it — 9 days left
Just shipped (2)
  • Morning list stops repeating yesterday's dates in item titles
What it learned
  • ✅ Helped: stale-clock check — that mistake hasn't recurred in 3 weeks
  • ⚠ Suspect: shorter DMs — you replied half as often (Undo in the Changes tab)
Up next
  • Gmail "nothing found" checks can miss threads; teach the check to say so
Needs your OK (1) — open the Changes tab
```

## 11. What gets removed

- **App:** the Proposals sidebar item and `apps/macos/Scout/Proposals/` with its tests; the
  Wishlist entry in `PerFileTabConfig` (the Research tab stays). The sidebar gains "Changes".
- **Phases:** `feedback-processing` Step 1e (handle/write proposals) is replaced by the test-loop
  step and the governance path in §8. The audit-pattern → proposal bridge becomes "every change
  carries its pattern in `Scout-Change-Why:`". The `wishlist` mode keeps working its queue but
  stops treating the page as the owner surface.
- **`/scout-status`:** the "Pending Proposals" and "Wishlist status" sections become the digest.
- **Vault data:** `dreaming-proposals/` and `dreaming-proposals-archive.md` are left in place,
  frozen, and imported by the backfill. `docs/wishlist/` files stay as Scout's internal queue.
- Vault brain mirrors follow via `/scout-update` re-render, never hand-edited.

## 12. Build order

Each step is its own PR on `Raven-Scout/Scout`; nothing here runs `release-app.sh` or
`release-plugin.sh`.

1. **Engine ledger + contract.** Schemas, `scoutctl changes|engagement record|--json|digest`,
   contract fixture + test, the commit-time detector, the reliance hook branch.
2. **Phases.** Slack signal recording in feedback processing, `dm-sent` from the outbound gate,
   `red-carried` from the materializer, the test-loop step, governance path, Step 1e removal,
   commit trailers in the commit instructions.
3. **App v1.** Changes tab (digest card, OK strip, trend + markers, list, detail); remove
   Proposals and Wishlist tabs.
4. **Digest everywhere.** Weekly DM, `/scout-status` section.
5. **Backfill.** Git + archive import; opt-in transcript backfill.
6. **Follow-up visuals** from §9, in whatever order the owner asks for.

Test plan per PR: ledger schema round-trip and validation tests; detector fixtures for each
change kind and for broker/sandbox commits; reliance hook tests proving scheduled runs and
non-owner readers are never counted and that no path or content is written; verdict-rule unit
tests including `can't tell` and overlap; app contract test against the fixture; view smoke
tests.

## 13. Decided

**By the owner (2026-11-17):**
- Remove the Proposals page; deprecate the Wishlist page.
- The map shows every change and its effect; effect = fewer mistakes and less noise.
- Owner engagement (feedback + reliance by owner-controlled Claude sessions) is the most
  important signal. DM replies are a good sign, not noise.
- Scout may change its own behavior based on that signal.
- Approach 1 (engine ledger + app tab), with as many visual options as possible and a plain
  "What Scout is working on" digest.
- **Flag only, no automatic rollback, for now.** The worry: Scout will be over-eager and swing
  in different directions because of travel or noise, and this needs experimentation first.

**Calls made in this doc (owner can override):**
- Noise = untouched DMs (48 h) and 🔴s carried 3+ days untouched.
- 14-day baseline/review windows and the verdict thresholds in §7.
- Rollback is owner-triggered (one-click Undo on a suspect change); `auto_rollback` modes exist
  but default `off` until the calibration review (§7a). Governance and engine code are never
  rolled back by Scout.
- A 4–6 week calibration period with "would have done" lines and a "verdict is wrong" button
  (§7a); at most one engagement-targeted change under test at a time.
- Governance changes wait in a "Needs your OK" strip (§8) instead of a Proposals page.
- Wishlist files stay as an internal queue; only the page goes.
- Backfilled changes get history but no verdict; transcript backfill is opt-in.
- Out-of-vault reliance capture is asked once on every install, new or upgraded; nothing
  outside the vault is counted until it's answered.
- Owner-sourced rows and approvals live in the owner-only store so runs can't forge them; run-
  recorded rows carry verifiable refs (§6.1).
- Reliance is derived at session end from the existing Stop hook, not from per-call hooks (§6.3).
- Rates are per calendar day; `can't tell` comes only from calendar absence (§3, §7).
- A stale 🔴 counts as noise once; late Slack replies are caught by a 7-day re-read; Telegram
  replies count once readable (§6.4).
- Governance approvals are recorded by the app and applied by the next run; the app never edits
  brain files (§8).
- v1 visual set as marked in §9.

## 14. Left to the builder

Exact JSON field names beyond those listed; where in the broker the detector hooks; Swift
Charts vs hand-drawn views; digest DM scheduling mechanics; the PNG chart renderer; config key
names; how the app's write model emits `app-edit` rows.
