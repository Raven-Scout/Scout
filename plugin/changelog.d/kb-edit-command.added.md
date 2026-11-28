- **`scoutctl kb edit <path> --ops -`: a scheduled run changes a KB note by rule, in place of a python script**
  (`engine/scout/kb/edit.py`). Runs edited notes about once per run with python heredocs, which the read gate
  can't tell apart from a program that sends the note's text elsewhere (found in #824's replay). The
  command takes a JSON list of operations, applied in order: a literal `replace` and a `regex` replace, each
  with the number of matches the note must hold (`count`, default 1, or `"all"`) and `first` to replace only
  the first; `insert` at the end or start of the section under a heading (`create` adds a missing one), or
  right after or before the one line holding an anchor; `append`; set or remove a top-level `frontmatter` field
  (the block must read back with only that field changed); and a one-file unified-diff `patch`.
  - One call edits up to 10 notes (`--ops` maps each path to its list, with no `<path>`): about a third of the
    runs' scripts changed 2 or 3 notes.
  - All operations, on every note, apply or nothing is written (exit 1, naming the op and why): every new text
    goes to a synced temp file beside its note first, and the renames happen only once all are ready and no
    note has changed meanwhile. If a rename still fails part-way, it exits 4 and says which notes were
    written, so a run reads them before running it again. Folders are opened from the vault down without
    following a symlink. A regex op, and a patch's search for where a hunk goes, stops after 2 s. `--check`
    reports without writing.
  - It prints counts and status only: never the note's text, a pattern, a heading or an `--ops` key. The
    operations come only on stdin (`--ops -`), at most 1 MiB, and a key named twice is refused.
  - A scheduled run edits only the vault in `SCOUT_DATA_DIR`, which must hold `scout-config.yaml`; a `--repo`
    naming another folder is refused (exit 3), so the run can't pick the frame the rules are judged in.
  - Only `owner` and `autonomous` edit, only notes whose vault path starts with `knowledge-base/` or
    `action-items/context/`, never a file the write gate protects (a hard-linked note included), and never in
    a way that changes the note's privacy label, except making it private (exit 3). The commit's privacy check judges the edit as it judges
    one made with the Edit tool.
  - `phases/core/privacy.md` and the read gate's deny message point runs at it.
- **Run health counts the KB edits runs still make with interpreter scripts** (`kb.interpreter_edits`, a note):
  how many `kb_read_gate.kb_edit` events each of the last 10 runs left, and, once they stop, how long ago the
  last one was, for 14 days. The read gate doesn't refuse those scripts in this release.
