from __future__ import annotations

import json
from datetime import datetime

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.scripts import self_update_run as sur
from tests.unit.self_update_fakes import make_deps, pointer

runner = CliRunner()


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Route the CLI's default_deps to fakes rooted at SCOUT_DATA_DIR."""
    holder = {}

    def fake_default_deps(vault, *, home=None, log_name=None, quiet=False):
        deps, world, notified = make_deps(tmp_path, **holder.get("kw", {}))
        assert vault == deps.vault
        deps.log_name = log_name
        holder.update(deps=deps, world=world, notified=notified, quiet=quiet)
        return deps

    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "vault"))
    monkeypatch.setattr(sur, "default_deps", fake_default_deps)
    return holder


def _marker(tmp_path):
    return tmp_path / "vault" / ".scout-cache" / f"self-update-{datetime.now():%Y-%m-%d}.done"


def test_scheduled_applied_writes_the_marker_and_json(tmp_path, wired):
    result = runner.invoke(app, ["self-update", "run", "--scheduled", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["row"]["result"] == "applied" and payload["settled"] is True
    assert payload["row"]["log"] == f"self-update-{datetime.now():%Y-%m-%d}.log"
    assert wired["quiet"] is True
    assert _marker(tmp_path).exists()


def test_waiting_release_writes_the_marker(tmp_path, wired):
    wired["kw"] = {"release": None}
    assert runner.invoke(app, ["self-update", "run", "--scheduled"]).exit_code == 0
    assert _marker(tmp_path).exists()


def test_deferred_does_not_write_the_marker(tmp_path, wired):
    wired["kw"] = {"lock_busy": lambda: True}
    result = runner.invoke(app, ["self-update", "run", "--scheduled", "--json"])
    assert json.loads(result.output)["row"]["result"] == "deferred"
    assert not _marker(tmp_path).exists()


def test_manual_run_never_writes_the_marker(tmp_path, wired):
    assert runner.invoke(app, ["self-update", "run"]).exit_code == 0
    assert not _marker(tmp_path).exists()
    assert wired["deps"].log_name is None


def test_not_now_text(tmp_path, wired):
    wired["kw"] = {"in_off_peak": lambda: False}
    from scout import update_outcomes as uo
    from tests.unit.self_update_fakes import NOW

    (tmp_path / "vault" / ".scout-logs").mkdir(parents=True)
    uo.append(
        tmp_path / "vault",
        uo.UpdateOutcome(at=NOW, from_version="0.16.0", target="0.16.0", managed_by="install.sh",
                         result="up_to_date", step="check"),
    )
    result = runner.invoke(app, ["self-update", "run", "--scheduled"])
    assert result.exit_code == 0 and "not now (outside the off-peak window)" in result.output
    assert not _marker(tmp_path).exists()


def test_dry_run_flag_reaches_run_update(tmp_path, wired):
    result = runner.invoke(app, ["self-update", "run", "--scheduled", "--dry-run", "--force", "--json"])
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload == {"row": None, "reason": "dry run", "settled": False}
    assert wired["world"].calls == []
    assert not _marker(tmp_path).exists()


def test_scout_app_engine_via_cli(tmp_path, wired):
    wired["kw"] = {"ptr": pointer("scout-app")}
    result = runner.invoke(app, ["self-update", "run", "--scheduled", "--force", "--json"])
    row = json.loads(result.output)["row"]
    assert row["result"] == "not_eligible" and row["detail"] == "managed_by=scout-app"
    assert wired["world"].calls == []
