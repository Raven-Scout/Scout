"""An upgrade merges the vault's .gitignore instead of overwriting it.

The vault's sessions auto-commit it, so a .gitignore line is sometimes all that
keeps a secret out of git history (``.mcp.json`` holds live API keys). The
upgrade used to replace the file with the template, dropping every line a vault
had added. It is now append-only: vault lines stay, missing template lines are
added, nothing is removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts import bootstrap
from scout.scripts.bootstrap import BootstrapConfig, install, upgrade

PLUGIN_ROOT = Path(__file__).resolve().parents[3]


def _config(vault: Path, *, version: str = "0.4.0") -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=PLUGIN_ROOT,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version=version,
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    install(_config(v))
    return v


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_upgrade_keeps_gitignore_lines_the_template_does_not_have(vault: Path) -> None:
    gitignore = vault / ".gitignore"
    gitignore.write_text(
        gitignore.read_text(encoding="utf-8") + "\n# Local secrets\nsecrets.env\nprivate-notes/\n",
        encoding="utf-8",
    )

    upgrade(_config(vault, version="0.4.1"))

    after = _lines(gitignore)
    assert "secrets.env" in after
    assert "private-notes/" in after
    assert "# Local secrets" in after


def test_upgrade_restores_template_lines_missing_from_the_vault_gitignore(vault: Path) -> None:
    """New template lines reach old vaults; the vault's own lines stay put."""
    gitignore = vault / ".gitignore"
    gitignore.write_text("# my vault\n.obsidian/\nsecrets.env\n", encoding="utf-8")

    upgrade(_config(vault, version="0.4.1"))

    after = _lines(gitignore)
    assert after[:3] == ["# my vault", ".obsidian/", "secrets.env"], "vault lines must keep their order"
    for line in (".mcp.json", ".venv/", ".scout-logs/", "*.bak.*"):
        assert line in after
    assert after.count(".obsidian/") == 1, "a line both sides have is not duplicated"


def test_gitignore_merge_is_idempotent(vault: Path) -> None:
    gitignore = vault / ".gitignore"
    gitignore.write_text("secrets.env\n", encoding="utf-8")
    upgrade(_config(vault, version="0.4.1"))
    once = gitignore.read_text(encoding="utf-8")
    upgrade(_config(vault, version="0.4.2"))
    assert gitignore.read_text(encoding="utf-8") == once


def test_merge_gitignore_carries_a_missing_lines_comment_with_it() -> None:
    template = "# Holds a live API key — never commit\n.mcp.json\n\n.venv/\n"
    merged = bootstrap.merge_gitignore("secrets.env\n", template)
    assert merged == "secrets.env\n\n# Holds a live API key — never commit\n.mcp.json\n.venv/\n"


def test_merge_gitignore_leaves_a_complete_vault_file_byte_identical() -> None:
    vault_text = "# mine\n.mcp.json\n.venv/\nextra/"  # no trailing newline, extra line
    assert bootstrap.merge_gitignore(vault_text, ".venv/\n.mcp.json\n") == vault_text


def test_merge_gitignore_ignores_whitespace_differences() -> None:
    assert bootstrap.merge_gitignore(".venv/   \n", ".venv/\n") == ".venv/   \n"
