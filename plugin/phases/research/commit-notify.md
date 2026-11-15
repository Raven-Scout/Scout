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
{{SCOUT_DIR}}/scripts/git-safe-commit.sh "research [HH:MM]: <summary of targets researched and key findings>" <only the paths THIS run touched>
```

Exit 4: nothing changed. Exit 3 or 5: report it in the notification; never fall back to `-A`.

Use `TZ="$(scripts/scout-tz.sh)" date '+%H:%M'` for the timestamp (the computer's own zone, unless `scout-config.yaml` sets a `timezone:` override).

## Step 4b: Session Entry

Add one row (≤ 500 chars) to this month's shard `knowledge-base/session-log/YYYY-MM.md`, with Edit/Write (never `ls`/`tail`/`grep` the KB to find it):

`| YYYY-MM-DD | HH:MM | Research | <one sentence: targets researched, key findings, entities updated, with [[links]]> | <commit> |`

Then commit it: `{{SCOUT_DIR}}/scripts/git-safe-commit.sh "research [HH:MM]: session log" knowledge-base/session-log/YYYY-MM.md`.

## Step 4c: Session Cost

```bash
{{SCOUT_DIR}}/scripts/write-session-cost.sh research "${CLAUDE_MAX_BUDGET_USD:-0}" "${CLAUDE_BUDGET_SPENT_USD:-0}" 0
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

Send a brief DM to {{USER_NAME}} (Slack ID: `{{USER_SLACK_ID}}`) summarizing:
1. What was researched
2. Key new information discovered
3. Entities updated
4. Actionable insights (if any)

Keep it concise — 5-8 lines max. The value is in the KB updates, not the summary. Send it through the privacy phase's outbound-draft step (`.scout-cache/outbound-draft.md`, `scripts/scoutctl kb check-outbound` with `--surface slack`, send only on exit 0).

---

phase: research
name: Telegram Research Notification
slot: notification
mode: [research]
requires: telegram
---

## Step 4d: Telegram Notification

Send the research wrap to {{USER_NAME}}'s Telegram chat, **alongside** any Slack DM in this file (send both). This is the owner's private chat, an owner-only surface: it may name `[private]` items, by their exact title, which the Slack DM never does:

```bash
scoutctl notify telegram --tier info --parse-mode html --body-file - <<'SCOUT_WRAP'
<b>Scout research complete.</b>
• Researched: [targets]
• Key findings: [1–3 lines]
• Entities updated: [list]
SCOUT_WRAP
```

Same content as the Slack version: what was researched, key new information, entities updated, actionable insights; 5-8 lines. Use only `<b> <i> <u> <s> <a href="…"> <code> <pre> <blockquote>`, escape `&`, `<` and `>` in any text you did not write, and use only `http(s)://` or `mailto:` links. A non-zero exit means the Telegram wrap was not delivered: say so in your final message, and do not fall back to Slack (never resend this message, or anything only it carried, as a Slack DM).
