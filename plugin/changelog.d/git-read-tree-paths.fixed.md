- **A scheduled run's git read of a KB note is judged by the repository paths git reads** (new
  `engine/scout/kb/git_read.py`; `engine/scout/hooks/kb_read_gate.py`, `engine/scout/kb/live_run.py`,
  `hooks/prefilter.sh`). The KB read gate matched git reads by their text, so `git -C action-items show
  HEAD:./context/x.md`, `git -C action-items diff HEAD -- context/x.md`, `git -C action-items log -p context/`
  and `git -C action-items grep x context/` got through. Now a git call that prints file contents (`show`,
  `cat-file -p`, `diff`, `log -p|--patch|--follow|-L`, `grep`, `blame`, `archive`, `format-patch`, `difftool`,
  `add -p` …) is judged from the folder git runs in: the shell's, then each `-C` in turn, with
  `--git-dir`/`--work-tree` and `GIT_DIR`/`GIT_WORK_TREE` choosing the repository and its top as git does. A
  `<rev>:<path>` is a path in the commit's tree (`./x` and `../x` from that folder, `x` and `:/x` from the top), a
  pathspec covers everything below it (a wildcard crosses folders, as in git), and `git grep`/`git archive` with
  no path read everything below the folder. Folders count by file identity, so letter case and symlinks don't
  matter, and only the vault's own repository holds the KB (a linked worktree of it too, and the vault's git
  folder with another work tree). Names-only calls (`git status`, `git log --oneline`, `git diff --stat`,
  `git show --stat`) and commits of other files are unchanged. Two kinds are recorded in the
  `kb_read_gate.unresolved_read` event rather than denied, each behind its own switch in `kb_read_gate.py` (off):
  a git read from a folder, repository or path the gate can't work out (`git_unresolved`,
  `DENY_UNRESOLVED_GIT_READS`), and a content read of the vault's repository with no path to bound it, such as
  `git diff`, `git show HEAD` or `git log -p` (`git_whole_repo`, with the subcommand in the event's
  `git_whole_repo` list, `DENY_GIT_WHOLE_REPO_READS`). The shell pre-filter now sends any `--git-dir`,
  `--work-tree`, `GIT_DIR` or `GIT_WORK_TREE`, a git call naming `action-items`, a `.`/`..`/`:/x`/`:(top)x`
  pathspec and `git archive` to the gate.
