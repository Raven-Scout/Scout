"""Which agent runtime, and which model on it, a scheduled run uses (spec §8.7).

Runtime precedence, first match wins: ``SCOUT_AGENT_RUNTIME``, the slot's
``agent_runtime:`` in ``.scout-state/schedule.yaml``, ``agent_runtimes.default`` in
``scout-config.yaml``, then ``claude-code``.

Model precedence is ``scout.models``' unchanged for Claude Code. For any other
runtime: ``SCOUT_MODEL``, the slot's ``model:``, ``agent_runtimes.<id>.model``, then
the adapter's ``default_model()``; ``models.default`` applies to Claude Code only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from scout import models, paths
from scout.agent_runtimes import registry
from scout.errors import ConfigError

RuntimeSource = Literal["env", "slot", "config", "builtin"]


@dataclass(frozen=True)
class ResolvedRuntime:
    runtime: str
    source: RuntimeSource


def validate_runtime(value: object, *, where: str) -> str:
    """``value`` as a registered runtime id (stripped, lower case), or ConfigError naming *where* and the value."""
    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in registry.ids():
            return normalised
    raise ConfigError(f"{where}: agent runtime {value!r} is not one of {', '.join(registry.ids())}")


def runtimes_config(data_dir: Path | None = None) -> Mapping[str, Any]:
    """scout-config.yaml's ``agent_runtimes`` block ({} when absent). A wrong shape is a ConfigError."""
    from scout.config import load_config

    block = load_config(data_dir).get("agent_runtimes")
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ConfigError(f"scout-config.yaml: agent_runtimes must be a mapping, got {block!r}")
    for key, value in block.items():
        if key != "default" and value is not None and not isinstance(value, dict):
            raise ConfigError(f"scout-config.yaml: agent_runtimes.{key} must be a mapping, got {value!r}")
    return block


def slot_runtimes(data_dir: Path | None = None) -> dict[str, str | None]:
    """Each slot's own ``agent_runtime:`` (None when it has none), from the vault schedule or the shipped one."""
    from scout.schedule import load_default_schedule, load_schedule

    vault_schedule = paths.state_dir(data_dir) / "schedule.yaml"
    schedule = load_schedule(vault_schedule) if vault_schedule.exists() else load_default_schedule()
    return {key: slot.agent_runtime for key, slot in schedule.items()}


def default_runtime(data_dir: Path | None = None) -> ResolvedRuntime:
    """``agent_runtimes.default``, else ``claude-code``."""
    value = runtimes_config(data_dir).get("default")
    if value is None:
        return ResolvedRuntime(registry.BUILTIN_DEFAULT, "builtin")
    return ResolvedRuntime(validate_runtime(value, where="scout-config.yaml agent_runtimes.default"), "config")


def resolve_runtime(
    data_dir: Path | None,
    slot_key: str | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> ResolvedRuntime:
    """The runtime a run should use, and which setting chose it. Raises ConfigError."""
    env = os.environ if environ is None else environ
    override = env.get(registry.ENV_VAR, "")
    if override.strip():
        return ResolvedRuntime(validate_runtime(override, where=registry.ENV_VAR), "env")
    if slot_key and (runtime := slot_runtimes(data_dir).get(slot_key)):
        return ResolvedRuntime(runtime, "slot")
    return default_runtime(data_dir)


def resolve_model_for(
    data_dir: Path | None,
    slot_key: str | None,
    runtime: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> models.ResolvedModel:
    """The model for a run on *runtime*. For ``claude-code`` this is exactly ``models.resolve_model``."""
    if runtime == registry.BUILTIN_DEFAULT:
        return models.resolve_model(slot_key=slot_key, data_dir=data_dir, environ=environ)
    adapter = registry.get(runtime)
    env = os.environ if environ is None else environ
    override = env.get(models.ENV_VAR, "")
    if override.strip():
        return models.ResolvedModel(adapter.validate_model(override, where=models.ENV_VAR), "env")
    if slot_key and (model := models.slot_models(data_dir).get(slot_key)):
        # A slot whose runtime came from the env or the config was loaded under the Claude rule; check it here.
        return models.ResolvedModel(adapter.validate_model(model, where=f"slot {slot_key!r}"), "slot")
    block = runtimes_config(data_dir).get(runtime) or {}
    if (value := block.get("model")) is not None:
        return models.ResolvedModel(
            adapter.validate_model(value, where=f"scout-config.yaml agent_runtimes.{runtime}.model"), "config"
        )
    return models.ResolvedModel(adapter.default_model(), "builtin")


__all__ = [
    "ResolvedRuntime",
    "default_runtime",
    "resolve_model_for",
    "resolve_runtime",
    "runtimes_config",
    "slot_runtimes",
    "validate_runtime",
]
