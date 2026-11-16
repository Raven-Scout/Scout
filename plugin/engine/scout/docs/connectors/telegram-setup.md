# Telegram outbound — operator setup

Telegram outbound lets a Scout session push notifications to your phone, in a
Telegram chat. Once set up, it carries the session-wrap message too, alongside
the Slack DM unless you choose one channel (see §7).
It is **outbound only by design** in v0.4: Scout sends; you receive. There is
no return-bridge yet (inbound replies as feedback signals are v0.7+ territory
per the event-architecture spec).

The integration uses a Telegram **bot account** that you create via
[@BotFather](https://t.me/BotFather), paired with a single chat-id (yours).
The bot can ONLY message that one chat — there is no broadcast surface, no
group reach, and no way for the bot to message anyone you haven't explicitly
DM'd first. That's the privacy posture, and it's enforced by Telegram, not
by Scout.

## Privacy posture (what Scout will and won't do)

- **Outbound only.** The connector is registered in `connectors.yaml` with
  `capabilities: [outbound]`. The CLI command `scoutctl notify telegram`
  wraps a single Telegram Bot API method (`sendMessage`). No read tools,
  no inbound webhooks, no polling for replies in v0.4.
- **One chat-id per install.** Your bot has one destination — the chat-id
  you saved during setup. Scout cannot fan out to other chats; the Bot API
  call hardcodes the saved chat-id.
- **Secrets stay local.** The token + chat-id live in `~/.scout-secrets/`
  on your Mac, mode 600. Nothing transits a Scout-operated server.
- **Tier maps to push behavior.** `--tier info` posts silently
  (`disable_notification=true`); `--tier action_required` posts loud. That's
  the only push knob — Scout doesn't sound the notification beyond it.
  `--parse-mode html` only changes how the text is rendered (see
  [Formatting](#formatting-html-mode)).

If any of those don't sit right, don't enable this connector — your Slack DM
covers the same wrap-message use case.

## 1. Create the bot via @BotFather

Open Telegram and DM [@BotFather](https://t.me/BotFather):

1. Send `/newbot`.
2. Pick a **display name** — anything. e.g. `Scout`.
3. Pick a **username** — must end in `bot`. e.g. `your_scout_bot`.
4. BotFather replies with a token that looks like
   `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`. **Save this** — it's the
   only auth credential the API needs, and treating it like a password is
   correct (anyone who has it can post as your bot).

If the username is taken, BotFather will say so; pick another.

## 2. Save the token to `~/.scout-secrets/`

```bash
mkdir -p ~/.scout-secrets
chmod 700 ~/.scout-secrets
printf '%s' '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11' > ~/.scout-secrets/telegram-bot-token
chmod 600 ~/.scout-secrets/telegram-bot-token
```

`printf '%s'` (no trailing newline) is intentional — `_read_secret()`
strips whitespace, but skipping the newline avoids ambiguity if you ever
`cat` the file by hand.

> **Don't commit this anywhere.** `~/.scout-secrets/` is per-user and
> outside the repo on purpose. Your home dir's gitignore should already
> cover it; double-check you're not inside a tracked directory before
> running the command.

## 3. DM your new bot once

Telegram bots can only send messages to chats they're already in. The
quickest way to add yours is:

1. In Telegram, search for your bot's `@username`.
2. Open the chat and tap **Start** (or just send `hi`).

That's it — your bot now knows about a chat it can post to. The chat-id is
your personal account's id with respect to this bot.

## 4. Capture the chat-id

Telegram surfaces the chat-id via the Bot API's `getUpdates` method. Run:

```bash
TOKEN="$(cat ~/.scout-secrets/telegram-bot-token)"
curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates" | jq '.result[0].message.chat.id'
```

You should see a number — usually a 9–10 digit positive int for personal
DMs, or a negative int for group chats. Save it:

```bash
printf '%s' '987654321' > ~/.scout-secrets/telegram-chat-id
chmod 600 ~/.scout-secrets/telegram-chat-id
```

> **If `getUpdates` returns an empty `result` array,** you haven't actually
> sent the bot a message yet. Go back to step 3 and send any text. Telegram
> only retains updates for ~24h and only for chats with recent activity.

> **Group chats are supported** but require an extra step — add the bot to
> the group, then post anything in the group. The chat-id will be negative.
> Use the same file path; the value can be either a string or a number per
> Telegram's API. Scout treats it as a raw string.

## 5. Verify with a dry-run

Before sending real traffic, exercise the wiring with `--dry-run`:

```bash
scoutctl notify telegram --tier info --body "hello from Scout" --dry-run
```

Expected output:

```
[dry-run] POST https://api.telegram.org/bot.../sendMessage
[dry-run] body: {"chat_id": "987654321", "text": "hello from Scout", "disable_notification": true}
{
  "id": "01J...",
  "ts": "2026-04-28T...",
  "kind": "notification.sent",
  "source": "cli:notify_telegram",
  "payload": {
    "tier": "info",
    "channel": "telegram",
    "body_chars": 16,
    "dry_run": true
  }
}
```

The `[dry-run]` lines go to stderr; the JSON Event payload goes to stdout
(safe to `| jq`).

If the dry-run fails with `Missing secret: ...`, one of the two files is
absent or unreadable — re-check steps 2 and 4. Dry-run reads the secrets
on purpose so this kind of install gap surfaces immediately rather than
later when a real send is attempted.

## 6. Send for real

Drop `--dry-run`:

```bash
scoutctl notify telegram --tier info --body "hello from Scout"
```

Within a few seconds, the message should appear in your Telegram chat with
the bot. Silent push (`info`) won't sound; loud push (`action_required`)
will.

## 7. How Scout uses this

The runners (`run-scout.sh`, `run-dreaming.sh`, `run-research.sh`) only say
that a wrap message is owed. The brain file's notification section names the
channel and the recipient:

| Runner             | Reads the channel from                                               |
|--------------------|----------------------------------------------------------------------|
| `run-scout.sh`     | `SKILL.md`'s notification sections                                   |
| `run-dreaming.sh`  | `DREAMING.md`'s notification section, else `SKILL.md`'s              |
| `run-research.sh`  | `RESEARCH.md`'s Step 4d, else `SKILL.md`'s notification sections     |

Turn it on with the `telegram` connector:

- **New vault:** once both secrets from steps 2 and 4 exist, `/scout-setup`
  detects `telegram` (`test -s` on the two files; no network call). Enable it.
- **Existing vault:** add `telegram` to `connectors.enabled` in your vault's
  `scout-config.yaml`, then run `/scout-update`.

SKILL.md then gains a **Telegram Notification** section and RESEARCH.md a
**Step 4d: Telegram Notification**. Dreaming has no section of its own and
follows SKILL.md.

**Telegram is added to the Slack DM, not swapped for it.** With both Slack and
Telegram enabled, every wrap goes to both. To keep the wrap on one channel, set
`notify.wrap_channels` in your vault's `scout-config.yaml`, then run
`/scout-update`:

```yaml
notify:
  wrap_channels: [telegram]   # or [slack]; leave it out for both
```

Only `slack` and `telegram` are accepted; anything else, or an empty list,
makes `/scout-update` stop with an error instead of silently dropping the wrap.
Leaving a channel out removes only its notification section: Slack scans are
unaffected.

**Privacy still applies to each channel on its own.** Slack is a shared
surface, so the Slack DM goes through `scoutctl kb check-outbound` and never
names a `[private]` item. This Telegram chat is owner-only, so it may. With
both on, private items reach Telegram and the daily file, never Slack. If a
Telegram send fails, the session says so in its final message and does not
resend that message on Slack.

The section tells the session to pipe the wrap in through stdin, in HTML mode:

```bash
scoutctl notify telegram --tier info --parse-mode html --body-file - <<'SCOUT_WRAP'
<b>Scout morning briefing ready.</b>
...
SCOUT_WRAP
```

`--body-file -` reads the body from stdin, so a wrap that quotes Slack, GitHub
or mail text never goes through a shell argument (`--body-file PATH` reads a
UTF-8 file instead). Tier choice mirrors urgency: a session that surfaced 🔴
Urgent items escalates with `--tier action_required`; a routine run posts
`--tier info`.

To change the wording or the rule, edit the section in your vault's SKILL.md
or RESEARCH.md; `/scout-update` keeps your edits.

## Tier semantics

| Flag                        | Telegram `disable_notification` | Phone behavior     |
|-----------------------------|-------------------------------:|--------------------|
| `--tier info` (default)     |                          `true` | silent push (banner only) |
| `--tier action_required`    |                         `false` | loud push (sound + banner) |

There are only two tiers. `--tier critical` etc. are NOT supported — Scout
treats anything beyond the two as a usage error.

## Formatting (HTML mode)

By default the body goes out as plain text. `--parse-mode html` sends it with
Telegram's `parse_mode=HTML`, so you can use bold, italic, underline and
links:

```bash
scoutctl notify telegram --tier info --parse-mode html \
  --body '<b>Morning briefing</b> — <i>3 items</i>, <a href="https://example.com/pr/1">PR #1</a>'
```

- **Tags Telegram accepts:** `<b>`, `<i>`, `<u>`, `<s>`, `<a href="…">`,
  `<code>`, `<pre>` (optionally `<pre><code class="language-…">`) and
  `<blockquote>`. Nothing else — no `<br>`, `<p>`, `<ul>`: use plain
  newlines and `•` for lists.
- **Escape `<`, `>` and `&` in text** as `&lt;`, `&gt;` and `&amp;`, also in
  a URL inside `href` (`?a=1&amp;b=2`).
- **Scout keeps only `http://`, `https://` and `mailto:` links, written as
  `<a href="…">` with no other attribute.** Any other `<a>` (`tg://`,
  `obsidian://`, `javascript:`, a second `href`, `target=…`) is replaced by
  its label before sending, and a `warning: dropped N link(s) …` line goes to
  stderr.
  Telegram itself would keep a `tg://` deep link live (join a chat, add a
  proxy) and silently drop an `obsidian://` link. Put such a path in `<code>`
  instead.
- **Escape any text you did not write** (Slack, GitHub, mail) with
  `html.escape` before you put it in an HTML body. Only links Scout builds
  itself should be `<a>`: an unescaped `<a href="https://evil">` would show as
  a live link with someone else's label.
- **Why HTML and not MarkdownV2:** HTML needs only those three characters
  escaped. MarkdownV2 needs ~18, and one miss gets the whole message refused.

**A formatting mistake never loses the message.** If Telegram answers
`400 Bad Request: can't parse entities`, Scout resends that chunk as plain
text: tags stripped, link URLs kept in parentheses (`label (https://…)`),
entities unescaped. It prints a `warning: Telegram refused the HTML …` line
on stderr, and the Event payload gets `"parse_fallback": true`. Any other
error (bad chat-id, revoked token) is not retried. In HTML mode the Event
payload also carries `"parse_mode": "html"`.

## Long messages

Telegram caps `sendMessage` at 4096 chars per call. Scout splits longer
bodies on paragraph (`\n\n`), then line (`\n`), then word (` `), then a
hard cut at 4096. You'll see one push notification per chunk; the receive
order is sequential.

In HTML mode a cut never lands inside a tag or an entity, and each chunk is
valid on its own: a tag still open at the cut is closed at the end of that
chunk and reopened, with its attributes, at the start of the next. A chunk of
only whitespace is skipped, since Telegram refuses an empty message. Input
that is unbalanced to begin with (a stray `</b>`) is not repaired; Telegram
refuses that chunk and it goes out as plain text, as above. The same happens
if the tags open at a cut, with their closers, would take more than half a
chunk (about 2000 characters of nesting): Scout stops carrying them over.

## Troubleshooting

| Symptom                                                          | Likely cause                                                                |
|------------------------------------------------------------------|------------------------------------------------------------------------------|
| `Missing secret: .../telegram-bot-token`                         | Step 2 not done or path typo.                                                |
| `Missing secret: .../telegram-chat-id`                           | Step 4 not done.                                                             |
| `HTTP error: 401 Unauthorized`                                    | Token revoked by BotFather (`/revoke`) or copy-paste corruption.            |
| `HTTP error: 400 Bad Request: chat not found`                     | Wrong chat-id, or you never DM'd the bot (step 3 missed).                   |
| `warning: Telegram refused the HTML of chunk …`                  | `--parse-mode html` body has an unsupported or unclosed tag, or an unescaped `<`/`&`. The chunk was sent as plain text. |
| `warning: dropped N link(s) whose href is not http(s) or mailto` | `--parse-mode html` body has an `<a href>` with another scheme. The link was sent as its plain label. |
| Bot reachable but no message arrives                              | Check phone notification settings — silent push respects Do Not Disturb.    |
| `getUpdates` returns empty `result`                              | No recent activity in the chat. Send the bot a message; retry within 24h.   |

To rotate the token: in BotFather, `/revoke` against the bot, get a new
token, overwrite `~/.scout-secrets/telegram-bot-token`. The chat-id stays
valid across token rotations.

## See also

- [`whatsapp-setup.md`](whatsapp-setup.md) — sibling connector doc for the
  inbound side. Bidirectional Telegram (inbound replies as feedback
  signals) is v0.7+ per the event-architecture spec; it'll mirror the
  privacy posture documented in `whatsapp-setup.md` §6 (the lethal
  trifecta) once we're there.
- `engine/scout/connectors.yaml` — the `notify:telegram` row that registers
  this connector with the manifest.
- Telegram Bot API reference: <https://core.telegram.org/bots/api#sendmessage>.
- BotFather: <https://t.me/BotFather>.
