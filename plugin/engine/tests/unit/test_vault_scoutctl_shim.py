"""The vault's scripts/scoutctl: how brain files reach the engine when it isn't on PATH."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scout.scripts.phase_assembly import render_template

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "scoutctl.sh.tmpl"


def _engine(path: Path, name: str) -> Path:
    """A fake engine that reports who it is, its args and the vault it was pointed at."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "{name} $* data=$SCOUT_DATA_DIR"\n', encoding="utf-8")
    path.chmod(0o755)
    return path


def _shim(vault: Path, scoutctl_bin: Path) -> Path:
    shim = vault / "scripts" / "scoutctl"
    shim.parent.mkdir(parents=True)
    shim.write_text(render_template(TEMPLATE.read_text(encoding="utf-8"), {"SCOUTCTL_BIN": str(scoutctl_bin)}))
    shim.chmod(0o755)
    return shim


def _run(shim: Path, home: Path, *, path: str = "/usr/bin:/bin", **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(shim), "health", "--json"],
        env={"HOME": str(home), "PATH": path, **env},
        cwd=shim.parent.parent,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_on_path_wins_and_the_vault_is_where_the_shim_lives(tmp_path: Path, home: Path) -> None:
    on_path = _engine(tmp_path / "bin" / "scoutctl", "path-engine")
    shim = _shim(tmp_path / "moved" / "vault", _engine(tmp_path / "rendered" / "scoutctl", "rendered"))
    done = _run(shim, home, path=f"{on_path.parent}:/usr/bin:/bin")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == f"path-engine health --json data={(tmp_path / 'moved' / 'vault').resolve()}"


def test_an_explicit_scout_data_dir_wins(tmp_path: Path, home: Path) -> None:
    shim = _shim(tmp_path / "vault", _engine(tmp_path / "rendered" / "scoutctl", "rendered"))
    done = _run(shim, home, SCOUT_DATA_DIR="/somewhere/else")
    assert done.stdout.strip() == "rendered health --json data=/somewhere/else"


def test_falls_back_to_the_plugin_root_then_the_newest_cached_plugin(tmp_path: Path, home: Path) -> None:
    shim = _shim(tmp_path / "vault", tmp_path / "gone" / "scoutctl")
    root = tmp_path / "plugin-root"
    _engine(root / "engine" / "bin" / "scoutctl", "plugin-root")
    assert _run(shim, home, CLAUDE_PLUGIN_ROOT=str(root)).stdout.startswith("plugin-root ")

    cache = home / ".claude" / "plugins" / "cache"
    _engine(cache / "scout-plugin" / "scout" / "0.9.0" / "engine" / "bin" / "scoutctl", "v0.9.0")
    _engine(cache / "agnes" / "scout" / "0.18.2" / "engine" / "bin" / "scoutctl", "v0.18.2")
    _engine(cache / "agnes" / "scout" / "0.18.10" / "engine" / "bin" / "scoutctl", "v0.18.10")
    assert _run(shim, home).stdout.startswith("v0.18.10 health --json")


def test_no_engine_anywhere_fails_loudly(tmp_path: Path, home: Path) -> None:
    shim = _shim(tmp_path / "vault", tmp_path / "gone" / "scoutctl")
    done = _run(shim, home)
    assert done.returncode == 127
    assert "no Scout engine found" in done.stderr


@pytest.mark.skipif(not os.access("/bin/bash", os.X_OK), reason="needs bash")
def test_the_template_is_registered_as_an_executable_vault_script() -> None:
    from scout.scripts import bootstrap

    assert ("scripts/scoutctl", "templates/scripts/scoutctl.sh.tmpl") in bootstrap._CAT1_TEMPLATES
    managed = {m.vault_rel: m for m in bootstrap._MANAGED_FILES}
    assert managed["scripts/scoutctl"].executable and managed["scripts/scoutctl"].rendered
