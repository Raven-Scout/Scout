- **The `restrict_tools` gate now applies when the hook runs outside the session's vault**
  (`hooks/prefilter.sh`). The shell stage decided whether to start the gate by walking up from the hook
  process's own working directory, while the gate itself goes by the session's `cwd` in the payload. A
  session whose `cwd` was in a vault that uses `restrict_tools`, with the hook started from a folder
  outside every vault (or in another vault without the key), left the shell without the gate ever
  running, so its restrictions weren't enforced. The shell now finds the vault from the payload's `cwd`
  too, checked as written and with its symlinks resolved, with `SCOUT_DATA_DIR` still first, and starts
  the gate whenever it can't read that `cwd` for certain (none at the top level, not a string, empty,
  relative, holding a JSON escape, or a folder it can't enter). The reverse case no longer starts the gate
  for nothing. Outside every vault it still decides without starting Python, at the cost of one read of
  the payload and one `grep`.
- **The outbound check, the KB privacy label and the shared-read record find an owner session's vault
  the same way** (`hooks/prefilter.sh`). Each of these used the hook process's working directory too, so
  an owner session in a vault, with the hook started outside it, skipped the outbound warning, the
  `[private]` label on a KB note it read, or the record `check-outbound` uses to recognise shared notes.
  Scheduled runs were not affected: they always reach Python.
