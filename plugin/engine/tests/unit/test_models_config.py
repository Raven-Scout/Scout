"""scout.scripts.models_config — `models.default` edits keep the rest of scout-config.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout.scripts.models_config import ModelsWriteError, apply_models_default, write_models_default

RICH = """\
# Written by bootstrap — keep.
user:
  name: Alex   # hand note

# Budget note from `scoutctl budget set`.
budget:
  daily_usd: 40
"""


def test_appends_a_commented_block_and_keeps_every_other_byte() -> None:
    out = apply_models_default(RICH, "sonnet")
    assert out.startswith(RICH)
    assert yaml.safe_load(out)["models"] == {"default": "sonnet"}
    assert "scoutctl model set-default" in out[len(RICH) :]


def test_edits_an_existing_default_in_place_keeping_its_comment() -> None:
    text = RICH + "\nmodels:\n  default: opus   # chosen 2026-10\n\nauto_update:\n  enabled: false\n"
    out = apply_models_default(text, "Haiku")
    assert out == text.replace("default: opus", "default: haiku")


def test_adds_default_to_a_block_that_lacks_it() -> None:
    text = "models:\n  note: x\nuser:\n  name: Alex\n"
    out = apply_models_default(text, "sonnet")
    assert yaml.safe_load(out) == {"models": {"note": "x", "default": "sonnet"}, "user": {"name": "Alex"}}
    assert out.endswith("user:\n  name: Alex\n")


def test_same_value_is_a_no_op() -> None:
    text = "models:\n  default: sonnet\n"
    assert apply_models_default(text, "SONNET") is text


def test_empty_file_gets_the_block() -> None:
    assert yaml.safe_load(apply_models_default("", "opus")) == {"models": {"default": "opus"}}


@pytest.mark.parametrize("bad", ["sonet", "", "gpt-4"])
def test_bad_model_is_refused(bad: str) -> None:
    with pytest.raises(ModelsWriteError, match="models.default"):
        apply_models_default(RICH, bad)


def test_duplicate_block_is_refused() -> None:
    # pyyaml keeps the last duplicate, so an edit could land in the wrong one.
    with pytest.raises(ModelsWriteError):
        apply_models_default("models:\n  default: opus\nmodels:\n  default: haiku\n", "sonnet")


def test_scalar_models_is_refused() -> None:
    with pytest.raises(ModelsWriteError, match="not a mapping"):
        apply_models_default("models: sonnet\n", "opus")


def test_write_refuses_a_vault_without_config(tmp_path: Path) -> None:
    vault = tmp_path / "Scout"
    vault.mkdir()
    with pytest.raises(ModelsWriteError, match="no vault config"):
        write_models_default("sonnet", data_dir=vault)


def test_write_reports_changed_then_unchanged(tmp_path: Path) -> None:
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text(RICH, encoding="utf-8")
    first = write_models_default("sonnet", data_dir=vault)
    second = write_models_default("sonnet", data_dir=vault)
    assert (first["changed"], second["changed"]) == (True, False)
    assert first["default"] == "sonnet"
    assert not list(vault.glob("*.tmp"))
