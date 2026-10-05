"""custom_connector_ops: the JSON + exit-code contract behind `scoutctl connectors custom`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout import custom_connectors as cc
from scout.scripts import custom_connector_ops as ops
from scout.scripts.bootstrap import BootstrapConfig, install

PLUGIN = Path(__file__).parent.parent.parent.parent
SUITE = {
    "key": "suite_mail",
    "display_name": "Mail suite",
    "server": "example_suite",
    "probe": "mcp__example_suite__list_folders",
    "preset": "mail",
    "inbound": {"tools": ["mcp__example_suite__search_messages"]},
}
TICKETS = {
    "key": "tickets",
    "display_name": "Tickets",
    "probe": {"bash": "tix whoami"},
    "needs_user_input": ["team"],
    "inbound": {"tools": [{"bash": "tix list --team {{INPUT_TEAM}}"}], "focus": "Changed tickets."},
}


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    install(
        BootstrapConfig(
            vault=v,
            plugin_root=PLUGIN,
            instance_name="TestScout",
            instance_name_lower="testscout",
            user_name="Alex",
            user_email="alex@example.com",
            timezone="America/New_York",
            platform="macos",
            plugin_version="0.0.0",
            enabled_connectors={"slack"},
            connector_inputs={},
            skip_jobs=True,
            skip_claude=True,
        )
    )
    return v


def _add(vault: Path, definition: dict, **kw):
    kw.setdefault("inputs", {})
    return ops.add(vault, dict(definition), plugin_root=PLUGIN, plugin_version="0.0.0", **kw)


def _config(vault: Path) -> dict:
    return yaml.safe_load((vault / "scout-config.yaml").read_text())


def test_add_applies_and_records_everything(vault: Path):
    out = _add(vault, SUITE)
    assert (out.status, out.exit_code) == ("applied", 0)
    assert "## Mail suite Inbound Scan" in (vault / "SKILL.md").read_text()
    assert "suite_mail" in _config(vault)["connectors"]["enabled"]
    saved = yaml.safe_load((vault / cc.CUSTOM_FILE).read_text())["connectors"]["suite_mail"]
    assert "key" not in saved and saved["display_name"] == "Mail suite"


def test_re_add_replaces_the_entry_in_place(vault: Path):
    _add(vault, SUITE)
    changed = {**SUITE, "inbound": {"tools": SUITE["inbound"]["tools"], "focus": "Only the shared queue."}}
    out = _add(vault, changed)
    assert out.status == "applied"
    skill = (vault / "SKILL.md").read_text()
    assert skill.count("## Mail suite Inbound Scan") == 1
    assert "Only the shared queue." in skill
    assert list(yaml.safe_load((vault / cc.CUSTOM_FILE).read_text())["connectors"]) == ["suite_mail"]


def test_invalid_definition_exits_2_and_writes_nothing(vault: Path):
    out = _add(vault, {**SUITE, "server": "other_suite"})
    assert (out.status, out.exit_code) == ("invalid", 2)
    assert out.to_json()["issues"][0]["path"] == "connectors.suite_mail.server"
    assert not (vault / cc.CUSTOM_FILE).exists()


def test_missing_key_is_invalid(vault: Path):
    out = _add(vault, {k: v for k, v in SUITE.items() if k != "key"})
    assert out.status == "invalid" and out.issues[0].path == "key"


def test_missing_input_is_invalid_and_inputs_are_namespaced(vault: Path):
    assert _add(vault, TICKETS, probe_runner=lambda cmd: 0).status == "invalid"
    out = _add(vault, TICKETS, inputs={"team": "ops"}, probe_runner=lambda cmd: 0)
    assert out.status == "applied"
    assert _config(vault)["connectors"]["inputs"]["tickets__team"] == "ops"
    assert "`tix list --team ops` — run with Bash" in (vault / "SKILL.md").read_text()


def test_unknown_input_name_is_invalid(vault: Path):
    out = _add(vault, TICKETS, inputs={"team": "ops", "colour": "x"}, probe_runner=lambda cmd: 0)
    assert out.status == "invalid"


def test_failed_bash_probe_exits_2_unless_unverified(vault: Path):
    failing = lambda cmd: 127  # noqa: E731
    out = _add(vault, TICKETS, inputs={"team": "ops"}, probe_runner=failing)
    assert (out.status, out.exit_code) == ("probe-failed", 2)
    assert "exited 127" in out.issues[0].message
    assert _add(vault, TICKETS, inputs={"team": "ops"}, probe_runner=failing, unverified=True).status == "applied"


def test_dry_run_returns_sections_and_writes_nothing(vault: Path):
    skill_before = (vault / "SKILL.md").read_text()
    out = _add(vault, SUITE, dry_run=True)
    assert (out.status, out.exit_code) == ("dry-run", 0)
    assert out.sections[0]["target"] == "SKILL.md"
    assert "## Mail suite Inbound Scan" in out.sections[0]["body"]
    assert (vault / "SKILL.md").read_text() == skill_before
    assert not (vault / cc.CUSTOM_FILE).exists()


def test_pending_sidecar_defers_add_but_saves_it(vault: Path):
    (vault / "SKILL.md.proposed-merge").write_text("pending")
    out = _add(vault, SUITE)
    assert (out.status, out.exit_code, out.waiting) == ("deferred", 3, ["SKILL.md"])
    assert "SKILL.md" in out.message
    assert "suite_mail" in yaml.safe_load((vault / cc.CUSTOM_FILE).read_text())["connectors"]
    assert "suite_mail" in _config(vault)["connectors"]["enabled"]
    assert (vault / "SKILL.md.proposed-merge").read_text() == "pending"


def test_remove_takes_out_sections_entry_enabled_and_inputs(vault: Path):
    _add(vault, TICKETS, inputs={"team": "ops"}, probe_runner=lambda cmd: 0)
    out = ops.remove(vault, "tickets", plugin_root=PLUGIN, plugin_version="0.0.0")
    assert (out.status, out.exit_code) == ("applied", 0)
    assert "Tickets Inbound Scan" not in (vault / "SKILL.md").read_text()
    cfg = _config(vault)["connectors"]
    assert "tickets" not in cfg["enabled"] and "tickets__team" not in cfg["inputs"]
    assert yaml.safe_load((vault / cc.CUSTOM_FILE).read_text())["connectors"] == {}


def test_remove_unknown_key_is_invalid(vault: Path):
    assert ops.remove(vault, "nope", plugin_root=PLUGIN, plugin_version="0.0.0").exit_code == 2


def test_add_without_vault_is_an_error(tmp_path: Path):
    out = ops.add(tmp_path / "missing", dict(SUITE), plugin_root=PLUGIN, plugin_version="0.0.0", inputs={})
    assert (out.status, out.exit_code) == ("error", 1)


def test_list_custom_reports_definitions_and_issues(vault: Path):
    _add(vault, SUITE)
    raw = yaml.safe_load((vault / cc.CUSTOM_FILE).read_text())["connectors"]
    raw["broken"] = {"display_name": "B"}
    cc.write(vault, raw)
    listing = ops.list_custom(vault, plugin_root=PLUGIN)
    row = listing["connectors"][0]
    assert row == {
        "key": "suite_mail",
        "display_name": "Mail suite",
        "enabled": True,
        "server": "example_suite",
        "health_key": "mcp:example_suite",
        "preset": "mail",
        "activities": ["inbound"],
    }
    assert any(i["path"].startswith("connectors.broken") for i in listing["issues"])


def test_presets_json_lists_shipped_presets():
    assert set(ops.presets_json(PLUGIN)["presets"]) == {"mail", "chat", "calendar"}


def test_add_reports_error_when_scout_config_is_unreadable(vault: Path):
    """Controller ruling: a malformed scout-config.yaml surfaces as an error, not a crash."""
    (vault / "scout-config.yaml").write_text("connectors: [unclosed\n")
    out = _add(vault, SUITE)
    assert out.status == "error"
    assert out.exit_code == 1


@pytest.mark.parametrize("op", ["add", "remove"])
def test_non_mapping_scout_config_is_an_error_for_add_and_remove(vault: Path, op: str):
    """Fix round 1: scout-config.yaml that is valid YAML but not a mapping (e.g. a
    bare list) must not crash config_from_vault's .get() calls with AttributeError —
    it's an "error" Outcome like any other unreadable scout-config.yaml."""
    if op == "remove":
        _add(vault, TICKETS, inputs={"team": "ops"}, probe_runner=lambda cmd: 0)
    (vault / "scout-config.yaml").write_text("- a\n- b\n")
    if op == "add":
        out = _add(vault, SUITE)
    else:
        out = ops.remove(vault, "tickets", plugin_root=PLUGIN, plugin_version="0.0.0")
    assert (out.status, out.exit_code) == ("error", 1)


def _mcp_def(key: str) -> dict:
    return {
        "key": key,
        "display_name": key.capitalize(),
        "server": f"example_{key}",
        "probe": f"mcp__example_{key}__whoami",
        "inbound": {"tools": [f"mcp__example_{key}__search"], "focus": f"New {key} items."},
    }


def _assert_all_recorded(vault: Path, keys: set[str]) -> None:
    assert keys <= set(yaml.safe_load((vault / cc.CUSTOM_FILE).read_text())["connectors"])
    assert keys <= set(_config(vault)["connectors"]["enabled"])
    skill = (vault / "SKILL.md").read_text()
    for key in keys:
        assert f"## {key.capitalize()} Inbound Scan" in skill


def test_add_racing_another_add_keeps_both(vault: Path, monkeypatch: pytest.MonkeyPatch):
    """F2: vault state read before taking the lock let a concurrent add erase the other.

    The first lock acquisition by `add bravo` is delayed until a whole `add gamma`
    has committed, so bravo must read the vault only once it holds the lock.
    """
    assert _add(vault, _mcp_def("alpha")).status == "applied"
    real_acquire = ops.acquire_lock_with_wait
    calls = {"n": 0}

    def acquire_after_a_rival_commits(lock: Path, **kw) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            assert _add(vault, _mcp_def("gamma")).status == "applied"
        real_acquire(lock, **kw)

    monkeypatch.setattr(ops, "acquire_lock_with_wait", acquire_after_a_rival_commits)
    out = _add(vault, _mcp_def("bravo"))
    assert (out.status, out.exit_code) == ("applied", 0), out.message
    _assert_all_recorded(vault, {"alpha", "bravo", "gamma"})


def test_remove_racing_an_add_keeps_the_add(vault: Path, monkeypatch: pytest.MonkeyPatch):
    """F2 for remove: an add committed while remove waits for the lock must survive it."""
    for key in ("alpha", "bravo"):
        assert _add(vault, _mcp_def(key)).status == "applied"
    real_acquire = ops.acquire_lock_with_wait
    calls = {"n": 0}

    def acquire_after_a_rival_commits(lock: Path, **kw) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            assert _add(vault, _mcp_def("gamma")).status == "applied"
        real_acquire(lock, **kw)

    monkeypatch.setattr(ops, "acquire_lock_with_wait", acquire_after_a_rival_commits)
    out = ops.remove(vault, "alpha", plugin_root=PLUGIN, plugin_version="0.0.0")
    assert (out.status, out.exit_code) == ("applied", 0), out.message
    _assert_all_recorded(vault, {"bravo", "gamma"})
    assert "alpha" not in yaml.safe_load((vault / cc.CUSTOM_FILE).read_text())["connectors"]


def test_dry_run_never_takes_the_lock(vault: Path, monkeypatch: pytest.MonkeyPatch):
    def no_lock(*a, **kw):
        raise AssertionError("dry-run took the vault lock")

    monkeypatch.setattr(ops, "acquire_lock_with_wait", no_lock)
    assert _add(vault, SUITE, dry_run=True).status == "dry-run"
    assert not (vault / cc.CUSTOM_FILE).exists()


def test_list_custom_degrades_on_non_mapping_scout_config(vault: Path):
    """list_custom falls back to an empty enabled set and reports the config problem
    as an issue, rather than crashing (fix round 1)."""
    _add(vault, SUITE)
    (vault / "scout-config.yaml").write_text("- a\n- b\n")
    listing = ops.list_custom(vault, plugin_root=PLUGIN)
    assert listing["connectors"][0]["enabled"] is False
    assert any(i["path"] == "scout-config.yaml" for i in listing["issues"])
