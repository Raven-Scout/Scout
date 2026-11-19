#!/usr/bin/env bash
# Build a demo vault full of invented data, for screenshots, recordings and looking around the app.
#
#   docs/demo/build-demo-vault.sh [DEST]      (default: a new temporary directory)
#
# The vault is the eval harness's fixture (plugin/engine/tests/eval: "Alex Example",
# example-org, made-up tickets) with the busy-weekday scenario, plus the overlay in
# docs/demo/vault/: a "today" page in the shape a real run writes, the item notes behind
# it, and a source conflict recorded the way Scout records one (a [contradicted] KB note
# and a review-queue entry). Every proper noun in that overlay must appear in the eval
# allowlist; plugin/engine/tests/unit/test_demo_vault_scrub.py enforces it, so a real
# name can't reach a public screenshot.
#
# The build schedules nothing and touches nothing outside DEST. Absolute paths that the
# install writes into the runners and brain files are rewritten to /Users/alex, so they
# can't leak the builder's home directory into a screenshot. That also means the demo
# vault is for looking at, not for running.
#
# To open it in the app, use a Debug build (bundle id com.scout.Scout.dev, separate from
# the installed Scout.app's settings):
#
#   defaults write com.scout.Scout.dev scoutDataDir "$DEST"
#   # ...and afterwards:
#   defaults delete com.scout.Scout.dev scoutDataDir
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${SCOUT_PYTHON:-$REPO/plugin/.venv/bin/python}"
[[ -x "$PY" ]] || { echo "needs the plugin virtualenv at $PY (see plugin/README.md), or set SCOUT_PYTHON" >&2; exit 1; }

if [[ $# -gt 0 ]]; then
  DEST="$1"
  if [[ -e "$DEST" && -n "$(ls -A "$DEST" 2>/dev/null)" ]]; then
    echo "$DEST is not empty. Pick a new directory, or remove it first." >&2
    exit 1
  fi
  mkdir -p "$DEST"
else
  DEST="$(mktemp -d "${TMPDIR:-/tmp}/scout-demo.XXXXXX")"
fi
DEST="$(cd "$DEST" && pwd -P)"

cd "$REPO/plugin/engine"
"$PY" - "$DEST" "$REPO/docs/demo/vault" "$HOME" <<'PY'
import sys
from datetime import date
from pathlib import Path

from tests.eval.harness.build import _git, _git_env, _overlay, build_vault

dest, overlay, home = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
today = date.today()
build_vault(dest, today=today, scenario_dir=Path("tests/eval/scenarios/busy-weekday"))
_overlay(overlay, dest, today)

# Neutral paths: the vault root first (it may sit under $HOME), then $HOME itself.
swaps = [(str(dest), "/Users/alex/Scout"), (home.rstrip("/"), "/Users/alex")]
for f in dest.rglob("*"):
    if not f.is_file() or ".git" in f.relative_to(dest).parts:
        continue
    try:
        text = f.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    new = text
    for old, repl in swaps:
        new = new.replace(old, repl)
    if new != text:
        f.write_text(new, encoding="utf-8")

env = _git_env(today)
_git(dest, env, "add", "-A")
_git(dest, env, "commit", "-q", "--amend", "-m", "demo vault (invented data)")
PY

if grep -rIl --exclude-dir=.git -e "$HOME" "$DEST" >/dev/null 2>&1; then
  echo "warning: $HOME still appears in the demo vault; keep those files out of screenshots" >&2
fi
echo "$DEST"
