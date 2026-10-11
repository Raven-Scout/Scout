# Retiring `/scout-setup`: the app and `install.sh` do all of setup

**Date:** 2026-10-06
**Status:** Design approved in conversation (Jordan, 2026-10-06). The written spec is awaiting review.
**Author:** Jordan Burger (brainstormed with Claude)
**Builds on:**
- [`2026-10-05-unified-release-design.md`](https://github.com/Raven-Scout/Scout/pull/317) (#317): D4 and §8 decided that `/scout-setup` retires and left the design to this spec;
- [`2026-09-08-app-managed-engine-design.md`](2026-09-08-app-managed-engine-design.md): Part C, #319. Its onboarding (§5) and adoption table (§10) are the app half of this design;
- [`2026-10-02-custom-connectors-design.md`](2026-10-02-custom-connectors-design.md) and #261: the engine for custom connectors;
- [`2026-07-07-in-app-updates-design.md`](2026-07-07-in-app-updates-design.md), #318: Sparkle.

**Unblocks:** #321, Tasks 7, 9 and 10 and its app contract (§10 maps each item).

## 1. Intent

Jordan, in the unified-release spec: installing the app should be the only step
for most users, and setup steps move into the app. Jordan, 2026-10-06: people
should be able to download the app from the website, not the releases page,
through a download button on `https://raven-scout.github.io/Scout/`.

**Success looks like this:**
- A new Mac user clicks **Download for Mac** on the website, opens the DMG, and
  finishes onboarding with a first briefing. They never need a terminal or a
  slash command.
- A terminal user (Linux, or by choice) runs one `curl … install.sh | bash`,
  answers a few prompts, and gets the same vault. They never need
  `/scout-setup`.
- Every tool the user has connected to Claude Code is offered during setup:
  shipped connectors as toggles, everything else as a custom connector drafted
  for them. No path tells a user a tool "isn't supported".
- Existing installs keep working. Nothing is migrated without being asked.

**Assumptions** (Jordan confirmed them):
- `install.sh` installs the plugin and the engine, never the Mac app.
- The scope is this spec and its plan. Part C (#319) and Sparkle (#318) own the
  app code this depends on.

## 2. Decisions

| # | Decision | Choice |
|---|---|---|
| S1 | Where the steps live | One set of steps with two front ends: the app's onboarding and `scoutctl setup`. Both drive the same engine commands (§3). |
| S2 | The auto-update question | **Dropped** from every front end. Nothing acts on `auto_update.enabled` (`self-update` only checks). App engines update through Sparkle and `EngineUpgrader`. Terminal engines update by re-running `install.sh` or with `/scout-update`. |
| S3 | `install.sh` | Thin bash front end (prerequisites, marketplace, plugin, venv), then `exec scoutctl setup`, a new interactive Python wizard. A headless mode passes flags through. It never installs the app. |
| S4 | `/scout-setup` | Becomes a stub for one minor release: it checks the machine's state and points to Scout.app, `scoutctl setup` or `/scout-update`, and runs nothing. It is deleted in the next minor. |
| S5 | Custom connectors | The engine owns drafting through one headless `claude -p` per server (`connectors custom draft`). The app, `scoutctl setup` and `/scout-connect` all use it. Drafts are applied with `custom add` **after** the vault exists. #321 Task 7's install flags are dropped. |
| S6 | `/scout-update` | Stays, for engines the app doesn't manage. It loses the auto-update nudge and gains the `/scout-connect` pointer. |
| S7 | `/scout-connect` | New. A thin chat front end over `connectors uncovered`, `custom draft` and `custom add`/`remove`. It never drafts on its own. |
| S8 | Website download | `release.sh finalize` uploads the DMG under two names, `Scout-X.Y.Z.dmg` and `Scout.dmg`. The site's button links to `/releases/latest/download/Scout.dmg`. It goes live with v0.15.0, not before. |

## 3. One flow, two front ends

| Step | Scout.app onboarding (Part C §5) | `scoutctl setup` (terminal) |
|---|---|---|
| Preflight | `EngineLocator` state; Prerequisites step (Claude Code, Command Line Tools, git, uv) | `install.sh` checks prerequisites; `setup` refuses an app-managed engine and dispatches on `bootstrap auto`'s plan |
| Identity | About you step: instance name, name, email (from `git config`), timezone | the same four, as prompts with the same defaults |
| Shipped connectors | Connectors step: rows from `connectors detect --json`, with inputs from `needs_user_input` | `detect --json` shown as a numbered checklist; toggle by number; prompt for inputs |
| Everything else connected | Connectors step, "Also connected" section (§6) | the same, as `y/N` per server (§5.2) |
| Budget | per session, plus an optional daily budget | the same two prompts (default $5, daily blank = none) |
| ~~Auto-update~~ | — (S2) | — (S2) |
| Vault, schedule, launchd or cron, shim, pointer | `bootstrap auto --json --managed-by scout-app` | `bootstrap auto --managed-by install.sh` (or `claude-code`, §4.3) |
| Custom connectors applied | `custom add` once per confirmed draft, after the vault exists | the same |
| First briefing | Ready step: `schedule fire-now <first briefing slot>` | `Run your first briefing now? [y/N]`, then the same call |

Scheduling has no step of its own. `bootstrap install` seeds `schedule.yaml`
from `defaults/schedule.yaml` and installs the launchd plists (or the cron
block on Linux). Slots are edited later in the app's Schedule tab or with
`scoutctl schedule`.

## 4. Engine changes

### 4.1 `scoutctl connectors uncovered --json`

This lists the MCP servers that are connected to Claude Code but not read by
any connector. It makes no LLM call.

- It runs `claude mcp list` once, through the parser `connector_detect` already
  has (`parse_mcp_list`, `server_slug`).
- It drops a server when any of these is true:
  - a probe in the merged registry covers it: one of the probe's `tool_chain`
    entries starts with `mcp__<slug>__`;
  - a custom connector in `connectors.custom.yaml` names it as its `server`;
  - it is tooling rather than a source of work: browsers, terminals, session
    and sidebar managers, schedulers, visualizers, simulators. This is a
    pattern list in the engine, kept in one module and unit-tested.
- Output:

```json
{
  "schema_version": 1,
  "servers": [
    {"name": "claude.ai Example Suite", "slug": "claude_ai_Example_Suite",
     "status": "connected", "evidence": "claude.ai Example Suite: https://… - ✔ Connected"}
  ],
  "error": null
}
```

- `status` is `connected`, `needs_auth` or `unavailable`, mapped from the line's
  glyph exactly as `detect` maps it. When `claude mcp list` fails, `servers` is
  empty and `error` says why. The front ends then show "Couldn't list your
  other tools — Re-detect". They never show an empty "nothing else connected".

### 4.2 `scoutctl connectors custom draft --server <name> [--json] [--timeout 120]`

This turns one server into one or more custom-connector definitions. A suite
can be mail, calendar and chat, so one server can become several connectors.
The drafting rules are #261 Task 9's `scout-connect.md` sections 1–2. They move
into an engine prompt template, `plugin/engine/scout/defaults/draft-connector.md`,
and become the **only** copy of those rules.

**Two headless calls, both locked down:**

1. **Draft.** One call reads the server's tools and drafts definitions.
   - Command:
     `claude -p --output-format json --json-schema <draft schema> --permission-mode dontAsk --allowedTools ToolSearch --max-budget-usd 0.50 <prompt>`.
   - The prompt names the server and its slug.
   - The model loads the server's tool names and descriptions with ToolSearch
     (`+<slug>`). It can call nothing else, because `dontAsk` denies every
     tool not in `--allowedTools`.
2. **Probe.** A second call checks each draft's probe.
   - Command:
     `claude -p --output-format json --json-schema {ok, error} --permission-mode dontAsk --allowedTools <the probe tool, exact name> --max-budget-usd 0.10`.
   - It calls that one read tool once. If the call fails, the server needs
     sign-in.

**The engine checks every draft before returning it.** No draft is trusted as
written:
- Every tool and the probe start with `mcp__<slug>__`.
- Every tool reads, checked word by word. The action segment (after the
  last `__`) is split into words: camelCase, kebab-case and snake_case all
  split. A tool is rejected if any word is a write verb (send, post, create,
  update, delete, add, set, save, label, apply, merge, resolve, …), counting
  `un`/`re`/`de`-prefixed forms such as `unmark` and `resend`. It is also
  rejected unless at least one word is a read verb (list, get, search, read,
  find, query, fetch, …).
  - Both lists live in constants and are unit-tested in both directions:
    `list_labels` passes; `send_message`, `unmark_message_spam`,
    `label_message` and `add_reaction` fail.
  - A denylist alone missed real write tools in review, so the read-verb
    requirement is what makes this check fail closed.
- `cc.parse_file` validation, with the reserved keys, the presets, and a key
  that doesn't collide with an existing connector.

**Output:**

```json
{
  "schema_version": 1,
  "status": "drafted",
  "server": "claude.ai Example Suite",
  "definitions": [
    {"key": "suite_mail", "display_name": "Mail suite", "server": "claude_ai_Example_Suite",
     "probe": "mcp__claude_ai_Example_Suite__list_folders", "preset": "mail",
     "inbound": {"tools": ["mcp__claude_ai_Example_Suite__search_messages"]},
     "needs_user_input": []}
  ],
  "summary": [{"key": "suite_mail", "scans": "new mail that may need Alex's reply", "looks_up": "past threads with a person"}],
  "issues": []
}
```

- `status` is one of:
  - `drafted`: every definition passed validation and the probe;
  - `needs_auth`: the probe call failed;
  - `no_read_tools`: the server has no read tools, for example a send-only
    tool;
  - `invalid`: the model's output failed validation twice, with one retry that
    feeds back the issues;
  - `timeout`;
  - `error`, with `message`.
- Front ends map these to the three outcomes the hard rule allows: *added*,
  *skipped by the user*, or *sign in first*. `no_read_tools` and `invalid` read
  as "Scout couldn't find anything to read in <server> — Retry", never as
  "not supported".
- Drafting needs no vault. It reads the registry and presets from the plugin
  root, and `connectors.custom.yaml` only if a vault exists.

**Verify first** (plan Task 1, a spike that stops for Jordan if it fails): in a
`-p` session with `--permission-mode dontAsk --allowedTools ToolSearch`, two
things must hold:
- ToolSearch can load a claude.ai connector's deferred tool schemas;
- any other tool call is denied, not prompted.

If the first fails, the fallback is not to widen `--allowedTools` to
`mcp__<slug>__*`. It goes back to Jordan.

### 4.3 `scoutctl setup`: the terminal wizard

A new top-level command that runs §3's steps in a terminal. It calls the same
functions the CLI subcommands call (in-process, not by shelling out to
itself).

- **Where it reads answers from.** It reads prompts from `/dev/tty`, because
  under `curl | bash` stdin is the pipe. With no `/dev/tty` and no `--yes`, it
  exits 2 with a message listing the headless flags.
- **Dispatching on `bootstrap auto`'s plan** (`bootstrap_auto.detect`):
  - **upgrade**: it says "Found your vault at <path>, upgrading", runs `auto`,
    reports the doctor result and stops. It asks no questions. That makes
    **re-running `install.sh` the terminal update path.**
  - **install**, **resume** (the install-incomplete marker) or
    **migrate-legacy**: it runs the full wizard.
  - **refused**: it prints the reason and exits 2.
- **Engine pointer.** If the pointer says `managed_by: scout-app`, it refuses:
  "Scout.app manages this engine — open Scout.app."
- **Headless flags**, which mirror `bootstrap auto`:
  - `--vault PATH` (sets `SCOUT_DATA_DIR`), `--instance-name`, `--name`,
    `--email`, `--timezone`;
  - `--connectors LIST`, `--slack-id`, `--github-username`, `--github-repos`;
  - `--max-budget`, `--daily-budget`;
  - `--first-run/--no-first-run`, `--managed-by` (default `claude-code`;
    `install.sh` passes `install.sh`), `--yes`.
  - With `--yes` it skips the "Also connected" step. It ends with: "You also
    have <servers> connected — run `scoutctl connectors setup` to add them."
- **Exit code** is `bootstrap auto`'s: 0 green, 1 yellow, 2 red or refused.

### 4.4 `scoutctl connectors setup`

This is the "Also connected" step on its own, for an existing vault: it runs
`uncovered`, then `draft`, then a confirmation, then `custom add`. `scoutctl
setup` uses the same function in its connector step, holding the drafts until
the vault exists.

It covers only custom connectors. Turning a **shipped** connector on or off
after install has no command today (you edit `connectors.enabled` and run
`/scout-update`), and this spec doesn't add one (§12).

### 4.5 The `custom add|remove|list` contract (from #321)

- `custom add` and `custom remove` get a distinct `busy` status and a
  `--no-wait` flag.
  - Today they block for up to 300 s on the session lock and then return a
    generic `error`.
  - With `--no-wait`, a held lock returns `busy` at once.
  - The app always passes `--no-wait`, shows "A Scout session is running —
    we'll add it when it finishes", and retries every 30 s.
- `custom list` returns full definitions: tools, guidance,
  `needs_user_input`, `required_in_types`. Invalid entries come back as rows
  with their issues, so no front end parses the YAML.

### 4.6 Strings and the auto-update leftovers

- Every engine message that says "run `/scout-setup`" now says "open Scout.app
  or run `scoutctl setup`". The places include:
  - `bootstrap upgrade`'s no-vault refusal;
  - `cli.py`, `config.py`, `paths.py`, `heartbeat.py`, `connector_detect.py`,
    `connector_probes.py`, `auto_update_config.py`;
  - `templates/connector-probes.yaml`, `scout-config.yaml.tmpl`,
    `knowledge-base.md.tmpl`.

  A unit test greps `plugin/` (excluding `CHANGELOG.md` and the stub itself)
  and fails on any remaining `/scout-setup`.
- `bootstrap install --auto-update`, `config set-auto-update` and the
  `auto_update` key stay as they are. Removing them is a follow-up once someone
  decides what unattended updates should mean (§12). Only the questions and the
  nudge go.

## 5. `install.sh`

### 5.1 What it does

1. **Prerequisites**, unchanged: Command Line Tools on macOS, `claude`, `git`,
   and uv (installed if missing). The marketplace-source check is unchanged.
   `--check` is unchanged.
2. **Plugin and engine**, unchanged: add or update the `scout-plugin`
   marketplace, install and update `scout@scout-plugin`, resolve the install
   root, and run `install-venv.sh`.
3. **New:** `exec "$ROOT/.venv/bin/scoutctl" setup --managed-by install.sh "$@"`.
   It runs the venv's binary directly, because the shim doesn't exist until
   `bootstrap` writes it.
4. **New, headless:** `curl … | bash -s -- --yes --name "Alex" --email alex@example.com --connectors slack,github`.
   Every argument after `--` goes to `scoutctl setup`. `--check` stays the
   installer's own flag.
5. On macOS the closing message adds one line: "Prefer a Mac app? Download
   Scout.app from <site> — it adopts this install."

The old closing text ("Next step — … run `/scout-setup`") is removed.

### 5.2 What a terminal session looks like

```
Scout follows this Mac's timezone (America/New_York). Keep it? [Y/n]
Your name [Alex]:
Your email [alex@example.com]:

Connected tools:
  1 [✓] Slack        2 [✓] Calendar     3 [✗] Gmail (sign in through /mcp)
  4 [✓] GitHub       5 [✓] Linear       6 [?] Drive (couldn't tell)
Toggle by number, or press enter to continue:
Your Slack user ID: …

Also connected: Example Suite, Example CRM.
  Add Example Suite? [Y/n] … drafting (about 30 s)
    Mail suite · new mail that may need your reply · past threads with a person
    Keep it? [Y/n]
```

## 6. App changes (on top of Part C)

These land after #319's C8 wires onboarding into the app.

- **The Connectors step gains an "Also connected" section** under the shipped
  rows, from `connectors uncovered --json`.
  - Each server has an **Add** toggle. Turning it on starts `custom draft` in
    the background, with at most three running at a time.
  - Each draft shows its three-line summary with **Keep** / **Edit** /
    **Remove**. Edit opens a sheet over the definition's fields.
  - A `needs_auth` row reads "Sign in to <server> in Claude Code
    (`/mcp` or claude.ai ▸ Settings ▸ Connectors), then **Re-detect**", with
    a button that opens Claude Code through `ClaudeLauncher`.
  - The step's Continue never waits on a draft still running. That server
    moves to "Finish in Settings ▸ Connectors".
- **The Vault step applies the kept drafts** after `bootstrap auto` succeeds,
  with one `custom add --no-wait` each, before `doctor --json`. A failure
  leaves the vault in place: the row shows the status, and Settings ▸
  Connectors retries.
- **New: a Settings ▸ Connectors section.**
  - It lists custom connectors from `custom list`, each with Remove.
  - It shows the same "Also connected" section as onboarding.
  - This is the app's equivalent of `/scout-connect`.
- **`ConnectorHealthService`** merges `connectors list --json` over the
  bundled snapshot, so `tier: custom` rows show by name (#321).
- **Strings.** `EngineSettingsModel`, `EngineSettingsSection` and
  `KnowledgeBaseView` say "run `/scout-setup`" today. Each becomes the
  matching app action. #318's plugin row keeps "copy `/scout-update`" for
  external engines.

## 7. Commands

### 7.1 `/scout-setup`: a stub for one minor release

`scout-setup.md` shrinks to about 30 lines:
- **Description:** "Retired — points you to Scout.app or `scoutctl setup`."
- It keeps today's state check, then routes:

| State | What it tells the user |
|---|---|
| `APP_MANAGED` | Open Scout.app. |
| `VAULT_EXISTS` | Your vault is set up. To update, run `/scout-update`. |
| `FRESH` or `INSTALL_INCOMPLETE`, macOS | Download Scout.app: `<site>/#install`. Or, in a terminal: `scoutctl setup`, or the `install.sh` one-liner if `scoutctl` isn't installed. |
| `FRESH` or `INSTALL_INCOMPLETE`, Linux | In a terminal: the `install.sh` one-liner (re-running it is safe). |
| `ORPHAN_JOBS` | The manual reset snippet, unchanged. |

It runs no install itself: a Claude Code Bash call has no TTY to host the
wizard. A prose test asserts that the stub contains no `bootstrap install` and
no `bootstrap auto`.

It is deleted in the minor after it ships. If the retirement ships in v0.15.0,
the stub is deleted in v0.16.0.

### 7.2 `/scout-update`

- Step 0.1 (refuse app-managed engines) is unchanged.
- "Run `/scout-setup`" (`NO_VAULT`, `INSTALL_INCOMPLETE`) becomes "`scoutctl
  setup` or Scout.app".
- The **Auto-update nudge** section is removed (S2).
- After the report, it adds #261 Task 9's pointer: when `connectors uncovered
  --json` lists servers, it says "You also have <server> connected — run
  `/scout-connect <server>` to have Scout read it."
- The `connectors.custom.yaml` note replaces the
  `connector-probes.local.yaml` bullet (#261 Task 9, step 5).
- `/scout-status` drops its auto-update display.

### 7.3 `/scout-connect`: new

- **Usage:** `/scout-connect [<server>]` adds a connector; `/scout-connect
  --remove <key>` removes one.
- **Finding `scoutctl`:** through the shim on `PATH` (`command -v scoutctl`),
  so it works for every engine type, app-managed included. A custom connector
  is vault data.
- **Flow:**
  1. `connectors uncovered --json`. With no argument, it lists the uncovered
     servers and asks which to add.
  2. `custom draft --server <name> --json`.
  3. Show the summary, and let the user adjust it in chat (focus, which tools,
     skip an activity).
  4. `custom add --file -`.
- **Statuses:** the #261 Task 9 status table (`applied`, `invalid`,
  `probe-failed`, `deferred`, `conflict`, `error`), plus `busy`.
- **The hard rule** carries over from #261 Task 9: never say a tool can't be
  read. The prose guard's `DEAD_ENDS` list applies to `scout-connect.md`,
  `scout-update.md` and the stub.
- The chat may edit a draft, but every edit goes back through `custom add`'s
  validation, and the drafting rules are not restated in the command.

## 8. The website download button

- **Release.** `release.sh finalize` copies the notarized, stapled DMG to
  `Scout.dmg` and attaches both files: `gh release create … "$dmg"
  "$build/release/Scout.dmg" [appcast.xml]`.
  - The copy is never re-signed or re-notarized.
  - Publishing stays one `gh release create` call, after notarization.
  - An rc gets no `Scout.dmg`. It is never Latest, so the button's
    `/releases/latest/download/Scout.dmg` never reaches it.
  - The appcast keeps the versioned name.
  - #317's stubbed-`gh` tests assert that both assets are in the one call for
    a release, and that an rc has none. They use only the existing harness.
  - #317's owner reviews the change, which rides on whichever of #317 and
    this work merges second. (These rules came from that review on
    2026-10-06.)
- **Site** (`docs/index.html`):
  - The hero's primary button becomes **Download for Mac**, linking to
    `https://github.com/Raven-Scout/Scout/releases/latest/download/Scout.dmg`.
    The "View on GitHub" link stays.
  - The `#install` section leads with the same button and "macOS 13+, needs a
    Claude account". The prerequisites line shrinks, because onboarding
    checks Claude Code and hands off its install.
  - A secondary "Terminal or Linux" block holds the `curl … install.sh | bash`
    one-liner, with no `/scout-setup` line and no "by hand" paragraph.
  - The "Download the Mac app" link to the releases page is removed.
- **Timing.**
  - The site change and the `Scout.dmg` asset ship in the **v0.15.0** release
    PR. The 0.14.0 DMG doesn't install the engine, so a button before then
    would open on "Scout needs its engine".
  - Until then, the site's current text and link stay.
  - The site publishes from `main`, so the change is merged together with the
    release PR, and `finalize` follows within minutes. A test asserts that
    `index.html` holds the exact href and doesn't mention `/scout-setup`.
- **Acceptance** (added to C10): after `finalize v0.15.0`, the site's button
  downloads `Scout.dmg`, and that DMG is v0.15.0's and passes `spctl`.

## 9. Existing users

| Install found | What changes |
|---|---|
| Vault plus a GitHub-marketplace engine | Nothing. Updates come through `/scout-update`, or by re-running `install.sh`. Part C phase 3's **Migrate to app-managed** stays Part C's. |
| Dev checkout (`managed_by: dev`) | Nothing. The app adopts it read-only. |
| App-managed | Nothing. `scoutctl setup`, `/scout-update` and the stub all point to the app. |
| `install-incomplete` marker | Resumed by `scoutctl setup` or the app's onboarding: `bootstrap auto` maps it to install, resume. |
| Followed an old README or `install.sh` that says "run `/scout-setup`" | The stub routes them (§7.1). |
| A vault whose `auto_update.enabled` is true | Unchanged and harmless: nothing reads it (S2). |

## 10. #321, item by item

| #321 item | Where it goes now |
|---|---|
| Task 7: `bootstrap install --custom-connectors-file` / `--custom-input` | **Dropped.** Custom connectors are applied after the vault exists, with `custom add` (S5). Install never writes `connectors.custom.yaml`, so re-running `install --connectors` can't drop custom keys from `connectors.enabled`. |
| Task 9: onboarding offers every uncovered server | App: the Connectors step's "Also connected" section (§6). Terminal: `scoutctl setup` and `scoutctl connectors setup` (§4.3, §4.4). Claude Code: `/scout-connect` (§7.3). All three use `uncovered` and `draft` (§4.1, §4.2). |
| Task 9: the `/scout-setup` Step 2b | **Dropped.** `/scout-setup` is a stub (§7.1). |
| Task 9: the `/scout-update` pointer | §7.2. |
| Task 10: README "Custom connectors" section | Kept, plus the install rewrite in §11's phase 4. |
| App contract: `busy` and no-wait; full `custom list`; `ConnectorHealthService` merge | §4.5 and §6. |
| Engine hardening and the stale-lock race | Unchanged in #321; not this spec. |

## 11. Order of work

Each phase is one PR or more, merged on its own:

1. **Engine, additive.** It can merge any time and changes nothing users see.
   - The spike (§4.2, verify first).
   - `connectors uncovered`, `custom draft`, `connectors setup`, `scoutctl
     setup`.
   - `busy` and `--no-wait`, full `custom list`.
2. **Terminal surface.**
   - `install.sh` runs `scoutctl setup`.
   - New `/scout-connect`; `/scout-update` edits (§7.2).
   - The plugin README's terminal install section.
   - `/scout-setup` still works through this phase, so both paths exist.
3. **App.** Needs #319's C8 merged.
   - The "Also connected" section, applying drafts in the Vault step, Settings
     ▸ Connectors, the `ConnectorHealthService` merge, the strings.
4. **Retire.** Part of, or after, the v0.15.0 release PR.
   - The `/scout-setup` stub and every remaining string (§4.6).
   - The `Scout.dmg` asset in `release.sh`.
   - The website (§8).
   - Both READMEs lead with the app, with `install.sh` as the terminal path.
   - The changelog.
   - If phase 3 isn't merged by v0.15.0, the retirement waits for v0.16.0, as
     the unified-release spec §6 already allows. The website button still
     ships with v0.15.0, because Part C alone makes the DMG self-sufficient.
5. **Delete the stub** in the next minor.

**Owners.** The custom-connector pieces (phase 1's `uncovered`, `draft`,
`busy` and `list`; phase 3's "Also connected"; Task 10) belong to #321's
owner. The rest follows this spec's plan.

## 12. Out of scope

- Turning a shipped connector on or off after install, from the app or a
  command. Today you edit `connectors.enabled`, then run `/scout-update` or let
  the app upgrade.
- Unattended engine updates for terminal installs, and removing the
  `auto_update` key and flags.
- `install.sh` moving to the app's `~/.local/share/scout` layout (Part C phase
  3), and `install.sh` installing the app.
- Part C's **Migrate to app-managed** button.
- Linux packaging beyond `install.sh`.

## 13. Testing

- **`connectors uncovered`:** a fixture `claude mcp list` text with a
  registry-covered server, a custom-covered server, tooling servers, a
  `needs_auth` server and a parse-hostile line. A failing `claude` gives
  `error`, not an empty list.
- **`custom draft`:** run against a stub `claude` on `PATH` that returns
  canned JSON.
  - Valid → `drafted`.
  - A write tool, a foreign-server tool, an invalid definition and a key
    collision are each rejected.
  - One retry after `invalid`; a failing probe gives `needs_auth`; a timeout;
    budget flags present in the argv.
  - No test calls a real `claude`.
- **`scoutctl setup`:** Typer's `CliRunner` with scripted input.
  - Fresh → `bootstrap auto` gets the right flags, `budget set` runs, and
    `custom add` runs once per kept draft.
  - An existing vault → upgrade with no prompts.
  - An app-managed pointer → refused.
  - No TTY without `--yes` → exit 2.
  - `--yes` with flags → no prompts.
- **`install.sh`:** bash tests with stubbed `claude`, `uv` and the venv's
  `scoutctl`. Arguments after `--` reach `setup`; `--check` doesn't run
  `setup`; the closing text has no `/scout-setup`.
- **Prose guards:**
  - the stub runs no bootstrap;
  - `scout-connect.md` calls `custom draft` and `custom add --file -`, and
    contains no drafting rules (a marker phrase from the engine template is
    absent);
  - `DEAD_ENDS` holds across commands;
  - no `/scout-setup` remains in `plugin/`, except the stub and the changelog.
- **Release:** #317's stubbed-`gh` test checks for both DMG names.
- **Site:** `index.html` holds the exact download href and no
  `/scout-setup`.
- **App** (phase 3): `OnboardingViewModel` tests with a fake engine client.
  - Drafts run while you continue.
  - Kept drafts are applied after `bootstrap auto` and before doctor.
  - The `needs_auth` row text.
  - `busy` retries.
  - Settings ▸ Connectors removes a connector.
- **End to end:**
  - **C10 gains three checks:**
    - on the fresh macOS account, one uncovered server is drafted, kept and
      read by the first briefing;
    - the website button after `finalize`;
    - the stub's routing on that account.
  - **Terminal acceptance:** on a fresh Linux VM or macOS user, run
    `curl … | bash` to a first briefing. Re-run it, and it upgrades with no
    prompts.
- **Fixtures** use the stand-ins from `CLAUDE.md`: `Alex`,
  `alex@example.com`, `example_suite`, `claude_ai_Example_Suite`. No real
  server or workspace names.
