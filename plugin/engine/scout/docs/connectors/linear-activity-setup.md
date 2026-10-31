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

- Paged GraphQL requests to `api.linear.app` before each scheduled briefing
  and consolidation (never dreaming or research), each with a 10-second
  timeout, for the issues updated since the last successful briefing or
  consolidation (at most 7 days back). The first pass covers the issues
  assigned to you, created by you, commented on by you or that you subscribe
  to, up to 500. A second, best-effort pass reads the workspace's 200 most
  recently updated issues, to catch a move or edit on an issue you have no
  other tie to. Only your own actions are kept. If the first pass, or an
  issue's history, runs past its limit, `linear_own_activity_truncated` is
  `true` and the run checks the connector for the rest.
- The result goes into `.scout-cache/session-context.json` as
  `linear_own_activity`. Before committing, the run calls
  `scoutctl action-items check-linear-delta`, which wants each of those issues
  under `### Your Linear Activity` in the day's 📋 Scout Digest, on a line that
  run wrote (the digest is shared by the day's runs).
- The same first pass also gives `linear_assigned_due`: each open issue
  assigned to you that has a due date, whoever touched it. Before committing,
  `scoutctl action-items check-tier-ceiling` wants a task line naming each one
  in a tier of the day's action items, no higher than its due date allows.
- On any failure (no network, an expired key, an API error) the status says
  `failed: <why>` and the run falls back to asking the connector. The key is
  never written to a log or to the context file.

To stop, delete `~/.scout-secrets/linear-api-key`.
