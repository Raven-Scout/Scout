- **`scoutctl delegate status --json` prints a versioned status document for the app's Agent copies card**
  (#550; `engine/scout/delegate_status.py`). Per delegate: whether it is linked and listed, its mirror
  remote (GitHub `owner/name`, or a local path with home as `~`, and only to the owner at the keyboard), the
  last sync and its result, why a sync was refused (its code and a fixed sentence, never the refusal's
  message), whether the mirror is behind, deploy-key counts, what the last mirror held (files, withheld,
  left out, lines removed, and the files left out as agent config, #693), and whether the state's signature
  matched (`state_unverified`). Every field it reads from `.scout-state/delegate-mirrors.json` is
  shape-checked, and a state it can't trust (or can't parse, however deeply nested) shows in the document
  instead of failing it. It never holds the key file's path, fingerprints, commit ids or note names. It
  replaces the old `--json` dump of the raw link state. The text form prints the remote the same way, and only
  to the owner at the keyboard; a delegate link state nested too deeply to parse is now refused as
  `state_unreadable` rather than raising. `tests/fixtures/contract/delegate-status.json` is its contract fixture
  (`SCOUT_REGEN_CONTRACT=1`), byte-copied to the app and checked by `contract.yml`.
- **A delegate link remembers what its last mirror held**, as counts only (`last_counts`), so the status can
  show them without a scan; `delegate sync` and `delegate preview --json` also report `agent_config`, the
  number of files left out as agent config.
