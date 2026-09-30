# Upgrades never lose a vault's edits to plugin-owned files

**Status:** implemented on `fix/upgrade-never-loses-vault-edits` (stacked on #251)
**Date:** 2026-09-30

## Problem

`scoutctl bootstrap upgrade` rewrites the plugin-owned files in a vault from
`templates/`. `/scout-update` runs it, and an unattended auto-update will run it
too. What happens to a vault's own edit depends on which table the file is in:

| Files | Today | Edit survives? |
|---|---|---|
| `_CAT1_FILES_FROM_PLUGIN`, `_CAT1_TEMPLATES` (scripts, hooks, `render.py`, …) | overwritten | **no**, and nothing says so |
| `_CAT1B_RUNNERS` (`run-*.sh`) | overwritten, old copy kept as `run-*.sh.bak.<date>` | only in the backup; the live runner loses it |
| `_CAT_MERGE_FILES` (`parser.py`) | 3-way merged; a conflict writes `<file>.proposed-merge` | yes, but the sidecar blocks the next upgrade |

The runner backup can't tell a hand edit from an ordinary template change,
because no record of the previous render is kept. One vault lost the same local
fixes on two upgrades in a row and restored them by hand both times. #251 moved
those fixes into the templates, but any other patch to a plugin-owned script is
still lost on the next upgrade, with no warning.

## Goals

1. An upgrade never deletes a vault edit without saying so. An edit the plugin
   didn't touch is kept as is. An edit that merges cleanly with the plugin's
   change is merged. An edit that conflicts keeps the vault's version running.
2. Nothing this adds can block a later upgrade. An unattended upgrade must be
   able to run again the next day.
3. Every kept, merged or conflicting edit is reported in `UpgradeResult`, the
   CLI, `bootstrap doctor` and `/scout-update`.
4. A vault-local fix can be turned into a plugin PR (`scoutctl bootstrap drift --patch`).
5. The first upgrade after this ships, when no vault has a snapshot yet, must
   not flag every file as edited.

Out of scope:
- The assembled `SKILL.md`, `DREAMING.md` and `RESEARCH.md` keep their sidecar
  policy, and a pending sidecar still blocks the upgrade. `scoutctl phases backport`
  covers these files.
- `.gitignore` stays append-only merged (#251).
- The install-only seeds are never overwritten, as before.

## Managed files

Every file in these four tables is a **managed file**, and all of them go
through one stage, `_stage_managed_files`:

| Table | Rendered | chmod 755 | Missing source |
|---|---|---|---|
| `_CAT1_FILES_FROM_PLUGIN` | no | no | placeholder |
| `_CAT_MERGE_FILES` (`parser.py`) | no | no | skip |
| `_CAT1_TEMPLATES` minus `.gitignore` | yes | yes | placeholder |
| `_CAT1B_RUNNERS` | yes | yes | skip |

The chmod and missing-source columns match today's behaviour. `parser.py` joins
the other managed files, so a conflict in it no longer writes a blocking sidecar.
The `_CAT_MERGE_FILES` name stays for two reasons: a sidecar an older engine left
behind still blocks the upgrade until it is resolved, and #244's
`bootstrap_auto.pending_sidecars` imports the name.

## State in the vault

```
.scout-state/last-rendered/<rel>   what the plugin last wrote, or accepted as the baseline, for <rel>
.scout-state/drift/<rel>.plugin    conflict: the plugin's new version, not applied
.scout-state/drift/<rel>.merge     conflict: the 3-way merge with conflict markers, as a starting point
.scout-state/drift/<rel>.vault     first baseline: the vault's copy that was replaced
```

Everything the doctor and `drift` report is worked out from these files, so no
separate manifest can fall out of step with them. `last-rendered/` is derived
data and is gitignored, like `last-assembled/`. `drift/` stays tracked, so a
parked edit is also in the vault's git history.

## The decision per file

Terms: *new* is the render for this plugin version, *live* is the vault's file,
*base* is `last-rendered/<rel>`.

| Case | Action | Snapshot | Reported as |
|---|---|---|---|
| live missing | write new | new | — |
| live == new | nothing | new | — |
| live == base | write new (a plugin or template-variable change) | new | — |
| new == base, live differs | keep live | new | **kept** |
| both differ, merge clean | write the merge | new | **merged** |
| both differ, merge conflicts | keep live; park new as `.plugin` and the draft as `.merge` | unchanged (base) | **conflict** |
| no base, live != new | write new; park live as `.vault` | new | **replaced** |

Two rules hold across the table:

- **The snapshot advances only past changes the vault has absorbed.** After a
  conflict the base stays where it was, so the next upgrade tries the same merge
  again with the next plugin version. The conflict is reported until it is
  resolved, and the upgrade is never blocked.
- **Parked conflict files go away once the conflict does.** If a later upgrade
  merges cleanly, or finds live already equal to new, it removes `.plugin` and
  `.merge`. `.vault` copies stay until the user dismisses them.

If `git merge-file` itself fails (git missing, timeout), the file is treated as
a conflict: the vault's version keeps running.

Merges are line-based through `git merge-file`, the same tool the brain files
use. A clean merge can still be wrong in meaning (two edits that each work
alone). That is why every merge is reported, and `drift --diff` shows the
result.

### First baseline

A vault with no `last-rendered/<rel>` gives no way to tell a vault edit from a
template change between versions. Options considered:

- **Treat the live file as the base.** An edited file then looks unedited and is
  overwritten silently, as it is today. Rejected.
- **Keep the live file and flag it.** On the first upgrade after this ships,
  every file the plugin changed since the vault's last upgrade (usually several
  per release) would be flagged as edited. None of those fixes would land, and
  an old script could be left calling engine commands that no longer exist.
  Rejected; the brief rules it out explicitly.
- **Rebuild the old render from the plugin's git history.** This only works for
  git checkouts, and fails on shallow clones. It is also wrong whenever a
  template variable changed since the last render, for example `SCOUTCTL_BIN`
  moving with the plugin root: the merge would then treat the old path as a
  vault edit and put it back. Rejected.
- **Chosen: the plugin wins, and the vault's copy is parked and reported.**
  - live == new: the baseline is recorded and nothing is reported.
  - `parser.py`: the snapshot the old merge policy kept at
    `.scout-state/last-assembled/<rel>` is exactly the base. It is carried over
    (moved) and used.
  - Anything else: new is installed and the old live file is parked at
    `drift/<rel>.vault`. The upgrade reports this once as **replaced**, and
    `doctor` keeps a note while the copy exists.

  Plugin fixes land, and a vault that never edited anything loses nothing. A
  vault that did edit keeps the edit in a parked copy and is told where it is.
  From the next upgrade on, every file has a base and gets full protection.
  `migrate-legacy` goes through the same path. The `run-*.sh.bak.<date>` backups
  are retired: the `.vault` copies replace them, and the doctor still reports old
  `.bak` files.

## Resolving

- **Conflict:** edit the live file (the `.merge` file is a starting point), then
  run `scoutctl bootstrap drift --resolve <rel>`. This records `.plugin` as the
  base, so the upgrade treats the plugin's change as absorbed and what is left as
  the vault's edit, and it removes the parked files. It refuses while the live
  file still has conflict markers.
- **Take the plugin's version:** `cp .scout-state/drift/<rel>.plugin <rel>`. The
  next upgrade sees live == new and clears the conflict by itself.
- **Replaced copy:** once reviewed, `--resolve <rel>` deletes the `.vault`
  copies. To keep the old edit instead, copy the `.vault` file back first. From
  then on it is a normal vault edit and protected as one.

## Surfacing

- **`UpgradeResult.vault_edits`**: a list of `VaultEdit(path, outcome, parked)`
  entries for kept, merged, conflict and replaced files. `backups` now lists the
  parked `.vault` copies. For `migrate-legacy`, `MigrateLegacyResult` gets the
  same two fields.
- **`bootstrap upgrade` / `migrate-legacy` CLI**: one line per edit, e.g.
  `vault edit conflict: scripts/heartbeat.sh — …`.
- **`bootstrap doctor`** (still read-only and vault-only):
  - A pending conflict is a *warning*, so the doctor turns yellow and the upgrade
    exits 1: the vault is running an older version of a plugin-owned file.
  - Kept edits and parked `.vault` copies are *notes*, a new
    `DoctorReport.notes` field. They are printed but leave the severity alone.
    Carrying an edit on purpose is not a health problem, and a yellow doctor that
    never clears teaches people to ignore it.
- **`/scout-update`**: step 3 explains each outcome and how to resolve it.
- **Auto-update**: the engine has no auto-apply runner yet; the unattended entry
  point is #244's `bootstrap auto`. The contract for any unattended caller:
  - Vault edits never make the upgrade refuse.
  - Conflicts make the doctor yellow and show in `vault_edits`.
  - `scoutctl bootstrap drift --json` is the notifier's data source. A notifier
    must not parse the human-readable output.

  #244's `result_dict` should add `"vault_edits"` once both are merged.

## Back-port: `scoutctl bootstrap drift`

```
scoutctl bootstrap drift            # one line per managed file that isn't clean
scoutctl bootstrap drift --diff     # plugin render vs live, template variables rendered
scoutctl bootstrap drift --patch    # a git-apply-able patch against templates/ for the edited files
scoutctl bootstrap drift --json     # machine-readable: status, +/- counts, parked paths, stale flag
scoutctl bootstrap drift --resolve REL
```

`--patch` reuses `phase_backport`'s ideas. Rendering substitutes single-line
values, so line *i* of a template is line *i* of its render. The patch diffs
the render against the live file, then maps unchanged lines back to the raw
template lines and re-templatizes the added lines (`retemplatize`: the
`SAFE_VARS` values go back to `{{VAR}}`). The result applies to
`templates/<file>.tmpl` in a plugin checkout (`git apply`). Added lines that
still hold an instance-specific value (`RISKY_VARS`, or any variable value in a
verbatim `.py`) are listed on stderr. The plugin repo is public, so these must
be made generic before a PR.

Some files are skipped, each with a reason:
- **Stale files**, where the plugin has changes the vault hasn't taken yet
  (render != snapshot): the diff would revert them, so upgrade first.
- **Conflicts:** resolve them first.
- Files whose render changes the line count.

A dreaming session can run `drift --json` to find vault-local fixes worth
upstreaming.

## Testing

`engine/tests/unit/test_vault_drift.py` covers the decision table as a pure
function. `test_upgrade_keeps_vault_edits.py` runs whole upgrades against tmp
vaults with a tmp plugin root, so the plugin's version can change:

- A hand edit to `scripts/heartbeat.sh`, with no plugin change, is kept and
  reported.
- The same edit plus a plugin change elsewhere merges cleanly.
- An overlapping change conflicts: the vault's version stays live, the plugin's
  is parked, the doctor is yellow, and a second upgrade runs without error.
- A vault with no edits reports nothing and gets a green doctor.
- Repeated upgrades leave the files byte-identical.
- The first baseline of a no-snapshot vault parks only the files that differ;
  `parser.py`'s legacy snapshot is carried over.
- `drift --resolve` and `drift --patch` round-trip: the patch applied to the
  template renders exactly the live file.
