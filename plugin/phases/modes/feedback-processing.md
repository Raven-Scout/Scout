---
phase: mode
name: feedback-processing
slot: dreaming-phase-1
mode: [dreaming]
requires: slack
---

## Phase 1: Feedback Processing

This is the self-improvement loop. Harvest feedback from {{USER_NAME}}'s reactions and replies to {{INSTANCE_NAME}}'s messages, classify and record signals, update the mistake audit, apply improvements, and act on {{USER_NAME}}'s decisions about earlier changes.

***

### Step 1a: Harvest Feedback from Slack

Read the bot's DM conversation with {{USER_NAME}} using `slack_read_channel` with channel_id `{{USER_SLACK_ID}}`.

**Time window: always the trailing 7 days**, whenever the last dreaming run was. {{USER_NAME}} sometimes answers a DM days later; re-reading a week catches that, and the recording below stops a re-read from counting anything twice.

**For each message authored by {{INSTANCE_NAME}} (the bot) within the time window:**
1. Call `slack_read_thread` on that message's timestamp to retrieve all thread replies and reactions.
2. Collect and record:
   - **Message content**: the original text {{INSTANCE_NAME}} sent
   - **Reactions**: each emoji name and who added it (user ID + display name if available)
   - **Thread replies**: each reply's full text, author (user ID + display name), and timestamp

**{{USER_NAME}}'s top-level messages in the DM count too.** A message {{USER_NAME}} posts in the DM itself, not in a thread, is a reply to the newest bot message sent in the 48 h before it. With no bot message in those 48 h it is not feedback: leave it to the Slack scan as an ask.

Skip messages not authored by {{INSTANCE_NAME}}, and skip other people's replies and reactions: only {{USER_NAME}}'s answers to the bot's own outputs count.

**Record each of {{USER_NAME}}'s replies and reactions**, judging its sentiment by the Step 1b table (positive confirmation → `positive`; negative flag or correction → `negative`; mixed or unclear → `neutral`):

```bash
scripts/scoutctl engagement record dm-reply --surface slack-dm --ref <bot ts>/<reply ts> --ts <reply ts> --sentiment <positive|neutral|negative>
scripts/scoutctl engagement record dm-reaction --surface slack-dm --ref <bot ts>/r:<emoji name> --ts <bot ts> --sentiment <positive|neutral|negative>
```

`<bot ts>` is the Slack ts of the bot message being answered, and the ref must start with it and a `/`, or the reply never counts as an answer to that DM. A reaction has no time of its own in Slack, so it takes the bot message's ts. When a negative reply is about one specific change on the changes map (it quotes a `chg-…` id, the change's id on the map, or answers a wrap line about that change), add `--change <chg-id>`.

The command prints nothing when it records a new signal. **`already recorded` means an earlier run handled that signal: skip it in Steps 1b–1d.** Steps 1b–1d work on new signals only. On the first run under this rule, a new signal older than the last dreaming run (its row in the newest `knowledge-base/session-log/` shard) may already have been handled: check the mistake audit's evidence before acting on it again. If the command fails, handle the signal anyway and note the error in the run notes.

***

### Step 1b: Classify Feedback Signals

Categorize every piece of harvested feedback into one of these signal types:

| Signal Type | Indicators | Action |
|---|---|---|
| **Positive confirmation** | `+1`, thumbsup, checkmark, heart, or praise in thread replies ("great", "perfect", "exactly right") | Log what worked — the content, format, or behavior that earned approval |
| **Negative flag** | `x`, thumbsdown, `-1`, or criticism in thread replies ("wrong", "bad", "don't do this") | Log what failed — the specific output or behavior that was rejected |
| **Correction with context** | A thread reply that explains what was wrong AND provides the correct information or reasoning | Extract a concrete rule or heuristic from the correction. This is the highest-value signal. |
| **Mixed** | Positive reactions on some parts of a message, negative on others; or a reply that says "X was good but Y was wrong" | Separate into individual positive and negative items, handle each independently |

**Rules for classification:**
- A bare reaction with no thread context is a weaker signal than a reply with explanation. Still record it, but weight corrections with context higher.
- If the same message has conflicting signals from different people, note the conflict but weight {{USER_NAME}}'s signal highest.
- If a reaction is ambiguous (e.g., a thinking-face emoji), do not classify it as positive or negative. Skip it.

***

### Step 1c: Cross-Reference with Mistake Audit

Read the mistake audit with `scripts/scoutctl kb read knowledge-base/scout-mistake-audit.md`. For every negative or correction signal from Step 1b:

**If the signal matches an existing pattern in the mistake audit:**
- Increment the occurrence count for that pattern.
- Add this instance as a new evidence entry (date, message content, feedback received).
- If the pattern's status was `Fixed` but this is a recurrence, change status back to `Open` and add a `[regression]` flag with the date. This is the most important update — regressions indicate the fix was incomplete.
- Record the recurrence, so a change that targeted this pattern is judged by it: `scripts/scoutctl engagement record recurrence --surface mistake-audit --ref pattern-<N>@<evidence Slack ts or date> --ts <evidence Slack ts or ISO date>`, where `<N>` is the pattern's number and the evidence is the feedback message's Slack ts (or the evidence's date, `YYYY-MM-DD`, when it has no ts).

**If the signal does NOT match any existing pattern:**
- Add a new entry to the mistake audit with:
  - **Error type**: category (e.g., "stale data", "wrong attribution", "hallucinated detail", "formatting issue", "missed context")
  - **What happened**: specific description of the incorrect output
  - **Root cause**: best assessment of why the error occurred (e.g., "relied on cached KB data without re-querying", "assumed person X was still on team Y")
  - **Fix needed**: concrete corrective action (e.g., "always re-query issue status before reporting", "cross-reference people.md entries with live sources")
  - **Occurrences**: 1
  - **Status**: Open

**For positive signals on previously problematic areas:**
- If a positive confirmation relates to a topic or behavior that has an Open or Fixed entry in the mistake audit, update the entry:
  - If status is `Open` and the positive signal shows the fix is working, change to `Fixed` with evidence (date + the positive feedback).
  - If status is already `Fixed`, add the positive signal as corroborating evidence.

***

### Step 1d: Determine and Apply Improvements

**Order:** do Step 1e (the decisions {{USER_NAME}} already made) before you edit any brain file or script here: a file with uncommitted edits can't be rolled back.

Based on the classified signals and mistake audit updates, determine what changes to make. Use this autonomy table:

| Target File | Autonomy Level | Action |
|---|---|---|
| `knowledge-base/scout-mistake-audit.md` | **Direct edit** | Apply updates from Step 1c immediately |
| KB files (content corrections) | **Direct edit** | Fix factual errors identified by feedback (e.g., wrong status, wrong person, outdated info) |
| `SKILL.md`, `DREAMING.md`, `RESEARCH.md`, `scripts/` | **Direct edit, except governance or safety gating** | Improve behavior based on patterns; commit each change with the change trailers (Step 1f). Governance or safety gating → a `governance-pending/` file (below). |
| New KB files or structural changes | **Direct edit** | Only if supported by clear evidence from multiple feedback signals |

**Self-improvement model.** Brain and script improvements, large or small, go in directly. Each commit becomes a change on the changes map (the app's Changes tab), which tests whether it helped and lets {{USER_NAME}} undo it (Step 1e). There are no proposals.

**The one exception: governance or safety gating**, meaning a change to what {{INSTANCE_NAME}} may do on its own (autonomy rules, safety or privacy gates, who approves what, rollback rules or verdict thresholds). Write it as `governance-pending/<slug>.md` (`<slug>`: lowercase letters, digits and hyphens, at most 61 characters, starting with a letter or digit) and commit that file. It is **never applied by the run that wrote it**: it waits for {{USER_NAME}}'s OK in the Changes tab, and a later run applies it (Step 1e). The file is YAML frontmatter between two `---` lines, holding `summary` (one plain sentence, at most 120 characters), `why` (the value a `Scout-Change-Why:` trailer takes: `pattern-<N>`, `owner-ask:<Slack ts>`, `wishlist:<slug>` or `research:<slug>`) and, optionally, `targets` (a list: the `pattern-<N>` it should reduce, or `engagement`); then a few lines of context for {{USER_NAME}}; then exactly one fenced `diff` block.

Build the diff with git rather than by hand: make the edit, copy `git diff -- <path>` into the block, then put the file back with `git show HEAD:<path> > <path>`. Its headers are the normal `diff --git a/<path> b/<path>`, `--- a/<path>` and `+++ b/<path>` lines with vault-relative paths (no spaces, quotes, `..` or absolute paths), and every hunk's line counts must match. A file that breaks any of this is skipped and never reaches {{USER_NAME}}: after committing it, check that `scripts/scoutctl changes --json` lists `gov-<slug>` under `needs_ok`.

**Harness fallback.** If the runtime blocks a direct brain commit (a safety classifier may stop autonomous self-modification), don't drop the improvement: write the same `governance-pending/<slug>.md` file, so {{USER_NAME}} can approve it and a later run applies it.

**Guardrails:**
- For KB content fixes, always cite the feedback that triggered the change: "Corrected per {{USER_NAME}} feedback on [date]: [brief description]."
- If a correction contradicts information from a live connector, investigate before changing. The correction may be about interpretation, not raw data.

***

### Step 1e: Test changes and act on your decisions

Each commit a run makes to `SKILL.md`, `DREAMING.md`, `RESEARCH.md` or `scripts/` is a change on the map, with an id like `chg-1a2b3c4d5e6f` (`chg-…`, the change's id on the map). The engine compares {{USER_NAME}}'s engagement (replies, reactions, edits, use of {{INSTANCE_NAME}}) and the noise (DMs with no reply or reaction within 48 h, 🔴 items carried 3 days untouched) before and after each change; a reply is a good sign, never noise. A change that made things worse becomes **suspect**. {{USER_NAME}} decides what to undo and which governance changes to allow; this step carries those decisions out. Run:

```bash
scripts/scoutctl changes due --json
```

- **When `verified` is false,** the change history couldn't be checked against git this run, so `rollbacks`, `blocked` and `suspect_unannounced` are empty by design. Say that in the wrap, then do `apply` and `discard`.
- **`rollbacks`** (`id`, `summary`, `reason`): undo each, one at a time. `reason` is `owner-undo` ({{USER_NAME}} pressed Undo) or `auto-targeted` / `auto-one-at-a-time` (an automatic rollback mode {{USER_NAME}} turned on). Run `scripts/scoutctl changes rollback <id>`; it restores the change's files and prints one changed path per line. Commit exactly those paths:
  ```bash
  {{SCOUT_DIR}}/scripts/git-safe-commit.sh $'dreaming [HH:MM]: roll back <id>\n\nScout-Change-Reverts: <id>\nScout-Change-Why: owner-ask:undo-<id>\nScout-Change-Summary: Undid: <its summary, shortened so this summary stays within 120 characters>' <the printed paths>
  ```
  For an `auto-…` reason, use `Scout-Change-Why: verdict:<id>`. Exit 2 means it refused and wrote nothing: put the id and the reason it printed in the wrap. List each rollback in the wrap: what was undone, and why.
- **`apply`** (ids `gov-<slug>`: `gov-…`, a waiting governance change, here one {{USER_NAME}} approved; its file is `governance-pending/<slug>.md`): run `scripts/scoutctl changes apply <gov-id>`. It patches the files, moves the pending file to `governance-pending/applied/<slug>.md`, and prints every changed path. Commit them the same way, subject `dreaming [HH:MM]: apply <gov-id>`, with `Scout-Change-Governance: yes` and `Scout-Change-Why:`, `Scout-Change-Summary:` and (if any) `Scout-Change-Targets:` copied from the moved file's `why`, `summary` and `targets`. Exit 2 with `patch does not apply`: the files changed since; say so in the wrap and leave the pending file.
- **`discard`** (governance changes {{USER_NAME}} rejected): `scripts/scoutctl changes discard <gov-id>` deletes the pending file and prints its path; commit it (`dreaming [HH:MM]: discard <gov-id>`), no trailers.
- **`blocked`** (`id`, `summary`, `why_blocked`): an undo that may not run. Never work around it. List each in the wrap with its reason in plain words: `governance` (it changed what {{INSTANCE_NAME}} may do; only {{USER_NAME}} undoes those), `not-a-run-change` (made by hand or by an upgrade, or itself a rollback; not tested or undone by {{INSTANCE_NAME}}), `root-commit` (the vault's first commit; nothing before it to restore), `edited-since` (a later commit changed the same files, or they have uncommitted edits right now; the second case clears once those are committed), `git-unreadable` (git couldn't read it this run; the next run retries).
- **`suspect_unannounced`** (`id`, `summary`, `reasons`): suspects no wrap has mentioned yet. One plain-words wrap line each: what the change did, and what got worse (`noise-up`: more ignored DMs or stale 🔴s while engagement didn't rise; `recurrence-up`: the mistake it targeted happened more; `named-negative`: {{USER_NAME}} replied negatively about it), ending "Undo in the Changes tab, or run `scoutctl changes undo <id>`". Once the wrap is written, run `scripts/scoutctl changes announced <id> [<id> …]` so the next run doesn't repeat them. Never undo a suspect yourself.
- **`engagement_tests_running` ≥ 1:** a change aimed at more engagement (`Scout-Change-Targets:` includes `engagement`) is still under test. Make no new engagement-targeted change this run.
- **`calibration_report_due` true:** write `docs/changes-calibration-report.md` from `scripts/scoutctl changes --json --since 90d`: verdict counts (each change's `test.verdict`: `helped`, `hurt`, `no clear effect`, `can't tell`, `rolled back`), how many {{USER_NAME}} marked wrong (`test.marked_wrong`), the `can't tell` count (away weeks caught), and proposed thresholds. Commit it. Changing a threshold is a governance change: file each one you propose as a `governance-pending/` item (Step 1d) whose diff adds it to the report under `## Approved thresholds`.

**Never run the owner-only commands** `approve`, `reject`, `undo`, `wrong`, `auto-rollback` or `calibrated` (`scoutctl changes <command>`): they are {{USER_NAME}}'s decisions, and they exit 2 in a run anyway.

**Engine back-port reminder (every run).** Applying a governance patch to `SKILL.md`, `DREAMING.md` or `RESEARCH.md` edits only the vault brain file; the same rule must also land in the engine's `phases/` fragments, or the next `/scout-update` re-render will sidecar it. So scan `governance-pending/applied/` for **applied governance patches whose engine back-port is not yet merged** and, if any are owed, surface a standing reminder in the wrap notification (and carry it as an action item — it must not silently drop):

> ⚠️ Engine back-port owed: N applied governance patch(es) not yet merged into the engine `phases/` — run `scoutctl phases backport` (dry-run first), then `--apply --plugin-root` into a clone under `~/.scout-worktrees/`, review the diff, and open a PR. **Never auto-run it** — it writes the shared/distributable engine, so it stays operator-triggered.

Whoever runs it, the write goes into a clone, never the live engine checkout (see Engine Changes: Never in the Live Engine Checkout). `--apply` refuses the live engine. Clear the reminder when the back-port PR merges.

***

### Step 1f: Commit

If Phase 1 made any changes (mistake audit updates, KB fixes, brain improvements, governance-pending files) — **stage explicit paths through the helper, NEVER bare `git add -A`.** A concurrently-running scheduled session shares this working tree, and `-A` stages *its* half-written files into your commit; the helper refuses `-A` (exit 2), takes a cross-session mutex, and clears provably-stale git locks:

```bash
{{SCOUT_DIR}}/scripts/git-safe-commit.sh "dreaming [HH:MM]: feedback processing — <summary of changes>" <only the paths THIS run touched>
```

Commit each brain or script improvement on its own, with the change trailers after a blank line (git-setup, "Changing the vault's git"): `Scout-Change-Why:` (`pattern-<N>`, `owner-ask:<Slack ts>`, `wishlist:<slug>` or `research:<slug>`), `Scout-Change-Summary:` (one plain sentence, at most 120 characters), and, when they apply, `Scout-Change-Targets:` (comma-separated patterns, or `engagement`) and `Scout-Change-Governance: yes`. A rollback adds `Scout-Change-Reverts: <id>` (Step 1e). One change per commit keeps each one testable and undoable on its own.

Paths are vault-relative; one that is gitignored or doesn't exist is skipped with a warning. Exit 4 means none of the named paths changed — nothing to do. Exit 3 (another session held the mutex too long) or 5 (git failed): report it in the wrap notification, and never fall back to `-A`.

The summary should mention what was processed: e.g., "3 feedback signals, 1 new mistake pattern, 2 KB fixes" or "rolled back 1 change, applied 1 governance change."

If Phase 1 found no actionable feedback and Step 1e had nothing due, skip the commit and proceed to Phase 2. Log "No feedback signals found in time window" in the session entry.
