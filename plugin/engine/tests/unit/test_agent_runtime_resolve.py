"""Which agent runtime and model a run uses (spec 2026-11-13 §8.7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.agent_runtimes.resolve import resolve_model_for, resolve_runtime, validate_runtime
from scout.errors import ConfigError
from scout.models import resolve_model
from scout.schedule import SlotRuntime, load_schedule
from tests._agent_runtimes import DummyRuntime, registered

SLOT = """\
  {key}:
    type: dreaming
    runner: run-dreaming.sh
    fires_at_local: "02:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 4
    on_miss: skip
    cooldown_minutes: 60
{extra}"""


def _schedule(*slots: tuple[str, str]) -> str:
    return "schema_version: 1\nslots:\n" + "".join(SLOT.format(key=k, extra=e) for k, e in slots)


def _vault(tmp_path: Path, *, schedule: str | None = None, config: str | None = None) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    if schedule is not None:
        (v / ".scout-state" / "schedule.yaml").write_text(schedule, encoding="utf-8")
    if config is not None:
        (v / "scout-config.yaml").write_text(config, encoding="utf-8")
    return v


def test_precedence_env_slot_config_builtin(tmp_path: Path) -> None:
    with registered(DummyRuntime("dummy")), registered(DummyRuntime("other")):
        v = _vault(
            tmp_path,
            schedule=_schedule(("dream", "    agent_runtime: dummy\n"), ("plain", "")),
            config="agent_runtimes:\n  default: other\n",
        )
        assert resolve_runtime(v, "dream", environ={"SCOUT_AGENT_RUNTIME": "claude-code"}).source == "env"
        assert resolve_runtime(v, "dream", environ={"SCOUT_AGENT_RUNTIME": "claude-code"}).runtime == "claude-code"
        assert resolve_runtime(v, "dream", environ={}) == resolve_runtime(
            v, "dream", environ={"SCOUT_AGENT_RUNTIME": " "}
        )
        r = resolve_runtime(v, "dream", environ={})
        assert (r.runtime, r.source) == ("dummy", "slot")
        r = resolve_runtime(v, "plain", environ={})
        assert (r.runtime, r.source) == ("other", "config")
        r = resolve_runtime(v, None, environ={})
        assert (r.runtime, r.source) == ("other", "config")
        bare = _vault(tmp_path / "b", schedule=_schedule(("plain", "")))
        r = resolve_runtime(bare, "plain", environ={})
        assert (r.runtime, r.source) == ("claude-code", "builtin")


def test_no_schedule_and_no_config_is_claude_code(tmp_path: Path) -> None:
    r = resolve_runtime(_vault(tmp_path), "morning-briefing", environ={})
    assert (r.runtime, r.source) == ("claude-code", "builtin")


def test_unknown_runtime_in_env_or_config_is_an_error_naming_it(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="SCOUT_AGENT_RUNTIME.*'codex'"):
        resolve_runtime(_vault(tmp_path), None, environ={"SCOUT_AGENT_RUNTIME": "codex"})
    v = _vault(tmp_path / "c", config="agent_runtimes:\n  default: grok\n")
    with pytest.raises(ConfigError, match="agent_runtimes.default.*'grok'"):
        resolve_runtime(v, None, environ={})
    v = _vault(tmp_path / "d", config="agent_runtimes: [claude-code]\n")
    with pytest.raises(ConfigError, match="agent_runtimes must be a mapping"):
        resolve_runtime(v, None, environ={})


def test_unknown_runtime_in_slot_is_schedule_error_naming_slot_and_value(tmp_path: Path) -> None:
    v = _vault(tmp_path, schedule=_schedule(("night-dream", "    agent_runtime: antigravity\n")))
    with pytest.raises(ConfigError) as exc:
        load_schedule(v / ".scout-state" / "schedule.yaml")
    assert "night-dream" in str(exc.value)
    assert "antigravity" in str(exc.value)


def test_model_must_fit_slot_runtime(tmp_path: Path) -> None:
    with registered(DummyRuntime("dummy")):
        ok = _vault(tmp_path, schedule=_schedule(("d", "    agent_runtime: dummy\n    model: dm-fast\n")))
        slot = load_schedule(ok / ".scout-state" / "schedule.yaml")["d"]
        assert (slot.agent_runtime, slot.model) == ("dummy", "dm-fast")
        bad = _vault(tmp_path / "b", schedule=_schedule(("d", "    agent_runtime: dummy\n    model: sonnet\n")))
        with pytest.raises(ConfigError) as exc:
            load_schedule(bad / ".scout-state" / "schedule.yaml")
        assert "'d'" in str(exc.value)
        assert "sonnet" in str(exc.value)
        # A Claude model on a slot without an agent_runtime is checked by the Claude rule, as before.
        claude = _vault(tmp_path / "c", schedule=_schedule(("d", "    model: dm-fast\n")))
        with pytest.raises(ConfigError, match="dm-fast"):
            load_schedule(claude / ".scout-state" / "schedule.yaml")


def test_claude_model_resolution_is_models_resolve_model(tmp_path: Path) -> None:
    v = _vault(
        tmp_path,
        schedule=_schedule(("d", "    model: sonnet\n"), ("e", "")),
        config="models:\n  default: haiku\n",
    )
    for slot in ("d", "e", None):
        for env in ({}, {"SCOUT_MODEL": "claude-opus-5-5"}):
            assert resolve_model_for(v, slot, "claude-code", environ=env) == resolve_model(
                slot_key=slot, data_dir=v, environ=env
            )


def test_other_runtime_model_precedence(tmp_path: Path) -> None:
    with registered(DummyRuntime("dummy")):
        v = _vault(
            tmp_path,
            schedule=_schedule(("d", "    agent_runtime: dummy\n    model: dm-slot\n"), ("e", "")),
            config="models:\n  default: haiku\nagent_runtimes:\n  dummy:\n    model: dm-config\n",
        )
        assert resolve_model_for(v, "d", "dummy", environ={"SCOUT_MODEL": "dm-env"}).model == "dm-env"
        assert resolve_model_for(v, "d", "dummy", environ={}).model == "dm-slot"
        # models.default (haiku) is Claude Code's, never another runtime's.
        assert resolve_model_for(v, "e", "dummy", environ={}).model == "dm-config"
        bare = _vault(tmp_path / "b")
        r = resolve_model_for(bare, None, "dummy", environ={})
        assert (r.model, r.source) == ("dm-default", "builtin")
        with pytest.raises(ConfigError, match="SCOUT_MODEL"):
            resolve_model_for(v, "d", "dummy", environ={"SCOUT_MODEL": "opus"})


def test_validate_runtime_normalises() -> None:
    assert validate_runtime(" Claude-Code ", where="x") == "claude-code"
    for bad in ("", "claude", None, 3):
        with pytest.raises(ConfigError):
            validate_runtime(bad, where="x")


def test_slot_runtime_local_remote_field_untouched(tmp_path: Path) -> None:
    v = _vault(tmp_path, schedule=_schedule(("d", "    runtime: local\n    agent_runtime: claude-code\n")))
    slot = load_schedule(v / ".scout-state" / "schedule.yaml")["d"]
    assert slot.runtime is SlotRuntime.LOCAL
    assert slot.agent_runtime == "claude-code"
    v = _vault(tmp_path / "b", schedule=_schedule(("d", "")))
    assert load_schedule(v / ".scout-state" / "schedule.yaml")["d"].agent_runtime is None


def test_snapshot_fields_unchanged() -> None:
    """agent_runtime stays out of schedule.snapshot.json while the seeded defaults carry none (spec §8.7)."""
    from scout.scripts.schedule_snapshot import build_snapshot, canonical_snapshot_path

    built = build_snapshot()
    assert "agent_runtime" not in json.dumps(built)
    shipped = json.loads(canonical_snapshot_path().read_text(encoding="utf-8"))
    built.pop("generated_from", None)
    shipped.pop("generated_from", None)
    assert built == shipped


def test_validate_model_dispatches_on_runtime() -> None:
    from scout.models import validate_model

    assert validate_model("Sonnet", where="x") == "sonnet"
    assert validate_model("Sonnet", where="x", runtime="claude-code") == "sonnet"
    with registered(DummyRuntime("dummy")):
        assert validate_model("DM-x", where="x", runtime="dummy") == "dm-x"
        with pytest.raises(ConfigError, match="dm-"):
            validate_model("sonnet", where="x", runtime="dummy")
    with pytest.raises(ConfigError, match="x: unknown agent runtime 'nope'"):
        validate_model("sonnet", where="x", runtime="nope")
