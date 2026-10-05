"""Shared pytest fixtures."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from _pytest.runner import runtestprotocol

# ---- quarantine: bounded reruns for known-flaky tests ------------------------
#
# pytest-rerunfailures isn't a dependency, so known flakes are retried here.
# ``@pytest.mark.flaky(reruns=2, issue="https://github.com/Raven-Scout/Scout/issues/N")``
# reruns a test whose *call* phase failed — a setup or teardown error is never
# retried — with fresh function-scoped fixtures, up to ``reruns`` more times.
# Only the last attempt is reported, so a test that fails every attempt still
# fails the run. Every earlier failure is listed under "quarantined flaky
# reruns" in the terminal summary with its issue, so a quarantined test that
# starts failing more often stays visible. Remove the marker when the issue is
# fixed; never add one without an issue.

_FLAKY_RERUNS = pytest.StashKey[list[str]]()


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_FLAKY_RERUNS] = []


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> bool | None:
    marker = item.get_closest_marker("flaky")
    if marker is None:
        return None
    issue = marker.kwargs.get("issue")
    if not issue:
        raise pytest.UsageError(f"{item.nodeid}: @pytest.mark.flaky needs issue=<tracking issue URL>")
    reruns = max(0, int(marker.kwargs.get("reruns", 2)))

    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    reports = runtestprotocol(item, nextitem=nextitem, log=False)
    for attempt in range(1, reruns + 1):
        call = next((r for r in reports if r.when == "call"), None)
        if call is None or not call.failed:
            break
        crash = getattr(call.longrepr, "reprcrash", None)
        why = crash.message.splitlines()[0] if crash and crash.message else "failed"
        item.config.stash[_FLAKY_RERUNS].append(f"{item.nodeid} attempt {attempt}/{reruns + 1}: {why} ({issue})")
        reports = runtestprotocol(item, nextitem=nextitem, log=False)
    for report in reports:
        item.ihook.pytest_runtest_logreport(report=report)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter, config: pytest.Config) -> None:
    reruns = config.stash.get(_FLAKY_RERUNS, [])
    if reruns:
        terminalreporter.section("quarantined flaky reruns", yellow=True)
        for line in reruns:
            terminalreporter.line(f"RERUN {line}")


@pytest.fixture(autouse=True)
def _hermetic_env(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the developer's live vault.

    paths.data_dir() falls back to Path.home()/Scout when SCOUT_DATA_DIR is
    unset, so a live ~/Scout makes non-fake_data_dir tests read real user
    data (and fail — the schedule CLI tests picked up the live overlay's
    extra slot). Point HOME at an empty per-test tmp dir and scrub SCOUT_*
    vars. Tests that need a data dir keep using fake_data_dir, which sets
    SCOUT_DATA_DIR after this fixture runs.

    The host's timezone is hidden the same way: SCOUT_LOCALTIME points host
    detection (scout.config.host_timezone_name, scripts/scout-tz.sh) at a link
    that does not exist, so an unconfigured vault resolves to the packaged
    default on every developer machine and CI host alike. Tests that exercise
    host detection point it at a fixture symlink of their own.
    """
    home = tmp_path_factory.mktemp("hermetic-home")
    monkeypatch.setenv("HOME", str(home))
    for key in list(os.environ):
        if key.startswith("SCOUT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SCOUT_LOCALTIME", str(home / "no-localtime"))


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
    """Unset any SCOUT_* env vars that might leak between tests (keeping
    _hermetic_env's SCOUT_LOCALTIME, which hides the host's timezone)."""
    for key in list(os.environ):
        if key.startswith("SCOUT_") and key != "SCOUT_LOCALTIME":
            monkeypatch.delenv(key, raising=False)


# ---- kb lint: throwaway git vault --------------------------------------------


@dataclass
class KbRepo:
    root: Path

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def stage(self, rel: str, text: str) -> None:
        self.write(rel, text)
        self.git("add", "--", rel)

    def commit(self, msg: str = "c") -> None:
        self.git("commit", "-q", "--no-verify", "-m", msg)


@pytest.fixture
def kb_repo(tmp_path: Path) -> KbRepo:
    repo = KbRepo(tmp_path / "vault")
    (repo.root / "knowledge-base").mkdir(parents=True)
    (repo.root / "action-items").mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "t@example.com")
    repo.git("config", "user.name", "T")
    repo.git("config", "commit.gpgsign", "false")
    repo.git("config", "core.quotepath", "false")
    repo.stage("README.md", "vault\n")
    repo.commit("init")
    return repo
