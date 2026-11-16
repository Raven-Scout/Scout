"""Unit tests for scout.agent_runtimes.base and registry (spec 2026-11-13 §8.2–§8.3)."""

from __future__ import annotations

import pytest

from scout.agent_runtimes import registry
from scout.agent_runtimes.base import (
    TIER_F_REQUIRES,
    TIER_L_REQUIRES,
    AgentRuntime,
    Capability,
    Tier,
    tier_for,
)
from tests._agent_runtimes import DummyRuntime, registered

C = Capability


def test_capability_values_are_the_spec_list() -> None:
    assert {c.value for c in Capability} == {
        "pre_tool_block",
        "pre_tool_fail_closed",
        "hook_env",
        "tool_coverage",
        "gate_config_outside_session",
        "post_tool_context",
        "mcp_stdio",
        "mcp_tool_allowlist",
        "path_deny",
        "headless_events",
        "usage_tokens",
        "cost_estimate",
        "budget_cap",
        "custom_endpoint",
        "skills",
        "commands",
        "context_file",
    }


def test_tier_f_requires_all_six() -> None:
    six = {
        C.PRE_TOOL_BLOCK,
        C.PRE_TOOL_FAIL_CLOSED,
        C.HOOK_ENV,
        C.TOOL_COVERAGE,
        C.GATE_CONFIG_OUTSIDE_SESSION,
        C.HEADLESS_EVENTS,
    }
    assert TIER_L_REQUIRES == six
    assert tier_for(frozenset(six | {C.POST_TOOL_CONTEXT})) is Tier.F
    for missing in six:
        assert tier_for(frozenset((six - {missing}) | {C.POST_TOOL_CONTEXT})) is Tier.D, missing


def test_tier_l_is_f_without_post_tool_context() -> None:
    assert tier_for(TIER_L_REQUIRES) is Tier.L
    assert tier_for(TIER_F_REQUIRES) is Tier.F


def test_tier_s_needs_path_deny() -> None:
    assert tier_for(frozenset({C.PATH_DENY})) is Tier.S
    assert tier_for(frozenset({C.PATH_DENY, C.PRE_TOOL_BLOCK})) is Tier.S
    # S is only the fallback: F and L win when they hold.
    assert tier_for(TIER_F_REQUIRES | {C.PATH_DENY}) is Tier.F
    assert tier_for(TIER_L_REQUIRES | {C.PATH_DENY}) is Tier.L


def test_anything_else_is_tier_d() -> None:
    assert tier_for(frozenset()) is Tier.D
    assert tier_for(frozenset({C.SKILLS, C.BUDGET_CAP, C.POST_TOOL_CONTEXT})) is Tier.D


def test_registry_unknown_id_names_known_ids() -> None:
    with pytest.raises(KeyError) as exc:
        registry.get("grok-build")
    assert "grok-build" in str(exc.value)
    assert "claude-code" in str(exc.value)


def test_registry_has_only_claude_code_in_phase_1() -> None:
    assert registry.ids() == ("claude-code",)
    assert registry.BUILTIN_DEFAULT == "claude-code"
    claude = registry.get("claude-code")
    assert isinstance(claude, AgentRuntime)
    assert (claude.id, claude.provider) == ("claude-code", "anthropic")


def test_register_and_unregister_a_dummy() -> None:
    with registered(DummyRuntime("dummy")) as dummy:
        assert registry.ids() == ("claude-code", "dummy")
        assert registry.get("dummy") is dummy
        assert isinstance(dummy, AgentRuntime)
    assert registry.ids() == ("claude-code",)
    with pytest.raises(ValueError):
        registry.unregister("claude-code")
