- **A scheduled run's program can't be hidden in a word the shell builds as the command runs**
  (`engine/scout/kb/live_run.py`, `engine/scout/hooks/kb_read_gate.py`). The run gate knew a program by the
  text of its command word, so `c=re; c+=ad; $c HOM? <<< h`, `rea? HOME` (once a file named `read` exists),
  `$(echo daer | rev) HOME`, `${(L)c}` or `f(){ "$@"; }; f $c …` got past every rule: run env, detach, the
  commit hooks, HOME and KB reads. Now a variable set to a plain word once earlier in the same command is
  judged as that program (`S=scripts/scoutctl; $S kb …` stays allowed), as is a value of `$HOME`, `$PWD` or `~`
  and plain text, an array of plain words (`cmd=(git status); "${cmd[@]}"`), and a `for` loop over plain words,
  judged once per word. A function call is judged as its body with the call's arguments put in, with the
  variables the command has set so far; `sh -c "$c"` and `eval "$c"` as the command `$c` holds. Every other
  built word is denied with its own reason: a glob or brace list, a command substitution or backtick, a zsh
  flag or modifier, a variable assigned twice, with `+=`, by `read`/`typeset -l`/`set -A`/`${x:=…}` or to a
  glob, one the command never sets, and `$1` outside a function body the gate can read (a hook such as
  `command_not_found_handler`, an anonymous or quoted-name function, `set -- …; "$@"`). Three shapes runs do
  use (a loop over words that aren't plain, a variable assigned a substitution or other variables, `$@` or `$1`
  as a function body's program) are judged as a program the gate can't name: as git and each program that
  writes files, as one that may read each operand it is given (a folder's files too), and as one that may set
  a variable an operand names (`HOME`, `HOME=x`, or a name built at run time). One that reaches a KB note,
  Scout's secrets, a hook, HOME, XDG_CONFIG_HOME, a GIT_CONFIG_* variable or a run variable is denied.
  Otherwise they are allowed, and recorded as a `kb_read_gate.unresolved_command_word` event with their kinds,
  never the command, until `DENY_UNRESOLVED_COMMAND_WORDS` is turned on. A name `hash`, `alias`,
  `commands[…]`, `functions -c` or a definition without a readable body made a program is judged that way
  too. After `.`, `source` or `autoload` a command word is judged as it reads and recorded (`sourced_function`),
  since the file may have defined it. A function shares the caller's variables unless it makes one `local`, so
  what it sets is "computed" afterwards. Over 281 scheduled runs, one call is newly denied: a program picked by
  `$( … )`.
- **A scheduled run can't unset, unexport or override git's config variables** (`engine/scout/kb/live_run.py`,
  `hooks/prefilter.sh`). `unset GIT_CONFIG_GLOBAL`, `env -u`, `export -n`, `typeset +x`, `read`, `+=`,
  `${GIT_CONFIG_GLOBAL:=…}` and quoted or escaped spellings (`GIT_CONF''IG_GLOBAL=x git commit`) are denied like
  `GIT_CONFIG_GLOBAL=x`. `export GIT_CONFIG_GLOBAL` and reading it stay allowed. The quoted spellings used to
  end in the shell filter without reaching the gate.
