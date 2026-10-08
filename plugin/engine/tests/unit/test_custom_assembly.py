"""custom_assembly: rendering custom-connector sections from the shipped templates."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout import custom_connectors as cc
from scout.scripts.custom_assembly import render_custom_sections
from scout.scripts.phase_assembly import parse_phase_file

ROOT = cc.default_plugin_root()
VARS = {"USER_NAME": "Alex", "INSTANCE_NAME": "Scout"}


def _connector(key: str, **body: object) -> cc.CustomConnector:
    presets = cc.load_presets(ROOT)
    c, issues = cc.parse_connector(key, body, reserved=set(), presets=presets)
    assert c is not None, issues
    return c


SUITE = _connector(
    "suite_mail",
    display_name="Mail suite",
    server="example_suite",
    probe="mcp__example_suite__list_folders",
    preset="mail",
    inbound={"tools": ["mcp__example_suite__search_messages"]},
    outbound={"tools": ["mcp__example_suite__search_messages"]},
    notes="The shared support mailbox is noise.",
)
DATAPLAT = _connector(
    "dataplat",
    display_name="Data platform",
    server="dataplat",
    probe="mcp__dataplat__info",
    lookup={"tools": ["mcp__dataplat__search"], "when": "A meeting mentions a table owned by {{USER_NAME}}."},
    needs_user_input=["project_id"],
    inbound={"tools": [{"bash": "dp jobs --project {{INPUT_PROJECT_ID}}"}], "focus": "Failed jobs."},
)
CONNECTORS = {"suite_mail": SUITE, "dataplat": DATAPLAT}


def test_templates_are_single_section_without_requires():
    for activity in cc.ACTIVITIES:
        sections = parse_phase_file(ROOT / "phases" / "custom" / f"{activity}.md")
        assert len(sections) == 1
        assert sections[0].requires is None


def test_skill_renders_inbound_and_outbound_with_preset_and_notes():
    out = render_custom_sections(ROOT, "SKILL", CONNECTORS, {"suite_mail"}, VARS, {})
    assert [(s.connector_key, s.activity) for s in out] == [("suite_mail", "inbound"), ("suite_mail", "outbound")]
    inbound = out[0].rendered_body
    assert "## Mail suite Inbound Scan" in inbound
    assert "`mcp__example_suite__search_messages` — call as an MCP tool" in inbound
    assert "cold outreach" in inbound  # preset text
    assert "for Alex" in inbound  # {{USER_NAME}} rendered, including inside preset text
    assert "The shared support mailbox is noise." in inbound
    assert "{{" not in inbound


def test_disabled_connector_renders_nothing():
    assert render_custom_sections(ROOT, "SKILL", CONNECTORS, set(), VARS, {}) == []


def test_an_unusable_template_is_skipped_with_a_warning(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    custom = tmp_path / "phases" / "custom"
    custom.mkdir(parents=True)
    shipped = (ROOT / "phases" / "custom" / "inbound.md").read_text(encoding="utf-8")
    (custom / "inbound.md").write_text(shipped, encoding="utf-8")
    # outbound.md is missing entirely; lookup.md holds two sections instead of one.
    (custom / "lookup.md").write_text(shipped + "\n" + shipped, encoding="utf-8")

    out = render_custom_sections(tmp_path, "SKILL", CONNECTORS, {"suite_mail", "dataplat"}, VARS, {})
    # Only inbound still renders; suite_mail's outbound and dataplat's lookup are dropped.
    assert [(s.connector_key, s.activity) for s in out] == [("dataplat", "inbound"), ("suite_mail", "inbound")]
    err = capsys.readouterr().err
    assert f"warning: custom-connector template {custom / 'outbound.md'} unusable: " in err
    assert f"warning: custom-connector template {custom / 'lookup.md'} must hold exactly one section" in err


def test_lookup_lands_in_skill_and_research_but_outbound_never_in_research():
    enabled = {"suite_mail", "dataplat"}
    research = render_custom_sections(ROOT, "RESEARCH", CONNECTORS, enabled, VARS, {})
    assert [(s.connector_key, s.activity) for s in research] == [("dataplat", "lookup")]
    skill = render_custom_sections(ROOT, "SKILL", CONNECTORS, enabled, VARS, {})
    assert ("dataplat", "lookup") in [(s.connector_key, s.activity) for s in skill]
    assert render_custom_sections(ROOT, "DREAMING", CONNECTORS, enabled, VARS, {}) == []


def test_sections_are_ordered_by_key_then_activity():
    skill = render_custom_sections(ROOT, "SKILL", CONNECTORS, {"suite_mail", "dataplat"}, VARS, {})
    assert [(s.connector_key, s.activity) for s in skill] == [
        ("dataplat", "inbound"),
        ("dataplat", "lookup"),
        ("suite_mail", "inbound"),
        ("suite_mail", "outbound"),
    ]


def test_inputs_are_namespaced_per_connector_and_bash_tools_render_as_commands():
    skill = render_custom_sections(ROOT, "SKILL", CONNECTORS, {"dataplat"}, VARS, {"dataplat__project_id": "p-42"})
    inbound = skill[0].rendered_body
    assert "`dp jobs --project p-42` — run with Bash" in inbound
    assert "{{INPUT_PROJECT_ID}}" in skill[0].raw_body  # raw keeps placeholders for backport


def test_raw_body_keeps_template_vars_and_rendered_has_none():
    out = render_custom_sections(ROOT, "SKILL", CONNECTORS, {"suite_mail"}, VARS, {})
    assert "{{USER_NAME}}" in out[0].raw_body
    assert "{{CONNECTOR_" not in out[0].raw_body
