---
phase: research
name: Commit and Notify
slot: commit-notify
mode: [research]
requires: null
---

# PHASE 4: COMMIT & NOTIFY

## Step 4a: Commit

```bash
git -C "{{SCOUT_DIR}}" add -A && git -C "{{SCOUT_DIR}}" commit -m "research [HH:MM]: <summary of targets researched and key findings>"
```

Use `TZ="$(scripts/scout-tz.sh)" date '+%H:%M'` for the timestamp (the computer's own zone, unless `scout-config.yaml` sets a `timezone:` override).

## Step 4b: Session Entry

Add one row (≤ 500 chars) to this month's shard `knowledge-base/session-log/YYYY-MM.md`, with Edit/Write (never `ls`/`tail`/`grep` the KB to find it):

`| YYYY-MM-DD | HH:MM | Research | <one sentence: targets researched, key findings, entities updated, with [[links]]> | <commit> |`

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

**Skip this step when this file also has a Step 4d: Telegram Notification:** the wrap then goes to Telegram only.

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

Send the research wrap to {{USER_NAME}}'s Telegram chat **instead of** any Slack DM (Telegram is the personal channel; a research summary must not land in a work Slack workspace):

```bash
scoutctl notify telegram --tier info --parse-mode html --body-file - <<'SCOUT_WRAP'
<b>Scout research complete.</b>
• Researched: [targets]
• Key findings: [1–3 lines]
• Entities updated: [list]
SCOUT_WRAP
```

Same content as the Slack version: what was researched, key new information, entities updated, actionable insights; 5-8 lines. Use only `<b> <i> <u> <s> <a href="…"> <code> <pre> <blockquote>`, escape `&`, `<` and `>` in any text you did not write, and use only `http(s)://` or `mailto:` links. A non-zero exit means the wrap was not delivered: say so in your final message, and do not fall back to Slack.
