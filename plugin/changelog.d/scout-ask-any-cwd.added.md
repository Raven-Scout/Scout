- `scoutctl kb vault [--json]` prints the vault a KB read should use, from any working directory. It goes by
  configuration only: `SCOUT_DATA_DIR`, else the engine pointer's vault, else `~/Scout` when there is no
  pointer. The cwd only sets `in_vault`, so a cloned repository with a planted `scout-config.yaml` is never
  taken for the vault. A relative path, a vault that is gone or a stale pointer is an error, not a fall-back.
  Exit 1 means no usable vault. `--json` also names the absolute `scoutctl` of the engine that answered, and
  `reason` says when `SCOUT_DATA_DIR` overrides the installed vault. It reads no note and writes nothing.
- A KB reader `elsewhere` (`scoutctl kb … --reader elsewhere`): the owner reading from outside the vault. It
  sees no private, local-only or shared-in note and no phone inbox, as a delegate without `include_private`
  would. Nothing uses it by default yet.
