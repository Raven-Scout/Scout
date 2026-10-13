"""Wheel-packaging smoke test.

Builds a wheel from the engine source, installs it into a fresh
virtualenv, and exercises the CLI + config loader. This is the safety
net behind the importlib.resources defaults lookup: a future change
that drops scout/defaults/ from the package or restructures
PACKAGE_DEFAULTS_PATH navigation would break this test.

Marked `slow` because building a wheel and creating a venv each run
takes a few seconds. CI runs it once per matrix row; local devs can
skip it via `-m 'not slow'` when iterating quickly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scout import __version__

ENGINE_DIR = Path(__file__).parent.parent.parent


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _uv_cache_dir() -> str | None:
    """uv's cache dir, resolved at import — before ``_hermetic_env`` points HOME
    at an empty tmp dir, which would hand every uv call a cold cache."""
    if not _have("uv"):  # pragma: no cover
        return None
    return subprocess.run(["uv", "cache", "dir"], capture_output=True, text=True, check=True).stdout.strip()


_UV_CACHE_DIR = _uv_cache_dir()


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """``subprocess.run(check=True)`` that keeps the command's stderr in the
    failure — CalledProcessError's message drops it, which hid why ``uv build``
    exited 2 (#309)."""
    r = subprocess.run(argv, capture_output=True, text=True)
    assert r.returncode == 0, f"{argv} exited {r.returncode}\nstderr:\n{r.stderr}"
    return r


def _uv(*args: str) -> subprocess.CompletedProcess[str]:
    """Run uv offline against the developer's real cache.

    Online, every build re-resolves ``hatchling`` and every install the runtime
    deps against PyPI, so one network blip failed the test with exit 2 (#309).
    Installing the engine venv (``uv pip install -e ".[dev]"``) already cached
    all of it — the editable install builds with hatchling too.
    """
    assert _UV_CACHE_DIR is not None
    env = {**os.environ, "UV_CACHE_DIR": _UV_CACHE_DIR}
    r = subprocess.run(["uv", *args, "--offline"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, (
        f"uv {' '.join(args)} --offline exited {r.returncode}. This test runs uv offline "
        f'from the cache that `uv pip install -e ".[dev]"` fills; run that first.\nstderr:\n{r.stderr}'
    )
    return r


pytestmark = pytest.mark.slow


@pytest.mark.skipif(not _have("uv"), reason="uv required to build/install wheel")
def test_wheel_install_runs_scoutctl_and_loads_defaults(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    venv_dir = tmp_path / "venv"

    # 1. Build the wheel.
    _uv("build", "--wheel", str(ENGINE_DIR), "-o", str(dist_dir))
    wheels = list(dist_dir.glob("scout_engine-*.whl"))
    assert wheels, f"no wheel produced in {dist_dir}"
    wheel = wheels[0]

    # 2. Create a fresh venv (so the test cannot accidentally pick up
    #    the editable install on the dev's PATH), on this run's Python: the
    #    cache holds wheels for it, not for whatever python is first on PATH.
    _uv("venv", "--python", sys.executable, str(venv_dir))
    venv_python = venv_dir / "bin" / "python"
    venv_scoutctl = venv_dir / "bin" / "scoutctl"

    # 3. Install the built wheel (and only the wheel) into the venv.
    _uv("pip", "install", "--python", str(venv_python), str(wheel))

    # 4. scoutctl version: confirms the entry point script is wired.
    r = _run([str(venv_scoutctl), "version"])
    assert r.stdout.strip() == __version__

    # 5. scoutctl manifest show: confirms imports + Typer enumeration.
    r = _run([str(venv_scoutctl), "manifest", "show"])
    payload = json.loads(r.stdout)
    assert payload["version"] == __version__

    # 6. load_config() reads the packaged defaults via importlib.resources.
    #    This is the assertion that justifies moving defaults/ under scout/.
    probe = (
        "import json, sys;"
        "from scout.config import load_config;"
        "cfg = load_config();"
        "json.dump({'schema': cfg.get('schema_version'),"
        " 'has_budget': 'budget' in cfg,"
        " 'has_thresholds': 'thresholds' in cfg}, sys.stdout)"
    )
    r = _run([str(venv_python), "-c", probe])
    probe_out = json.loads(r.stdout)
    assert probe_out == {"schema": 1, "has_budget": True, "has_thresholds": True}


def test_engine_dir_constant_is_engine_root() -> None:
    """Sanity check that ENGINE_DIR fixture above resolves correctly.

    This catches a path-navigation regression in the smoke test itself
    without paying the cost of a wheel build.
    """
    assert (ENGINE_DIR / "pyproject.toml").exists()
    assert (ENGINE_DIR / "scout" / "__init__.py").exists()


# Sanity-check sys.executable for completeness on platforms missing uv.
if not _have("uv"):  # pragma: no cover
    _ = sys.executable
