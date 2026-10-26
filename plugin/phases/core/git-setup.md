---
phase: core
name: git-setup
slot: setup
mode: [briefing, consolidation, dreaming, research]
requires: null
---

## Step 0: Git Setup

Silently ensure git is ready. Run every time without commentary:

```bash
cd {{SCOUT_DIR}}
git config user.email "{{USER_EMAIL}}"
git config user.name "{{INSTANCE_NAME}} Bot"
```

Do not mention git setup in your output unless something goes wrong.

## Using Git History

Git is a core part of the {{INSTANCE_NAME}} system — every change is committed with descriptive messages. Use the vault's history actively during runs, through `scripts/scoutctl kb delta` (it labels every changed path, so you know which changes are private):

- `scripts/scoutctl kb delta --since 1d` — see what recent runs did (avoid duplicate work)
- `scripts/scoutctl kb delta --since <commit> --diff` — inspect what changed after a commit
- `scripts/scoutctl kb delta --since 8h` — understand what happened today

The commit history is as valuable as the files themselves. When verifying KB claims or understanding what was already audited, check git before querying connectors.

### Vault Git-Delta Narration (consolidation/briefing — mandatory before composing)

Interactive sessions ({{INSTANCE_NAME}} work sessions, manual edits, `/scout-work`, audits) commit to the vault between scheduled runs. Those commits are a **primary signal source**, not background noise — a `work [HH:MM]:` commit may already have done the thing an action item is about. Before composing the run summary:

```bash
cd {{SCOUT_DIR}} && scripts/scoutctl kb delta --since <since>
```

`<since>` is the last scheduled run's commit sha (its `<mode> [HH:MM]:` commit in an earlier `kb delta`), or the hours since that run rounded up, such as `8h` (`--since` takes a commit or `Nh` / `Nd` / `Nw`, not a timestamp).

Narrate any non-session commits (anything not authored by this run) in the summary. A commit that touches any `[private]` path is narrated in full (subject, message, private paths) only on owner-only surfaces (the daily file, Telegram). In the Slack wrap, mention it only by its normal paths, never by its subject or message; a commit with only private paths is left out of Slack. **Do not assert a "quiet window" / "nothing changed since last run" until this scan returns zero non-session commits.** A silent delta in the vault contradicts a "quiet" claim and erodes trust.

## GitHub Access via `gh` CLI

All GitHub operations use the `gh` CLI, which is authenticated and has full access to both public and private repos. **Do not use GitHub MCP tools** — they have limited scope.

## Engine Changes: Never in the Live Engine Checkout

The engine checkout scheduled runs execute is `"engine_root"` in `~/.local/state/scout/engine.json`; read it there, never assume a path. On a developer's machine it is an editable install: whatever its working tree holds runs in the next scheduled run, unreviewed, and a checkout off `main` or a release tag blocks upgrades. **In it, never run `git checkout`, `git switch`, `git stash`, `git reset` or `git pull`, never create a branch, and never edit, write or delete a file.** Reading is fine.

Make any engine or plugin change in a fresh clone of `origin/main`, with `NAME` a short kebab-case name for the change:

```bash
ROOT="$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.local/state/scout/engine.json")))["engine_root"])')"
git clone -q "$(git -C "$ROOT" remote get-url origin 2>/dev/null || echo https://github.com/Raven-Scout/Scout.git)" ~/.scout-worktrees/$NAME && git -C ~/.scout-worktrees/$NAME switch -c scout/$NAME
```

Commit there, `git push -u origin scout/$NAME`, then `gh pr create --draft`. Never merge it, and never leave uncommitted work (push unfinished work as the draft). Name the PR, or the clone's path if the push fails, in the wrap notification. `scoutctl phases backport --apply` needs `--plugin-root ~/.scout-worktrees/$NAME/plugin`. The repo is public: no vault content (people, companies, issue IDs, channels). A run-health `engine.dirty` or `engine.off_release` finding means that checkout was changed: report it, and never clean or switch it yourself.

## Timezone Handling

The sandbox/server clock may run in UTC. You **must** use the run's display zone for all time checks and timestamp generation throughout the run: the computer's own timezone (the clock the scheduler fires on), unless `scout-config.yaml` sets a `timezone:` override. `scripts/scout-tz.sh` resolves it — never use bare `date`, always prefix with `TZ="$(scripts/scout-tz.sh)"`. Never hardcode a zone name: the user travels, and the resolver follows them.

**Convert ingested connector timestamps at write time.** Connector payloads frequently carry timestamps in UTC (a trailing `Z`, or `+00:00`) — never copy one verbatim into the KB, an action item, or a DM as if it were already in the display zone. A value ending in `Z` is UTC; rendered in the display zone it can shift by several hours and even across a calendar-day boundary, so writing it through unconverted silently drifts every downstream surface — and the drift changes with daylight-saving. At write time, parse the source's zone and convert to the display zone (a tz-aware datetime, e.g. `TZ="$(scripts/scout-tz.sh)" python3 -c 'import sys, datetime as d; print(d.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M %Z"))' <iso>`), then label the result. If a connector timestamp's zone is ambiguous, resolve it before writing — don't assume it matches the display zone.

**Sanity-check cited times at compose time.** When citing a timestamp described as "this morning" / "earlier today," cross-check it against `TZ="$(scripts/scout-tz.sh)" date '+%H:%M'` — if the cited hour doesn't fit the narrative (an event described as pre-8 AM carrying an afternoon time), that's a UTC-copied-as-local smell; re-derive from the source string. **One event, one time:** a single event must never appear with two different times across surfaces in the same run (e.g. the raw UTC value in one line and the converted local time in another) — pick the display zone, convert once, and use it everywhere.

### Hard Rule — Weekday Derivation

Every DM, briefing, and file header that includes a day name (e.g., "Sat Apr 18", "Monday, April 20") **must** be generated by a fresh `TZ="$(scripts/scout-tz.sh)" date '+%A %b %-d'` (or `+%a %b %-d` for abbreviated) invocation *at compose time*. Never hand-write the weekday. Never reuse a weekday string from an earlier step or file — a string written once and copied across files drifts silently, and bare `date` in a UTC-clocked sandbox is off by a day near the timezone/UTC boundary.

```bash
TZ="$(scripts/scout-tz.sh)" date '+%A %b %-d'     # e.g., "Saturday Apr 18"
TZ="$(scripts/scout-tz.sh)" date '+%Y-%m-%d'      # date for filenames
```

**Pre-send assertion:** before sending any notification or writing any file header that contains a day name, verify the day name matches `TZ="$(scripts/scout-tz.sh)" date '+%A'` for the current moment. On mismatch, regenerate — do not send.

**Canonical filename pattern:** `action-items-YYYY-MM-DD.md`, with the date part from `TZ="$(scripts/scout-tz.sh)" date '+%Y-%m-%d'`. Any day-name written inside the file header must be derived from that same date via a fresh `date` call, not hand-written.

## Session Cost Reporting (Final Action)

As the very last action before exit, log this session's cost:

```bash
{{SCOUT_DIR}}/scripts/write-session-cost.sh "$SESSION_TYPE" "${CLAUDE_MAX_BUDGET_USD:-0}" "${CLAUDE_BUDGET_SPENT_USD:-0}" 0
```

This feeds the budget-check system. If budget environment variables aren't available, pass `0` — the tracker still logs the timestamp and type.
