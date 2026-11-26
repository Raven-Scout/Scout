- **A run's Bash call that only names a KB path after the shell expands it now reaches the KB read gate**
  (`hooks/prefilter.sh`). The shell pre-filter let `cat k*/projects/a/a.md`, `K=knowledge; cat ${K}-base/x.md`,
  `cat "$(printf …)"` and `less $HOME/…/kn*` through without starting the Python gate, because their text
  spells no KB path. In a scheduled run, a command now goes to Python when it has a glob character, a `$`
  (other than `$?`, `$#`, `$!`, `$$`), a backtick, a brace list (`kn{o,}wledge-base`), a leading `~`, an
  absolute path, a `(` (zsh reads `kn(o|)wledge-base` as alternation), `xargs` or `parallel`, or a `cd`
  into `action-items/` (in any letter case, quoted or escaped: `cd 'action'-items`, `XARGS`); when its cwd is in or under `action-items/`; or when it names a KB folder in another
  letter case (`KNOWLEDGE-BASE`, the same folder on macOS's default disk). Owner sessions are unaffected: they
  leave before the filter runs. A launcher called by its full path (`/usr/bin/nohup`, `/usr/bin/env -i`) and
  `git -C $R $SUB` now reach Python too, which denies them. The Python gate does not yet deny the glob and
  expansion forms themselves: it judges a read by the path its words spell, so this change is the first of two.
- **A run's Grep, Glob or Bash call over 32 KB is blocked in the shell** (`hooks/prefilter.sh`). The
  pre-filter's patterns backtrack: a harmless 200 KB command kept it busy about 40 s, past the hook's 15 s
  timeout, and Claude Code allows a call whose hook it kills. The filter now reads at most 32 KB + 1 byte and
  blocks a longer call, counting bytes in any locale.
