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
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scout import __version__

ENGINE_DIR = Path(__file__).parent.parent.parent


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """``subprocess.run(check=True)`` that keeps the command's stderr in the
    failure — CalledProcessError's message drops it, which hid why ``uv build``
    exited 2 (#309)."""
    r = subprocess.run(argv, capture_output=True, text=True)
    assert r.returncode == 0, f"{argv} exited {r.returncode}\nstderr:\n{r.stderr}"
    return r


pytestmark = pytest.mark.slow


@pytest.mark.skipif(not _have("uv"), reason="uv required to build/install wheel")
# Quarantined: `uv build` intermittently exits 2 under concurrent uv load.
@pytest.mark.flaky(reruns=2, issue="https://github.com/Raven-Scout/Scout/issues/309")
def test_wheel_install_runs_scoutctl_and_loads_defaults(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    venv_dir = tmp_path / "venv"

    # 1. Build the wheel.
    _run(["uv", "build", "--wheel", str(ENGINE_DIR), "-o", str(dist_dir)])
    wheels = list(dist_dir.glob("scout_engine-*.whl"))
    assert wheels, f"no wheel produced in {dist_dir}"
    wheel = wheels[0]

    # 2. Create a fresh venv (so the test cannot accidentally pick up
    #    the editable install on the dev's PATH).
    _run(["uv", "venv", str(venv_dir)])
    venv_python = venv_dir / "bin" / "python"
    venv_scoutctl = venv_dir / "bin" / "scoutctl"

    # 3. Install the built wheel (and only the wheel) into the venv.
    _run(["uv", "pip", "install", "--python", str(venv_python), str(wheel)])

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
