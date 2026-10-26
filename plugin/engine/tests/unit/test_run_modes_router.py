"""The Run Modes table routes every step a mode runs (#84; #507's eval).

A run reads SKILL.md through the KB WRITE PROTOCOL in full, then reads each section its Run Modes
row names when it reaches that step. #507's eval found briefings skipping every connector section
the row didn't name (the row named only the Query sections), while those sections' frontmatter said
they were briefing steps too. These tests keep the frontmatter and the router in step: every section
a mode runs is either read in full up front, or its step name is in that mode's row and in the
section's heading, so a run can find it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scout.scripts.bootstrap import BootstrapConfig, _assemble
from scout.scripts.phase_assembly import parse_phase_file

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
PHASES = PLUGIN_ROOT / "phases"
WRITE_PROTOCOL = "## KB WRITE PROTOCOL"

# Slots read in full before any step: everything from the top through the KB WRITE PROTOCOL.
READ_UP_FRONT = {
    "run-modes",
    "run-health",
    "action-items",
    "setup",
    "inbox",
    "kb-guidelines",
    "privacy",
    "kb-write-protocol",
}
# Slots a run reaches as a step: the step name its row and its section heading both carry.
ROUTED = {
    "outbound-scan": "Outbound Scan",
    "inbound-scan": "Inbound Scan",
    "cross-check": "Cross-Check",
    "update": "KB Updates",
    "writeback": "Write-Back",
    "query": "Lookup",
    "notification": "Notification",
    "meeting-prep-generation": "Meeting Prep Generation",
    "post-meeting-processing": "Post-Meeting Processing",
    "monday-preview": "Monday Preview",
}
ROWS = {
    "briefing": ("morning-briefing", "weekend-briefing"),
    "consolidation": ("morning-consolidation",),
}
SECTION_DIRS = ("core", "connectors", "custom")
TEMPLATES = {"{{CONNECTOR_NAME}}": "Acme", "{{USER_NAME}}": "Alex"}


def _sections() -> list:
    return [s for d in SECTION_DIRS for f in sorted((PHASES / d).glob("*.md")) for s in parse_phase_file(f)]


def _in_mode(section, mode: str) -> bool:
    return not section.mode or mode in section.mode


def _heading(section) -> str:
    match = re.search(r"^## (.+)$", section.body, re.M)
    assert match, section.name
    return match.group(1)


def _row(mode: str) -> str:
    (table,) = [s for s in parse_phase_file(PHASES / "core" / "00-run-modes.md") if s.slot == "run-modes"]
    rows = [ln for ln in table.body.splitlines() if ln.startswith("| `")]
    return "\n".join(ln for ln in rows if any(f"`{key}`" in ln.split("|")[1] for key in ROWS[mode]))


def test_every_briefing_or_consolidation_slot_is_classified() -> None:
    """A new slot must be read up front or routed: otherwise no row sends a run to it."""
    for s in _sections():
        if _in_mode(s, "briefing") or _in_mode(s, "consolidation"):
            assert s.slot in READ_UP_FRONT | set(ROUTED), f"{s.name}/{s.slot}: add it to READ_UP_FRONT or ROUTED"


@pytest.mark.parametrize("mode", sorted(ROWS))
def test_the_mode_row_names_every_routed_step(mode: str) -> None:
    row = _row(mode)
    assert row, mode
    for s in _sections():
        if _in_mode(s, mode) and s.slot in ROUTED:
            assert f"**{ROUTED[s.slot]}**" in row, f"{mode} row doesn't name {s.name}/{s.slot}"


def test_each_routed_section_heading_carries_its_step_name() -> None:
    """The run finds a step's section by heading."""
    for s in _sections():
        if s.slot in ROUTED and (_in_mode(s, "briefing") or _in_mode(s, "consolidation")):
            heading = _heading(s)
            for token, value in TEMPLATES.items():
                heading = heading.replace(token, value)
            assert ROUTED[s.slot].lower() in heading.lower(), (s.name, heading)


def _connectors() -> set[str]:
    return {s.requires for f in (PHASES / "connectors").glob("*.md") for s in parse_phase_file(f) if s.requires}


def test_read_up_front_sections_come_first_and_end_at_the_write_protocol() -> None:
    cfg = BootstrapConfig(
        vault=Path("/tmp/does-not-matter"),
        plugin_root=PLUGIN_ROOT,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="UTC",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=_connectors(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )
    skill = _assemble(cfg, "SKILL", custom={})
    end = skill.index(WRITE_PROTOCOL)
    for s in _sections():
        if not (_in_mode(s, "briefing") or _in_mode(s, "consolidation")):
            continue
        heading = "## " + _heading(s).replace("{{USER_NAME}}", "Alex")
        if s.slot in READ_UP_FRONT:
            assert skill.index(heading) <= end, heading
        elif s.requires:  # a connector step sits after the part read up front
            assert skill.index(heading) > end, heading


def test_no_shipped_connector_keeps_a_briefing_only_query_section() -> None:
    """Briefing data gathering lives in each Inbound Scan's On a Briefing, Also step."""
    for f in sorted((PHASES / "connectors").glob("*.md")):
        for s in parse_phase_file(f):
            assert s.slot != "query", f"{f.name}: fold the query section into the Inbound Scan"
            if s.slot == "inbound-scan" and "briefing" in s.mode:
                assert "### On a Briefing, Also" in s.body, f.name


def test_the_runner_reads_up_front_then_each_named_section() -> None:
    tmpl = (PLUGIN_ROOT / "templates" / "run-scout.sh.tmpl").read_text(encoding="utf-8")
    assert "SKILL.md in full from the top through its KB WRITE PROTOCOL section" in tmpl
    assert "Before each one, read its section of SKILL.md in full" in tmpl
