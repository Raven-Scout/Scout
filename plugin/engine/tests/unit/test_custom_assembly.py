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


TIX = _connector(
    "tix",
    display_name="Tickets",
    probe={"bash": "tix whoami"},
    needs_user_input=["team"],
    inbound={
        "tools": [{"bash": "# fetch open tickets\ntix list --open --team {{INPUT_TEAM}}"}, {"bash": "tix whoami"}],
        "focus": "Open tickets.",
    },
)


def _template_headings(kind: str = "inbound") -> list[str]:
    text = (ROOT / "phases" / "custom" / f"{kind}.md").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.startswith("#")]


def test_multi_line_bash_tool_renders_in_a_fenced_block_so_a_shell_comment_is_no_heading():
    """#344: a column-0 shell comment used to land in SKILL.md as a markdown H1."""
    body = render_custom_sections(ROOT, "SKILL", {"tix": TIX}, {"tix"}, VARS, {"tix__team": "ops"})[0].rendered_body
    assert "- run with Bash:\n  ```bash\n  # fetch open tickets\n  tix list --open --team ops\n  ```" in body
    # Single-line commands keep the 0.15.0 inline form, so their sections don't drift.
    assert "- `tix whoami` — run with Bash" in body
    headings = [line for line in body.splitlines() if line.startswith("#")]
    assert len(headings) == len(_template_headings())


def test_fence_outgrows_any_backtick_run_and_blank_lines_carry_no_indent():
    c = _connector(
        "tix",
        display_name="Tickets",
        probe={"bash": "tix whoami"},
        inbound={"tools": [{"bash": "tix list \\\n  --format '````'\n\ntix done"}], "focus": "Open tickets."},
    )
    body = render_custom_sections(ROOT, "SKILL", {"tix": c}, {"tix"}, VARS, {})[0].rendered_body
    assert "  `````bash\n  tix list \\\n    --format '````'\n\n  tix done\n  `````" in body


def test_a_stored_input_that_breaks_structure_skips_the_connector_with_a_warning(capsys: pytest.CaptureFixture[str]):
    """#344: scout-config.yaml connectors.inputs is hand-editable; a bad value never reaches SKILL.md."""
    enabled = {"tix", "suite_mail"}
    inputs = {"tix__team": "ops\n# Injected heading"}
    out = render_custom_sections(ROOT, "SKILL", {"tix": TIX, "suite_mail": SUITE}, enabled, VARS, inputs)
    assert [s.connector_key for s in out] == ["suite_mail", "suite_mail"]
    err = capsys.readouterr().err
    assert "warning: connectors.tix.needs_user_input: the value for 'team' stored in scout-config.yaml" in err
    assert "skipped until fixed" in err
    assert "Injected" not in err


def test_a_non_string_stored_input_renders_as_text():
    out = render_custom_sections(ROOT, "SKILL", CONNECTORS, {"dataplat"}, VARS, {"dataplat__project_id": 42})
    assert "`dp jobs --project 42` — run with Bash" in out[0].rendered_body


def test_raw_body_keeps_template_vars_and_rendered_has_none():
    out = render_custom_sections(ROOT, "SKILL", CONNECTORS, {"suite_mail"}, VARS, {})
    assert "{{USER_NAME}}" in out[0].raw_body
    assert "{{CONNECTOR_" not in out[0].raw_body
