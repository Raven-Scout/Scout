"""Scheduled runs make engine changes only in a separate clone, never in the engine launchd runs.

On a dev-managed engine (an editable install) a dreaming run created a branch in the live
engine checkout and edited it there: the edits ran in every later scheduled run, unreviewed,
and the off-main branch blocked the release upgrade. A later run did the right thing (a clone
under ~/.scout-worktrees/), so the phases allowed both. These pin the rule into every mode.
"""

from __future__ import annotations

import re
from pathlib import Path

from scout.scripts.phase_assembly import parse_phase_file

PHASES = Path(__file__).resolve().parents[3] / "phases"


def _section(rel: str, heading: str) -> str:
    sections = [s for s in parse_phase_file(PHASES / rel)]
    assert len(sections) == 1
    body = sections[0].body
    start = body.index(heading)
    nxt = body.find("\n## ", start + len(heading))
    return body[start : nxt if nxt != -1 else len(body)]


RULE = ("core/git-setup.md", "## Engine Changes: Never in the Live Engine Checkout")


def test_the_rule_reaches_every_scheduled_mode() -> None:
    (sec,) = parse_phase_file(PHASES / RULE[0])
    assert sec.requires is None
    assert set(sec.mode or []) == {"briefing", "consolidation", "dreaming", "research"}
    assert RULE[1] in sec.body


def test_the_live_engine_root_comes_from_the_engine_pointer() -> None:
    rule = _section(*RULE)
    assert "~/.local/state/scout/engine.json" in rule
    assert '"engine_root"' in rule


def test_it_names_every_mutating_git_operation_it_forbids() -> None:
    rule = _section(*RULE)
    for op in ("git checkout", "git switch", "branch", "git stash", "git reset", "git pull", "edit"):
        assert op in rule, op


def test_it_prescribes_a_fresh_clone_of_origin_main_under_scout_worktrees() -> None:
    rule = _section(*RULE)
    assert re.search(r"git clone [^\n]*~/\.scout-worktrees/", rule)
    assert "origin/main" in rule
    assert "gh pr create --draft" in rule
    assert "uncommitted" in rule


def test_it_routes_the_phases_backport_tool_to_the_clone() -> None:
    assert "--plugin-root ~/.scout-worktrees/" in _section(*RULE)
    reminder = (PHASES / "modes" / "feedback-processing.md").read_text(encoding="utf-8")
    assert "--plugin-root" in reminder


def test_it_says_what_an_engine_health_finding_means() -> None:
    rule = _section(*RULE)
    assert "engine.dirty" in rule and "engine.off_release" in rule


def test_the_engine_repo_is_public() -> None:
    assert "public" in _section(*RULE)


def test_no_phase_hard_codes_a_live_engine_path() -> None:
    """The live engine is wherever the pointer says; a maintainer clone path in phase text
    is what invited runs to edit that checkout in place."""
    hits = [
        f"{p.relative_to(PHASES)}:{n}"
        for p in sorted(PHASES.rglob("*.md"))
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"(~|\$HOME|\$\{HOME\})/scout-plugin\b", line)
    ]
    assert hits == []
