"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _hermetic_env(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the developer's live vault.

    paths.data_dir() falls back to Path.home()/Scout when SCOUT_DATA_DIR is
    unset, so a live ~/Scout makes non-fake_data_dir tests read real user
    data (and fail — the schedule CLI tests picked up the live overlay's
    extra slot). Point HOME at an empty per-test tmp dir and scrub SCOUT_*
    vars. Tests that need a data dir keep using fake_data_dir, which sets
    SCOUT_DATA_DIR after this fixture runs.
    """
    home = tmp_path_factory.mktemp("hermetic-home")
    monkeypatch.setenv("HOME", str(home))
    for key in list(os.environ):
        if key.startswith("SCOUT_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _block_real_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may reach the real ``gh`` (Agent Sessions spec §7).

    ``gh`` reads the developer's auth and hits the network. Every test sees gh as
    absent, and the default runner fails loudly if something calls it anyway. Tests
    that need gh behaviour inject a fake through ``BuildOptions`` or their own
    monkeypatch, which runs after this fixture and so wins.
    """
    import scout.sessions.github as gh

    def _real_gh_blocked(argv: list[str]) -> str | None:
        raise AssertionError("real gh called from a test")

    monkeypatch.setattr(gh, "gh_available", lambda: False)
    monkeypatch.setattr(gh, "default_runner", _real_gh_blocked)


@pytest.fixture
def fake_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A writable tmp data dir wired up via SCOUT_DATA_DIR."""
    d = tmp_path / "Scout"
    d.mkdir()
    (d / ".scout-logs").mkdir()
    (d / ".scout-cache").mkdir()
    (d / ".scout-state").mkdir()
    (d / "knowledge-base").mkdir()
    (d / "action-items").mkdir()
    monkeypatch.setenv("SCOUT_DATA_DIR", str(d))
    yield d


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset any SCOUT_* env vars that might leak between tests."""
    for key in list(os.environ):
        if key.startswith("SCOUT_"):
            monkeypatch.delenv(key, raising=False)
