- **A delegate mirror can be linked when `privacy.sync_only` doesn't list `scout-config.yaml` (#550).** The
  allowlist kept the config home, but a mirror can't hold anything without it, so `delegate link` always refused
  (`config_not_mirrored`). The config is now the one file the allowlist can't keep out of a mirror, as the
  generated policy file is for Agnes. The mirror's copy then holds the `privacy` block alone, and none of the
  config's other keys. When the allowlist lists the config, the mirror's copy keeps those keys as before.
- **The mirror's `privacy` block holds only what the policy reads.** It's rebuilt, at every level, from the
  keys and fields the policy uses, in their canonical form, so a note, an address or any other value beside a
  setting stays home. `sync_only` itself never goes: no label depends on it.
- **The mirror's config is checked against a fresh rebuild on every push, even when it is HEAD's blob
  unchanged.** Before, a copy equal to the vault's own config skipped that check.
- **A hidden note named "Scout" no longer keeps the config out of every mirror.** `scout-config.yaml` is
  Scout's own name for the file, so it's no longer matched against hidden notes' names. Its contents are still
  scanned.
