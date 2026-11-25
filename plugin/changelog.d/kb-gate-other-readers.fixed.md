- **A scheduled run's copy, archive, `diff -r` or program that reads a KB note is denied like any other shell
  read** (`engine/scout/kb/other_reads.py`, `engine/scout/kb/live_run.py`, `engine/scout/kb/raw_read.py`,
  `engine/scout/hooks/kb_read_gate.py`, `hooks/prefilter.sh`). The KB read gate judged a reader's operands
  only, so a note got through by way of a command whose files are something else: a copy or an archive
  (`cp -r con* ../x; cat ../x/x.md` and `tar cf - con* | tar xOf -` from `action-items/`, `tar cf - . | tar
  xOf -` from the vault, and `zip`, `rsync`, `pax`, `ditto`, `split`, `gzip -c` alike), `diff -rN . e`,
  `dd if=`, `source` / `.`, and a file named in a program's text: sed's `r FILE`, awk's `getline < "FILE"`
  (or `< f`, with `f` set in the program or by `-v`), perl's `open(…)` and the files `-n`, `-p` or `<>` read,
  and the commands such a program runs (awk's `system()` and `"cmd" | getline`, perl's backticks). Each is
  now judged by the path the shell hands over, a recursive one by whether its root holds the KB; a copy
  *into* the KB stays allowed (it reads nothing out of it). A program's path is read as written (no glob,
  `~` or `$`); one it builds at run time is allowed and recorded as a `kb_read_gate.unresolved_read` of the
  new kind `program_path` (`DENY_UNRESOLVED_READS` denies it instead). `join`, `expand`, `look`, `shuf` and
  the other text utilities the reader list lacked are readers too. `cat "$(cat <<'EOF'` … `)"` is judged by
  the path its here-document holds (it was refused as naming too many files), and an unquoted
  here-document's `$(…)` sees the variables set before its command (`X=kn; cat <<EOF` with
  `$(cat ${X}owledge-base/a.md)` was only recorded as unresolved). The shell pre-filter sends `diff -r`,
  `pax`, `zip`, `cpio`, `ditto`, `scp`, `gzip`, `zstd` and `lz4` calls on to the gate.
