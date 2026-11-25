---
phase: research
name: Commit and Notify
slot: commit-notify
mode: [research]
requires: null
---

# PHASE 4: COMMIT & NOTIFY

## Step 4a: Commit

Explicit paths through the helper, **never `git add -A`**: a concurrent session shares this tree, and `-A` commits its half-written files.

```bash
{{SCOUT_DIR}}/scripts/git-safe-commit.sh "research [HH:MM]: <threads worked and what moved>" <only the paths THIS run touched>
```

The paths include the lab files this run wrote under `knowledge-base/lab/` (front pages, `<slug>-timeline.md`, experiment notes). Exit 4: nothing changed. Exit 3 or 5: report it in the notification; never fall back to `-A`. Timestamp: `scripts/scout-tz.sh date '+%H:%M'`.

## Step 4b: Session Entry

Add one row (≤ 500 chars) to this month's shard `knowledge-base/session-log/YYYY-MM.md`, with Edit/Write (never `ls`/`tail`/`grep` the KB to find it):

`| YYYY-MM-DD | HH:MM | Research | <one sentence: threads worked, what moved, notes updated, with [[links]]> | <commit> |`

Then commit it: `{{SCOUT_DIR}}/scripts/git-safe-commit.sh "research [HH:MM]: session log" knowledge-base/session-log/YYYY-MM.md`.

## Step 4c: Session Cost

```bash
{{SCOUT_DIR}}/scripts/write-session-cost.sh research 0 0 0
```

---

phase: research
name: Slack Research Notification
slot: notification
mode: [research]
requires: slack
---

## Step 4d: Slack Notification

**Send this DM even when this file also has a Step 4d: Telegram Notification:** the wrap then goes to both. The vault's `notify.wrap_channels` in `scout-config.yaml` decides which channels get a step, so if this step is here, send it.

Run `scripts/scoutctl lab list --json` at wrap time, then DM {{USER_NAME}} (Slack ID: `{{USER_SLACK_ID}}`) in 5–8 lines:
- **Threads:** one line per touched thread ("Q3 now has a planned experiment; Q1 answered").
- **Approvals:** the `needs_you.proposals` proposals awaiting your approval.
- **Comments:** N comments still waiting, the sum of `unanswered_comments` over `active` and `paused` threads.
- **Experiments:** each one run, any left `not run`, each detached one still running with its elapsed time, and the sum of `checkpoint_bytes` across the lab.
- Insights to act on, and any refusal or mandate date to report.

Name only normal-visibility threads: "N private threads also moved" is allowed. Send it through the privacy phase's outbound-draft step (`.scout-cache/outbound-draft.md`, `scripts/scoutctl kb check-outbound` with `--surface slack`, send only on exit 0).

---

phase: research
name: Telegram Research Notification
slot: notification
mode: [research]
requires: telegram
---

## Step 4d: Telegram Notification

Send the research wrap to {{USER_NAME}}'s Telegram chat, **alongside** any Slack DM in this file (send both). This is the owner's private chat, an owner-only surface: it may name `[private]` items and private threads by their exact title, which the Slack DM never does. Run `scripts/scoutctl lab list --json` at wrap time for: `needs_you.proposals`; `unanswered_comments` over `active` and `paused` threads; experiments run, left `not run`, or detached and still running (elapsed); `checkpoint_bytes` summed across the lab.

```bash
scoutctl notify telegram --tier info --parse-mode html --body-file - <<'SCOUT_WRAP'
<b>Scout research complete.</b>
• [one line per touched thread, e.g. Q3 now has a planned experiment; Q1 answered]
• [N] proposals awaiting your approval
• [N] comments still waiting
• Experiments: [run / not run / still running, elapsed; checkpoint bytes]
SCOUT_WRAP
```

Add insights to act on and any refusal or mandate date; 5-8 lines. Use only `<b> <i> <u> <s> <a href="…"> <code> <pre> <blockquote>`, escape `&`, `<` and `>` in any text you did not write, and use only `http(s)://` or `mailto:` links. A non-zero exit means the Telegram wrap was not delivered: say so in your final message, and do not fall back to Slack (never resend this message, or anything only it carried, as a Slack DM).
