---
phase: mode
name: change-decisions
slot: dreaming-phase-1e
mode: [dreaming]
requires: null
---

## Step 1e: Test changes and act on your decisions

Run this every dreaming run, with or without Slack, before any brain or script edit this run (a file with uncommitted edits can't be rolled back). Each commit a run makes to `SKILL.md`, `DREAMING.md`, `RESEARCH.md` or `scripts/` is a change on the changes map (the app's Changes tab), with an id like `chg-1a2b3c4d5e6f` (`chg-…`, the change's id on the map). The engine compares {{USER_NAME}}'s engagement (replies, reactions, edits, use of {{INSTANCE_NAME}}) and the noise (DMs with no reply or reaction within 48 h, 🔴 items carried 3 days untouched) before and after each change; a reply is a good sign, never noise. A change that made things worse becomes **suspect**. {{USER_NAME}} decides what to undo and which governance changes to allow; this step carries those decisions out. Run:

```bash
scripts/scoutctl changes due --json
```

- **When `verified` is false,** the change history couldn't be checked against git this run, so `rollbacks`, `blocked` and `suspect_unannounced` are empty by design. Say that in the wrap, then do `apply` and `discard`.
- **`rollbacks`** (`id`, `summary`, `reason`): undo each, one at a time. `reason` is `owner-undo` ({{USER_NAME}} pressed Undo) or `auto-targeted` / `auto-one-at-a-time` (an automatic rollback mode {{USER_NAME}} turned on). Run `scripts/scoutctl changes rollback <id>`; it restores the change's files and prints one changed path per line. Commit exactly those paths:
  ```bash
  {{SCOUT_DIR}}/scripts/git-safe-commit.sh $'dreaming [HH:MM]: roll back <id>\n\nScout-Change-Reverts: <id>\nScout-Change-Why: owner-ask:undo-<id>\nScout-Change-Summary: Undid: <its summary, shortened so this summary stays within 120 characters>' <the printed paths>
  ```
  For an `auto-…` reason, use `Scout-Change-Why: verdict:<id>`. Exit 2 means it refused and wrote nothing: put the id and the reason it printed in the wrap. List each rollback in the wrap: what was undone, and why.
- **`apply`** (ids `gov-<slug>`: `gov-…`, a waiting governance change, here one {{USER_NAME}} approved; its file is `governance-pending/<slug>.md`): run `scripts/scoutctl changes apply <gov-id>`. It patches the files, moves the pending file to `governance-pending/applied/<slug>.md`, and prints every changed path. Commit them the same way, subject `dreaming [HH:MM]: apply <gov-id>`, with `Scout-Change-Governance: yes` and `Scout-Change-Why:`, `Scout-Change-Summary:` and (if any) `Scout-Change-Targets:` copied from the moved file's `why`, `summary` and `targets`. Exit 2 with `patch does not apply`: the files changed since; say so in the wrap and leave the pending file.
- **`discard`** (governance changes {{USER_NAME}} rejected): `scripts/scoutctl changes discard <gov-id>` deletes the pending file and prints its path; commit it (`dreaming [HH:MM]: discard <gov-id>`), no trailers.
- **`blocked`** (`id`, `summary`, `why_blocked`): an undo that may not run. Never work around it. List each in the wrap with its reason in plain words: `governance` (it changed what {{INSTANCE_NAME}} may do; only {{USER_NAME}} undoes those), `not-a-run-change` (made by hand or by an upgrade, or itself a rollback; not tested or undone by {{INSTANCE_NAME}}), `root-commit` (the vault's first commit; nothing before it to restore), `edited-since` (a later commit changed the same files, or they have uncommitted edits right now; the second case clears once those are committed), `git-unreadable` (git couldn't read it this run; the next run retries).
- **`suspect_unannounced`** (`id`, `summary`, `reasons`): suspects no wrap has mentioned yet. One plain-words wrap line each: what the change did, and what got worse (`noise-up`: more ignored DMs or stale 🔴s while engagement didn't rise; `recurrence-up`: the mistake it targeted happened more; `named-negative`: {{USER_NAME}} replied negatively about it), ending "Undo in the Changes tab, or run `scoutctl changes undo <id>`". Once the wrap is written, run `scripts/scoutctl changes announced <id> [<id> …]` so the next run doesn't repeat them. Never undo a suspect yourself.
- **`engagement_tests_running` ≥ 1:** a change aimed at more engagement (`Scout-Change-Targets:` includes `engagement`) is still under test. Make no new engagement-targeted change this run.
- **`calibration_report_due` true:** write `docs/changes-calibration-report.md` from `scripts/scoutctl changes --json --since 90d`: verdict counts (each change's `test.verdict`: `helped`, `hurt`, `no clear effect`, `can't tell`, `rolled back`), how many {{USER_NAME}} marked wrong (`test.marked_wrong`), the `can't tell` count (away weeks caught), and proposed thresholds. Commit it (`dreaming [HH:MM]: changes calibration report`). Changing a threshold is a governance change: file each one you propose as a `governance-pending/<slug>.md` item (frontmatter `summary`, `why`, optional `targets`, then one fenced `diff` block, as in feedback processing Step 1d) whose diff adds it to the report under `## Approved thresholds`.

**Never run the owner-only commands** `approve`, `reject`, `undo`, `wrong`, `auto-rollback` or `calibrated` (`scoutctl changes <command>`): they are {{USER_NAME}}'s decisions, and they exit 2 in a run anyway.

**Engine back-port reminder (every run).** Applying a governance patch to `SKILL.md`, `DREAMING.md` or `RESEARCH.md` edits only the vault brain file; the same rule must also land in the engine's `phases/` fragments, or the next `/scout-update` re-render will sidecar it. So scan `governance-pending/applied/` for **applied governance patches whose engine back-port is not yet merged** and, if any are owed, surface a standing reminder in the wrap (and carry it as an action item — it must not silently drop):

> ⚠️ Engine back-port owed: N applied governance patch(es) not yet merged into the engine `phases/` — run `scoutctl phases backport` (dry-run first), then `--apply --plugin-root` into a clone under `~/.scout-worktrees/`, review the diff, and open a PR. **Never auto-run it** — it writes the shared/distributable engine, so it stays operator-triggered.

Whoever runs it, the write goes into a clone, never the live engine checkout (see Engine Changes: Never in the Live Engine Checkout). `--apply` refuses the live engine. Clear the reminder when the back-port PR merges.
