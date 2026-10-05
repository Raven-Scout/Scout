"""Custom connectors flow into assembled brain files, backport, and doctor."""

from __future__ import annotations

from pathlib import Path

from scout import custom_connectors as cc
from scout.scripts.bootstrap import BootstrapConfig, _assemble, _template_vars, install
from scout.scripts.bootstrap_doctor import run_doctor
from scout.scripts.phase_backport import build_rendered_sections, plan_backport

PLUGIN = Path(__file__).parent.parent.parent.parent

SUITE = {
    "display_name": "Mail suite",
    "server": "example_suite",
    "probe": "mcp__example_suite__list_folders",
    "preset": "mail",
    "inbound": {"tools": ["mcp__example_suite__search_messages"]},
}


def _cfg(vault: Path, enabled: set[str]) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=PLUGIN,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=enabled,
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


def test_enabled_custom_connector_is_appended_after_shipped_sections(tmp_path: Path):
    cc.write(tmp_path, {"suite_mail": SUITE})
    skill = _assemble(_cfg(tmp_path, {"slack", "suite_mail"}), "SKILL")
    assert "## Mail suite Inbound Scan" in skill
    assert skill.index("## Slack Inbound Scan") < skill.index("## Mail suite Inbound Scan")


def test_custom_connector_not_in_enabled_is_not_rendered(tmp_path: Path):
    cc.write(tmp_path, {"suite_mail": SUITE})
    assert "Mail suite" not in _assemble(_cfg(tmp_path, {"slack"}), "SKILL")


def test_broken_custom_file_does_not_break_assembly(tmp_path: Path, capsys):
    (tmp_path / cc.CUSTOM_FILE).write_text("connectors: [unclosed\n")
    skill = _assemble(_cfg(tmp_path, {"slack"}), "SKILL")
    assert "## Slack Inbound Scan" in skill
    assert cc.CUSTOM_FILE in capsys.readouterr().err


def test_broken_entry_is_skipped_and_valid_one_still_renders(tmp_path: Path):
    cc.write(tmp_path, {"suite_mail": SUITE, "broken": {"display_name": "B"}})
    skill = _assemble(_cfg(tmp_path, {"suite_mail", "broken"}), "SKILL")
    assert "## Mail suite Inbound Scan" in skill


def test_backport_renders_the_same_custom_sections_and_never_writes_into_them(tmp_path: Path):
    cc.write(tmp_path, {"suite_mail": SUITE})
    cfg = _cfg(tmp_path, {"suite_mail"})
    custom = cc.load(tmp_path, plugin_root=PLUGIN).connectors
    vars_ = _template_vars(cfg)
    sections = build_rendered_sections(
        PLUGIN / "phases", "SKILL", vars_, cfg.enabled_connectors, custom=custom, inputs={}
    )
    custom_sections = [s for s in sections if s.custom_key == "suite_mail"]
    assert custom_sections and custom_sections[0].rendered_body in _assemble(cfg, "SKILL")

    snapshot = _assemble(cfg, "SKILL")
    anchor = (
        "Check Mail suite for anything new since the last run that may need Alex's action, "
        "or that changes what Alex knows about a project or a person."
    )
    assert anchor in snapshot
    live = snapshot.replace(anchor, anchor + "\n\nAlso skim the archive folder.")
    results = plan_backport(snapshot, live, sections, vars_)
    assert [r.status for r in results] == ["needs-review"]
    assert "connectors.custom.yaml" in results[0].reason


def test_doctor_warns_on_custom_file_issues(tmp_path: Path):
    vault = tmp_path / "Scout"
    install(_cfg(vault, set()))
    cc.write(vault, {"broken": {"display_name": "B"}})
    report = run_doctor(vault=vault, check_jobs=False)
    assert any(cc.CUSTOM_FILE in w and "connectors.broken" in w for w in report.warnings)
