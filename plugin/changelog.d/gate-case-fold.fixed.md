- **A scheduled run can no longer get past the run gates by changing a program name's letter case**
  (`engine/scout/kb/raw_read.py`, `engine/scout/kb/live_run.py`, `hooks/prefilter.sh`). On macOS's default
  case-insensitive disk, `FIND`, `RG`, `NOHUP`, `SETSID`, `XARGS`, `GIT` and `PYTHON3` run the real programs, but
  the gates matched program names as spelled, so `NOHUP sleep 100`, `FIND . -name a.md` and `RG foo .` from the
  vault root, `echo knowledge-base/a.md | XARGS cat` and `GIT -c core.hooksPath=… commit` were allowed.
  - The gates now compare every program name in lower case, after the shell's quoting is removed (`'NOHUP'`,
    `\NOHUP`, `NO''HUP`) and its folder dropped (`/usr/bin/NOHUP`), and through prefixes such as `/usr/bin/nice`,
    `COMMAND` and `TIME`. On macOS `/usr/bin/command` and `/usr/bin/time` run the word after them, as the builtins do.
  - A shell builtin or keyword keeps its spelling (`cd`, `exec`, `eval`, `for`), since the shell only knows
    those in lower case: `CD knowledge-base` runs `/usr/bin/cd` in a child process and changes no folder. Paths
    and arguments are never folded, so `echo FIND` and `git commit -m 'NOHUP note'` are still allowed.
  - Git's subcommand and `scoutctl` are matched in any letter case too. Git itself refuses `git COMMIT`, so this
    can only over-deny.
  - The shell pre-filter checks the same program words in any letter case, on the command as written and with
    its quotes and backslashes removed, so these calls now reach the Python gate.
