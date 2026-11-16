"""Which Claude model a scheduled Scout run uses.

A run's model comes from, first match wins:

  1. ``SCOUT_MODEL`` in the environment (a manual override),
  2. the slot's ``model:`` in the vault's ``.scout-state/schedule.yaml``, when the
     run names a slot (``SCOUT_FORCE_MODE``, which the runners call ``MODE``),
  3. ``models.default`` in the vault's ``scout-config.yaml``,
  4. ``opus``, the model every run used before models were configurable.

Runs started without a slot (the heartbeat, ``/scout-briefing``) skip step 2.
The runners ask ``scoutctl model resolve --slot "$MODE"`` and pass the answer to
``claude --model``. See docs/superpowers/specs/2026-10-13-slot-model-selection-design.md.

A model is one of the Claude Code aliases (``opus``, ``sonnet``, ``haiku``),
which follow the latest model in each family, or a full ``claude-…`` model ID
for a user who wants to pin a version. Full IDs are only syntax-checked: Scout
keeps no catalogue of model IDs to go stale.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scout import paths
from scout.errors import ConfigError

ALIASES: tuple[str, ...] = ("opus", "sonnet", "haiku")
BUILTIN_DEFAULT = "opus"
ENV_VAR = "SCOUT_MODEL"

_FULL_ID_RE = re.compile(r"^claude-[a-z0-9][a-z0-9.\-]*$")


@dataclass(frozen=True)
class ResolvedModel:
    model: str
    source: str  # env | slot | config | builtin


def validate_model(value: object, *, where: str) -> str:
    """Return ``value`` normalised (stripped, lower case), or raise ConfigError.

    ``where`` names the setting in the message, e.g. ``slot 'midday-consolidation'``.
    """
    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in ALIASES or _FULL_ID_RE.match(normalised):
            return normalised
    raise ConfigError(
        f"{where}: model {value!r} is not {', '.join(ALIASES[:-1])} or {ALIASES[-1]}, "
        "or a full claude-… model ID (e.g. claude-sonnet-5-5)"
    )


def default_model(data_dir: Path | None = None) -> ResolvedModel:
    """The model for a run whose slot names none: ``models.default``, else opus."""
    from scout.config import load_config

    models = load_config(data_dir).get("models")
    if models is None:
        return ResolvedModel(BUILTIN_DEFAULT, "builtin")
    if not isinstance(models, dict):
        raise ConfigError(f"scout-config.yaml: models must be a mapping with a default: key, got {models!r}")
    value = models.get("default")
    if value is None:
        return ResolvedModel(BUILTIN_DEFAULT, "builtin")
    return ResolvedModel(validate_model(value, where="scout-config.yaml models.default"), "config")


def slot_models(data_dir: Path | None = None) -> dict[str, str | None]:
    """Each slot's own ``model:`` (None when it has none), from the vault schedule.

    Falls back to the shipped schedule when the vault has none, as the
    dispatcher does. Raises ConfigError when the schedule does not load.
    """
    from scout.schedule import load_default_schedule, load_schedule

    vault_schedule = paths.state_dir(data_dir) / "schedule.yaml"
    schedule = load_schedule(vault_schedule) if vault_schedule.exists() else load_default_schedule()
    return {key: slot.model for key, slot in schedule.items()}


def resolve_model(
    *,
    slot_key: str | None,
    data_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ResolvedModel:
    """The model a run should use, and which setting chose it. Raises ConfigError."""
    env = os.environ if environ is None else environ
    override = env.get(ENV_VAR, "")
    if override.strip():
        return ResolvedModel(validate_model(override, where=ENV_VAR), "env")
    if slot_key and (model := slot_models(data_dir).get(slot_key)):
        return ResolvedModel(model, "slot")
    return default_model(data_dir)


__all__ = [
    "ALIASES",
    "BUILTIN_DEFAULT",
    "ENV_VAR",
    "ResolvedModel",
    "default_model",
    "resolve_model",
    "slot_models",
    "validate_model",
]
