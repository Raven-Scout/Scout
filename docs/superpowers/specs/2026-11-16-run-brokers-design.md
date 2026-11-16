# Phase 1d: a commit broker and a send-only Telegram broker for scheduled runs

Status: **approved, built** (2026-11-16). Jordan accepted every §11 recommendation; the
decisions are recorded there. Follows Phase 1c (spec
`2026-11-15-run-write-sandbox-design.md`, PR #642, shipped in v0.18.14). Part of the
runtime-agnostic hardening (spec `2026-11-13-runtime-agnostic-design.md`, #634).

## 1. Why

Since v0.18.14, every scheduled run executes inside a `sandbox-exec` profile that denies
writes to the paths `hook_paths` protects. Two carve-outs remain, and both are there only
because a run does the work *in-session*:

1. **Git.** The profile lets a run write `.git/objects`, `refs/`, `logs/`, the index and
   their lock files, so `git-safe-commit.sh` and plain git keep working. The cost is that a
   run can commit an edited protected file through git plumbing (objects + `update-ref`)
   without ever writing the file. The post-run integrity check (1c §13) finds and reverts
   that, but it reacts after the fact, and a pushed commit has already left the machine.
2. **Telegram.** `~/.scout-secrets` is read-denied except `telegram-bot-token` and
   `telegram-chat-id`, because `scoutctl notify telegram` runs in-session. A run can read the bot
   token, so it can send anything to anyone the bot can reach, for as long as the token lives.

The fix for both is the same: the run *asks*, and a small trusted process outside the
sandbox *does*, after checking the request. Then the sandbox can deny every write to the
vault's git dir and every read in the secrets dir.

## 2. Goals, non-goals

Goals:

1. **Commit broker.** In-session, `scripts/git-safe-commit.sh "msg" <paths…>` keeps its
   interface, output (`committed <sha>`) and exit codes, but the commit is made by a broker
   outside the sandbox. It runs `git-safe-commit.sh` itself, so its checks and the pre-commit
   hook run unchanged. Before that, the broker refuses any path outside the vault, inside a git
   dir, or protected by `hook_paths`.
2. **Send-only Telegram broker.** In-session `scoutctl notify telegram` hands the body to the
   broker, which sends it to the owner's chat. The recipient, rate limit and size limit come
   from the owner's files, never from the request.
3. **Tighten the profile** while the broker runs: deny every write under the vault's git
   dir(s) (no carve-outs left), and deny reads of everything in `~/.scout-secrets` and
   `$SCOUT_SECRETS_DIR`, folder listing included.
4. **Nothing is lost when the broker is down.** If the broker doesn't answer, the request goes
   to a queue in the vault. The queue is drained after the session and by the 5-minute tick.
5. **No run loses functionality.** That covers the commit SHA mid-session (§3), the exit codes
   and the wrap. The replay (§9) is the gate.

Non-goals:

- Other repos' git dirs. Their hooks and config are already denied anywhere (1c §5.1), and
  nothing in them decides whether Scout's gates run.
- The engine-PR broker. It is specced as a follow-up in §8, not built here.
- Linux. The sandbox there is detection only (1c §9), so the brokers have nothing to tighten.
  The client side works there all the same.

## 3. What runs do today (50 newest scheduled runs, read-only, counts only)

Measured with a throwaway survey over `~/.claude/projects/<vault>/` (`entrypoint: sdk-cli`):
6,563 tool calls.

| Behaviour | Count |
|---|---|
| `git-safe-commit.sh` calls | 327 mentions in 44 of 50 sessions; 153 printed `committed <sha>`. Median 6 commits per session, max 14 |
| **The run reuses a SHA it just committed** | **40 of 50 sessions**: 98 Write/Edit inputs (run logs in `.scout-cache`, KB edits) and 68 Bash commands (log appends, `git log`/`show`), nearly all with "commit … run" wording near the SHA |
| HEAD lookups (`rev-parse`/`log -1` on HEAD) | 84 |
| Direct git writes **in the vault** | `checkout --` 26, `add` 25, `restore` (`--staged`/`--worktree`/`--source`) 24, `commit` 19, `rm` 15, `stash` 8, `reset` 4, `mv` 2, `fetch` 1. In 2–13 sessions each |
| Direct git writes **elsewhere** | `fetch` 8 (other repos), engine clones in `~/.scout-worktrees` (`clone`, `switch -c`, `commit`, `push`, `stash`), which 1c option (a) already refuses |
| Read-only git | `log` 277, `status` 110, `show` 69, `grep` 37, `diff` 26, `stash list` 8 |
| `scoutctl notify telegram` | 25 calls in 17 sessions (1–2 per session); body p50 306 chars, p90 572, max 1,134; 0 errors, 0 duplicate refusals |
| `~/.scout-secrets` named in a command | 4 (listing the folder) |

The second row decides the shape. **Runs need the SHA back during the session**, so a
broker that commits only after the session would break 40 of 50 runs. The commit broker
answers synchronously, the way `git-safe-commit.sh` does today.

## 4. Threat model (what changes from 1c)

The adversary is the same: the run's own agent, steered by injected content, with a shell as
the user inside the sandbox. New in this phase:

- **The broker is a trusted process that takes requests from the sandbox.** Its request
  parsing, its argv and its environment are now attack surface. Mitigations: a fixed verb
  set; JSON with size caps; no argv or env from the request except the commit message, the
  paths and `SCOUT_LINT_OVERRIDE`; paths validated before git sees them; git run with
  `GIT_LITERAL_PATHSPECS=1` and a clean environment; Telegram limits held in the broker's
  memory and in owner files, never in the vault.
- **What the broker executes**: `bash <vault>/scripts/git-safe-commit.sh`, git, and the
  pre-commit hook. All three are owner-controlled: `scripts/git-safe-commit.sh` is a protected
  runner file, and the hook and the engine it calls are protected roots. The hook keeps
  treating the commit as a scheduled run's, because the broker passes `SCOUT_MODE` and
  `SCOUT_READER` on from the wrapper, which the run can't change.
- **The socket**: in a fresh `mktemp -d` folder (mode 0700) under the wrapper's `$TMPDIR`.
  The profile denies writes there, so a run can't remove or replace the socket. Another
  user can't reach it. A run that opens its own socket and points `SCOUT_RUN_BROKER` at it
  only talks to itself, and that process is still sandboxed.
- **The queue** lives in the vault, so a run can write it. Anything drained from it is
  validated exactly like a live request: the queue gives a run no more than the socket does.
  The drain runs outside the sandbox, from the wrapper or the tick.
- **What stays with other layers**: the content a run commits (the pre-commit hook's privacy
  and size checks), and what it sends to the owner's private chat (`send()`'s outbound check
  for group chats). The broker only makes sure that the recipient is the owner's, the paths
  are allowed, and the volume is bounded.

What the tightening buys: in-session git can't write any object, ref, index or config in the
vault, whatever flags or `GIT_*` variables a command uses (`--no-verify`, `-c core.hooksPath`,
`GIT_CONFIG_*`). Per-command hook skipping (1c non-goal) is no longer possible for the
vault, because the only commit path runs git with the broker's argv and environment. The
integrity check stays as a belt (Q8).

## 5. Design

### 5.1 Placement and lifecycle

```
run-*.sh   (unchanged: lock, budget, gate probe, `scoutctl sandbox profile`, integrity snapshot)
  └─ scripts/claude-with-retry.sh
       ├─ BROKER_DIR=$(mktemp -d "$TMPDIR/scout-broker.XXXXXX")
       ├─ scoutctl broker serve --vault V --dir D --parent $$ &      (outside the sandbox)
       ├─ scoutctl broker profile --vault V --dir D                   (waits ≤10 s for the socket,
       │     reads the 1c profile from SCOUT_SANDBOX_PROFILE, appends §5.4's rules, self-tests,
       │     prints the result; non-zero = keep the 1c profile)
       ├─ export SCOUT_RUN_BROKER=D/b.sock GIT_OPTIONAL_LOCKS=0
       ├─ attempts: sandbox-exec -p "$PROFILE" claude …               (the broker outlives retries)
       └─ on exit (trap): stop the broker, `scoutctl broker drain --vault V` (outside), rm -rf D
  └─ integrity verify, post-session steps   (unchanged)
```

- **All of it is in `claude-with-retry.sh`.** The runner templates don't change, which keeps
  this away from the Phase 1 runtime-interface work in `run-*.sh`. The broker is started once
  per run and lives across retries. It also exits by itself when `--parent` is gone, so a
  wrapper killed with SIGKILL doesn't leave it running.
- **The tightening depends on the broker.** The rules that deny the git dir and the Telegram
  files are appended only after the broker answers. They are also appended only when the
  vault's `git-safe-commit.sh` has the client mode, which `broker profile` checks for. So a
  newer engine with older vault scripts (a parked sidecar, an upgrade half done) runs as 1c,
  never as "commits denied, nowhere to send them". The base profile from `scoutctl sandbox
  profile` doesn't change, and neither does its exit-code contract (1c §8).
- **Observability.** The wrapper writes `.scout-logs/.run-broker.json`
  (`{"at", "status": "on"|"unavailable"|"off", "reason"}`), next to `.claude-sign-in.json`,
  and a line in the run log. Health turns yellow on `runs.broker_unavailable` when the
  newest run was sandboxed but had no broker. Doctor adds one line.

### 5.2 Protocol

One JSON object per line on the unix socket, one reply line, then the connection closes.
Requests are capped at 1 MiB, replies at 64 KiB.

```json
{"v": 1, "verb": "commit",   "message": "…", "paths": ["…"], "override": "…"?}
{"v": 1, "verb": "telegram", "tier": "info|action_required", "parse_mode": "none|html",
 "body": "…", "allow_duplicate": false, "dry_run": false}
{"v": 1, "verb": "ping"}
→ {"v": 1, "exit": 0, "stderr": "…", "stdout": "…"}
```

The reply mirrors the command the client stands in for. The client prints `stderr` and
`stdout` and exits with `exit`, so a run sees exactly what it sees today.

### 5.3 The commit verb

**Client.** `scripts/git-safe-commit.sh` keeps its usage and blanket-pathspec checks. Then,
when `SCOUT_RUN_BROKER` is set, it hands over:
`exec "$VAULT/scripts/scoutctl" broker commit -- "$MSG" "$@"`. Owner sessions don't have the
variable, so nothing changes for them. A run that unsets it gets the direct path, which the
kernel now refuses: a failure, not a bypass.

**Broker checks, before git runs** (refusal = exit 2, the script's usage code, with the reason):

- message: non-empty, ≤ 10,000 chars, no NUL;
- paths: 1–500 of them, each ≤ 4,096 chars, no NUL or other control character, not starting
  with `-` or `:` (no option, no pathspec magic), none of the script's blanket forms;
- each path, taken relative to the vault (an absolute path must be inside it), is normalised.
  It is refused if it is outside the vault after `realpath`, if it has a `.git` component, or
  if `hook_paths.protected_write` names it (judged both as spelled and resolved, case-folded,
  like the gates);
- `override` (`SCOUT_LINT_OVERRIDE`, write-protocol's escape hatch after two failed lint fixes)
  is ≤ 500 chars with no control characters. It is the only variable a request can set. The
  hook still logs it and never lets it lift a privacy block.

**Then** it runs `/bin/bash <vault>/scripts/git-safe-commit.sh MSG PATHS…` with `cwd` = the
vault, a 300 s timeout, and a clean environment: `PATH`, `HOME`, `USER`, `LOGNAME`, `LANG`,
`LC_*`, `TMPDIR`, `SCOUT_MODE`, `SCOUT_READER`, `SCOUT_DATA_DIR` = the vault,
`GIT_LITERAL_PATHSPECS=1`, and nothing else from `GIT_*`. It replies with the script's exit code
and output. Commits are serialised by the script's own mutex, so overlapping runs, each with
its own broker, behave as today.

**Moves and deletions** don't need git in-session: `mv a b` (or `rm a`), then
`git-safe-commit.sh "msg" a b`. The helper already commits a vanished tracked path as a
deletion. `kb-management.md` and `kb-page-standard.md` say `git mv` today; they change to that.

### 5.4 What the profile adds (appended last, so it wins)

```scheme
;; Phase 1d (scout.sandbox.broker): the broker commits, so the vault's git dir is read-only…
(deny file-write* (subpath "<each vault git dir, resolved and as spelled>") (literal "<vault>/.git"))
;; …the secrets are unreadable, Telegram's included, and so is the folder listing…
(deny file-read-data (subpath "<~/.scout-secrets and $SCOUT_SECRETS_DIR, both forms>"))
;; …and the broker's socket folder can't be changed.
(deny file-write* (subpath "<broker dir>"))
```

`profile.broker_rules()` builds these. `decide()` models them (the replay uses
`build_rules() + broker_rules()`), and the kernel test checks the model against
`sandbox-exec`. Metadata stays readable: `test -s ~/.scout-secrets/telegram-bot-token` still
says whether Telegram is set up. Folder listing does not (4 uses in the 50 runs).
`phases/connectors/telegram.md` already says the section is present only when Telegram is
configured.

**The hook layer matches it.** `hook_paths.secret_read` drops its Telegram exception when
`SCOUT_RUN_BROKER` is set. Hooks get Claude Code's environment, which a run can't change,
so the Read/Grep/Bash gates refuse the token as well.

### 5.5 The Telegram verb

**Client.** `scoutctl notify telegram`, with `SCOUT_RUN_BROKER` set, sends
`{tier, parse_mode, body, allow_duplicate, dry_run}` and maps the reply onto today's exits:
0 sent, with `id=<ulid>` on stderr; 3 duplicate; 4 outbound blocked; 10 not set up; 1 failed.
One new exit, **5, "refused by the broker's limits"**, comes with the reason.

**Broker.** It calls `notify_telegram.send()` in-process. That function reads the token and
chat id itself (owner files), so **the request names no recipient**. Limits:

| Limit | Default | Where it is set |
|---|---|---|
| messages per run (per broker) | 4 (observed max 2) | `~/.local/state/scout/run-brokers/config.json` → `telegram.max_per_run` |
| messages per rolling hour, all runs | 12 | `telegram.max_per_hour`; the count is kept in `run-brokers/telegram-sent.jsonl` |
| body size | 8,000 chars (observed max 1,134; Telegram splits at 4,096) | `telegram.max_chars` |

The `run-brokers/` folder (limits, hourly count, drain lock) is one of `hook_paths`' engine roots,
next to the 1c switch file. Both layers then deny a run's write to it. Nothing in the vault, which a
run can write, sets a limit. A missing or malformed owner file means the defaults. Dry runs send
nothing and count against neither limit; the size limit applies to them too.

### 5.6 The queue: a broker that is down loses nothing

If the client can't connect (no socket, refused, or a timeout on connect), or the broker goes away
before replying, it writes the request to `<vault>/.scout-state/broker-queue/<verb>/<ulid>.json`
(ULIDs sort by time), through a temp file and an atomic rename, mode 0600:

```json
{"v": 1, "verb": "commit", "queued_at": "2026-11-16T09:00:00Z", "message": "…", "paths": ["…"], "override": null}
```

- **commit**: prints `git-safe-commit: the commit broker isn't answering; queued <id>, it
  lands after this session (no SHA yet)` and exits **6** (new, "queued"). It is non-zero on
  purpose: no SHA exists yet, and the run must not write one down. A retry only queues a
  duplicate, which drains as "nothing to commit" (exit 4).
- **telegram**: prints `id=<id>` first (the receipt line, as today), then `queued <id>: Telegram
  sends it after this session`, and exits **0**. A dry run isn't queued.
  The message will be delivered, and the phase's rule ("non-zero means not delivered") stays
  true (Q5).

**Drain** (`scoutctl broker drain --vault V`, outside the sandbox): the wrapper runs it as it
exits, and the tick runs it detached, the same way it starts the delegate-mirror sync, when the
queue isn't empty. A `flock` on `~/.local/state/scout/run-brokers/drain.lock` (owner-only, so a
run's leftover process can't hold it) makes sure only one drain runs. Entries are handled in name
order, the folder (not the file) names the verb, and each is validated exactly like a live request.
One drain has one run's Telegram allowance. A broker only ever commits for a run, so a drained
commit gets the scheduled-run checks too (`SCOUT_MODE` defaults to `scheduled`).
A sent or committed entry is deleted. A transient failure (commit mutex timeout, network) stays
queued with `attempts` + 1. An entry that fails validation, or is older than 24 h, or has had 10
attempts, moves to `broker-queue/failed/` with an event and a health finding,
`broker.queue_failed` (yellow). A queued commit takes the paths' content **at drain time**: a
later edit to the same path rides in the earlier message's commit. That is the cost of the
fallback, and it is written down here rather than hidden.

### 5.7 What changes for a run

| Today, in the vault | With the broker |
|---|---|
| `scripts/git-safe-commit.sh "m" p…` | Same command, same output, SHA and exit codes; the broker commits |
| `git add` + `git commit` (19 commits, 8 sessions) | Refused (the index is read-only). Use the helper. Phase texts already say so; write-protocol's `SCOUT_LINT_OVERRIDE="…" git commit` becomes `SCOUT_LINT_OVERRIDE="…" scripts/git-safe-commit.sh …` |
| `git mv` / `git rm` | `mv` / `rm`, then the helper with both paths (§5.3) |
| `git checkout -- p`, `git restore --worktree p` (put a file back) | `git show HEAD:p > p`, read-only git plus a work-tree write |
| `git restore --staged`, `git reset`, `git stash` | Refused. A run no longer stages anything, so there is nothing of its own to unstage or stash |
| `git status`, `log`, `show`, `diff`, `grep` | Unchanged (`GIT_OPTIONAL_LOCKS=0` keeps `status` from trying its optional index refresh) |
| `scoutctl notify telegram …` | Same command and exits, plus exit 5; the broker sends |
| `cat ~/.scout-secrets/telegram-*` | Refused |

`phases/core/write-protocol.md`, `kb-management.md`, `kb-page-standard.md` and
`git-setup.md` get the one-line replacements. A run that tries the old form gets
`Operation not permitted` from git, and the phase text names the replacement.

## 6. Failure modes

| Case | What happens | Run outcome | Health |
|---|---|---|---|
| Broker up, profile tightened | commits and sends go through the broker | as today | — |
| Broker won't start, its profile fails the self-test, or the vault's helper predates it | the run keeps the 1c profile (carve-outs, Telegram readable); log line; `.run-broker.json` `unavailable` | as today | yellow `runs.broker_unavailable` |
| Sandbox off, unavailable or conflict (1c) | no broker; owner-style direct git and Telegram | as today | 1c's |
| Broker dies mid-run | clients queue (commit exit 6, telegram 0); the wrapper's drain at exit sends them | as today, minus the SHAs of queued commits | `runs.broker_unavailable` if anything was queued |
| The wrapper is killed (SIGKILL) before its drain | the broker exits on its own (`--parent`); the queue waits for the next tick | — | — |
| A queued entry can't be applied (validation, 24 h, 10 tries) | moved to `failed/`, event logged | — | yellow `broker.queue_failed` |
| A request breaks the rules | refused with a reason (commit exit 2, telegram exit 5); nothing is queued | the run reads the reason | — |
| A commit's pre-commit hook blocks it | the hook's own output and exit 5, as today | as today | — |

## 7. Telegram and the commit broker share one process

One broker per run, two verbs. That means one socket, one lifecycle and one place to
validate. They share no state: separate locks, separate limits.

## 8. Follow-up: the engine-PR broker (not built here)

The reviewer's shape fits the queue. A run that wants an engine change writes a plain
unified diff (`diff -u` against the read-only engine checkout; it can't clone) plus a title
and body to `.scout-state/broker-queue/engine-pr/`. After the session, outside the sandbox,
the drain:

1. clones `origin/main` with `git clone --template=` (no hooks dir) into a temp folder;
2. runs `git apply --check`, then applies the diff;
3. refuses any path under `.github/`, `scripts/release*`, the CI config, or the
   marketplace files;
4. runs the same public-surface scrub the PR template asks for (no vault names), commits as
   the owner, pushes `scout/<name>`, and opens a **draft** PR.

It isn't built here because the open questions are the owner's to decide. It pushes to a
public repo under Jordan's GitHub login, so it needs his OK on whether a scheduled run may
publish anything at all, even as a draft. The scrub must be strict enough for a public
surface. It also needs a rate limit (1 PR per day?). Until then, 1c's rule stands: the
run describes the change in the wrap.

## 9. Tests and the replay

Affected tests only; CI is the gate. Defensive framing: each test asserts a positive property.

1. **Unit:** request validation: allowed paths pass; outside-vault, `.git`, protected,
   option-like, magic, blanket and oversize requests are refused, with no git call. Telegram
   limits come from the owner file and defaults, never from the request; the per-run, hourly
   and size caps hold. Queue writes are atomic, and the drain orders, retries, expires and
   moves to `failed/`. `broker_rules()` rendering. `decide()` with the broker rules: every git-dir
   write and every secrets read is denied, and ordinary vault writes are allowed.
   `secret_read` with and without `SCOUT_RUN_BROKER`.
2. **Integration (real git, any OS):** a broker on a temp socket, a fixture vault with
   the real `git-safe-commit.sh` and a pre-commit hook. A queued commit lands, the hook runs,
   and the client prints `committed <sha>` (the SHA is HEAD). A refused path leaves HEAD
   unchanged. With the broker stopped, the commit is queued (exit 6) and `drain` commits it.
   `notify telegram` goes through the broker to a stub sender, and is queued with exit 0 when
   the broker is down.
3. **macOS kernel:** under `sandbox profile` + `broker_rules`, `git add`/`commit`/
   `update-ref`/`stash` in the fixture vault are refused, and the repo is unchanged. The
   helper commits through the broker. `cat` of the Telegram token is refused, and so is a
   write into the broker folder. `git status`/`log`/`diff` work. The model matches the kernel.
4. **Wrapper:** `claude-with-retry.sh` with a stub claude starts the broker, exports
   `SCOUT_RUN_BROKER`, runs the attempt under the tightened profile, stops the broker and
   drains at exit. It falls back to the 1c profile when `broker profile` fails.
5. **Real claude, no spend:** FakeAnthropic scripted through a note write, a helper commit
   (`committed <sha>` in the tool result), a `notify telegram` (stub sender) and a direct
   `git commit` (refused). Checks: exit 0, the commit is in HEAD, the transcript was written.
6. **Replay** (`scoutctl sandbox replay --brokers`, read-only, counts only) over the 50
   newest runs: write targets and secret reads under the tightened rules; every in-vault git
   write classified by the replacement in §5.7; helper and Telegram calls, which keep their
   interface; SHA reuse after a helper call, which is kept because the broker answers in
   session. The gate: **0 calls with no replacement**, and 0 denied writes outside the git dir
   and the secrets.

## 10. Files (for the parallel Phase 1 runtime-interface card)

- New: `plugin/engine/scout/sandbox/broker.py`; tests under `plugin/engine/tests/{unit,integration}/`.
- `plugin/engine/scout/sandbox/profile.py` (`broker_rules`), `replay.py` (`--brokers`),
  `kb/hook_paths.py` (the `run-brokers` owner folder, `secret_read`), `cli.py` (`broker` group; the
  `notify telegram` client branch), `scripts/notify_telegram.py` (`cli_send`, the CLI's exit-code
  mapping, shared with the broker), `scripts/schedule_tick.py` (drain spawn), `health.py`
  (two findings), `scripts/bootstrap_doctor.py` (one line).
- `plugin/templates/scripts/claude-with-retry.sh.tmpl`: one block next to WRITE SANDBOX, plus
  an exit trap. `plugin/templates/scripts/git-safe-commit.sh.tmpl`: the client branch.
- Phase text: `write-protocol.md`, `kb-management.md`, `templates/docs/kb-page-standard.md`,
  `git-setup.md`, `connectors/telegram.md`, `action-items.md` (a re-tag's `git mv`) and
  `kb-deep-work.md` (filing loose files).
- **Not touched:** `run-scout.sh`, `run-dreaming.sh`, `run-research.sh`, `run-outcome.sh`.

## 10.1 What the build found

- **The replay** (`scoutctl sandbox replay --brokers`, the 50 newest scheduled runs, 3,919 tool
  calls, read-only, counts only): 0 in-vault git writes without a replacement; 0 writes newly denied
  outside the git dir; 2 newly denied secret reads, both listings of `~/.scout-secrets` (the
  Telegram phase now says `test -s`); 0 reads of the Telegram files themselves. Helper calls: 166
  (126 printed a SHA); 40 sessions reuse a SHA mid-session, which the synchronous broker keeps.
  Telegram: 16 calls. In-vault git writes, each with its replacement: `add` 14, `checkout --` 12,
  `commit` 11, `restore --staged` 10, `rm` 8, `reset` 3, `mv` 1. §3's survey counted some calls
  twice (it read every transcript row; the replay reads assistant and user rows only, as
  `tool_calls` does).
- **`git config user.email|user.name`** (Step 0 of `git-setup.md`): 64 calls in 28 sessions. They
  write `.git/config`, which the 1c profile already refuses, so the brokers change nothing for them;
  they have failed since v0.18.14. Follow-up: skip Step 0 in a sandboxed run.
- **Unix sockets:** `(deny file-write* (subpath D))` leaves `connect()` to a socket in D allowed and
  refuses removing or replacing it (checked against the kernel).
- **Real claude** against the fake endpoint, through the wrapper, inside the tightened profile:
  a note written, a helper commit with `committed <sha>` in the tool result, a Telegram dry run
  through the broker to the owner's chat id, a direct `git commit` refused, the token unreadable.

## 11. Decisions (Jordan accepted every recommendation, 2026-11-16, relayed by the Scout coordinator)

Q1 synchronous broker with the queue as fallback; Q2 tighten only while the broker is up; Q3 refuse
the other in-vault git writes, with the phase-text replacements; Q4 4 per run, 12 per hour, 8,000
characters; Q5 a queued Telegram message exits 0; Q6 a queued commit exits 6; Q7 deny the folder
listing too and update the phase text for the runs that listed it; Q8 keep the integrity check;
Q9 the engine-PR broker is a follow-up. The questions as asked:

- **Q1 Synchronous broker** on a socket, with the queue as fallback *(rec.)*, or a queue only,
  drained after the session? The queue-only option loses the SHA in 40 of 50 runs.
- **Q2 Tighten only while the broker is up** *(rec.)*, falling back to 1c's carve-outs with a
  yellow finding? Or always deny, and accept failed commits when the broker is down or the
  vault's scripts are older?
- **Q3 In-vault git besides commits** (`checkout --`, `restore`, `stash`, `reset`; 2–11
  sessions each): refuse them, with the phase-text replacements in §5.7 *(rec.)*, or add broker
  verbs for them? Verbs mean a larger surface for a handful of calls.
- **Q4 Telegram limits**: 4 per run, 12 per hour, 8,000 chars, owner-overridable in
  `~/.local/state/scout/run-brokers.json` *(rec.)*. Other numbers?
- **Q5 A queued Telegram message exits 0** ("will be delivered") *(rec.)*, or non-zero?
- **Q6 A queued commit exits 6** (no SHA yet) *(rec.)*, or 0?
- **Q7 Folder listing of `~/.scout-secrets`** denied too ("fully read-denied") *(rec.)*, or kept?
  Kept would leave names visible, not contents.
- **Q8 Keep the post-run integrity check** as a belt *(rec.)*. It costs ~100 ms per run and still
  covers runs with no broker.
- **Q9 Engine-PR broker**: a follow-up card after you decide §8's questions *(rec.)*, or build now?
