- **Engine and CLI text counts in real plurals everywhere** ("1 proposal waits", "2 copies were never
  taken", "1 commit on top of your last push"), not "proposal(s) wait" or "cop(ies)". The last ~100
  "(s)"-style plurals across `scoutctl` output, delegate inbox and mirror notes, repo sync warnings, KB
  read, lint, share and structure lines now go through `engine/scout/plural.py`, with the verb agreeing for
  0, 1 and many (new helpers `agree` and `are`). `engine/tests/unit/test_plural_wording.py` now scans every
  module under `engine/scout/`, not three, with an explicit allow-list for the few "(s)" that count nothing
  (`http(s)`, a docstring quoting code). The "Resolve with Claude" prompt still reads the protected-file
  line from run logs written before this change.
