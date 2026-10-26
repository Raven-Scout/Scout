---
phase: core
name: action-items
slot: action-items
mode: [briefing, consolidation]
requires: null
---

## Step 0: Archive Old Action Items

Move any `action-items/action-items-*.md` files older than 7 days into `action-items/archive/`. Create the archive folder if it doesn't exist. Use the date in the filename to determine age.

```bash
mkdir -p {{SCOUT_DIR}}/action-items/archive
# Move files older than 7 days based on filename date
```

## Step 0.5: Critical Blocks Check

Before building the list, read the mistake audit with `scripts/scoutctl kb read knowledge-base/scout-mistake-audit.md`. For every pattern with status **Open** that carries a {{USER_NAME}}-facing blocker — a setup task {{USER_NAME}} must do, an unanswered question {{INSTANCE_NAME}} asked him, or an approved-but-unapplied proposal:

1. Check whether it's been resolved since last run (search messaging, verify files/state).
2. If still unresolved and >24h old, surface it today. When {{USER_NAME}} has to act, make it a **🔴** item: "**Still blocked:** [pattern] — [what {{USER_NAME}} needs to do]". An approved-but-unapplied proposal is Scout's own move: leave it to the next dreaming run's feedback processing (`phases/modes/feedback-processing.md`), as a 🟢 Watching item until then.
3. If stuck 48h+, call it out explicitly in the run summary/notification so it isn't lost.

This makes the mistake audit an active tracker, not a passive log — {{USER_NAME}} expects every run type (not just dreaming) to keep pushing critical blocks toward resolution. Track specifically: unresolved setup/integration tasks, open questions awaiting {{USER_NAME}}'s answer, and approved proposals not yet applied.

## Action Item Categories

Categorize every action item using these levels:

- **🔴 Urgent**: {{USER_NAME}}'s move, today
- **🟡 To Do**: {{USER_NAME}}'s move, soon
- **🟢 Watching**: Waiting on someone or something else
- **✅ Done**: Completed with evidence of completion

### Hard Rule — 🔴 and 🟡 Are {{USER_NAME}}'s Move

A task goes in 🔴 or 🟡 only when its next step is {{USER_NAME}}'s to take now. For example:
- reply to someone, decide something, write or file something;
- something to settle with others, such as "agree the rollout with Alex in the 12:00 meeting";
- an open question to {{USER_NAME}}, checked this run to be still open;
- a review requested from {{USER_NAME}} by name;
- {{USER_NAME}}'s own PR when it needs them: it is ready to merge, or it is `BEHIND`, `DIRTY`, `UNSTABLE` or has `CHANGES_REQUESTED`. Decide this by **PR status claims** in the GitHub cross-check (`phases/connectors/github.md`), not from this list.

When the next step is someone else's, the task goes to 🟢 Watching:
- **Waiting on a person**, such as {{USER_NAME}}'s PR while a requested human reviewer hasn't reviewed it (a bot approval doesn't count), or a question {{USER_NAME}} already asked. Write `waiting on <who> since <date>`. It returns to 🟡, for {{USER_NAME}} to nudge, after 2 working days without a reply (unless its context note says otherwise) or once its deadline is under 3 days out.
- **Someone else is doing the work:** an agent or a teammate (the board shows `State: doing` as Doing). This includes {{USER_NAME}}'s own delegated agent PRs until their merge state is `CLEAN`.
- **A review requested only through a team:** it stays in 🟢 until nobody on the team has picked it up for 2 working days, then it moves to 🟡.

A task waiting on another task stays in its section with `Depends:`. The deadline-distance ceiling below still caps how high a task can go.

**Re-check each run** the 🔴 and 🟡 tasks this run has new evidence for, plus any due within 7 days or overdue, and any 🟢 task it has new evidence for (a review, a reply, a merge). Other tasks move under this rule when a later run touches them.

### Hard Rule — Deadline-Distance Ceiling

Urgency is **capped by how far away the deadline is** — distance sets a *ceiling*, not a floor. Before assigning any tier, compute the days between today and the item's real deadline:

- **30+ days out** → 🟢 Watching (max). Far-future items never lead the list.
- **7–30 days out** → 🟡 To Do (max).
- **< 7 days out** → eligible for 🔴 Urgent.
- **< 3 days out** → 🔴 when the next action is {{USER_NAME}}'s to take.
- **Past due** → *Past-Due Tasks Step Down* below decides.

**Evaluate the actual deadline, not the source's tone.** Notification language — "action required", "expiring soon", "final notice", "limit reached" — is alarm-word framing, not real urgency, and never promotes an item past the distance ceiling. Deprecation / end-of-life notices specifically stay 🟢 Watching until ~2–3 weeks before the cutover. This ceiling is the counterpart to the deadline *escalation* rule under Knowledge Graph Personal Tasks below: escalation raises priority as a deadline nears; the ceiling stops a distant deadline from being surfaced as urgent on tone alone.

### Hard Rule — Past-Due Tasks Step Down

A deadline that passed with nobody acting on it is no longer today's work. Without this rule a past-due task stays 🔴 for good, and the few that really are urgent get lost among them.

For each 🔴 or 🟡 task whose due date has passed, count its **quiet days**: the calendar days since the later of its due date and the last new evidence on it (a reply, a PR or ticket update, a meeting mention, a comment from {{USER_NAME}}), as its context note dates that evidence. Then:

- **Under 7 quiet days:** treat it as under 3 days out: 🔴 when the next step is {{USER_NAME}}'s, 🟡 for a nudge when it waits on someone else. A deadline just missed is still urgent.
- **7 or more:** 🟡 at most. The first time, whether you move it down from 🔴 or it was already 🟡, rewrite its body once to the due date and one question, e.g. `— due Sep 22; still want this?`, and add a dated line to its context note: `stepped down, past due with no news`.
- **14 days after that line, still with no news:** move it to 🅿️ Parked as it is, without editing its text. The board shows it as on hold.
- **New evidence restarts the count.** Record it, dated, in the context note, and place the task under the usual rules again, even if it is parked. {{USER_NAME}} answering "still want this?" is evidence: yes means re-place it (with a new due date, if they give one); no means close it as a directive.
- A task snoozed past today is skipped: the snooze is {{USER_NAME}}'s decision.
- A recurring task the cadence computer reports `overdue` is 🔴 on its due day and the 2 days after. On the 3rd day past due with no completion evidence it steps down to 🟡 with the question and the note line (without waiting for 7 quiet days), and parks 14 days after that line, as above.

This is demotion within view: the task keeps its row and its `[#TAG]`, so the count-guard and the dropoff audit see no change. A parked task nobody touches leaves through the 60-day *Stale Items Auto-Archive*, never by being dropped.

## Action Items File Format

Create `action-items/action-items-YYYY-MM-DD.md` using today's date. Include:

```markdown
# Action Items — YYYY-MM-DD

## 🔴 Urgent

- [ ] [#XXXX] **[Short natural imperative title — no ids/status/emoji/dates]** — [due/waiting-on, specific not vague] · → [[<project, topic or source note>]]
  - [Optional: the one blocker, or {{USER_NAME}}'s comment thread]

## 🟡 To Do

- [ ] [#XXXX] **[Item title]** — [one-line status] · → [[<note>]]

## 🟢 Watching

- [ ] [#XXXX] **[Item title]** — [what you're tracking] · → [[<note>]]

## ✅ Done

- [x] [#XXXX] **[Item title]** — [What was completed and how]
  - Evidence: [Link to message, PR, calendar change, or other proof]
  - Completed: [date/time]

## Carryover

Items carried forward from previous days that are still open.

- [ ] [#XXXX] **[Item title]** — [one-line status since last check] · → [[<note>]]

## 🪵 Run notes & connector availability

_Always the LAST section of the file — run metadata is a footer for review, never a hero block at the top. First-paint should be 🔴 Urgent, not run narrative. Append newest entry first; keep only the last 3 runs (older entries roll off)._

- **YYYY-MM-DD HH:MM [timezone]** ([mode]) — X new / Y completed / Z carried forward. Connectors: [available, or "degraded: <name>"].
```

An open item's sources, evidence, quotes and history go in its note, `action-items/context/<TAG>.md` (see **Item line format**), never in prose sub-bullets. Machine lines (plan markers, `- Refs:`) still go under the item.

All action items files must include `[[wikilinks]]` to any KB files referenced by action items.

### Hard Rule — Daily-File Completeness Invariant

`action-items/action-items-YYYY-MM-DD.md` must contain the **full carried-forward item list from the moment it exists**. Companion surfaces (scout-app, the TUI) render the daily file as the complete truth — a stub makes every open item invisible until the next full rewrite.

- If today's file does not exist when your session starts, run the deterministic backstop FIRST, then edit on top of the complete file:
  ```bash
  scripts/scoutctl action-items materialize
  ```
  It copies the most recent prior daily file (up to 7 days back) verbatim under a fresh date header and a provisional banner — minus any items it auto-archived as stale (see *Stale Items Auto-Archive* below), which the banner names. Idempotent — a no-op when today's file exists. The runner preambles already call it before every session; this in-session call covers sessions launched outside the runners.
- **NEVER write a section that points at a previous day's file in lieu of the items.** "Carry forward in full from yesterday — see that file" is FORBIDDEN, no matter how lightweight your session is. This binds every session type — briefing, consolidation, research, dreaming, and any auxiliary session that happens to be the day's first writer.
- When you find the mechanical carry-forward banner at the top of today's file, you are the enriching pass: rewrite the header/focus sections for today, reconcile items normally, and remove the banner. Do not treat the banner as a reason to start a fresh file. If the banner reports auto-archived items, carry that count into this run's `🪵 Run notes` entry before removing it.
- This rule complements the continuity rules below: the count-guard and dropoff audit protect the ledger *across* days; this invariant protects the rendered surface *within* the day.

### Hard Rule — Every Task Line Has a Stable `[#TAG]`

**Every new task line you write MUST start with a stable `[#TAG]` identifier** — 2–8 uppercase letters/digits with at least one letter (e.g. `[#PLANREV]`, `[#XI7391]`, `[#NTX]`). The tag is the structural identifier scout-app uses to mark tasks done, snooze them, and attach comments — without it, the app falls back to brittle markdown-substring matching that fails on emoji, italics, em-dashes, embedded links, or any non-ASCII drift. Issue #10 of scout-app catalogs the failure modes.

**Prefer a short, meaningful mnemonic** that hints at the task and is easy to cross-reference from other lines (e.g. `[#PLANREV]`, `[#IOTA]`, `[#XI7391]`). When nothing meaningful fits, mint a random one:

```bash
PFX=$(scripts/scoutctl action-items new-prefix)   # random 4-char fallback id
echo "- [ ] [#${PFX}] **${SUBJECT}** ${BODY}" >> "$DAILY_FILE"
```

**Canonical task line shape:**
```
- [ ] [#TAG] **<bold subject>** <optional body, links, italic context>
```
The tag goes **after** the checkbox marker and **before** the bold subject. Exactly that order — the parser keys off the leading position.

**Tag rules:**
- 2–8 chars, `[A-Z0-9]`, at least one letter. (Pure-numeric like `[#555]` is reserved for GitHub issue refs and is NOT a valid tag.)
- **Unique within the file** — never give two open tasks the same tag (scout-app's `--by-id` will refuse an ambiguous tag).
- **Carry-forward keeps the original tag verbatim.** When propagating an item from yesterday into today, copy its `[#TAG]` exactly — do NOT mint a new one. The tag is the task's identity across days.
- **Carry-forward rewrites the item into the canonical shape.** When carrying an open item into today's file: keep the `[#TAG]` verbatim and preserve every fact, but re-author the line — clean title per the *Clean Title, Prose Body, Refs Block* hard rule below, narrative into the body, all machine refs consolidated into the `- Refs:` sub-bullet, no inline priority emoji. Do NOT preserve legacy formatting for its own sake. This is how the corpus converges; there is no other migration.

**Existing unprefixed lines (legacy carryover):** when you find a task that lacks a `[#TAG]`, give it one on first touch, or run the idempotent one-shot backfill (it leaves already-tagged lines alone):
```bash
scripts/scoutctl action-items backfill-prefixes "$DAILY_FILE"
```

**Self-check before commit:** every `- [ ]`/`- [x]` line MUST carry a `[#TAG]`. A heuristic grep catches drift:
```bash
grep -nE '^\s*- \[[ x]\] ' "$DAILY_FILE" | grep -vE ' \[#[A-Z0-9]{2,8}\] ' && \
    echo "ERROR: lines missing [#TAG] prefix above — fix before commit" >&2
```
If that grep finds anything, the file is non-compliant and scout-app's writes will fall back to fragile subject-matching for those lines.

### Hard Rule — Clean Title, Prose Body, Refs Block

The **bold segment is the human-readable title** and must read as a short natural imperative phrase — what to do, in plain words. Keep it scannable (aim for a single line).

The bold title MUST NOT contain any of:
- the `[#TAG]` (it sits *before* the bold, never inside it);
- Linear ids (`PROJ-1234`), GitHub refs (`#1234`, `owner/repo#1234`), or cross-reference hashtags (`#SHORTCODE`);
- status words ("MERGED", "DEPLOYED", "created + self-assigned", "done→todo");
- dates, times, or quoted snippets;
- emoji of any kind (including priority 🔴🟡🟢);
- an internal ` — ` / ` – ` separator (that dash separates title from body).

The **body** (after the ` — ` separator) is short human prose: the next step, with its status and dates, in at most two short sentences (about 25 words). A task stepped down under *Past-Due Tasks Step Down* carries its due date and "still want this?" instead. Write a date as a date (`due Oct 3`), never as an age that changes every day (`4 days overdue`, `now 5 days past`). Say what the work does and what breaks or waits without it. Never give a bare PR number as the reason, and never refer to internal context ("you asked session X…"). The `Action:` board field is only the 1–3-word form of that next step. Quotes, sources and longer context go in the item's note, `action-items/context/<TAG>.md`. Entity wikilinks (`[[people/alex|Alex]]`) may appear inline in the body ONLY where a name reads naturally in a sentence. Bare machine ids never appear in the body prose.

**All machine refs go in ONE `- Refs:` sub-bullet** directly under the task line, ` · `-separated: Linear ids, GitHub refs, Slack permalinks, cross-reference hashtags, and entity wikilinks that are pure references (not part of a sentence). Omit the sub-bullet when a task has no refs.

**Priority is expressed only by which section the item lives in** (🔴 Urgent / 🟡 To Do / 🟢 Watching). Do NOT prepend a priority emoji to a task line.

Good vs bad (anonymized):

    ✅  - [ ] [#REPLYX] **Reply to Alex about her purchase question** — She said 1/8–1/2; still open per the sweep. Loop in [[people/priya|Priya]] on onboarding.
    ✅    - Refs: [[people/alex]] · [[PROJ-3026]] · example-org/repo#7056 · #XREF
    ❌  - [ ] [#REPLYX] 🟡 **Reply to Alex — purchase Q still open (Thu 4:55 PM: "1/8 to 1/2") PROJ-3026** _(carries)_ — …

### Hard Rule: Plan Markers Carry Verbatim

`/scout-plan` writes up to three machine sub-bullets under a task, always in this order and only through `scoutctl action-items set-estimate | set-block | set-actual | clear-block | clear-plan`:

    - [ ] [#REPLYX] **Reply to Alex about her purchase question**
      - estimate: 45m (raw: 30m, kind: comms)
      - block: 2026-09-30 10:00-10:45 (event: abc123)
      - actual: 1h (2026-09-30)

- When you carry a task forward, move it to another section (including Recently Completed when it is done), or rewrite the file in full, copy these lines **verbatim**, directly under the task line, in the same order. Never re-author, merge, round, translate or drop them, and never fold them into the `- Refs:` sub-bullet. They are the one exception to the single-Refs-line rule because the apps and the planner parse them.
- Never write, edit or remove them yourself. If one looks wrong, say so in the digest and leave it.
- A past `block:` without an `actual:` is data the estimate feedback loop still needs. Keep it until the task is done and the user has logged the time with `/scout-plan review`.

### Board Fields (optional)

The app's topic board reads a few machine sub-bullets that a section can't express. Like plan markers and `- Refs:`, they are an exception to the one-sub-bullet rule in **Item line format**, but only because they are short and rare: write one only when it adds something the section, the title and the body don't already say. Most tasks carry none, or only `- Topic:`. They go directly under the task line, after any plan markers, in this order:

    - [ ] [#REPLYX] **Reply to Alex about her purchase question**
      - Topic: #STRM
      - Parent: #RTALL
      - State: doing
      - Action: Reply
      - Depends: #XREF, #QBLK
      - Session: https://claude.ai/code/session_0123
      - Priority: 2
      - Cycle: current

- `Topic:` is the `[#TAG]` of the workstream the task belongs to, described in `action-items/topics.md` (below). Tasks without one stand alone on the board.
- `Parent:` is the `[#TAG]` of a task that groups several separate actions; the board nests the children under it. Never pack several actions into one task: give each its own task with `Parent:`. A parent exists only while it has two or more open children. With one left, promote that child (drop its `Parent:`) and close the parent; with none left, close it too. Closing means ticking it and moving it to ✅ Recently Completed with its children as the evidence, so the Continuity-Dropoff Audit sees a closure.
- `State:` is written only as `doing` or `blocked`; everything else comes from where the task sits. Write `doing` only with proof of activity on that task itself: a commit or PR update in about the last hour, or its own session visibly working on it. An agent drafting while it works is `doing`; a related session merely running is not. Write `blocked` only for a blocker that is not a task (an outage, a decision outside this board). A task waiting on another task gets `Depends:` instead, and the board derives blocked from it.
- `Action:` names the next step on a 🔴 Urgent or 🟡 To Do task, 1–3 words ("Merge", "Review + decide", "Answer agent", "Reply to session", "Decide"), when the title doesn't already say it. Never on a parent.
- `Depends:` lists the tasks this one needs first, each written `#TAG`. The rule is functional, and it is the one rule for PR dependencies (the GitHub cross-check applies it too): a PR whose feature only works once another PR lands, in any repo, depends on it even when it would merge cleanly and does nothing alone (flag, config and chart PRs count). Find that from this PR's body and the other PRs fetched this run.
- `Session:` is the Claude session working on it: a `claude.ai/code/session_…` link, or a local session's UUID.
- `Priority:` is the linked Linear issue's priority, 1 urgent … 4 low; omit it for 0 (no priority). `Cycle: current` marks a Linear issue in the active cycle, on tasks someone is working. Omit both without a Linear issue, and rewrite either only when the Linear value actually changed.
- Carry them forward verbatim with the task, like plan markers. Never fold them into `- Refs:`.

**How the board places a task**, first rule that applies:
1. A ticked `[x]` task is done.
2. An active snooze (`snoozed-until` in the future) puts it on hold until that date.
3. A `Depends:` on a task that is still open makes it blocked. A dependency that is done or gone no longer blocks.
4. `State: doing` or `State: blocked` applies.
5. Otherwise the section decides: 🔴 Urgent and 🟡 To Do are {{USER_NAME}}'s move, 🟢 Watching is waiting on someone else, 🅿️ Parked is on hold.

**Topics** live in `action-items/topics.md`, one `##` heading per workstream, so they outlast the daily files:

    # Topics

    ## [#STRM] Make stream reattach survive a restart
    - Session: https://claude.ai/code/session_0123
    - Summary: Two PRs, the second needs the first

- Mint the tag with `scripts/scoutctl action-items new-prefix`, like a task's; it never reuses a topic's tag. When a known root Claude session produced the workstream, it is the topic: put its link in `- Session:` and give each of its tasks `- Topic:` with that tag.
- Keep the title a short noun phrase and the summary to one line. Update them freely, but copy any other line under the heading byte for byte. The user's own names for topics live in `action-items/topic-names.md`, written only by `scoutctl action-items rename-topic` from the app; never write that file.
- Remove a topic once none of its tasks has been open for 14 days.

**Inbox and Loose ends** are two optional sections after 🟢 Watching, one bullet each, `Source · date — [what](url)` (the link is optional):

    ## 📥 Inbox

    - Slack · Oct 10 — [Priya asked about the retention window](https://acme-co.slack.com/archives/C0123456789/p1700000000000000)

    ## 🧹 Loose ends

    - [Delete the merged reattach branch](https://github.com/example-org/web/tree/reattach)

- Inbox holds new mentions, DMs and notifications not yet turned into tasks. Loose ends holds cleanup: stale branches, drafts, finished threads to close. This `## 📥 Inbox` section is not `inbox.md`, the user's own capture file that inbox processing empties.
- Give every entry the link to the thing itself whenever one exists. The link is how the entry is recognised: an entry without one is keyed by its exact text, so rewording it brings a dismissed entry back.
- `action-items/dismissed.md` lists the entries the user closed, by link or else by text. Never re-add a listed entry, and never write that file yourself. It closes only that Inbox or Loose ends entry: it says nothing about tasks, so keep tracking any task for the same link as usual. `scoutctl action-items materialize` also drops listed entries when it carries a day forward.

### Hard Rule — Trim by Demotion, Never by Omission

When the list grows long, achieve focus by **reprioritizing**, never by hiding items from view. "Don't overwhelm me" and "don't drop my items" are both real constraints — resolve the tension by demotion *within* view, not omission *from* view.

1. Keep a tight, time-bound **🔴 Urgent** set at the top.
2. Demote lower-priority open items from 🔴 to 🟡 and reorder within 🟡 — {{USER_NAME}} can scan a long, ordered list and ignore the bottom; he cannot recover items that aren't rendered at all. Never demote {{USER_NAME}}'s own work into 🟢 Watching: that section means the next step is someone else's. The one way down to 🅿️ Parked is *Past-Due Tasks Step Down*.
3. Mark an item `[unverified]` or drop it **only** when {{USER_NAME}} has explicitly said so (reply, reaction, or an inline `//==<<` directive).

The one exception is the deterministic stale-item archive below — it is not an omission, and you must not undo it.

### Hard Rule — Stale Items Auto-Archive

An open item whose block (the task line plus its indented sub-bullets) has not changed for **60 days** is moved, verbatim, out of the daily file into `action-items/archive/stale-items-YYYY-MM.md`, under a dated heading and with an `_auto-archived YYYY-MM-DD · unchanged since YYYY-MM-DD_` note. `scoutctl action-items materialize` does this when it creates the day's file; nothing is deleted. Moving an item between sections does not count as touching it — only a change to its text does (a comment, a snooze, a status note, a rewrite), and not a change to its dates, times or ages alone. Items snoozed past today are never archived.

- **Do not re-add an auto-archived item** to keep the list "complete". It left on purpose, and the count-guard and dropoff audit below account for it.
- **Restore it when it comes back to life.** If this run finds new activity on an archived item (a reply, a PR update, a meeting mention), or {{USER_NAME}} names it, copy its block back from the stale-items file into the right section of today's file, update it with what's new, and remove the `_auto-archived …_` note. The edit restarts its 60-day clock.
- **Don't refresh text to keep an item alive.** Rewording an item you have no news about defeats the archive. Edit an item only when something about it actually changed.
- **Age is not a touch.** Never rewrite an item only to update how old or overdue it is, and never add `_re-anchored <date>_` notes: its age follows from its due date. The archive ignores edits that change only dates, times or ages anyway, so they cost a rewrite and keep nothing alive.

To apply the rule to a file that already exists (e.g. after a manual cleanup), run `scripts/scoutctl action-items archive-stale` (`--dry-run` lists what would move).

**Every open carried item MUST be rendered as its own `- [ ]` checkbox row in exactly one section.** Summary lines like "…plus the standing backlog (#A, #B, … etc.) — carried unchanged" are **FORBIDDEN** as a substitute for rendering individual items. An `etc.` that hides open items reads as a drop from {{USER_NAME}}'s perspective even when the IDs technically persist inside the prose string.

**Compose-time count-guard (run before commit):** count the rendered open rows (`- [ ]` lines plus 🟢 Watching bullets) in today's file. That count MUST be ≥ the prior day's open-item count minus any items closed this run (`- [x]`), auto-archived as stale today, or dropped on an explicit {{USER_NAME}} directive. If today's count is lower, items were collapsed/omitted — expand them back into individual rows before committing.

```bash
# Compose-time count-guard
PREV=$(ls -t {{SCOUT_DIR}}/action-items/action-items-*.md | sed -n 2p)
TODAY=$(basename "$DAILY_FILE" .md | sed 's/^action-items-//')
prev_open=$(grep -cE '^\s*- \[ \] ' "$PREV" 2>/dev/null || echo 0)
today_open=$(grep -cE '^\s*- \[ \] ' "$DAILY_FILE")
closed_today=$(grep -cE '^\s*- \[x\] ' "$DAILY_FILE")
# Open rows (nested ones included) under today's heading in the stale-items archive.
archived_today=$(cat {{SCOUT_DIR}}/action-items/archive/stale-items-*.md 2>/dev/null | \
    awk -v d="## $TODAY " 'index($0, d) == 1 { on = 1; next } /^## / { on = 0 } on && /^[ \t]*- \[ \] / { n++ } END { print n + 0 }')
[ "$today_open" -lt $((prev_open - closed_today - archived_today)) ] && \
    echo "ERROR: open-row count dropped ($prev_open→$today_open, only $closed_today closed + $archived_today auto-archived) — items were collapsed; expand them before commit" >&2
```

### Hard Rule — Continuity-Dropoff Audit (ID-level, pre-commit)

The count-guard catches *how many* dropped; this catches *which*. Before committing, diff today's open `[#XXXX]` ids against the prior day's. For every id open in N-1 but absent in N:

1. It has a ✅ Recently Completed entry in today's file → confirmed closure, OK.
2. It has an explicit `//==<<` drop directive since N-1 → confirmed drop, OK.
3. It appears in `action-items/archive/stale-items-*.md` with an `_auto-archived <today>` note → stale auto-archive, OK. Do not re-add it (see *Stale Items Auto-Archive*).
4. **Otherwise → silent dropoff.** Re-add it to today's file with a `[carried-via-audit]` annotation, surface `🚨 N items silently dropped from yesterday — please verify` in the notification, and write a `review-queue.md` entry. Never let an open item vanish without one of (1)/(2).

### Hard Rule — Review Requests Never Drop (pre-commit)

Before committing a briefing or consolidation, run:

```bash
scripts/scoutctl action-items check-review-requests --data-dir {{SCOUT_DIR}}
```

It lists each review request the runner pre-fetched into `.scout-cache/session-context.json` that today's file does not account for. An item accounts for a request when its line or its `- Refs:` line names the PR as `owner/repo#N` or by its URL. So does a skip line with a reason, or {{USER_NAME}} having dismissed the PR's link. A request sent only to a team counts like any other. Act on the exit code:

- **0:** every request is accounted for. Go on.
- **1:** for each listed `owner/repo#N`, run `gh pr view N --repo owner/repo --json state,author,headRefName,reviewRequests,isDraft`, classify it with the three-bucket rule in the GitHub connector phase, and place it in its bucket's section with `owner/repo#N` in its `- Refs:` line. A request sent only to a team goes to 🟢 Watching (see *🔴 and 🟡 Are {{USER_NAME}}'s Move*). Only when it should not be an item at all (it merged or closed since the fetch, say), write this line under this run's 🪵 Run notes entry instead: `  - Skipped review request owner/repo#N — <reason>`. On a `weekend-briefing`, the reason may be `weekend scope`: the next weekday consolidation places it. Then run the check again until it exits 0.
- **2:** it could not check, and says why. If the context file is missing or from another day, run `scripts/scoutctl pre-session data <run mode>` once and check again. Otherwise add `review-request check could not run: <its message>` to this run's 🪵 Run notes entry and go on.

Never edit `session-context.json` to make the check pass.

### Hard Rule — Leave-State Compose Gate

Before generating any action item that names a specific person as the next-action owner (a 🟢 Watching item, since the next step is theirs), read that person's note: `scripts/scoutctl kb resolve <name>` finds it, `scripts/scoutctl kb read <path>` reads it. If it carries an active leave/out-of-office state (a `status:` containing `leave`/`oof`/`vacation`/`paternity`/`maternity`, a dated leave block overlapping today, or a 🚨/`### ACTIVE STATUS` body header) — do NOT assign them as the owner. Keep it in 🟢 and add `(on leave through <date>; auto-resume on return)`, unless the action item is itself about *responding to* that leave. This prevents assigning work to someone Scout already knows is away.

Whenever you flip an item from `[ ]` to `[x]` — or process a {{USER_NAME}}-authored `[x]`, an inline `//==<<` close-out directive ("close this out", "I don't need this anymore", "move to completed"), or a close-it-out reply — you MUST, in the same write:

1. Add an entry under `## ✅ Recently Completed` summarizing the closure with date + evidence (commit hash / transcript / message ts / the inline-comment quote) and any wikilinks the prior framing carried.
2. **Delete the original row from its origin section** (🔴/🟡/🟢). Never leave a checked row sitting in an active section — that's a duplicate-surface graveyard.
3. If the closure is too trivial to deserve a Recently-Completed entry, still delete the origin row. Never write the entry without the inverse delete.

**Inline `//==<<` close-out directives are first-class delete instructions** — acknowledging one in the next DM is insufficient; the file must be physically updated (check box → write completed entry → delete origin row → remove the marker).

**Batch exception (threshold N=5).** If a run encounters **more than 5 unprocessed `//==<<` markers** across the vault (a large batch dropped at once), do NOT try to resolve them all this run — switch to **triage mode**: inventory + categorize + route them into a dated `knowledge-base/comment-triage-YYYY-MM-DD.md` index and the right queues, leaving the markers in place, and surface the index in the wrap notification. Full rule in the KB-deep-work phase (Step 2-pre).

**Pre-commit audit** — no `[x]` rows may sit outside the Recently Completed section:

```bash
grep -nE '^\s*- \[x\]' "$DAILY_FILE" | grep -viE 'recently completed|## ✅' \
  && echo "WARN: checked rows above are outside ✅ Recently Completed — migrate or annotate '_(kept intentionally — REASON)_' before commit" >&2
```

(Sunset: retire this rule when a programmatic `action-items` done-lifecycle ships and handles section migration deterministically.)

### Hard Rule — Transcript-Derived Names Must Pass an Ontology Match

Auto-transcribed sources (Granola, Gemini/Drive auto-notes, Fathom recaps, meeting summaries) frequently mis-hear names. **Never** write a transcribed name into action items, KB, or a DM without first resolving it against the knowledge graph. For every name-shaped token from a transcribed source:

```bash
cd {{SCOUT_DIR}} && scripts/scoutctl kb name-lookup "<Token>"
```

- **Exact match** → use the matched entity with its `[[people/<slug>]]` wikilink.
- **Fuzzy match** (within threshold ≈ Levenshtein-2 / phonetic-equivalent) → use that entity, append a `[name-fuzzy-resolved]` marker.
- **No match** → write at most "Contact person matching '<verbatim-token>' (no KB match — please confirm)" and route a `[transcript-drift]` entry to `review-queue.md`. **NEVER elevate an unresolved transcribed name to a 🔴 headline** — 🟡 with the explicit "no KB match" framing is the ceiling.

If {{USER_NAME}} corrects a name ("who is X?", "X doesn't exist", "this is hallucinated"), bind the misheard form to the correct entity as a "known transcription drift" note so it resolves next time.

## Knowledge Graph Personal Tasks

Query the knowledge graph for personal tasks and deadlines:

```bash
cd {{SCOUT_DIR}} && scripts/scoutctl kb query --type task --exclude-status "completed,cancelled"
```

Each result carries its label; a `[private]` task renders in the daily file as usual (an owner-only surface) and keeps its label into the wrap, where the privacy phase decides which channel may name it.

**Always filter terminal statuses — never query bare `--type task`.** A bare query returns *every* task entity, including ones already finished, so completed work resurfaces on the daily list as if it were still open (the classic regression: a task whose `status` flipped to `completed` weeks ago reappears tomorrow). Pass `--exclude-status "completed,cancelled"` to get the currently-actionable set — task entities use mixed statuses (`open` / `in-progress` / `scheduled` / …), so excluding the terminal ones is more robust than trying to enumerate the live ones. Narrow further with `--domain personal` / `--domain work` when you want a single slice, `--status <s>` for an exact status, and `--deadline-before <ISO-date>` for deadline sweeps.

**`surface_rule` windows are authoritative.** Before rendering any `type: task` entity into the daily file, read its `surface_rule:` block. If `default: do_not_surface` and **no** `windows` entry covers today's date AND **no** `always_visible_if` condition fires, the task is intentionally muted — do not render it, do not flag it stale, do not mention it in the wrap notification. If a window covers today, render at that window's `surface:` priority using its `task:` label override if present. If `always_visible_if` fires, force 🔴, unless the task is past due: then *Past-Due Tasks Step Down* applies. A task with no `surface_rule` falls back to surfacing daily until `status: completed` (back-compat). This stops long-window tasks (multi-week trips, future-dated deadlines) from polluting the daily surface during their idle phases.

For the morning briefing, focus on:

1. **Open personal tasks** — tasks with `domain: personal` and `status: open`. These carry forward into daily action items alongside work tasks.
2. **Deadline escalation** — any task with a `deadline` field. Apply escalating priority:
   - 7+ days out: keep existing priority
   - 3-7 days out: escalate to 🟡 if not already higher, when the next action is {{USER_NAME}}'s to take
   - <3 days out: escalate to 🔴, when the next action is {{USER_NAME}}'s to take
   - past due: *Past-Due Tasks Step Down* applies instead
   - a task waiting on someone else stays in 🟢, with the deadline in its body, until it is under 3 days out; then it goes to 🟡 for {{USER_NAME}} to nudge
3. **Birthday alerts** — check if any person entity has a `birthday` field matching today's month/day. If so, add a reminder to the action items.

Personal tasks appear in the action items file in a **Personal** section after work items.

During consolidation, also check for completion signals:
- If any personal task has a `completion_signal: gmail_confirmation`, check Gmail for matching confirmations. If found, update the entity file's `status` to `completed` and add `completed_date`.
- If {{USER_NAME}} reported completion via Slack DM, update the entity file.
- Carry open personal tasks forward in the action items file's Personal section: read the tasks in `.scout-cache/session-context.json` (`personal_tasks`) and keep each one whose surface window covers today in the daily file, private ones included.

## Recurring-Task Cadences (briefing AND every consolidation)

Recurring commitments are **cadence-driven, not event-driven** — a "quiet delta" consolidation must still surface them. This is the load-bearing fix for missed standing commitments (e.g. a weekly Friday status update). If the KB has any `recurring_task` entities, run the cadence computer at compose time:

```bash
cd {{SCOUT_DIR}} && python3 scripts/recurring-task-status.py --date "$(TZ="$(scripts/scout-tz.sh)" date '+%Y-%m-%d')"
```

**If it exits non-zero, the cadence check did not run — never read that as "no recurring tasks due".** Add a 🔴 action item quoting its one-line `error:` message (it says how to fix it, e.g. a Python without PyYAML), and still surface any `recurring_task` entity whose `cadence` plainly falls today.

**Live-completion lookup (do this before trusting the date math).** For each entity with a `completion_evidence` source (`linear_project_update`, `slack_post`, `gmail_confirmation`), resolve the *real* last-completion date from the live source — e.g. Linear `get_project` → `lastUpdateAt`, or a Slack/Gmail search — and feed it back so the verdict reflects live evidence (the override is not written to the entity file):

```bash
python3 scripts/recurring-task-status.py --date "<today>" \
    --last-completed <entity-slug>=<YYYY-MM-DD>
```

For each entity the script returns:

1. **`due` / `overdue`** → **mandatory** action item. 🔴 when `surface_window` is `T-0 morning` or the item is `overdue` (for its first 2 days past due; then *Past-Due Tasks Step Down* applies); 🟡 when the window is wider than 24h. Link `[[recurring-tasks/<name>]]`. `domain: personal` ones go in the Personal section. **Unless someone else does it:** a row whose Owner reads `(not {{USER_NAME}})` (JSON `owner_is_user: false`; the list under the table names each one) is a 🟢 Watching item waiting on that owner, per *🔴 and 🟡 Are {{USER_NAME}}'s Move*. The owner comes from the entity's `observed_author`, else its `last_completed_by`.
2. **`done`** (completion evidence satisfied for this cadence window) → surface as ✅ Recently Completed, not a TODO.
3. **`surfacing`** (inside the window, not yet the due day) → 🟡 heads-up.
4. **`upcoming` / `unknown`** → no action item.

**Do not write "quiet window" / "nothing material" framing until the `due`/`overdue` list is exhausted** — a `weekly:friday` cadence is by definition material on a Friday.

## Task Surface-Rule Triggers (briefing AND every consolidation)

Paired with the recurring-task step above. The `surface_rule` render rule (under Knowledge Graph Personal Tasks) only reads the block *when a task is already being surfaced* — nothing pulls an otherwise-idle task onto the list when one of its declared triggers goes live. This step is the missing driver: the **active** form of the entity's declared surfacing intent, mirroring how the cadence computer drives `recurring_task` entities.

For every open `task` entity (query the parser for `type: task`, keep `status: open`/`in-progress`), read its `surface_rule` and compute the `always_visible_if` triggers from the entity's own fields, in the configured timezone:

- **Deadline-window triggers** (e.g. a "final week before the deadline/return date" trigger) → fire when `0 ≤ (deadline − today) ≤ <window-days>`.
- **Event-derived triggers** (a schedule change, a budget/threshold anomaly) → fire when *this run's own* connector scan or computed watch value shows the change.
- **Any `windows` entry** whose `[from, to]` range contains today.

When any trigger or window is live, surface the task as an action item at the window's `surface:` priority (default 🟡; escalate to 🔴 when `always_visible_if` fires, per the render rule) — with **explicit transition framing drawn from the entity** (e.g. *"Final week — N days to the [date] deadline: \<render the entity's prep-task list\>"*), never as a bare day-counter buried in a header. **Forbid "nothing hard-due / quiet day" framing while any task's trigger window is live.** A declared trigger that never fires because no step computed it is a silent-miss bug, not a quiet day.

## Continuity Analysis (briefing)

**Before writing any action items, synthesize the previous day's work into today's priorities.** This is not just carrying items forward — it's connecting what {{USER_NAME}} was working on, what changed overnight, and what today's best use of time is.

For each carryover item from the previous action-items file:

1. **Time-based triggers:** Does the item have an implicit or explicit timer? "Monitoring 2–3 days for clean data" → count the days since the fix; flag when the window is ending. "Wait ~1 week, then build X" → when does the week end? A dated handover/availability window → how many actionable days remain before someone is out?
2. **Changed context:** Did anything happen overnight (from the connector queries) that changes this item's priority or next step? A PR that was blocked yesterday may have merged; a "waiting on X" item may have gotten its reply.
3. **Work trajectory:** Based on recent sessions, messages, and commits, what was {{USER_NAME}} MOST focused on? Today's briefing should build on that momentum, not ignore it.

**Output: 2–3 "Today you should…" recommendations** at the TOP of the action items file, above the 🔴 section — synthesized guidance, not new action items:

```markdown
## 💡 Today's Focus (continuity from yesterday)
- **Finish X** — [why today is the right day, what changed]
- **Check Y** — [monitoring window ending, follow-up due]
- **Prepare for Z** — [meeting coming up, deadline approaching]
```

This is the file-side counterpart of the wrap-DM continuity line (see the notification phase) — the DM opens with the throughline; this section carries the reasoning.

**Day-planning line.** When the engine advertises `planning_v1` (`scripts/scoutctl manifest show`), check today's plan with `scripts/scoutctl action-items list --json --with-plan --include-done` (a task checked off after its block still needs its actual time). Add at most one Focus bullet:
- items carry a `block:` dated before today with no `actual:` -> `**Log yesterday's actual times**: run /scout-plan review (N blocks)`;
- otherwise, no open item carries a `block:` dated today -> `**Plan the day**: run /scout-plan (N open to-dos, none scheduled)`.

Skip it in weekend briefings and when the user has already planned today.

## Mandatory Cross-Check

**Before ANY item becomes a To Do, it must pass ALL available cross-checks.** The cross-check adapts to your connected services:

- **If Calendar connected:** Is this already scheduled? Does a meeting already exist for this? Was an event recently cancelled (meaning {{USER_NAME}} already handled it)?
- **If project tracker connected (e.g., Linear, GitHub Issues, Jira):** Does a ticket already exist? Has it already been resolved?
- **If messaging connected (e.g., Slack, email):** Did {{USER_NAME}} already handle this? Search outbound messages about this topic. If {{USER_NAME}} sent a message about it, the item is likely handled or in progress.
- **If code host connected (e.g., GitHub, GitLab):** Did {{USER_NAME}} already submit a PR, merge code, or commit changes related to this? Check recent activity.
- **Always (regardless of connectors):** Is this the same item phrased differently? Deduplication pass across all candidates.

For each candidate action item, apply every available cross-check from the list above. The number of checks scales with your connected services — a 2-connector setup uses 2 checks, a 5-connector setup uses all 5. Every check that CAN be run MUST be run before an item is written.

## Source Equality for Action Items

**Meeting transcripts and messages are signals, not facts.** Every action item candidate must be verified against other available sources before being written down. A meeting transcript saying "{{USER_NAME}} will do X" does not mean X is a valid action item — it means X is a *candidate* that must survive the cross-check.

**{{USER_NAME}}'s own actions are the most important signal.** What {{USER_NAME}} has actually DONE (messages sent, meetings cancelled, code committed, PRs submitted) always takes priority over what notes say they should do. If a meeting transcript says "{{USER_NAME}} will send the proposal" but {{USER_NAME}} already sent it (found in outbound messages or sent mail), the item is Done, not To Do.

## Per-Item Reconciliation (Consolidation Mode)

During consolidation runs, every action item being written or updated must go through individual reconciliation. This is the most important step — do not batch or shortcut it.

**For EVERY action item:**

### 1. Check if {{USER_NAME}} already handled it
Search for evidence that {{USER_NAME}} completed or progressed the item:
- Outbound messages or DMs about the topic
- Calendar changes (cancelled events, new events created)
- Code commits, PRs opened or merged
- Documents created or edited
- Session history showing work done with AI tools

If evidence of completion exists, mark the item ✅ Done with a citation to the evidence.

**No unverified negatives.** Before asserting a *negative* completion-state — "not done yet", "still your move", "awaiting X", "unsent", "not created" — where the completion would be **observable on a connector**, you MUST query that connector this run. A "nothing happened" is a claim, not the default: an unverified negative is the easiest thing to assert lazily and the hardest to notice is wrong, and telling {{USER_NAME}} that completed work was ignored erodes trust. If you can't query (connector down, out of scope), write the claim `[unverified — not queried this run]`, never as fact. (Repo-creation items are the classic trap — verify with `gh repo list`, not a PR scan; see the GitHub Repository Activity phase.)

### 2. Do a targeted topic search
Search specifically for the topic keywords across all available connectors. Don't rely on the broad scan — do a focused search for this specific item. This catches context that a general sweep might miss.

### 3. Enrich with specifics
Never write vague action items. If a meeting transcript says "{{USER_NAME}} will PR something," don't write that — search the code host for the actual PR. If a to-do says "contact someone about a project," check messaging and the issue tracker to see if that's already been done and what the actual next steps are.

**Bad:** "Follow up on the deployment issue"
**Good:** "Reply to [Person]'s message in #project-channel about the staging deployment failure (error: connection timeout on service X) — they asked for help debugging at 2:15 PM"

### 4. Apply the cross-check
Run every available cross-check (calendar, issue tracker, messaging, code host, deduplication) against this specific item.

### 5. Write with full context and evidence
- If completed: mark ✅ with evidence (link to the message, calendar change, PR, commit, etc.)
- If partially done: describe what's done and what specifically remains in the item's note (`action-items/context/<TAG>.md`); the line keeps a one-line status
- If not started: write the full context from all sources into the item's note, not just the one that surfaced it
- Always include source citations showing which connectors confirmed the item — in the note

After reconciliation is complete, refresh the `## 🪵 Run notes & connector availability` block at the **bottom** of the action items file: keep **one line per run** (≤ 300 chars: timestamp, mode, counts, connector availability) for the last 3 runs only. Do not write run metadata at the top of the file.

## Item line format (enforced: 60 KB file, 500-char lines)

Each item is **one line of up to 300 characters**, plus **at most one sub-bullet** of up to 300 characters (the blocker, or {{USER_NAME}}'s comment thread):

`- [ ] [#XXXX] **Title** — next step · due YYYY-MM-DD · → [[<project, topic or source note>]]`

A 🟢 Watching line says whom it waits on instead: `— waiting on <who> since <date>`. Tags and other refs go in `- Refs:` (*Clean Title, Prose Body, Refs Block*).

**Context lives in the item's own note**, `action-items/context/<TAG>.md`, never in extra sub-bullets. Create it in the same commit as the item. Frontmatter: `tag`, `title` (plain text), `created`, `updated` (YYYY-MM-DD). Body: what this is and why, current status, what blocks it, the evidence (quotes, timestamps, ticket and PR state), and links to the project, topic and source notes that hold the durable knowledge. When the item gains context, update the note (and its `updated` date), not the daily file. List notes with `scripts/scoutctl kb files --glob 'action-items/context/*.md'` and read one with `scripts/scoutctl kb read <path>`, never `ls` or `cat`. Carry-forward never touches the note. **A re-tag renames the note** to the new tag in the same commit (`git mv`) — the one exception to "never rename or move existing files". The `→ [[…]]` slot may point at the note as `→ [[action-items/context/<TAG>|context]]`; it is optional, because the apps find the note by tag. The only exception is machine lines: plan markers, `- Refs:` and the board fields in **Board Fields**, each written only when it adds something.

**Fixed sections, in order:** `## 🎯 Today's Focus` (≤ 5) · `## 🔴 Urgent` · `## 🟡 To Do` · `## 🟢 Watching` · `## 🅿️ Parked` · `## 📅 Meetings` · `## ✅ Recently Completed` (7 days, then the line is dropped; git keeps it) · `## 📋 Scout Digest` (see below) · `## 🪵 Run notes & connector availability` (bottom).

**A tier change moves the line.** Never write "Demoted by…", "Re-tiered by…", "Relocated from…" or "From this run…" sections. Git records why.

## Scout Digest

At the end of every briefing and consolidation run, create or update the **`## 📋 Scout Digest — <date> (<time>)` section at the bottom of today's action-items file** (`action-items/action-items-YYYY-MM-DD.md`), just above `## 🪵 Run notes & connector availability`. This is the same digest the dreaming phase maintains — there is **one** digest per day, shared across all of today's sessions, so {{USER_NAME}} can catch up on what {{INSTANCE_NAME}} has been doing across runs without reading each one. The companion apps build their Digest view from this `📋` section, so it must stay an H2 in the daily file.

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
- If a digest already exists from an earlier session today, **update it in place** (don't duplicate) — bump the time, fold in this run's changes, and trim older detail so the section stays under ~6 KB.
- Only include files that changed **substantively** (not just timestamp updates).
- "Your Input Needed" lists ONLY items where {{USER_NAME}}'s action unblocks {{INSTANCE_NAME}} or a project.
- Keep it scannable — no walls of text. Link to KB files for details.
- On a `weekend-briefing` run, fold the **Monday Preview** into the digest rather than emitting it separately.
