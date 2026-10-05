"""/scout-update's plugin-root resolver handles both checkout layouts at ~/scout-plugin.

A legacy single-repo scout-plugin clone keeps the plugin at its root; a
Raven-Scout/Scout monorepo clone keeps it under plugin/. A bare .git is not
enough to short-circuit — otherwise a monorepo clone would resolve to its
root, where there is no .venv and no scripts/install-venv.sh. These tests run
the Step 0 pre-flight block (which embeds the canonical resolver) against a
temporary HOME, with a stub `claude` first on a minimal PATH.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUNBOOK = REPO / "commands" / "scout-update.md"


def _step0_block() -> str:
    text = RUNBOOK.read_text(encoding="utf-8")
    step0 = text[text.index("## Step 0: Pre-flight") :]
    match = re.search(r"```bash\n(.*?)```", step0, re.S)
    assert match is not None
    return match.group(1)


def _finished_vault(home: Path) -> None:
    (home / "Scout" / ".scout-state").mkdir(parents=True)
    (home / "Scout" / "scout-config.yaml").write_text("instance: {name: Scout}\n")


def _run(home: Path) -> str:
    # A stub `claude` that reports no installed plugins, so a resolver that falls
    # through to `claude plugin list` gets valid empty JSON (the block runs under
    # `set -e`) and never reaches a real Claude Code install.
    stub = home / "stub-bin"
    stub.mkdir(exist_ok=True)
    (stub / "claude").write_text("#!/bin/sh\necho '{\"plugins\": {}}'\n")
    (stub / "claude").chmod(0o755)
    proc = subprocess.run(
        ["bash", "-c", _step0_block()],
        env={"HOME": str(home), "PATH": f"{stub}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""


def _checkout(home: Path, plugin_json: str) -> Path:
    clone = home / "scout-plugin"
    (clone / ".git").mkdir(parents=True)
    (clone / plugin_json).parent.mkdir(parents=True, exist_ok=True)
    (clone / plugin_json).write_text("{}\n")
    return clone


def test_a_legacy_clone_is_the_plugin_root(tmp_path):
    _finished_vault(tmp_path)
    clone = _checkout(tmp_path, ".claude-plugin/plugin.json")
    # No venv in the fake checkout, so pre-flight names the root it resolved.
    assert _run(tmp_path) == f"VENV_MISSING:{clone}"


def test_a_monorepo_clone_resolves_to_its_plugin_dir(tmp_path):
    _finished_vault(tmp_path)
    clone = _checkout(tmp_path, "plugin/.claude-plugin/plugin.json")
    assert _run(tmp_path) == f"VENV_MISSING:{clone / 'plugin'}"


def test_a_bare_git_dir_no_longer_short_circuits(tmp_path):
    _finished_vault(tmp_path)
    (tmp_path / "scout-plugin" / ".git").mkdir(parents=True)
    # Falls through to `claude plugin list` (the stub: no plugins) and the empty
    # marketplace cache, so nothing resolves.
    assert _run(tmp_path) == "PLUGIN_ROOT_NOT_FOUND"


def test_every_resolver_copy_matches_the_canonical_one():
    text = RUNBOOK.read_text(encoding="utf-8")
    section = text[text.index("## Locating scoutctl") :]
    canonical = re.search(r"```bash\n(NEW_ROOT=\"\"\n.*?PLUGIN_ROOT_NOT_FOUND\"; exit 1; \}\n)", section, re.S)
    assert canonical is not None
    starts = text.count('NEW_ROOT=""\n')
    assert starts >= 6
    assert text.count(canonical.group(1)) == starts
    assert 'NEW_ROOT="$HOME/scout-plugin"\n' not in text
