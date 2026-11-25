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
  no path read everything below the folder; `git grep`, `git archive` and a two-tree `git diff` read all of a
  `<rev>:<folder>` (`git grep x HEAD:`). A pathspec made only of exclusions (`:!docs`, `:(exclude)docs`) reads
  everything else: below the folder for `grep` and `archive`, the whole tree otherwise, as git does. Inside `.git`
  git works from the top (`git -C .git grep x HEAD`). Folders count by file identity, so letter case and symlinks
  don't matter (a folder made earlier in the same command is compared by its spelling), and only the vault's own
  repository holds the KB (a linked worktree of it too, and the vault's git folder with another work tree).
  Names-only calls (`git status`, `git log --oneline`, `git diff --stat`, `git show --stat`) and commits of
  other files are unchanged. Two kinds are recorded in the
  `kb_read_gate.unresolved_read` event rather than denied, each behind its own switch in `kb_read_gate.py` (off):
  a git read from a folder, repository or path the gate can't work out (`git_unresolved`,
  `DENY_UNRESOLVED_GIT_READS`), and a content read of the vault's repository with no path to bound it, such as
  `git diff`, `git show HEAD` or `git log -p` (`git_whole_repo`, with the subcommand in the event's
  `git_whole_repo` list, `DENY_GIT_WHOLE_REPO_READS`); `git show --stat <object id>` (it may be a blob) counts
  as one, and `… | xargs git cat-file -p` (objects from stdin) as unresolved. The shell pre-filter now sends to
  the gate any `--git-dir`, `--work-tree`, `GIT_DIR` or `GIT_WORK_TREE`, every git subcommand that prints file
  contents (`show`, `diff`, `log`, `grep`, `blame`, `archive`, `cat-file`, `stash show`, …, so `git log --oneline`
  and `git diff HEAD~3` reach it too and are allowed there), the `-p`/`-v` hunks of `add`, `checkout`, `reset`,
  `restore`, `commit`, `status` and `stash`, and an exclusion pathspec, also when quotes, backslashes, a tab or a
  line break spell git's words (`git 'log' -p -- .`, `g\it grep x`). Short pathspec magic is read in any order
  (`:/!docs`, `:!:docs`). The commands git runs for you (`rebase -x|--exec`, `bisect run`, `difftool
  -x|--extcmd`, `submodule foreach`, `filter-branch` filters, a `GIT_PAGER` or `PAGER` the command sets) are
  judged as if run directly, and a variable naming git (`G=git; $G archive HEAD`) is worked out. A git read after
  the command links, moves or copies folders (`ln -s`, `mv`, `cp`, `git worktree add`, `git clone`), or from a
  folder not on disk, is recorded as `git_unresolved`, and denied when the command names the vault or a KB folder
  or copied the vault's repository (`git worktree add`, `git clone .`). `git merge-tree` reads all of a
  `<rev>:<folder>`, `git merge-file -p` the files it names, and `git request-pull -p` the whole repository.
