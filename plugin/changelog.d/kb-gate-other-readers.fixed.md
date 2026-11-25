- **A scheduled run's copy, archive, `diff -r` or program that reads a KB note is denied like any other shell
  read** (`engine/scout/kb/other_reads.py`, `engine/scout/kb/live_run.py`, `engine/scout/kb/raw_read.py`,
  `engine/scout/hooks/kb_read_gate.py`, `hooks/prefilter.sh`). The KB read gate judged a reader's operands
  only, so a note got through by way of a command whose files are something else: a copy or an archive
  (`cp -r con* ../x; cat ../x/x.md` and `tar cf - con* | tar xOf -` from `action-items/`, `tar cf - . | tar
  xOf -` from the vault, and `zip`, `rsync`, `pax`, `ditto`, `split`, `gzip -c` alike), `diff -rN . e`,
  `dd if=`, `source` / `.`, and a file named in a program's text: sed's `r FILE`, awk's `getline < "FILE"`
  (or `< f`, with `f` set in the program or by `-v`), perl's `open(…)` and the files `-n`, `-p` or `<>` read,
  and the commands such a program runs (awk's `system()`, `"cmd" | getline` and what it prints into `sh`;
  perl's `system`/`exec` in either form, backticks and `qx{}`; tar's `-I` and `--to-command`). A program's file
  or command is worked out from its string literals (Perl's `q{}` too), the variables it sets to them, joined
  strings, `@ARGV = (…)` and awk's `ENVIRON["F"]` (from a `F=…` the command sets, or the run's own). Each is
  now judged by the path the shell hands over, a recursive one by whether its root holds the KB; a copy
  *into* the KB stays allowed (it reads nothing out of it), unless tar pipes the files through a program. A
  program's path is read as written (no glob or `~`, and no `$` but one the shell fills in first, as in
  `sed "r $D/x.md"`); one it builds at run time, and the script a `sed -f` or `awk -f` file holds, is allowed
  and recorded as a `kb_read_gate.unresolved_read` of the new kind `program_path` (`DENY_UNRESOLVED_READS`
  denies it instead). `printf`/`echo` words read by a `while read` loop are judged as the paths they name. `join`, `expand`, `look`, `shuf` and
  the other text utilities the reader list lacked are readers too. `cat "$(cat <<'EOF'` … `)"` is judged by
  the path its here-document holds (it was refused as naming too many files), and an unquoted
  here-document's `$(…)` sees the variables set before its command (`X=kn; cat <<EOF` with
  `$(cat ${X}owledge-base/a.md)` was only recorded as unresolved). A lister's output read through a
  substitution or a loop (`cat $(find .)`, `for f in $(find .)`, `find . | while read f; do cat "$f"`, and
  the same with `rg --files` or `git ls-files`) is judged by the folders it lists, as an `xargs` lister is:
  denied from `action-items/`, and allowed from a folder holding no note, with nothing recorded as
  unresolved any more. The shell pre-filter sends `diff -r`,
  `pax`, `zip`, `cpio`, `ditto`, `scp`, `gzip`, `zstd` and `lz4` calls on to the gate.
