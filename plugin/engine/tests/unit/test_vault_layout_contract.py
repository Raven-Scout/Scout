"""`scoutctl vault layout --json` is a contract with the macOS app (layout v2, spec 2026-11-26 §6).

``tests/fixtures/contract/vault-layout.json`` is the canonical copy; the app keeps a byte copy at
``apps/macos/ScoutTests/Fixtures/vault-layout.json`` (``contract.yml`` compares them), and
``apps/macos/ScoutTests/Engine/VaultLayoutContractTests.swift`` pins every path the app hardcodes to it. Set
``SCOUT_REGEN_CONTRACT=1`` to rewrite the canonical file from the vault below, then ``cp`` it over the app copy.
A new key goes into the layout table (``scout.layout``), never the JSON.

The vault is on layout v2 with three spaces: ``work`` (the default), ``personal`` and ``svj`` (both private).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from scout import layout

# Read at import: tests/conftest.py scrubs SCOUT_* from the environment before each test runs.
REGEN = os.environ.get("SCOUT_REGEN_CONTRACT") == "1"
CANONICAL = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "vault-layout.json"

CONFIG = """plugin:
  applied_migrations:
    - layout-v2
spaces:
  - key: work
    default: true
    visibility: normal
  - key: personal
    visibility: private
  - key: svj
    visibility: private
"""


def build_vault(root: Path) -> Path:
    vault = root / "Scout"
    (vault / "scout" / "brain").mkdir(parents=True)
    (vault / "scout-config.yaml").write_text(CONFIG, encoding="utf-8")
    return vault


def render(root: Path, monkeypatch) -> str:  # type: ignore[no-untyped-def]
    from scout.cli import app

    vault = build_vault(root)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    result = CliRunner().invoke(app, ["vault", "layout", "--json"])
    assert result.exit_code == 0, result.output
    return result.output


def test_vault_layout_matches_the_shared_fixture(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    text = render(tmp_path, monkeypatch)
    if REGEN:
        CANONICAL.write_text(text, encoding="utf-8")
    assert CANONICAL.read_text(encoding="utf-8") == text, (
        "regenerate with SCOUT_REGEN_CONTRACT=1, then cp it to apps/macos/ScoutTests/Fixtures/vault-layout.json"
    )


def test_the_fixture_has_every_key_and_no_absolute_path(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    text = render(tmp_path, monkeypatch)
    doc = json.loads(text)
    assert str(tmp_path) not in text
    assert doc["schema_version"] == layout.SCHEMA_VERSION == 1
    assert doc["layout_version"] == 2
    assert doc["migration"] == {"id": "layout-v2", "state": "applied"}
    assert set(doc["paths"]) == set(layout.V2.paths)
    assert all(not v.startswith("/") for v in doc["paths"].values())
    assert [s["key"] for s in doc["spaces"]] == ["work", "personal", "svj"]
    assert [s["default"] for s in doc["spaces"]] == [True, False, False]
    assert doc["spaces"][2] == {"key": "svj", "default": False, "visibility": "private", "root": "knowledge-base/svj"}
    assert doc["brain"]["SKILL"] == "scout/brain/SKILL.md"


def test_a_v1_vault_reports_v1_paths_and_no_spaces(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from scout.cli import app

    vault = tmp_path / "v1"
    vault.mkdir()
    (vault / "SKILL.md").write_text("# SKILL\n")
    (vault / "scout-config.yaml").write_text("{}\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    doc = json.loads(CliRunner().invoke(app, ["vault", "layout", "--json"]).output)
    assert doc["layout_version"] == 1 and doc["spaces"] == [] and doc["space_paths"] == {}
    assert doc["paths"]["brain_dir"] == "." and doc["paths"]["inbox"] == "inbox.md"
    assert doc["migration"]["state"] == "pending"
