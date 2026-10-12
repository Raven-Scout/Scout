#!/usr/bin/env bash
# Scout agent-behaviour eval harness (docs/superpowers/specs/2026-10-12-eval-harness-design.md).
# A maintainer tool: it runs from a repo checkout with the engine venv at
# plugin/engine/.venv (uv venv && uv pip install -e ".[dev]").
#
#   plugin/scripts/eval.sh build <dest> [--scenario NAME] [--today YYYY-MM-DD]
set -euo pipefail

ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../engine" && pwd)"
if [ ! -x "$ENGINE/.venv/bin/python" ]; then
    echo "eval.sh: no engine venv at $ENGINE/.venv — run: cd $ENGINE && uv venv && uv pip install -e '.[dev]'" >&2
    exit 2
fi
cd "$ENGINE"
exec "$ENGINE/.venv/bin/python" -m tests.eval.harness "$@"
