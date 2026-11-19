"""``scoutctl kb vault`` / ``scout.kb.locate``: the vault a KB question goes to, from any working directory.

The scout-ask skill runs it first so a session in some other repository reads the configured vault, never
whatever repository the cwd is in. Order: SCOUT_DATA_DIR, the vault holding the cwd, the engine pointer, ~/Scout.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import paths
from scout.cli import app
from scout.kb.locate import VaultLocation, locate_vault
from scout.scripts.engine_pointer import EnginePointer, write_pointer


def _vault(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    (p / "scout-config.yaml").write_text("{}\n", encoding="utf-8")
    return p.resolve()


def _pointer(home: Path, vault: Path) -> None:
    write_pointer(
        EnginePointer(
            version="0.0.0",
            engine_root="/engine",
            python="/engine/.venv/bin/python",
            scoutctl="/engine/bin/scoutctl",
            vault=str(vault),
            managed_by="dev",
            written_at="2026-01-01T00:00:00Z",
        ),
        home=home,
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def elsewhere(tmp_path: Path) -> Path:
    """Some other repository: not a vault, no vault above it."""
    d = tmp_path / "work" / "some-repo"
    d.mkdir(parents=True)
    return d


def test_env_wins_and_is_authoritative(tmp_path: Path, home: Path, elsewhere: Path) -> None:
    v = _vault(tmp_path / "env-vault")
    _pointer(home, _vault(tmp_path / "pointed"))
    loc = locate_vault({"SCOUT_DATA_DIR": str(v), "HOME": str(home)}, elsewhere)
    assert loc == VaultLocation(v, "env", False)

    # A SCOUT_DATA_DIR that isn't a vault never falls through to another vault.
    loc = locate_vault({"SCOUT_DATA_DIR": str(tmp_path / "nope"), "HOME": str(home)}, elsewhere)
    assert loc.vault is None and loc.source == "none" and "SCOUT_DATA_DIR" in loc.reason


def test_env_vault_that_holds_the_cwd_is_in_vault(tmp_path: Path, home: Path) -> None:
    v = _vault(tmp_path / "vault")
    (v / "knowledge-base").mkdir()
    assert locate_vault({"SCOUT_DATA_DIR": str(v), "HOME": str(home)}, v / "knowledge-base").in_vault


def test_the_vault_holding_the_cwd_beats_the_pointer(tmp_path: Path, home: Path) -> None:
    here = _vault(tmp_path / "worktree-vault")
    (here / "action-items").mkdir()
    _pointer(home, _vault(tmp_path / "installed"))
    assert locate_vault({"HOME": str(home)}, here / "action-items") == VaultLocation(here, "cwd", True)


def test_another_repo_gets_the_pointer_vault_not_its_own_repo(tmp_path: Path, home: Path, elsewhere: Path) -> None:
    installed = _vault(tmp_path / "installed")
    _pointer(home, installed)
    assert locate_vault({"HOME": str(home)}, elsewhere) == VaultLocation(installed, "pointer", False)


def test_a_stale_pointer_falls_back_to_home_scout(tmp_path: Path, home: Path, elsewhere: Path) -> None:
    _pointer(home, tmp_path / "moved-away")
    default = _vault(home / "Scout")
    assert locate_vault({"HOME": str(home)}, elsewhere) == VaultLocation(default, "default", False)


def test_no_vault_configured(home: Path, elsewhere: Path) -> None:
    (home / "Scout").mkdir()  # a folder named Scout is not a vault without scout-config.yaml
    loc = locate_vault({"HOME": str(home)}, elsewhere)
    assert loc.vault is None and loc.source == "none" and "no Scout vault" in loc.reason
    assert loc.as_json() == {
        "vault": None,
        "scoutctl": None,
        "source": "none",
        "in_vault": False,
        "reason": loc.reason,
    }


def test_no_cwd_still_finds_the_installed_vault(home: Path) -> None:
    default = _vault(home / "Scout")
    assert locate_vault({"HOME": str(home)}, None) == VaultLocation(default, "default", False)


def test_unresolvable_paths_never_raise(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    def boom(p: str | Path) -> Path:
        raise RuntimeError("symlink loop")

    monkeypatch.setattr(paths, "resolve_path", boom)
    loc = locate_vault({"SCOUT_DATA_DIR": "/loop", "HOME": str(home)}, Path("/loop"))
    assert loc.vault is None and "SCOUT_DATA_DIR" in loc.reason
    assert locate_vault({"HOME": str(home)}, Path("/loop")).vault is None


def test_as_json_names_the_vault_scoutctl(tmp_path: Path) -> None:
    v = _vault(tmp_path / "v")
    assert VaultLocation(v, "env", True).as_json() == {
        "vault": str(v),
        "scoutctl": str(v / "scripts" / "scoutctl"),
        "source": "env",
        "in_vault": True,
        "reason": "",
    }


# --- the CLI ---------------------------------------------------------------------------------------------


def _run(cwd: Path, *args: str) -> tuple[int, str, str]:
    old = Path.cwd()
    os.chdir(cwd)
    try:
        r = CliRunner().invoke(app, ["kb", "vault", *args])
    finally:
        os.chdir(old)
    return r.exit_code, r.stdout, r.stderr


def test_cli_prints_the_installed_vault_from_another_repo(tmp_path: Path, elsewhere: Path) -> None:
    installed = _vault(tmp_path / "installed")
    _pointer(Path(os.environ["HOME"]), installed)
    code, out, _ = _run(elsewhere)
    assert (code, out) == (0, f"{installed}\n")
    code, out, _ = _run(elsewhere, "--json")
    assert code == 0 and json.loads(out) == VaultLocation(installed, "pointer", False).as_json()


def test_cli_exits_1_when_no_vault_is_configured(elsewhere: Path) -> None:
    code, out, err = _run(elsewhere)
    assert (code, out) == (1, "")
    assert "no Scout vault is configured" in err
    code, out, _ = _run(elsewhere, "--json")
    assert code == 1 and json.loads(out)["vault"] is None


def test_cli_survives_a_deleted_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    default = _vault(Path(os.environ["HOME"]) / "Scout")

    def gone() -> Path:
        raise FileNotFoundError("cwd deleted")

    monkeypatch.setattr(Path, "cwd", staticmethod(gone))
    r = CliRunner().invoke(app, ["kb", "vault"])
    assert (r.exit_code, r.stdout) == (0, f"{default}\n")
