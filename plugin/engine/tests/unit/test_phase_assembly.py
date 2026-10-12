"""Unit tests for engine/scout/scripts/phase_assembly.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.phase_assembly import (
    parse_phase_file,
    render_template,
    select_sections,
)

FIXTURES = Path(__file__).parent / "fixtures" / "phases"


def test_parse_single_section_file():
    sections = parse_phase_file(FIXTURES / "core" / "dummy-core.md")
    assert len(sections) == 1
    s = sections[0]
    assert s.phase == "core"
    assert s.name == "dummy-core"
    assert s.slot == "setup"
    assert s.mode == ["briefing"]
    assert s.requires is None
    assert "Hello {{USER_NAME}}" in s.body


def test_parse_multi_section_file():
    sections = parse_phase_file(FIXTURES / "connectors" / "dummy-slack.md")
    assert len(sections) == 2
    assert sections[0].slot == "query"
    assert sections[1].slot == "outbound-scan"
    assert sections[0].requires == "slack"


def test_select_filters_by_enabled_connectors():
    sections = parse_phase_file(FIXTURES / "connectors" / "dummy-slack.md")
    selected = select_sections(sections, enabled_connectors={"slack"})
    assert len(selected) == 2
    selected_disabled = select_sections(sections, enabled_connectors=set())
    assert selected_disabled == []


def test_select_keeps_requires_null():
    sections = parse_phase_file(FIXTURES / "core" / "dummy-core.md")
    selected = select_sections(sections, enabled_connectors=set())
    assert len(selected) == 1


def test_render_template_substitutes_variables():
    out = render_template(
        "Hello {{USER_NAME}} at {{SCOUT_DIR}}",
        {"USER_NAME": "Alice", "SCOUT_DIR": "/tmp/x"},
    )
    assert out == "Hello Alice at /tmp/x"


def test_render_template_empty_for_unknown_var():
    out = render_template("X {{UNKNOWN_VAR}} Y", {})
    assert out == "X  Y"


def test_select_filters_by_slot():
    sections = parse_phase_file(FIXTURES / "connectors" / "dummy-slack.md")
    selected = select_sections(
        sections,
        enabled_connectors={"slack"},
        slot="outbound-scan",
    )
    assert len(selected) == 1
    assert selected[0].slot == "outbound-scan"


def test_select_filters_mixed_connectors_within_file():
    """Two sections in the same file with different `requires` are filtered independently."""
    sections = parse_phase_file(FIXTURES / "connectors" / "dummy-mixed.md")
    assert len(sections) == 2
    only_slack = select_sections(sections, enabled_connectors={"slack"})
    assert len(only_slack) == 1
    assert only_slack[0].requires == "slack"
    only_email = select_sections(sections, enabled_connectors={"email"})
    assert len(only_email) == 1
    assert only_email[0].requires == "email"
    both = select_sections(sections, enabled_connectors={"slack", "email"})
    assert len(both) == 2


def test_select_filters_by_modes_excludes_non_intersecting(tmp_path):
    """Sections declaring `mode: [briefing]` are dropped when target is `dreaming`."""
    f = tmp_path / "p.md"
    f.write_text("---\nphase: core\nname: briefing-only\nslot: x\nmode: [briefing]\nrequires: null\n---\nBODY-B\n")
    sections = parse_phase_file(f)
    kept = select_sections(sections, enabled_connectors=set(), modes={"dreaming"})
    assert kept == []
    kept_briefing = select_sections(sections, enabled_connectors=set(), modes={"briefing"})
    assert len(kept_briefing) == 1


def test_select_filters_by_modes_keeps_intersecting(tmp_path):
    """A section declaring `mode: [briefing, consolidation]` is kept by either."""
    f = tmp_path / "p.md"
    f.write_text(
        "---\nphase: core\nname: shared\nslot: x\nmode: [briefing, consolidation]\nrequires: null\n---\nBODY\n"
    )
    sections = parse_phase_file(f)
    for target in ({"briefing"}, {"consolidation"}, {"briefing", "consolidation"}):
        kept = select_sections(sections, enabled_connectors=set(), modes=target)
        assert len(kept) == 1, target


def test_select_modes_none_disables_mode_filter(tmp_path):
    """Passing modes=None means every mode passes (back-compat)."""
    f = tmp_path / "p.md"
    f.write_text("---\nphase: core\nname: briefing-only\nslot: x\nmode: [briefing]\nrequires: null\n---\nBODY\n")
    sections = parse_phase_file(f)
    kept = select_sections(sections, enabled_connectors=set())  # modes default = None
    assert len(kept) == 1


def test_select_empty_mode_list_applies_to_every_target(tmp_path):
    """A section with no `mode:` (or `mode: []`) lands in every assembly target."""
    f = tmp_path / "p.md"
    f.write_text("---\nphase: core\nname: cross-cutting\nslot: x\nrequires: null\n---\nBODY\n")
    sections = parse_phase_file(f)
    for target in ({"briefing"}, {"dreaming"}, {"research"}):
        kept = select_sections(sections, enabled_connectors=set(), modes=target)
        assert len(kept) == 1, target


def test_parse_skips_trailing_fence_junk_section(tmp_path):
    """A file ending with '---' should not yield an empty junk section."""
    p = tmp_path / "trailing.md"
    p.write_text("---\nphase: core\nname: x\nslot: setup\nmode: []\nrequires: null\n---\n\nbody\n\n---\n")
    sections = parse_phase_file(p)
    assert len(sections) == 1
    assert sections[0].name == "x"


def test_parse_raises_on_missing_phase(tmp_path):
    """A section with empty 'phase' field is rejected."""
    p = tmp_path / "no_phase.md"
    p.write_text("---\nname: x\nslot: setup\nmode: []\nrequires: null\n---\n\nbody\n")
    with pytest.raises(ValueError, match="'phase' field is required"):
        parse_phase_file(p)


def test_parse_raises_on_string_mode(tmp_path):
    """`mode: briefing` (string) instead of `mode: [briefing]` (list) is rejected."""
    p = tmp_path / "bad_mode.md"
    p.write_text("---\nphase: core\nname: x\nslot: setup\nmode: briefing\nrequires: null\n---\n\nbody\n")
    with pytest.raises(ValueError, match="'mode' must be a YAML list"):
        parse_phase_file(p)


def test_parse_raises_on_list_requires(tmp_path):
    """`requires: [slack, gmail]` (list) instead of `requires: slack` (string) is rejected."""
    p = tmp_path / "bad_requires.md"
    p.write_text("---\nphase: connector\nname: x\nslot: setup\nmode: []\nrequires: [slack, gmail]\n---\n\nbody\n")
    with pytest.raises(ValueError, match="'requires'"):
        parse_phase_file(p)


def test_all_shipped_phase_files_parse():
    """Every bundled phase fragment must parse — a phase that fails parse_phase_file
    is silently dropped from assembly (regression guard for the bare-'---'-HR bug)."""
    from scout.scripts.phase_assembly import parse_phase_file

    phases_root = Path(__file__).parent.parent.parent.parent / "phases"
    failures = []
    for pf in sorted(phases_root.rglob("*.md")):
        try:
            sections = parse_phase_file(pf)
            assert sections, f"{pf} parsed to zero sections"
        except Exception as e:  # noqa: BLE001
            failures.append(f"{pf.relative_to(phases_root)}: {type(e).__name__}: {e}")
    assert not failures, "Unparseable phase files:\n" + "\n".join(failures)


def test_run_health_phase_parses_with_all_four_modes():
    phases_root = Path(__file__).parent.parent.parent.parent / "phases"
    (section,) = parse_phase_file(phases_root / "core" / "01-run-health.md")
    assert (section.phase, section.name, section.slot) == ("core", "run-health", "run-health")
    assert set(section.mode) == {"briefing", "consolidation", "dreaming", "research"}
    assert section.requires is None


def test_run_health_wrap_treats_the_cache_as_the_state_before_this_run():
    """The cache is written at session start, before this run's own ledger row: the wrap must not
    repeat what this run disproves (a working login, its own lane) and must date the rest."""
    phases_root = Path(__file__).parent.parent.parent.parent / "phases"
    (section,) = parse_phase_file(phases_root / "core" / "01-run-health.md")
    body = section.body
    assert "Never repeat `auth.expired`" in body
    assert "`runs.failing`" in body and "`lane.dark:<type>`" in body and "`lane.silent:<type>`" in body
    assert "this run's own session type" in body
    assert "Before this run: <summary> — <fix>" in body
    assert "run-health warnings before this run — first: <summary>" in body
    assert "N commits in this run's window (newest `abc1234`)" in body


@pytest.mark.parametrize("kind", ["SKILL", "DREAMING", "RESEARCH"])
def test_run_health_is_the_first_section_after_run_modes(kind):
    """'At the start of the run' only works if the section is read first: right after the
    run-modes dispatch table in SKILL, and first of all in DREAMING/RESEARCH (no run-modes)."""
    from scout.scripts.bootstrap import _assemble
    from tests.unit.test_briefing_mode_layer import _config

    text = _assemble(_config(), kind)
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    expected_first = ["## Run Modes", "## Run Health"] if kind == "SKILL" else ["## Run Health"]
    assert [h.split(" — ")[0] for h in headings[: len(expected_first)]] == expected_first
    at = text.index("\n## Run Health\n")
    assert all(text.index(h) > at for h in headings[len(expected_first) :])
    assert "Final Action" not in text[:at]


@pytest.mark.parametrize("kind", ["SKILL", "DREAMING", "RESEARCH"])
def test_every_assembled_brain_file_carries_the_run_health_phase(kind):
    from scout.scripts.bootstrap import _assemble
    from tests.unit.test_briefing_mode_layer import _config

    text = _assemble(_config(), kind)
    assert "scoutctl health --json" in text
    assert ".scout-cache/run-health.json" in text
    assert 'Never write "by this run"' in text
