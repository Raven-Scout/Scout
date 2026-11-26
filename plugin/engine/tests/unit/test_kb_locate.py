"""``scoutctl kb vault`` / ``scout.kb.locate``: the vault a KB question goes to, from any working directory.

The scout-ask skill runs it first, so a session in another repository reads the configured vault. Order:
SCOUT_DATA_DIR, the engine pointer, ~/Scout (only with no pointer). Never the cwd: a cloned repo with a planted
scout-config.yaml and scripts/scoutctl would otherwise get its script run (#721 review).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import paths
from scout.cli import app
from scout.kb import locate
from scout.kb.locate import VaultLocation, engine_scoutctl, locate_vault
from scout.scripts.engine_pointer import EnginePointer, write_pointer


def _vault(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    (p / "scout-config.yaml").write_text("{}\n", encoding="utf-8")
    return p.resolve()


def _pointer(home: Path, vault: Path | str) -> None:
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


def test_env_wins_and_names_the_installed_vault_it_overrides(tmp_path: Path, home: Path, elsewhere: Path) -> None:
    v = _vault(tmp_path / "env-vault")
    installed = _vault(tmp_path / "pointed")
    _pointer(home, installed)
    loc = locate_vault({"SCOUT_DATA_DIR": str(v), "HOME": str(home)}, elsewhere)
    assert (loc.vault, loc.source, loc.in_vault) == (v, "env", False)
    assert loc.reason == f"SCOUT_DATA_DIR ({v}) overrides the installed vault ({installed})"
    # The same vault as the pointer's, or no pointer at all: nothing to say.
    assert locate_vault({"SCOUT_DATA_DIR": str(installed), "HOME": str(home)}, elsewhere).reason == ""
    assert locate_vault({"SCOUT_DATA_DIR": str(v), "HOME": str(tmp_path)}, elsewhere).reason == ""


def test_env_is_authoritative(tmp_path: Path, home: Path, elsewhere: Path) -> None:
    """A SCOUT_DATA_DIR that isn't an absolute path to a vault never falls through to another vault."""
    _pointer(home, _vault(tmp_path / "pointed"))
    for value, why in ((str(tmp_path / "nope"), "is not a Scout vault"), ("relative/vault", "not an absolute path")):
        loc = locate_vault({"SCOUT_DATA_DIR": value, "HOME": str(home)}, elsewhere)
        assert (loc.vault, loc.source) == (None, "none") and why in loc.reason and "SCOUT_DATA_DIR" in loc.reason


def test_env_vault_that_holds_the_cwd_is_in_vault(tmp_path: Path, home: Path) -> None:
    v = _vault(tmp_path / "vault")
    (v / "knowledge-base").mkdir()
    assert locate_vault({"SCOUT_DATA_DIR": str(v), "HOME": str(home)}, v / "knowledge-base").in_vault


def test_a_vault_in_the_cwd_never_beats_the_pointer(tmp_path: Path, home: Path) -> None:
    """The cwd only decides in_vault: a vault worktree or sandbox opened as the cwd still reads the installed one."""
    here = _vault(tmp_path / "worktree-vault")
    (here / "action-items").mkdir()
    installed = _vault(tmp_path / "installed")
    _pointer(home, installed)
    assert locate_vault({"HOME": str(home)}, here / "action-items") == VaultLocation(installed, "pointer", False)


def test_a_planted_vault_in_a_cloned_repo_is_ignored(tmp_path: Path, home: Path) -> None:
    """A repo carrying scout-config.yaml and scripts/scoutctl loses to the pointer, and with no configured vault
    it is still never chosen."""
    repo = _vault(tmp_path / "cloned-repo")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "scoutctl").write_text("#!/bin/sh\necho planted\n", encoding="utf-8")
    (repo / "scripts" / "scoutctl").chmod(0o755)
    assert locate_vault({"HOME": str(home)}, repo).vault is None
    installed = _vault(tmp_path / "installed")
    _pointer(home, installed)
    assert locate_vault({"HOME": str(home)}, repo).vault == installed


def test_another_repo_gets_the_pointer_vault(tmp_path: Path, home: Path, elsewhere: Path) -> None:
    installed = _vault(tmp_path / "installed")
    _pointer(home, installed)
    assert locate_vault({"HOME": str(home)}, elsewhere) == VaultLocation(installed, "pointer", False)
    assert locate_vault({"HOME": str(home)}, installed / "x") == VaultLocation(installed, "pointer", True)


@pytest.mark.parametrize(("pointed", "why"), [("moved-away", "is not a Scout vault"), ("rel", "not an absolute")])
def test_a_bad_pointer_is_an_error_not_a_fall_back(
    tmp_path: Path, home: Path, elsewhere: Path, pointed: str, why: str
) -> None:
    _vault(home / "Scout")  # present, but the pointer says the vault is elsewhere
    _pointer(home, tmp_path / pointed if pointed == "moved-away" else pointed)
    loc = locate_vault({"HOME": str(home)}, elsewhere)
    assert (loc.vault, loc.source) == (None, "none") and why in loc.reason and "pointer" in loc.reason


def test_a_malformed_pointer_counts_as_none_and_says_so(home: Path, elsewhere: Path) -> None:
    from scout.scripts.engine_pointer import pointer_path

    pointer_path(home).parent.mkdir(parents=True)
    pointer_path(home).write_text("{not json", encoding="utf-8")
    loc = locate_vault({"HOME": str(home)}, elsewhere)
    assert loc.vault is None and "malformed" in loc.reason and "no Scout vault is configured" in loc.reason
    default = _vault(home / "Scout")
    loc = locate_vault({"HOME": str(home)}, elsewhere)
    assert (loc.vault, loc.source) == (default, "default")
    assert "malformed" in loc.reason and loc.reason.endswith(f"using {default}")


def test_no_pointer_falls_back_to_home_scout(home: Path, elsewhere: Path) -> None:
    default = _vault(home / "Scout")
    assert locate_vault({"HOME": str(home)}, elsewhere) == VaultLocation(default, "default", False)


def test_no_vault_configured(home: Path, elsewhere: Path) -> None:
    (home / "Scout").mkdir()  # a folder named Scout is not a vault without scout-config.yaml
    loc = locate_vault({"HOME": str(home)}, elsewhere)
    assert loc.vault is None and loc.source == "none" and "no Scout vault" in loc.reason
    assert loc.as_json(None) == {
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
    def boom(_: str | Path) -> Path:
        raise RuntimeError("symlink loop")

    monkeypatch.setattr(paths, "resolve_path", boom)
    loc = locate_vault({"SCOUT_DATA_DIR": "/loop", "HOME": str(home)}, Path("/loop"))
    assert loc.vault is None and "SCOUT_DATA_DIR" in loc.reason
    assert locate_vault({"HOME": str(home)}, Path("/loop")).vault is None


def test_as_json_names_the_engine(tmp_path: Path) -> None:
    v = _vault(tmp_path / "v")
    engine = tmp_path / "venv" / "bin" / "scoutctl"
    assert VaultLocation(v, "env", True).as_json(engine) == {
        "vault": str(v),
        "scoutctl": str(engine),
        "source": "env",
        "in_vault": True,
        "reason": "",
    }


# --- the engine the skill calls --------------------------------------------------------------------------------


def _exe(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\n", encoding="utf-8")
    p.chmod(0o755)
    return p


def test_engine_scoutctl_is_the_console_script_beside_this_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    beside = _exe(tmp_path / "venv" / "bin" / "scoutctl")
    monkeypatch.setattr("scout.scripts.install_schedule_plist.resolve_scoutctl_bin", lambda: beside)
    assert engine_scoutctl() == beside


def test_engine_scoutctl_falls_back_to_the_checkouts_launcher(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import scout

    monkeypatch.setattr("scout.scripts.install_schedule_plist.resolve_scoutctl_bin", lambda: tmp_path / "missing")
    launcher = Path(scout.__file__).resolve().parent.parent / "bin" / "scoutctl"
    assert engine_scoutctl() == launcher  # this checkout ships engine/bin/scoutctl
    monkeypatch.setattr(locate, "_executable", lambda p: False)
    assert engine_scoutctl() is None


def test_executable_needs_an_absolute_executable_file(tmp_path: Path) -> None:
    exe = _exe(tmp_path / "x")
    plain = tmp_path / "plain"
    plain.write_text("", encoding="utf-8")
    assert locate._executable(exe)
    assert not locate._executable(plain) and not locate._executable(tmp_path) and not locate._executable(Path("x"))


# --- the CLI ---------------------------------------------------------------------------------------------


def _run(cwd: Path, *args: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    old = Path.cwd()
    os.chdir(cwd)
    try:
        r = CliRunner().invoke(app, ["kb", "vault", *args], env=env)
    finally:
        os.chdir(old)
    return r.exit_code, r.stdout, r.stderr


def test_cli_prints_the_installed_vault_from_another_repo(tmp_path: Path, elsewhere: Path) -> None:
    installed = _vault(tmp_path / "installed")
    _pointer(Path(os.environ["HOME"]), installed)
    code, out, err = _run(elsewhere)
    assert (code, out, err) == (0, f"{installed}\n", "")
    code, out, _ = _run(elsewhere, "--json")
    assert code == 0 and json.loads(out) == VaultLocation(installed, "pointer", False).as_json(engine_scoutctl())


def test_cli_warns_on_stderr_when_env_overrides_the_installed_vault(tmp_path: Path, elsewhere: Path) -> None:
    _pointer(Path(os.environ["HOME"]), _vault(tmp_path / "installed"))
    v = _vault(tmp_path / "other")
    code, out, err = _run(elsewhere, env={"SCOUT_DATA_DIR": str(v)})
    assert (code, out) == (0, f"{v}\n") and "overrides the installed vault" in err


def test_cli_exits_1_when_no_vault_is_usable(elsewhere: Path) -> None:
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
