# Vault and engine access for agents that run off the laptop — design

Status: **draft for Jordan's review.** Design only; nothing here is built.
Spec only. Implementation needs Jordan's go on each phase (§9) and answers to
the open questions (§10).

Builds on the per-note privacy spec
(`2026-10-20-per-note-privacy-design.md`, #453, cited here as "privacy §n"),
the Agnes repository sync and lease (`scout/repo_sync.py`, `scout/repo_lease.py`,
and the Agnes-side "Scout in Agnes" design of 2026-10-20, cited as "Agnes §n"),
and the connected-accounts spec (`2026-10-29-connected-accounts-oauth-design.md`).

## 1. Why

Jordan runs agents outside the laptop: **grokbot** (xAI's Grok Bot) and
**Claude duet**. He wants them to use Scout to its fullest: the knowledge base
through `scoutctl kb`, the action items, pre-session data, the skills and the
brain files. In his words: *"The simplest way is by storing it as a private
github repo, but I'm not convinced that's the best."*

Today the vault (a git repo) and the engine live on one Mac. Scheduled runs
are launched there by launchd, read and write the vault there, and are kept
inside the privacy model by Claude Code hooks that run there
(`kb-read-gate`, `kb-read-label`, `outbound-gate`). A remote agent is none of
those things. It is a **new reader on someone else's computer**, often driven
by a different model vendor, and possibly visible to other people. This spec
decides what such an agent may see, what it may write, and how both reach it.

## 2. Summary of the recommendation

**A hybrid, in phases. The laptop vault stays the only canonical writer.**

1. **A new reader class, `delegate:<id>`**, for the owner's own agents that
   run off the laptop. By default it sees `visibility: normal` notes only,
   never private ones, never local-only paths. The owner can widen one
   delegate to private notes in the overlay (§4).
2. **Reads through a per-delegate filtered mirror** (reusing `repo_sync`'s
   mirror builder with a new GitHub host and a "delegate profile" that also
   strips private notes). The mirror is a free private GitHub repo. The agent
   clones it read-only and installs the engine from the `stable` branch, so it
   runs the real `scoutctl kb`, `action-items list`, `pre-session` and the
   skills against it. The privacy boundary is **structural**: what is not in
   the mirror can't leak, whatever runtime, model or missing hook the agent has (§6).
3. **Writes as proposals, never to the mirror's `main`.** The agent pushes
   observations and action-item operations to its own inbox repo. The next
   laptop run (the curator) applies them under its own lease, through the
   normal lint and widening guard. No second writer, so no conflict with
   scheduled runs, and the Repository Writer Lease needs no GitHub
   counterpart (§7).
4. **Remote MCP only where a platform cannot run code.** Grok Bot has a shell
   and runs the engine itself. Claude Duet documents no shell, so it starts
   on the GitHub connector over its own delegate mirror (no Scout server at
   all), and gets a small read-mostly `scoutctl mcp` server over the same
   mirror only if Duet honours custom connectors and the GitHub route proves
   too weak (§5.3, Q3).

**Per platform:** grokbot → B (mirror in `/workspace`, engine installed,
inbox writes), provided no Bot on that account is shared with other people.
Claude duet → B's mirror read through the GitHub connector first, C later if
needed.

Rejected as the primary path: a plain private GitHub repo of the whole vault
(§5.1). It is the simplest, but it ships local-only paths and private notes
to two third parties, leaves the gates to each agent's runtime, and makes
every agent a second writer.

## 3. The platforms

Facts from the vendors' docs, checked 2026-10-31. **[docs]** is the vendor's
own page; **[2nd]** is secondary; **[unknown]** means the docs don't say, and
V0 (§9) checks it by hand.

### 3.1 grokbot = xAI's Grok Bot

Persistent agent "teammates", beta since 2026-08; Team Bots (shared bots with
their own Slack handle) since 2026-09-28.

| Question | Answer |
|---|---|
| Where agents run | One persistent cloud computer **per user account**, shared by all of that account's Bots, each Bot with its own screen; hosted by xAI's infrastructure partner in the US; no self-hosting [docs] |
| Shell / code | Yes: browser, command line, filesystem [docs] |
| Filesystem | `/workspace` persists across updates; installed packages, temp dirs and **Python virtual environments are not kept** across a computer update, recover or reset [docs] |
| Isolation between Bots | **None.** Files, browser sessions and command-line credentials are shared by every Bot on the account; "screens are separate work surfaces, not separate security boundaries"; the docs say not to place a credential or file there that another Bot on the account shouldn't use [docs]. Between users: dedicated, hardware-isolated [docs] |
| MCP | Add a server by URL or by a command that runs on the computer; a command-started (stdio) server is available to the account's other Bots too; private-network servers ask for approval [docs]. Transport and auth for URL servers [unknown] |
| Git / private repos | A GitHub connector and GitHub-event triggers exist [docs]; cloning a private repo with a token from the shell [unknown, expected to work given a shell and egress] |
| Secrets | A secrets store keyed by env-var name, hidden from the model [docs]; exposure to shell commands as env vars implied [unknown] |
| Schedule / unattended | Routines on a schedule (≥ 5 min apart) or on events (Slack, GitHub, webhook …), running with the laptop closed [docs] |
| Egress | Allow-all by default from shared static IPs; enterprise allowlists; private networks via Tailscale/cloudflared setup scripts on enterprise plans [docs] |
| Skills / plugins | "Skills" are reusable instructions shared by the account's Bots; file format [unknown]; whether it reads SKILL.md, Claude Code plugins or AGENTS.md [unknown] |
| Cost | Included in some paid X / Cursor tiers [docs]; nothing extra for Scout |

**What this means here.** Grok Bot can run the engine (Option B fits): clone
the mirror into `/workspace`, reinstall the engine's venv on start because it
doesn't survive updates. Scout's Claude Code hooks don't run there, so the
boundary must be structural. And because **every Bot on the account shares
one computer and its credentials**, the reader is decided per *account*, not
per Bot: if any Bot on that account is a Team Bot or answers in a shared
channel, the whole computer is a shared surface (§4.2).

### 3.2 Claude duet = Anthropic's Claude Duet

A proactive agent app, research preview by waitlist since 2026-10-21, on iOS,
Android and claude.ai, for US Pro/Max/Ultra plans, drawing on the plan's usage
limits [docs].

| Question | Answer |
|---|---|
| Where agents run | Its own VM with a browser, working while the owner's devices are off [docs]; sign-in tokens are added by a proxy outside the agent's VM [docs] |
| Shell / code / files | Not documented [unknown]; secondary reports describe a Linux container [2nd] |
| MCP / connectors | Connectors and plugins from the Claude Marketplace, and the apps already connected to the owner's Claude account [docs]. Whether claude.ai **custom** (remote MCP) connectors work in Duet [unknown] |
| Skills / memory | Carry over from the owner's Claude account [docs] |
| Git / secrets | Not documented [unknown] |
| Schedule | A morning brief, checks every few hours, routines [docs] |
| Egress | No allowlist documented; whether it reaches an arbitrary self-hosted HTTPS host [unknown] |
| Cost | No extra charge on the plans above [docs] |

**What this means here.** Plan for Duet as a **connector-only** client until
V0 proves otherwise: it gets Scout through (a) a custom remote MCP connector,
if Duet honours custom connectors, or (b) the GitHub connector pointed at its
delegate mirror, which needs no Scout server at all. Duet is Anthropic's, the
same model provider the laptop runs use, but its VM and memory are a
different product with its own retention, so it still gets the delegate
default (§4.1, Q4).

### 3.3 What neither platform gives Scout

- Neither runs Scout's Claude Code hooks (`kb-read-gate`, `kb-read-label`,
  `outbound-gate`) as far as the docs say. The gates that protect a laptop run
  don't exist there.
- Neither offers a per-agent isolation boundary inside the owner's account
  (Grok: documented; Duet: unknown).
- Neither is the owner's own machine; both keep their own memory of what the
  agent read.

## 4. Readers and what a remote agent may see

### 4.1 The new reader: `delegate:<id>`

The privacy spec has three CLI readers: `owner` and `autonomous` see
everything (labelled); `agent:<principal>` (another person's agent) sees
nothing in v1 (privacy §4.1). None fits an agent the owner owns that runs on
a third party's computer:

- `owner`/`autonomous` would put private notes, and anything a hook would
  normally keep from leaving, on a platform where Scout's hooks don't run and
  whose operator is not the owner's LLM provider today.
- `agent:*` would give it nothing, which defeats the purpose.

So: **`delegate:<id>`**, where `<id>` is a slug the owner picks
(`grok-personal`, `duet`). Narrowing order becomes
`owner` > `autonomous` > `delegate:*` > `agent:*`. A delegate can't be swapped
for another delegate (lateral, like `agent:*` today). `scout/kb/reader.py`
gains the scheme; every `scoutctl kb` command and the audit log
(privacy §7.4) treat it like any non-owner reader.

| Delegate sees | Default | Owner may widen to |
|---|---|---|
| `visibility: normal` notes, labelled | yes | — |
| `visibility: private` notes | **no** | yes, per delegate (`privacy.delegates.<id>.include_private: true`, §4.5), only for a delegate whose conversations are the owner's alone (§4.2) |
| Local-only paths (plumbing, trading) | **never** | never: path-level, like any sync (privacy §4.3) |
| Action items (daily file, context notes) | yes, minus lines that link a private note (§4.4) | whole file, when private is widened |
| Brain files, `scout-config.yaml` minus secrets, schedule snapshot | yes | — |
| `.scout-state/` beyond the sync allow-list, `.scout-logs/`, `.scout-cache/` | never | never |

**Precedent.** Agnes already ruled the same way for its own non-private
channel: in a Slack DM, the owner's agent reads only notes whose effective
visibility is `normal` (Agnes §6, condition 2, Jordan 2026-10-21). A delegate
on a third-party platform is at least as exposed as that.

### 4.2 Which agent conversations count as the owner's alone

The brain-coordinator rule still holds: raw vault access only for agents the
owner owns, in conversations that are theirs alone (privacy §4.1). Per platform:

- **An agent only the owner talks to** (a personal bot in a private chat, a
  personal duet session): `delegate:<id>`.
- **Anything other people can read or drive** (a team bot, a bot in a shared
  Slack channel, a conversation with a second human participant, a bot whose
  memory is shared with a team): **`agent:*`, which gets nothing in v1.** This
  is the line Jordan drew for Agnes (ruling 3) and it applies here unchanged.
  Such a bot is not given the mirror or any credential to it at all.

The host decides the reader, not the model: the credential handed to an agent
is minted for one delegate id, and the mirror it unlocks contains only what
that delegate may see (§6).

**The account is the unit, not the bot.** On Grok Bot every Bot on an account
shares one computer, its files and its command-line credentials (§3.1). A
mirror clone or a token placed there is readable by every Bot on the account.
So:

- a Grok account gets a delegate mirror only if **no** Bot on it is a Team
  Bot, has a shared Slack handle, or answers in a channel other people read;
- if Jordan wants a Team Bot, it lives on a different account from his
  personal delegate, and gets nothing from Scout in v1;
- `scoutctl delegate init` asks the owner to confirm this once per delegate
  and records the confirmation in the overlay; it can't verify it.

Claude Duet is per Claude account and documents no sharing; the same rule
applies if that changes.

### 4.3 Output classes for a delegate

Delegate platforms add surfaces. The privacy spec's classes (privacy §4.2)
extend as data in `privacy.yaml → surfaces`:

| Surface | Class |
|---|---|
| The delegate's own private conversation with the owner | owner-only **if** the delegate has `include_private`; otherwise it never holds private content to begin with |
| The delegate's platform memory / files / workspace storage | owner-only only for a single-owner workspace; shared if any other person can read it |
| Anything the delegate posts to Slack, email, other tools | shared (unchanged) |
| The delegate's inbox repo (§7) | owner-only (only the owner and that delegate can read it) |

With the default profile (no private notes in the mirror) the delegate holds
no private content, so no outbound check is load-bearing for privacy. Normal
content may go wherever the task calls for (privacy §3.1).

### 4.4 Paragraph-level leaks the mirror must handle

`visibility` is per file, but owner-only files link private notes freely
(privacy §8.2): the daily action-items file links private personal tasks every
day, and context notes mix work and personal lines. A delegate mirror without
private notes would still carry their titles, slugs and links inside those
files. So the delegate profile:

- drops every **line** of an action-items or context file that links or names
  a private note (the same matchers as `kb check-outbound`, privacy §8.2),
  and records the count, never the text, in the mirror manifest;
- drops `knowledge-base/people.md`-style root indexes' lines the same way;
- leaves other normal notes whole: a normal note that mentions a private one
  by name is a lint question for the owner, and `kb lint --report` lists them
  (privacy §8.2). Open question Q6 asks whether to strip those lines too.

The redacted file is a new blob in the mirror commit only; the vault file is
untouched, so the laptop never replays a redaction back (§7.3).

### 4.5 Settings and keys

Delegate settings follow the privacy-policy key layout agreed with the Shared
Areas marking slice (S1a, Raven-Scout/Scout#553): everything lives in
`scout-config.yaml → privacy:`, and `privacy-policy.json → policy` mirrors
it. The keys are grouped by reader, additive, and `schema_version` stays 1.
Unknown keys are ignored, so a vault without delegates parses unchanged.
Federation keys (`federation`, `federation_ceilings`, `federation_defaults`)
are for `agent:*` readers only. Delegate keys sit beside them, never inside:

```yaml
privacy:
  # v1 keys for all readers: local_only_add, visibility_defaults, shared_files_add, surfaces
  # agent:* readers (Shared Areas): federation, federation_ceilings, federation_defaults
  delegates:                    # delegate:* readers (this spec, V1)
    grok-personal:              # the <id> in delegate:<id>
      include_private: false    # §4.1; only the literal `true` widens, anything else is false
      account_unshared: true    # §4.2; the owner's one-time confirmation that no Bot on the account is shared
    duet: {}
  # delegation: {...}           # reserved for policy-wide delegate switches; none in V1
```

Rules:

- **Ids.** `^[a-z0-9][a-z0-9-]{0,39}$`. A `delegate:<id>` reader whose id
  isn't listed sees nothing (fail closed), so the list itself is the on
  switch, and V1 needs no global `enabled` key.
- **Policy-wide switches** go in the sibling `delegation:` section, never as
  a reserved key inside `delegates:` (no `_defaults`), so every key under
  `delegates:` is an id.
- **Separate sections.** No delegate key goes inside `federation*`, and no
  federation key goes inside `delegates`.
- **Labels gain no delegate keys.** What a delegate sees is derived from
  `visibility`, `local_only` and that delegate's `include_private`. The
  line-level redaction of mixed files (§4.4) is part of the mirror build, not
  a label. `kb.privacy.visible_to` gains the policy as an argument for this
  (V1).
- **Only the owner edits it.** The widening guard already blocks a scheduled
  run's change to the `privacy:` block (privacy §8.3); that covers
  `delegates:`.
- **Not policy.** Mirror links (host, repo, clone URL, profile, credentials)
  are link state in `.scout-state/repo-sync.json` and `~/.scout-secrets/`, not
  privacy keys (§6.1, §6.4).
- **Agnes** treats readers it doesn't know as not indexed, so `delegates:` in a
  synced `privacy-policy.json` changes nothing there. A delegate mirror's own
  `privacy-policy.json` carries only that delegate's entry (V2).

## 5. Options compared

### 5.1 Option A — the whole vault as a private GitHub repo, engine in each agent

`git remote add` a private GitHub repo, push `main`, give each agent a token,
install the engine there.

- **Privacy: fails the model.** The full history goes up, including every past
  version of local-only paths (the reason Agnes pushes a fresh snapshot at link
  time, Agnes §4.1) and every private note. Gates are Claude Code hooks; a
  runtime that isn't Claude Code (Grok Bot) or doesn't load the plugin's hooks
  reads raw files freely. Removing a path later leaves it in history.
- **Writes:** every agent is a second writer on `main`. GitHub has no lease,
  and free private repos have no branch protection, so a bad push or a force
  push from an agent lands. Merge conflicts with scheduled runs land on the
  laptop's next pull, mid-run.
- **Freshness:** good if the laptop pushes after each run.
- **Upkeep / cost:** lowest. Free.
- **Engine work:** almost none.

Verdict: simplest, but it is exactly the "raw filesystem for a reader that
isn't the owner" the privacy spec forbids for anything outside the owner's own
machine and runs. Rejected as the primary path.

### 5.2 Option B — a filtered mirror per delegate (reuse `repo_sync`)

`scoutctl repo link --host github --as delegate:<id>` pushes a filtered mirror
to a private GitHub repo: no local-only paths, no private notes, redacted
mixed files (§4.4), labels and `.scout-state/privacy-policy.json` in each
commit, linear history from link time. The agent clones it read-only and
installs the engine.

- **Privacy:** structural. The mirror contains only what the delegate may see,
  so a runtime with no hooks, a different model or a `cat` can't reach more.
  Residual risks: a note that *becomes* private stays in mirror history
  (§6.3); normal content can still be sent anywhere (by design).
- **Writes:** none to `main`; proposals through an inbox (§7). No lease needed
  on GitHub.
- **Freshness:** as fresh as the last laptop sync (after every run, plus a
  cheap interval, §6.2). Reads keep working while the Mac sleeps, at the last
  synced state.
- **Upkeep:** one link per delegate; tokens per delegate. Free.
- **Engine work:** medium: a `GitHubHost` for `repo_sync.Host`, the delegate
  profile in `MirrorBuilder`, the reader, the inbox (§9).

### 5.3 Option C — one central vault host, agents call tools over remote MCP

A `scoutctl mcp` server exposes `kb_*`, `action_items_*` and `pre_session`
tools; agents connect to it over streamable HTTP. The server evaluates the
privacy policy with the reader fixed per credential.

Where it could run:

| Host | Cost | Freshness | When the Mac sleeps | Exposure |
|---|---|---|---|---|
| This Mac, over a free tunnel (Cloudflare Tunnel or Tailscale Funnel) | free | live vault | **down** | the live vault machine reachable from the internet through one process |
| A small VPS holding a clone of the delegate mirror | about USD 4–6/month (not free; Q3) | last sync | up | a server to patch; the mirror, not the vault |
| Agnes (already plans per-person repo hosting, an owner-only reader and MCP tools, Agnes §3, §6, §7) | none to Jordan | last sync | up | employer infrastructure; needs a scoped token Agnes doesn't have today (§11) |
| A free-tier always-on VM (e.g. a cloud provider's free micro instance) | free, needs a sign-up Jordan does himself | last sync | up | a server to patch |

- **Privacy:** the strongest boundary of any option when the server holds the
  live vault: the agent never sees a file, only tool results computed with a
  fixed reader. This is what privacy §12.4 requires for other people's agents
  in Stage 2.
- **Writes:** tool calls can append to the inbox (§7) or, on the Mac, run the
  action-item commands under the lease.
- **Upkeep:** highest: a long-running service, auth (bearer tokens or the MCP
  OAuth flow, depending on what each platform's client supports, §3), uptime.
- **Engine work:** high: a new server, a new dependency, an auth layer.
- **Coverage:** an MCP client gets tools, not skills or brain files; a run
  that wants SKILL.md still needs the files.

Verdict: keep as Phase 3, for a platform that can't run code. Never the only
path while the Mac sleeps.

### 5.4 Option D — a read-only snapshot export

`scoutctl kb export --reader delegate:<id>` writes a bundle (markdown tree plus
`labels.json`, or one SQLite file with FTS) that the laptop uploads to wherever
the platform keeps files.

- **Privacy:** same as B (same filter), no history problem (each export
  replaces the last).
- **Writes:** none.
- **Freshness:** at export cadence.
- **Upkeep:** low; depends on the platform having an upload API Scout can call
  unattended.
- **Engine work:** low: `scoutctl kb export` already exists (privacy §6.1,
  P2); it needs the delegate reader (V1) and an upload step per platform.

Verdict: the fallback for a platform with file storage but no git and no code.

### 5.5 Scorecard

| | A whole repo | B delegate mirror | C remote MCP | D snapshot |
|---|---|---|---|---|
| Local-only paths stay local | ✗ (history) | ✓ | ✓ | ✓ |
| Private notes stay off third-party hosts | ✗ | ✓ (default) | ✓ | ✓ |
| Boundary holds without Scout's hooks | ✗ | ✓ structural | ✓ server-side | ✓ structural |
| No second writer on `main` | ✗ | ✓ | ✓ | ✓ (no writes) |
| Works while the Mac sleeps | ✓ | ✓ reads, writes queue | only if not hosted on the Mac | ✓ |
| Full engine + skills in the agent | ✓ | ✓ | tools only | files only |
| Hosting cost | free | free | free on the Mac, else paid or a sign-up | free |
| Engine work | ~none | medium | high | low |

## 6. The delegate mirror in detail (Option B)

### 6.1 Building it from `repo_sync`

`repo_sync` already has every hard part: the union of local-only rules
(`LocalOnlyRules`), case- and Unicode-folded matching, a filtered tree per
vault commit (`MirrorBuilder.tree`), the generated `privacy-policy.json` with
labels, a pre-push re-check (`check_mirror_commits`), the trading and
widening-guard gates, and history that starts at link time. Changes:

1. **Hosts.** `Host` is a protocol with one implementation (`AgnesHost`).
   Add `GitHubHost`: `status/create` through `gh`/the REST API on a private
   repo the owner names; `set_local_only` is local state (GitHub has no list);
   `git_env` uses the delegate-sync credential (below); the `lease_*` methods
   are no-ops that return a laptop-only grant, because only the laptop ever
   pushes to a delegate mirror.
2. **Profiles.** A link records a **profile**: `full` (today's Agnes mirror:
   everything but local-only, labelled) or `delegate:<id>` (also drops
   private files and redacts mixed files, §4.4). `MirrorBuilder.tree` takes a
   per-file predicate from the profile; `check_mirror_commits` re-checks it.
3. **Many links.** `.scout-state/repo-sync.json` holds one link today. It
   becomes a map keyed by link name (`agnes`, `grok-personal`, …), each with
   its own commit map and `last_mirror`. Agnes stays one entry; nothing about
   it changes.
4. **No replay for delegate links.** `sync` replays mirror commits made on the
   host back into the vault. A delegate mirror refuses any commit it didn't
   make (`mirror_diverged`, already a refusal) and never replays: writes come
   through the inbox (§7).

### 6.2 When it syncs

- At the end of every scheduled run, after the run's own commit (a new
  post-session step in the runners; nothing syncs there today).
- On a launchd/cron interval (every 15 minutes) that exits at once when HEAD
  hasn't moved, so hand edits in Obsidian reach the agents too.
- `scoutctl repo sync --all` by hand.

A failed sync is a run-health event, not a run failure.

### 6.3 What the mirror can't take back

- **A note that turns private** leaves the mirror's HEAD on the next sync but
  stays in its history, on GitHub and in every clone. Same for a path added to
  `local_only_add` after link. The tools: `scoutctl repo relink --replace-remote`
  (force-push a fresh one-commit history, already a flag for Agnes), and the
  agent clones with `--depth 1` so a stale clone holds less. The privacy check
  that matters is upstream: a run can't widen a note (privacy §8.3), and the
  owner who marks a note private after it synced is told how many mirrors held
  it and offered the relink.
- **A delegate platform's own memory** may keep what the agent read. Scout
  can't reach into it. Q5 asks Jordan whether that is acceptable per platform.

### 6.4 Credentials

- **Laptop → mirror:** one fine-grained GitHub token, contents read/write on
  the mirror repos only, in `~/.scout-secrets/` like the Agnes PAT, never in
  the vault.
- **Agent → mirror:** per delegate, a read-only credential for its one mirror
  repo: a read-only deploy key, or a fine-grained token with contents:read on
  that repo only. Stored in the platform's secret store (§3).
- **Agent → inbox:** per delegate, write access to its own inbox repo only.
- Revocation: delete the deploy key or token; `scoutctl repo unlink <name>`.

### 6.5 The engine inside the agent

- **Install:** `uv tool install "scoutctl @ git+https://github.com/Raven-Scout/Scout@stable#subdirectory=plugin/engine"`
  (or pip), the base install with no `full` or `dev` extras (lean since #493). The engine version follows
  `stable`, like the marketplace. Agnes found that a sandbox without egress to
  PyPI can't install it (Agnes §8.2); a platform with that limit needs the
  wheel vendored into its image or the snapshot path (Option D). Grok Bot has
  allow-all egress, but drops virtual environments on every computer update
  (§3.1): the clone lives in `/workspace`, and `scoutctl delegate init` is
  idempotent and re-run at the start of a routine to rebuild the venv when
  it's missing.
- **Environment:** `SCOUT_DATA_DIR=<clone>`, `SCOUT_READER=delegate:<id>`
  (fixed by the bootstrap script, §6.6). No `SCOUT_MODE`: an agent task is not
  a scheduled run and must not try to write as one.
- **What works there:** every read command: `scoutctl kb
  resolve|files|read|search|query|related|traverse|path|name-lookup|delta|classify`,
  `scoutctl action-items list`, `scoutctl pre-session data`,
  `scoutctl bootstrap doctor`.
- **What is refused there:** every vault write (`action-items mark-done` and
  friends, `kb` writes, `bootstrap`, `schedule`, `repo`, `notify`,
  `self-update`, `phases backport`): with `SCOUT_READER=delegate:*` they exit
  with "this is a delegate checkout; write through the inbox" and print the
  inbox form of the same operation (§7.2). This is a guard against accidents,
  not the boundary; the boundary is that the agent can't push `main`.

### 6.6 Skills and brain files

- The mirror carries the vault's brain files (SKILL.md, DREAMING.md,
  RESEARCH.md) and the plugin's skills are in the engine checkout. A
  **delegate bootstrap** (`scoutctl delegate init`, run once in the agent)
  writes an `AGENTS.md` (and `CLAUDE.md` for Claude runtimes) at the clone's
  root that says: read the KB through `scoutctl kb`, never write the clone,
  propose changes through the inbox, and which brain-file sections apply.
- **Scheduled modes don't run in delegates.** Briefing, consolidation,
  dreaming and research write the vault, send the wrap and own the action
  items; they stay the laptop's (or Agnes's, once `runtime: remote` lands
  there). A delegate *asks* (scout-ask), *works* (scout-work's read side, with
  proposals for each action) and *contributes* (observations). Q2 asks
  whether Jordan wants more than that.

## 7. Writes: the inbox and the curator

### 7.1 Shape

One rule: **only the laptop's runs and the owner write the vault.** A
delegate writes proposals; the laptop applies them.

- **Inbox repo per delegate** (private, free). The delegate appends files under
  `inbox/<delegate-id>/<ulid>.json` and pushes to its own inbox repo. It can't
  push the mirror.
- **Kinds of proposal** (versioned JSON, one op per file):
  - `observation`: free text plus optional `about` (a KB name or slug the
    delegate saw), `source` (a URL or the conversation), `confidence`.
    Becomes input to the next consolidation, like a connector finding.
  - `action_item_op`: one of the existing action-item commands with its
    arguments (`mark-done`, `snooze`, `add-comment`, `set-estimate`,
    `set-block`, `clear-block`, `dismiss`, `watch`), addressed by stable id.
  - `kb_note_proposal`: a new note or a patch to a normal note, as a unified
    diff against the mirror blob it was made from.
- **Pickup:** `scoutctl inbox pull` fetches every linked inbox repo into
  `.scout-inbox/agents/<id>/` (already private by default, privacy §5.2), and
  a phase step in consolidation and briefing applies them:
  - `action_item_op` → the engine command, deterministically; a stale id or a
    no-op is recorded, not retried.
  - `observation` → the run reads them as one more source.
  - `kb_note_proposal` → the run applies the patch if the blob still matches,
    else three-way merges it like a brain file and, on conflict, files it in
    the review queue.
  - Applied files are deleted from the inbox repo by a laptop commit there.
- **Owner-only actions** (a proposal that would widen privacy, touch
  `scout-config.yaml`, brain files, or send anything) are never applied
  automatically: they go to the review queue.

### 7.2 Why not let delegates write `main`

- **The lease.** The Repository Writer Lease exists on Agnes only. GitHub has
  no lease API; a ref-based compare-and-swap is possible but is a third
  writer protocol to keep correct. With one writer there is nothing to lease.
- **The gates.** Every vault commit passes kb-lint and the widening guard in
  the pre-commit hook (privacy §8.3). A delegate's commit would run whatever
  hooks its runtime runs, which for Grok Bot is none of Scout's.
- **The redactions.** A delegate edits a redacted copy (§4.4); pushing it back
  would delete the redacted lines from the vault.
- **Precedent.** The same "curator is the sole canonical writer, other agents
  contribute observations through an inbox" shape is in Scout's own roadmap
  for a cloud Scout and a shared KB.

### 7.3 Latency

A proposal lands at the next laptop run that pulls the inbox: within the
consolidation cadence on a weekday, at the next wake when the Mac sleeps. For
action items that is usually fine (the daily file is re-rendered at each run).
A delegate that needs a write to show up faster can say so to the owner, who
can run `scoutctl inbox apply` by hand. If that's too slow, Phase 3's MCP
server on the Mac can apply action-item ops live while the Mac is awake.

## 8. Failure modes

| Event | Effect | Handling |
|---|---|---|
| Mac asleep or offline | Mirrors stop updating; inbox proposals wait | Reads keep working at the last sync; the agent sees the mirror's last-sync time in `pre-session data` and says so |
| GitHub down | Agents can't fetch; laptop sync fails | Run-health event; the laptop's runs are unaffected |
| A delegate's token leaks | Read of that delegate's mirror (normal notes only by default) and write to its inbox | Revoke the key/token; relink with `--replace-remote` if the mirror should be rotated |
| An inbox proposal is malicious (prompt injection on the platform) | It reaches the laptop run as untrusted input | Proposals are data, never instructions; `action_item_op` is a fixed command set; anything owner-only goes to the review queue; observations are cited with their delegate as source |
| A note turns private after it synced | It stays in the mirror's history | §6.3 |
| Two delegates propose conflicting ops | Applied in ULID order; the second becomes a no-op or a review-queue item | Recorded per op |
| The privacy check can't run at mirror build | Sync refused | Same fail-closed rule as a run's commit (privacy §8.3) |

## 9. Plan

Each phase is one PR or more, and starts only on Jordan's go.

| Phase | Ships | Done when |
|---|---|---|
| **V0 Answers** | Jordan answers §10; a manual check of the facts §3 marks unknown: Grok Bot clones a private repo with a token from its secrets store and runs `pip`/`uv`; Duet reads a private repo through the GitHub connector, and whether it accepts a custom remote MCP connector | §3 has no unknowns that change the plan |
| **V1 Delegate reader** | `delegate:<id>` in `scout/kb/reader.py`; every `kb` command filters private notes for it unless `privacy.delegates.<id>.include_private`; audit rows; the write-refusal in §6.5; `privacy-corpus.json` gains delegate cases (contract change: tell the Agnes indexer owners) | `scoutctl kb files` as `delegate:x` on the privacy fixture vault prints no `CANARY-PRIV-*` |
| **V2 Delegate mirror** | `GitHubHost`; link profiles; multi-link state; line redaction for mixed files (§4.4); `repo sync --all`; the post-run and interval sync hooks | A fixture vault links to a throwaway private repo; the mirror has no canary, no local-only path, and labels for every file |
| **V3 Delegate bootstrap** | `scoutctl delegate init` (AGENTS.md / CLAUDE.md, env, install check); docs per platform | A fresh Linux container with only the mirror clone and a token answers a scout-ask question through `scoutctl kb` |
| **V4 Inbox** | proposal schema; `inbox pull/apply`; the phase step in consolidation and briefing; review-queue routing | An `action_item_op` made in the container is applied by the next fixture run; an owner-only op lands in the review queue |
| **V5 (only if needed) MCP** | `scoutctl mcp serve` over a delegate mirror clone: read tools (`kb_resolve/files/read/search/query/related`, `action_items_list`, `pre_session`) plus `inbox_propose`. Stdio first (Grok Bot can start it by command); streamable HTTP for Duet, which as a claude.ai custom connector must be publicly reachable and needs the MCP OAuth flow or an authless URL (an unguessable path is weak auth, acceptable only for a normal-only mirror; Jordan decides, Q3) | Duet answers a scout-ask question through it |
| **V6 Live** | Jordan links his own delegates; the vault's trading paths are confirmed local-only first (privacy P5) | Jordan's delegates answer from the mirror; doctor shows mirror ages |

V1 is useful alone (it is the reader any later host needs). V2–V4 are the
recommendation. V5 waits for a platform that needs it. Tests follow the repo's
rules: affected tests only locally, fixture vaults anonymized, no test touching
`~/Scout` or the live scheduler.

## 10. Open questions for Jordan

1. **Platform facts** (§3): for each of grokbot and Claude duet, which of the
   unknowns in §3 are wrong? Especially: does it run shell and Python, keep
   files between tasks, hold secrets, and take local stdio or only remote MCP?
2. **Scope of "fullest":** read + propose (this spec), or should a delegate
   also run scheduled modes (briefings, dreaming) on its own platform? That
   would make it a second canonical writer and needs the lease on GitHub;
   recommended against unless Agnes is the host.
3. **Hosting for Phase 3**, if a platform needs remote MCP: this Mac over a
   free tunnel (down when asleep), a small VPS (about USD 4–6 a month), a
   free-tier VM you sign up for yourself, Agnes, or a host you already pay for?
4. **Private notes per delegate:** keep the default (normal only) for both?
   Grok Bot is xAI's infrastructure and model; Claude duet may be
   Anthropic's. Is a personal duet session close enough to "the owner's own
   run" to widen it?
5. **Platform memory:** is it acceptable that a delegate's own memory may keep
   what it read from normal notes, beyond Scout's reach?
6. **Normal notes that name a private note** (§4.4): strip those lines from
   delegate mirrors too, or leave it to lint?
7. **Team bots:** confirm that a team or shared-channel bot gets nothing
   (`agent:*`), as Agnes ruling 3 says, and that the Grok account holding
   your personal delegate has no Team Bot on it (§4.2: all Bots on an
   account share one computer).
9. **Duet's first path:** start with the GitHub connector reading its delegate
   mirror (no server), and build the MCP server only if that is too weak?
8. **One mirror per delegate or one shared "normal" mirror?** One per delegate
   costs a repo each but lets each be revoked and widened alone. Recommended.

## 11. Overlaps with the Agnes work (for the Scout-Agnes sub-coordinator)

Nothing here changes an Agnes plan. Points to weigh there:

- **Shared code, two hosts.** V2 generalises `repo_sync` from one link to
  many and adds profiles. The Agnes link stays profile `full`, one entry in the
  map, same behaviour. The refactor touches R1's code; the sub-coordinator
  should review that PR.
- **The delegate reader and the indexer.** `delegate:*` is a new reader in the
  privacy corpus. The Agnes indexer runs that corpus, so V1 is a contract
  change it has to accept (it can ignore delegates, but must not fail on them).
- **Agnes as the Phase 3 host.** Agnes already plans owner-only repository
  MCP tools. An external delegate calling them today would need an Agnes PAT,
  which carries the owner's full RBAC (no read-only or repository-only scope),
  and would be read as `RepositoryReader.owner`, private notes included. To
  host delegates, Agnes would need a repository-scoped, read-only token and a
  delegate reader that drops private notes. That is an Agnes design question,
  not a request.
- **Slack DM precedent.** The "normal notes only" default for delegates mirrors
  Agnes's Slack-DM ruling; if that ruling changes, this default should be
  re-asked.
- **`runtime: remote`.** If Agnes ends up running scheduled modes, it is the
  one place a second canonical writer is designed for (the lease). Delegates
  stay proposal-only either way.

## 12. Calls made in this spec (check while reading)

- A new reader class rather than reusing `autonomous` or `agent:*` (§4.1).
- Private notes off third-party platforms by default, following the Agnes
  Slack-DM ruling (§4.1).
- Mixed owner-only files are redacted line by line for delegates (§4.4).
- The laptop stays the only canonical writer; delegates propose (§7).
- GitHub as the free transport, one mirror and one inbox repo per delegate (§6.4, §10 Q8).
- MCP is a later, optional phase, not the core (§5.3).
