# Connected accounts: Sign in with Linear and Slack — design

Status: **draft; §14's four open questions decided** (Jordan, 2026-10-29, via the coordinator:
yes to all four, recorded in §4 and §14). Spec only: no code, no OAuth apps registered, no
secrets stored. Phase 0 waits for Jordan to create the Linear app.
Builds on: #532 (Linear own-activity pre-fetch, 🔴 project channel poll). Related: the Slack
mention trigger (`scout/triggers/sources/slack.py`), `docs/superpowers/specs/2026-09-08-app-managed-engine-design.md`.

## 1. Why

#532 pre-fetches the user's own Linear activity before each briefing and consolidation, so the
run reports it from data instead of being trusted to query Linear. The pre-fetch runs in the
engine, before the Claude session starts, and the claude.ai Linear connector can't be reached from
there: its login lives inside Claude. So #532 reads an optional personal API key from
`~/.scout-secrets/linear-api-key`. A user who wants the pre-fetch has to make a key in Linear,
write it to a file and `chmod 600` it.

Slack is in the same position. Scout's Slack token (`~/.scout-secrets/slack-search-token`, made by
hand) is used only by the mention trigger. #532's 🔴 poll pre-fetches which channels to read, but
the reading itself stays in-session through the claude.ai Slack connector.

Goal: a **Connected accounts** section in Scout.app Settings with **Sign in with Linear** and
**Sign in with Slack**. One click and a browser consent replace the key file. The engine keeps
the result working while the user sleeps, with or without the app running.

Out of scope: GitHub (already uses the user's `gh` login); the claude.ai connectors themselves
(this doesn't replace them, the in-session tools still use them); iOS.

## 2. Constraints

- **No paid or hosted anything** unless it can't be avoided. §3 shows it can be avoided for both
  providers.
- **No client secret ships in the app binary or the engine.** A secret in a public binary isn't
  secret.
- **Headless and dev-managed installs keep the hand-placed key file path** (#532's contract) and
  get the same sign-in from the CLI.
- **The app may be app-managed or dev-managed** (`managed_by` in `~/.local/state/scout/engine.json`).
  Both must work the same way.
- **Contract artifacts are generated** (the root `CLAUDE.md`): any new roster field goes through
  `connectors.yaml` → `connectors_snapshot` → byte copies.
- **No vault content on public surfaces**: examples use the `PROJ-1234` / `acme-co` stand-ins.

## 3. What the providers support (from their docs, checked 2026-10-29)

### 3.1 Linear: PKCE without a client secret — yes

Source: <https://linear.app/developers/oauth-2-0-authentication>,
<https://linear.app/developers/oauth-app-manifests>, <https://linear.app/developers/rate-limiting>,
<https://linear.app/docs/third-party-application-approvals>.

- **PKCE is supported.** `/oauth/authorize` takes `code_challenge` and
  `code_challenge_method` (`plain` or `S256`; we use `S256`). The token exchange at
  `https://api.linear.app/oauth/token` takes `code_verifier`, and for a PKCE request the docs
  list `client_secret` as **optional**. Refreshing a PKCE-issued token needs only `client_id`.
  So Linear needs **no server and no secret**.
- **Redirect URIs** are listed on the app (the manifest allows 1–32, each an absolute
  `http://` or `https://` URL). A custom scheme (`scout://`) is not allowed by the manifest schema.
  The docs' own examples use `http://localhost:3000/oauth/callback`. They say nothing about
  RFC 8252 loopback port wildcards. Third-party reports say the match is exact, port included, so
  we register **three fixed loopback ports** and use the first free one (§6.2). Phase 0 confirms it.
- **Lifetimes.** Access tokens last **24 hours** (`expires_in: 86399`). The code exchange also
  returns a **refresh token**. Every OAuth app was moved to refresh tokens on 2026-04-01, so a new
  app gets them. Each refresh returns a new access token **and a new refresh token** (rotation),
  with a **30-minute grace period** on reusing the old refresh token. The docs don't give a
  refresh-token lifetime.
- **Revocation:** `POST https://api.linear.app/oauth/revoke` with `token=…`
  (`token_type_hint=access_token|refresh_token`).
- **API calls** send `Authorization: Bearer <access_token>`. #532 sends a personal key bare
  (`Authorization: <key>`), so the header depends on the credential kind (§5.3).
- **Distribution:** an app is `private` (its own workspace only) or `public` (any workspace).
  Scout's has to be `public`. A paid workspace can require admin approval for third-party apps
  (§10).
- **Rate limits:** an OAuth app gets 5,000 requests/hour and 2,000,000 complexity points/hour
  per user, against 2,500 and 3,000,000 for an API key. #532's two paged passes are well inside
  that, but the complexity budget is lower, so Phase 1 measures one pre-fetch's complexity.

### 3.2 Slack: PKCE without a client secret — yes, with a rate-limit catch

Source: <https://docs.slack.dev/authentication/using-pkce>,
<https://docs.slack.dev/changelog/2026/03/30/pkce>,
<https://docs.slack.dev/authentication/using-token-rotation>,
<https://docs.slack.dev/distribution>,
<https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps>,
<https://docs.slack.dev/reference/methods/conversations.history>,
<https://docs.slack.dev/reference/methods/search.messages>.

- **PKCE has been self-service since 2026-03-30** (a toggle under *OAuth & Permissions*, or
  `oauth_config.pkce_enabled: true` in the manifest). Turning it on **marks the app a public
  client, and that's one-way** (only Slack support can undo it). With PKCE on, `oauth.v2.access`
  takes the `code_verifier` and **no `client_secret`**. `S256` is the only method.
- **Desktop redirects.** A custom scheme (`myApp://auth`) is always a desktop redirect. A
  `http://localhost:…` redirect counts as one **only once the app has PKCE on**; otherwise it's
  treated as a server redirect. Desktop redirects **cannot request bot scopes**, so Scout asks for
  **user-token scopes only**, which is what it wants anyway (read as the user).
- **Lifetimes.** Without token rotation, a Slack user token (`xoxp-`) doesn't expire. With
  rotation, it lasts **12 hours** (`expires_in: 43200`, prefix `xoxe.xoxp-`), and each refresh
  token works once. With PKCE on, **refresh tokens expire after 30 days**. A custom-scheme
  redirect **forces** rotation on; a web or localhost redirect gets it only if the app turns
  rotation on, and **rotation can't be turned off once on**. The docs disagree on whether a refresh
  needs the secret: the token-rotation page says `client_secret` is "still required", while the PKCE
  page says desktop-redirect tokens refresh without it. **Decision: use a localhost redirect and
  leave rotation off.** The token is then long-lived like today's hand-made one, nothing has to
  refresh, and there's no 30-day cliff. The engine still handles `expires_in` should Slack ever
  force rotation on (§7).
- **Revocation:** `auth.revoke` with the token. `auth.test` confirms it's gone.
- **Distribution:** installing outside the app's own workspace means *Activate Public
  Distribution* (the distribution page asks for SSL on redirect URLs; whether a PKCE localhost
  redirect is exempt is checked in Phase 0). Workspaces can require admin approval (§10).
- **The catch: the non-Marketplace rate limit.** Since 2025-05-29, a **new install of a
  commercially distributed app that isn't in the Slack Marketplace** is limited on
  **`conversations.history` and `conversations.replies` to 1 request/minute, at most 15 objects
  per call**. Marketplace apps and "internal customer-built" apps keep Tier 3 (50+/min, 1,000
  objects). A Raven-Scout app installed in a user's company workspace is an unlisted distributed
  app. The API Terms (<https://slack.com/terms-of-service/api>) define "Commercially Distribute"
  as an app where "users could pay fees for your product, service, or features". Scout charges
  nothing, so it probably doesn't count, but the rate-limit changelog doesn't point to that
  definition, so we still assume the cap applies. With it, reading five 🔴 channels takes at least five minutes,
  and more once one has over 15 new messages.
- **`search.messages` isn't in that change.** It's Tier 2 (20+/min, up to 100 results a
  page), takes `in:<channel>` modifiers, and needs only the user scope `search:read`. That is
  the scope the current hand-made token already has. Slack calls the method legacy and points to
  the Real-time Search API (`assistant.search.context`) instead, so it's a dependency to watch.

### 3.3 The answer in one line each

| | PKCE, no secret | Redirect | Server needed? | Refresh |
|---|---|---|---|---|
| Linear | Yes (secret optional on exchange and refresh) | `http://localhost:<fixed port>/…`, exact match; no custom scheme | **No** | 24 h access, rotating refresh (30 min grace) |
| Slack | Yes (one-way public-client toggle) | localhost (desktop redirect once PKCE is on) or custom scheme | **No** | none with rotation off (long-lived user token) |

**No hosted service is needed for either provider.** The one thing that could need more than free
infrastructure is lifting Slack's history rate cap through a Marketplace listing (§12, §13). That costs
no money but takes a review, plus a public privacy-policy and support page (GitHub Pages is
enough). It isn't needed if the 🔴 pre-fetch reads through search (§8.2).

## 4. Decisions

Rows marked **Decided** were ruled by Jordan on 2026-10-29 (via the coordinator). The rest are
the spec's recommendations, open for review on the PR.

| # | Decision | Recommendation / ruling |
|---|---|---|
| D1 | Where the OAuth flow runs | **In the engine** (`scoutctl accounts login`), with the app as a thin UI over it. Linear rules out a custom scheme, so the flow needs a loopback listener and the system browser either way (`ASWebAuthenticationSession` only catches custom-scheme or associated-domain callbacks). Putting it in the engine gives headless and dev-managed users the same sign-in, keeps tokens out of the app process entirely, and is testable in pytest. |
| D2 | Who refreshes | **The engine, lazily, just before it uses the token**, under a file lock. Not the app on a timer: scheduled runs fire at night with the app closed, and a Linear access token lasts only 24 h. §7. |
| D3 | Token storage | **Decided: file-only.** One file per provider in `~/.scout-secrets/`, mode 600, as the only store. No Keychain copy. §5.1 says why (this departs from the brief's "Keychain plus file"). |
| D4 | Contract | A `sign_in:` block on the existing `mcp:claude_ai_Linear` and `mcp:claude_ai_Slack` roster rows, projected into the snapshot. §5.4. |
| D5 | Precedence | **OAuth token first, hand-placed key file second.** A failed or expired sign-in falls back to the key file if there is one, then to #532's gate. |
| D6 | Slack read path for the 🔴 poll | **Decided: `search.messages` (`search:read`)**, subject to Phase 0's completeness check. No history scope and no rate cap. Add `channels:history` + `groups:history` only if Phase 0 shows search misses what the poll needs. §8.2. |
| D7 | Who owns the OAuth apps | A **Raven-Scout-owned** Linear app and Slack app (Jordan's call; §12 lists the steps). Client IDs are public and ship in the roster. A `client_id` override lets anyone use their own app instead. |
| D8 | `connectors.inputs.user_slack_id` | **Decided: auto-filled at Slack sign-in** (§8.2). |
| D9 | Accounts per provider | **Decided: one for now.** Switching workspace is disconnect + sign in. |
| D10 | A refresh whose outcome is lost | Write-ahead, a bounded retry with the old refresh token inside Linear's 30-minute grace, then `needs_sign_in` with one notice. §7.1. |

## 5. Storage and the app→engine contract

### 5.1 Why there's no Keychain copy

The brief asks for "Keychain plus the file the engine reads". This design keeps only the file:

1. **A rotating refresh token can live in one place only.** Linear's refresh token is single-use:
   once the engine refreshes, any copy in the Keychain is dead. Two stores means syncing them, and
   a crash between the two writes leaves one wrong.
2. **The engine must read it unattended.** An item Scout.app writes to the login Keychain has an
   ACL that trusts Scout.app. When the engine's Python reads it, macOS prompts, and nobody answers
   a prompt at 07:00. This setup already shows the problem: the release flow's notary Keychain
   profile can't be read while the screen is locked (the ledger's release rule). A scheduled run
   at 07:00 on a locked Mac would hit the same wall.
3. **It matches every other Scout secret.** Telegram, the Slack search token, the Agnes PAT and
   lease, and #532's Linear key all live in `~/.scout-secrets/` at mode 600, outside the vault,
   and `repo_sync` already refuses to push that directory.
4. **The app never holds a token** (D1), so the app has nothing of its own to keep in the Keychain.

The only consistent Keychain variant would be the Keychain as the sole store, with the engine
reading it through `/usr/bin/security`, and that breaks on a locked screen (point 2). **Jordan
ruled for file-only storage on 2026-10-29.**

### 5.2 Files

| File | Written by | Format |
|---|---|---|
| `~/.scout-secrets/linear-oauth.json` | `scoutctl accounts login linear`; rewritten on every refresh | `scout-oauth-token/1` (below) |
| `~/.scout-secrets/linear-api-key` | the user, by hand (#532, unchanged) | one line, the key |
| `~/.scout-secrets/slack-oauth.json` | `scoutctl accounts login slack` | `scout-oauth-token/1` |
| `~/.scout-secrets/slack-search-token` | the user, by hand (unchanged) | one line, the token |
| `~/.scout-secrets/<name>.lock` | the engine (`fcntl.flock`), empty | — |

`SCOUT_SECRETS_DIR` moves the whole directory (as in #532). The Slack trigger source hard-codes
`~/.scout-secrets` today; Phase 2 moves it onto the shared resolver.

Permissions: the directory is 0700 and each file 0600. A write goes to a temp file opened with
`O_CREAT|O_EXCL` at 0600 in the same directory, is `fsync`ed, then `os.replace`d over the old one.
`repo_lease.save` already writes a 0600 temp file and `os.replace`s it. This store adds
`O_EXCL` and the `fsync`s, which `repo_lease.py` doesn't do. A reader refuses a file that is group- or
world-readable (as #532 does), with the `chmod 600` hint.

`scout-oauth-token/1`:

```json
{
  "schema": "scout-oauth-token/1",
  "provider": "linear",
  "client_id": "<the app that issued it>",
  "token_type": "Bearer",
  "access_token": "…",
  "refresh_token": "…",
  "scopes": ["read"],
  "expires_at": "2026-10-30T07:00:00Z",
  "obtained_at": "2026-10-29T07:00:00Z",
  "refreshed_at": "2026-10-29T07:00:00Z",
  "account": {
    "user_id": "…", "name": "Alex Example", "email": "alex@example.com",
    "workspace_id": "…", "workspace_name": "Acme Co", "workspace_url_key": "acme-co"
  },
  "state": "ok",
  "needs_sign_in": null,
  "refresh_attempt": null,
  "last_error": null
}
```

- `client_id` is stored so a refresh uses the app that issued the token, even after an override
  changes.
- For Slack, `access_token` holds `authed_user.access_token`. `refresh_token` and `expires_at`
  are `null` while rotation is off. `account` comes from the `oauth.v2.access` response
  (`authed_user.id`, `team`) and `auth.test` (user name, team URL).
- `state` is one of:
  - `ok`;
  - `refresh_pending`: a refresh was started and its outcome isn't known yet (§7.1);
  - `needs_sign_in`: the token can't be used again, and only a new sign-in fixes it.
- `needs_sign_in`, set with that state, is `{"since": "...", "reason": "refresh_refused" |
  "grace_exhausted" | "revoked"}`.
  - The engine then blanks `access_token` and `refresh_token` but keeps `account`, so the UI can
    still say who to sign back in as.
  - Whether the notice went out isn't kept here: the notifier's state records it (§7.1 (c)).
- `refresh_attempt`, set with `refresh_pending`, is `{"started_at": "...", "attempts": N,
  "next_at": "..."}`.
  - It never holds a token.
  - The old refresh token stays in `refresh_token` until a new pair replaces it.
- `last_error` is `{"at": "...", "code": "invalid_grant", "message": "<redacted, ≤200 chars>"}`.
  It never holds a token.

### 5.3 One resolver

`scout/accounts/credentials.py` exposes `credential(provider) -> Credential | None`, where
`Credential` is `kind` (`oauth` | `api_key`), `token`, `header` (`Bearer <t>` for OAuth, the
bare key for a Linear personal key) and `source` (the file path). It applies D5 and refreshes
when needed (§7). It takes the endpoints and file names from the packaged seed only, never from
the merged registry (§5.4). `linear_activity.read_key()` becomes a call to it, so #532's `NO_KEY` and
`FAILED` statuses keep their meaning. The Slack trigger source and the 🔴 pre-fetch use it too.

### 5.4 Declared in the connector roster

`connectors.yaml` gains an optional `sign_in:` block on the two existing rows. They are the same
service reached two ways (in-session MCP, engine pre-fetch), so there are no new rows, and the
connector-health rail doesn't change:

```yaml
  mcp:claude_ai_Linear:
    # … existing fields unchanged …
    sign_in:
      provider: linear
      used_for: "Your own Linear activity, pre-fetched before briefings and consolidations"
      credentials:                       # in precedence order (D5)
        - {file: linear-oauth.json, kind: oauth, written_by: "scoutctl accounts login linear"}
        - {file: linear-api-key,    kind: api_key, written_by: hand}
      oauth:
        authorize_url: https://linear.app/oauth/authorize
        token_url: https://api.linear.app/oauth/token
        revoke_url: https://api.linear.app/oauth/revoke
        client_id: "<from Phase 0>"      # public; SCOUT_LINEAR_CLIENT_ID overrides
        scopes: [read]
        redirect: {host: localhost, ports: [<p1>, <p2>, <p3>], path: /oauth/linear}
```

The Slack row is the same shape: `provider: slack`, credentials `slack-oauth.json` then
`slack-search-token`, `authorize_url: https://slack.com/oauth/v2/authorize` with `user_scope`
(not `scope`), `token_url: https://slack.com/api/oauth.v2.access`, `revoke_url:
https://slack.com/api/auth.revoke`.

- `_build_connector` ignores unknown keys today, so the loader takes the block without a schema
  bump. A `SignIn` dataclass and a validation test come with it.
- `connectors_snapshot` projects `sign_in` as `{provider, used_for, files: [...]}` with no
  endpoints and no client ID: the app needs only the rows to show and the file names to point at.
- The snapshot stays `schema_version: 1`. The field is additive, and the app's `SnapshotFile`
  `Decodable` ignores unknown keys.
- Regenerate it with `--no-also-write-app-fixture` and `cp` it over both app copies in the same
  commit (`contract.yml` byte-compares them). Never hand-edit it.
- Custom (vault-local) connectors can't declare `sign_in` in v1, because the endpoints would be
  user-supplied URLs the engine posts tokens to.

**`sign_in` comes from the packaged roster only.** `connectors.load_registry()` deep-merges
`<vault>/.scout-state/connectors.local.yaml` over the shipped rows (`_deep_merge_dict`). The
vault is a directory scheduled sessions write to. If the overlay could reach `sign_in`, anything
that steered a session into writing that file could set `token_url` or `revoke_url` to its own
host. The engine would then POST the refresh token there, or the `credentials[].file` entries
could be pointed elsewhere. So:
- **The resolver and every `scoutctl accounts` command read `sign_in` from the packaged
  `connectors.yaml` only.** That is the file next to `scout/connectors.py`, through a separate
  `load_sign_in_seed()` that never looks at the overlay or the custom roster. They never take it
  from the merged registry. `sign_in` is also dropped from the merged `Connector`, so nothing can
  read it from there by mistake.
- **An overlay that touches `sign_in` is ignored, with a warning.** That covers a `sign_in` key
  on any row, shipped or new, and any key under it. `bootstrap doctor` and `scoutctl health`
  report the yellow finding `connectors.overlay_sign_in_ignored`, which names the overlay path and
  the row, never a value. The rest of the overlay still applies.
- **An overlay can't add or rename a sign-in credential file either.** A row whose overlay names a
  file that a `sign_in` block in the seed declares (for example `linear-oauth.json` under some
  other key) is ignored the same way.
- **The snapshot is projected from the seed.** `connectors_snapshot` already reads only the seed,
  and it keeps taking `sign_in` from there.
- **Only `SCOUT_<PROVIDER>_CLIENT_ID` can override a value.** `SCOUT_LINEAR_CLIENT_ID` and
  `SCOUT_SLACK_CLIENT_ID` override the client ID; nothing else does. The endpoints, scopes,
  redirect ports and file names have no override.
  - The env var is a launchd or shell setting, not a vault file, so a session can't set it for a
    later scheduled run.
  - The tests use `SCOUT_LINEAR_API_URL`-style `file://` stand-ins, which only redirect the data
    API (as #532 does), never the token endpoints. The token endpoint stand-in in tests is a
    monkeypatched transport, not a URL setting.

## 6. The engine side: `scoutctl accounts`

### 6.1 Commands

| Command | Does | Network |
|---|---|---|
| `accounts status [--json] [--check]` | Every `sign_in` provider: `source` (`oauth`/`api_key`/none), `state`, `needs_sign_in`, `refresh_attempt`, `fallback`, `account`, `expires_at`, `last_error`, `notice`, the secrets dir in use (§7.1 (d)). `--check` adds one live call (Linear `viewer { id }`, Slack `auth.test`), and refreshes if due or pending. Exit 1 if any provider is `needs_sign_in`. | only with `--check` |
| `accounts login <provider> [--json] [--no-browser] [--timeout 300]` | The PKCE flow (§6.2). Writes the token file. Prints the account. For Slack it also fills `user_slack_id` (§8.2). `--set-user-id` (Slack) replaces a different `user_slack_id` from the current token, with no browser. | yes |
| `accounts logout <provider> [--json] [--keep-remote]` | Revokes, then deletes the file (§8.4). | yes, unless `--keep-remote` |
| `accounts refresh <provider> [--resume]` | Forces a refresh (tests and debugging). `--resume` is the detached retrier of §7.1 (b): it retries a pending refresh until its deadline. | yes |

`--json` output is one object, the shape the app decodes (`BudgetSettingsService.invoke` is the
pattern), and never contains a token. `login --json` also prints one `{"event":"waiting","port":N}`
line first, so the app can show "Waiting for your browser…".

### 6.2 The login flow

1. Generate a `code_verifier` (43–128 chars from `secrets.token_urlsafe`), its S256
   `code_challenge`, and a `state` (32 random bytes). They stay in memory and are never written.
2. Bind an HTTP listener on loopback, IPv4 `127.0.0.1` and IPv6 `::1`, at the first free port in
   `redirect.ports`. If all three are taken, exit with `ports_busy` and the ports listed.
3. Open `authorize_url?client_id=…&redirect_uri=http://localhost:<port><path>&response_type=code&scope=…&state=…&code_challenge=…&code_challenge_method=S256`
   with macOS `open`. Linear adds `prompt=consent`, so the user can pick a workspace. With
   `--no-browser` (SSH), print the URL instead.
4. Serve exactly one request on `<path>`:
   - A wrong or missing `state` returns 400 and the listener keeps waiting, until the timeout.
   - An `error=access_denied` is `cancelled`; the other provider errors are reported by code.
   - A good request gets a small "You can close this tab" page, with no token in it.
5. Exchange the code at `token_url` (form-encoded): `grant_type=authorization_code`, `code`,
   `redirect_uri`, `client_id`, `code_verifier`. Never a secret.
6. Fetch the account (Linear `viewer { id name email } organization { id name urlKey }`; Slack
   `auth.test`). Write the file (§5.2) under the lock. Close the listener.
7. Exit codes: 0 connected; distinct codes for `cancelled`, `timeout`, `ports_busy`,
   `exchange_failed`, `insecure_secrets_dir`.

SIGTERM (the app's Cancel) closes the listener and writes nothing.

## 7. Refresh and expiry

**The engine refreshes, lazily, just before it uses the token** (D2):

- In `credential(provider)`: if `expires_at` is less than 10 minutes away (or past), take
  `flock` on `<file>.lock`, **re-read the file** (another process may have just refreshed it), and
  if it's still due, refresh as §7.1 says.
  - The lock waits at most 30 s. If it can't be taken, the fetch reports `FAILED` "credential
    busy" and the run carries on through the gate.
- If a call with a token that isn't due gets a 401, refresh once and retry once. A second 401
  means `needs_sign_in` (`reason: revoked`).
- A network error or 5xx on an API call (not the refresh) leaves the file alone and reports
  `FAILED` for this run only.

### 7.1 When a refresh's outcome is lost (Linear)

**The danger.** Linear rotates on every refresh. Once the server has issued a new pair, the old
refresh token works for only **30 more minutes** (the reuse grace). If the engine never persists
the new pair, the account is lost for good after those 30 minutes. That happens on:
- a crash between the response and the write;
- a disk error on the write;
- a network drop after the server rotated but before the response arrived.

Every step below runs under the provider's `flock`.

**(a) Write-ahead.**
1. **Journal before the POST.** Write the file with `state: refresh_pending` and
   `refresh_attempt.started_at = now`, keeping a `started_at` that is already there. Use the same
   atomic write as §5.2: temp file at 0600, `fsync` it, `os.replace`, then `fsync` the directory.
   The old `refresh_token` stays in the file. The journal tells any later process that the server
   may already have rotated, and when the grace window began. The server rotated after
   `started_at`, so counting the deadline from it is the safe side.
2. POST `grant_type=refresh_token&refresh_token=<old>&client_id=…` (form-encoded, 10 s timeout, no
   secret).
3. On a 200: **persist the new pair first**, with the same atomic write (`state: ok`,
   `refresh_attempt: null`, the new `expires_at`, `refreshed_at`). Only once the rename and the
   directory `fsync` have returned does the caller get the new access token.
4. If that write fails (`OSError`: disk full, EIO, permissions), drop the in-memory pair unused.
   The file still holds the old token and the journal, so the outcome counts as unknown (b).
   Never use a pair that isn't on disk: one copy, one truth.

**(b) A failed or unknown outcome: retry with the old token inside the grace window.**
- **Counts as unknown:** a timeout or connection reset after the request may have reached Linear,
  a 5xx, a 429, or a failed write (a4). Linear may or may not have rotated. Either way the old
  refresh token is still good inside the grace window, so the engine retries **with the old
  refresh token**, from the file.
- **The budget:** `deadline = refresh_attempt.started_at + 25 min`, which leaves 5 minutes of
  margin under Linear's 30.
- **In-process backoff, bounded:** retry after 5 s, 20 s, then 60 s, a 429's `Retry-After` taking
  precedence. No more than 4 attempts or about 2 minutes, so the pre-fetch never holds a run up.
  Each attempt is steps 2–4 again, and `attempts` and `next_at` are journaled before each POST.
- **If still unknown, keep retrying out of process.** The run reports `FAILED` "Linear refresh
  pending" and goes on through the gate. Before returning, it starts one detached retrier,
  `scoutctl accounts refresh linear --resume`, which outlives the run (isolation below).
  - The retrier tries again at +1, +2, +4 and +8 minutes, and stops at `deadline`.
  - It takes the `flock` for each attempt and re-reads the file first, so it stops the moment
    another process has succeeded.
  - Only one retrier at a time: a `<file>.retrier.pid` that is alive means none is started.
- **The retrier is isolated from the run that spawned it.** The lock reaper (#468,
  `_scout_reap_holder` in `scout-lock.sh.tmpl`) kills a wedged run's process group **and every
  descendant it finds by walking `ppid`** (`_scout_session_pids`). #476's progress check counts
  those same descendants' CPU and process list. Neither may take the retrier down mid-refresh, and
  the retrier must not keep a dead run looking alive.
  1. **Its own session, and not a descendant: a double fork.** `setsid()` alone takes the
     retrier out of the run's group, but leaves it a descendant (its `ppid` is the spawner) until
     the spawner exits. So:
     - The spawner runs `subprocess.Popen([scoutctl, "accounts", "refresh", "linear", "--resume",
       "--detach"], start_new_session=True, ...)`, the `setsid` pattern `schedule_tick.py`,
       `heartbeat.py` and `self_update_run.py` already use.
     - With `--detach`, that intermediate child `os.fork()`s once more and exits at once. The
       grandchild, the real retrier, runs on.
     - The spawner `wait()`s for the intermediate, which takes milliseconds and leaves no zombie.
       So **by the time the spawn call returns, the retrier's parent is launchd (pid 1)**. macOS
       has no subreaper, so an orphan always goes to launchd, and the reaper's `ppid` walk from the
       run never reaches it.
     - The grandchild's session and pgid are the intermediate's (the `setsid` happened there) and
       never the run's, so a `kill -- -<run pgid>` misses it too.
     - Spawn options: `stdin=DEVNULL`; stdout and stderr go to its own log,
       `.scout-logs/accounts-retrier.log` (redacted), never the run's pipes, so a runner waiting
       for EOF on its log isn't held open. `close_fds=True`, so it inherits no lock or pipe fd from
       the run.
  2. **It never takes or holds the vault session lock** (`.scout-logs/.scout-session.lock`).
     - It doesn't open the file, doesn't write its pid there, and doesn't check it. The only lock
       it takes is the per-provider token `flock` (`<file>.lock`), for one attempt at a time.
     - It writes nothing the reaper or #476's progress check reads (no heartbeat or progress
       files), so it is never counted as the run's work.
     - The spawn also strips the run's lock and slot environment variables (`SCOUT_MODE`, the
       runner's lock path), so nothing downstream can mistake it for the run.
  3. **A hard wall-clock exit.**
     - On start, it reads `deadline` from the journal. If `deadline` has already passed, it
       records `grace_exhausted` (c) and exits.
     - Otherwise it arms a watchdog: `signal.alarm` for `deadline − now + 15 s`, whose handler calls
       `os._exit(3)`. A hung DNS lookup or socket therefore can't keep it alive, and no orphaned
       `--resume` lingers.
     - It never starts an attempt that could end after `deadline`: the 10 s request timeout must
       fit before it.
     - Any process that later finds a pid file whose process is dead removes it.
     - The watchdog firing mid-write is safe: the write is a temp file plus `os.replace`, so the
       file is either the old pair or the new one.
- **Any process that finds `refresh_pending`** (a run, `accounts status --check`, the retrier)
  before `deadline` makes one attempt itself first.
- **Definite answers skip the retries:** a 400 `invalid_grant` goes straight to (c). A
  `401 invalid_client` is a configuration error (a wrong or overridden `client_id`): no retry,
  `last_error` records it, and the state stays `ok`, because a sign-in with the same client ID
  wouldn't help.

**(c) Grace window exhausted, or `invalid_grant`.**
- Set `state: needs_sign_in`, with `needs_sign_in.reason`:
  - `grace_exhausted`: `deadline` passed with no success;
  - `refresh_refused`: a definite `invalid_grant`;
  - `revoked`: the 401 path in §7.
- `needs_sign_in.since` is now. Blank both tokens, keep `account`, record `last_error`.
- `credential()` then falls through D5: to the hand-placed `linear-api-key` if there is one, else
  `NO_KEY`. So #532's gate takes over, and the run never fails.
- **One notice.** Run health gets the yellow finding `accounts.linear.needs_sign_in`. Today the
  notifier (run-health spec §4.4) pushes only red findings, with a reminder every 24 h. This adds a
  **`push: once`** class:
  - The notifier pushes the finding once, the first time it sees it, on Scout's normal channels in
    their usual order: desktop, Telegram, `notify.command`.
  - The text is the roster row's `first_fix`: "Linear sign-in expired. Sign in again: Scout.app →
    Settings → Connected accounts, or `scoutctl accounts login linear`."
  - It sends no 24 h reminders, and it doesn't make the overall severity red.
  - The notifier's state keeps the id until the finding goes away, which happens on the next
    sign-in. A later episode notifies again.
  - Like the other pushes, a push no channel delivered is retried on the next call.
  - **Coalescing.** The notifier sends at most one red push per call, listing every new or
    overdue red (run-health §13.5).
    - If that call is sending a red push anyway, a due `push: once` finding rides along as a
      separate "Also:" line at the end, and is marked sent with it.
    - Otherwise it goes out on its own, as one push. Several due `push: once` findings in one call
      (Linear and Slack together) share that one push.
    - It never delays a red push, and never makes the red push's headline its own.
  - The session wrap lists the finding only in the run that made the transition, not in every
    later wrap.

**(d) How it shows.**
- `scoutctl accounts status` (text) prints, per provider:
  - `refresh_pending`: "Linear: refreshing. Linear didn't answer (attempt 3); retrying until 07:27."
  - `needs_sign_in`, first line: "Linear: needs sign-in since 2026-10-30 07:27 (Linear's refresh
    grace ran out). Was: Alex Example · Acme Co."
  - `needs_sign_in`, second line: "Meanwhile: using `~/.scout-secrets/linear-api-key`", or
    "Meanwhile: briefings ask Linear in-session."
  - `needs_sign_in`, last line: "Fix: `scoutctl accounts login linear`."
- `--json` gives `state`, `needs_sign_in` (`since`, `reason`), `refresh_attempt` (`started_at`,
  `attempts`, `next_at`, `deadline`), `fallback` (`api_key` | `gate`), `account`, `last_error`,
  and `notice` (`sent_at` | `null`, from the notifier's state). It never holds a token.
- Exit 0 when every provider is `ok` or not connected. Exit 1 when any provider is
  `needs_sign_in`.
- Settings: the rows in §9 (`refresh_pending` → "Refreshing…", `needs_sign_in` → "Sign in again"
  with the reason and the fallback).

**(e) Slack: simpler, since rotation is off.**
- With rotation off, there is no refresh, no rotation and no grace window, so (a)–(b) don't apply.
- **Definite answers mean `needs_sign_in` at once.** On `invalid_auth`, `token_revoked`,
  `account_inactive` or `token_expired`, from an API call or from the `auth.test` that confirms
  it, the engine sets `needs_sign_in` (`reason: revoked`, or `refresh_refused` for
  `token_expired`) and blanks the token. There's no retry: the answer is definite.
- **Network errors, 5xx and `ratelimited`** are `FAILED` for this run only; the token is kept.
- **Fallback:** `slack-search-token` if there is one, else the in-session poll through the
  claude.ai connector, as today.
- **The notice:** `accounts.slack.needs_sign_in`, with the same `push: once` behaviour.
- **If rotation is ever forced on** (the custom-scheme fallback in §12, which also moves Slack's
  sign-in into the app and ends headless Slack sign-in), (a) and (c) apply as written. (b) does not: Slack's refresh tokens are single-use and Slack documents no grace window.
  So a lost refresh outcome means `needs_sign_in` (`grace_exhausted`) as soon as the old token is
  refused. Phase 0 confirms Slack's behaviour before that path is built.

Why not the app on a timer: the app is optional, may be closed, and isn't a login item by default
(`SMAppService` is off unless the user turns it on). Linear's token lasts 24 h, so a weekend with
the app closed would expire every scheduled run. And two refreshers racing for a single-use
refresh token is exactly the failure the lock exists to stop. With one refresher, the engine, the
app never needs the token.

A Mac left off for weeks: Linear doesn't document a refresh-token lifetime. If it has one, the
first run after the break gets `invalid_grant` and goes `needs_sign_in` (§7.1 (c)), and the gate
covers it until the user signs in again (§9). With rotation off, Slack has no expiry.

A Mac that sleeps mid-retry: the detached retrier sleeps with it. If the Mac wakes after
`deadline`, the next attempt is never made, and the next process sets `grace_exhausted`. That's
the right answer: the old token is dead by then.

The app learns about expiry two ways: the Settings section runs `accounts status --json` when it
opens, and run health (below) shows up through the doctor refresh the app already runs every
600 s (`EngineHealthService`).

Run health: `scoutctl health` and `bootstrap doctor` gain the yellow finding
`accounts.<provider>.needs_sign_in`, which pushes once (§7.1 (c)).
- `refresh_pending` is not a finding: it resolves within 25 minutes, one way or the other.
- `FAILED` for one run is not a finding either: #532's gate already records it in the digest.

## 8. Behaviour

### 8.1 Linear (Phase 1)

`linear_activity.fetch_own_activity` takes its credential from the resolver (§5.3) and sends the
right header. Nothing else in #532 changes: `FETCHED` / `NO_KEY` / `FAILED` and
`check-linear-delta` work as they do now. `NO_KEY` now means "no OAuth file and no key file". If
the user hasn't set it, the signed-in `workspace_url_key` also fills `SCOUT_LINEAR_WORKSPACE` for
deep links and offers to fill the app's Linear workspace setting.

### 8.2 Slack (Phase 2)

- **Search.** The mention trigger and anything else that searches use `credential("slack")`.
  That is `slack-oauth.json` if it has `search:read`, else `slack-search-token`. **The current
  hand-made token keeps working, untouched.** (Answer to Q3: yes, search can keep using it.)
- **The 🔴 poll.** #532 pre-fetches *which* channels (`red_project_channels`, each `{id, name}`).
  Phase 2 also pre-fetches *what they said*: one `search.messages` call per channel,
  `query="in:<#C…> after:<date>"`, `sort=timestamp`, `count=100`, paged, for the window since the
  last run. The results go into `session-context.json` as `red_project_messages`, and
  `check-red-polls` accepts a pre-fetched channel the way `check-linear-delta` accepts the
  pre-fetched activity. Search is Tier 2 and isn't under the non-Marketplace cap, and it needs
  no scope beyond `search:read`.
  - **Cache-only and pruned** (Slack API Terms: keep use, processing and retention of API data
    to the minimum necessary).
    - `red_project_messages` exists only in `.scout-cache/session-context.json`. That file is
      gitignored, outside the KB, and never synced: `repo_sync` already keeps `.scout-cache/` on
      the machine.
    - It holds only the run's window, never more than #532's `MAX_LOOKBACK` (7 days), and only
      the fields the poll reads: `channel`, `ts`, `user`, `text`, `thread_ts`, `permalink`.
    - Every pre-fetch overwrites it.
    - When the session ends, the runner removes the field (`scoutctl pre-session prune`, called
      from `run-outcome.sh`). A file left by a crashed run is pruned when the next run starts.
    - The session writes its own summary into the daily file and KB, as the 🔴 phase does today.
      Messages are never copied into the KB verbatim, and the eval fixtures for it use the
      `acme-co` stand-ins.
- **Phase 0 checks whether search is enough**, using the existing token on Jordan's machine (read-only, nothing posted):
  - Do thread replies, bot posts and edited messages come back for a channel?
  - Does `in:` take a channel ID (`in:<#C…>`)? The method docs only show `in:channel_name`.
  - Does `after:` behave as it does in the Slack UI? It takes a date, not a time, so a run that
    fires more than once a day filters the results by `ts` against its own window.
  - Is the index lag short enough for a 07:00 run?
  If not, Phase 2 adds the user scopes `channels:history` (public 🔴 channels) and `groups:history`
  (private ones) and reads with `conversations.history`. That path takes the 1/min, 15-object cap
  into account: one channel per minute, and the check reports `truncated` when a channel had more
  messages than were fetched.
- **Minimal Slack scopes:** `search:read` (user scope). If needed: `channels:history`,
  `groups:history`. Never bot scopes, which desktop redirects can't request anyway. Never
  `im:history`/`mpim:history`, since the 🔴 poll reads project channels, not DMs.
- **`user_slack_id` is auto-filled at sign-in** (D8, decided). The mention trigger needs it.
  After a successful `accounts login slack`, the engine puts `authed_user.id` into
  `connectors.inputs.user_slack_id` in the bound vault's `scout-config.yaml` (the vault in
  `engine.json`). It writes through `bootstrap.write_connector_config`, which keeps the file's
  comments, under the vault lock.
  - **A short lock wait, never the 300 s one.** `acquire_lock_with_wait` defaults to polling for
    300 s, and a scheduled run holds `.scout-session.lock` for the whole run. So `login slack`
    waits at most **5 s** for the lock (`timeout_s=5, poll_s=1`).
    - If the lock is still busy, the token is already saved (the token file needs no vault lock),
      so sign-in has succeeded. Only the config write waits.
    - `login` reports `user_slack_id_pending`, and the token file records
      `pending_user_slack_id: "U0BBB…"` (an ID, not a secret).
    - The next `accounts status`, `accounts login` or pre-fetch that can take the lock within 5 s
      applies it under the same rules as below, then clears the field.
    - Settings shows "Connected", plus "Your Slack ID will be saved after the current run."
  - **Empty or missing:** written. The `login` output says so.
  - **Already the same ID:** nothing changes.
  - **A different ID** (another account, or a hand-typed one): **not overwritten.** `login`
    reports `user_slack_id_mismatch` with both IDs. `accounts status` keeps reporting it, and the
    Settings row shows "Your Slack ID in scout-config.yaml is U0AAA…; you signed in as U0BBB…"
    with a **Use U0BBB…** button, which runs `accounts login slack --set-user-id`, with no browser
    when a valid token is already there.
  - No vault configured (a bare headless engine): skipped, with a note.
  - `logout` never clears it.

### 8.3 With no app

Everything above runs from the CLI. `scoutctl accounts login linear` opens the browser on the
Mac. `--no-browser` prints the URL for an SSH session; the user opens it on a machine whose
browser can reach the Mac's loopback port, usually through `ssh -L <port>:localhost:<port>`. The
README section says so. The hand-placed key files keep working exactly as in #532 (D5), so a
headless box that never runs a browser loses nothing.

### 8.4 Sign-out and revocation

`accounts logout <provider>`:
1. Under the lock, POST the revoke endpoint. Linear: `token=<refresh_token>` with
   `token_type_hint=refresh_token`. The docs don't say whether that also ends the access token
   and the grant (Phase 0 checks). If it doesn't, revoke the access token too. Slack: `auth.revoke` with the user token. The timeout is
   10 s.
2. **Delete the file whatever the revoke returned.** If the revoke failed (offline, 4xx), say so
   and point at the provider's own place to remove the app: Linear's *Settings → Account →
   Security & access → Authorized applications*, Slack's app management page. The exact paths are
   confirmed in Phase 0.
3. Never touch the hand-placed key file. If one exists, the output says "Still using the key file
   at …".

`--keep-remote` skips step 1 (used when switching workspaces, or by the app when it removes a
stale entry).

## 9. Settings UI (Scout.app)

The new section is **Connected accounts**, added to `SettingsView.swift` with the existing
`section(label:)` helper, between *Claude Code* and *Budget*. It's its own file
(`ConnectedAccountsSettingsSection.swift`, like `EngineSettingsSection`), with one
`SettingsCard` row per roster row that has `sign_in`, built from the bundled snapshot.

Each row: the provider name, the `used_for` line as help text, a status chip in the
`UpdateTrackRow` capsule style, and buttons. The chip colours use `DS.Status`.

| State (from `accounts status --json`) | Chip | Detail line | Buttons |
|---|---|---|---|
| none | "Not connected" (p3) | `used_for`. "Without it, the briefing asks Linear in-session." | **Sign in with Linear** |
| connecting | "Waiting for browser…" (p3) | "Finish signing in in your browser." | Cancel |
| ok (oauth) | "Connected" (ok) | "As Alex Example · Acme Co" | Check now · Disconnect |
| ok (api_key) | "Using key file" (ok) | "`~/.scout-secrets/linear-api-key`" | Sign in with Linear (replaces the key; the file is kept as the fallback) |
| refresh_pending | "Refreshing…" (p3) | "Linear didn't answer a token refresh. Retrying until 07:27." | Check now |
| needs_sign_in | "Sign in again" (warn) | The reason, in plain words: "Linear refused Scout's sign-in on <date>" (`refresh_refused`), "Scout couldn't finish refreshing its Linear sign-in on <date>" (`grace_exhausted`), "Access was removed in Linear on <date>" (`revoked`). Then the fallback: "Meanwhile: using your key file" or "Meanwhile: briefings ask Linear in-session." Then "Was: Alex Example · Acme Co". | **Sign in again** · Disconnect |
| user_slack_id_mismatch (Slack) | "Connected" (ok) + warn note | "Your Slack ID in scout-config.yaml is U0AAA…; you signed in as U0BBB…" | Use U0BBB… |
| error (last check failed, token kept) | "Couldn't check" (warn) | The redacted `last_error.message` | Check now |
| insecure dir / file | "Fix permissions" (err) | "`chmod 600 …`" with a copy button | Check now |
| engine too old | "Needs engine X.Y" (p3) | The `/scout-update` hand-off copy, as in `EngineSettingsModel` | — |

- **Sign in** runs `scoutctl accounts login <provider> --json` through the scoutctl the app
  already resolves: the shim for app-managed engines, the located one for external engines.
  - It needs a cancellable variant of `ProcessRunner`. The current `SystemProcessRunner` waits for
    exit, so Cancel sends SIGTERM through the new variant.
  - The chip shows "Waiting for browser…" from the first JSON line until the process exits. The
    timeout is 5 minutes.
- **Disconnect** runs `accounts logout`, after a confirmation that names the account.
- **Check now** runs `accounts status --check --json`. Opening Settings runs `status --json`,
  which makes no network call.
- **No row edits a file itself.** The app never reads or writes `~/.scout-secrets/`, so the
  engine stays the only writer, whatever `managed_by` says. The app doesn't modify the engine for
  this either: it runs a command, so writing a secret is allowed for a dev-managed engine too.
- **Engine floor:** the app checks the engine version, as it does for `SCScoutPluginFloor`.
  - Below the first version with `accounts`, the row shows "Needs engine X.Y".
  - For a dev-managed engine, that comes with the `/scout-update` hand-off, never an app-driven
    upgrade.
- **Secrets dir:** the app passes no `SCOUT_SECRETS_DIR`. `EnvironmentInjectingRunner` injects
  only `SCOUT_DATA_DIR`, so the app and launchd agree on `~/.scout-secrets`. `status` prints the
  dir it used, and the row shows it under a disclosure.
- **Badge:** an `accounts.*` finding in the doctor report puts a warn dot on the Settings menu
  item, the same signal the engine-health row uses.

Error copy, by exit code:
- `cancelled`: "Sign-in cancelled."
- `timeout`: "No answer from the browser in 5 minutes."
- `ports_busy`: "Ports <p1>–<p3> are in use; quit the app using them and try again."
- `admin_approval_required` (Linear's or Slack's approval page): "Your workspace admin has to
  approve Scout first. Linear/Slack sent them the request."
- `exchange_failed`: "Linear refused the sign-in (<code>)."

## 10. Failure modes (every one ends at #532's gate)

| Failure | What the engine does | What the run does |
|---|---|---|
| No OAuth file, no key file | `NO_KEY` | The gate asks the session (unchanged) |
| Access token due | Refresh under the lock, then fetch | Pre-fetched |
| Refresh refused (`invalid_grant`) | `needs_sign_in` (`refresh_refused`), tokens blanked; falls to the key file if there is one | Key-file fetch or the gate; one `push: once` notice |
| 401 on a token that isn't due (revoked in the provider's UI) | One refresh + retry, then `needs_sign_in` (`revoked`) | Same as above |
| Network error / 5xx / timeout on an API call | `FAILED`; file untouched | The gate ("Linear unavailable: …") |
| Lock busy for 30 s (another run is refreshing) | `FAILED` "credential busy" | The gate |
| Refresh outcome unknown (timeout, reset, 5xx or 429 on the refresh; crash or disk error before the new pair is on disk) | Journal kept (`refresh_pending`); in-process retries with the old refresh token (≤ 4, about 2 min); then a detached retrier until `started_at + 25 min` (§7.1) | `FAILED` "refresh pending" → the gate for this run; the next run is normally back to `ok` |
| Grace window exhausted | `needs_sign_in` (`grace_exhausted`), tokens blanked | Key file or the gate; one notice |
| Slack `invalid_auth` / `token_revoked` / `account_inactive` | `needs_sign_in` (`revoked`) at once, no retry | `slack-search-token` or the in-session poll; one notice |
| File or dir mode too open | Refuse, with the `chmod` hint (as #532) | The gate; the UI shows "Fix permissions" |
| A vault overlay (`connectors.local.yaml`) touches `sign_in` | Ignored; the seed's endpoints, client ID and files are used; doctor warns `connectors.overlay_sign_in_ignored` | Unaffected |
| Reaper kills a wedged run while the retrier is pending | Unaffected: double-forked, `ppid` 1, own session; outside the reaper's group and descendant walk (§7.1 (b)) | The retrier finishes or hits its deadline |
| `login slack` while a run holds the vault lock | Token saved; `user_slack_id_pending` after a 5 s wait, applied by the next `status`/`login` | Unaffected |
| Workspace needs admin approval | `login` exits `admin_approval_required` | n/a (no token yet) |
| Different workspace than the claude.ai connector | Works; the row shows the workspace | The pre-fetch reads the signed-in workspace; the phase text says which |
| Slack history cap (if D6 falls back to `conversations.history`) | One channel a minute; `truncated` when it got 15 and more were left | The check asks the session to read past the cut |
| `search.messages` retired by Slack | Search returns `method_deprecated`/`unknown_method` → `FAILED` | The gate: the poll stays in-session, as today |
| Engine older than the app expects | The app shows "Needs engine X.Y" | Key-file behaviour unchanged |

The pre-fetch never blocks or fails a run (`fetch_own_activity` "never raises" today, and stays
that way).

## 11. Privacy

- **Tokens never cross a process boundary.**
  - They're never on a command line or in an environment variable, and never in `--json` output.
  - They never reach the app or the session (`session-context.json` gets the fetched data, never
    the credential).
  - They never go into logs, `last_error`, health records, connector-health or transcripts.
- `scout/redact.py` gains the new shapes:
  - Slack's rotating `xoxe.xoxp-…` and refresh `xoxe-…` (today's `xox[abposr]-` misses `xoxe-`).
  - Linear's `lin_api_…` / `lin_oauth_…` (prefixes confirmed in Phase 0).
  - Every error string from `scout/accounts/` passes `redact_secrets(text, token)`.
- The authorize URL carries `state` and `code_challenge`, which are useless without the in-memory
  verifier. The app still doesn't log it.
- `~/.scout-secrets/` is outside the vault and never synced; `repo_sync` already refuses it.
- The `account` block (name, email, workspace) is personal data. It stays in the secrets file and
  in local `status` output only, and never goes into run health that's sent over Slack or Telegram.
- Channel messages fetched for the 🔴 poll stay in `.scout-cache` only, and are pruned after each
  run (§8.2, per Slack's API Terms on retention).
- The endpoints that see a token (token, refresh, revoke) come from the packaged roster only. No
  vault file can redirect them (§5.4, B1).
- Tests use the `Alex` / `acme-co` / `PROJ-1234` stand-ins. The data API uses a `file://` stand-in,
  as `SCOUT_LINEAR_API_URL` does; the token endpoints use a monkeypatched transport, never a URL
  setting.

## 12. What Jordan has to do (the OAuth apps)

None of this costs money. Client IDs are public and go into `connectors.yaml` in the Phase 1/2 PR.
**Copy no client secret anywhere.** Linear and Slack still generate one, and we never use it. If
one is ever exposed, rotate it in the app settings.

**Linear (Phase 0, about 15 minutes):**
1. Create a free Linear workspace owned by Raven-Scout (for example "Raven Scout Apps"). Linear
   recommends a dedicated workspace for app management, because every admin of the owning
   workspace can see the app. Using it keeps the app out of any employer's workspace.
2. In it, open *Settings → API → Applications → New* (`linear.app/settings/api/applications/new`)
   and fill in:
   - name **Scout** (it may not contain "Linear");
   - developer **Raven-Scout**;
   - homepage `https://github.com/Raven-Scout/Scout`;
   - icon: the app icon.
3. Redirect URIs: `http://localhost:<p1>/oauth/linear`, `…<p2>…`, `…<p3>…` (the card picks three
   unassigned ports in Phase 0).
4. **Distribution: public.** No webhook. Leave client credentials off.
5. Send the **client ID** to the card. Don't send the secret.

**Slack (Phase 2, about 20 minutes):**
1. Create a free Slack workspace owned by Raven-Scout. An app is created in, and owned by, one
   workspace, so it shouldn't be an employer's.
2. At `api.slack.com/apps`, choose **Create New App → From a manifest** and paste the manifest the
   Phase 2 PR commits (`plugin/engine/scout/accounts/slack-app-manifest.json`). It sets:
   - user scopes only: `search:read`, plus `channels:history`/`groups:history` only if Phase 0 says
     so;
   - the three localhost redirect URLs;
   - `pkce_enabled: true`.
3. Confirm the PKCE toggle under *OAuth & Permissions*. **This is one-way.** Leave **token
   rotation off**; that's one-way too.
4. *Manage Distribution → Activate Public Distribution.* If the checklist refuses localhost
   redirect URLs (the SSL item), stop and tell the card. The fallback is a custom-scheme redirect
   (`scout-oauth://slack`). It costs more than 12 h tokens:
   - **It forces rotation**, so 12 h access tokens and single-use refresh tokens with no
     documented grace window (§7.1 (e)), plus a 30-day refresh-token expiry with PKCE.
   - **The engine's loopback listener can't receive it** (D1). A `scout-oauth://` callback goes to
     whichever app registered the scheme, so Scout.app must catch it with
     `ASWebAuthenticationSession(callbackURLScheme:)`, finish the exchange itself, and hand the
     token pair to the engine on stdin (`scoutctl accounts store slack --stdin`). That's the one
     place a token would cross from the app to the engine, and it would need its own redaction and
     test.
   - **Headless and dev-managed Slack sign-in is lost.** With no app to catch the callback, those
     installs keep only the hand-placed `slack-search-token`. Linear is unaffected.
   - If it comes to that, the card brings it back to Jordan before building it.
5. Optional, later: submit to the Slack Marketplace only if the history scopes turn out to be
   needed and the 1/min cap hurts. That needs a public privacy policy and support page (GitHub
   Pages, free) and Slack's review.
6. Send the **client ID** to the card.

## 13. Phased plan

**Phase 0: verify (one card with Jordan, no merge).**
- Jordan registers the Linear app (§12).
- On a throwaway secrets dir, with a scratch script outside the repo, the card checks:
  - a PKCE exchange and a refresh without a secret;
  - the exact-match loopback redirect: does `localhost` match, and is a different port refused?;
  - the token prefix, and revoke: does revoking the refresh token also end the access token and
    the grant? Linear's docs don't say;
  - the complexity cost of one #532 pre-fetch.
- With Jordan's existing Slack search token, read-only: does `search.messages in:<#C…>` cover a
  🔴 channel (§8.2)?
- Results go into this spec as a short "verified" section, with no vault content.

**Phase 1: Linear, one PR, ships after #532.**
- `scout/accounts/` (credentials, store with locking, PKCE flow, CLI), wired into
  `linear_activity`.
- The `sign_in` roster block + snapshot + both app copies, in ONE commit (cross-cutting, gated by
  `contract.yml`).
- `redact.py` shapes, run-health findings, README + `linear-activity-setup.md` ("sign in, or
  place a key").
- The Settings section, for Linear only, and the cancellable process runner.
- The engine and app halves can be two PRs if the snapshot copy lands with the engine half, since
  the CLI is usable on its own. One PR is simpler.
- The `push: once` notifier class (§7.1 (c)) and the `accounts.<provider>.needs_sign_in` finding.
- Tests:
  - flow against a local fake provider (loopback, state mismatch, cancel, ports busy);
  - refresh race (two processes, one refresh);
  - precedence, perms, redaction;
  - app view-model states.
- Refresh-failure tests: unit tests against a fake token endpoint, an injectable clock and
  injectable write failures.
  - **(a) Write-ahead**
    1. The file reads `refresh_pending` with `started_at` set when the fake endpoint receives the
       refresh POST, so the journal comes before the request.
    2. The fake API sees the first call with the new access token only after the file holds the
       new pair (it reads the file in its handler).
    3. An `os.replace` that raises `OSError` after a 200 leaves the in-memory pair unused. The file
       keeps the old refresh token and `refresh_pending`, and the result is `FAILED`.
    4. An exception injected between the response and the write ("crash"): the next
       `credential()` within the grace window retries with the old refresh token, gets a pair,
       and ends `ok`.
    5. A second `started_at` never replaces the first across retries.
    6. The directory is `fsync`ed after the rename. Patch `os.fsync` and assert it's called on the
       dir fd.
  - **(b) Bounded retry**
    1. A timeout, a reset, a 5xx and a 429 each count as unknown. Retries reuse the old refresh
       token at 5 s, 20 s and 60 s on the fake clock, and a 429 `Retry-After` overrides them.
    2. There are never more than 4 in-process attempts, and the total wait stays within the budget.
    3. After the in-process attempts, exactly one detached retrier is spawned. Assert the spawn
       command and the pid-file guard: a second failing run spawns none.
    4. The retrier stops at `started_at + 25 min`, and stops early once another process has
       written `ok`.
    5. A process that finds `refresh_pending` before the deadline attempts once itself.
    6. Two processes in the race: one POST, and the second re-reads under the lock and uses the
       new pair.
    7. A `401 invalid_client` isn't retried, and the state stays `ok` with `last_error` set.
    8. **A reaped parent doesn't kill the retrier.**
       - The test starts a stand-in "run" in its own process group (`start_new_session=True`, like
         the runner). The run fails the refresh and spawns the retrier against a fake endpoint
         that answers only after a delay.
       - The stand-in run stays alive (the test keeps it busy, so it's still a live parent) and
         holds `.scout-session.lock`.
       - The test then runs the **real** `_scout_reap_holder <run pid>`. It sources the rendered
         `scout-lock.sh` in `bash`, the way the integration tests for #468/#476 do, so the `ppid`
         descendant walk is exercised, not only a group kill.
       - Assert:
         - the retrier's pid isn't in `_scout_session_pids <run pgid> <run pid>` before the reap;
         - its `ppid` is 1 once the spawn call has returned;
         - its pgid and sid differ from the run's;
         - it is still alive after the reap, while the run and its real children are gone;
         - the delayed response is persisted, and the file ends `ok`.
       - A second case injects a sleep before the intermediate's `_exit`. Assert that the spawn
         call doesn't return until the intermediate has exited, and that the retrier's `ppid` is
         1 when it does. That window, while the retrier is still the run's descendant, is the
         only one the reaper could hit, and it closes before the run moves on.
    9. **The retrier and the session lock.**
       - With `.scout-logs/.scout-session.lock` held by a live stand-in pid, the retrier still
         runs and succeeds. The lock file's bytes and mtime are unchanged afterwards.
       - `is_lock_held_by_live_pid` never names the retrier's pid.
       - `lsof`/`/proc` shows no session-lock fd in the retrier.
       - Its environment has none of the run's lock or slot variables.
    10. **The hard exit.**
        - With a short deadline (an injectable grace for tests) and a fake endpoint that never
          answers, the retrier exits by `deadline + 15 s` with code 3.
        - With a deadline already past on start, it exits at once and writes `grace_exhausted`.
        - A stale pid file left behind is cleared by the next spawn.
    11. **A hostile roster overlay changes nothing (B1).**
        - The test writes `<vault>/.scout-state/connectors.local.yaml` with, on
          `mcp:claude_ai_Linear`:
          - `sign_in.oauth.token_url` and `revoke_url` pointing at a local "attacker" listener;
          - a different `client_id`;
          - a `credentials` list naming another file.
          It also adds a `sign_in` block to a new row.
        - Then it runs a due refresh, `accounts logout` and `accounts status`.
        - Assert:
          - the attacker listener gets no request;
          - the refresh and the revoke go to the seed's endpoints (the monkeypatched transport
            records the URL) with the seed's client ID;
          - the seed's file is the one read and written;
          - `bootstrap doctor --json` carries `connectors.overlay_sign_in_ignored`, naming the
            path and the rows but no values;
          - the snapshot is byte-identical with and without the overlay;
          - the rest of the overlay (a `remediation` change) still applies to the merged registry.
        - With `SCOUT_LINEAR_CLIENT_ID` set, that ID is used. No other env var or file changes an
          endpoint.
  - **(c) `needs_sign_in`**
    1. `invalid_grant` → `refresh_refused` at once, with no retry.
    2. The deadline passing → `grace_exhausted`.
    3. A second 401 → `revoked`.
    4. Each blanks both tokens and keeps `account`.
    5. `credential()` then returns the key file if there is one, and `None` otherwise, so #532's
       `NO_KEY` and the gate apply.
    6. Over N runs the notifier pushes `accounts.linear.needs_sign_in` exactly once, with no 24 h
       reminder.
    7. An undelivered push retries on the next call.
       - Coalescing: with a red finding due in the same call, there's one push that carries the
         red headline plus an "Also:" line, and both are marked sent.
       - With no red due, there's one `push: once` push of its own, and two due `push: once`
         findings share it.
    8. The id clears after a sign-in, and a new episode pushes again.
    9. The wrap lists the finding only in the run that made the transition.
  - **(d) `status`**
    1. Text and `--json` for `ok`, `refresh_pending` (with `deadline`) and `needs_sign_in` (each
       reason × each fallback).
    2. Exit 0 or 1.
    3. No token in any output (scan for the fake tokens).
    4. The app view-model decodes and labels each state.
  - **(e) Slack** (lands with Phase 2, listed here so the resolver's interface covers it)
    1. `invalid_auth`, `token_revoked`, `account_inactive` → `needs_sign_in` with no retry.
    2. A network error, a 5xx or `ratelimited` → `FAILED`, with the token kept.
    3. The fallback goes to `slack-search-token`.
    4. A forced-rotation fixture: a lost outcome → `grace_exhausted` on the first refusal.
    5. `user_slack_id`: filled when empty; a no-op when the same; reported, not overwritten, when
       different; skipped with no vault.
    6. With `.scout-session.lock` held by a live pid, `login slack` returns within about 5 s.
       - It exits 0 with `user_slack_id_pending`, and the token is saved.
       - The next `accounts status` with the lock free writes the ID and clears
         `pending_user_slack_id`.
       - Nothing waits 300 s.
    7. `red_project_messages` is only ever in `.scout-cache/session-context.json`.
       - It's gone after `pre-session prune`, and a crashed run's copy is pruned at the next run
         start.
       - It's never older than the window, and carries only the listed fields.
- No eval change: the pre-fetch path is already seeded (`busy-briefing` `linear_delta`) through
  `SCOUT_LINEAR_API_URL`.

**Phase 2: Slack, one PR.**
- The `slack` provider (rotation-aware but off), the trigger source moved onto the resolver
  (fixing its hard-coded secrets dir), the `search.messages` 🔴 pre-fetch,
  `red_project_messages` + the `check-red-polls` change, and the manifest file.
- Its eval seed: `busy-briefing` already has `red_poll`. Add a recorded search response, in the
  same `file://` style.
- Plus the Slack row in Settings.

**Phase 3: only if needed.** Slack history scopes and the rate-cap-aware reader; a Marketplace
listing; per-user "bring your own app" (the `client_id` override, surfaced in Settings).

## 14. Decided (Jordan, 2026-10-29, via the coordinator)

The four open questions in the first draft were all ruled yes:

1. **File-only token storage, no Keychain copy** (D3, §5.1).
2. **The 🔴 poll goes through `search.messages` (`search:read`)**, subject to Phase 0's
   completeness check (D6, §8.2).
3. **`user_slack_id` is auto-filled at sign-in** (D8, §8.2): written when empty, never overwritten
   when different.
4. **One account per provider for now** (D9).

Also added at the coordinator's request: the refresh-failure handling in §7.1 (write-ahead, a
bounded retry inside the grace window, `needs_sign_in` with one notice, how status shows it,
Slack's equivalent) and its Phase 1 tests (§13).

Nothing is open in the design now. Phase 0 waits for Jordan to create the Linear app (§12).
