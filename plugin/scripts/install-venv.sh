#!/bin/bash
# Build the scout-engine venv.
#
# Location:  $SCOUT_VENV_DIR (default: <plugin-root>/.venv). Scout.app passes
#            ~/.local/share/scout/venv/<version> so the venv lives OUTSIDE the
#            plugin tree Claude Code copies into its cache (spec §4.1).
# Builder:   uv when available ($SCOUT_UV, `uv` on PATH, or ~/.local/bin/uv).
#            uv downloads a managed CPython, so this works on Macs whose only
#            python3 is Apple's 3.9. Falls back to `python3.1x -m venv` + pip
#            (SCOUT_INSTALL_NO_UV=1 forces the fallback).
# Extras:    $SCOUT_VENV_EXTRAS (default: dev). Scout.app passes `full`.
# Python:    $SCOUT_PYTHON_VERSION (default: 3.12) — uv path only.
#
# Usage: [SCOUT_VENV_DIR=…] bash <plugin-root>/scripts/install-venv.sh
# The plugin root is derived from this script's own location, so it works
# whether the script lives under ~/.claude/plugins/…, ~/.local/share/scout/
# engine/<v>/, or a hand-cloned ~/scout-plugin.

set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${SCOUT_VENV_DIR:-$PLUGIN_ROOT/.venv}"
EXTRAS="${SCOUT_VENV_EXTRAS:-dev}"
PY_VERSION="${SCOUT_PYTHON_VERSION:-3.12}"

if [ ! -d "$PLUGIN_ROOT/engine" ]; then
    echo "error: engine directory not found at $PLUGIN_ROOT/engine" >&2
    exit 1
fi

# The uv and Claude Code installers put binaries here; a fresh shell may lack it.
export PATH="${HOME:-/nonexistent}/.local/bin:$PATH"

# Builder: uv when available (install.sh guarantees it). SCOUT_INSTALL_NO_UV=1
# forces the python3.1x + pip fallback below.
UV=""
if [ -z "${SCOUT_INSTALL_NO_UV:-}" ]; then
    UV="${SCOUT_UV:-}"
    if [ -z "$UV" ] && command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"; fi
    if [ -z "$UV" ] && [ -x "${HOME:-/nonexistent}/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"; fi
fi

# Choose the builder BEFORE touching an existing venv: a machine with neither
# uv nor a Python >= 3.11 must keep the venv it has, not lose it to the error.
PYTHON=""
if [ -z "$UV" ]; then
    # No uv: pick a Python >= 3.11 (engine[requires-python]). Apple's bundled
    # /usr/bin/python3 is 3.9 on every macOS we support, so try explicit minors.
    for candidate in python3.13 python3.12 python3.11; do
        if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
    done
    if [ -z "$PYTHON" ] && command -v python3 >/dev/null 2>&1; then
        if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
            PYTHON="python3"
        fi
    fi
    if [ -z "$PYTHON" ]; then
        cat >&2 <<EOF
error: neither uv nor a Python >= 3.11 was found on PATH.
scout-engine requires Python 3.11 or newer. Easiest fix — install uv, which
provides Python automatically, then re-run:
  curl -LsSf https://astral.sh/uv/install.sh | sh
Or install a Python yourself:
  macOS:   brew install python@3.13
  Debian:  sudo apt install python3.13 python3.13-venv
then re-run: bash $PLUGIN_ROOT/scripts/install-venv.sh
EOF
        exit 1
    fi
fi

if [ -d "$VENV" ]; then
    echo "venv already exists at $VENV — recreating..."
    rm -rf "$VENV"
fi
mkdir -p "$(dirname "$VENV")"

if [ -n "$UV" ]; then
    echo "using uv ($UV), python $PY_VERSION"
    "$UV" venv --python "$PY_VERSION" "$VENV"
    echo "installing scout-engine[$EXTRAS] in editable mode..."
    "$UV" pip install --python "$VENV/bin/python" --quiet -e "$PLUGIN_ROOT/engine[$EXTRAS]"
else
    echo "using $PYTHON ($("$PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'))"
    "$PYTHON" -m venv "$VENV"
    echo "installing scout-engine[$EXTRAS] in editable mode (this may take 30-60s)..."
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -e "$PLUGIN_ROOT/engine[$EXTRAS]"
fi

if [ ! -x "$VENV/bin/scoutctl" ]; then
    echo "error: scoutctl not found at $VENV/bin/scoutctl after install" >&2
    exit 1
fi

echo "ok: venv ready at $VENV ($("$VENV/bin/python" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'))"
echo "verify: $VENV/bin/scoutctl version"
