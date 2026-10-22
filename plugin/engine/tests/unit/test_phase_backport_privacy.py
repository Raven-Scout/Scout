"""`scoutctl phases backport` refuses a bundle naming a private note (#453, spec §8.4, §11.2 Back-port).

The bundle is what the back-port writes into the (public) plugin repo's
``phases/``: every phase file an applied hunk edits, as it would be written.
``check_outbound(..., "public_repo")`` runs over each file's added lines, in
a dry run and with ``--apply``, and a hit refuses the whole command before
anything is written. The refusal names the phase file and line numbers only.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.kb import outbound
from scout.kb.ontology import clear_frontmatter_cache
from scout.scripts.phase_backport import (
    build_rendered_sections,
    bundle_files,
    bundle_privacy_hits,
    combine_applied,
    plan_backport,
)
from tests.unit.privacy_vault_helpers import build_privacy_vault

VARS = {"USER_EMAIL": "alex@example.com", "SCOUT_DIR": "/home/alex/vault"}
BODY = "### Step\n\nScan under {{SCOUT_DIR}}.\nScore files."
PRIVATE_LINE = "Remember task-renew-permit before Friday."  # a private note's slug
CLEAN_LINE = "Skip files under the archive folder."


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    outbound.clear_cache()
    return build_privacy_vault(tmp_path)


def _plugin(tmp_path: Path) -> Path:
    plugin = tmp_path / "plugin"
    pf = plugin / "phases" / "core" / "kb.md"
    pf.parent.mkdir(parents=True)
    pf.write_text(f"---\nphase: core\nname: KB\nslot: s\nmode: [briefing]\nrequires: null\n---\n{BODY}\n")
    return plugin


def _assembled(plugin: Path) -> str:
    return "\n\n".join(s.rendered_body for s in build_rendered_sections(plugin / "phases", "SKILL", VARS, set()))


def _plan(plugin: Path, added: str):  # type: ignore[no-untyped-def]
    sections = build_rendered_sections(plugin / "phases", "SKILL", VARS, set())
    snapshot = _assembled(plugin)
    live = snapshot.replace("Scan under /home/alex/vault.", f"Scan under /home/alex/vault.\n{added}")
    results = plan_backport(snapshot, live, sections, VARS)
    return [r for r in results if r.status == "applied"], sections


def test_bundle_files_is_what_apply_writes(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    applied, sections = _plan(plugin, CLEAN_LINE)
    ((pf, (old, new)),) = bundle_files(applied, sections).items()
    assert pf == plugin / "phases" / "core" / "kb.md"
    assert old == pf.read_text()
    assert new == old.replace("Scan under {{SCOUT_DIR}}.\n", f"Scan under {{{{SCOUT_DIR}}}}.\n{CLEAN_LINE}\n")


def test_a_bundle_naming_a_private_note_is_refused(tmp_path: Path, vault: Path) -> None:
    plugin = _plugin(tmp_path)
    applied, sections = _plan(plugin, PRIVATE_LINE)
    hits = bundle_privacy_hits(vault, bundle_files(applied, sections))
    assert hits == [(plugin / "phases" / "core" / "kb.md", [11])]


def test_a_clean_bundle_passes(tmp_path: Path, vault: Path) -> None:
    plugin = _plugin(tmp_path)
    applied, sections = _plan(plugin, CLEAN_LINE)
    assert bundle_privacy_hits(vault, bundle_files(applied, sections)) == []


def test_only_added_lines_count(tmp_path: Path, vault: Path) -> None:
    """A fragment that already says "billing" (a private slug in this vault) isn't refused for it."""
    plugin = _plugin(tmp_path)
    pf = plugin / "phases" / "core" / "kb.md"
    pf.write_text(pf.read_text().replace("Score files.", "Score files.\nCheck billing pages."))
    applied, sections = _plan(plugin, CLEAN_LINE)
    assert bundle_privacy_hits(vault, bundle_files(applied, sections)) == []


def _cli(monkeypatch: pytest.MonkeyPatch, vault: Path, plugin: Path, *args: str):  # type: ignore[no-untyped-def]
    import scout.cli as cli
    from scout.scripts import bootstrap

    cfg = SimpleNamespace(plugin_root=plugin, enabled_connectors=set(), connector_inputs={})
    monkeypatch.setattr(cli, "_vault_bootstrap_config", lambda *_a, **_k: cfg)
    monkeypatch.setattr(bootstrap, "_template_vars", lambda _cfg: VARS)
    monkeypatch.setattr(bootstrap, "load_custom", lambda _cfg: {})
    return CliRunner().invoke(app, ["phases", "backport", "--kind", "SKILL", "--vault", str(vault), *args])


def _brain(vault: Path, plugin: Path, added: str) -> None:
    snapshot = _assembled(plugin)
    snap = vault / ".scout-state" / "last-assembled" / "SKILL.md"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(snapshot)
    (vault / "SKILL.md").write_text(
        snapshot.replace("Scan under /home/alex/vault.", f"Scan under /home/alex/vault.\n{added}")
    )


@pytest.mark.parametrize("apply", [False, True])
def test_the_cli_refuses_a_private_bundle_and_writes_nothing(
    tmp_path: Path, vault: Path, monkeypatch: pytest.MonkeyPatch, apply: bool
) -> None:
    plugin = _plugin(tmp_path)
    before = (plugin / "phases" / "core" / "kb.md").read_text()
    _brain(vault, plugin, PRIVATE_LINE)
    result = _cli(monkeypatch, vault, plugin, *(["--apply"] if apply else []))
    assert result.exit_code == 1, result.output
    assert "phases/core/kb.md" in result.output and "line 11" in result.output
    assert "permit" not in result.output
    assert (plugin / "phases" / "core" / "kb.md").read_text() == before


def test_the_cli_applies_a_clean_bundle(tmp_path: Path, vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _plugin(tmp_path)
    _brain(vault, plugin, CLEAN_LINE)
    result = _cli(monkeypatch, vault, plugin, "--apply")
    assert result.exit_code == 0, result.output
    assert CLEAN_LINE in (plugin / "phases" / "core" / "kb.md").read_text()


def test_a_hunk_planned_for_two_brain_files_is_bundled_once(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    applied, sections = _plan(plugin, CLEAN_LINE)
    again, _ = _plan(plugin, CLEAN_LINE)
    ((_pf, (_old, new)),) = bundle_files(combine_applied([applied, again]), sections + sections).items()
    assert new.count(CLEAN_LINE) == 1


def test_an_applied_result_without_a_fragment_bundles_nothing(tmp_path: Path) -> None:
    from scout.scripts.phase_backport import HunkResult

    plugin = _plugin(tmp_path)
    _applied, sections = _plan(plugin, CLEAN_LINE)
    assert bundle_files([HunkResult("applied", [CLEAN_LINE], anchor="x", retemplatized=[CLEAN_LINE])], sections) == {}


def test_a_clean_dry_run_reports_and_writes_nothing(
    tmp_path: Path, vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = _plugin(tmp_path)
    before = (plugin / "phases" / "core" / "kb.md").read_text()
    _brain(vault, plugin, CLEAN_LINE)
    result = _cli(monkeypatch, vault, plugin)
    assert result.exit_code == 0, result.output
    assert "1 applied" in result.output and "dry-run" in result.output
    assert (plugin / "phases" / "core" / "kb.md").read_text() == before
