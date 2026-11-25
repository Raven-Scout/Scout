- **The `restrict_tools` gate no longer lifts every restriction when the engine can't start**
  (`hooks/prefilter.sh`, `hooks/hooks.json`). Claude Code lets a PreToolUse call through when its hook
  exits with anything but 0 or 2, so a `scoutctl` that couldn't start (no venv, a shim naming a missing
  interpreter, a Python crash before the gate, an engine half-replaced mid-upgrade: exit 1, 126, 127 or a
  signal) let every MCP call through silently, including the ones `restrict_tools` exists to stop. The gate
  itself always exits 0, so any other exit now means it did not run, and the call is blocked (exit 2,
  saying to run `/scout-update` or `scoutctl bootstrap doctor`) in a session that may be in a vault:
  `SCOUT_DATA_DIR` set, or a `scout-config.yaml` at or above the payload's `cwd`, by the same rule the
  Python entry uses. A `cwd` the shell can't read for certain (none, empty, relative, or holding a JSON
  escape) counts as in a vault. Outside any vault, and for a payload that isn't an MCP call, the hook still
  exits 1 and the call goes through, since `hooks.json` applies to every session on the machine. If
  `prefilter.sh` is missing or broken too, the `hooks.json` entry does the same on `SCOUT_DATA_DIR` and its
  own working directory. Sessions that never start the engine (outside a vault, or in one without
  `restrict_tools`) run exactly as before; where the engine does start, the shell adds one read of the
  payload ahead of it.
