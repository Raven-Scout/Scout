- **Scout's launchers never run a program from the working directory** (`engine/bin/scoutctl`,
  `templates/scripts/scoutctl.sh.tmpl`, `scripts/install-venv.sh`, the vault's pre-commit hook in
  `engine/scout/kb/hook.py`, and the repo root's `install.sh`). Skills and hooks start Scout from any
  folder, and a relative PATH entry (`.`, `./bin`, `node_modules/.bin`, or an empty one, which also
  means the working directory) let a `python3`, `bash`, `scoutctl`, `uv` or `dirname` planted in a
  cloned repo or the vault run as you. Each script now drops relative and empty PATH entries before
  it runs anything, and the processes it starts inherit that PATH. The launcher's shebang is
  `#!/bin/bash` (`/usr/bin/env bash` searched PATH for bash), and its last resort, when no venv or
  engine pointer works, runs `python3` only from an absolute PATH entry, then `/usr/bin`,
  `/opt/homebrew/bin` or `/usr/local/bin`, and otherwise exits 127 with a message. The pre-commit
  hook's baked-in scoutctl path skips relative PATH entries too. Vaults pick up the new
  `scripts/scoutctl` and pre-commit hook on the next `/scout-update`.
