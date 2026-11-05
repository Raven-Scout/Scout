# The Scout Slack app — operator setup

Without this, every Slack message a Scout run sends goes through the claude.ai Slack
connector and is posted **as you**: the wrap lands in your own self-DM, looks like
something you wrote, and never notifies your phone. With the Scout app installed, the
same messages come **from Scout**, a bot user in your workspace, as ordinary DMs that
push to your phone and that you can reply to in thread.

Reading Slack is unchanged: runs still read through the connector, as you. The app can
only post.

## Privacy posture

- **Two bot scopes, nothing else.** `chat:write` (post) and `im:write` (open the DM).
  No history, no channel reads, no user scopes, no events, no socket mode.
- **One recipient.** `scoutctl notify slack` DMs `user.slack_user_id` from your
  `scout-config.yaml` and has no flag for anyone else or any channel.
- **Same outbound check as the connector.** The workspace isn't yours, so the DM is
  the shared surface `slack`: a body that names a private note is dropped (exit 4).
- **The token stays local.** `~/.scout-secrets/slack-bot-token`, mode 600, sent only
  in the `Authorization` header to `slack.com`. Nothing transits a Scout server.

## 1. Create the app from the manifest (about 3 minutes)

1. Open <https://api.slack.com/apps> → **Create New App** → **From a manifest**.
2. Pick the workspace you want Scout to post in.
3. Paste the contents of
   [`slack-app-manifest.json`](slack-app-manifest.json) (JSON tab) and create it.
4. Optional: under **Basic Information → Display Information**, upload the Scout icon.

The app belongs to that workspace and isn't distributed anywhere else. If your workspace
requires admin approval for apps, **Install** becomes **Request to install**; Scout keeps
posting through the connector until an admin approves.

## 2. Install it and save the bot token

1. **OAuth & Permissions** → **Install to Workspace** → **Allow**.
2. Copy the **Bot User OAuth Token** (it starts with `xoxb-`).
3. Save it without putting it in your shell history:

```bash
mkdir -p ~/.scout-secrets && chmod 700 ~/.scout-secrets
pbpaste > ~/.scout-secrets/slack-bot-token && chmod 600 ~/.scout-secrets/slack-bot-token
```

Make sure `user.slack_user_id` is set in your vault's `scout-config.yaml` (your member ID:
Slack profile → ⋮ → **Copy member ID**).

## 3. Test it

```bash
scoutctl notify slack --dry-run --body "health check"
scoutctl notify slack --body "Scout can post as itself now."
```

The second one should arrive as a DM from **Scout** and push to your phone. Its stdout is
the Event JSON; `payload.ts` is the message's timestamp, and
`scoutctl notify slack --thread-ts <ts> --body "…"` replies in its thread.

## How runs use it

The Slack Notification section of SKILL.md sends the wrap with `scoutctl notify slack`
whenever `~/.scout-secrets/slack-bot-token` exists, and falls back to the connector DM
otherwise. Your replies in the Scout DM's threads are read the same way as before, through
the connector.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Sent (or dry run). |
| 1 | Bad arguments or empty body. |
| 2 | Slack refused (`invalid_auth`, `missing_scope`, `thread_not_found`, …) or the network failed. |
| 3 | A byte-identical message went to the same thread in the last 10 minutes; `--allow-duplicate` overrides. |
| 4 | The body names a private note, or no vault was found to check it against. Nothing sent. |
| 10 | Token missing, not `xoxb-`, not mode 600, or `user.slack_user_id` unset. |

## Revoking

**OAuth & Permissions → Revoke Tokens**, or delete the app. Then remove
`~/.scout-secrets/slack-bot-token`; runs go back to the connector DM.
