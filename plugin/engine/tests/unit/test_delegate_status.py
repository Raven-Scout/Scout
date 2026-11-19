"""`scoutctl delegate status --json` (#550): shape checks on a state a run can write, and what it never shows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import delegate_mirror as dm
from scout import delegate_status as ds
from scout.cli import app

from .privacy_vault_helpers import git

runner = CliRunner()

CONFIG = "privacy:\n  delegates:\n    grok-personal: {}\n    duet: {}\n"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    v.mkdir()
    (v / "scout-config.yaml").write_text(CONFIG, encoding="utf-8")
    git(v, "init", "-q", "-b", "main")
    git(v, "add", "-A")
    git(v, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "config")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def _link(**kw: object) -> dm.DelegateLink:
    base: dict[str, object] = {
        "id": "grok-personal",
        "remote": "https://github.com/example-org/grok-mirror.git",
        "linked_at": "2026-11-10T09:00:00Z",
        "account_unshared": True,
        "last_sync": "2026-11-19T11:45:00Z",
    }
    return dm.DelegateLink(**{**base, **kw})  # type: ignore[arg-type]


def _raw_state(vault: Path, links: dict[str, object]) -> None:
    path = dm.state_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "links": links, "mac": "0" * 64}), encoding="utf-8")


def _owner() -> dict[str, str]:
    import os

    return {"HOME": os.environ["HOME"]}


def test_no_state_lists_the_configured_delegates_as_unlinked(vault: Path) -> None:
    doc = ds.report(vault, env=_owner())
    assert doc["state"] == "none"
    assert [(d["id"], d["linked"], d["can_preview"], d["can_sync"]) for d in doc["delegates"]] == [
        ("grok-personal", False, True, False),
        ("duet", False, True, False),
    ]


def test_an_unsigned_state_is_listed_flagged_and_never_offers_a_sync(vault: Path) -> None:
    dm.save_links(vault, {"grok-personal": _link()})
    text = dm.state_path(vault).read_text(encoding="utf-8")
    dm.state_path(vault).write_text(text.replace("grok-mirror", "other-mirror"), encoding="utf-8")  # breaks the mac
    doc = ds.report(vault, env=_owner())
    assert doc["state"] == "unverified"
    grok = doc["delegates"][0]
    assert grok["state_unverified"] is True and grok["behind"] is None
    assert grok["remote"] == {"kind": "github", "display": "example-org/other-mirror"}  # shown, for the owner to check
    assert grok["can_sync"] is False and grok["can_preview"] is True


def test_an_unreadable_state_lists_no_link(vault: Path) -> None:
    dm.state_path(vault).parent.mkdir(parents=True, exist_ok=True)
    dm.state_path(vault).write_text("{not json", encoding="utf-8")
    doc = ds.report(vault, env=_owner())
    assert doc["state"] == "unreadable"
    assert {d["linked"] for d in doc["delegates"]} == {False}


def test_every_field_is_shape_checked(vault: Path) -> None:
    _raw_state(
        vault,
        {
            "grok-personal": {
                "id": "grok-personal",
                "remote": "https://github.com/example-org/m.git\n[[sam]]",
                "linked_at": "yesterday",
                "last_sync": 5,
                "last_error": {"code": "Sam's notes/[[sam]]", "at": "2026-11-19T11:00:00Z"},
                "deploy_key_id": "101",
                "stale_key_ids": ["x"],
                "last_counts": {"files": -1, "withheld": 0, "left_out": 0, "redacted_lines": 0, "agent_config": 0},
                "pending_mirror": "not a sha",
            },
            "duet": {"id": "someone-else"},  # its id doesn't match its key
            "Bad Id": {"id": "Bad Id"},
            "x" * 41: {"id": "x" * 41},
        },
    )
    doc = ds.report(vault, env=_owner())
    assert doc["skipped"] == 3
    grok = next(d for d in doc["delegates"] if d["id"] == "grok-personal")
    assert grok["remote"] is None and grok["linked_at"] is None and grok["last_sync"] is None
    assert grok["refusal"] == {"code": "unknown", "reason": ds.GENERIC_REASON, "at": "2026-11-19T11:00:00Z",
                               "since": "2026-11-19T11:00:00Z"}  # fmt: skip
    assert grok["deploy_key"] is None and grok["old_keys_live"] is None and grok["counts"] is None
    assert grok["agent_config_left_out"] is None and grok["last_result"] == "failed"
    assert "sam" not in json.dumps(doc)
    duet = next(d for d in doc["delegates"] if d["id"] == "duet")
    assert duet["linked"] is False  # listed in the config; the bad entry under its key was dropped


def test_a_scheduled_run_gets_no_remote_and_no_action(vault: Path) -> None:
    dm.save_links(vault, {"grok-personal": _link()})
    doc = ds.report(vault, env={**_owner(), "SCOUT_MODE": "briefing"})
    assert doc["owner"] is False
    grok = doc["delegates"][0]
    assert grok["remote"] is None and grok["can_sync"] is False and grok["can_preview"] is False


def test_the_cli_prints_the_document_even_for_a_state_it_cannot_trust(vault: Path) -> None:
    _raw_state(vault, {"grok-personal": _link().to_json()})
    r = runner.invoke(app, ["delegate", "status", "--json"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout)
    assert doc["schema_version"] == 1 and doc["state"] == "unverified"
    r = runner.invoke(app, ["delegate", "status"])  # the text form still refuses it
    assert r.exit_code == 2 and "wasn't written by Scout" in r.output


def test_the_key_file_and_fingerprints_never_show(vault: Path) -> None:
    dm.save_links(
        vault,
        {"grok-personal": _link(key_file="/secret/place/key", checked_fingerprint="e" * 64, last_inputs="9" * 64)},
    )
    text = json.dumps(ds.report(vault, env=_owner()))
    assert "/secret/place" not in text and "e" * 64 not in text and "9" * 64 not in text
