# Phase 1c: an OS-level write sandbox around each scheduled run

Status: **approved by Jordan 2026-11-15** (every §12 recommendation, plus Q8); built in the same PR, as-built notes in §13. Part of the runtime-agnostic
hardening (spec `2026-11-13-runtime-agnostic-design.md`, PR #634; Phase 1a #635,
Phase 1b #637). Built on #637's branch: `scout/kb/hook_paths.py` is the protected-path
list for both layers.

## 1. Why

Scout's gates are code that runs on every tool call or commit: Claude Code
PreToolUse hooks (kb-read-gate, kb-write-gate, outbound-gate, mcp-tool-gate), git's
pre-commit hook (the privacy and size blocks), and the engine both of those run. A
scheduled run that can *write* any of the files that decide whether that code runs
can switch it off, for itself or for every later run.

#637 closes that at the hook layer: a run's Write/Edit/MultiEdit/NotebookEdit calls
and its shell commands are checked against `hook_paths`. Three review rounds broke
it each time:

- a FIFO at the audit file hung the hook past its 15 s timeout, and Claude Code treats
  a timed-out PreToolUse hook as **allow**;
- a 450-path `rm` made the path check itself slow enough to time out;
- `> $(printf .claude)/settings.local.json`, so command substitution in a redirect target;
- obfuscated inline Python: `os.path.join('.claude','settings.local.json')`.

A hook can't fully parse shell and inline programs, and it fails open on a timeout.
The backstop has to be the kernel refusing the write, whatever program makes it.

## 2. Goals, non-goals, anti-goals

Goals:

1. While a scheduled run is active (`run-scout.sh`, `run-dreaming.sh` or
   `run-research.sh`, launched through `scripts/claude-with-retry.sh`), the `claude`
   process and **every descendant** can't write, create, rename, unlink, chmod or
   xattr anything `hook_paths` protects. That covers the vault git dir with its hooks,
   config, info and `core.hooksPath`, agent settings, the plugin store, Scout's engine,
   the runner scripts, user git config, LaunchAgents, shell rc files and other agent
   CLIs' config.
2. Ordinary run work keeps working: notes, the KB, `.scout-logs`, `.scout-state`,
   `.scout-cache`, commits through `git-safe-commit.sh` and plain git, temp dirs, the
   network as today, and the session files Claude Code itself writes.
3. One protected-path list. The sandbox profile is generated from `hook_paths` at
   launch, so the two layers can't drift. The few kernel-only carve-outs (§5.3) are
   listed in code next to it and pinned by tests.
4. Fail safe: if the sandbox is expected but can't be applied, the run is skipped and
   health goes red. If the platform has no sandbox, the run goes ahead exactly as today
   and health says so (§8, Q1).
5. Owner (interactive) sessions are unchanged. Only the runner wrapper applies the
   sandbox.
6. Health and doctor report whether runs are sandboxed.

Non-goals:

- Reads and exfiltration. The read gates, outbound-gate and mcp-tool-gate keep those.
- Per-command hook skipping, such as `git commit --no-verify`, `-c core.hooksPath=…`
  or `GIT_CONFIG_COUNT` in the environment. The kernel can't see a per-process git
  setting. #607's shell gate keeps these, and §6 shows what would move them into the
  kernel (a commit broker, Phase 1d).
- Root or kernel exploits, and a user who is already compromised.
- Linux enforcement in this PR (§9).

Anti-goals: this fails if a run that does nothing wrong breaks. That includes a
commit that no longer lands, a connector MCP server that can't start, or claude
failing to write its transcript. The replay and the real-run tests (§10) are the gate
for turning it on by default.

## 3. Threat model

**Adversary:** the scheduled run's own agent, steered by prompt injection in content
it reads (mail, Slack, web pages, meeting transcripts) or simply wrong. It has an
arbitrary shell as the user (Bash, inline interpreters, any binary on disk), plus
Claude Code's file tools. It wants a **persistent** change that turns off a gate for
the next commit or the next run.

**Trusted computing base:** the runner scripts *before* `claude` starts and after it
exits (they run outside the sandbox), `sandbox-exec` and the kernel, and the profile,
which is built once per run, outside the sandbox, and passed by value to every attempt
(§4).

**Paths around the profile, and how each is handled (macOS):**

| Path out | Handling |
|---|---|
| Any write by a descendant (shell, python, node, git, MCP server, hook) | Kernel `file-write*` deny, inherited across fork/exec |
| Symlink to a protected file, then write | Kernel checks the resolved vnode (tested: denied) |
| Hard link of a protected file into a writable dir | `link()` from a denied file is itself denied (tested) |
| `rename()` into or out of a protected path | Denied on both ends (tested) |
| Rename or unlink an *ancestor* (`mv ~/Scout ~/Scout.old`, then recreate it without hooks) | `file-write-unlink` deny on every ancestor dir of every protected root (tested) |
| Case or Unicode variants (`.GIT/HOOKS`, `Settings.Local.json`) | The kernel matches the stored name, and matching is case-insensitive on APFS (tested, §5.4) |
| Mount something over a protected dir (`mount_smbfs`, a disk image) | `(deny file-mount)` |
| Launch a process outside the sandbox through launchd (`launchctl submit/bootstrap`, `launchctl setenv BASH_ENV …` for the next tick) | launchd refuses sandboxed callers (tested for `setenv`: "Not privileged to set domain environment"), plus `(deny job-creation)` and `(deny process-exec /bin/launchctl)` |
| LaunchServices (`open -a Terminal x.command`) | `(deny process-exec /usr/bin/open)` and `mach-lookup` of `com.apple.coreservices.launchservicesd` |
| AppleEvents (`osascript -e 'tell app "Terminal" to do script …'`) | `(deny appleevent-send)` |
| A long-running daemon that runs commands for its clients: Docker (`docker run -v ~/Scout:/v …`), a tmux/screen server, `ssh localhost` | Unix-socket connects to `docker.sock`, `tmux-*/`, `.screen/` and TCP to loopback :22 are denied. This is the one network change. |
| cron / at | Writes to `/usr/lib/cron` and `/private/var/at` are denied (crontab is setuid; the sandbox survives exec) |
| Other spawners we haven't listed (the Claude desktop app, Scout.app, a user's own agent daemons) | **Residual.** Named in the doc and the PR; health can't see it. |
| A protected tracked file's **committed** content changes during the run (the git carve-outs let a run write objects, the index and refs, while the file on disk stays intact), so a later unsandboxed checkout would write it | The post-run integrity check (§13): outside the sandbox, after claude exits, protected tracked paths in HEAD, the index and every branch, tag and stash ref the run moved are compared with the run's start and put back in a new commit; health turns red |

Per-command git tricks stay with the hook layer (non-goal above). A run can still
*commit* with `-c core.hooksPath=/dev/null` if it also gets past #607's shell gate,
but it can't make that stick: the next commit, run or session gets the real hooks.

## 4. Where the sandbox goes

```
run-*.sh  (outside: lock, budget, gate probe, model)
  └─ scoutctl sandbox profile --vault V   → profile text on stdout, exit code = status
  └─ SCOUT_SANDBOX_PROFILE=<text> scripts/claude-with-retry.sh LOG claude …
        reads it into a shell variable, unsets the env var
        each attempt: [caffeinate -i] /usr/bin/sandbox-exec -p "$PROFILE" claude …
  └─ post-session steps, run-outcome  (outside again)
```

- **Once per run, by value to every attempt.** The runner builds the profile once; it
  is in the wrapper's memory, never in a file the run could rewrite before a retry.
- **Self-test.** Before printing, `scoutctl sandbox profile` runs
  `sandbox-exec -p <profile + one canary deny> /bin/sh -c …` against two temp paths:
  a write to the canary must fail and a write to a temp file must succeed (~20 ms).
  Either result being wrong counts as a failure.
- **The gate probe (#635) runs first, outside, and can't nest.** A second
  `sandbox-exec` inside a sandboxed process with real rules fails with
  `sandbox_apply: Operation not permitted` (exit 71), tested. So nothing inside a run
  can start its own Seatbelt sandbox. That includes Claude Code's Bash sandbox
  (`sandbox.enabled`), which the eval shim turns on (§8).
- **Compose with the probe, don't nest (follow-up).** The probe's single profile could become
  *write rules + loopback-only network*. The probe would then also prove claude starts,
  loads hooks and finishes under the write rules before any real run. Deferred (§13):
  until then, a claude version that breaks under the rules fails its run, which health
  reports as `runs.failing`.

## 5. The profile

Generated by `scout/sandbox/profile.py` from `hook_paths.protected_specs()`: the
roots #637 checks, before case-folding, each with `path`, `form` and `tree`. SBPL
is **last-match-wins** (tested both orders), so the layers go in this order:

```scheme
(version 1)
(allow default)                       ; everything else as today, network included
;; 1. every protected root (hook_paths): trees as subpath, files as literal
(deny file-write* (subpath "/Users/u/Scout/.git") (literal "/Users/u/.claude/settings.json") …)
;; 2. kernel carve-outs: what trusted programs must write under a protected root (§5.3)
(allow file-write* (subpath "/Users/u/Scout/.git/objects") (subpath "…/.git/refs") (subpath "…/.git/logs")
                   (literal "…/.git/index") (literal "…/.git/index.lock") (literal "…/.git/HEAD") …
                   (subpath "/Users/u/.claude/plugins/data"))
;; 3. re-deny protected roots that sit under a carve-out, plus .git/objects/info/alternates
(deny file-write* (subpath "<core.hooksPath if it is inside a carve-out>") …)
;; 4. ancestors: no rename or unlink of a folder that holds a protected root
(deny file-write-unlink (literal "/Users/u/Scout") (literal "/Users/u") (literal "/Users/u/.claude") …)
;; 5. no way around the sandbox (§3)
(deny file-mount) (deny job-creation) (deny appleevent-send)
(deny process-exec (literal "/bin/launchctl") (literal "/usr/bin/open"))
(deny mach-lookup (global-name "com.apple.coreservices.launchservicesd"))
(deny network-outbound (remote unix-socket (path-regex #"docker\.sock$")) … (remote tcp "localhost:22"))
(deny file-write* (subpath "/private/var/at") (subpath "/usr/lib/cron"))
```

Paths are emitted twice where they differ, as resolved (`realpath`) and as spelled,
the same pair #637 compares. The quoting escapes `\` and `"`. A path with a control
character or a newline makes the build fail, so the run is skipped, never run with
a profile that may be wrong.

### 5.1 What each protected category becomes

| `hook_paths` form | Rule |
|---|---|
| `.git` (git dir, worktree real + common dir, `.git` file) | subpath deny, then the git carve-outs (§6) |
| `.git/hooks` (`git rev-parse --git-path hooks`, honours `core.hooksPath`) | subpath deny, re-denied after carve-outs |
| `.gitattributes`, `.gitmodules`, `.mcp.json` | literal deny |
| agent settings (`~/.claude/settings*.json`, `.claude.json`, `$CLAUDE_CONFIG_DIR`, `~/.claude/plugins`, vault `.claude/settings*.json`, `.claude/{hooks,commands,agents}`, `.codex .gemini .agents .grok .cursor`) | literal/subpath deny; `plugins/data` carved out (§5.3) |
| Scout's engine (package, plugin dir, `sys.prefix` venv, `$CLAUDE_PLUGIN_ROOT`, `engine.json`, pre-commit's plugin) | subpath/literal deny. New: the pointer's venv and the sandbox switch file (§7) join `hook_paths`, so both layers protect them. |
| runner scripts (`RUNNER_FILES`) | literal deny |
| git config (`~/.gitconfig`, `$GIT_CONFIG_GLOBAL`, `$XDG_CONFIG_HOME/git`) | literal/subpath deny |
| startup files (shell rc files, `~/Library/LaunchAgents`) | literal/subpath deny |
| Scout's schedule (`.scout-state/schedule.yaml`, `triggers.yaml`) | literal deny; the loader, the tick and the trigger action also accept only the three runners (review of #642) |
| git config the runners' git reads (each git's `--system` file from `--show-origin`, `$GIT_CONFIG_SYSTEM`, `include.path` targets, asked again from inside the vault so an `includeIf "gitdir:…"` that matches it counts) | literal deny |
| programs the runners run (`~/.local/bin`, the resolved claude install and its `versions/` (the runner passes its own `CLAUDE_BIN` as `SCOUT_CLAUDE_BIN`, so an install off the scheduler's PATH counts), a user-writable Homebrew prefix's `bin`, `sbin`, `etc`, `lib` incl. `python*/site-packages`, `opt`, `Cellar`) | subpath deny; plus, kernel only, no new or changed entry directly in a user-writable PATH folder |
| ssh config (`~/.ssh`) | subpath deny; `known_hosts` and `known_hosts.old` carved out (ssh records new hosts there) |

Kernel-only rules (the hook layer lists their parts, the profile denies the whole):

| Rule | Why |
|---|---|
| all of `~/.claude` and `$CLAUDE_CONFIG_DIR`, then §5.2's list carved out | the same allowlist pattern as the git dir: a file Claude Code adds later fails closed |
| all of the vault's `.claude/` (skills, commands, agents, hooks, settings) | a session writes nothing there |
| any path ending `/.git/hooks/…`, `/.git/config`, `/.claude/settings.json`, `/.claude/settings.local.json`, `/.mcp.json` | other repos and the dev checkout; literal suffixes, no character classes, so case-insensitive matching only widens them |
| `file-write-unlink` of `.git/objects/info` | `objects/` is carved out; the folder that holds `alternates` can't be swapped |

### 5.2 What claude and its helpers write (allowed, verified with real claude 2.1.318)

`~/.claude/projects/<cwd>/*.jsonl`, `~/.claude/{session-env,sessions,shell-snapshots,backups,todos,file-history,statsig,debug}`,
`$TMPDIR`, `/private/tmp/claude-<uid>/…`, the vault (except the protected roots), `.scout-*`.
Since the review of #642 the `~/.claude` part of this list is the carve-out list itself
(`profile.CLAUDE_HOME_CARVE_DIRS`, plus `plugins/data`): everything else under `~/.claude`
is denied. The real-claude kernel test (§10.4) still finishes, commits and writes its
transcript under it.
Claude Code tries to update `~/.claude.json` and fails quietly. The session still
exits 0 and writes its transcript, and nothing in Scout reads that file for a
scheduled run (Q7).

### 5.3 Kernel carve-outs (the only places the kernel layer allows what the file-tool layer denies)

The file-tool layer judges the *agent's* writes. The kernel also sees writes by
trusted programs the agent runs. Each carve-out is under a protected root and is
pinned by a test (the list is `profile.CARVE_OUTS`):

1. **git internals** that every commit, checkout, stash, fetch and gc writes (§6).
2. **`~/.claude/plugins/data/`**, Claude Code's `CLAUDE_PLUGIN_DATA`, where plugins and
   their MCP servers keep their state. Scout keeps nothing there. The rest of the plugin
   store (`installed_plugins.json`, `known_marketplaces.json`, `cache/`,
   `marketplaces/`, `config.json`, the catalog caches) stays denied (Q4).
3. **§5.2's session folders under `~/.claude`**, now that `~/.claude` is denied as a whole.
4. **`~/.ssh/known_hosts`** and `known_hosts.old`.

### 5.4 Case and normalisation

On case-insensitive APFS the kernel matched `subpath`/`literal` rules whatever the
case of the rule *and* of the path, including a **new** file created under a different
spelling (tested). The catch: **regexes match case-insensitively too**, so
`[A-Z_]+_HEAD` also matched `config.lock`, and the first spike's `git config` got
through. The profile therefore uses no character-class regexes. Git's pseudo-refs are
listed by exact name, and `config` and `config.lock` are re-denied after the carve-outs
as a belt. On a case-sensitive volume, a variant spelling is a different file that
claude and git never read, so nothing is lost.

## 6. Git: how commits keep working

Measured over the 50 newest scheduled-run transcripts (counts only): runs commit
mostly through `git-safe-commit.sh` (157 calls). Direct git writes in-session cover
12 subcommands: add 15, commit 14, checkout 17, restore 12, stash 9, fetch 9, rm 4,
mv 2, tag 2, reset 2, switch 2, push 2. There were 60 `git config` calls (reads,
going by #637's replay).

**A. Carve-outs inside the git dir (recommended).** Deny the whole git dir, then allow:

- `objects/` (re-deny `objects/info/alternates` and `http-alternates`, and no unlink of
  `objects/info` itself), `refs/`, `logs/`, `rr-cache/`, `lfs/` (git-lfs's store);
- exact files, each also as `<name>.lock`: `index`, `HEAD`, `ORIG_HEAD`, `FETCH_HEAD`,
  `MERGE_HEAD`, `MERGE_MSG`, `MERGE_MODE`, `MERGE_RR`, `AUTO_MERGE`, `CHERRY_PICK_HEAD`,
  `REVERT_HEAD`, `REBASE_HEAD`, `COMMIT_EDITMSG`, `SQUASH_MSG`, `TAG_EDITMSG`,
  `packed-refs`, `gc.pid`, `gc.log`, `shallow`;
- no `rebase-merge/`, `rebase-apply/` or `sequencer/`, and no `BISECT_LOG` (review of
  #642): none of the last 50 scheduled runs used them, so a rebase, a multi-commit
  cherry-pick or a bisect inside a run fails and leaves the repo as it was;
- for a worktree, the same set under `worktrees/<name>/`, with `config.worktree`,
  `commondir` and `gitdir` still denied.

These stay denied: `config`, `config.worktree`, `hooks/` and `core.hooksPath`, `info/`
(attributes, exclude, sparse-checkout), `commondir`, `modules/`, `description`, and
anything git adds later. Allowing a list, rather than denying one, makes a new git file
fail closed. A fixture repo runs every subcommand above, plus gc, `git-safe-commit.sh`
and the pre-commit hook, under the generated profile (§10.2).

**B. Commit broker (stronger, Phase 1d candidate).** Deny *all* writes to the git dir,
and run `git-safe-commit.sh` outside the sandbox: a small server started by the wrapper
on a unix socket, taking `{message, paths}` and running git with a clean environment and
fixed argv. The kernel would then also stop `--no-verify`, `-c core.hooksPath` and
`GIT_CONFIG_*`. The cost: those 80-odd direct calls (checkout, stash, restore, fetch…)
would all fail, or need broker verbs. And the broker is a new unsandboxed process taking
requests from the sandbox, so its argv and env handling become the attack surface.

**C. No git writes in-session at all,** with every commit made post-session. This
changes run behaviour (and breaks the 157 helper calls), so it's rejected.

Recommendation: **A now**. B becomes worth building if #607's shell gate keeps
missing per-command forms (Q3).

## 7. The switch

`sandbox.scheduled_runs: true|false` can't live in `scout-config.yaml`, because a run
can write that file and so could switch the sandbox off for the next run. The switch:

- **a protected owner file**, `~/.local/state/scout/run-sandbox.json`
  (`{"scheduled_runs": false}`), written by `scoutctl sandbox off|on`. It joins
  `hook_paths`' engine roots, so both layers deny a run's write to it;
- **the environment variable `SCOUT_RUN_SANDBOX=off`**, for tests and the eval harness.
  A run can't change a later run's environment: launchd refuses `setenv` from a
  sandboxed process, and the plists and rc files are protected;
- **default: on** once the replay (§10.3) and the real-run tests are clean, which is
  what this PR proposes.

## 8. Failure modes

`scoutctl sandbox profile` exit code → what the runner does → run-outcome → health:

| Case | Exit | Runner | Outcome row | Health |
|---|---|---|---|---|
| Profile built, self-test passed | 0 | runs sandboxed | `sandbox: enforced` | nothing |
| macOS, `sandbox-exec` present but the build or self-test fails | 3 | **skip** | `skipped_sandbox` | red `sandbox.failed` (with the reason from the log) |
| No OS sandbox here (Linux in this PR, macOS with no `sandbox-exec`) | 6 | runs as today | `sandbox: unavailable` | yellow `runs.unsandboxed` (Q1) |
| Owner turned it off | 7 | runs as today | `sandbox: off` | nothing (doctor shows a note; §13) |
| Claude Code's own Bash sandbox is on in the effective settings (can't nest) | 8 | runs as today, Claude's sandbox only | `sandbox: conflict` | yellow `runs.unsandboxed` (Q2) |
| scoutctl missing | 127 | **skip** (same rule as #635) | `skipped_sandbox` | red |
| claude itself breaks under the rules (a future version needs a new path) | (run) | the run fails | `failed` | `runs.failing` (the composed probe would catch it first; follow-up) |
| Turned off by `SCOUT_RUN_SANDBOX=off` in the scheduler's environment | (runner) | runs as today | `sandbox: off`, `sandbox_source: env` | note `runs.sandbox_env_off` |
| Integrity check put a protected tracked file back after the run (§13) | `verify` 10 | carries on | `integrity: restored` | red `run.integrity_restored` for 7 days |
| … a tag or the stash still holds the change (not rewritten) | `verify` 11 | carries on | `integrity: unrestored` | red `run.integrity_restored` |
| The integrity check couldn't run, or scoutctl took no start snapshot | `verify` 12, or no snapshot | carries on | `integrity: error` | yellow `run.integrity_unchecked` (`unchecked`, recorded only when there's no scoutctl at all, says nothing) |

The eval harness sets `SCOUT_RUN_SANDBOX=off`, because its sessions run under Claude
Code's Bash sandbox with `failIfUnavailable`.

Doctor shows a line: `run sandbox: enforced (sandbox-exec, self-test ok)`, `off (turned
off by scoutctl sandbox off)`, `unavailable on linux` or `failed: <reason>`. It never
starts claude. It judges the switch as the scheduler sees it: `SCOUT_RUN_SANDBOX` in the
doctor's own shell is left out (and named in a note; with it off, the doctor reports the
owner's switch file and builds no profile), and whether the last run was off, and from
where, comes from that run's `sandbox_source`, so its advice names the right switch. The doctor JSON shape doesn't change (same pattern as #635), so the
contract fixture stays as it is.

Side fix: `doctor`'s `launchctl list` check treats a non-zero exit as "couldn't check",
never as "jobs not registered". Inside the sandbox, `launchctl list` is refused.

## 9. Linux

- **bubblewrap** (`bwrap --dev-bind / / --ro-bind P P …` for each protected path that
  exists, `--die-with-parent`). It covers existing files and trees. A protected *file*
  that doesn't exist yet (`~/.claude/settings.local.json`) can't be bound, so a run
  could create it. A partial fix is binding the parent dir read-only, which is too broad
  for `~/.claude`. It needs user namespaces, which some distros disable.
- **Landlock** only grants access. A rule can't deny a subtree inside a tree it allows,
  so "the vault minus `.git/hooks`" isn't expressible. Unsuitable on its own.
- **This PR:** detection only. Linux records `sandbox: unavailable`, health shows
  yellow, and nothing about the run changes. Follow-up card: a bwrap backend behind the
  same `profile` → argv interface, tested in the Docker e2e harness.

## 10. Test plan

Affected tests only. CI is the gate.

1. **Unit:** profile rendering from specs (quoting, dedup, order, ancestors, carve-outs,
   re-denies, refusal of bad paths); `decide(path)`, a Python model of the rules with
   last-match-wins and case-folding, used by the replay; every carve-out under a
   protected root; the runner templates and the probe use the same builder; switch
   precedence (env, file, default); exit codes.
2. **macOS kernel tests (skip elsewhere):**
   - `decide()` cross-checked against real `sandbox-exec` on a fixture tree (the model
     can't drift from the kernel);
   - the attack corpus: every #637 review break plus §3's list (redirect with `$(…)`,
     obfuscated python, rename-over, hard link, symlink, chmod, xattr, parent rename,
     case variants, `git config`, a write into the hooksPath dir, an engine-cache
     write, `launchctl setenv`);
   - the git matrix: every subcommand from §6 plus gc and `git-safe-commit.sh` in a
     fixture repo with a pre-commit hook, which must succeed (and the hook must run),
     while `git config x y`, `.git/hooks/*` and `.git/info/*` must fail;
   - the wrapper: `claude-with-retry.sh` with a stub claude, sandboxed on exit 0;
     `skipped_sandbox` on 3; `unavailable` runs.
   - Not run: `launchctl submit`/`bootstrap`, which would load a job (ground rule),
     and Docker. Both rely on the tested `setenv` refusal and on the rules' syntax
     being accepted. Named as unverified.
3. **Replay (read-only):** `scoutctl sandbox replay --transcripts DIR --limit 50` reads
   the newest scheduled-run transcripts (`entrypoint: sdk-cli`). For every Write, Edit,
   MultiEdit and NotebookEdit path, and every path #637's shell parser says a Bash
   command writes (`live_run.run_escape` with a collecting callback), it asks
   `decide()`. It prints counts only: calls, would-deny by form, and would-deny calls
   that main's hook layer allows (the false-deny count). The gate for "on by default"
   is **zero would-deny calls outside the known-bad set**.
4. **Real claude, no spend:** a fixture vault and temp HOME, `--plugin-dir` this
   plugin, FakeAnthropic scripted through a note write, a log append, an in-session
   commit (the pre-commit hook ran), a temp write, and attempts on `.git/hooks`, user
   settings, vault settings, `.git/config` and the engine cache. Checks: exit 0, the
   transcript is written, every attempt gets EPERM. The spike already passes on 2.1.318.
   Skipped when no claude is on PATH, the same rule as #635's tests.

## 11. Build order (one PR, stacked on #637)

1. `hook_paths.protected_specs()` (the un-folded list `protected_roots` is built from),
   plus the pointer venv and the switch file.
2. `scout/sandbox/profile.py`: specs → SBPL, `decide()`, self-test, switch.
   `scoutctl sandbox profile|status|on|off|replay`.
3. `claude-with-retry.sh.tmpl` and the three runners; `run-outcome.sh` gets
   `skipped_sandbox` and a `sandbox` field.
4. The probe composes the write rules; the cache key gets the profile's hash.
5. Health `sandbox.failed` / `runs.unsandboxed`; doctor line; the `launchctl list` fix.
6. The eval harness and its tests set `SCOUT_RUN_SANDBOX=off`; CHANGELOG.

## 12. Decisions (Jordan, 2026-11-15; each the recommendation)

- **Q1 No OS sandbox on this platform:** run as today with yellow health *(rec.)*, or
  skip the run?
- **Q2 Claude Code's own Bash sandbox is on (they can't nest):** run with Claude's only,
  yellow *(rec.)*; or force Claude's off with `--settings` and use ours; or skip?
- **Q3 Git:** carve-outs now, broker as Phase 1d if per-command bypasses keep turning up
  *(rec.)*; or the broker now?
- **Q4 `~/.claude/plugins/data` carve-out:** allow it, so plugin MCP servers keep their
  state *(rec.)*; or deny it with the rest of the plugin store and accept that some
  plugins' servers may fail?
- **Q5 The one network change:** deny connects to Docker, tmux and screen sockets and to
  loopback ssh *(rec.)*; or leave the network exactly as today and accept those spawners
  as residual?
- **Q6 Default on in this PR** once the replay and the real-run tests are clean *(rec.)*,
  or ship it off and flip it after a week of your live runs? (Your live setup only gets
  it at the next manual upgrade either way.)
- **Q7 `~/.claude.json`:** keep it write-denied (scheduled runs don't persist Claude
  Code's per-project state; tested harmless) *(rec.)*?
- **Q8 `~/.scout-secrets` (#636's review):** deny reads of the contents and every write,
  for `~/.scout-secrets` and `$SCOUT_SECRETS_DIR` (what `scout.linear_activity.secrets_dir()`
  resolves). Metadata (`stat`, `test -s`) and listing the folder stay allowed, and so do
  reads of `telegram-bot-token` and `telegram-chat-id`, because the Telegram wrap is sent
  from inside the session (18 of the last 50 runs). *Alternative, not taken:* deny
  everything now and accept failed Telegram wraps until a send-only Telegram broker
  exists. The broker is Phase 1d, next to the commit broker. Nothing else in the secrets
  dir is read in-session (linear-api-key is pre-session only, the Slack token belongs to
  the tick, and the Agnes token and delegate keys had 0 in-session uses). Follow-up:
  `notify_telegram` and `delegate_mirror` hard-code `~/.scout-secrets` instead of using
  the override-aware helper.

All of Q1–Q8 were answered with the recommendation.

## 13. As built (differences from the sections above, found while building)

- **Regex carve-outs for git's per-process temp files.** Partial commits (`git commit
  <paths>`, which is what `git-safe-commit.sh` does) write `next-index-<pid>.lock`, and
  stash writes `index.stash.<pid>`. Exact names can't cover those, so two regexes do,
  digits only, because SBPL regexes match case-insensitively (§5.4). The kernel test's
  git matrix found both.
- **A read allow must use the deny's own operation.** A `file-read*` allow doesn't
  override a `file-read-data` deny, so the Telegram files and the folder listing are
  allowed with `file-read-data`. The kernel test found this; the model now treats reads
  as `file-read-data` only.
- **Dedup keeps the last copy** of a repeated rule. With last-match-wins, that copy is
  the one that decides. The first version kept the first copy and lost the re-deny of a
  `core.hooksPath` inside `refs/`; a unit test pins this.
- **`/dev/*` is never denied.** `GIT_CONFIG_GLOBAL=/dev/null` (the eval harness sets it)
  made `hook_paths` protect `/dev/null`.
- **The runner reads `SCOUT_RUN_SANDBOX=off` itself**, without asking the engine. Runner
  tests use `/usr/bin/true` as a no-op engine, which can't print a profile. The test
  suite's autouse fixture sets it, and the sandbox's own tests clear it. An engine that
  exits 0 with no profile still skips the run (`skipped_sandbox`).
- **`off` isn't a health finding.** The owner chose it, and the doctor reports it. Health
  reports `failed` (red) and `unavailable`/`conflict` (yellow) only.
- **The probe composition is a follow-up**, not in this PR (§4).
- **Replay result** (50 newest scheduled runs, 3,928 tool calls, read-only, counts only):
  2,285 write targets and 410 reads in scope. Denied: **0 writes, 0 secret reads.**
  147 path literals inside inline programs (`python3 -c`, heredocs) fall under a deny, all
  of them runner scripts the programs read or run. The kernel allows reads and execution,
  so they're not writes. 63 paths built at run time couldn't be resolved statically.
  Re-run after the review round below (the 50 newest runs then, 3,877 tool calls): 2,253
  write targets and 394 reads in scope, **0 denied writes, 0 denied secret reads**, 0
  unresolved. The replay is a **static lower bound**: it judges the paths a tool call names
  or the shell parser sees it write, not paths built at run time or the writes programs
  and git make themselves (see the clone item below).
- **Tested against the kernel** (macOS 26.5.2): the model matches `sandbox-exec` on 29
  write targets and 4 secret reads. 20 attacks left every protected file unchanged. The
  git matrix (commit, checkout, switch, restore, stash, reset, mv, rm, tag, push, fetch,
  merge, cherry-pick, gc) works with the pre-commit hook running. `git-safe-commit.sh`
  commits. Real claude 2.1.318 finished a session against FakeAnthropic, writing a note,
  committing and writing its transcript; its protected writes were refused and
  `~/.claude.json` was left as it was.
- **Found after the build: a protected tracked file's committed content could change
  without the file being written.** The carve-outs let git write objects, the index and
  refs, so HEAD could end the run holding an edited runner script while the file on disk
  stayed intact, and the next unsandboxed checkout or pull (on any machine that shares
  the vault) would write it. Jordan chose option 1 of three (a post-run integrity check;
  the others were a pre-commit refusal and the Phase 1d commit broker), built in this PR:
  - `scout/sandbox/integrity.py`, `scoutctl sandbox snapshot` and `scoutctl sandbox
    verify`. Each runner takes the snapshot outside the sandbox just before claude starts
    and keeps it in a shell variable (never a file), then pipes it to `verify` right
    after claude exits, before any post-session step.
  - The scope is every `hook_paths.protected_specs()` entry inside the vault's work tree
    and outside its git dir: the runner scripts, `.gitattributes`, `.gitmodules`,
    `.mcp.json`, `.claude/…`, the other agent dirs, the delegate state, and
    `.scout-state/schedule.yaml` and `triggers.yaml`. Paths compare NFC-normalised and
    case-folded, as the sandbox does.
  - Compared: HEAD (detached too), the index (unmerged stages too) and every branch, tag,
    stash and replace ref the run created or moved. A ref that existed is held to its
    own start content, a new one to the start HEAD's. Objects are read with replace refs
    off.
  - Restored: each changed branch (or detached HEAD) gets one new commit on top that puts
    the changed paths back as they were (the run's other work stays), by `commit-tree` +
    a compare-and-swap `update-ref`; the index entries go back too. A tag or the stash is
    reported, not rewritten (`unrestored`). The run-outcome row gets `integrity: clean |
    restored | unrestored | error | unchecked`, and health is red for 7 days after a
    restore (§8). The log names the paths and the restoring commit, never content.
  - Not compared: the work tree (under an enforced sandbox it can't change; with the
    sandbox off or unavailable, a changed file on disk is the pre-1c situation),
    remote-tracking refs (fetch moves them legitimately) and `ORIG_HEAD` (git's own
    pointer to where a reset or merge started, which only a later `reset ORIG_HEAD` reads).
  - A commit the session pushes reaches the remote before the check restores it. The
    restoring commit sits on top of the local branch, so the remote holds the change until
    the branch is pushed again, and a clone or pull elsewhere in between gets it.
  - Linked worktrees share the vault's branches, so a branch an interactive session in a
    vault worktree commits to during the run moves too (found 2026-11-19: a session's commit
    of `.claude/settings.local.json` on its own branch was "restored" onto that live branch).
    Jordan chose (2026-11-19) to attribute by evidence a run can't forge: a moved branch is
    left alone, with a log line and no finding, when a linked worktree that existed at the
    snapshot (`<common>/worktrees/*/HEAD`, read outside the sandbox) still has it checked
    out and that worktree's own index holds exactly the tip's entry for every changed
    protected path. The profile carves out only the vault's own git dirs, so a run can move
    any branch (`commit-tree` + `update-ref`) but can't write another worktree's HEAD or
    index, or create a worktree; a planted change on a session's branch fails the index test
    and is restored. The worktree is read through its admin dir, never its folder's `.git`
    file. Only under an enforced sandbox (`SCOUT_SANDBOX_STATUS=enforced`, which the runner
    exports before claude starts and the run can't change): off, unavailable or conflict
    (Linux included), the run could write that worktree's HEAD and index itself, so no branch
    is left alone. Not trusted, so still restored: a worktree made after the snapshot (with the
    sandbox off a run could make one, and `verify` can't tell), a worktree now on another
    branch, and a session whose index no longer matches its tip for a changed path.
  - Tests: `tests/unit/test_run_integrity.py` (a committed, staged, new-branch, detached,
    mid-merge and tag case on real git; an ordinary run adds no commit) and the runner
    wiring in `tests/integration/test_runner_skip_outcomes.py`.
- **More attack cases (from #637's list of what it leaves to 1c):** a script the run
  writes and then runs; `chr()`- and base64-built names in inline Python; `tar -C` into
  the git dir; `find -exec`; `xargs`; `git checkout --` of a staged blob over a runner
  script. The kernel refuses every one; all 27 attacks are pinned.

### 13.1 The coordinator's review at f88a7dcd (built in this PR)

- **Runner allowlist** (f55a1fb5): a slot's or trigger's `runner:` must be `run-scout.sh`,
  `run-dreaming.sh` or `run-research.sh`; the loader, the tick and the trigger action
  refuse anything else. `schedule.yaml` and `triggers.yaml` are protected.
- **More protected roots** (f55a1fb5 and after): each git's system config and
  `$GIT_CONFIG_SYSTEM`, `include.path` targets, the claude install with its versions,
  `~/.local/bin` (whether or not it's on PATH), a user-writable Homebrew prefix, the
  engine's `sys.base_prefix`/`base_exec_prefix`, and `~/.ssh` (known_hosts carved out).
  Scheduled claude runs set `DISABLE_AUTOUPDATER=1`. Vault scripts started from
  templates and `hooks/` run with `-I`, and a static test covers both.
- **Kernel-only wider trees**: `~/.claude` and the vault's `.claude/` as a whole, with
  §5.2's list carved out, and literal-suffix rules for any repo's hooks and config and
  any project's agent settings (§5.1).
- **Git carve-outs**: no `rebase-merge/`, `rebase-apply/`, `sequencer/` or `BISECT_LOG`;
  `lfs/` added; no unlink of `objects/info`.
- **Git's config writes**: `claude-with-retry.sh` appends `branch.autoSetupMerge=false` and
  `push.autoSetupRemote=false` as `GIT_CONFIG_*`, so `git checkout -b x origin/x` and a
  plain push write no config; `git push -u` pushes, and only its upstream record is
  refused, with a warning (kernel test).
- **The doctor**: `launchctl` that can't start (`PermissionError` in the sandbox) is a
  skipped check, not missing jobs; the switch is read as in §8.
- **The self-test** reports a profile that doesn't start by its exit code and stderr, not
  as "an ordinary write was refused".
- **`SCOUT_RUN_SANDBOX=off`** is recorded with its source and gets a health note (§8).
- **The hook's time budget** (#637's review): building the protected list inside a hook
  runs `git config --show-origin` for each git on PATH; kb-read-gate's watchdog blocks at
  8 s. The hook build now has one 4 s deadline (`hook_paths.HOOK_ROOTS_BUDGET_S`) and
  skips the git calls left after it; the profile, built once per run, has none and keeps
  every git's config. Tested with a fake git that sleeps.
- **Who writes `schedule.yaml` and `triggers.yaml`** (#637's review): the kernel now
  refuses a run's write to them. No run transcript writes either; `schedule init` is the
  owner's and bootstrap's. `scoutctl repo sync` writes `schedule.yaml` into the vault
  (`read-tree -u` of Agnes's commits, R1), but no runner step, phase, skill, command or
  hook starts it (a static test pins that), the runner's lease keepalive only renews the
  lease, and the replay counts 0 in-session `scoutctl repo` calls. So an in-session
  `repo sync` would now fail at that write, which nothing does today.
- **Smaller fixes**: the switch file is written through `mkstemp` and an atomic rename;
  the launcher's best-effort cache write sends the redirect's own error to /dev/null.

Residuals and decisions this round leaves open:

- **Decided (Jordan, 2026-11-15): (a), strict.** `phases/core/git-setup.md` now tells a
  run inside the sandbox not to start the clone, and to describe the change it would make
  in the wrap notification instead; owner sessions keep the clone flow. No eval seed:
  this is a capability the sandbox removes, not a judgement the eval can score. What
  was asked: **engine-change clones.** `phases/core/git-setup.md` told a run to
  make any engine change in a fresh clone, `git clone … ~/.scout-worktrees/$NAME`, then
  `switch -c`, commit and `push -u`. The new any-repo rules refuse that clone's
  `.git/config` and hook samples, so the clone fails (2 `git clone` calls in the 50
  runs replayed; the static replay can't see git's own writes). Options: (a) keep the
  rules strict and accept that runs can't open engine PRs; (b) carve out
  `~/.scout-worktrees/` from the any-repo rules, accepting that a later owner command in
  such a clone runs whatever hooks the run put there; (c) carve out only the clone's
  `.git/config` there, with `git clone --template=` in the phase so no hooks dir is
  written.
- **Any in-run `git clone`**, not only the engine-change one: the any-repo rules refuse
  the new repository's `.git/config` and hook samples wherever it is made, so a clone into
  a temp dir, `gh repo clone`, and a package install that clones a git dependency (pip,
  npm or uv from a `git+` URL) all fail inside the sandbox. The 50 runs replayed had no
  clone other than the engine-change one.
- **Phase 1d, an engine-PR broker** (the reviewer's shape, next to the commit and
  Telegram brokers): the session writes its change into the vault as a `git format-patch`
  series or a bundle; after claude exits, outside the sandbox, the runner makes a clean
  `git clone --template=` of `origin/main`, applies it, pushes a `scout/<name>` branch
  and opens the draft PR, so a run's engine change still reaches review without the run
  writing any repo's config or hooks.
- **`git branch -m`** renames the branch, then exits 128, because git always moves the
  branch's config section in `.git/config` and no setting stops that. 0 of the 50 runs
  used it.
- **Package-manager config** (`~/.npmrc`, `uv.toml`, `pip.conf`, user site-packages):
  follow-up, not this PR.
- **Linux**: detection only (§9).
