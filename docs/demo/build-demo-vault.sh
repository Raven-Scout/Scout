#!/usr/bin/env bash
# Build a demo vault full of invented data, for screenshots, recordings and trying the app.
#
#   docs/demo/build-demo-vault.sh [DEST]      (default: a new temporary directory)
#
# The vault is the eval harness's fixture (plugin/engine/tests/eval: "Alex Example",
# example-org, made-up tickets) with the busy-weekday scenario, plus the overlay in
# docs/demo/vault/ that fills in a "today" page. Every proper noun in that overlay
# must appear in the eval allowlist; plugin/engine/tests/unit/test_demo_vault_scrub.py
# enforces this, so a real name can't slip into a public screenshot.
#
# The build schedules nothing and touches nothing outside DEST. To look at it in the
# app, use a Debug build (bundle id com.scout.Scout.dev, separate from the installed
# Scout.app's settings):
#
#   defaults write com.scout.Scout.dev scoutDataDir "$DEST"
#   # ...and afterwards:
#   defaults delete com.scout.Scout.dev scoutDataDir
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${SCOUT_PYTHON:-$REPO/plugin/.venv/bin/python}"
[[ -x "$PY" ]] || { echo "needs the plugin virtualenv at $PY (see plugin/README.md)" >&2; exit 1; }

DEST="${1:-$(mktemp -d -t scout-demo)}"
DEST="$(mkdir -p "$DEST" && cd "$DEST" && pwd)"

cd "$REPO/plugin/engine"
"$PY" - "$DEST" "$REPO/docs/demo/vault" <<'PY'
import subprocess, sys
from datetime import date
from pathlib import Path

from tests.eval.harness.build import _git, _git_env, _overlay, build_vault

dest, overlay = Path(sys.argv[1]), Path(sys.argv[2])
today = date.today()
scenario = Path("tests/eval/scenarios/busy-weekday")
build_vault(dest, today=today, scenario_dir=scenario)
_overlay(overlay, dest, today)
env = _git_env(today)
_git(dest, env, "add", "-A")
_git(dest, env, "commit", "-q", "--amend", "-m", "demo vault (invented data)")
PY

echo "$DEST"
