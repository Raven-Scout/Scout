from __future__ import annotations

from dataclasses import replace

import pytest

from scout.config import load_config
from scout.scripts.bootstrap import _auto_update_enabled
from tests.unit.test_bootstrap_auto import _cfg


def test_auto_update_defaults_present(tmp_path):
    # With no vault config, packaged defaults must supply auto_update (disabled).
    cfg = load_config(data_dir=tmp_path)
    assert cfg["auto_update"]["enabled"] is False
    assert cfg["auto_update"]["channel"] == "stable"
    assert cfg["auto_update"]["soak_hours"] == 24


@pytest.mark.parametrize(
    ("config", "flag", "expected"),
    [
        (None, None, "false"),  # packaged default
        ("auto_update:\n  enabled: true\n", None, "true"),  # the vault's own value
        ("auto_update:\n  enabled: false\n", None, "false"),
        ("auto_update: on\n", None, "false"),  # malformed block reads as off
        ("auto_update:\n  enabled: true\n", False, "false"),  # this run's flag wins
        (None, True, "true"),
    ],
)
def test_auto_update_render_value(tmp_path, config, flag, expected):
    vault = tmp_path / "Scout"
    vault.mkdir()
    if config is not None:
        (vault / "scout-config.yaml").write_text(config)
    assert _auto_update_enabled(replace(_cfg(vault), auto_update=flag)) == expected
