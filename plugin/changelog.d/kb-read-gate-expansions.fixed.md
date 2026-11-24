- **A scheduled run's shell read of a KB note is judged by the path the shell hands the reading command**
  (`engine/scout/hooks/kb_read_gate.py`, `engine/scout/kb/live_run.py`, `engine/scout/kb/raw_read.py`). The KB
  read gate used to deny only a KB path it could see in the command's text, so `cat k*/x.md`, `cat */a.md`,
  `ls k*`, `K=knowledge; cat ${K}-base/x.md`, `cat $'knowledge\x2dbase/x.md'`, `cat kn{o,}wledge-base/x.md`,
  `printf 'knowledge%sbase/x.md' - | xargs cat` and `cd action-items && cat context/x.md` got through, and so did
  a KB path spelled out after a quoted `\|` or `;` (`grep -n "a\|b" knowledge-base/x.md`,
  `sed -n '/x/{=;p;}' knowledge-base/x.md`). Now each file a reading command (`cat`, `ls`, `grep`, `head`,
  `sed`, …) reads, and each input redirect, has its brace lists expanded, the command's own variables put in
  (inside `$(…)` too), `$'…'` decoded and its globs matched from the folder it runs in, under the existing
  glob budget (over the match cap or the time limit, the call is refused). `xargs` reads what `echo` or
  `printf` prints, or the files under what `git ls-files`, `find`, `grep -l` or `rg -l|--files` lists, so
  `git ls-files scripts | xargs rg x` stays allowed and `find . | xargs cat` doesn't. A match in
  `knowledge-base/` or `action-items/context/` is denied. A read whose path the gate can't work out (a command
  substitution, a backtick, a variable the command doesn't set, `xargs` input from any other command) is
  allowed for now and recorded as a `kb_read_gate.unresolved_read` event naming only its kind
  (`command_output`, `unset_variable`, `xargs_input`) and the run's mode, never the command or the path;
  `DENY_UNRESOLVED_READS` in `kb_read_gate.py` denies them instead, as an unresolved write already is. A
  `$(date +FORMAT)` that prints no `/`, with or without a `TZ=` for the call, counts as any one name, so
  `action-items/action-items-$(date +%F).md` is allowed with nothing recorded and
  `knowledge-base/$(date +%F).md` is denied.
