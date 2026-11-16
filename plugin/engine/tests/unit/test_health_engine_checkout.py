"""Run health reports a dev engine checkout that scheduled runs would execute unreviewed.

A dev-managed engine is an editable install: whatever sits in its working tree runs in the
next scheduled run, and a checkout that is off main or a release tag can't be upgraded.
Scheduled runs once branched and edited that checkout directly, so every run's wrap now
surfaces it (phases/core/01-run-health.md), and so do `scoutctl health`, the doctor and the app.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from scout import health
from scout.engine_checkout import CheckoutState
from scout.scripts.engine_pointer import EnginePointer, write_pointer

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
ROOT = Path("/Users/alex/scout-engine")


def _pointer(home: Path, managed_by: str = "dev") -> None:
    write_pointer(
        EnginePointer(
            version="0.1.0",
            engine_root=str(ROOT / "plugin"),
            python=str(ROOT / "plugin" / ".venv" / "bin" / "python"),
            scoutctl=str(ROOT / "plugin" / ".venv" / "bin" / "scoutctl"),
            vault="/Users/alex/Scout",
            managed_by=managed_by,
            written_at="2026-10-20T00:00:00Z",
        ),
        home=home,
    )


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Stub the git probe; records the root it was asked about."""
    box: dict[str, object] = {"state": CheckoutState(root=ROOT, branch="main", release_tag=None, changes=())}

    def fake(path: Path) -> CheckoutState | None:
        box["asked"] = path
        return box["state"]  # type: ignore[return-value]

    monkeypatch.setattr(health, "checkout_state", fake)
    return box


def _ids(home: Path) -> dict[str, health.Finding]:
    return {f.id: f for f in health.engine_findings(home=home)}


def test_no_pointer_no_findings(tmp_path: Path, probe) -> None:
    assert _ids(tmp_path) == {}
    assert "asked" not in probe


@pytest.mark.parametrize("managed_by", ["scout-app", "install.sh", "claude-code", "unknown"])
def test_only_a_dev_managed_engine_is_checked(tmp_path: Path, probe, managed_by: str) -> None:
    _pointer(tmp_path, managed_by)
    probe["state"] = CheckoutState(root=ROOT, branch="wip", release_tag=None, changes=("x.py",))
    assert _ids(tmp_path) == {}


def test_a_clean_main_checkout_is_healthy(tmp_path: Path, probe) -> None:
    _pointer(tmp_path)
    assert _ids(tmp_path) == {}
    assert probe["asked"] == ROOT / "plugin"


def test_an_engine_root_that_is_no_checkout_is_not_reported(tmp_path: Path, probe) -> None:
    _pointer(tmp_path)
    probe["state"] = None
    assert _ids(tmp_path) == {}


def test_uncommitted_changes_are_a_yellow_finding(tmp_path: Path, probe) -> None:
    _pointer(tmp_path)
    probe["state"] = CheckoutState(
        root=ROOT, branch="main", release_tag=None, changes=("plugin/engine/x.py", "plugin/y.py", "z.py")
    )
    found = _ids(tmp_path)
    assert set(found) == {"engine.dirty"}
    f = found["engine.dirty"]
    assert f.severity == "yellow" and f.source == "git" and f.since is None
    assert str(ROOT) in f.summary
    assert "3 uncommitted change(s)" in f.summary and "plugin/engine/x.py" in f.summary
    assert "~/.scout-worktrees/" in f.fix


def test_one_change_is_named_without_an_e_g(tmp_path: Path, probe) -> None:
    _pointer(tmp_path)
    probe["state"] = CheckoutState(root=ROOT, branch="main", release_tag=None, changes=("plugin/a.py",))
    assert "1 uncommitted change(s) (plugin/a.py)" in _ids(tmp_path)["engine.dirty"].summary


def test_a_branch_other_than_main_is_a_yellow_finding(tmp_path: Path, probe) -> None:
    _pointer(tmp_path)
    probe["state"] = CheckoutState(root=ROOT, branch="fix/some-connector", release_tag=None, changes=())
    found = _ids(tmp_path)
    assert set(found) == {"engine.off_release"}
    f = found["engine.off_release"]
    assert f.severity == "yellow" and f.source == "git"
    assert "branch fix/some-connector" in f.summary and str(ROOT) in f.summary
    assert "switch main" in f.fix


def test_a_detached_head_off_any_release_is_reported_as_such(tmp_path: Path, probe) -> None:
    _pointer(tmp_path)
    probe["state"] = CheckoutState(root=ROOT, branch=None, release_tag=None, changes=())
    assert "a detached HEAD" in _ids(tmp_path)["engine.off_release"].summary


def test_dirty_and_off_release_are_both_reported(tmp_path: Path, probe) -> None:
    """The live state found on 2026-10-20: a branch at the release commit, with uncommitted edits."""
    _pointer(tmp_path)
    probe["state"] = CheckoutState(root=ROOT, branch="fix/x", release_tag=None, changes=("a.py",))
    assert set(_ids(tmp_path)) == {"engine.dirty", "engine.off_release"}


def test_evaluate_includes_the_engine_findings(tmp_path: Path, probe) -> None:
    vault = tmp_path / "Scout"
    (vault / ".scout-logs").mkdir(parents=True)
    _pointer(tmp_path)
    probe["state"] = CheckoutState(root=ROOT, branch="main", release_tag=None, changes=("a.py",))
    report = health.evaluate(vault, now=NOW, home=tmp_path)
    assert "engine.dirty" in {f.id for f in report.findings}


def test_evaluate_defaults_to_the_real_home(tmp_path: Path, probe, monkeypatch: pytest.MonkeyPatch) -> None:
    """`scoutctl health` passes no home: the engine pointer is read from $HOME (hermetic in tests)."""
    vault = tmp_path / "Scout"
    (vault / ".scout-logs").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    _pointer(tmp_path)
    probe["state"] = CheckoutState(root=ROOT, branch="wip", release_tag=None, changes=())
    report = health.evaluate(vault, now=NOW)
    assert "engine.off_release" in {f.id for f in report.findings}
