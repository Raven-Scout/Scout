"""scoutctl setup's auto-update question and flag (run-health spec §7.9, #360)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from scout import setup_wizard as sw
from scout.cli import app
from scout.scripts.bootstrap_auto import AutoAction
from tests.unit.test_setup_wizard import Scripted, _deps, _opts

QUESTION = "Keep Scout up to date automatically?"
# instance, name, email, keep tz, toggle (enter), slack id, per-session, daily — then auto-update, first run
ANSWERS = ["", "", "", "", "", "U0123", "", "20"]


def _flag(argv: list[str]) -> str | None:
    found = [a for a in argv if a in ("--auto-update", "--no-auto-update")]
    assert len(found) <= 1
    return found[0] if found else None


@pytest.mark.parametrize(("answer", "flag"), [("", "--auto-update"), ("y", "--auto-update"), ("n", "--no-auto-update")])
def test_install_asks_and_defaults_to_on(tmp_path, answer, flag):
    deps, calls = _deps(tmp_path)
    p = Scripted([*ANSWERS, answer, "n"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 0
    assert QUESTION in " ".join(p.said)
    assert _flag(calls[0]) == flag


@pytest.mark.parametrize("managed_by", ["install.sh", "claude-code"])
def test_asked_for_both_headless_managers(tmp_path, managed_by):
    deps, calls = _deps(tmp_path)
    p = Scripted([*ANSWERS, "", "n"])
    sw.run_setup(_opts(tmp_path, managed_by=managed_by), p, deps)
    assert QUESTION in " ".join(p.said) and _flag(calls[0]) == "--auto-update"


def test_never_asked_for_a_dev_engine(tmp_path):
    deps, calls = _deps(tmp_path)
    p = Scripted([*ANSWERS, "n"])
    sw.run_setup(_opts(tmp_path, managed_by="dev"), p, deps)
    assert QUESTION not in " ".join(p.said) and _flag(calls[0]) is None


@pytest.mark.parametrize(("value", "flag"), [(True, "--auto-update"), (False, "--no-auto-update")])
def test_an_explicit_flag_wins_without_asking(tmp_path, value, flag):
    deps, calls = _deps(tmp_path)
    p = Scripted([*ANSWERS, "n"])
    sw.run_setup(_opts(tmp_path, auto_update=value), p, deps)
    assert QUESTION not in " ".join(p.said) and _flag(calls[0]) == flag


def test_yes_turns_it_on(tmp_path):
    deps, calls = _deps(tmp_path)
    opts = _opts(tmp_path, yes=True, name="Alex", email="alex@example.com", first_run=False)
    assert sw.run_setup(opts, sw.HeadlessPrompter(), deps) == 0
    assert _flag(calls[0]) == "--auto-update"


def test_upgrade_passes_only_an_explicit_flag(tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    sw.run_setup(_opts(tmp_path), Scripted([]), deps)
    assert _flag(calls[0]) is None
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    sw.run_setup(_opts(tmp_path, auto_update=False), Scripted([]), deps)
    assert _flag(calls[0]) == "--no-auto-update"


def test_cli_flag_reaches_setup_options(monkeypatch, tmp_path):
    deps, calls = _deps(tmp_path)
    monkeypatch.setattr("scout.setup_wizard.default_deps", lambda claude_bin="": deps)
    argv = ["setup", "--vault", str(tmp_path / "V"), "--yes", "--name", "Alex", "--email", "alex@example.com"]
    result = CliRunner().invoke(app, [*argv, "--no-first-run", "--no-auto-update"])
    assert result.exit_code == 0, result.output
    assert _flag(calls[0]) == "--no-auto-update"
