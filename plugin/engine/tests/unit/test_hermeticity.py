"""Canary: the suite must never see the developer's real HOME or SCOUT_* env.

paths.data_dir() falls back to Path.home()/Scout when SCOUT_DATA_DIR is
unset, so without isolation any test that exercises default path resolution
reads the developer's live vault (e.g. the schedule CLI tests picked up the
live schedule.yaml overlay and failed on slot count). The autouse
_hermetic_env fixture in conftest.py points HOME at a pytest tmp dir and
scrubs SCOUT_* vars; these tests fail loudly if that ever regresses.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import scout.sessions.github as gh


def test_home_is_isolated() -> None:
    # tmp_path_factory dirs always contain a "pytest-<N>" path segment.
    assert "pytest-" in str(Path.home()), f"Path.home() leaked the real home: {Path.home()}"


def test_no_scout_env_leaks() -> None:
    leaked = sorted(k for k in os.environ if k.startswith("SCOUT_"))
    assert leaked == [], f"SCOUT_* env vars leaked into the test env: {leaked}"


def test_real_gh_is_blocked(fake_data_dir: Path) -> None:
    """Spec §7: the autouse `_block_real_gh` fixture keeps every test off the real `gh`."""
    assert gh.gh_available() is False, "the autouse gh guard in conftest.py is not active"
    with pytest.raises(AssertionError, match="real gh called from a test"):
        gh.default_runner(["--version"])
    from scout.sessions.index import default_options

    opts = default_options(fake_data_dir)  # BuildOptions late-binds its gh defaults to the guarded ones
    assert opts.gh_available() is False
    with pytest.raises(AssertionError, match="real gh called from a test"):
        opts.gh_runner(["--version"])


def test_a_test_can_still_inject_its_own_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test's own monkeypatch runs after the autouse guard, so it wins."""
    monkeypatch.setattr(gh, "gh_available", lambda: True)
    monkeypatch.setattr(gh, "default_runner", lambda argv: "canned")
    assert gh.gh_available() is True and gh.default_runner(["pr", "view", "1"]) == "canned"
