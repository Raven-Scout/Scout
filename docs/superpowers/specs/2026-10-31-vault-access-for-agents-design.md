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
| The phone-notification inbox (`.scout-inbox/**`) | **never** | never (Jordan, 2026-11-13) |
| Notes built from another person's shared note (#553 provenance, `source: shared:…`), and notes whose frontmatter can't be read | **never** | never, even when marked `visibility: normal` (Jordan, 2026-11-13): that content was shared with the owner, not with a third-party platform |
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

**As built (V2a, #632):** redaction runs on every kept text file, not only the
files above. This is the fail-closed answer to Q6, and Jordan may narrow it. It
matches against every note hidden from that delegate, not only private ones:
local-only notes, provenance notes and notes shared with the owner (their
citing form and quotes). A hit in the frontmatter removes the whole list item
or top-level key, so the YAML stays whole. A file that would no longer parse, or
whose label would change, is left out. A non-markdown text file other than
`.txt`/`.csv`/`.tsv`/`.jsonl` that hits is left out whole.

The review of #632 added these rules:
- **Content decides, not the extension.** Any file that decodes as UTF-8 is
  scanned as text. A file that doesn't is left out, unless it is a known binary
  type, which is kept on its label alone (Jordan to accept).
- **Paths are matched as well as content.** A hidden file's path counts as
  naming it. In a folder, the path matches as written. At the vault root, only
  the full file name (`health.md`) counts, so an ordinary-word note name blocks
  nothing.
- **Accents are optional** in the shared matcher, so `Rene` and `René` match
  each other.
- **The generated policy document and manifest** are not scanned for names. The
  pre-push check rebuilds them from the tree itself and compares.

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
      # account_unshared: not a key; as built it is `delegate link --confirm-account-unshared`, kept in link state (§4.2)
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

**As built (V2a, #632): a separate module, not a generalised `repo_sync`.**
Items 1–4 above assumed one sync engine for both hosts. A delegate mirror
needs none of the Agnes machinery (no lease, replay, host list or pending note),
so `scout/delegate_mirror.py` reuses `repo_sync`'s building blocks read-only:
`LocalOnlyRules`, the tree writer, the gates and `_stricter_policy`. It keeps its
own state (`.scout-state/delegate-mirrors.json`, keyed by delegate id) and refs
(`refs/scout-delegate/<id>`). The Agnes link's code, state and refs don't change.
Other differences from items 1–4:

- **One generic commit per sync** that changes the tree, not one per vault
  commit. It has a fixed identity and a fixed message, so no vault commit
  subject goes up.
- **The push is a compare-and-swap** on the remote's `main`
  (`--force-with-lease`).
- **The new tree is checked again before the push** (`check_tree`), separately
  from the build.
- **The remote** is a private GitHub repository, verified private through
  `gh api` at link and before every push (fail closed), or a local path.
- **Repository creation and dedicated credentials** are V2b.

### 6.2 When it syncs

- At the end of every scheduled run, after the run's own commit (a new
  post-session step in the runners; nothing syncs there today).
- On a launchd/cron interval (every 15 minutes) that exits at once when HEAD
  hasn't moved, so hand edits in Obsidian reach the agents too.
- `scoutctl repo sync --all` by hand.

A failed sync is a run-health event, not a run failure.

**As built (V2c, #636):**

- **After each run:** the runners call `delegate sync --all` after the
  connector-health roll-up. The step runs only in a vault with delegate links,
  and ends with `|| true`.
- **Between runs:** the five-minute schedule tick starts
  `delegate sync --all --if-moved` detached, at most every 15 minutes, and only
  when a mirror may be behind: HEAD moved, its last sync failed, or a push
  didn't finish. This needs no new launchd or cron job.
- **Outcomes:** each sync records `last_attempt` on the link, and `last_error`
  until a sync succeeds. It also writes the event `delegate_mirror.synced` or
  `delegate_mirror.sync_failed`.
- **Run health** turns yellow on `delegate.sync_failed`.
- **`bootstrap doctor`** notes each mirror's age, and warns on a failed sync,
  an unfinished push, or a mirror behind HEAD that no sync has tried for an
  hour.

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

**As built (V2b, #633):**

- `scoutctl delegate link <id> --create-repo owner/name` makes the private
  repository with the owner's `gh` login. It runs only after the checks that
  don't need the repository have passed.
- `scoutctl delegate key <id>` makes an ed25519 deploy key with
  `read_only=true`. It refuses, and deletes the key, if GitHub records it as
  writable. The private half goes to `~/.scout-secrets/delegate-mirrors/`. The
  command prints the agent's `git clone --depth 1` line. `--rotate` and
  `--revoke` replace or delete the key.
- `delegate unlink` revokes the key first, and keeps the link if GitHub won't.
- The laptop's narrow token is optional: `delegate push-token`. Git gets it
  through `GIT_CONFIG_*` in its environment, never its arguments. Without it,
  the laptop pushes with git's own login.
- The agent → inbox credential is V4's.

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

**As built (V3, PR to follow):**

- **The guide is part of the mirror, not written by `init`.** Every mirror carries
  Scout's guide for its agent as its root `AGENTS.md` and `CLAUDE.md`, in place
  of the vault's own. A Scout vault's root `CLAUDE.md` is for the laptop's
  scheduled runs and tells them how to write. In a mirror it would mislead the
  agent, and a file `init` wrote at the same path would collide with it on the
  next pull. The guide is fixed text and the delegate's id. The pre-push check
  regenerates it and compares bytes, as it does for the policy document.
- **`scoutctl delegate init`** runs in the agent, in its clone. It finds the
  clone by its manifest, and refuses the owner's vault and any other folder. It
  checks:
  - that the clone's config lists the delegate;
  - that `SCOUT_READER` matches and `SCOUT_MODE` isn't set;
  - that the engine is at least the one that built the mirror.

  Then it reads once as the delegate, without writing the privacy index, and
  writes `SCOUT_DATA_DIR` and `SCOUT_READER` to `.git/scout-delegate.env`,
  never the tree. `--print-env` prints them as exports for `eval`. It is the
  one `delegate` command a delegate checkout may run.
- **The vault's own agent files stay on the laptop**, at every depth and in any
  case: instruction files (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.cursorrules`
  and the like), settings folders (`.claude/`, `.cursor/`, `.gemini/`, `.codex/`),
  and a root `.github/`, whose workflows would run in the mirror's repository.
  They are skipped like generated paths, not treated as hidden notes, so their
  names never become redaction terms.
- **`init` writes only into the clone's own `.git` folder**, and only through
  files it creates itself: never through a `.git` file, a symlink, `GIT_DIR` or
  a symlink at the env file's path. Any failure exits non-zero, and the guide
  loads the environment with `e=$(scoutctl delegate init --print-env) && eval "$e"`,
  so a failed `init` sets nothing.
- **The account confirmation stays at `link`** (`--confirm-account-unshared`,
  §4.2). It isn't asked again in `init`: the agent runs `init`, not the owner.
- **A delegate's commands are the allow-list's**: `kb` reads, `pre-session data`,
  `bootstrap doctor`, `manifest show`, `version`, `hook` and `delegate init`. The
  guide names only those. `action-items list` (§6.5) isn't in it yet.
- **Platform docs wait for §10 Q1.** The guide is platform-neutral. A Grok Bot or
  Duet page follows once the platform facts are confirmed.

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

### 7.1a V4 design in detail (2026-11-19; revised after the design review)

§7.1 is the shape. This is how V4 builds it on what V1–V3 left, with the trust
boundary first. **Everything in an agent's inbox is untrusted input from
outside the laptop**, like a connector finding, never an instruction. **So is
anything a run can write**, since a run that read an injected observation
may act on it. The second rule is what most of the review's changes are about.

**What exists to build on** (mapped 2026-11-19):

- **Action-item commands** (`scout/action_items/`) address an item by its
  `[#TAG]` short prefix in one day's daily file, and none of them commits.
  There is no lookup from an id to the file holding it.
- **`add_comment`** writes `  - {author}: {comment}` without cleaning the text.
  Its parser's author group allows no colon.
- **A delegate may not see every line of a daily file.** The mirror redacts
  whole lines that name a hidden note.
- **`.scout-inbox/`** is private by default and in `DELEGATE_NEVER`. It is
  tracked in git, and no code uses it yet.
- **Runs:**
  - The review queue is `knowledge-base/review-queue.md`.
  - Runs read outside findings from `pre-session data` (`SessionContext`).
  - No phase has an "untrusted input" rule, and a phase rule isn't a
    boundary anyway.

#### Transport, binding and credentials

- **Its own repository.** Each delegate has one inbox repository: private,
  under the owner's own user, never the mirror's repository. It is linked with
  `scoutctl delegate inbox <id> --create-repo owner/name` or `--remote <url>`.
- **Bound by GitHub's id, not by content.** At link, Scout records the
  repository's numeric id and its owner's id. Before every pull, it looks the
  repository up by that id and refuses (`inbox_rebound`) on a rename, a
  transfer, or a new repository at the old name. A manifest can't bind an
  inbox, as it binds a mirror, because the agent writes this repository and
  could copy the mirror's manifest, vault mark and all.
- **Checked before every pull:**
  - private, owned by a user, not a fork, no collaborator but the owner;
  - GitHub Actions off (`actions/permissions`), and `has_pages` false;
  - no other link or mirror uses it.

  At link, Scout warns about GitHub Apps installed on all of the owner's
  repositories. Scout turns Actions off when it creates the repository, and
  refuses an inbox with Actions on (`inbox_actions_on`).
- **The agent writes with its own key.** It is a deploy key with write access on
  the inbox repository only, titled `scout-delegate-inbox-<id>`, made by
  `delegate key <id> --inbox`. Scout refuses unless GitHub records it as
  writable. The mirror key stays read-only. The V2b title checks apply before
  any deletion.
- **What that key can do, and the guard on it.** A deploy key can push any
  branch or tag, workflows included, and force-push or delete `main`, and a
  free private repository has no branch protection. So:
  - Actions stays off;
  - Scout reads only `main`;
  - Scout records the sha of each of its own removal commits, and refuses
    (`inbox_rewritten`) a `main` that doesn't descend from it.

#### The agent's side (allowed for a delegate checkout)

- `scoutctl inbox propose observation --text … [--about <name>] [--source <url>]`
- `scoutctl inbox propose action <op> --tag <TAG> --date <YYYY-MM-DD> [op args]`

Each writes one file, `inbox/<id>/<ULID>.json`, into the agent's inbox clone
and commits it there; `--push` pushes it. `delegate init` learns where that
clone is. The format is `schema_version: 1`, with fixed fields only
(`scout/delegate_proposals.py`):

- `id` is a canonical ULID: 26 uppercase Crockford characters, equal to the
  file name.
- `delegate` and `kind`, plus the kind's own fields.
- `made_at` is the agent's own claim. It is shown, and never used for order or
  for judging.

**One-line text only, in anything that reaches the vault:**

- No control or bidi characters. No `[[` or `[#`, so a proposal can neither
  link a note nor name a task.
- A comment is capped at 1000 characters. It is written with author
  `delegate-<id>`, which `add_comment`'s parser already reads. A colon in the
  author would fail the parser, and changing that regex is a parser-corpus
  change that needs the app.
- An observation's `text` may span lines, since it never reaches a daily file
  as written (V4b), but it follows the same character rules.

#### `scoutctl inbox pull` (the laptop)

**A fresh, locked-down fetch on every pull, outside the vault.**

- A new bare repository each time, in a temporary folder outside the vault,
  made with `git init --bare --template=` and removed afterwards. Never under
  `.scout-state/`, where a hijacked run could plant `core.sshCommand` or a
  reference-transaction hook to run outside the sandbox.
- git runs with:
  - `-c core.hooksPath=/dev/null -c transfer.fsckObjects=true`;
  - `-c protocol.allow=never` with `https` or `ssh` (only the one in use)
    allowed again;
  - `-c submodule.recurse=false`;
  - `GIT_NO_LAZY_FETCH=1` and `GIT_NO_REPLACE_OBJECTS=1`;
  - a timeout and an `RLIMIT_FSIZE` cap.
- The fetch is `--no-tags --no-recurse-submodules --depth=1
  --filter=blob:limit=16k`, of `main` only. A missing blob means the file was
  over 16 KiB, and it is refused as `too_big`.
- **Checking `main` still builds on Scout's last removal.** A one-commit fetch
  can't show that, so the fetch deepens (`--deepen`, blobs still filtered) in
  steps until it reaches the recorded removal commit, at most 256 commits
  back. If that commit isn't among them, `main` was rewritten or ran too far
  ahead, and the pull refuses (`inbox_rewritten`), taking nothing.
- **Recovering from `inbox_rewritten`.** The owner runs `scoutctl delegate inbox
  <id> --reset`, at the keyboard. It shows the current `main` and, once
  confirmed, takes it as the new base, without pushing anything. The proposals
  already recorded still dedupe, so nothing is applied twice. A `push --force`
  by hand doesn't help: the next pull would refuse it the same way.
- **Nothing is ever checked out.** Entries are read with `ls-tree -z` and
  `cat-file` only. A path, a symlink, a case or NFKC collision, or a name that
  isn't UTF-8 never touches the disk.

**What is read:**

- Only entries at `inbox/<id>/<ULID>.json`, mode `100644`, at most 16 KiB.
- They must parse under the schema. A ULID whose time is more than 10 minutes
  ahead of the laptop's clock is refused (`future_id`).
- At most 50 per pull, in ULID order, counting only entries not already
  recorded. An entry already recorded is only removed again, so an agent
  that keeps pushing can't stall the inbox.
- Anything else in the tree is counted and ignored. "No `main`" is reported
  apart from "empty inbox".

**Taking a proposal, crash-safe and unforgeable, in this order:**

1. **The signed "taken" record** is `.scout-state/delegate-inbox.json`, signed
   with the state key and listed in `hook_paths.protected_specs` (beside
   `delegate-mirrors.json` and `delegate-scan/`), so a run can't write it. It
   records each ULID and an HMAC of the proposal's bytes; a refused entry is
   recorded with its reason. The same signed file holds:
   - each delegate's inbox binding and the sha of its last removal commit;
   - the ULID-time high-water mark;
   - every applied op's prior state, for `inbox revert`. Revert is the real
     risk: a run that could edit that state would get its own text written
     back by the owner's revert.
2. **The copy.** A valid proposal goes to `.scout-inbox/agents/<id>/<ULID>.json`.
3. **The removal.** A compare-and-swap commit on the inbox's `main` removes
   every entry taken or refused, so a refused one isn't read again. It has a
   fixed identity and message and carries no outcomes, since the agent reads
   the inbox. Its sha is recorded. If the agent pushed in between (the
   compare-and-swap loses), the pull fetches again and retries once;
   otherwise the removal waits for the next pull. Entries already recorded
   are never taken twice either way.

A crash between steps leaves an entry that the next pull recognises by its
record, so nothing is lost or taken twice.

**Dedup across computers.** The signed record is per computer. So pull also
dedupes against a committed ledger of taken ULIDs,
`.scout-inbox/agents/<id>/taken.ledger`, and against a per-delegate
high-water mark of ULID time. That covers a new laptop, or state rebuilt by
`trust-state`. The ledger is add-only and used only to skip: Scout never
removes a line from it, and never applies anything because of it. At worst a
run that writes it hides proposals from being taken, which the counts in
health show.

**The runner commits the pull, never the run.** It runs as a step outside the
run's sandbox, like the other runner steps.

**As built in V4a1 (#718).** These changes were made while building:

- **The base is the newest `main` checked**, not only Scout's last removal
  commit. Each `main` was checked to descend from the base before it, so the
  guarantee is the same. Without this, an inbox with nothing to remove would
  run past the 256-commit window.
- **Refusals are kept as the last 100 per delegate**: a hash of the path,
  the code and the time, plus running totals per code. They aren't kept per
  ULID, so an agent pushing junk can't grow the signed file.
- **At most 200 taken per inbox per UTC day**, as well as 50 per pull. What
  waits stays in the inbox. Fifty every five-minute tick would otherwise
  flood `.scout-inbox/agents/`.
- **`stale_id`.** A ULID more than a day behind the newest one taken is
  refused and removed, not silently skipped.
- **The copies' folder and the ledger are checked before anything is
  recorded.** A symlink, a file or a hard link in the way refuses
  (`inbox_folder_unsafe`), and the proposal stays in the inbox. If a
  proposal was recorded but its copy never landed, the next pull copies it
  again, provided its bytes still match the recorded HMAC.
- **Only `inbox/<id>/` is judged and removed.** Anything else in the tree
  stays where it is: another delegate's folder, `.github/`, or `INBOX/` in
  another case.
- **The agent's side.** `delegate init --inbox <clone>` puts
  `SCOUT_INBOX_DIR` in the env file, and later runs remember it. `inbox
  propose --push` rebases on the inbox's `main` before pushing. The agent
  only ever adds files, so that rebase can't conflict.
- **A local bare inbox (for tests) is bound by its file identity and a random
  token** written into it at link, where a GitHub inbox is bound by GitHub's
  ids. Linux reuses inode numbers, so a repository deleted and made again at
  the same path would otherwise pass.

**After the security review of #718.** These changes came out of the review:

- **The high-water mark is per computer.** It lives in the signed state, so
  it gives no dedup across computers, contrary to what this section said
  above. Only the committed ledger does that. On a new computer the ledger
  alone keeps a proposal from being taken twice. `--reset` clears the mark,
  so a poisoned one (a far-future ULID) can't refuse every later proposal.
- **All the inbox's local state is one folder**, `.scout-state/delegate-inbox/`:
  the signed state, its temporary files, the lock and each pull's throwaway
  repository. The folder is 0700 and owner-checked, it is reached by a
  no-follow walk, and `hook_paths` protects it as a tree. Each pull's
  repository is made there, not in `$TMPDIR`, where a run could repoint a
  ref or plant a pack.
- **No input wedges a pull.** `parse` raises only `ProposalError`; any other
  failure is `bad_entry`. A ULID must start with 0-7, since 26 characters
  hold 130 bits and a ULID only 128. Every pattern ends at `\Z`, so a
  trailing newline can't alias a ULID.
- **Text can't speak for the owner.** Besides `[[` and `[#`, a text field may
  not hold `<!--`, `-->`, `![`, `](`, a task checkbox at a line's start, or
  the owner-directive marker (`//==<<` or `>>==//`, matched with whitespace
  removed). No control, format, surrogate, private-use or unassigned
  character is allowed either, nor an invisible mark such as a variation
  selector or a tag character.
- **Any deploy key but the agent's refuses** (`inbox_other_key`), since its
  holder could write proposals as this delegate.
- **The listing.** Only `inbox/<id>/` is listed, streamed, at most 2000
  entries a pull. The removal commit rebuilds only the trees on the removed
  paths, with `mktree`, never an index of the whole tree.
- **The fetch.** The remote has a random name, so no `remote.<name>.*` in the
  user's config applies, and the only credential helper is gh's. `pull`
  refuses with `SCOUT_MODE` set: the runner pulls, outside any run. A timeout
  kills git's whole process group.

#### `scoutctl inbox apply` (action items)

- **It acts only on what the signed record took.** A copy in
  `.scout-inbox/agents/` is applied only when its ULID is in the signed
  "taken" record and its bytes match the recorded HMAC. A run can write that
  folder, so a planted or edited copy is refused (`not_taken`).
- **Ops:**
  - `mark-done`, `snooze`, `add-comment`, `set-estimate`, `set-block` and
    `clear-block`, through the existing commands, addressed by id only
    (never `by_subject`);
  - `dismiss` and `rename-topic` aren't addressed by id, so they wait for the
    review queue (V4b).
- **The delegate must have seen the item.**
  - The commit is the delegate's signed `last_mirror`, or its signed
    `pending_mirror` (a push the agent may already have seen before
    `_adopt_pending` caught up), never what the remote says. The item counts
    if either holds it.
  - With no mirror commit at all, the outcome is `no_mirror`, which is
    retried at the next apply, not final.
  - Scout parses that date's daily file in that commit with the action-items
    parser, and requires a task whose short prefix is the TAG. A substring
    match isn't enough, since a TAG can appear in a visible comment.
  - At apply time, Scout redacts the current vault line again, and the item
    must still be visible.
  - Otherwise the outcome is `unknown_item`, the same whether the item is
    hidden or missing.
- **Limits:**
  - per-op daily caps per delegate;
  - a breaker that sends a burst (for example, a run of `mark-done`) to the
    review queue instead of applying it;
  - a maximum snooze horizon, since a snooze to 2099 is a dismiss.

  Fifty proposals per pull on a five-minute tick would otherwise be about
  14,000 a day.
- **Undoable.** Each applied op records the line's prior state, so
  `scoutctl inbox revert <ULID>` restores it. The briefing lists what each
  delegate did ("done by delegate-grok-personal").
- **Outcomes are recorded by ULID,** never with the proposal's text:
  `applied`, `no_op`, `stale`, `unknown_item`, `limited`, `not_taken`,
  `refused`, and `no_mirror` (retried).

#### Observations (V4b), decided now because a phase rule isn't a boundary

- **No sending from a session that reads them.** A session that reads agents'
  observations may not also send anything without the owner's review: no
  wrap, no notification, no Agnes share. Observations go to runs that don't
  send, or their sends wait for review.
- **No spreading.** KB text that comes from an observation carries
  `source: delegate:<id>`. Every delegate mirror leaves it out until the owner
  accepts it, so one agent's injection can't reach another agent.
- **An `include_private` delegate's** observations default to private.
- **Shown only as plain text.** Text an agent wrote is rendered as plain text
  wherever it shows:
  - the app's DOMPurify allows only `http`, `https` and `mailto` links, and no
    remote images;
  - Telegram HTML is escaped;
  - Slack's `<!channel>`, `<@U…>` and `<url|text>` are escaped.
- **Agnes.** Agnes's full mirror leaves out `.scout-inbox/agents/`, or marks it
  untrusted (a point for the Agnes sub-coordinator).

#### Slices

- **V4a (first PR):**
  - the inbox link with id binding, and the `--inbox` key;
  - the schema;
  - `inbox propose`;
  - `inbox pull` (the locked-down fetch, the signed record, the removal,
    dedup);
  - `inbox apply` for action items, with authorization, limits and revert.

  The tests use local bare repositories and a fake `gh`, including hostile
  inboxes: oversize, wrong path, a symlink, a case collision, a wrong delegate
  id, bad JSON, a workflow file, a tag, a force-pushed `main`, a flood, a
  planted copy, and a TAG the delegate can't see or only sees in a comment.
- **V4b:**
  - the runner pulls and applies outside the sandbox before a run;
  - `pre-session data` gains `agent_observations`, marked untrusted, read
    only by runs that don't send;
  - `dismiss`, `rename-topic`, bursts and anything owner-only go to the review
    queue;
  - "done by delegate" in the briefing;
  - health and doctor show inbox age and refused proposals;
  - the rendering rules above.
- **V4c:** `kb_note_proposal`, as a diff against the mirror blob it was made
  from, always to the review queue, never applied automatically.

### 7.1b V4b design (2026-11-19, after V4a merged as #718 and #725)

V4a gave the laptop `inbox pull`, `apply` and `revert`, all run by hand. V4b
makes the inbox run by itself and gives the owner one place to see what agents
sent. It was mapped against the engine as it is on main. Five facts from that
mapping change the plan in §7.1a:

- **A run can't host apply.** Every `run-*.sh` exports `SCOUT_MODE` and
  `SCOUT_READER=autonomous`, and holds `.scout-logs/.scout-session.lock` from
  start to exit. `kb/live_run.commit_source` also counts any commit whose
  process ancestry reaches the runner as the run's own. `apply` refuses all
  three by design (#725), and must keep doing so.
- **Every scheduled run sends.** Each runner prompt ends with the wrap (Slack,
  Telegram, the Digest). No run "doesn't send", so §7.1a's "observations go
  to runs that don't send" has nowhere to go.
- **`knowledge-base/review-queue.md` is the wrong home.** It is free prose,
  written by the model. Every wrap reads its pending count, and as a normal
  note it goes to every delegate mirror and to Agnes. One delegate's text
  there would reach the next run's sends and every other delegate.
- **Pulled copies are in limbo.** Nothing commits, ignores or keeps local
  `.scout-inbox/agents/`. Committed, it would sync to Agnes: `.scout-inbox/**`
  is only `visibility: private`, not local-only. An `autonomous` run can read
  it.
- **The run gate doesn't know the inbox.** `live_run._DELEGATE_WRITES` names
  link, unlink, key and trust-state, not `inbox pull`, `apply` or `revert`.

**The rule V4b keeps: agent text never reaches a run.** What runs see from the
inbox is ids, ops, tags, dates and commits, read from the signed state. Agent
text (observations, comments) goes only to the owner, who decides what, if
anything, becomes vault content.

#### The tick step: `scoutctl inbox sync`

- **Where it runs.** `schedule_tick._do_tick` starts it beside
  `_sync_delegates`, detached, the same way as `_spawn_delegate_sync`:
  - its working folder is outside the vault;
  - `SCOUT_MODE` and `SCOUT_READER` are both removed, so it runs as the owner;
  - it is gated on `.scout-state/delegate-inbox/state.json` existing.

  It never runs inside a run.
- **What it does,** in order, under the session lock (a live run makes it wait
  for the next tick, `busy`, which is not an error):
  1. `pull --all`.
  2. A commit of the pull, through `git-safe-commit.sh`, as
     `inbox: pull from delegate-<id>`. It commits only `taken.ledger`; the
     copies stay uncommitted (below).
  3. `apply --all`. Each change is its own commit, as in #725.
- **Agnes.** In an Agnes-linked vault (`.scout-state/repo-sync.json`) it takes
  the repo lease (`scoutctl repo lease acquire`) for its commits, or skips this
  tick.
- **Pace.** It runs at most every 15 minutes, like the mirror sync (`due`), and
  records events through `schedule_tick._emit_event`.

#### Where agent text lives: the owner's inbox, never the vault

- **Copies stay on this computer.** `.scout-inbox/agents/*/*.json` is
  gitignored, and `.scout-inbox/agents/**` goes under `local_only.always`
  (`defaults/privacy.yaml`, with the snapshot regenerated). Copies are never
  committed, so never synced to Agnes or another clone. The ledger, which
  holds ULIDs only, is committed, for dedup across computers.
- **Runs can't read copies.** The run read gate (`hooks/kb_read_gate.py`)
  denies `.scout-inbox/agents/` to every run (Read, Grep, Glob, and a Bash
  command that names it), not only to delegate readers.
- **The run gate refuses the inbox.** `inbox pull`, `apply` and `revert` join
  `_DELEGATE_WRITES` in `kb/live_run.py`.
- **`scoutctl inbox review`** (owner only, `headless_gate`) lists what waits
  for a decision:
  - held `add-comment` ops;
  - `limited` ops on their last day;
  - observations;
  - `dismiss` and `rename-topic`, once the schema allows them (V4c).

  The agent's text is printed as plain text: control characters are already
  refused by the schema, and the terminal output is never interpreted.

  `inbox review accept <ULID>`:
  - **A comment** is written with author `delegate-<id>` by the owner's own
    hand, in a commit naming both.
  - **An observation** becomes a note at `knowledge-base/from-agents/<id>/<ULID>.md`,
    with `source: delegate:<id>` and `visibility: private` (§7.1a: no
    spreading). The privacy classifier gains `delegate_source`, which
    `visible_to` hides from every delegate, the author included.

  `inbox review reject <ULID>` records the decision. Both outcomes are
  recorded by ULID in the signed state.
- **The app** shows the same list later, through `inbox review --json`, as
  plain `Text` (no markdown, no links). That is a separate app PR, for the app
  session that owns the agent-copies card.

#### What runs do see, all of it from the signed state

- **Done by a delegate.** `pre-session data` gains `delegate_changes`: for each
  op applied since the last run, the delegate id, op, `[#TAG]`, the date and
  the commit. It holds no agent text.
- **The close-out rule.** `phases/core/action-items.md` (the rule at about
  :335) gains a sub-rule: a `[x]` that `delegate_changes` names gets
  `Completed by: delegate-<id>` with that commit as evidence. It is never
  taken as the owner's.
- **The wrap** may count "N done by agents". The count is a number, so no
  escaping is needed.

#### Health, doctor and status

- **`health.delegate_inbox_findings`.** It is gated on the inbox state, like
  `delegate_findings`. It warns on:
  - an inbox not pulled in a day, or failing (`last_error`);
  - copies waiting on review for more than a week;
  - `dirty`, `busy` or `stale` counts above a floor;
  - untaken copies (planted files).

  A vault with no inbox has no finding, so the doctor contract fixture is
  unchanged.
- **The doctor** adds `_check_delegate_inboxes`, beside
  `_check_delegate_mirrors`.
- **`delegate status --json`** gains each inbox's last pull, error and counts,
  for the app's agent-copies card.

#### Smaller fixes in V4b

- **Only today's daily file.** `apply` acts only on today's file (as
  `retitle._require_today` does); a proposal for another date is `stale`.
  Consolidation carries open items forward into today's file, so a change to
  an older file would be lost or applied twice.
- **Events.** Each applied change appends an event,
  `source: delegate:<id>`, holding ULID, op, tag and commit only, to
  `schedule-events-*.jsonl`. It is telemetry, not proof: `.scout-logs/` is
  run-writable. The proof is the signed state and the commit.
- **Agnes.** `.scout-inbox/agents/**` also goes into Agnes's own
  always-local list (`repo_sync.HOST_ALWAYS_LOCAL`, mirrored from Agnes). That
  is a note for the Agnes sub-coordinator.

#### Slices

- **V4b1 (engine, one PR):**
  - the tick step and `inbox sync`;
  - gitignore and local-only for the copies;
  - the read gate and run gate;
  - today's file only;
  - events;
  - `delegate_changes` and the close-out sub-rule;
  - health, doctor and status.
- **V4b2 (engine):** `inbox review` (list, accept, reject), the
  `delegate_source` classifier fact, and the `from-agents/` notes.
- **V4b3 (app):** the review list in plain text, for the app session.

**Decided by the owner** (relayed 2026-11-19 from a side session): an
accepted observation keeps the default, a private note with
`source: delegate:<id>`, hidden from every delegate mirror, the author's
included. So that agents' shared memory isn't cut off for good, V4b2 also
adds a separate, deliberate **promote**:

- **The command** is `scoutctl inbox review promote <ULID>`, with an app
  button later. It is the owner's decision, per item (`headless_gate`).
- **Before promoting,** the full text rules run again, and the text is shown
  with every hidden character made visible.
- **Promoting** drops the `delegate_source` mark (the provenance is
  rewritten), so the note follows the normal visibility rules and can reach
  mirrors, the author's included. An optional scope limits a promoted note to
  its author or to named delegates.
- **The signed state** records the promotion by ULID.

Unreviewed text still never spreads. Reviewed text spreads only by a
deliberate decision of the owner's, item by item.

**After the coordinator's review of this plan (2026-11-19).** These conditions
were added, and V4b1 builds them in:

- **The sync never makes a run skip.** It pulls without the session lock (the
  inbox lock is enough), and holds the session lock only to commit and apply,
  starting no new work after 60 seconds. A runner that fires meanwhile waits
  (`SCOUT_LOCK_WAIT_SECS`, 30 minutes) and then runs. The tick doesn't start
  a sync within 5 minutes before a slot's time.
- **The Agnes lease is the Agnes sub-coordinator's to settle.** Until then, an
  Agnes-linked vault pulls but commits and applies nothing.
- **What accepting means (V4b2).** Accepting writes agent text into the vault:
  a comment into the daily file, an observation into
  `knowledge-base/from-agents/`. After that, runs **will** read it. That is
  acceptable only because the owner reviewed it, and it is the one way agent
  text reaches a run. So:
  - accept re-runs the same text rules as `parse` (no directive marker,
    comment markers, task checkboxes, links or markup, and no format,
    surrogate, line-separator or invisible characters), since it writes the
    exact stored text;
  - the owner is shown the text with any such character made visible before
    accepting.
- **The copies are kept from runs at three layers:** the read hook, the write
  hook, and, on macOS, the run's sandbox, which denies reads
  (`file-read-data`) and writes under `.scout-inbox/agents/`. Elsewhere the
  hooks are the only layer.
- **What V4b1 doesn't build.** `delegate status --json` gains its inbox data
  in V4b3, with the app card that reads it, in one commit with its contract
  fixture and app copy.

**After the review of #735.**

- **Undo has a window.** `inbox revert` restores a task only while it reads as
  the op left it. Once the next run's close-out moves a delegate's `[x]` into
  Recently Completed (as it must), the row has changed, and revert refuses
  (`revert_conflict`). Then the owner reopens the item by hand. The
  `delegate_changes` entry and the commit name what the agent did.
- **On Linux, the hooks are the only layer.** The hooks keep runs off the
  copies on every platform, but a hook judges a path by its spelling: a
  symlinked path, or a spelling the shell prefilter doesn't send to the gate,
  can pass. On macOS the run's sandbox denies the folder in the kernel, and
  that is the real layer. Elsewhere the copies are as safe as the hooks.
- **Health and the doctor inside a run.** A run can't read the state's signing
  key, so they skip the delegate checks there ("checked outside runs"); the
  owner's terminal and the app check them. No finding ever advises moving the
  signed state aside: revert needs its records.
- **A killed apply.** A change a crash kept from being committed is committed
  by the next sync under its delegate's name, when it is the only change in
  the file. Otherwise it is left for the owner, with a note, since the next
  run's commit would take it as the run's own.
- **The lease** is renewed once, between the ledger commit and apply. On the
  way out the local lease file is dropped first, then Agnes is asked to
  release it.

#### V4b2 plan: the owner's review (2026-11-19, for the coordinator's review)

**Reading what waits.** `scoutctl inbox review [--json]` is the owner's only
(`headless_gate`). It lists, per delegate:

- held `add-comment` ops;
- observations;
- `limited` ops still within a week.

Each item shows its ULID, kind, op, `[#TAG]` and date, and its text with every
character outside printable ASCII and ordinary Unicode letters shown as
`\u{…}`. The schema already refuses hidden characters, so this is a second
look, not the gate. Each copy is read with `_read_copy` and must match the
signed record's HMAC; a copy that doesn't is never listed.

**`inbox review accept <ULID>`.** It asks to type the ULID at a terminal, or
takes `--confirm`, and re-runs every text rule (`parse` on the copy's exact
bytes) before writing anything.

- **A comment** goes through `add_comment` with author `delegate-<id>`. It
  takes apply's checks: today's file, seen in the mirror, still visible, a
  plain and committed file, the session lock and the inbox lock. It gets one
  commit, `inbox: add-comment [#TAG] from delegate-<id> (<ULID>), accepted by
  the owner`, and the outcome `accepted` with the block's before and after,
  so `inbox revert` works for it.
- **An observation** becomes
  `knowledge-base/from-agents/<id>/<ULID>.md`, written with `safe_write`. Its
  frontmatter comes from `yaml.safe_dump`, so no field can break out of it:
  - `title` (from `about`, or "Observation from <id>");
  - `source: delegate:<id>`;
  - `visibility: private`;
  - `accepted_at`;
  - `agent_source` (the observation's own `source`, as a plain string).

  The body is the text. It is one commit naming the delegate and the owner,
  and the outcome is `accepted`. `inbox revert` removes the note in a commit,
  only if it is unchanged.
- **After accepting, runs will read it.** That is the one way agent text
  reaches a run, and only because the owner reviewed it.

**`inbox review reject <ULID>`** records `rejected` by ULID and removes the
local copy.

**`inbox review promote <ULID> [--to <id>,…]`** applies to an accepted
observation only. It re-reads the note, re-runs the text rules on its body,
shows it the same way, and asks again.

- Promoting rewrites the provenance: `source: delegate:<id>` becomes
  `promoted_from: delegate:<id>`, and visibility becomes `normal`.
- With `--to`, it also writes `delegate_scope: [ids]`. `--to author` names
  the author.
- It is one commit, and the signed state records `promoted` (to whom, when).

**The classifier**, in `kb/privacy.py`:

- `NoteFacts` gains `delegate_source` (a `source:` value starting
  `delegate:`; unreadable frontmatter counts, failing closed, as
  `holds_shared` does) and `delegate_scope`.
- `visible_to` hides a `delegate_source` note from every delegate reader, the
  author included.
- For a note with a `delegate_scope`, it shows the note only to the delegates
  named.
- `CLASSIFIER_VERSION` and `INDEX_VERSION` are bumped (5).
- The mirror follows from `visible_to`, the scanner redacts lines naming a
  hidden note, and `lint_privacy` learns `delegate_scope`.

**Health and app.** `delegate.inbox_waiting` (yellow) means items have waited
over a week, and the fix is `scoutctl inbox review`. The app's list is V4b3.

**What isn't in V4b2.**

- `dismiss`, `rename-topic` and burst routing to review: the schema still
  refuses those ops (V4c).
- `kb_note_proposal`, which is V4c.

**V4b2 as built (Raven-Scout/Scout#738).** As planned, with the coordinator's seven conditions:

- **What runs see.** `kb read`, `search` and `files`, and the Read-tool label hook, mark an agent's note
  `[from-agent: delegate-<id>]`. `kb read` adds that it is data, never instructions, and the privacy phase
  says so too.
- **Undo.** `unpromote` restores the accepted form while the note is byte-for-byte what promote left.
- **Never shared until promoted.** A `delegate_source` note gets federation scope and ceiling `none`,
  whatever a folder default says. The privacy lint blocks a scheduled run that makes one normal
  (`delegate-source-private`, in every lint mode), promotes one, or sets, changes or removes
  `promoted_from` or `delegate_scope`.
- **KB tooling leaves `from-agents/` alone.** It is not a graph entity, it is skipped by freshness and
  lint reports, and deep work and fact-checks don't pick it.
- **Look-alike words** are named in the list.
- **Confirmation.** Accept and promote take the ULID typed again, or `--confirm <ULID>`.
- **Versions.** The classifier and index are version 5. The index rebuilds on the version mismatch,
  and mirrors re-plan because their fingerprint holds the engine version.

`Label` gains `delegate_source`, `delegate_scope` and `delegate_author` as internal fields, so published
labels, the corpus and Agnes are unchanged. `elsewhere` doesn't see an unpromoted agent's note either. An
accepted note's body is a provenance line, then the text as a block quote, wrapped below kb-lint's strict
line limit.

**After the review of #738.**

- **Promote publishes only what the owner accepted.** It refuses unless the note is byte for byte what
  accept wrote (or what unpromote put back), and what the preview just showed. The preview is printed with
  `--confirm` too.
- **Runs can't write `knowledge-base/from-agents/`.** It is a protected tree: the write gate denies it on
  every platform, and on macOS so does the run's sandbox. A run can still read it.
- **Agent text is marked in `kb delta`.** Paths are marked, and a diff that holds agent text is preceded by
  the data-not-instructions notice. The action-items phase says a `delegate-<id>:` comment is an agent's
  text, never the owner's.
- **Two more text bans.** Proposal text may not hold `%%` (an Obsidian comment, or a `%%privacy:allow%%`
  marker) or `[from-agent`.

**Residuals, known and accepted.**

- **`git show <commit>` or `git log -p` with no path prints agent text unlabelled.** The read gate doesn't
  label git's own output, a gap that predates V4b2. The phases send runs to `scoutctl kb delta`, which
  labels it.
- **The shell gate doesn't parse `git mv` into a protected tree.** It catches `>`, `>>`, `cp`, `mv` and the
  file tools. On macOS the run's sandbox denies the write in the kernel; elsewhere the hooks are the only
  layer, as for the inbox copies.

**Parked follow-ups (the coordinator, 2026-11-19).**

- **The outbound excerpt check could cover `delegate:` notes too.** `shared_reads.source_note_records`
  feeds it the private notes with a `shared:` source, so a Slack message that quotes or paraphrases one is
  caught. An accepted agent's note is private, so naming or linking it is already caught, but its text
  isn't. Not needed for V4b2.

#### V4b3 plan: the review in the app (2026-11-20, approved)

Two PRs. The first is small and engine-only; the second is one cross-cutting commit, because it changes a contract the app
reads (the root `CLAUDE.md`'s rule).

**PR 1: the nits from #738's verification** (engine only).

- `inbox review` lists, and `accept`'s output says, that an accepted note must not be edited by hand if you
  mean to promote it: promote publishes only the accepted bytes.
- `revert`'s refusal for an edited note points at the same rule.
- No contract changes.

**PR 2: V4b3** (engine, fixtures and app in one commit).

- **`delegate status --json` gains `inbox` per delegate.** It is ids, counts and times only, never agent text, read from the
  signed inbox state through `delegate_inbox_sync.summary`:
  - `linked`, `last_pull`, `refusal` (a code and a fixed sentence, as for mirrors), `review` (items waiting for the owner),
    `oldest_review`, `waiting` (taken, not settled) and `untaken`;
  - `null` when the delegate has no inbox. When the state doesn't verify, `state_unverified` is set and nothing in it is
    trusted.

  The field is additive and optional, so `schema_version` stays 1: an older app build ignores it and a newer one decodes it as
  optional. The contract fixture `delegate-status.json` is regenerated with `SCOUT_REGEN_CONTRACT=1` from its builder, which
  gains an inbox with two items waiting, one with a failed pull, and a delegate with none. The fixture's app byte copy and the
  pins in `DelegateStatusTests.swift` change in the same commit.
- **`inbox review --json` becomes a contract too.**
  - The canonical copy is `tests/fixtures/contract/inbox-review.json`, generated from a builder vault: an observation with a
    hidden character and a look-alike word, a held comment, and a limited op. All text is generic (stand-in names only).
  - The app keeps a byte copy, and `contract.yml` gains the byte-compare step, written like the `delegate-status.json` one.
  - Every item already carries `shown` text, look-alikes and the delegate id.
- **The app.** Settings ▸ Agent copies gets, per delegate, an inbox line ("2 proposals wait for your review", a failed pull's
  fixed sentence, or nothing) and a **Review** button. The button opens a sheet listing the items:
  - **What each item shows:**
    - a header, `FROM AGENT delegate-<id>`, with the kind, op, `[#TAG]` and date, and the proposal time;
    - the engine's `shown` text in a monospaced `Text(verbatim:)`, never Markdown, never link-detected, never selectable into
      a send;
    - the look-alike words named in a warning colour.
  - **Accept and Reject per item.**
    - Accept opens a confirmation that repeats the delegate id and the full text, with a button labelled `Accept from
      delegate-<id>`. It then runs `scoutctl inbox review accept <ULID> --confirm <ULID> --json`.
    - Reject runs `scoutctl inbox review reject <ULID> --json`.
    - The sheet then refreshes both the list and the status.
  - **One allow-list.** A new `InboxReviewService` runs every `scoutctl inbox` command in the app through one `Command` enum
    (see the decisions below). A test fails on the quoted word `inbox` anywhere else in the app, as `DelegateStatusTests`
    does for `delegate`. The app never runs `pull`, `apply`, `revert` or `sync`.
  - **After accepting an observation,** the sheet says it stays private until promoted.
- **Tests.**
  - Engine: the builders and both contract tests, the `inbox` field's shape checks (a bad state, no inbox, a refused pull)
    and the counts.
  - App: decoding both fixtures, the model's plain-language lines, the confirmation's wording, and the allow-list scan.
  - The app's affected suites are derived from the touched symbols, with the source-scan guard suites added.

**Decided (2026-11-20, Jordan, through the coordinator).**

1. **Accept in the app: the named button.** The confirmation repeats the delegate id and the full escaped text, its button
   reads `Accept from delegate-<id>`, and the app passes `--confirm <ULID>`. A person pressing a button that names the agent,
   under its full text, is the deliberate act `--confirm` stands for.
2. **Promote and unpromote in the app, in V4b3.** So PR 2 grows:
   - **Engine.** `inbox review promote <ULID> --preview --json` writes nothing and returns the note's exact text (escaped as
     in review), its look-alike words, the audience (the `--to` ids resolved, or every delegate that can see the folder when
     there is no `--to`, as delegate ids) and the note's `sha256`. `promote <ULID> --previewed <sha> --confirm <ULID>` refuses
     unless the note is byte for byte what was accepted and what was previewed, as now. `unpromote <ULID> --preview --json`
     returns what goes back (the text, and who stops seeing it); `unpromote` gains `--previewed`, checked the same way. It
     needs no `--confirm` in the engine, because it only narrows who sees the note. The app still shows the preview and a named
     button (`Make private again: hide from delegate-a, delegate-b`).
   - **The audience is pinned.** The app passes `--to` with exactly the previewed delegate ids, so the note is scoped to
     the delegates the button named and never reaches one added to the config later. The audience is found by classifying
     the note as promote would write it and asking `visible_to` for each listed delegate.
   - **Something to promote later.** `inbox review --accepted --json` lists the accepted observations from the signed
     state: delegate, ULID, note path, when accepted, and whether and to whom promoted. It holds ids and times only; the
     text comes from a preview.
   - **The status document:** the signed inbox state is all or nothing, so a top-level `inbox_state` (`ok`, `none`,
     `unverified`, `unsafe`, `unreadable`) replaces a per-delegate `state_unverified`. Each delegate gains `inbox` and
     `can_review`.
   - **App.** The promote dialog shows the full text and exactly who will see it, and its button names them (`Promote to
     delegate-a, delegate-b`). The app passes the previewed hash back. A refusal (`note_changed` and the rest) is shown in
     the engine's own words. Unpromote follows the same pattern: a preview of what reverts, then a named button.
   - **The allow-list** becomes `review`, `accept`, `reject`, `promote` and `unpromote` (each with `--preview` where it has
     one). The source-scan test still fails on `pull`, `apply`, `revert` or `sync`.
   - **The contract.** `inbox-review.json` gains a promote preview from the builder, under the same byte copy and
     `contract.yml` compare.

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
| **V1 Delegate reader** (built: #625) | `delegate:<id>` in `scout/kb/reader.py`; `privacy.delegates` parsed per §4.5; every `kb` read command, the graph, `kb delta`, `kb classify`, the KB read gate and `pre-session data` filter for it unless `privacy.delegates.<id>.include_private`; audit rows; the write-refusal in §6.5. **No contract change:** delegates don't change labels, so `privacy-corpus.json`, `privacy.snapshot.json` and `CLASSIFIER_VERSION` stay as they are, and the policy JSON names `delegates` only when a vault lists one. Delegate visibility is tested in the engine's own unit tests. Residual left for V2: a normal note's body that names a private note still shows (§4.4) | `scoutctl kb files` as `delegate:x` on the privacy fixture vault prints no `CANARY-PRIV-*` |
| **V2 Delegate mirror** | **V2a (built: #632):** `scoutctl delegate link/sync [--all]/preview/status/unlink`; per-delegate tree with line redaction (§4.4); pre-push re-check; compare-and-swap push to a private GitHub repo or local path (§6.1 "As built"). **V2b (built: #633, stacked on #632):** `link --create-repo`, `delegate key` (read-only deploy key, rotate, revoke), `delegate push-token`. **V2c (built: #636, stacked on #633):** the post-run sync, the tick's 15-minute sync, mirror ages in doctor, failures in run health | A fixture vault links to a throwaway private repo; the mirror has no canary, no local-only path, and labels for every file (V2a's tests prove this with local bare repos; a real GitHub repo waits for V2b and Jordan's OK) |
| **V3 Delegate bootstrap** (built: platform-neutral part, §6.6 "As built") | `scoutctl delegate init` (AGENTS.md / CLAUDE.md, env, install check); docs per platform | A fresh Linux container with only the mirror clone and a token answers a scout-ask question through `scoutctl kb` |
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
   delegate mirrors too, or leave it to lint? *V2a strips them (fail closed);
   say if lint alone should handle it.*
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

- **Shared code, two hosts.** As built, V2 doesn't touch `repo_sync`: delegate
  mirrors are their own module that imports R1's building blocks (§6.1 "As
  built"). An R1 change to `LocalOnlyRules`, `_stricter_policy` or the gates now
  also reaches delegate mirrors, so the sub-coordinator should run
  `test_delegate_mirror.py` alongside R1's tests.
- **The delegate reader and the indexer.** `delegate:*` is a new reader, but
  it changes no label, so the privacy corpus the Agnes indexer runs is
  unchanged and V1 is not a contract change. A synced `privacy-policy.json`
  may carry a `delegates` key; the indexer ignores readers it doesn't know.
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
