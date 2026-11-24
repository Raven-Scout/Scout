---
phase: mode
name: change-decisions
slot: dreaming-phase-1e
mode: [dreaming]
requires: null
---

## Step 1e: Test changes and act on your decisions

Run this every dreaming run, with or without Slack, before any brain or script edit this run (a file with uncommitted edits can't be rolled back). Each commit a run makes to `SKILL.md`, `DREAMING.md`, `RESEARCH.md` or `scripts/` is a change on the changes map, with an id like `chg-1a2b3c4d5e6f`. The engine compares {{USER_NAME}}'s engagement (replies, reactions, edits, use of {{INSTANCE_NAME}}) with the noise (DMs unanswered for 48 h, 🔴 items carried 3 days untouched) before and after each change; a reply is a good sign, never noise. A change that made things worse becomes **suspect**. {{USER_NAME}} decides what to undo and which governance changes to allow; this step carries that out. Run:

```bash
scripts/scoutctl changes due --json
```

- **When `verified` is false,** git couldn't confirm the change history, so `rollbacks`, `blocked` and `suspect_unannounced` are empty by design. Say so in the wrap, then do `apply` and `discard`.
- **`rollbacks`** (`id`, `summary`, `reason`: `owner-undo`, or `auto-…` for an automatic mode): one at a time, run `scripts/scoutctl changes rollback <id>`. It undoes the change's own lines (later edits elsewhere stay) and prints one path per line. Commit exactly those paths:
  ```bash
  {{SCOUT_DIR}}/scripts/git-safe-commit.sh "dreaming [HH:MM]: roll back <id>

  Scout-Change-Reverts: <id>
  Scout-Change-Why: owner-ask:undo-<id>
  Scout-Change-Summary: Undid: <its summary, shortened to keep this within 120 characters>" <the printed paths>
  ```
  Inside the quotes, never write `"`, `$`, a backtick or a backslash. The commit counts as the rollback only because it puts the change's lines back; the trailer just names it. For an `auto-…` reason, use `Scout-Change-Why: verdict:<id>`. Exit 2: it refused and wrote nothing; give the id and its reason in the wrap. List each rollback in the wrap: what was undone, and why.
- **`apply`** (`gov-<slug>` ids: governance changes {{USER_NAME}} approved, file `governance-pending/<slug>.md`): run `scripts/scoutctl changes apply <gov-id>`; it patches the files, moves the pending file to `governance-pending/applied/<slug>.md` and prints every changed path. Commit them the same way, subject `dreaming [HH:MM]: apply <gov-id>`, with `Scout-Change-Why:`, `Scout-Change-Summary:` and any `Scout-Change-Targets:` from the file's `why`, `summary` and `targets`. That move into `applied/` is what marks the commit as a governance change; no trailer does. A `governance: false` file (a harness fallback) is deleted instead of moved: its commit is an ordinary change, tested and undoable. Exit 2 with `patch does not apply`: say so in the wrap and leave the file.
- **`discard`** (governance changes {{USER_NAME}} rejected): `scripts/scoutctl changes discard <gov-id>` deletes the pending file and prints its path; commit it (`dreaming [HH:MM]: discard <gov-id>`), no trailers.
- **`blocked`** (`id`, `summary`, `why_blocked`): an undo that may not run; never work around it. Give each in the wrap in plain words: `governance` (it changed what {{INSTANCE_NAME}} may do; only {{USER_NAME}} undoes those), `not-a-run-change` (made by hand, by an upgrade, or itself a rollback), `root-commit` (nothing before it), `edited-since` (a later commit changed the same lines, or its files have uncommitted edits, which clears once committed), `git-unreadable` (the next run retries). Then run `scripts/scoutctl changes announced --blocked <id>` so later runs don't repeat it.
- **`suspect_unannounced`** (`id`, `summary`, `reasons`, `undo_blocked`): one plain-words wrap line each: what the change did and what got worse (`noise-up`: more ignored DMs or stale 🔴s while engagement didn't rise; `recurrence-up`: the mistake it targeted happened more; `named-negative`: {{USER_NAME}} replied negatively about it). End it by `undo_blocked`: null, "Undo in the Changes tab, or run `scoutctl changes undo <id>`"; `governance`, "It changed what {{INSTANCE_NAME}} may do, so only you can undo it, by hand"; `edited-since`, "Later edits changed the same lines; revert it by hand if you want it gone"; otherwise the reason in plain words. Then run `scripts/scoutctl changes announced <id> [<id> …]`. Never undo a suspect yourself.
- **`engagement_tests_running` ≥ 1:** a change aimed at more engagement is still under test: make no new one this run. Even at 0, at most one per run.
- **`calibration_report_due` true:** write `docs/changes-calibration-report.md` from `scripts/scoutctl changes --json --since 90d`: counts of each `test.verdict`, how many {{USER_NAME}} marked wrong (`test.marked_wrong`), and proposed thresholds. Commit it (`dreaming [HH:MM]: changes calibration report`). Each threshold you propose is a governance change: file it as a `governance-pending/<slug>.md` item (feedback processing Step 1d) whose diff adds it under `## Approved thresholds`; in the wrap, say to approve or reject it in the Changes tab. Never edit a pending file once committed; to change one, file a new slug.

**Never run the owner-only commands** `approve`, `reject`, `undo`, `wrong`, `auto-rollback` or `calibrated` (`scoutctl changes <command>`): they are {{USER_NAME}}'s decisions, and they exit 2 in a run anyway.

**Engine back-port reminder (every run).** An applied proposal or governance patch to `SKILL.md`, `DREAMING.md` or `RESEARCH.md` edits only the vault brain; the rule must also land in the engine's `phases/`, or the next `/scout-update` re-render sidecars it. Scan `dreaming-proposals.md` (`Applied` entries) and `governance-pending/applied/` for applied changes whose back-port isn't merged; if any are owed, put a standing reminder in the wrap (and carry it as an action item):

> ⚠️ Engine back-port owed: N applied change(s) not yet merged into the engine `phases/` — run `scoutctl phases backport` (dry-run first), then `--apply --plugin-root` into a clone under `~/.scout-worktrees/`, review the diff, and open a PR. **Never auto-run it** — it writes the shared/distributable engine, so it stays operator-triggered.

The write goes into a clone, never the live engine checkout (Engine Changes: Never in the Live Engine Checkout); `--apply` refuses the live engine. Clear the reminder when the back-port PR merges.
