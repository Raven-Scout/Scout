# Brain-file sidecars never block an upgrade

**Status:** implemented on `fix/brain-sidecars-never-block-upgrade`
**Date:** 2026-10-02
**Related:** `fix/upgrade-never-loses-vault-edits` (the non-blocking policy for
plugin-owned scripts, not merged yet), #244 (`bootstrap auto`)

## Problem

`scoutctl bootstrap upgrade` 3-way merges the assembled brain files (`SKILL.md`,
`DREAMING.md`, `RESEARCH.md`) against `.scout-state/last-assembled/`
(`_stage_cat4_upgrade`). Two rules combine to stall upgrades:

1. **A pending sidecar blocks everything.** A conflict writes
   `<KIND>.md.proposed-merge`, and `_refuse_pending_sidecars` makes the *next*
   upgrade raise before any stage runs. The scripts, runners, jobs and version
   stamp stay on the old plugin too, not only the brain file. An unattended
   auto-update can never run again until a person resolves the sidecar, and
   #244's `bootstrap auto` refuses the same way. After the 0.11.0 upgrade a
   real vault had to move its sidecars aside by hand.
2. **Every phase change sidecars an unedited vault.** When the vault never
   edited a file (`base == theirs`) but the plugin's assembly changed
   (`ours != theirs`), the upgrade writes `ours` to a sidecar instead of
   applying it. A routine release therefore produces a sidecar, and by rule 1
   a blocked upgrade, on a vault that did nothing.

Rule 2 exists because of the **M3 incident**: `migrate-legacy` seeds the
snapshot by copying the live file. For a legacy vault, `base == theirs` then
means "no edit history", not "no edits", and the original fast-forward wiped a
hand-grown brain (85 KB of `SKILL.md`, plus `DREAMING.md` and `RESEARCH.md`).

## Goals

1. A pending brain-file sidecar never aborts the upgrade. Only that file's
   merge is skipped, the sidecar is left exactly as the user left it, and every
   other stage runs. The skip is reported in `UpgradeResult`, the CLI,
   `bootstrap doctor` and `/scout-update`.
2. A plugin change to a brain file the vault never edited is applied, with no
   sidecar.
3. A legacy-migrated (M3-shaped) vault never has its live brain overwritten or
   merged away. This holds both when it has been edited since migration and
   when it hasn't.
4. `scoutctl phases backport` keeps working. It diffs the snapshot against the
   live file, so the snapshot must still be the assembly the live file descends
   from.

Out of scope:
- `knowledge-base/ontology/parser.py` (`_CAT_MERGE_FILES`). Its sidecar still
  blocks, as before. The drift branch moves it to the non-blocking
  `.scout-state/drift/` policy.
- The stale base after a hand-resolved conflict, a limitation that predates this
  change. When a user resolves a conflict sidecar by editing it, the snapshot
  stays at the older base. The next merge can then report conflicts that aren't
  real, but it never loses content. An explicit resolve command could fix it,
  like `drift --resolve` does.
- Restoring a deleted brain file. A missing live file is still treated as
  `theirs = ours` (the snapshot advances, nothing is written), as before.

## Rule 1: a pending sidecar skips only its own file

Options weighed:

- **Skip that file's merge; touch nothing for it** (chosen). Live, snapshot,
  sidecar and provenance stay as they are, and every other stage runs. Nothing
  the user is halfway through resolving can be overwritten. The sidecar goes
  stale as later plugin versions land, but that costs little: once the user
  resolves it, the next upgrade merges against the plugin of that day.
- **Rewrite the sidecar on every upgrade.** This would clobber a resolution in
  progress. Rejected.
- **Rewrite the sidecar only while it is byte-identical to what the engine
  wrote.** This keeps the proposal current but needs another piece of state.
  Deferred: it's an improvement on top of the chosen option, not a fix it needs.
- **Move brain conflicts into `.scout-state/drift/`** like the scripts. The
  `.proposed-merge` sidecar is the documented brain-file workflow (README,
  `/scout-update`, `phases backport` spec), so changing it is a separate
  decision. Rejected here.

`_refuse_pending_sidecars` now covers only `_CAT_MERGE_FILES`. Two helpers make
the split explicit: `brain_merge.pending_brain_sidecars(vault)` and
`bootstrap.blocking_sidecars(vault)`. #244's `bootstrap_auto.pending_sidecars`
should switch to `blocking_sidecars` once both are merged, and add `skipped` to
its `result_dict`.

## Rule 2: fast-forward only over the plugin's own assembly

The question `base == theirs` was really asking is whether the live file is
exactly something the plugin wrote. If it is, replacing it loses nothing. If
the snapshot was seeded from live, or changed outside the engine, nobody knows
what the live file contains.

Options weighed:

- **Always sidecar** (today). Stalls every routine release. Rejected.
- **Always fast-forward** (before M3). Wipes legacy vaults. Rejected.
- **Fingerprint only.** Every bootstrap assembly starts with
  ``# KIND\n\n**BASE_DIR:** ` `` (since the pipeline's first commit), while
  Plan-5-era brain files start with YAML frontmatter (`---`). This needs no
  state, but it can't see a snapshot that was overwritten by hand. Copying live
  over the snapshot to silence a sidecar is a plausible workaround, and
  fast-forwarding after it would wipe the vault's edits. Not enough on its own.
- **Fast-forward and park the replaced live copy.** This doesn't lose bytes,
  but a legacy vault's working brain is swapped for one with a different
  structure, which is the M3 outcome. Rejected.
- **Chosen: a provenance record, with the fingerprint as a one-time fallback.**

### Provenance

`.scout-state/last-assembled/provenance.json` (gitignored like the snapshots):

```json
{
  "version": 1,
  "files": {
    "SKILL.md": {"snapshot": "assembled", "sha256": "<sha256 of the snapshot as the engine wrote it>",
                 "proposed_sha256": "<sha256 of the assembly last written to a sidecar>"}
  }
}
```

- `install`, and every upgrade outcome that advances the snapshot, records
  `"snapshot": "assembled"` with the snapshot's hash and clears
  `proposed_sha256`.
- `migrate-legacy` records `"snapshot": "seeded"`.
- Writing a sidecar records `proposed_sha256` = the hash of `ours`. The
  snapshot fields stay as they are.

The base counts as a **plugin assembly** when:
- the entry records the snapshot: it is `assembled` and its hash matches the
  snapshot file. A `seeded` snapshot, or one changed outside the engine, does
  not count.
- the entry has no snapshot record (a vault last upgraded before this change):
  the snapshot starts with the assembly fingerprint. This fallback is used until
  the first upgrade that advances the snapshot.
- in any case, the snapshot file exists.

An upgrade interrupted between writing a snapshot and writing `provenance.json`
fails the hash check next time, so the file is proposed, not overwritten. A
provenance file that can't be read counts as absent, with a warning on stderr,
which puts every brain file back on the fingerprint fallback until the next
snapshot write.

### The decision per brain file

*ours* = fresh assembly, *theirs* = live file (`ours` if missing),
*base* = snapshot.

| # | Case | Action | Snapshot | Reported |
|---|---|---|---|---|
| 1 | sidecar pending | **skip**: touch nothing | unchanged | `skipped` |
| 2 | `ours == theirs` | nothing to write | → ours | — |
| 3 | live is a proposal adopted verbatim (`sha256(theirs) == proposed_sha256`) | **fast-forward**: live → ours | → ours | — |
| 4 | base isn't a plugin assembly | **propose**: sidecar = ours; live untouched | unchanged | `conflicts` |
| 5 | `base == theirs` | **fast-forward**: live → ours | → ours | — |
| 6 | both changed, merge clean | live → merge | → ours | — |
| 7 | both changed, merge conflicts | sidecar = conflict-marked merge; live untouched | unchanged | `conflicts` |

Row 4 replaces the merge as well as the fast-forward. With a seeded base, the
seed is not a common ancestor of the plugin's assembly, so a "clean" merge would
apply "delete the legacy brain, add the plugin's" to the vault. That is the M3
loss along another path. So the vault gets the plugin's version as a proposal
and decides, as the brain-structure ADR asks: Phase 2 adoption is deliberate.

Row 3 is how a proposal converges. A vault that adopts the plugin's version
(`mv SKILL.md.proposed-merge SKILL.md`) holds a file byte-identical to an
assembly the plugin produced, so the next upgrade can fast-forward it even
though the snapshot is still the seed. Without row 3, an adopted legacy vault
would get a fresh proposal on every release that touches the file.

The snapshot still advances only when the live file has absorbed `ours` (rows
2, 3, 5, 6). `phases backport` therefore still sees exactly the vault's edits:
none after a fast-forward, the edits on top of the last absorbed assembly
otherwise, and the edits since migration for a seeded vault.

## Surfacing

- **`UpgradeResult.skipped`**: the sidecar names whose file was skipped.
  `conflicts` still lists the sidecars this upgrade wrote.
- **CLI** (`bootstrap upgrade`): ``skipped (sidecar pending): SKILL.md.proposed-merge — SKILL.md left as is until it is resolved``
  next to the existing `conflict (sidecar):` lines.
- **Doctor**: a pending brain sidecar is still a warning, so the doctor shows
  yellow. A vault whose brain isn't receiving plugin changes needs attention.
  The message now says upgrades skip the file, not that they are blocked.
- **`/scout-update`**: step 0 no longer refuses on brain sidecars. It names
  them and continues. Step 3 explains `skipped` rows.

## Testing

- `test_brain_merge.py`: the decision table as a pure function, plus
  provenance read/write and the fingerprint fallback.
- `test_upgrade_brain_sidecars.py`: whole upgrades against `tmp_path` vaults
  with a `tmp_path` plugin root, so the phases can change between versions:
  - A pending sidecar: the upgrade completes, the other brain files and the
    stamp update, the skipped file, its snapshot and the sidecar are
    byte-identical, the skip is reported, and a second upgrade also runs.
  - An unedited vault takes two routine phase changes in a row with no sidecar.
    A vault from before provenance existed (no `provenance.json`) does too.
  - An M3-shaped legacy vault, unedited or edited since migration, with or
    without a provenance record, keeps its live brain byte-identical across
    upgrades. Adopting the proposal converges on the next upgrade.
  - A snapshot overwritten by hand is never fast-forwarded over.
  - `phases backport` still maps a vault edit after a skipped upgrade, and sees
    no edits after a fast-forward.
