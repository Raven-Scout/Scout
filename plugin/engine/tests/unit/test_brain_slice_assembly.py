"""The shipped SKILL.md, sliced per mode (#84, spec 2026-10-13-static-instruction-trim §4, §7).

Assembled from the shipped phases with every connector enabled, then cut the way the
runner cuts it (``write_slice`` on the live file), these pin that a slice loses no rule
its mode runs:

- every section lands in exactly the slices of the modes its ``mode:`` names;
- the safety rules (privacy and its ``check-outbound``/``check-query`` gates, the write
  protocol, engine changes never in the live checkout, review requests) are in full in
  every mode's brain where they apply, dreaming and research included;
- a section a slice keeps never points, by name, at a section that slice dropped,
  apart from the reviewed exceptions in ``DANGLING_OK``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from scout import custom_connectors as cc
from scout.scripts.bootstrap import BootstrapConfig, _assemble, _template_vars
from scout.scripts.brain_merge import assembly_header
from scout.scripts.brain_slice import SLICE_CLASSES, slice_text, strip_markers, write_slice
from scout.scripts.phase_assembly import PhaseSection, parse_phase_file, render_template, select_sections
from scout.scripts.three_way_merge import three_way_merge

PLUGIN_ROOT = Path(__file__).parent.parent.parent.parent
PHASES = PLUGIN_ROOT / "phases"

# Every slot a scheduled run of each kind uses, and the brain file it reads.
SKILL_SLOTS = {
    "morning-briefing": "briefing",
    "weekend-briefing": "briefing",
    "morning-consolidation": "consolidation",
    "midday-consolidation": "consolidation",
    "afternoon-consolidation": "consolidation",
    "evening-consolidation": "consolidation",
}
ALL_RUNS = [*SKILL_SLOTS, "dreaming", "research"]


def _shipped_connectors() -> set[str]:
    return {s.requires for f in (PHASES / "connectors").glob("*.md") for s in parse_phase_file(f) if s.requires}


def _config(vault: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=PLUGIN_ROOT,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=_shipped_connectors(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


@pytest.fixture(scope="module")
def vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("vault")
    for kind in ("SKILL", "DREAMING", "RESEARCH"):
        (root / f"{kind}.md").write_text(_assemble(_config(root), kind, custom={}), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def brains(vault: Path) -> dict[str, str]:
    """What each run reads: its slot's slice of SKILL.md, or DREAMING.md / RESEARCH.md."""
    out = {}
    for slot in SKILL_SLOTS:
        read, _ = write_slice(vault, "SKILL", slot)
        assert read != vault / "SKILL.md", f"{slot}: expected a slice, got the whole file"
        out[slot] = read.read_text(encoding="utf-8")
    out["dreaming"] = (vault / "DREAMING.md").read_text(encoding="utf-8")
    out["research"] = (vault / "RESEARCH.md").read_text(encoding="utf-8")
    return out


def _rendered(vault: Path, section: PhaseSection) -> str:
    return render_template(section.body, _template_vars(_config(vault)))


def _skill_sections() -> list[PhaseSection]:
    return [
        s
        for d in ("core", "connectors")
        for f in sorted((PHASES / d).glob("*.md"))
        for s in select_sections(
            parse_phase_file(f), enabled_connectors=_shipped_connectors(), modes={"briefing", "consolidation"}
        )
    ]


# ---------- every section in the right slices ----------


@pytest.mark.parametrize("cls", sorted(SLICE_CLASSES))
def test_each_section_is_in_exactly_the_slices_of_its_modes(vault: Path, cls: str) -> None:
    sliced = slice_text((vault / "SKILL.md").read_text(encoding="utf-8"), cls).text
    for s in _skill_sections():
        body = _rendered(vault, s)
        runs = not s.mode or cls in s.mode
        assert (body in sliced) is runs, (
            f"{s.name} ({s.slot}, mode {s.mode}) {'missing from' if runs else 'leaked into'} the {cls} slice"
        )


def test_the_slices_together_keep_every_line(vault: Path) -> None:
    text = (vault / "SKILL.md").read_text(encoding="utf-8")
    kept = {line for cls in SLICE_CLASSES for line in slice_text(text, cls).text.splitlines()}
    assert [line for line in strip_markers(text).splitlines() if line not in kept] == []


def test_markers_change_nothing_but_marker_lines(vault: Path) -> None:
    """Upgrades insert the markers as whole lines at section boundaries and nothing else."""
    text = (vault / "SKILL.md").read_text(encoding="utf-8")
    bodies = [_rendered(vault, s) for s in _skill_sections()]
    assert strip_markers(text) == "\n\n".join([assembly_header("SKILL", vault), *bodies])


@pytest.mark.parametrize("kind", ["DREAMING", "RESEARCH"])
def test_single_mode_brain_files_carry_no_markers(vault: Path, kind: str) -> None:
    assert "scout:only" not in (vault / f"{kind}.md").read_text(encoding="utf-8")


def test_a_custom_connectors_consolidation_only_section_is_marked(tmp_path: Path) -> None:
    cc.write(
        tmp_path,
        {
            "suite_mail": {
                "display_name": "Mail suite",
                "server": "example_suite",
                "probe": "mcp__example_suite__list_folders",
                "preset": "mail",
                "inbound": {"tools": ["mcp__example_suite__search_messages"]},
                "outbound": {"tools": ["mcp__example_suite__search_sent"]},
            }
        },
    )
    cfg = _config(tmp_path)
    skill = _assemble(replace(cfg, enabled_connectors={"suite_mail"}), "SKILL")
    assert "## Mail suite Outbound Scan" in skill
    assert "## Mail suite Outbound Scan" not in slice_text(skill, "briefing").text
    assert "## Mail suite Outbound Scan" in slice_text(skill, "consolidation").text
    assert "## Mail suite Inbound Scan" in slice_text(skill, "briefing").text


# ---------- the safety rules reach every mode they apply to ----------

# (phase file, section name, a line that must open its rendered body, runs it applies to)
SAFETY_SECTIONS = [
    ("core/privacy.md", "privacy", "## Privacy: owner-only and shared surfaces", ALL_RUNS),
    ("core/write-protocol.md", "write-protocol", "## KB WRITE PROTOCOL", ALL_RUNS),
    ("core/git-setup.md", "git-setup", "## Step 0: Git Setup", ALL_RUNS),
    ("core/action-items.md", "action-items", "## Step 0: Archive Old Action Items", list(SKILL_SLOTS)),
    ("connectors/github.md", "github", "## GitHub Inbound Scan", list(SKILL_SLOTS)),
    ("connectors/github.md", "github", "### PRs Requesting Test User's Review", list(SKILL_SLOTS)),
]
# Rules named inside those sections, checked by their own words in every run they apply to.
SAFETY_PHRASES = [
    ("scoutctl kb check-outbound", ALL_RUNS),
    ("scoutctl kb check-query", ALL_RUNS),
    ("## Engine Changes: Never in the Live Engine Checkout", ALL_RUNS),
    ("### Hard Rule — Review Requests Never Drop (pre-commit)", list(SKILL_SLOTS)),
    ("action-items check-review-requests", list(SKILL_SLOTS)),
    ("### PR Review Requests", list(SKILL_SLOTS)),
]


def _section(vault: Path, rel: str, name: str, opening: str) -> str:
    rendered = [_rendered(vault, s) for s in parse_phase_file(PHASES / rel) if s.name == name]
    hits = [body for body in rendered if body.startswith(opening)]
    assert len(hits) == 1, f"{rel} [{name}]: expected one section opening with {opening!r}"
    return hits[0]


@pytest.mark.parametrize(
    ("rel", "name", "opening", "runs"), SAFETY_SECTIONS, ids=[f"{r}:{o}" for r, _, o, _ in SAFETY_SECTIONS]
)
def test_safety_sections_are_loaded_whole_in_every_run_they_apply_to(
    vault: Path, brains: dict[str, str], rel: str, name: str, opening: str, runs: list[str]
) -> None:
    body = _section(vault, rel, name, opening)
    missing = [run for run in runs if body not in brains[run]]
    assert missing == [], f"{rel} [{name}] is not loaded in full for: {', '.join(missing)}"


@pytest.mark.parametrize(("phrase", "runs"), SAFETY_PHRASES, ids=[p for p, _ in SAFETY_PHRASES])
def test_safety_rules_are_named_in_every_run_they_apply_to(
    brains: dict[str, str], phrase: str, runs: list[str]
) -> None:
    assert [run for run in runs if phrase not in brains[run]] == []


# ---------- no kept section points at a dropped one ----------

# (slice, the name a kept section uses for a dropped one) → why that's fine.
DANGLING_OK = {
    ("briefing", "Decisions Made"): "a project file's field name (kb-management), not a section reference",
    ("briefing", "context"): "emphasis in prose",
    ("consolidation", "Query — Briefing Data Gathering"): "the Run Modes table's morning-briefing row",
    ("consolidation", "Monday Preview"): "weekend-briefing's row and its digest rule: briefing runs only",
    ("consolidation", "Open PRs"): "a field label in GitHub-Sourced KB Updates",
}
_HEADING = re.compile(r"^#+\s*(.+?)\s*$", re.MULTILINE)
_EMPHASIS = re.compile(r"(?<![*\w])(\*\*|\*)([^*\n]+?)\1(?![*\w])")


def _names_a_heading(name: str, headings: set[str]) -> bool:
    """``**Outbound Scan**`` names every ``## <Connector> Outbound Scan``."""
    return any(h == name or h.endswith(name) for h in headings)


@pytest.mark.parametrize("cls", sorted(SLICE_CLASSES))
def test_kept_sections_only_reference_sections_the_slice_kept(vault: Path, cls: str) -> None:
    text = (vault / "SKILL.md").read_text(encoding="utf-8")
    every_heading = set(_HEADING.findall(strip_markers(text)))
    sliced = slice_text(text, cls).text
    kept_headings = set(_HEADING.findall(sliced))
    dangling = {
        (cls, name)
        for m in _EMPHASIS.finditer(sliced)
        if (name := m.group(2).strip().rstrip(".:"))
        and _names_a_heading(name, every_heading)
        and not _names_a_heading(name, kept_headings)
    }
    assert dangling == {k for k in DANGLING_OK if k[0] == cls}, (
        "a kept section names a section this slice drops: keep the target in both modes "
        "(its mode: frontmatter) or, if the reference is only another mode's, add it to DANGLING_OK"
    )


# ---------- the upgrade that brings the markers in ----------


def _upgrade_merge(vault: Path, edit: Callable[[str], str]) -> tuple[str, bool]:
    """Merge the marked assembly into a vault that ``edit`` changed, from the pre-marker
    assembly (``strip_markers`` gives it back byte for byte), as ``bootstrap upgrade`` does."""
    ours = (vault / "SKILL.md").read_text(encoding="utf-8")
    base = strip_markers(ours)
    merged = three_way_merge(base=base, ours=ours, theirs=edit(base))
    return merged.content, merged.conflicts


def _append_to_section(text: str, heading: str, rule: str) -> str:
    start = text.index(heading)
    end = text.index("\n\n## ", start)  # the blank line before the next section
    return text[:end] + f"\n{rule}" + text[end:]


def test_rules_a_vault_appended_to_sections_merge_cleanly_and_stay_in_their_mode(vault: Path) -> None:
    def edit(base: str) -> str:
        base = _append_to_section(base, "## Slack Query — Briefing Data Gathering", "- VAULT RULE: briefing only")
        base = _append_to_section(base, "## Slack Outbound Scan", "- VAULT RULE: consolidation only")
        return _append_to_section(base, "## Slack Inbound Scan", "- VAULT RULE: every mode")

    merged, conflicts = _upgrade_merge(vault, edit)
    assert not conflicts
    briefing, consolidation = (slice_text(merged, cls).text for cls in ("briefing", "consolidation"))
    assert "VAULT RULE: briefing only" in briefing and "VAULT RULE: briefing only" not in consolidation
    assert "VAULT RULE: consolidation only" in consolidation and "VAULT RULE: consolidation only" not in briefing
    assert "VAULT RULE: every mode" in briefing and "VAULT RULE: every mode" in consolidation


def test_a_section_a_vault_inserted_where_a_marker_goes_is_a_sidecar_not_a_silent_merge(vault: Path) -> None:
    """The documented case (CHANGELOG upgrade note): an insertion exactly where a marker goes."""

    def edit(base: str) -> str:
        at = base.index("## Slack Query — Briefing Data Gathering")
        return base[:at] + "## My Own Section\nmine\n\n" + base[at:]

    merged, conflicts = _upgrade_merge(vault, edit)
    assert conflicts and "## My Own Section" in merged
