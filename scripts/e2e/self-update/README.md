# Headless install + self-update e2e

A disposable Linux container installs one Scout release the headless way and lets it update
itself to another. This covers the manual end-to-end check in run-health spec §11
(`docs/superpowers/specs/2026-10-11-run-health-and-headless-updates-design.md`) for
`scoutctl self-update run` (#360, P7 #421, P8 #422). Use it to rehearse an update before
publishing it, for example 0.18.0 → 0.18.1 with the release branch as the target.

```bash
scripts/e2e/self-update/run.sh --from v0.18.0 --to release/v0.18.1
```

It needs Docker or Podman. If you have neither, run the **e2e-self-update** workflow
(Actions tab → Run workflow; ubuntu, free runners, manual only). The report and logs are
uploaded as an artifact.

## What it never touches

Everything runs inside the container, as a container user, with a per-scenario `HOME`
under a temp dir. Your `~/Scout`, `~/scout-plugin`, `~/.local/state/scout`, launchd jobs,
crontab, Keychain and desktop notifications are unreachable from inside. No model runs
and no API key is used: `claude` is a stub (`bin/claude`). The only host side effects are
one image (`scout-e2e-self-update`) and one temp dir. Your checkout is read through a
read-only mount, and its commits are pushed *into* a scratch repo there.

## Choosing what to test

| Flag | Meaning |
|---|---|
| `--from TAG` | The release the user has installed. It is installed with **that tag's** `install.sh`. Must be 0.18.0 or later. Default: the newest `vX.Y.Z` tag. |
| `--to REF` | What `stable` moves to: a tag (`v0.18.1`), a release candidate (`v0.18.1-rc.1`), a branch (`release/v0.18.1`), a commit, or `WORKTREE` for your uncommitted tracked changes. Default: `HEAD`. |
| `--as-version X.Y.Z` | Stamp the target as this version first, the way `release.sh prepare` does. Default: stamp only when the target isn't newer than `--from`, as its patch + 1. So `--to HEAD` on `main` rehearses "the next patch". |
| `--scenarios` | Any of `happy,rollback,sidecars` (default: all three). |
| `--work DIR` / `--keep` | The scratch dir, and whether to keep the homes and venvs after the run for poking around. `report.json` and `logs/` are always kept. |

## How the fakes work

- **The marketplace and `stable`.** The two refs are pushed into a scratch bare repo. That
  repo stands in for `github.com/Raven-Scout/Scout`, and "publishing" moves its `stable`
  and `main` branches, as `release.sh finalize` does.
- **The stub `claude`.** It implements `plugin marketplace add|update` and
  `plugin install|update|list`. It extracts the `git-subdir` the marketplace entry names
  into `~/.claude/plugins/cache/scout-plugin/scout/<version>`, one directory per version,
  as Claude Code does. Every call is logged. A `fail-<verb>` file induces a failure.
- **Two GitHub reads.** `self-update` reads the `stable` manifest from
  raw.githubusercontent.com and the release from api.github.com, at URLs fixed in the
  engine. `pysite/sitecustomize.py` (on `PYTHONPATH` in the container only) answers both
  from the scratch repo and from per-host release JSON. That works for any `--from`
  version, with no seam needed in the engine. An `offline` file simulates an outage.
- **The clock.** No time is faked. The harness picks real IANA zones whose local time is
  03:xx (off-peak, far from any default slot) or 21:20–21:55 (within 45 min of the 22:00
  dreaming slot). It sets both `TZ` and `SCOUT_LOCALTIME`, because the slot lookahead and
  the off-peak window read the zone in different ways
  ([Raven-Scout/Scout#451](https://github.com/Raven-Scout/Scout/issues/451)).
- **Notifications.** The vault's `notify.command` is `bin/notify-sink`, which records each
  push. `notify-send` is a stub that only logs.
- **Content.** The vault is seeded with the eval fixture's stand-ins
  (`plugin/engine/tests/eval/vault`, dates rendered by the eval harness's own renderer).

## What it checks

**happy:**
- The install: the pointer, `auto_update` on, and the cron jobs.
- `not_eligible(disabled)`.
- A `scout-app` pointer is refused with no `claude` call, no network read and no crontab change.
- `up_to_date`.
- `waiting_release` for a missing release, a prerelease and a soaking release.
- Three network failures give `deferred`, `deferred`, then `failed`, with a red push.
- `--dry-run` records and fetches nothing.
- `deferred` when a slot is due or the vault lock is held.
- The scheduled run outside off-peak is "not now".
- Then `applied`: the venv is built in the new root and the old root is kept. The pointer,
  shim and crontab are re-pointed, and every cron command is executable. The vault is
  stamped, the fixture content is untouched, and the doctor is not red. `scoutctl health`
  shows `update.last`. There are "updated itself" and "recovered" pushes, and the daily
  marker is written.
- Finally the 24 h apply spacing, and `up_to_date`.

**rollback** (spec §7.6):
- A `plugin update` failure gives `failed(fetch)`, then the 24 h backoff.
- A broken `install-venv.sh` gives `failed(venv)`. After each failure, the pointer, crontab,
  shim and vault stamp are unchanged, and the old engine still answers.
- `update.failed` is red, `health` exits non-zero, and a red push goes out.
- After 3 failures it stops retrying until a newer release or `--force`.
- A fixed next patch is applied without `--force`, and `update.failed` clears.

**sidecars** (spec §7.7):
- A pending `parser.py` sidecar gives `held` and a yellow `update.held`.
- A local `SKILL.md` edit that conflicts with an upstream phase change gives
  `applied_with_sidecars`. The `SKILL.md.proposed-merge` sidecar holds both sides, and the
  live file keeps the edit.
- `update.sidecars` is yellow and a "files to merge" push goes out.

**KNOWN** lines are Linux problems the harness surfaces but does not fail on. They report
PASS once the fix lands:

- [Raven-Scout/Scout#433](https://github.com/Raven-Scout/Scout/issues/433): the vault is not a git repo after setup.
- [Raven-Scout/Scout#434](https://github.com/Raven-Scout/Scout/issues/434): BSD `date -v` in `phases/connectors/github.md`.
- [Raven-Scout/Scout#449](https://github.com/Raven-Scout/Scout/issues/449): the install doctor runs a `launchctl list` check on every POSIX host. A
  Linux install is therefore always yellow, and `install.sh` exits 1 after a good install.

## What it does not cover

- **A real scheduled tick.** The harness calls `scoutctl self-update run --scheduled`
  itself, not through `heartbeat.sh` from a running cron daemon.
- **The macOS app-managed case** (§11). The app-managed half is covered in two places:
  the `scout-app` pointer step above, and the unit test
  `plugin/engine/tests/unit/test_self_update_run_app_managed.py`. That test drives the real
  `default_deps` and fails on any subprocess, network, config or doctor call. A Mac with
  Scout.app installed is still a manual check (#357).
- **Telegram.** Only `notify.command` and `notify-send` are wired.
