"""No Scout launcher runs a program planted in the working directory.

Skills and hooks start Scout from any cwd (a cloned repo, the vault), and a
user's PATH can hold relative entries: `.`, `./bin`, `node_modules/.bin`, or an
empty entry, which also means the cwd. A `python3`, `bash`, `scoutctl`, `uv`,
`git` or `dirname` planted in that cwd would then run as the user. Each test
here runs a real launcher with real bash (or sh) and such a PATH, from a folder
full of planted programs, and asserts none of them ever ran.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scout.kb.hook import default_scoutctl, render_hook
from scout.scripts.phase_assembly import render_template
from tests.unit.test_install_sh_runs_setup import INSTALL_SH, _world
from tests.unit.test_install_venv_script import SCRIPT as INSTALL_VENV
from tests.unit.test_install_venv_script import _fake_uv

ENGINE_DIR = Path(__file__).resolve().parents[2]
LAUNCHER = ENGINE_DIR / "bin" / "scoutctl"
SHIM_TEMPLATE = ENGINE_DIR.parent / "templates" / "scripts" / "scoutctl.sh.tmpl"

# Everything a launcher here looks up by name, and the interpreters it might try.
PLANTED = (
    "bash sh env python python3 python3.11 python3.12 python3.13 scoutctl uv git claude curl "
    "dirname sed head tail sort rm cat mkdir tr cmp grep uname xcode-select ls chmod printf"
).split()

# The relative entries a careless or hostile PATH might carry: the cwd itself, a cwd-relative
# folder, and the empty entries (leading, doubled, trailing) that a shell also reads as the cwd.
RELATIVE = [".", "./bin", "node_modules/.bin", ""]


def _plant(cwd: Path) -> Path:
    """Fill cwd, cwd/bin and cwd/node_modules/.bin with programs that leave a mark if they run."""
    marker = cwd.parent / f"{cwd.name}-planted-ran"
    for folder in (cwd, cwd / "bin", cwd / "node_modules" / ".bin"):
        folder.mkdir(parents=True, exist_ok=True)
        for name in PLANTED:
            p = folder / name
            p.write_text(f'#!/bin/sh\necho "{folder.name}/{name} $*" >>"{marker}"\necho PLANTED\nexit 0\n')
            p.chmod(0o755)
    return marker


def _hostile_path(*absolute: str | Path) -> str:
    """A PATH that starts and ends with relative entries, with the absolute ones in between."""
    return ":".join(["", *RELATIVE, *map(str, absolute), *RELATIVE, ""])


def _assert_nothing_planted_ran(marker: Path, out: subprocess.CompletedProcess[str]) -> None:
    ran = marker.read_text() if marker.exists() else ""
    assert not ran, f"planted programs ran:\n{ran}\nstdout={out.stdout!r}\nstderr={out.stderr!r}"
    assert "PLANTED" not in out.stdout + out.stderr


def _fake_python3(folder: Path, tag: str = "SYSTEM_PY") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    py3 = folder / "python3"
    py3.write_text(f'#!/bin/sh\necho "{tag} $*"\n')
    py3.chmod(0o755)
    return folder


def _plugin_tree(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    (root / "engine" / "bin").mkdir(parents=True)
    shutil.copy(LAUNCHER, root / "engine" / "bin" / "scoutctl")
    (root / "engine" / "bin" / "scoutctl").chmod(0o755)
    return root


# --- engine/bin/scoutctl ---------------------------------------------------------------------


def _launch(root: Path, cwd: Path, home: Path, path: str) -> subprocess.CompletedProcess[str]:
    # Executed directly, as the hooks and skills do, so the shebang is part of what is tested:
    # `#!/usr/bin/env bash` would have env run the planted bash.
    return subprocess.run(
        [str(root / "engine" / "bin" / "scoutctl"), "version"],
        env={"HOME": str(home), "PATH": path},
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_launcher_last_resort_takes_python3_from_an_absolute_path_entry(tmp_path: Path) -> None:
    """No venv, no pointer: the planted python3 in `.` comes first on PATH, the real one later."""
    root = _plugin_tree(tmp_path)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)
    sysbin = _fake_python3(tmp_path / "sysbin")

    out = _launch(root, repo, tmp_path / "home", _hostile_path(sysbin, "/usr/bin", "/bin"))

    _assert_nothing_planted_ran(marker, out)
    assert out.returncode == 0, out
    assert out.stdout.strip() == "SYSTEM_PY -P -m scout.cli version"


@pytest.mark.parametrize("path", ["", ".", ".:./bin", "./bin:", "::"], ids=repr)
def test_launcher_with_only_relative_path_entries_never_runs_a_planted_program(tmp_path: Path, path: str) -> None:
    """PATH=.:./bin and friends: nothing absolute to search, so the launcher falls back to the
    system folders. Whatever python3 it finds there (or none), it is never the cwd's."""
    root = _plugin_tree(tmp_path)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)

    out = _launch(root, repo, tmp_path / "home", path)

    _assert_nothing_planted_ran(marker, out)


def test_launcher_hands_the_engine_a_path_without_relative_entries(tmp_path: Path) -> None:
    """The engine's own children (git, claude) look PATH up too, so they inherit the cleaned one."""
    root = _plugin_tree(tmp_path)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    (sysbin / "python3").write_text('#!/bin/sh\nprintf "%s\\n" "$PATH"\n')
    (sysbin / "python3").chmod(0o755)

    out = _launch(root, repo, tmp_path / "home", _hostile_path(sysbin, "/usr/bin", "/bin"))

    _assert_nothing_planted_ran(marker, out)
    assert out.stdout.strip() == f"{sysbin}:/usr/bin:/bin"


def test_launcher_venv_still_wins_under_a_hostile_path(tmp_path: Path) -> None:
    """The cleaning changes nothing for a normal install: the venv runs, with its args intact."""
    root = _plugin_tree(tmp_path)
    cli = root / ".venv" / "src" / "scout" / "cli.py"
    cli.parent.mkdir(parents=True)
    cli.touch()
    py = root / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    (root / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    py.write_text(f'#!/bin/sh\nif [ "$1 $2" = "-P -c" ]; then echo "{cli}"; exit 0; fi\necho "VENV_PY $*"\n')
    py.chmod(0o755)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)

    out = _launch(root, repo, tmp_path / "home", _hostile_path("/usr/bin", "/bin"))

    _assert_nothing_planted_ran(marker, out)
    assert out.stdout.strip() == "VENV_PY -P -m scout.cli version"


def test_launcher_shebang_never_searches_path() -> None:
    assert LAUNCHER.read_text().splitlines()[0] == "#!/bin/bash"


# --- the vault's scripts/scoutctl ------------------------------------------------------------


def test_vault_shim_never_runs_a_planted_scoutctl(tmp_path: Path) -> None:
    """The shim's first choice is a scoutctl on PATH. A relative entry must not make that the cwd's."""
    engine = tmp_path / "engine" / "scoutctl"
    engine.parent.mkdir()
    engine.write_text('#!/bin/sh\necho "real-engine $*"\n')
    engine.chmod(0o755)
    vault = tmp_path / "vault"
    shim = vault / "scripts" / "scoutctl"
    shim.parent.mkdir(parents=True)
    shim.write_text(render_template(SHIM_TEMPLATE.read_text(encoding="utf-8"), {"SCOUTCTL_BIN": str(engine)}))
    shim.chmod(0o755)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)

    out = subprocess.run(
        [str(shim), "kb", "vault"],
        env={"HOME": str(tmp_path / "home"), "PATH": _hostile_path("/usr/bin", "/bin")},
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    _assert_nothing_planted_ran(marker, out)
    assert out.returncode == 0, out
    assert out.stdout.strip() == "real-engine kb vault"


def test_vault_shim_still_prefers_an_absolute_path_scoutctl(tmp_path: Path) -> None:
    on_path = tmp_path / "onpath"
    on_path.mkdir()
    (on_path / "scoutctl").write_text('#!/bin/sh\necho "path-engine $*"\n')
    (on_path / "scoutctl").chmod(0o755)
    vault = tmp_path / "vault"
    shim = vault / "scripts" / "scoutctl"
    shim.parent.mkdir(parents=True)
    shim.write_text(render_template(SHIM_TEMPLATE.read_text(encoding="utf-8"), {"SCOUTCTL_BIN": "/nonexistent"}))
    shim.chmod(0o755)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)

    out = subprocess.run(
        [str(shim), "health"],
        env={"HOME": str(tmp_path / "home"), "PATH": _hostile_path(on_path, "/usr/bin", "/bin")},
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    _assert_nothing_planted_ran(marker, out)
    assert out.stdout.strip() == "path-engine health"


# --- scripts/install-venv.sh -----------------------------------------------------------------


def test_install_venv_never_runs_a_planted_uv(tmp_path: Path) -> None:
    """/scout-update runs install-venv.sh with the vault as cwd; uv is found by name."""
    root = tmp_path / "plugin"
    (root / "engine").mkdir(parents=True)
    (root / "scripts").mkdir()
    shutil.copy(INSTALL_VENV, root / "scripts" / "install-venv.sh")
    uv = _fake_uv(tmp_path)  # tmp_path/fakebin/uv
    log = tmp_path / "uv.log"
    repo = tmp_path / "vault"
    marker = _plant(repo)

    out = subprocess.run(
        ["/bin/bash", str(root / "scripts" / "install-venv.sh")],
        env={
            "HOME": str(tmp_path / "home"),
            "PATH": _hostile_path(uv.parent, "/usr/bin", "/bin"),
            "FAKE_UV_LOG": str(log),
        },
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    _assert_nothing_planted_ran(marker, out)
    assert out.returncode == 0, out
    assert f"using uv ({uv})" in out.stdout


# --- install.sh ------------------------------------------------------------------------------


def test_install_sh_never_runs_a_planted_tool(tmp_path: Path) -> None:
    """`curl … | bash` runs from whatever folder the user is in; claude, git, uv, python3 and
    curl are all found by name, and so is everything the engine setup starts after them."""
    env, log = _world(tmp_path)
    repo = tmp_path / "cloned-repo"
    marker = _plant(repo)
    env["PATH"] = _hostile_path(tmp_path / "bin", "/usr/bin", "/bin")

    out = subprocess.run(
        ["/bin/bash", str(INSTALL_SH), "--yes"],
        env=env,
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    _assert_nothing_planted_ran(marker, out)
    assert out.returncode == 0, out
    assert log.read_text().splitlines()[0] == "setup"


# --- the vault's git pre-commit hook ---------------------------------------------------------


def test_pre_commit_hook_never_runs_a_planted_scoutctl(tmp_path: Path) -> None:
    """The hook's fallback is `command -v scoutctl`, and git runs it with the vault as cwd."""
    vault = tmp_path / "vault"
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    marker = _plant(vault)
    real = tmp_path / "realbin"
    real.mkdir()
    (real / "scoutctl").write_text('#!/bin/sh\necho "real-scoutctl $*" >&2\nexit 0\n')
    (real / "scoutctl").chmod(0o755)
    hook = tmp_path / "pre-commit"
    hook.write_text(render_hook(str(tmp_path / "gone" / "scoutctl")))  # baked path is gone: fallback
    hook.chmod(0o755)

    out = subprocess.run(
        ["/bin/sh", str(hook)],
        env={"HOME": str(tmp_path / "home"), "PATH": _hostile_path(real, "/usr/bin", "/bin")},
        cwd=vault,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    _assert_nothing_planted_ran(marker, out)
    assert out.returncode == 0, out
    assert "real-scoutctl kb lint --staged" in out.stderr


def test_default_scoutctl_skips_relative_path_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scoutctl baked into the hook is never a cwd-relative path."""
    repo = tmp_path / "cloned-repo"
    _plant(repo)
    real = tmp_path / "realbin"
    real.mkdir()
    (real / "scoutctl").write_text("#!/bin/sh\n")
    (real / "scoutctl").chmod(0o755)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "no-venv" / "python"))

    monkeypatch.setenv("PATH", _hostile_path(real))
    assert default_scoutctl() == str(real / "scoutctl")

    monkeypatch.setenv("PATH", ":".join(RELATIVE))
    assert default_scoutctl() == "scoutctl"
