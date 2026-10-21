"""Bootstrap seeds the ontology schema the vault's parser.py loads.

A fresh vault had ``knowledge-base/ontology/parser.py`` but no ``schema.yaml``
beside it, so ``parser.py validate`` (the CLI every session and the meta-review
runs) died with FileNotFoundError. The schema is now a create-only seed, copied
from the engine's packaged ``scout/kb/schema.yaml``.

Every install here passes ``skip_jobs=True``: no test registers launchd/cron jobs.
"""

from __future__ import annotations

import subprocess
import sys
from importlib.resources import files
from pathlib import Path

from scout.kb.paths import resolve_schema_path
from scout.scripts.bootstrap import BootstrapConfig, install, upgrade

PLUGIN = Path(__file__).parent.parent.parent.parent
SCHEMA_REL = Path("knowledge-base/ontology/schema.yaml")


def _config(vault: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=PLUGIN,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="",
        platform="macos",
        plugin_version="0.4.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


def _packaged() -> str:
    return files("scout").joinpath("kb", "schema.yaml").read_text(encoding="utf-8")


def _validate(vault: Path) -> subprocess.CompletedProcess[str]:
    """Run the vault's parser.py the way sessions do: no --schema, no --kb-root."""
    parser = vault / "knowledge-base" / "ontology" / "parser.py"
    return subprocess.run([sys.executable, str(parser), "validate"], capture_output=True, text=True, check=False)


def test_install_seeds_the_packaged_schema_and_parser_validates(tmp_path: Path) -> None:
    vault = tmp_path / "Scout"
    install(_config(vault))

    schema = vault / SCHEMA_REL
    assert schema.read_text(encoding="utf-8") == _packaged()
    assert resolve_schema_path(vault) == schema

    result = _validate(vault)
    assert result.returncode == 0, result.stderr
    assert "No validation errors." in result.stdout


def test_parser_validates_an_entity_against_the_seeded_schema(tmp_path: Path) -> None:
    """The seeded schema is the one the parser checks entities against."""
    vault = tmp_path / "Scout"
    install(_config(vault))
    person = vault / "knowledge-base" / "people" / "alex.md"
    person.write_text("---\ntype: no-such-type\nname: Alex\n---\n\nAlex.\n", encoding="utf-8")

    result = _validate(vault)
    assert result.returncode == 0, result.stderr
    assert "validation error" in result.stdout


def test_upgrade_seeds_a_missing_schema(tmp_path: Path) -> None:
    """A vault installed before the schema was seeded receives it on /scout-update."""
    vault = tmp_path / "Scout"
    install(_config(vault))
    (vault / SCHEMA_REL).unlink()

    upgrade(_config(vault))
    assert (vault / SCHEMA_REL).read_text(encoding="utf-8") == _packaged()
    assert _validate(vault).returncode == 0


def test_upgrade_never_overwrites_a_vault_schema(tmp_path: Path) -> None:
    """The vault extends its ontology in this file; an upgrade keeps it byte for byte."""
    vault = tmp_path / "Scout"
    install(_config(vault))
    mine = _packaged() + "\n# extended by the vault\n"
    (vault / SCHEMA_REL).write_text(mine, encoding="utf-8")

    upgrade(_config(vault))
    assert (vault / SCHEMA_REL).read_text(encoding="utf-8") == mine
