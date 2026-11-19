"""Runs record the changes map's signals and act on the owner's decisions (spec 2026-11-17 §6.1, §6.2, §6.4, §7, §8).

The engine computes verdicts and lists what is due; only the phase text makes a scheduled run record a DM send, a
reply, a recurrence or an away day, commit its self-edits with the trailers the scanner reads, and carry out the
rollbacks and governance decisions the owner made. These pin that text, and that every command it names exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.changes.models import RUN_KINDS, SURFACES
from scout.cli import app as scoutctl
from scout.scripts.bootstrap import BootstrapConfig, _assemble
from scout.scripts.phase_assembly import parse_phase_file

PLUGIN = Path(__file__).resolve().parents[3]
PHASES = PLUGIN / "phases"
FEEDBACK = PHASES / "modes" / "feedback-processing.md"
DECISIONS = PHASES / "modes" / "follow-change-decisions.md"
STEP_1E = "## Step 1e: Test changes and act on your decisions"
PRIVACY = PHASES / "core" / "privacy.md"
CALENDAR = PHASES / "connectors" / "calendar.md"
GIT_SETUP = PHASES / "core" / "git-setup.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _step(body: str, heading: str) -> str:
    """From ``heading`` to the next ``### `` (or ``## ``) heading."""
    start = body.index(heading)
    nxt = re.search(r"\n#{2,3} ", body[start + len(heading) :])
    return body[start : start + len(heading) + nxt.start() if nxt else len(body)]


def _config(connectors: set[str]) -> BootstrapConfig:
    return BootstrapConfig(
        vault=Path("/tmp/does-not-matter"),
        plugin_root=PLUGIN,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=connectors,
        connector_inputs={},
        skip_jobs=True,
    )


# --- feedback processing ----------------------------------------------------------------------------------------------


def test_feedback_processing_rereads_seven_days_and_records_signals() -> None:
    step_1a = _step(_text(FEEDBACK), "### Step 1a")
    assert "trailing 7 days" in step_1a
    assert "top-level" in step_1a and "48 h" in step_1a  # a top-level owner message answers the newest bot message
    assert "scripts/scoutctl engagement record dm-reply" in step_1a
    assert "engagement record dm-reaction" in step_1a
    assert "--ref <bot ts>/<reply ts>" in step_1a
    assert "<bot ts>/r:<emoji" in step_1a
    assert "--surface slack-dm" in step_1a and "--sentiment" in step_1a and "--ts" in step_1a
    assert "--change" in step_1a
    assert "already recorded" in step_1a
    step_1c = _step(_text(FEEDBACK), "### Step 1c")
    assert "scoutctl engagement record recurrence --surface mistake-audit --ref pattern-<N>@" in step_1c


def test_step_1d_routes_governance_to_a_pending_file() -> None:
    step_1d = _step(_text(FEEDBACK), "### Step 1d")
    assert "governance-pending/<slug>.md" in step_1d
    for field in ("summary", "why", "targets"):
        assert f"`{field}`" in step_1d, field
    assert "```diff" in step_1d or "`diff` block" in step_1d
    assert "never applied by the run that wrote it" in step_1d
    assert "Harness fallback" in step_1d


def test_step_1e_is_its_own_dreaming_fragment_with_no_connector() -> None:
    (section,) = parse_phase_file(DECISIONS)
    assert section.requires is None
    assert section.mode == ["dreaming"]
    assert STEP_1E in section.body
    # It assembles right after feedback processing (files sort by name within phases/modes/).
    names = sorted(p.name for p in (PHASES / "modes").glob("*.md"))
    assert names.index(DECISIONS.name) == names.index(FEEDBACK.name) + 1
    # Feedback processing points at it by name and no longer carries the loop.
    feedback = _text(FEEDBACK)
    assert "Step 1e (Test changes and act on your decisions)" in feedback
    assert "**Step 1e: Test changes and act on your decisions**" in feedback
    assert "scoutctl changes due" not in feedback


def test_step_1e_acts_on_due() -> None:
    step_1e = _step(_text(DECISIONS), STEP_1E)
    assert "scripts/scoutctl changes due --json" in step_1e
    assert "scoutctl changes rollback <id>" in step_1e
    for trailer in ("Scout-Change-Reverts:", "Scout-Change-Why: owner-ask:undo-", "Scout-Change-Summary:"):
        assert trailer in step_1e, trailer
    assert "verdict:" in step_1e
    assert "scoutctl changes apply" in step_1e and "scoutctl changes discard" in step_1e
    assert "Scout-Change-Governance: yes" in step_1e
    assert "blocked" in step_1e and "edited-since" in step_1e
    assert "Slack" not in step_1e.replace("with or without Slack", "")  # the wrap, whatever its channel
    assert "Undo in the Changes tab, or run `scoutctl changes undo <id>`" in step_1e
    assert "scoutctl changes announced" in step_1e
    assert "engagement_tests_running" in step_1e
    assert "calibration_report_due" in step_1e and "docs/changes-calibration-report.md" in step_1e
    assert "governance-pending/" in step_1e
    assert "`verified` is false" in step_1e
    assert "Engine back-port" in step_1e


def test_runs_never_call_owner_only_commands() -> None:
    step_1e = _text(DECISIONS)
    for cmd in ("approve", "reject", "undo", "wrong", "auto-rollback", "calibrated"):
        assert f"`{cmd}`" in step_1e, cmd
    assert "Never run" in step_1e


def test_step_1f_lists_the_trailers() -> None:
    step_1f = _step(_text(FEEDBACK), "### Step 1f")
    for trailer in ("Scout-Change-Why:", "Scout-Change-Summary:", "Scout-Change-Reverts:"):
        assert trailer in step_1f, trailer


def test_no_phase_routes_to_dreaming_proposals() -> None:
    hits = [str(p.relative_to(PHASES)) for p in sorted(PHASES.rglob("*.md")) if "dreaming-proposals" in _text(p)]
    assert hits == []


def test_action_items_blocker_is_the_governance_pending_equivalent() -> None:
    text = _text(PHASES / "core" / "action-items.md")
    assert "approved-but-unapplied proposal" not in text
    assert "governance-pending/" in text


@pytest.mark.parametrize("rel", ["modes/wishlist.md", "modes/kb-deep-work.md"])
def test_governance_routes_to_the_pending_folder(rel: str) -> None:
    assert "governance-pending/" in _text(PHASES / rel)


# --- signals recorded by every run ------------------------------------------------------------------------------------


def test_dm_sent_recorded_only_after_an_owner_dm_send() -> None:
    (section,) = parse_phase_file(PRIVACY)
    assert section.requires is None  # every mode sends through this step
    body = section.body
    assert "scripts/scoutctl engagement record dm-sent --surface slack-dm --ref <ts>" in body
    assert "only then" in body
    at = body.index("engagement record dm-sent")
    for other in ("channel", "thread"):
        assert other in body[at - 400 : at + 400], other


def test_owner_away_is_recorded_by_the_inbound_calendar_scan() -> None:
    inbound = [s for s in parse_phase_file(CALENDAR) if s.slot == "inbound-scan"]
    assert len(inbound) == 1
    assert "briefing" in (inbound[0].mode or [])
    assert "scripts/scoutctl engagement record owner-away --surface calendar --ref <YYYY-MM-DD>" in inbound[0].body
    others = [s for s in parse_phase_file(CALENDAR) if s.slot != "inbound-scan"]
    assert not any("owner-away" in s.body for s in others)


def test_git_setup_names_the_trailers() -> None:
    (section,) = parse_phase_file(GIT_SETUP)
    assert set(section.mode or []) == {"briefing", "consolidation", "dreaming", "research"}
    rule = _step(section.body, "### Changing the vault's git")
    for needle in (
        "Scout-Change-Why:",
        "Scout-Change-Summary:",
        "Scout-Change-Targets:",
        "Scout-Change-Governance: yes",
        "`pattern-<N>`",
        "`owner-ask:<Slack ts>`",
        "`wishlist:<slug>`",
        "`research:<slug>`",
        "SKILL.md",
        "DREAMING.md",
        "RESEARCH.md",
        "scripts/",
        "120",
        "engagement_tests_running",
    ):
        assert needle in rule, needle
    assert re.search(r"git-safe-commit\.sh \$'[^']*\\n\\nScout-Change-Why: ", rule)
    for kind in ("SKILL", "DREAMING", "RESEARCH"):
        brain = _assemble(_config(set()), kind, custom={})
        assert "Scout-Change-Why:" in brain and "Scout-Change-Summary:" in brain, kind
        assert "`engagement_tests_running` is 0" in brain, kind


# --- what reaches the brains ------------------------------------------------------------------------------------------


def test_dreaming_brain_carries_the_test_loop() -> None:
    for connectors in ({"slack"}, set()):  # the loop doesn't depend on Slack
        dreaming = _assemble(_config(connectors), "DREAMING", custom={})
        assert "scripts/scoutctl changes rollback <id>" in dreaming, connectors
        assert dreaming.count(STEP_1E) == 1, connectors
    with_slack = _assemble(_config({"slack"}), "DREAMING", custom={})
    assert with_slack.index("## Phase 1: Feedback Processing") < with_slack.index(STEP_1E)
    assert with_slack.index(STEP_1E) < with_slack.index("## Phase 2: KB Deep Work")
    skill = _assemble(_config({"calendar", "slack"}), "SKILL", custom={})
    assert "owner-away" in skill
    assert "dm-sent" in skill


# --- every command the phases name is real ----------------------------------------------------------------------------


def _all_phase_text() -> str:
    return "\n".join(_text(p) for p in sorted(PHASES.rglob("*.md")))


def test_every_changes_subcommand_named_exists() -> None:
    subs = set(re.findall(r"scoutctl changes ([a-z][a-z-]+)", _all_phase_text()))
    assert {"due", "rollback", "apply", "discard", "announced"} <= subs
    for sub in sorted(subs):
        result = CliRunner().invoke(scoutctl, ["changes", sub, "--help"])
        assert result.exit_code == 0, sub


def test_every_engagement_record_names_a_run_kind_and_a_known_surface() -> None:
    calls = re.findall(r"engagement record ([a-z|-]+)((?: --[a-z]+ [^\s`]+)*)", _all_phase_text())
    assert calls
    for kinds, flags in calls:
        for kind in kinds.split("|"):
            assert kind in RUN_KINDS, kind
        surface = re.search(r"--surface ([a-z-]+)", flags)
        if surface:
            assert surface.group(1) in SURFACES, surface.group(1)
    assert {"dm-sent", "dm-reply", "dm-reaction", "recurrence", "owner-away"} <= {
        k for kinds, _ in calls for k in kinds.split("|")
    }
