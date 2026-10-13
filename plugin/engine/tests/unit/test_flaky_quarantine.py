"""The @pytest.mark.flaky quarantine in tests/conftest.py, run as CI runs it.

CI runs the suite under pytest-xdist, where the rerun happens in a worker and
the summary prints in the controller. A quarantined rerun must still show up
there, or a flake that starts failing more often goes unseen.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[2]

_FLAKY_TEST = """
import pytest
from pathlib import Path

@pytest.mark.flaky(reruns=2, issue="https://github.com/example-org/repo/issues/1")
def test_fails_once_then_passes():
    marker = Path({marker!r})
    if not marker.exists():
        marker.write_text("failed once")
        assert False, "first attempt fails"
"""


def _run(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_quarantined.py").write_text(_FLAKY_TEST.format(marker=str(tmp_path / "attempted")))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(ENGINE / "pyproject.toml"), "--rootdir", str(tmp_path),
         "-p", "tests.conftest", "-p", "no:cacheprovider", *extra, str(tmp_path / "test_quarantined.py")],
        cwd=ENGINE, capture_output=True, text=True, timeout=120,
    )  # fmt: skip


@pytest.mark.parametrize("extra", [(), ("-n", "2")], ids=["serial", "xdist"])
def test_a_quarantined_rerun_is_listed_in_the_summary(tmp_path: Path, extra: tuple[str, ...]) -> None:
    r = _run(tmp_path, *extra)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 passed" in r.stdout
    assert "quarantined flaky reruns" in r.stdout
    assert "test_fails_once_then_passes attempt 1/3: AssertionError: first attempt fails" in r.stdout
    assert "issues/1" in r.stdout
