"""The KB page standard: the phases follow it, and bootstrap seeds it as a plugin-owned doc."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scout.kb.lint_config import load_lint_config
from scout.scripts import vault_drift
from scout.scripts.bootstrap import BootstrapConfig, _assemble, install, upgrade

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
STANDARD = "docs/kb-page-standard.md"
TEMPLATE = PLUGIN_ROOT / "templates" / STANDARD


def _cfg(vault: Path, plugin_root: Path = PLUGIN_ROOT) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=plugin_root,
        instance_name="Scout",
        instance_name_lower="scout",
        user_name="Sam",
        user_email="sam@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end, text.index(start))]


# ------------------------------------------------------------------ phases ----


@pytest.mark.parametrize("kind", ["SKILL", "DREAMING", "RESEARCH"])
def test_every_brain_points_at_the_standard(kind: str, tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path / "Scout"), kind)
    assert "[[docs/kb-page-standard]]" in text
    assert "knowledge-base/projects/<name>/knowledge/<topic>.md" in text


@pytest.mark.parametrize("kind", ["SKILL", "DREAMING"])
def test_no_phase_text_contradicts_the_standard(kind: str, tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path / "Scout"), kind)
    # Project pages: verification in frontmatter, decision log in its own note.
    assert 'Current state with "Last verified" date' not in text
    assert "Dated log of architectural and strategic decisions" not in text
    assert "<project-name>-decisions.md" in text
    # knowledge-base.md holds no run history.
    assert "Recent session history" not in text
    # Topic domains are for cross-project subjects, not any subject at 3+ notes.
    assert "create `topics/<domain>/` when a subject reaches three or more topic notes" not in text
    assert "`topics/<domain>/` is only for subjects that span projects" in text


def test_deep_work_records_verification_in_frontmatter(tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path / "Scout"), "DREAMING")
    execute = _section(text, "### Step 2c: Execute", "### Step 2d")
    assert 'Update "Last verified" dates' not in execute
    assert "`last_verified:`" in execute and "`verified_against:`" in execute
    assert 'Never add a "Last verified" note or blockquote to the body' in execute


def test_nightly_pass_rebuilds_one_project_page_in_the_standards_order(tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path / "Scout"), "DREAMING")
    step = _section(text, "### Step 2a-shrink", "### Step 2b")
    assert "Rebuild **one** over-budget project page" in step
    assert "One page per night, never more" in step
    order = [
        "**Archive the page verbatim**",
        "**Move its knowledge into `knowledge/` topic notes**",
        "**Re-verify the current state live.**",
        "**Rewrite the page in place**",
        "**Check nothing got worse:**",
    ]
    positions = [step.index(s) for s in order]
    assert positions == sorted(positions)
    assert "scoutctl kb validate" in step and "dangling" in step
    # The 2026-10-04 off switch is the vault's; the engine's pass is on.
    assert "deliberately OFF" not in text


# ---------------------------------------------------------------- template ----


def test_the_template_is_copied_verbatim_and_carries_no_template_variables() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert text.startswith("# KB page standard\n")
    assert "{{" not in text  # a verbatim managed file: nothing would substitute it
    for heading in ("## Folder layout", "## Project page", "## Restructuring an existing over-long page"):
        assert heading in text


def test_the_standard_is_a_vault_developed_managed_file() -> None:
    assert STANDARD in vault_drift.VAULT_DEVELOPED_FILES


def test_install_seeds_the_standard(tmp_path: Path) -> None:
    vault = tmp_path / "Scout"
    install(_cfg(vault))
    assert (vault / STANDARD).read_text(encoding="utf-8") == TEMPLATE.read_text(encoding="utf-8")


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    shutil.copytree(PLUGIN_ROOT / "templates", root / "templates")
    return root


def test_upgrade_writes_the_standard_into_a_vault_that_lacks_it(tmp_path: Path, plugin: Path) -> None:
    vault = tmp_path / "Scout"
    install(_cfg(vault, plugin))
    (vault / STANDARD).unlink()
    shutil.rmtree(vault / vault_drift.SNAPSHOT_DIR / "docs")

    upgrade(_cfg(vault, plugin))

    assert (vault / STANDARD).read_text(encoding="utf-8") == TEMPLATE.read_text(encoding="utf-8")


def test_upgrade_keeps_a_standard_the_vault_wrote_itself(tmp_path: Path, plugin: Path) -> None:
    """A vault that wrote its own standard before the plugin shipped one keeps
    it running; the plugin's is parked, and the next upgrade still runs."""
    vault = tmp_path / "Scout"
    install(_cfg(vault, plugin))
    own = "# KB page standard\n\nThis vault's own version.\n"
    (vault / STANDARD).write_text(own, encoding="utf-8")
    shutil.rmtree(vault / vault_drift.SNAPSHOT_DIR / "docs")  # no record of a render

    result = upgrade(_cfg(vault, plugin))

    assert (vault / STANDARD).read_text(encoding="utf-8") == own
    assert [(e.path, e.outcome) for e in result.vault_edits] == [(STANDARD, "conflict")]
    assert (vault / vault_drift.DRIFT_DIR / f"{STANDARD}.plugin").is_file()
    upgrade(_cfg(vault, plugin))  # not blocked


def test_upgrade_merges_a_tailored_standard_with_a_plugin_change(tmp_path: Path, plugin: Path) -> None:
    vault = tmp_path / "Scout"
    install(_cfg(vault, plugin))
    with (vault / STANDARD).open("a", encoding="utf-8") as fh:
        fh.write("\n## This vault's extra rule\n\nKeep pages short.\n")
    tmpl = plugin / "templates" / STANDARD
    v2 = tmpl.read_text(encoding="utf-8").replace("# KB page standard\n", "# KB page standard\n\n<!-- v2 -->\n", 1)
    tmpl.write_text(v2, encoding="utf-8")

    result = upgrade(_cfg(vault, plugin))

    text = (vault / STANDARD).read_text(encoding="utf-8")
    assert "<!-- v2 -->" in text and "This vault's extra rule" in text
    assert [(e.path, e.outcome) for e in result.vault_edits] == [(STANDARD, "merged")]


# -------------------------------------------------------------------- lint ----


def test_project_knowledge_notes_are_topic_notes_to_lint(tmp_path: Path) -> None:
    cfg = load_lint_config(tmp_path)
    note = "knowledge-base/projects/widgets/knowledge/release-pipeline.md"
    assert cfg.budget_for(note) == 10240
    assert cfg.is_topic(note)
    assert cfg.budget_for("knowledge-base/projects/widgets/widgets.md") == 15360
    assert not cfg.is_topic("knowledge-base/projects/widgets/widgets.md")
