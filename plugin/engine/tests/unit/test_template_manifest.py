"""Every file under plugin/templates/ has a reader.

A template no manifest in ``bootstrap.py`` names reaches no vault, on install or
upgrade, and nothing says so: nine sat that way, five of them KB seeds every
session reads (#197). Each path here is either named by a bootstrap manifest or
allow-listed with the code that reads it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts import bootstrap
from scout.scripts.bootstrap import BootstrapConfig, install
from scout.scripts.migrate_perfile import RUN_LOG_HEADER

PLUGIN = Path(__file__).parent.parent.parent.parent
ENGINE_SRC = PLUGIN / "engine" / "scout"

# Template files no bootstrap manifest names, each with the engine module that
# reads it. A new entry needs a real reader: the test checks the module names it.
_READ_ELSEWHERE = {
    "templates/connector-probes.yaml": "scripts/connector_probes.py",
}


def _manifest_paths() -> set[str]:
    return {
        *bootstrap._CAT1_FILES_FROM_PLUGIN.values(),
        *bootstrap._CAT_MERGE_FILES.values(),
        *(p for _, p in bootstrap._CAT1_TEMPLATES),
        *(p for _, p in bootstrap._INSTALL_ONLY_TEMPLATES),
        *(p for _, p in bootstrap._CAT1B_RUNNERS),
    }


def _template_files() -> set[str]:
    root = PLUGIN / "templates"
    return {
        p.relative_to(PLUGIN).as_posix()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }


def test_every_template_is_named_by_a_manifest_or_allow_listed():
    orphans = _template_files() - _manifest_paths() - set(_READ_ELSEWHERE)
    assert not orphans, (
        "templates no bootstrap manifest names reach no vault; add each to a manifest "
        f"in bootstrap.py, delete it, or allow-list its reader here: {sorted(orphans)}"
    )


def test_every_manifest_entry_has_a_template():
    missing = _manifest_paths() - _template_files()
    assert not missing, f"manifest entries with no file under templates/: {sorted(missing)}"


@pytest.mark.parametrize(("template", "reader"), sorted(_READ_ELSEWHERE.items()))
def test_allow_listed_template_is_read_by_its_reader(template, reader):
    assert (PLUGIN / template).is_file(), template
    assert Path(template).name in (ENGINE_SRC / reader).read_text(encoding="utf-8")


def _config(vault: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=PLUGIN,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.4.0",
        enabled_connectors={"slack"},
        connector_inputs={"user_slack_id": "U0123456789"},
        skip_jobs=True,
        skip_claude=True,
    )


@pytest.mark.parametrize(
    "vault_rel",
    [
        "knowledge-base/knowledge-base.md",
        "knowledge-base/people.md",
        "knowledge-base/channels.md",
        "knowledge-base/projects/projects.md",
        "knowledge-base/research-queue.md",
    ],
)
def test_install_seeds_the_kb_index_files(tmp_path, vault_rel):
    """The sessions read and link these; install stopped writing them when
    /scout-setup became a wrapper around `bootstrap install`."""
    vault = tmp_path / "Scout"
    install(_config(vault))
    text = (vault / vault_rel).read_text(encoding="utf-8")
    assert text.startswith("# ")
    assert "{{" not in text


def test_install_seeds_people_with_the_users_row(tmp_path):
    vault = tmp_path / "Scout"
    install(_config(vault))
    people = (vault / "knowledge-base/people.md").read_text(encoding="utf-8")
    assert "| Test User | (you) | U0123456789 | test@example.com |" in people


def test_research_queue_seed_is_the_migrated_run_log():
    """migrate_perfile treats any other shape as a legacy queue to split up."""
    text = (PLUGIN / "templates/knowledge-base/research-queue.md.tmpl").read_text(encoding="utf-8")
    assert text.startswith(RUN_LOG_HEADER)


@pytest.mark.parametrize("vault_rel", ["docs/wishlist", "knowledge-base/research-queue"])
def test_install_creates_the_per_file_item_dirs(tmp_path, vault_rel):
    vault = tmp_path / "Scout"
    install(_config(vault))
    assert (vault / vault_rel).is_dir()
