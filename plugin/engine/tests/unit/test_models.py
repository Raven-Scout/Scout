"""Unit tests for scout.models — which Claude model a scheduled run uses."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.errors import ConfigError
from scout.models import ALIASES, BUILTIN_DEFAULT, default_model, resolve_model, validate_model

SLOT_YAML = """\
schema_version: 1
slots:
  midday-consolidation:
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "13:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 2
    on_miss: collapse
    cooldown_minutes: 90
    model: sonnet
  morning-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 60
"""


def _vault(tmp_path: Path, *, schedule: str | None = SLOT_YAML, config: str | None = None) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    if schedule is not None:
        (v / ".scout-state" / "schedule.yaml").write_text(schedule, encoding="utf-8")
    if config is not None:
        (v / "scout-config.yaml").write_text(config, encoding="utf-8")
    return v


# ----- validate_model --------------------------------------------------------


@pytest.mark.parametrize("value", ["opus", "sonnet", "haiku", "Sonnet", " HAIKU "])
def test_aliases_are_accepted_and_lower_cased(value: str) -> None:
    assert validate_model(value, where="x") == value.strip().lower()


@pytest.mark.parametrize("value", ["claude-sonnet-5-5", "claude-opus-4-1-20250805", "Claude-Haiku-5-5"])
def test_full_model_ids_are_accepted(value: str) -> None:
    assert validate_model(value, where="x") == value.lower()


@pytest.mark.parametrize("value", ["sonet", "gpt-4", "", "  ", "claude-", "claude sonnet", 3, None, ["opus"]])
def test_other_values_are_rejected_naming_where_and_what(value: object) -> None:
    with pytest.raises(ConfigError) as exc:
        validate_model(value, where="slot 'midday-consolidation'")
    message = str(exc.value)
    assert "slot 'midday-consolidation'" in message
    assert repr(value) in message
    for alias in ALIASES:
        assert alias in message
    assert "claude-" in message


def test_builtin_default_is_opus() -> None:
    # Today's behaviour: nothing changes until the user picks a model.
    assert BUILTIN_DEFAULT == "opus"


# ----- resolve_model: precedence -------------------------------------------


def test_slot_model_wins_over_config_default(tmp_path: Path) -> None:
    v = _vault(tmp_path, config="models:\n  default: haiku\n")
    resolved = resolve_model(slot_key="midday-consolidation", data_dir=v, environ={})
    assert (resolved.model, resolved.source) == ("sonnet", "slot")


def test_slot_without_model_uses_config_default(tmp_path: Path) -> None:
    v = _vault(tmp_path, config="models:\n  default: haiku\n")
    resolved = resolve_model(slot_key="morning-briefing", data_dir=v, environ={})
    assert (resolved.model, resolved.source) == ("haiku", "config")


def test_no_config_default_falls_back_to_builtin(tmp_path: Path) -> None:
    v = _vault(tmp_path, config="user:\n  name: Alex\n")
    resolved = resolve_model(slot_key="morning-briefing", data_dir=v, environ={})
    assert (resolved.model, resolved.source) == ("opus", "builtin")


def test_env_override_wins_over_everything(tmp_path: Path) -> None:
    v = _vault(tmp_path, config="models:\n  default: haiku\n")
    resolved = resolve_model(slot_key="midday-consolidation", data_dir=v, environ={"SCOUT_MODEL": "Opus"})
    assert (resolved.model, resolved.source) == ("opus", "env")


def test_blank_env_override_is_ignored(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    resolved = resolve_model(slot_key="midday-consolidation", data_dir=v, environ={"SCOUT_MODEL": "  "})
    assert (resolved.model, resolved.source) == ("sonnet", "slot")


def test_invalid_env_override_raises(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    with pytest.raises(ConfigError, match="SCOUT_MODEL"):
        resolve_model(slot_key="midday-consolidation", data_dir=v, environ={"SCOUT_MODEL": "sonet"})


@pytest.mark.parametrize("slot_key", [None, "manual", "dreaming-manual", "no-such-slot"])
def test_slotless_or_unknown_slot_skips_the_slot_layer(tmp_path: Path, slot_key: str | None) -> None:
    v = _vault(tmp_path, config="models:\n  default: haiku\n")
    resolved = resolve_model(slot_key=slot_key, data_dir=v, environ={})
    assert (resolved.model, resolved.source) == ("haiku", "config")


def test_missing_vault_schedule_uses_shipped_defaults(tmp_path: Path) -> None:
    # The shipped schedule carries no model, so the slot layer has nothing to say.
    v = _vault(tmp_path, schedule=None, config="models:\n  default: haiku\n")
    resolved = resolve_model(slot_key="morning-briefing", data_dir=v, environ={})
    assert (resolved.model, resolved.source) == ("haiku", "config")


def test_invalid_config_default_raises(tmp_path: Path) -> None:
    v = _vault(tmp_path, config="models:\n  default: sonet\n")
    with pytest.raises(ConfigError, match="models.default"):
        resolve_model(slot_key="morning-briefing", data_dir=v, environ={})


def test_invalid_slot_model_raises_from_the_schedule_loader(tmp_path: Path) -> None:
    v = _vault(tmp_path, schedule=SLOT_YAML.replace("model: sonnet", "model: sonet"))
    with pytest.raises(ConfigError, match="midday-consolidation"):
        resolve_model(slot_key="morning-briefing", data_dir=v, environ={})


def test_default_model_reports_config_or_builtin(tmp_path: Path) -> None:
    v = _vault(tmp_path, config="models:\n  default: Sonnet\n")
    assert default_model(v).model == "sonnet"
    assert default_model(v).source == "config"


def test_non_mapping_models_block_raises(tmp_path: Path) -> None:
    v = _vault(tmp_path, config="models: sonnet\n")
    with pytest.raises(ConfigError, match="models"):
        default_model(v)
