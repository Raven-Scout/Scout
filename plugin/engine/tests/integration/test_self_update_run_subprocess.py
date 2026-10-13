"""``self_update_run`` against real subprocesses: a fake ``claude`` and a fake new engine.

The fake ``claude`` is a bash script that "installs" a plugin root under
``tmp_path``; its ``scripts/install-venv.sh`` writes a ``.venv/bin/scoutctl``
stub. The real ``_subprocess_runner`` runs them, so argv, env and output
parsing are exercised end to end. Nothing outside ``tmp_path`` is touched: no
real claude, no launchd, no network.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from scout import update_outcomes as uo
from scout.scripts import self_update_run as sur
from tests.unit.self_update_fakes import make_deps, pointer

FAKE_CLAUDE = r"""#!/bin/bash
set -euo pipefail
echo "$*" >> "$FAKE_LOG"
ROOT="$FAKE_CACHE/scout/0.17.0"
case "$*" in
  "plugin marketplace update scout-plugin") echo "marketplace updated" ;;
  "plugin update scout@scout-plugin --scope user")
    mkdir -p "$ROOT/.claude-plugin" "$ROOT/scripts"
    echo '{"version": "0.17.0"}' > "$ROOT/.claude-plugin/plugin.json"
    cat > "$ROOT/scripts/install-venv.sh" <<'VENV'
#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -z "${SCOUT_VENV_DIR:-}" ] || { echo "SCOUT_VENV_DIR leaked" >&2; exit 9; }
mkdir -p "$ROOT/.venv/bin"
cat > "$ROOT/.venv/bin/scoutctl" <<'STUB'
#!/bin/bash
echo "scoutctl $* SCOUT_DATA_DIR=${SCOUT_DATA_DIR:-}" >> "$FAKE_LOG"
case "$1" in
  version) echo "0.17.0" ;;
  bootstrap)
    if [ "$2" = auto ]; then
      echo "progress line"
      cat "$FAKE_AUTO_JSON"
    else
      echo '{"severity": "green", "errors": [], "warnings": []}'
    fi ;;
esac
STUB
chmod +x "$ROOT/.venv/bin/scoutctl"
echo "ok: venv ready"
VENV
    ;;
  "plugin list --json")
    ENTRY='{"id": "scout@scout-plugin", "installPath": "%s"}'
    printf "{\"plugins\": {\"scout@scout-plugin\": [$ENTRY]}}\n" "$ROOT" ;;
  *) echo "unexpected: $*" >&2; exit 64 ;;
esac
"""


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    auto_json = tmp_path / "auto.json"
    auto_json.write_text(json.dumps({"action": "upgrade", "conflicts": [], "mutated": False}) + "\n")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FAKE_AUTO_JSON", str(auto_json))
    monkeypatch.setenv("SCOUT_VENV_DIR", str(tmp_path / "live-venv-must-not-be-touched"))
    return claude, log, auto_json


def test_applies_through_real_subprocesses(tmp_path, fake_env):
    claude, log, _ = fake_env
    deps, _, _ = make_deps(tmp_path, run=sur._subprocess_runner, claude_bin=lambda: str(claude))
    pointer_before = pointer()
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "applied", row.detail
    lines = log.read_text().splitlines()
    assert lines[:3] == [
        "plugin marketplace update scout-plugin",
        "plugin update scout@scout-plugin --scope user",
        "plugin list --json",
    ]
    assert lines[3] == "scoutctl version SCOUT_DATA_DIR="
    assert lines[4].startswith("scoutctl bootstrap auto --no-interactive --yes --json --managed-by install.sh")
    assert lines[4].endswith(f"SCOUT_DATA_DIR={deps.vault}")
    assert lines[5] == f"scoutctl bootstrap doctor --json SCOUT_DATA_DIR={deps.vault}"
    assert not (tmp_path / "live-venv-must-not-be-touched").exists()
    assert deps.read_pointer() == pointer_before
    assert [r.result for r in uo.read(deps.vault)] == ["applied"]


def test_lock_refusal_defers_through_real_subprocesses(tmp_path, fake_env):
    claude, _, auto_json = fake_env
    auto_json.write_text(
        json.dumps({"action": "refused", "error": "lock /x held by live PID 77", "mutated": False}) + "\n"
    )
    deps, _, _ = make_deps(tmp_path, run=sur._subprocess_runner, claude_bin=lambda: str(claude))
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "deferred" and row.step == "vault"


def test_missing_claude_binary_fails_at_fetch(tmp_path, fake_env):
    deps, _, _ = make_deps(tmp_path, run=sur._subprocess_runner, claude_bin=lambda: str(tmp_path / "nope"))
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "failed" and row.step == "fetch" and "could not run nope" in row.detail
    assert not Path(tmp_path / "cache").exists()
