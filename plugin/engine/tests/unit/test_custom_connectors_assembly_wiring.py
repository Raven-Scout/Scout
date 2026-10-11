"""Custom connectors flow into assembled brain files, backport, and doctor."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from scout import custom_connectors as cc
from scout.connectors import Tier, load_registry
from scout.scripts.bootstrap import BootstrapConfig, _assemble, _template_vars, install
from scout.scripts.bootstrap_doctor import run_doctor
from scout.scripts.connector_probes import resolve_registry
from scout.scripts.phase_backport import build_rendered_sections, plan_backport

PLUGIN = Path(__file__).parent.parent.parent.parent

SUITE = {
    "display_name": "Mail suite",
    "server": "example_suite",
    "probe": "mcp__example_suite__list_folders",
    "preset": "mail",
    "inbound": {"tools": ["mcp__example_suite__search_messages"]},
}

LOOKUP = {
    "display_name": "Mail suite",
    "server": "example_suite",
    "probe": "mcp__example_suite__list_folders",
    "preset": "mail",
    "lookup": {"tools": ["mcp__example_suite__search_messages"]},
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
    # F6: name the shipped CLI, not the not-yet-shipped /scout-connect.
    assert "/scout-connect" not in results[0].reason
    assert "scoutctl connectors custom add" in results[0].reason


def test_doctor_warns_on_custom_file_issues(tmp_path: Path):
    vault = tmp_path / "Scout"
    install(_cfg(vault, set()))
    cc.write(vault, {"broken": {"display_name": "B"}})
    report = run_doctor(vault=vault, check_jobs=False)
    assert any(cc.CUSTOM_FILE in w and "connectors.broken" in w for w in report.warnings)


def test_one_malformed_entry_leaves_the_valid_one_working_everywhere(tmp_path: Path):
    """F1: `preset: [mail]` on one entry raised TypeError (unhashable) out of the
    parser and took down assembly, the roster, the probe registry and doctor."""
    vault = tmp_path / "Scout"
    # suite_mail must be in the persisted scout-config.yaml's connectors.enabled for
    # load_registry/resolve_registry (which read it from disk) to surface its row;
    # bad_one need not be — it never parses into a CustomConnector either way.
    install(_cfg(vault, {"suite_mail"}))
    cc.write(vault, {"bad_one": {**SUITE, "preset": ["mail"]}, "suite_mail": SUITE})

    assert "## Mail suite Inbound Scan" in _assemble(_cfg(vault, {"suite_mail", "bad_one"}), "SKILL")
    assert load_registry(data_dir=vault)["mcp:example_suite"].tier is Tier.CUSTOM
    probes = resolve_registry(plugin_root=PLUGIN, data_dir=vault)
    assert "suite_mail" in probes and "bad_one" not in probes
    report = run_doctor(vault=vault, check_jobs=False)
    assert any(cc.CUSTOM_FILE in w and "connectors.bad_one.preset" in w for w in report.warnings)


def test_custom_file_warnings_print_once_per_install(tmp_path: Path, capsys):
    """Install assembles three brain files; a broken connectors.custom.yaml must
    be loaded (and its issues warned on) once for the whole run, not once per
    brain kind — otherwise each real problem is reported three times over."""
    vault = tmp_path / "Scout"
    vault.mkdir()
    cc.write(vault, {"broken": {"display_name": "B"}})
    install(_cfg(vault, set()))
    warning_lines = [
        line for line in capsys.readouterr().err.splitlines() if line.startswith(f"warning: {cc.CUSTOM_FILE}:")
    ]
    assert warning_lines
    assert len(warning_lines) == len(set(warning_lines))


def test_research_places_custom_lookups_before_deep_research(tmp_path: Path):
    cc.write(tmp_path, {"suite_mail": LOOKUP})
    research = _assemble(_cfg(tmp_path, {"suite_mail"}), "RESEARCH")
    custom_at = research.index("## Mail suite Lookup")
    assert custom_at < research.index("# PHASE 2: DEEP RESEARCH")
    # commit-notify sorts first alphabetically, so it stays above the lookups.
    assert custom_at > research.index("# PHASE 4: COMMIT & NOTIFY")
    for later in ("# PHASE 3: KNOWLEDGE INTEGRATION", "# PHASE 1: SELECT RESEARCH TARGETS"):
        assert custom_at < research.index(later)


def test_research_with_no_enabled_custom_connector_is_unchanged(tmp_path: Path):
    cc.write(tmp_path, {"suite_mail": LOOKUP})
    cfg = _cfg(tmp_path, {"slack"})
    # suite_mail is not enabled, so nothing is inserted: byte-identical to no custom set at all.
    assert _assemble(cfg, "RESEARCH") == _assemble(cfg, "RESEARCH", custom={})


def test_skill_still_appends_custom_sections_last_and_dreaming_is_untouched(tmp_path: Path):
    cc.write(tmp_path, {"suite_mail": {**SUITE, **LOOKUP}})
    cfg = _cfg(tmp_path, {"suite_mail"})
    plain = _assemble(cfg, "SKILL", custom={})
    assert _assemble(cfg, "SKILL").startswith(plain + "\n\n")
    # Dreaming renders no custom sections, so it is byte-identical to the no-custom assembly.
    assert _assemble(cfg, "DREAMING") == _assemble(cfg, "DREAMING", custom={})


def test_research_without_a_deep_research_section_appends(tmp_path: Path):
    plugin = tmp_path / "plugin"
    shutil.copytree(PLUGIN / "phases", plugin / "phases")
    (plugin / "phases" / "research" / "deep-research.md").unlink()
    (tmp_path / "vault").mkdir()
    cc.write(tmp_path / "vault", {"suite_mail": LOOKUP})
    cfg = replace(_cfg(tmp_path / "vault", {"suite_mail"}), plugin_root=plugin)
    plain = _assemble(cfg, "RESEARCH", custom={})
    assert _assemble(cfg, "RESEARCH").startswith(plain + "\n\n")
