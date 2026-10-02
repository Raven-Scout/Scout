"""The upgrade's version stamp keeps the comments in scout-config.yaml.

``_stage_version_stamp`` rewrote the whole file through a pyyaml round-trip, so
every upgrade deleted every comment — including the one ``scoutctl budget set``
writes above the ``budget:`` block. Untouched blocks now come back verbatim.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout.scripts.bootstrap import BootstrapConfig, install, upgrade
from scout.scripts.budget_config import apply_updates

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


def test_upgrade_keeps_the_comment_above_the_budget_block(vault: Path) -> None:
    config = vault / "scout-config.yaml"
    # `scoutctl budget set` appends the block with its explanatory comment.
    config.write_text(apply_updates(config.read_text(encoding="utf-8"), {"daily_budget_usd": 120}), encoding="utf-8")
    assert "# Written by `scoutctl budget set`; safe to edit by hand." in _lines(config)

    upgrade(_config(vault, version="0.4.1"))

    after = config.read_text(encoding="utf-8")
    assert "# Budget gating for scheduled runs — inspect with `scoutctl budget show`.\n" in after
    assert "# Written by `scoutctl budget set`; safe to edit by hand.\nbudget:\n" in after
    parsed = yaml.safe_load(after)
    assert parsed["plugin"]["version_at_last_update"] == "0.4.1"
    assert parsed["budget"]["daily_usd"] == 120


def test_upgrade_keeps_comments_throughout_the_config(vault: Path) -> None:
    config = vault / "scout-config.yaml"
    config.write_text(
        "# TestScout configuration — hand-tuned\n"
        + config.read_text(encoding="utf-8")
        + "\n# Off-peak hours, local time\n"
        "off_peak:\n"
        "  # prefer the small hours\n"
        "  start: 23\n"
        "  end: 6  # inclusive\n",
        encoding="utf-8",
    )

    upgrade(_config(vault, version="0.4.1"))

    after = config.read_text(encoding="utf-8")
    assert after.startswith("# TestScout configuration — hand-tuned\n")
    assert (
        "\n# Off-peak hours, local time\noff_peak:\n  # prefer the small hours\n  start: 23\n  end: 6  # inclusive\n"
        in after
    )
    assert yaml.safe_load(after)["plugin"]["version_at_last_update"] == "0.4.1"


def test_upgrade_keeps_the_comment_above_a_block_it_rewrites(vault: Path) -> None:
    """`plugin:` changes on every upgrade; the comment above it still survives."""
    config = vault / "scout-config.yaml"
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text.replace("\nplugin:\n", "\n# Stamped by bootstrap — do not hand-edit\nplugin:\n"), encoding="utf-8"
    )

    upgrade(_config(vault, version="0.4.1"))

    after = config.read_text(encoding="utf-8")
    assert "\n# Stamped by bootstrap — do not hand-edit\nplugin:\n" in after
    assert yaml.safe_load(after)["plugin"]["version_at_last_update"] == "0.4.1"


def test_upgrade_adds_missing_blocks_to_a_config_without_a_trailing_newline(vault: Path) -> None:
    config = vault / "scout-config.yaml"
    config.write_text("# hand-written\ntimezone: America/New_York\nextra: 1", encoding="utf-8")

    upgrade(_config(vault, version="0.4.1"))

    after = config.read_text(encoding="utf-8")
    parsed = yaml.safe_load(after)
    assert after.startswith("# hand-written\n")
    assert parsed["extra"] == 1
    assert parsed["plugin"]["version_at_last_update"] == "0.4.1"
    assert parsed["user"]["name"] == "Alex"


def test_upgrade_still_collapses_a_duplicated_block(vault: Path) -> None:
    """A duplicated top-level key cannot be rewritten block by block; the stamp
    falls back to a plain dump, which keeps the last block as PyYAML reads it."""
    config = vault / "scout-config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "plugin:\n  version_at_last_setup: 0.3.0\n", encoding="utf-8"
    )

    upgrade(_config(vault, version="0.4.1"))

    after = config.read_text(encoding="utf-8")
    assert after.count("\nplugin:\n") == 1
    parsed = yaml.safe_load(after)
    assert parsed["plugin"]["version_at_last_setup"] == "0.3.0"
    assert parsed["plugin"]["version_at_last_update"] == "0.4.1"


def test_upgrade_warns_when_it_cannot_keep_the_comments(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A document marker defeats the block scanner, so the stamp falls back to a
    plain dump. The comments are lost, as before; the upgrade must say so."""
    config = vault / "scout-config.yaml"
    config.write_text("---\n# hand-written note\n" + config.read_text(encoding="utf-8"), encoding="utf-8")

    upgrade(_config(vault, version="0.4.1"))

    assert yaml.safe_load(config.read_text(encoding="utf-8"))["plugin"]["version_at_last_update"] == "0.4.1"
    assert "scout-config.yaml comments not preserved" in capsys.readouterr().err


def test_upgrade_is_silent_when_there_were_no_comments_to_keep(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = vault / "scout-config.yaml"
    config.write_text("---\n" + config.read_text(encoding="utf-8"), encoding="utf-8")

    upgrade(_config(vault, version="0.4.1"))

    assert "comments not preserved" not in capsys.readouterr().err
