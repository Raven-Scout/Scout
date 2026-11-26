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

**Order:** do **Step 1e: Test changes and act on your decisions** (the section right after this phase: the decisions {{USER_NAME}} already made) before you edit any brain file or script here: a file with uncommitted edits can't be rolled back.

Based on the classified signals and mistake audit updates, determine what changes to make. Use this autonomy table:

| Target File | Autonomy Level | Action |
|---|---|---|
| `knowledge-base/scout-mistake-audit.md` | **Direct edit** | Apply updates from Step 1c immediately |
| KB files (content corrections) | **Direct edit** | Fix factual errors identified by feedback (e.g., wrong status, wrong person, outdated info) |
| `SKILL.md`, `DREAMING.md`, `RESEARCH.md`, `scripts/` | **Direct edit, proposal, or governance file** | Additive, feedback-aligned or pattern-closing: edit directly; each commit is a change on the changes map, tested and undoable (Step 1e), and carries the change trailers (Step 1f). Large, structural, behavior-removing or uncertain: a proposal (below). Governance or safety gating: a `governance-pending/` file (below). |
| New KB files or structural changes | **Direct edit** | Only if supported by clear evidence from multiple feedback signals |

**Proposals.** A **large, structural, behavior-removing or genuinely uncertain** brain change goes to `dreaming-proposals.md` as an opt-out proposal: `### [Date] — [Short description]`, then `**Trigger:**`, `**Proposed change:**` (exact before/after text and where, specific enough to apply mechanically), `**Rationale:**`, `**Evidence:**`, and `**Status:** Pending (auto-apply after [today + 3 days])`. **Before writing new ones, apply** every proposal whose status is `Approved`, or `Pending (auto-apply after <date>)` with that date passed and not `Rejected`: make the edit exactly as written, set its status to `Applied — [today's date]`, and commit each on its own with the change trailers (`Scout-Change-Why:` from its trigger, or `owner-ask:proposal-<date>` when {{USER_NAME}} marked it `Approved`), naming every proposals path: `<the brain file> dreaming-proposals.md dreaming-proposals/ dreaming-proposals-archive.md`.

**Governance or safety gating** (a change to what {{INSTANCE_NAME}} may do on its own: autonomy rules, safety or privacy gates, who approves what, rollback rules, verdict thresholds) never goes in directly or as a proposal. Write `governance-pending/<slug>.md` (`<slug>`: lowercase letters, digits and hyphens, at most 61 characters, starting with a letter or digit) and commit it. It is **never applied by the run that wrote it**: it waits for {{USER_NAME}}'s OK, then a later run applies it (Step 1e). The file is YAML frontmatter between two `---` lines with `summary` (one plain sentence, at most 120 characters), `why` (a `Scout-Change-Why:` value) and optional `targets` (a list of `pattern-<N>`s, or `engagement`); a few lines of context; then exactly one fenced `diff` block.

Build the diff with git: make the edit, copy `git diff -- <path>` into the block, then restore the file with `git show HEAD:<path> > <path>` (vault-relative paths with no spaces, quotes or `..`; hunk line counts must match). A file that breaks this never reaches {{USER_NAME}}: after committing, check that `scripts/scoutctl changes --json` lists `gov-<slug>` under `needs_ok`, and in the wrap name the file and how to decide: `scoutctl changes approve gov-<slug> --diff-hash <its diff_hash>` or `scoutctl changes reject gov-<slug>`. Never edit a pending file once committed: the decision is bound to the file {{USER_NAME}} saw. To change one, file a new slug.

**Harness fallback.** If the runtime blocks a direct brain commit (a safety classifier may stop autonomous self-modification), don't drop the improvement: file it as an opt-out `Pending` proposal (above), so a later run or {{USER_NAME}} applies it.

**Guardrails:**
- For KB content fixes, always cite the feedback that triggered the change: "Corrected per {{USER_NAME}} feedback on [date]: [brief description]."
- If a correction contradicts information from a live connector, investigate before changing. The correction may be about interpretation, not raw data.

***

**Step 1e (Test changes and act on your decisions) is its own section, right after this phase,** so it runs on every dreaming run, with or without Slack.

***

### Step 1f: Commit

If Phase 1 made any changes (mistake audit updates, KB fixes, brain improvements, proposals, governance-pending files) — **stage explicit paths through the helper, NEVER bare `git add -A`.** A concurrently-running scheduled session shares this working tree, and `-A` stages *its* half-written files into your commit; the helper refuses `-A` (exit 2), takes a cross-session mutex, and clears provably-stale git locks:

```bash
{{SCOUT_DIR}}/scripts/git-safe-commit.sh "dreaming [HH:MM]: feedback processing — <summary of changes>" <only the paths THIS run touched>
```

Commit each brain or script improvement on its own with the change trailers ("Commits That Change {{INSTANCE_NAME}} Itself"): `Scout-Change-Why:`, `Scout-Change-Summary:`, and when they apply `Scout-Change-Targets:` (`engagement` for at most one change per run) and, for a rollback (Step 1e), `Scout-Change-Reverts: <id>`. One change per commit keeps each testable and undoable on its own.

Paths are vault-relative; one that is gitignored or doesn't exist is skipped with a warning. Exit 4 means none of the named paths changed — nothing to do. Exit 3 (another session held the mutex too long) or 5 (git failed): report it in the wrap notification, and never fall back to `-A`.

The summary should mention what was processed: e.g., "3 feedback signals, 1 new mistake pattern, 2 KB fixes."

If Phase 1 found no actionable feedback, skip the commit and proceed to Phase 2. Log "No feedback signals found in time window" in the session entry.
