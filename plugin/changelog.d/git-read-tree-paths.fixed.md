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
  Names-only calls (`git status`, `git log --oneline`, `git diff --stat`, `git show --stat`) stay allowed.
  Two kinds are recorded in the
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
  `<rev>:<folder>`, `git merge-file -p` the files it names, and `git request-pull -p` the whole repository. Git's
  subcommand is compared in any letter case, as a case-insensitive disk runs it (`git ARCHIVE`, with `GIT`/`Git`
  from the program-name folding). Options that hold a command are read as git reads them, by any prefix of their
  name and in a cluster of short flags (`--exe`, `-qx`), and the transports' `--upload-pack`/`-u` and
  `--receive-pack`/`--exec` count too, as do `GIT_SSH_COMMAND`, `GIT_PROXY_COMMAND` and `GIT_EXTERNAL_DIFF`. Each
  such command goes through the same checks as a shell command run directly, from git's folder. `clone`/`init
  --template`, an `ext::` URL, `GIT_SSH`, `GIT_ASKPASS`, `SSH_ASKPASS`, `GIT_ALLOW_PROTOCOL` and the
  `core.gitProxy` and `protocol.*` settings are denied in a run. A link's target is read from the link's own
  folder (`cd ~ && ln -s Vault V2`, `ln -s . self`), and a bundle or a push of the vault's repository into a
  folder counts as a copy of it. A cluster of short flags stops at one that takes a value (`-Xx -x 'cmd'`). A
  config key that makes git run a command is refused in a run by its name's ending, in any section
  (`remote.x.uploadpack`, `remote.x.receivepack`, `*.cmd`, `*command`, `*.program`, `*.path`, `*.browser`, …),
  plus `gc.recentObjectsHook`, `core.alternateRefsCommand` and `interactive.diffFilter`; a test checks the
  list against `git help --config`. A link to a glob, or to a target the gate can't work out, counts as one
  that may be the vault. In a run, `git config` writes, `-c` and `--config-env` may set only a reviewed list of
  plain keys (`user.*`, `color.*`, `advice.*`, `pull.rebase`, `push.default`, `branch.*.remote`, `remote.*.url`,
  …; `live_run.CONFIG_ALLOWED`), and only in the repository's own config: `--global`, `--system`,
  `--worktree` and `--file` writes are refused for every key, so a run never changes the user's `~/.gitconfig`.
  Any other key, and a section renamed or removed unless it is a plain one, is refused, and a test checks every
  key `git help --config` lists against the list. An option's value may be
  `--`, as git allows (`git rebase -s -- -x 'cmd'`). A git subcommand that isn't git's own (an alias, or a typo
  `help.autocorrect` may run) is refused in a run, as are `send-email`, `instaweb`, `imap-send` and `daemon`;
  `git grep -O` counts as a command git runs; and a repository whose `core.worktree` is the vault, or a folder in
  or above it, holds the KB.
- **In a scheduled run, git is an allowlist** (new `engine/scout/kb/git_allow.py`). Fifty runs used only `log`,
  `config` reads, `show`, `status`, `diff`, `add` and `remote`, with display options, so a run may use those and a
  few more read forms (`rev-parse`, `ls-files`, `branch`/`tag` listings, `describe`, `merge-base`, `rev-list`,
  `shortlog`), each with a listed set of options spelled out in full (no prefixes) and at most `-C <dir>` before
  the subcommand. Any other subcommand, option or global option (`-c`, `--exec-path`, `--no-pager`, `-p`), and
  any git call in a command that sets a `GIT_*` variable, `PATH` or zsh's `path`, an editor or pager, or zsh's
  command table (`commands`, `functions`, `alias`, `hash`), is refused with a message naming the forms a run may
  use. Commits go through `scripts/git-safe-commit.sh`, which a run's `git commit` now gets pointed to. `git config`
  may only read (`<key>`, `--get`, `--get-regexp`, `--list`, `get`, `list`) or set `user.email` and `user.name`
  in the repository's config (Step 0 of every run outside the write sandbox). A word the shell fills in before
  `--` must be one the command sets (`X=-p; git log $X` is read as `-p`), and a glob, brace list, zsh array or
  `xargs` input there is refused, since it may make an option. The content-read judging stays for the allowed
  forms. A repository other than the vault's whose own config runs a command (fsmonitor, a filter, textconv, an
  external diff, a credential helper for `remote`; Git LFS's filters and fsmonitor's `true` excepted), or holds
  an `includeIf`, is refused: a run can build one by hand with a `.git` file. Such a repository's
  `<rev>:<path>` and pathspecs that name a KB path are refused too, since it may hold the vault's history. The
  repository search now reads config files as git parses them (`git config --file … --list`), so comments,
  quotes and continued lines in `core.worktree` count, and it finds bare repositories and git folders. The shell
  pre-filter sends every git call to the gate.
