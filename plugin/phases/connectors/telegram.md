---
phase: connector
name: telegram
slot: notification
mode: [consolidation, briefing]
requires: telegram
---

## Telegram Notification

Send the wrap notification to {{USER_NAME}}'s Telegram chat, summarizing the run results. The bot token and chat ID live in `~/.scout-secrets/`; never print or log them.

**Telegram is sent alongside any Slack DM, not instead of it.** If this file also has a Slack Notification section, send both: the Slack DM through its outbound check, and this one here. The vault's `notify.wrap_channels` in `scout-config.yaml` decides which channels get a notification section, so if this section is here, send it. This is {{USER_NAME}}'s private chat, an owner-only surface: unlike the Slack DM, it may name `[private]` items, by their exact title. Never copy that private content into the Slack DM.

### How to send

Write the body to stdin with a quoted heredoc, so nothing in it is expanded by the shell:

```bash
scoutctl notify telegram --tier info --parse-mode html --body-file - <<'SCOUT_WRAP'
<b>Scout consolidation complete.</b>
• Action items: 3 new, 2 completed, 5 carried forward
• Urgent: none
SCOUT_WRAP
```

- **Tier:** `action_required` (a loud push) when the run surfaced 🔴 urgent items; `info` (silent) otherwise.
- **HTML:** use only `<b> <i> <u> <s> <a href="…"> <code> <pre> <blockquote>`. Escape `&` as `&amp;`, `<` as `&lt;` and `>` as `&gt;` in **every** piece of text you did not write yourself (Slack messages, PR titles, mail subjects, file names). If Telegram refuses the HTML, Scout resends the message as plain text, so a mistake costs the formatting, not the message.
- **Links:** only `http(s)://` and `mailto:` links survive; Scout drops any other scheme and keeps its label. An `obsidian://` link will not be clickable, so name the file instead: `action-items/action-items-YYYY-MM-DD.md` in `<code>`.
- A non-zero exit means the Telegram wrap was not delivered: say so in your final message. Do not fall back to Slack: never resend this message, or anything only it carried, as a Slack DM. A Slack Notification section in this file is sent on its own terms either way.

### Consolidation Notification (3-5 lines)

```
<b>Scout consolidation complete.</b>
• Action items: X new, Y completed, Z carried forward
• KB audited: [files checked/updated]
• Urgent: [any urgent items, or "none"]
```

### Briefing Notification (5-8 lines)

Open with a one-line **Today's focus**: the one or two things today is really about, connecting recent work to now. Counts come after it.

```
<b>Scout morning briefing ready.</b>
<b>Today's focus:</b> [the 1–2 things today is really about]
• Today's meetings: [count] ([first meeting time])
• Action items: X urgent, Y to-do, Z watching
• New since yesterday: [brief summary]
• Review queue: [count] items pending your review
```

### Rules

- End with a one-line footer naming what was actually scanned: `Sources checked: N CC sessions · N commits · N Slack threads · …`. Count only sources you queried this run; a "quiet" run must show non-zero counts.
- No bare internal `#TAG` shortcodes: expand them to the item's plain-English label or drop them.
- No sensitive details: summaries and counts. Name urgent items briefly.
- Say where the full report is: `{{SCOUT_DIR}}/action-items/`.
- If a connector was unreachable, say so, so {{USER_NAME}} knows the run was partial.
