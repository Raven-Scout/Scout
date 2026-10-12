# Scout Engine

Python package providing the `scoutctl` CLI, hooks, runners, and
library code for the Scout productivity system.

## Install (dev)

From this directory:

```bash
uv venv
uv pip install -e ".[dev,full]"
```

Verify:

```bash
scoutctl --help
scoutctl version
scoutctl manifest show
```

## Tests

```bash
pytest tests/
```

## Non-canonical install locations

The engine works at any path on disk, not just `~/scout-plugin/`. Three
install methods are all supported:

- **Claude Code marketplace** — Claude Code clones the marketplace repo to
  `~/.claude/plugins/marketplaces/<marketplace>/` and runs the plugin from a
  versioned copy under `~/.claude/plugins/cache/<marketplace>/scout/<version>/`.
  The versioned copy comes from the `stable` branch (the latest release), while
  the clone tracks `main`. In a `Raven-Scout/Scout` clone the plugin is the
  `plugin/` subdirectory, so the clone-side venv is
  `marketplaces/<marketplace>/plugin/.venv`; a legacy `scout-plugin` clone has
  it at the clone root. `engine/bin/scoutctl` probes both when it runs from
  `cache/`.
- **Local-plugins / dev tree** — plugin lives in your own directory tree
  (e.g. `~/LOCAL_PLUGINS/scout-plugin/`).
- **Canonical git clone** — a legacy `scout-plugin` clone at `~/scout-plugin/`
  (plugin at its root), or a `Raven-Scout/Scout` clone (plugin at
  `<clone>/plugin/`).
- **App-managed** — Scout.app builds the engine's venv outside any plugin
  tree, at `~/.local/share/scout/venv/<version>`, by passing that path to
  `scripts/install-venv.sh` as `SCOUT_VENV_DIR` (whose default is
  `<plugin_root>/.venv`). There is no `<plugin_root>/.venv` in this layout.

Wherever the venv is, two things find it: the engine pointer
`~/.local/state/scout/engine.json` (its `scoutctl`, `python` and `vault`
fields, rewritten by every `bootstrap install`/`upgrade` that installs jobs),
and the `~/.local/bin/scoutctl` shim, which runs the same `scoutctl`.
`<plugin_root>/.venv` is only the default for dev checkouts and Claude Code
installs.

How each layer adapts:

- **Slash commands (`/scout-setup`, `/scout-update`)**: use
  `$CLAUDE_PLUGIN_ROOT` (set by Claude Code at slash-command invocation)
  to locate the plugin, install the venv inside it via
  `scripts/install-venv.sh`, and verify the venv is editable-installed
  from the same checkout — flagged as `VENV_MISMATCH` otherwise.
- **`scoutctl schedule install-plist` / `install-cron`**: write the
  `scoutctl` beside the interpreter running the command (`bin/scoutctl` of
  whichever venv that is) into the plist or cron block. No override knob —
  enforcing this single source of truth eliminates the failure mode
  where the scheduler runs a different scoutctl than the engine the
  user thinks is loaded. Both point scheduled runs at the vault through
  `SCOUT_DATA_DIR` (`$SCOUT_DATA_DIR` or `~/Scout` when run by hand; the
  bootstrapped vault when bootstrap installs them).
- **`scoutctl bootstrap doctor` / `/scout-status`**: read the installed
  plist's `ProgramArguments[0]` (or the cron block's command) and verify it
  points at an existing, executable scoutctl outside any macOS TCC-protected
  directory (`~/Documents/`, `~/Desktop/`, `~/Downloads/`). Flagged RED with a
  fix-command hint if any check fails. The doctor also warns when the engine
  pointer disagrees with the plist (its `scoutctl` or `SCOUT_DATA_DIR`), with
  what `~/.local/bin/scoutctl` runs, or with the vault being checked.

The legacy `~/scout-plugin/` path is preserved everywhere as a fallback
default — canonical-layout installs see no behavior change.

## See also

- `../.claude-plugin/plugin.json` — Claude Code plugin manifest
- Scout unification design spec lives in the scout-app repo under
  `docs/superpowers/specs/`.
