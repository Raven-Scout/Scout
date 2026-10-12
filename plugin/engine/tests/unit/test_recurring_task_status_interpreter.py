"""recurring-task-status.py under a Python that lacks PyYAML (#403).

The brain files run the script with whatever ``python3`` is first on PATH. After
a Homebrew upgrade that interpreter has a fresh, empty site-packages, and the
script died at ``import yaml``. It now re-execs itself under the engine's own
Python, named by the engine pointer (``~/.local/state/scout/engine.json``), or
the vault's ``.venv``; with neither it fails with one actionable line.

"An interpreter lacking yaml" is this test's own Python run with ``-S``: the
standard library still loads, site-packages (where PyYAML lives) does not.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scout.scripts import vault_drift as vd
from scout.scripts.bootstrap import BootstrapConfig, install, upgrade

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "recurring-task-status.py"

ENTITY = """---
type: recurring_task
name: weekly-status
cadence: weekly:friday
surface_window: T-0 morning
---

# weekly-status
"""


def _no_yaml_without_site() -> bool:
    probe = subprocess.run([sys.executable, "-S", "-c", "import yaml"], capture_output=True, env=_clean_env())
    return probe.returncode != 0


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "SCOUT_VENV_BOOTSTRAPPED"}}
    env.update(extra)
    return env


pytestmark = pytest.mark.skipif(not _no_yaml_without_site(), reason="PyYAML is importable without site-packages")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / "scripts").mkdir(parents=True)
    shutil.copy(TEMPLATE, v / "scripts" / "recurring-task-status.py")
    tasks = v / "knowledge-base" / "recurring-tasks"
    tasks.mkdir(parents=True)
    (tasks / "weekly-status.md").write_text(ENTITY, encoding="utf-8")
    return v


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


def _fake_python(path: Path, marker: Path) -> Path:
    """A stand-in interpreter that logs its use, then runs the real (yaml-bearing) Python."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "$0" >> "{marker}"\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _write_pointer(home: Path, python: Path) -> None:
    state = home / ".local" / "state" / "scout"
    state.mkdir(parents=True)
    pointer = {
        "engine_root": "/nonexistent",
        "managed_by": "dev",
        "python": str(python),
        "schema_version": 1,
        "scoutctl": "/nonexistent/scoutctl",
        "vault": "/nonexistent/vault",
        "version": "0.0.0",
        "written_at": "2026-01-01T00:00:00Z",
    }
    (state / "engine.json").write_text(json.dumps(pointer), encoding="utf-8")


def _run(vault: Path, home: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    """Run the vault's copy the way a brain file does: bare interpreter, vault cwd."""
    return subprocess.run(
        [sys.executable, "-S", "scripts/recurring-task-status.py", "--date", "2027-06-18", *args],
        cwd=vault,
        capture_output=True,
        text=True,
        env=_clean_env(HOME=str(home), **env),
        timeout=60,
    )


def test_engine_python_from_the_pointer_is_used(vault: Path, home: Path, tmp_path: Path) -> None:
    marker = tmp_path / "used"
    engine_py = _fake_python(tmp_path / "engine" / ".venv" / "bin" / "python", marker)
    _write_pointer(home, engine_py)

    result = _run(vault, home, "--json")

    assert result.returncode == 0, result.stderr
    assert marker.read_text().splitlines() == [str(engine_py)]
    # argv passes through unchanged: --json and --date reached the re-exec'd run.
    rows = json.loads(result.stdout)
    assert [(r["name"], r["status"]) for r in rows] == [("weekly-status", "due")]


def test_engine_python_wins_over_the_vault_venv(vault: Path, home: Path, tmp_path: Path) -> None:
    marker = tmp_path / "used"
    engine_py = _fake_python(tmp_path / "engine" / "python", marker)
    _fake_python(vault / ".venv" / "bin" / "python3", marker)
    _write_pointer(home, engine_py)

    assert _run(vault, home).returncode == 0
    assert marker.read_text().splitlines() == [str(engine_py)]


def test_vault_venv_is_the_fallback_when_the_pointer_python_is_gone(vault: Path, home: Path, tmp_path: Path) -> None:
    marker = tmp_path / "used"
    vault_py = _fake_python(vault / ".venv" / "bin" / "python3", marker)
    _write_pointer(home, tmp_path / "moved-away" / "python")

    result = _run(vault, home)

    assert result.returncode == 0, result.stderr
    assert marker.read_text().splitlines() == [str(vault_py)]
    assert "weekly-status" in result.stdout


def test_exit_code_passes_through_the_re_exec(vault: Path, home: Path, tmp_path: Path) -> None:
    _write_pointer(home, _fake_python(tmp_path / "engine" / "python", tmp_path / "used"))

    result = _run(vault, home, "--last-completed", "not-a-pair")

    # The script's own exit 2, reached under the engine Python — not the import guard's.
    assert result.returncode == 2
    assert "--last-completed expects SLUG=YYYY-MM-DD" in result.stderr


@pytest.mark.parametrize("pointer", ["absent", "malformed", "dead-python"])
def test_no_usable_interpreter_fails_with_one_actionable_line(
    vault: Path, home: Path, tmp_path: Path, pointer: str
) -> None:
    if pointer == "malformed":
        (home / ".local" / "state" / "scout").mkdir(parents=True)
        (home / ".local" / "state" / "scout" / "engine.json").write_text("{not json", encoding="utf-8")
    elif pointer == "dead-python":
        _write_pointer(home, tmp_path / "gone" / "python")

    result = _run(vault, home)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith("error: ")
    assert "PyYAML" in lines[0]
    assert "scoutctl bootstrap upgrade" in lines[0]
    assert "pip install" not in lines[0]


def test_the_guard_stops_a_re_exec_loop(vault: Path, home: Path, tmp_path: Path) -> None:
    """Already re-exec'd once and still no yaml: fail, never exec again."""
    marker = tmp_path / "used"
    _write_pointer(home, _fake_python(tmp_path / "engine" / "python", marker))

    result = _run(vault, home, SCOUT_VENV_BOOTSTRAPPED="1")

    assert result.returncode == 2
    assert not marker.exists()
    assert "PyYAML" in result.stderr


# ---- upgrade: a vault that edited the import guard merges, nothing is parked ----

GUARD_START = "def _run_under_a_python_with_yaml() -> None:\n"
GUARD_END = "_run_under_a_python_with_yaml()\n\n"
VAULT_LINES = "# vault-local interpreter fix\n"
OLD_MESSAGE = 'print("error: pyyaml not installed. run: pip install pyyaml", file=sys.stderr)'
VAULT_MESSAGE = 'print("error: pyyaml missing (vault wording)", file=sys.stderr)'


def _pre_fix(text: str) -> str:
    """The template as it shipped before #403: no re-exec guard."""
    start = text.index(GUARD_START)
    end = text.index(GUARD_END) + len(GUARD_END)
    return text[:start] + text[end:]


def _config(vault: Path, plugin: Path, version: str) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=plugin,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version=version,
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


def test_an_upgrade_merges_the_guard_into_a_vault_that_edited_the_import_block(tmp_path: Path) -> None:
    """The guard sits above the stdlib imports, away from `try: import yaml`, which
    is where a vault patches this file, so the three-way merge needs no hand help."""
    plugin = tmp_path / "plugin"
    shutil.copytree(TEMPLATE.parents[1], plugin / "templates")
    rel = "scripts/recurring-task-status.py"
    shipped = plugin / "templates" / rel
    current = shipped.read_text(encoding="utf-8")
    shipped.write_text(_pre_fix(current), encoding="utf-8")
    vault = tmp_path / "Scout"
    install(_config(vault, plugin, "0.17.0"))

    live = vault / rel
    guard = "\ntry:\n    import yaml\n"
    edited = live.read_text(encoding="utf-8").replace(guard, f"\n{VAULT_LINES}{guard.lstrip()}")
    live.write_text(edited.replace(OLD_MESSAGE, VAULT_MESSAGE), encoding="utf-8")
    shipped.write_text(current, encoding="utf-8")

    result = upgrade(_config(vault, plugin, "0.17.1"))

    merged = live.read_text(encoding="utf-8")
    assert vd.VaultEdit(rel, "merged") in result.vault_edits
    assert GUARD_START in merged and VAULT_LINES in merged and VAULT_MESSAGE in merged
    assert not (vault / ".scout-state" / "drift" / f"{rel}.plugin").exists()
