- **A delegate mirror can be linked when `privacy.sync_only` doesn't list `scout-config.yaml` (#550).** The
  allowlist kept the config home, but a mirror can't hold anything without it, so `delegate link` always refused
  (`config_not_mirrored`). The config is now the one file the allowlist can't keep out of a mirror, as the
  generated policy file is for Agnes. The mirror's copy then holds the `privacy` block alone, scoped to the
  mirrored files, and none of the config's other keys. When the allowlist lists the config, the mirror's copy
  keeps those keys as before.
- **A hidden note named "Scout" no longer keeps the config out of every mirror.** `scout-config.yaml` is
  Scout's own name for the file, so it's no longer matched against hidden notes' names. Its contents are still
  scanned.
