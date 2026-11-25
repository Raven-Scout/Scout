- **The KB read gate judges a run's `grep` and `find` as the programs the run's shell starts** (`engine/scout/kb/raw_read.py`, `engine/scout/kb/live_run.py`, `hooks/prefilter.sh`). Claude Code's shell snapshot, which every Bash call of a run sources, runs `grep` as the Claude binary's embedded ugrep and `find` as its embedded bfs. The gate judged them as BSD grep and find, so these got through:
  - ugrep's own options: `-g`, `-O` and `-t` filters, `!` and `^` exclusions, `-t` prefixes.
  - Recursion without `-r`: `-3`, `-3-5`, `--depth`, `--index`, `--include-dir`, or a `-g`/`--include` glob holding a `/`.
  - A folder operand, which ugrep reads one level deep (`grep TODO context` from `action-items/`, `grep -n TODO *`).
  - `--from` lists.
  - bfs paths placed after the expression (`find docs -name '*.md' .`).

  A bare `grep`, `egrep`, `fgrep` or `find` is now judged both ways, as itself and as the snapshot's program, and denied if either reads the KB. Called by its path (`/usr/bin/grep`), it is judged as the real program. Every run `grep` now goes to Python, not only `grep -r`.
- **`-g '!dir'` no longer counts as excluding a folder for ugrep.** ugrep skips files of that name and still searches the folder. Only `!dir/`, `^dir/` and `--exclude-dir` keep a folder out.
- **A `$(…)` or backtick substitution is judged from the folder the command has `cd`'d into by then.** `cd docs; echo $(find .)` is no longer denied as a vault-root listing. Two reads are now denied:
  - A substitution a reader takes as its files is a read of what it lists: `cd action-items && cat $(find .)`.
  - A `cd` inside `( … )` no longer carries past the `)`: `(cd docs); grep -r TODO .` from the vault root.
- **`find -L docs` and `find -s docs` are no longer denied as vault-root searches.** The gate now reads the paths after find's leading flags.
- **`scoutctl bootstrap doctor` reports what Claude Code's newest shell snapshot runs for `grep` and `find`** (`engine/scout/kb/shell_shadows.py`). It warns when the snapshot shadows a name, or uses a program or flags, the gate doesn't model.
