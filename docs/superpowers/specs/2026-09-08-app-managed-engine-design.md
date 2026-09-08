# App-managed engine — Scout.app installs and owns the Scout engine

**Date:** 2026-09-08
**Status:** Design drafted, awaiting review
**Author:** Jordan Burger (brainstormed with Claude)
**Repos affected:** `Raven-Scout/Scout` (this repo — the installer, onboarding, settings), `Raven-Scout/scout-plugin` (six small engine changes, §7)
**Tracks:** [Scout#51](https://github.com/Raven-Scout/Scout/issues/51) (Mac-app-first onboarding, Hermes-style), [scout-plugin#26](https://github.com/Raven-Scout/scout-plugin/issues/26) (`scoutctl bootstrap auto`)
**Relates to:** [Scout#74](https://github.com/Raven-Scout/Scout/pull/74) (in-app updates — amends its plugin track, §9), [Scout#99](https://github.com/Raven-Scout/Scout/pull/99) (monorepo — answers its open question 2, §9), `2026-04-24-scout-unification-design.md` §8 (the first-run wizard and `EngineClient` it specified but never built)

## 1. Problem statement

Scout is one product delivered as two installs that do not know about each other.

**Today's path for a new user** (from both READMEs): download the DMG and drag
Scout.app to Applications; separately install Claude Code; run
`curl … install.sh | bash` in a terminal (which adds a marketplace, installs the
plugin, and builds a Python venv); open Claude Code and run `/scout-setup`, a
137-line LLM-executed runbook that probes connectors by calling MCP tools, asks
four identity questions, and hands off to `scoutctl bootstrap install`. Only
then does the app have anything to show. Until then the app renders empty
tabs — the 2026-06-22 audit found no "engine missing" banner anywhere, and
`~/Scout` hardcoded at `AppState.swift:65` with no override.

**The engine has no canonical location.** Depending on how it was installed,
the plugin tree and its venv live in `~/scout-plugin/` (maintainer clone),
`~/.claude/plugins/marketplaces/<name>/` (GitHub marketplace clone), or
`~/.claude/plugins/cache/scout-plugin/scout/<version>/` (Claude Code's frozen
copy). The `engine/bin/scoutctl` launcher probes four locations and
cross-jumps between them; `/scout-update` re-resolves the root in every shell
block with a three-way fallback; the app's first-priority candidate
`~/scout-plugin/bin/scoutctl` ([`AppState.swift:383`](../../../Scout/Shell/AppState.swift))
does not exist and the app works via `$PATH` luck ([#99](https://github.com/Raven-Scout/Scout/pull/99) §1.6).
Verified on this machine: Claude Code's cache copy of a *directory* marketplace
duplicates the **entire** source directory — including the 100 MB `.venv`,
`.mypy_cache` and `.scoutctl-py-cache`, whose contents point back at the
source tree. It works only because the source tree still exists.

**Updates are asymmetric because the plugin is assumed unreachable from the
app.** [#74](https://github.com/Raven-Scout/Scout/pull/74) designs the plugin
track as "detect + notify + hand off" on the premise that *"there is no
app→Claude-Code interface to drive a slash command."* That premise is true of
slash commands and false of everything they wrap. Verified against Claude Code
2.1.259: `claude plugin marketplace add <path|repo>`, `claude plugin install
scout@scout-plugin`, `claude plugin update`, `claude plugin list --json`,
`claude auth status --json` (`{"loggedIn": true, …}`), and `claude mcp list`
(one line per server with `✔ Connected` / `! Needs authentication`) are all
headless. `/scout-setup` and `/scout-update` are thin wrappers over
`scoutctl bootstrap install|upgrade`; the only genuinely LLM-mediated step is
MCP connector probing, and `claude mcp list` answers that too.

**Setup state detection is delegated to an LLM.** [scout-plugin#26](https://github.com/Raven-Scout/scout-plugin/issues/26)
records the cost: the runbook picks `install` vs `upgrade` vs
`migrate-legacy`; when it drifts or skips a step, users land in the wrong
subcommand. That issue's `scoutctl bootstrap auto` has been spec-only since
2026-05-19 and is exactly the entry point a native installer needs.

### Root cause

Nothing owns the engine. Claude Code owns a copy, the venv builder owns a
path convention, the plists own a rendered path, the app owns a guess list.
Each consumer resolves the engine independently, so every install method
produces a slightly different machine and every update has to re-derive
where things are.

## 2. Goals and non-goals

### Goals

1. **One download.** A new user installs Scout.app. The app installs and
   manages everything else Scout needs that is not Claude Code itself: the
   plugin tree, a Python ≥ 3.11 venv, registration with Claude Code, the
   vault, and the launchd jobs.
2. **One version.** The app ships the engine version it was built against.
   Updating the app updates the engine. "What version of Scout do you have?"
   has one answer.
3. **One location.** A canonical on-disk layout and a machine-readable pointer
   that the app, the `scoutctl` launcher, the plists, the hooks, the doctor and
   `install.sh` all read — so no consumer guesses.
4. **Native onboarding.** The `/scout-setup` runbook becomes a SwiftUI flow:
   prerequisites, engine install, identity, connectors, vault, verify. Every
   empty or broken state in the app routes to it instead of rendering blanks.
5. **Edit-and-go survives.** A maintainer's `~/scout-plugin` dev checkout is
   adopted read-only and never overwritten.
6. **Existing installs keep working.** Marketplace and `install.sh` installs
   are adopted as-is; migration to app-managed is explicit and later (§10).
7. **Configurable vault root** ([#51](https://github.com/Raven-Scout/Scout/issues/51) scope item; roadmap Phase 2).

### Non-goals

- **Installing Claude Code inside the bundle or automating its login.** The
  app detects Claude Code, opens Terminal with Anthropic's documented install
  command when it is missing, and opens `claude auth login` when it is signed
  out. It never pipes a remote script silently and never touches credentials.
- **Bundling a Python interpreter.** `uv` manages Python (§4.3); the DMG stays
  small.
- **Sparkle mechanics** — owned by [#74](https://github.com/Raven-Scout/Scout/pull/74). This design consumes its
  `UpdateService`; it does not change how the app binary updates.
- **Repository layout** — owned by [#99](https://github.com/Raven-Scout/Scout/pull/99). This design is
  layout-agnostic; §9 states what changes under a monorepo (one script's
  source path).
- **Linux / Windows GUI, iOS, Android.** Engine-side changes stay
  cross-platform; the installer is macOS.
- **Automatic migration of legacy installs in v1.** Adopt in v1; one-click
  migrate in a later phase with the mechanism specified (§10).
- **A second engine channel** ("latest from GitHub" alongside the bundled
  engine). Considered in §3; deferred.

## 3. Approaches considered

**A. Bundled engine, app-managed (recommended).** The app carries the plugin
tree (≈ 2 MB tarball) as a bundle resource, pinned to a scout-plugin tag at
build time. On first run it unpacks the tree to a canonical location, obtains
Python via `uv`, builds the venv, registers the tree with Claude Code as a
directory marketplace, and runs `scoutctl bootstrap auto`. Updating the app
updates the engine. Offline after download; provably version-matched; no
runtime code download for the engine.

**B. Downloaded engine, app-managed.** Identical layout and installer, but the
app `git clone`s scout-plugin at a pinned tag instead of unpacking a bundled
tarball. Buys independent engine releases without an app release. Costs:
network and `git` at install time, a pin that can drift from what was tested,
and (pre-#99) cross-repo release coordination at *install* time rather than at
*build* time. Kept as the natural future "latest" channel; not v1.

**C. Guide-only.** The app detects the missing engine and copies the right
commands to the clipboard — the minimal reading of #51 plus #74's hand-off.
Does not meet "users only need to install the app." Rejected.

A is the recommendation because it is the only one where Scout has a single
version and a single artifact, and because the trade-off it makes — an
engine-only fix needs an app release to reach app-managed users — is cheap
once #74's Sparkle lands (an app release is a script run and a dialog).

## 4. Architecture

```
┌──────────────────────────── Scout.app ────────────────────────────┐
│  Resources/engine-release.json  (pin: version, tag, commit, sha)   │
│  Resources/scout-engine-<v>.tar.gz  (plugin tree, build product)   │
│                                                                    │
│  EngineLocator ──► EngineState ──► gates MainWindowView            │
│  PrerequisiteChecker   (claude, auth, git, uv)                     │
│  EngineInstaller       (6 idempotent steps, §4.4)                  │
│  EngineUpgrader        (bundled > installed → re-run 2..5)         │
│  OnboardingFlow        (§5)     Settings ▸ Engine (§5)             │
└───────────┬───────────────────────────────┬────────────────────────┘
            │ unpack / venv / bootstrap      │ claude plugin marketplace add
            ▼                               ▼
~/.local/share/scout/                 Claude Code (~/.claude/plugins/…)
  engine/<v>/   ◄── directory marketplace "scout-plugin" → cache copy
  engine/current -> <v>                (commands, skills, Stop hooks)
  venv/<v>/                                     │ hooks call
~/.local/state/scout/engine.json  ◄─────────────┘ ${CLAUDE_PLUGIN_ROOT}/engine/bin/scoutctl
~/.local/bin/scoutctl (shim)  ~/.local/bin/uv        (launcher reads the pointer)
~/Scout (vault, configurable)  ~/Library/LaunchAgents/com.scout.*.plist
```

### 4.1 Canonical layout

```
~/.local/share/scout/
  engine/
    0.10.0/                 # the plugin tree exactly as tagged, minus .git and caches
    current -> 0.10.0       # what Claude Code's marketplace entry points at
  venv/
    0.10.0/                 # Python venv; editable install of engine/0.10.0/engine
~/.local/state/scout/
  engine.json               # the pointer (§4.2)
  install.log               # installer transcript, appended per run
~/.local/bin/uv             # installed by the app if absent (§4.3)
~/.local/bin/scoutctl       # the existing bootstrap-written shim, now → venv/<v>/bin/scoutctl
```

Why here: space-free (shell templates and `ProgramArguments` never see a
quoting problem), XDG-conventional for CLI tooling (Claude Code itself uses
`~/.local/share/claude/versions/` and `~/.local/bin/claude`), already on the
`PATH` the installed plists set (`__USER_HOME__/.local/bin:…`), and outside
every macOS TCC-protected directory the doctor checks
(`~/Documents`, `~/Desktop`, `~/Downloads`). The layout is identical on Linux,
so `install.sh` can converge on it later (§10).

**The venv lives outside the plugin tree.** Two reasons, both verified:
Claude Code copies a directory marketplace wholesale into its cache (a
`.venv` inside the tree becomes a 100 MB copy whose scripts point at the
source), and a venv rebuild must never touch the tree Claude Code has
registered. Consequence: the engine must stop assuming
`<plugin_root>/.venv` — that is engine change E1 (§7).

**Versioned directories, not in-place mutation.** A new version is unpacked
and built beside the old one; `bootstrap upgrade` (which already re-renders
the plists and shim to the venv that runs it) is the atomic switch; the old
version is garbage-collected afterwards, keeping one previous version on disk.
A crash mid-upgrade leaves the old engine fully working.

### 4.2 The engine pointer — `~/.local/state/scout/engine.json`

The one file that answers "where is the engine?" for every consumer.

```json
{
  "schema_version": 1,
  "version": "0.10.0",
  "engine_root": "/Users/alex/.local/share/scout/engine/0.10.0",
  "python": "/Users/alex/.local/share/scout/venv/0.10.0/bin/python",
  "scoutctl": "/Users/alex/.local/share/scout/venv/0.10.0/bin/scoutctl",
  "vault": "/Users/alex/Scout",
  "managed_by": "scout-app",
  "written_at": "2026-09-08T14:02:11Z"
}
```

- **Written by the engine**, not the app: `scoutctl bootstrap install|upgrade|
  migrate-legacy|auto` write it in the same stage that writes the
  `~/.local/bin/scoutctl` shim (E2). The engine already knows its root
  (`Path(scout.__file__).parent.parent.parent`) and its interpreter
  (`sys.executable`), so no caller has to tell it anything except
  `--managed-by` (`scout-app` | `install.sh` | `claude-code` | `dev`;
  default `unknown`).
- **Read by:** the app (`EngineLocator`, §4.4), the `engine/bin/scoutctl`
  launcher (as a candidate right after `<plugin_root>/.venv`, so Claude
  Code's cache copy resolves the real venv without cross-jumping), the
  doctor (pointer ↔ plist ↔ shim consistency check), and later `install.sh`.
- **Authoritative but not exclusive.** When the pointer is absent the app
  falls back to the conventional layout and then to legacy discovery
  (§4.4), so a pre-pointer engine is still found.

### 4.3 Prerequisites and how each is satisfied

| Need | Why | Detect | Satisfy |
|---|---|---|---|
| Claude Code CLI | runs the sessions; hosts the plugin | `ClaudeLauncher.resolveClaudePath` (exists) + `claude --version` | **Hand off, visibly:** open the user's terminal (the existing `CLITerminal` / `ClaudeLauncher` machinery) running Anthropic's documented native installer `curl -fsSL https://claude.ai/install.sh \| bash`, then re-check. Never run inside the app. |
| Signed in | scheduled runs 401 otherwise (doctor already detects this after the fact) | `claude auth status --json` → `loggedIn` | Open Terminal running `claude auth login`; poll. **Not blocking** for setup — the engine and vault install fine signed-out; the badge stays until signed in. |
| `git` | the vault is a git repo; every run commits | `xcode-select -p` exits 0, or Homebrew/MacPorts git exists. Never *invoke* `/usr/bin/git` to test — with no CLT installed that pops Apple's dialog | Button runs `xcode-select --install` (Apple's own prompt). |
| `uv` | provides Python ≥ 3.11 on Macs that ship 3.9 | `~/.local/bin/uv` or `PATH` | Download the **pinned** release asset `uv-{aarch64,x86_64}-apple-darwin.tar.gz` from `github.com/astral-sh/uv/releases`, verify against the SHA-256 baked into `engine-release.json`, install to `~/.local/bin/uv`. Existing uv is used as-is. |
| Python ≥ 3.11 | engine `requires-python` | (via uv) | `uv venv --python 3.12` downloads a managed CPython into `~/.local/share/uv/python/` on demand. |

Claude Code *installed* is the only hard gate before engine installation
(`claude plugin marketplace add` needs the CLI). Everything else degrades to a
visible, actionable state.

### 4.4 App components

All new Swift lives under `Scout/Engine/` (services) and `Scout/Onboarding/`
(views), auto-compiled by the synchronized file groups. Every shell-out goes
through the existing `ProcessRunner` protocol; every filesystem read takes an
injectable root so tests use temp directories.

**`EngineRelease`** — decodes `Resources/engine-release.json`:

```json
{ "schema_version": 1,
  "engine": { "version": "0.10.0", "tag": "v0.10.0", "commit": "<sha>",
              "repo": "Raven-Scout/scout-plugin", "tarball": "scout-engine-0.10.0.tar.gz",
              "sha256": "<sha>" },
  "uv":     { "version": "0.12.1",
              "sha256": { "aarch64-apple-darwin": "<sha>", "x86_64-apple-darwin": "<sha>" } } }
```

`bundled: BundledEngine?` is `nil` when the tarball is not in the bundle (a
Debug build made without running the bundling step, §6); the UI says so
instead of failing.

**`EngineLocator`** — pure filesystem function → `EngineState`:

| Evidence (checked in this order) | State |
|---|---|
| pointer with `managed_by == "scout-app"`, `scoutctl` executable | `.managed(EngineInstall)` |
| pointer with any other `managed_by`, `scoutctl` executable | `.external(EngineInstall, source)` — `source` mapped from `managed_by`: `dev` → `.devCheckout`, `install.sh` → `.installSh`, `claude-code` → `.claudeCode`, else `.unknown(String)` |
| no pointer; `engine/current` + `venv/<v>/bin/scoutctl` exist | `.managed(…, vaultBootstrapped: false)` (installer stopped before `bootstrap`) |
| `~/.local/bin/scoutctl` carrying the shim marker → its `exec` target exists | `.external(…, .shim)` |
| `~/.claude/plugins/installed_plugins.json` `scout@scout-plugin` `installPath` with a `.venv` (uses #74's `PluginManifests`) | `.external(…, .marketplaceCache)` |
| `~/scout-plugin/.venv/bin/scoutctl` | `.external(…, .devCheckout)` |

`ExternalSource` is one enum — `.devCheckout | .installSh | .claudeCode |
.marketplaceCache | .shim | .unknown(String)` — used by §10's table and by
the Settings "source" label.
| pointer present but a referenced path is missing / not executable | `.broken(EngineInstall?, reason)` |
| none of the above | `.notInstalled` |

`EngineInstall { root, scoutctl, python, version, vault }`; `version` comes
from `<root>/.claude-plugin/plugin.json` synchronously and is confirmed by
`scoutctl version` asynchronously. This replaces
`AppState.resolveScoutctlPath()`; the `/usr/bin/env scoutctl` fallback is
removed — an unresolvable engine is now a first-class state the UI shows,
not a silent `ENOENT` inside `ScheduleService`.

**`PrerequisiteChecker`** — async; returns `Prerequisites { claude: .missing |
.installed(version) ; auth: .unknown | .signedOut | .signedIn ; git: .missing |
.present ; uv: .missing | .present(path) }` from the probes in §4.3. Runs on
every onboarding step render and on a 3-second timer while a hand-off Terminal
is open.

**`EngineInstaller`** (actor) — six idempotent steps, each re-runnable alone,
streamed as `InstallProgress { step, status, log }` to the UI and appended to
`~/.local/state/scout/install.log`:

1. **`ensureUv`** — present → skip; else download + verify + install (§4.3).
2. **`unpackEngine`** — extract the bundled tarball into
   `engine/<v>.partial`, check `.claude-plugin/plugin.json` `version ==
   <v>`, rename to `engine/<v>`, repoint `current`. Existing `engine/<v>`
   with a matching manifest → skip.
3. **`buildVenv`** — `SCOUT_VENV_DIR=~/.local/share/scout/venv/<v> bash
   engine/<v>/scripts/install-venv.sh` (E5 makes the script uv-aware and
   location-aware), then `venv/<v>/bin/scoutctl version` must print `<v>`.
   The engine's own script stays the single source of truth for how a venv
   is built; the app supplies a location and a `uv`.
4. **`registerWithClaudeCode`** — read `~/.claude/plugins/known_marketplaces.json`
   (shape verified; parsed by #74's `PluginManifests`, or a local equivalent
   until #74 lands): no `scout-plugin` entry → `claude plugin marketplace add
   ~/.local/share/scout/engine/current`; entry whose source is *not* that
   path → stop and surface (this is the adopt/migrate decision, §10); then
   `claude plugin install scout@scout-plugin` (`update` when
   `installed_plugins.json` already lists it). A restart-required notice is
   shown once (Claude Code loads plugins at session start).
5. **`bootstrapVault`** — `scoutctl bootstrap auto --no-interactive --yes
   --json --managed-by scout-app --claude-bin <path> --platform macos
   [identity + connector flags]` with `SCOUT_DATA_DIR=<vault>` in the
   environment (E3). The engine detects fresh / legacy / existing and
   dispatches; the app decodes `BootstrapResult` (action taken, doctor
   severity, errors, warnings, sidecar conflicts, runner backups).
6. **`verify`** — `scoutctl bootstrap doctor --json`; result feeds
   `EngineHealthService`.

Failure semantics: no step leaves a half-state that looks whole — partial
directories carry `.partial`, `current` is only repointed after the manifest
check, and the pointer is written by the engine only after its own venv ran.
Any step's failure shows its log tail and a **Retry** for that step.

**`EngineUpgrader`** — at launch, when `EngineState == .managed` and
`installed.version < bundled.version`: run steps 2–5 for the new version
(step 4 as `claude plugin marketplace update scout-plugin` + `claude plugin
update scout@scout-plugin`; step 5 dispatches to `upgrade`, which re-renders
plists and shim to the new venv and rewrites the pointer), then GC
`engine/` and `venv/` down to the current + one previous version. Runs
automatically with a progress sheet (decision flagged in §11 Q1). Never runs
for `.external` engines — those show installed vs bundled and #74's
`/scout-update` hand-off.

**`EngineHealthService`** (`ObservableObject`) — publishes `EngineState`,
`Prerequisites`, last `DoctorReport`, `needsAttention: Bool`. Refreshed at
launch, after any installer/upgrader run, on Settings ▸ Engine "Check", and
every 10 minutes. Drives the window gate, the Settings section, and the
menu-bar badge (the affordance #74 adds; until #74 lands, a dot on the
sidebar Settings row).

**`ClaudeCodeCLI`** — thin, tested argv builders + decoders for the five
`claude` invocations above and for `auth status --json`; the JSON shapes are
pinned by anonymized fixtures.

**`AppState` changes** — `scoutctlExecutable` comes from `EngineLocator`;
`scoutDirectory` resolves `UserDefaults["scoutDataDir"]` → pointer `vault` →
`~/Scout`; every `scoutctl` invocation passes `SCOUT_DATA_DIR` (the
`ProcessRunner` already takes an environment). Settings' "Scout directory"
row becomes editable. Changing it **points** the app at a vault; it never
moves data. If the new path holds a vault, the app offers to re-bootstrap
(`bootstrap auto` → `upgrade`, which re-renders the plists and pointer for
that vault); if it holds nothing, onboarding resumes at step 4 to create one
there. The previous vault is left untouched on disk.

## 5. Onboarding and settings UX

Onboarding is not a separate window. When `EngineHealthService.state` is
anything but healthy, `MainWindowView` renders `OnboardingView` in the detail
pane and dims the sidebar rows that have nothing to show; the Settings row
stays live. This is #51's "route empty states into onboarding" — the tabs'
own missing-engine texts (`ActionItemsEnvironmentCheck`'s *"install
scout-plugin and re-launch"*, `ScheduleService.lastError`'s ENOENT case) are
replaced by a one-line pointer to the Engine section.

**Steps** (a `OnboardingViewModel` state machine; each step is skippable
when already satisfied, so an existing install lands directly on 7):

1. **Welcome** — what will be installed and where, in six lines. Vault folder
   picker, default `~/Scout`.
2. **Prerequisites** — three rows from `Prerequisites`: Claude Code
   (Install… / Sign in… buttons open Terminal, §4.3), Command Line Tools
   (Install…), uv (installed automatically in the next step). Continue is
   enabled when Claude Code is installed; sign-in can be finished later.
3. **Install engine** — live progress for installer steps 1–4.
4. **About you** — instance name (default `Scout`), your name and email
   (prefilled from `git config --global user.name/email` when present),
   timezone (system default, picker).
5. **Connectors** — `scoutctl connectors detect --json` (E4) renders one row
   per connector: `connected` / `needs authentication` / `not found` /
   `unknown`, each with a toggle the user can override, plus the inputs the
   registry declares (`needs_user_input`: Slack user ID, GitHub username and
   repos) and the per-session budget. A hint explains that connectors are
   authorized in Claude Code (`claude mcp list` / claude.ai connectors) and
   can be re-detected.
6. **Create vault** — installer step 5 progress, then the doctor result:
   green → next; yellow → list warnings, allow continue; red → list errors,
   Retry / "Copy diagnostics".
7. **Ready** — "Run your first briefing now" (`scoutctl schedule fire-now
   <slot>` for the first slot of type `briefing` in `scoutctl schedule list
   --json` — the same `fireNow` path the Control Center uses) and "Open
   Claude Code here" (the existing `ClaudeLauncher`). Then the gate lifts
   and the Control Center shows.

**Settings ▸ Engine** (replaces the static *Plugin: scout-plugin / Daemon:
healthy* rows in About): installed version → bundled version; source
(app-managed / dev checkout / marketplace); engine root and vault (vault
editable); doctor severity with its messages; buttons **Check now**,
**Update engine** (managed, behind), **Repair** (re-run installer steps),
**Migrate to app-managed** (external, non-dev; phase 3, §10), **Open
`/scout-status` in Claude Code**. Debug builds without a bundled tarball say
so here.

## 6. Build and release changes (this repo)

**`scripts/bundle-engine.sh`** — materializes the engine payload for a build.
Reads `Scout/Resources/engine-release.json`; obtains the plugin tree at the
pinned **commit** from, in order: `$SCOUT_ENGINE_SOURCE` (a checkout path),
the sibling `../scout-plugin` checkout if its `git rev-parse <tag>` equals
the pinned commit, else a shallow clone of the tag; verifies
`.claude-plugin/plugin.json` version equals the pin; produces a
deterministic tarball (sorted entries, fixed mtimes/owners) excluding `.git`,
`docs/assets`, `__pycache__`, `.venv`, `.*_cache`; writes the sha256 back into
`engine-release.json` and copies the tarball into the built product's
`Resources/`. Runs as an Xcode **Run Script build phase** (the one
`.pbxproj` edit this design needs) so Debug builds and CI get the same
payload; a Debug build with no network and no sibling checkout produces a
bundle without the tarball, which `EngineRelease` reports as *not bundled*.
The tarball is a build product, **not committed**.

**`scripts/release.sh`** — before building: refuse if `engine-release.json`
pins a tag that does not exist on `Raven-Scout/scout-plugin` or whose commit
differs from the pin; after building: refuse if the bundled `plugin.json`
version ≠ pin (belt and braces on the build phase). Release notes gain an
"Engine: scout-plugin vX.Y.Z" line.

**CI** — `ci.yml` runs the bundling step (network is available on the
runner) and `EngineReleaseTests` asserts that the bundled tarball's manifest
version equals the pin, so a stale pin fails the build rather than shipping.

**Pin bumps** are ordinary commits: edit `engine-release.json`
(`version`/`tag`/`commit`), CI proves the tarball matches. Post-#99 this
file is generated from `plugin/.claude-plugin/plugin.json` and
`bundle-engine.sh` tars `plugin/` from the same tree with no network (§9).

## 7. Engine-side changes (scout-plugin)

Six small changes, each independently shippable and tested with pytest (fake
`home=` / `tmp_path`, the idiom `test_bootstrap_*.py` already uses). The app
pins the first plugin release that carries all six.

- **E1 — `resolve_scoutctl_bin()` derives from the running interpreter.**
  `Path(sys.executable).parent / "scoutctl"` instead of
  `<plugin_root>/.venv/bin/scoutctl`. Strictly more correct in every layout
  (it names the venv that is actually executing) and required for a venv
  outside the tree. `install_scoutctl_shim`, the plists and the doctor
  inherit the fix.
- **E2 — engine pointer.** `scout/scripts/engine_pointer.py` with
  `write_pointer(home=…)` / `read_pointer(home=…)`; written in the shim
  stage of install / upgrade / migrate-legacy; `--managed-by` flag on all
  bootstrap subcommands. `engine/bin/scoutctl` adds the pointer's `python`
  as a candidate after `<plugin_root>/.venv` (extracted with `sed`, not
  Python — the launcher must work with only `/bin/sh`). Doctor gains a
  pointer ↔ plist ↔ shim consistency check.
- **E3 — `scoutctl bootstrap auto`** ([#26](https://github.com/Raven-Scout/scout-plugin/issues/26)) with
  `--json`, `--dry-run`, `--no-interactive`, `--yes`, plus `--json` on
  `install`, `upgrade`, `migrate-legacy` and `doctor`. One `BootstrapResult`
  JSON shape: `{ "action": "install|upgrade|migrate-legacy|refused",
  "vault": …, "plugin_version": …, "doctor": { "severity", "errors",
  "warnings" }, "conflicts": [...], "backups": [...], "pointer": "<path>" }`.
  The state → action table is the one in #26's spec. This is the app's
  entire bootstrap contract; the text output stays for humans.
- **E4 — `scoutctl connectors detect --json [--claude-bin PATH]`.** Runs
  `bash` probes from the merged registry directly; for `mcp_tool` probes,
  runs `claude mcp list` once and maps each tool's server slug
  (`mcp__claude_ai_Gmail__list_labels` → server `claude.ai Gmail`;
  `mcp__plugin_slack_slack__…` → plugin `slack`) to that line's status.
  Output `{ "<connector>": { "status": "connected|needs_auth|unavailable|
  unknown", "needs_user_input": [...], "evidence": "<line>" } }`. `unknown`
  when `claude mcp list` fails or the slug does not match — detection is a
  hint the user confirms, exactly as the runbook's "Proceed with these
  connectors?" step is today. The registry and the user's
  `connector-probes.local.yaml` overlay remain the single source.
- **E5 — `scripts/install-venv.sh`** honors `SCOUT_VENV_DIR` (default
  unchanged: `$PLUGIN_ROOT/.venv`) and prefers `uv` when present
  (`uv venv --python 3.12 "$VENV"` + `uv pip install --python "$VENV/bin/python"
  -e "$PLUGIN_ROOT/engine[dev]"`), falling back to today's `python3.1x -m
  venv` path. Shell-tested with a fake `uv` on `PATH`. This also fixes the
  latent `/scout-update` failure on machines with no system Python ≥ 3.11.
- **E6 — plists carry `SCOUT_DATA_DIR`.** `install_schedule_plist` and
  `install_heartbeat_plist` render the bootstrap vault into
  `EnvironmentVariables` (and the log paths), so a non-default vault root
  works for launchd jobs; the runners already export it.

Adjacent, not in scope, but should land in the same plugin release because
the native wizard would otherwise reproduce it faithfully:
[scout-plugin#229](https://github.com/Raven-Scout/scout-plugin/issues/229)
(`--max-budget` never reaches the daily budget check).

## 8. Data flow

```
launch ─► EngineLocator ─► state ─┬─ .managed & healthy ──► tabs (today's app)
                                  ├─ .managed & bundled > installed ──► EngineUpgrader (2..5) ─► tabs
                                  ├─ .external ──► tabs + Settings ▸ Engine shows source; hand-off if behind
                                  ├─ .notInstalled / .broken / vault missing ──► OnboardingView (steps 1..7)
                                  └─ any ─► EngineHealthService.needsAttention ─► badge

onboarding step 3 ─► EngineInstaller 1..4 ─► Claude Code has marketplace "scout-plugin" → engine/current
onboarding step 5 ─► scoutctl connectors detect --json ─► toggles + inputs
onboarding step 6 ─► scoutctl bootstrap auto --json ─► vault, plists, shim, pointer ─► doctor --json
```

Error handling follows the repo's discipline: shell-outs never throw into
the UI; every failure becomes a state with a message and a retry. Work runs
off the main actor; published state mutates on `@MainActor` (the WriteOp
isolation lesson in project memory).

## 9. Relationship to open designs

**[#74 — in-app updates](https://github.com/Raven-Scout/Scout/pull/74).**
Unchanged: the Sparkle app track, `UpdateService`, `SemVer`,
`PluginManifests`, the badge, Settings ▸ Updates. Amended: the **plugin row's
source of truth and action**. For `.managed` engines, *installed* is the
pointer's version, *latest* is the bundled version, and the action is
**Update engine** → `EngineUpgrader` (applied in-app). For `.external`
engines, #74's design stands verbatim: latest resolved source-aware from the
marketplace, action = copy `/scout-update`. #74's "the app cannot apply a
plugin update" becomes "the app applies updates to engines it manages." The
two PRs do not conflict in code: this design's types live in `Scout/Engine/`,
#74's in `Scout/Services/Updates/`; the plugin row wiring is one small edit
on whichever lands second. Sequencing recommendation: land #74 first — its
Sparkle track is what makes "engine fixes ride app releases" cheap.

**[#99 — monorepo](https://github.com/Raven-Scout/Scout/pull/99).**
Layout-agnostic here: the only path-dependent piece is
`bundle-engine.sh`'s source, which becomes `plugin/` in-tree (no network, no
clone) and `engine-release.json` becomes a derived file. This answers #99's
open question 2 ("`install.sh` fetch the DMG vs #51 app-first — reconcile in
one design"): **the app is the primary macOS entry point; `install.sh` stays
the terminal/Linux entry point; both must produce the same machine** — the
layout in §4.1 and the pointer in §4.2 are that shared contract. `install.sh`
converging on the layout is phase 3 (§10). #99's `requiredPluginVersion`
floor is subsumed: for managed engines the floor is the bundled version by
construction; for external engines `EngineHealthService` compares the
adopted version against the bundled one and shows *engine behind*.

**`2026-04-24-scout-unification-design.md` §8.** This is the "first-run wizard
if either dir unresolved" and `EngineClient` that spec called for, built on
the `scoutctl bootstrap` pipeline Plan 8 delivered in between.

## 10. Adoption, migration, rollout

| Machine state found | `EngineState` | App behavior |
|---|---|---|
| nothing | `.notInstalled` | onboarding, fresh install |
| pointer `managed_by: scout-app` | `.managed` | normal; auto-upgrade when the bundle is newer |
| `~/scout-plugin/.venv` or pointer `managed_by: dev` | `.external(.devCheckout)` | adopt read-only; never modify; hand-off when behind |
| GitHub marketplace + cache venv, or pointer `managed_by: install.sh` | `.external(.marketplaceCache)` | adopt; **Migrate to app-managed** button (phase 3) |
| vault present, no engine | `.broken` | onboarding from step 3; `bootstrap auto` dispatches to `upgrade` |
| engine present, no vault | `.managed(vaultBootstrapped: false)` | onboarding from step 4 |

**Migration (phase 3)** = install the bundled engine into the canonical
layout, `claude plugin marketplace remove scout-plugin` (the GitHub one) then
`add …/engine/current`, `bootstrap auto` → `upgrade` (re-renders plists and
shim, rewrites the pointer as `scout-app`). The old cache copy is left for
Claude Code to prune. One click, reversible by re-adding the GitHub
marketplace.

**Rollout**

- *Phase 0 — engine (scout-plugin).* E1–E6 (+#229) → plugin release
  `v0.10.0`. Ships first; the app pins it.
- *Phase 1 — app, adopt.* `EngineLocator`, `EngineHealthService`, Settings ▸
  Engine, `AppState` wiring, configurable vault, empty-state routing. No
  installer yet. Fixes #99's dead candidate path and #51's silent blanks on
  its own, for every existing install.
- *Phase 2 — app, install.* `bundle-engine.sh`, `EngineRelease`,
  `PrerequisiteChecker`, `EngineInstaller`, `EngineUpgrader`, onboarding.
  First "one download" release; announced in the app README and the plugin
  README's install section (the `curl` one-liner stays for terminal users).
- *Phase 3 — converge.* Legacy migration button; `install.sh` writes the
  canonical layout + pointer; #74's plugin row consumes `EngineHealthService`.

Rollback at any phase: the engine pointer and layout are additive; removing
`~/.local/share/scout` and `~/.local/state/scout` and re-adding the GitHub
marketplace restores today's world. The vault is never moved.

## 11. Open questions for review

1. **Engine auto-upgrade after an app update: automatic or prompt?**
   Recommended: automatic with a progress sheet — the user consented to the
   app update, the pipeline is sidecar-safe, and a new app with an old
   engine is the skew this design exists to remove. Alternative: a
   one-click prompt like Sparkle's.
2. **Bundled-only v1, or also a "latest from GitHub" channel (approach B)?**
   Recommended: bundled only. B is a natural later addition behind the same
   installer.
3. **Claude Code install hand-off shape.** Recommended: open Terminal with
   the documented `curl … | bash` visible. Alternatives: link to the
   Desktop app download; or link to the docs page only.
4. **Sign-in before or after engine install?** Recommended: after — it is not
   needed to install anything, and blocking on it would stall users who want
   to look around first. The badge and Settings row keep nagging.
5. **Keep one previous engine version on disk** (recommended, no UI) — or
   none, or expose a Roll back button.
6. **Distribute the engine tarball as a release asset too?** Not needed by
   this design; would let `install.sh` fetch the exact bundled tree later.

## 12. Facts verified on this machine (2026-09-08)

- Claude Code `2.1.259`: `claude plugin {install,update,list --json,
  marketplace {add,update,list}}`, `claude auth status --json` →
  `{"loggedIn": true, "authMethod": "claude.ai", …}`, `claude mcp list`
  prints one `name: url - ✔ Connected | ! Needs authentication | ✘ Failed`
  line per server (including claude.ai connectors), `claude --plugin-dir`
  exists. Native installer: `curl -fsSL https://claude.ai/install.sh | bash`,
  launcher at `~/.local/bin/claude`, auto-updates.
- Directory marketplace here: `known_marketplaces.json` `scout-plugin` →
  `{"source":"directory","path":"/Users/…/scout-plugin"}`; installed copy at
  `~/.claude/plugins/cache/scout-plugin/scout/0.8.0/` **contains `.venv`,
  `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.scoutctl-py-cache`**
  (the latter pointing at the source tree's venv). No
  `~/.claude/plugins/marketplaces/scout-plugin` exists for a directory source.
- `~/.local/bin/scoutctl` is the bootstrap-written shim →
  `/Users/…/scout-plugin/.venv/bin/scoutctl`. Plists set
  `PATH=__USER_HOME__/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`
  and do **not** set `SCOUT_DATA_DIR`.
- `uv 0.12.1` release assets: `uv-aarch64-apple-darwin.tar.gz`,
  `uv-x86_64-apple-darwin.tar.gz`, each with a `.sha256`. Apple's
  `/usr/bin/python3` is 3.9.6.
- scout-plugin `v0.9.0` (2026-09-03) is the latest release; releases carry
  no assets beyond the auto-generated source archives. Scout.app `v0.12.0`
  DMG is 9.5 MB.
- Hermes Agent (the model for this): `curl -fsSL …/install.sh | bash`
  installs uv, Python 3.11, Node, ripgrep; then `hermes setup`; updates via
  `hermes update`. It is a CLI, not a GUI — Scout.app takes the same
  "installer owns the toolchain" stance from inside a Mac app.

## 13. References

- [Scout#51](https://github.com/Raven-Scout/Scout/issues/51) — the tracking issue; `docs/ROADMAP.md` Phase 5.
- [Scout#74](https://github.com/Raven-Scout/Scout/pull/74) — in-app updates; `docs/superpowers/specs/2026-07-07-in-app-updates-design.md` on `feat/in-app-updates`.
- [Scout#99](https://github.com/Raven-Scout/Scout/pull/99) — monorepo; `docs/superpowers/specs/2026-09-03-monorepo-consolidation-design.md` on `docs/monorepo-consolidation`.
- [scout-plugin#26](https://github.com/Raven-Scout/scout-plugin/issues/26) + `docs/specs/scoutctl-bootstrap-auto.md` — the `auto` dispatcher this design makes E3.
- [scout-plugin#195](https://github.com/Raven-Scout/scout-plugin/issues/195), [#229](https://github.com/Raven-Scout/scout-plugin/issues/229) — adjacent config bugs.
- `docs/superpowers/specs/2026-04-24-scout-unification-design.md` §8; `2026-05-09-plan-8-scout-setup-repair-design.md` (the bootstrap pipeline this drives); scout-plugin `docs/specs/2026-06-02-release-and-distribution-system.md` §7 (`install.sh`).
- scout-plugin: `commands/scout-setup.md`, `commands/scout-update.md`, `engine/bin/scoutctl`, `engine/scout/scripts/{bootstrap,bootstrap_doctor,install_schedule_plist,install_scoutctl_shim,self_update}.py`, `scripts/install-venv.sh`, `templates/connector-probes.yaml`, `hooks/hooks.json`.
- Claude Code docs: [Advanced setup](https://code.claude.com/docs/en/setup), [CLI reference](https://code.claude.com/docs/en/cli-reference).
- Nous Research [Hermes Agent](https://github.com/NousResearch/hermes-agent).
