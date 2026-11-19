- **Scout's launchers and hook commands never run a program from the working directory**
  (`engine/bin/scoutctl`, `templates/scripts/scoutctl.sh.tmpl`, `scripts/install-venv.sh`, the
  vault's pre-commit hook in `engine/scout/kb/hook.py`, the repo root's `install.sh`, the inline
  tool hooks in `hooks/hooks.json`, which Claude Code runs on every matching tool call in any repo,
  and the vault's run scripts `run-scout.sh`, `run-dreaming.sh`, `run-research.sh` and
  `scripts/scout-lock.sh`, which a manual run starts with your PATH and the vault as cwd).
  A relative PATH entry (`.`, `./bin`, `node_modules/.bin`, or an empty one, which also means the
  working directory) let a `python3`, `bash`, `scoutctl`, `uv`, `cat`, `grep` or `dirname` planted
  in a cloned repo or the vault run as you. Each now drops relative and empty PATH entries before it
  runs anything, and the processes it starts inherit that PATH; a leading `~` counts as `$HOME`, as
  bash's own PATH search reads it. The hooks climb to the vault with `${d%/*}` in place of
  `dirname`. The launcher's and `install.sh`'s shebang is `#!/bin/bash` (`/usr/bin/env bash`
  searched PATH for bash), and the launcher's last resort, when no venv or engine pointer works, runs
  `python3` only from an absolute PATH entry, then `/usr/bin`, `/opt/homebrew/bin` or
  `/usr/local/bin`, and otherwise exits 127 with a message. The pre-commit hook's baked-in scoutctl
  path skips relative PATH entries too, and `/scout-setup` and `/scout-update` run
  `install-venv.sh` with `/bin/bash`. Vaults pick up the new run scripts, `scripts/scoutctl` and
  pre-commit hook on the next `/scout-update`.
