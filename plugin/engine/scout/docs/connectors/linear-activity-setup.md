# Linear own-activity pre-fetch — optional setup

Every briefing and consolidation reports what you did in Linear since the last
run: issues you created, moved, commented on or edited. Without setup, the
session asks the Linear connector for that itself, and in practice it often
forgets. With a Linear API key, the runner fetches it before the session
starts, so the session only has to report it.

Scout keeps working without the key; nothing else changes.

## Set it up

1. In Linear, open **Settings → Account → Security & access → Personal API
   keys** and create a key for Scout. If Linear offers a permission choice,
   give it **Read** only: the pre-fetch never writes.
2. Save it where Scout looks, readable only by you:

   ```bash
   mkdir -p ~/.scout-secrets && chmod 700 ~/.scout-secrets
   printf '%s\n' '<your key>' > ~/.scout-secrets/linear-api-key
   chmod 600 ~/.scout-secrets/linear-api-key
   ```

   Scout refuses a key file other users can read.
3. Check it: `scoutctl pre-session data morning-briefing`, then look at
   `.scout-cache/session-context.json` in your vault. `linear_own_activity_status`
   should say `fetched`.

## What it does

- One GraphQL request to `api.linear.app` before each scheduled run, with a
  10-second timeout: the issues assigned to you, created by you or commented on
  by you, updated since the last successful briefing or consolidation (at most
  7 days back). Only your own actions on them are kept.
- The result goes into `.scout-cache/session-context.json` as
  `linear_own_activity`. Before committing, the run calls
  `scoutctl action-items check-linear-delta`, which wants each of those issues
  under `### Your Linear Activity` in the day's 📋 Scout Digest.
- On any failure (no network, an expired key, an API error) the status says
  `failed: <why>` and the run falls back to asking the connector. The key is
  never written to a log or to the context file.

To stop, delete `~/.scout-secrets/linear-api-key`.
