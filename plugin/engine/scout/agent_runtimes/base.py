"""The agent-runtime interface: one adapter per local agent CLI Scout launches.

Spec: docs/superpowers/specs/2026-11-13-runtime-agnostic-design.md §8.2–§8.3 ("spec §n").
An *agent runtime* is a local CLI that runs a Scout session (``claude-code`` today;
``antigravity``, ``codex``, ``gemini`` later). It is not ``Slot.runtime``, which says
*where* a slot runs (local or remote) and is unchanged (spec §8.1).

Phase 1 puts Claude Code behind this interface with its launch unchanged. The members
the first non-Claude runtime needs (``gate_config``, ``hook_in``, ``hook_out``,
``parse_events``, ``session_store``) arrive with it in Phase 2, shaped by what Phase 0
observes, rather than guessed here.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable


class Capability(enum.StrEnum):
    """What a runtime can do for Scout, per CLI version (spec §8.3).

    A capability counts only when it is *verified* (for Claude Code: the gate probe
    passed for this version). Unverified means absent.
    """

    PRE_TOOL_BLOCK = "pre_tool_block"
    PRE_TOOL_FAIL_CLOSED = "pre_tool_fail_closed"
    HOOK_ENV = "hook_env"
    TOOL_COVERAGE = "tool_coverage"
    GATE_CONFIG_OUTSIDE_SESSION = "gate_config_outside_session"
    POST_TOOL_CONTEXT = "post_tool_context"
    MCP_STDIO = "mcp_stdio"
    MCP_TOOL_ALLOWLIST = "mcp_tool_allowlist"
    PATH_DENY = "path_deny"
    HEADLESS_EVENTS = "headless_events"
    USAGE_TOKENS = "usage_tokens"
    COST_ESTIMATE = "cost_estimate"
    BUDGET_CAP = "budget_cap"
    CUSTOM_ENDPOINT = "custom_endpoint"
    SKILLS = "skills"
    COMMANDS = "commands"
    CONTEXT_FILE = "context_file"


class Tier(enum.StrEnum):
    """Privacy tier, derived from the verified capabilities (spec §8.3).

    F: full gates, may run ``autonomous`` on the canonical vault. L: F without
    post-tool context (labels), still allowed, with stricter reads. S: structural,
    on paper only (Q10), never built. D: delegate mirror only.
    """

    F = "F"
    L = "L"
    S = "S"
    D = "D"


# Tier L is Tier F without post_tool_context: these six make L, and with
# post_tool_context they make F.
TIER_L_REQUIRES: frozenset[Capability] = frozenset(
    {
        Capability.PRE_TOOL_BLOCK,
        Capability.PRE_TOOL_FAIL_CLOSED,
        Capability.HOOK_ENV,
        Capability.TOOL_COVERAGE,
        Capability.GATE_CONFIG_OUTSIDE_SESSION,
        Capability.HEADLESS_EVENTS,
    }
)
TIER_F_REQUIRES: frozenset[Capability] = TIER_L_REQUIRES | {Capability.POST_TOOL_CONTEXT}
# The capabilities a hook delivers. A Claude Code whose hooks aren't proven to load
# (the gate probe failed) has none of them.
HOOK_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.PRE_TOOL_BLOCK,
        Capability.PRE_TOOL_FAIL_CLOSED,
        Capability.HOOK_ENV,
        Capability.TOOL_COVERAGE,
        Capability.GATE_CONFIG_OUTSIDE_SESSION,
        Capability.POST_TOOL_CONTEXT,
    }
)


def tier_for(caps: frozenset[Capability] | set[Capability]) -> Tier:
    """The tier a verified capability set earns (spec §8.3).

    F needs every gate capability plus post-tool context; L is F without it. S
    (``path_deny`` without the hook guarantees) is returned only when F and L fail.
    It isn't built (Q10), and ``check`` treats it like D. Anything else is D.
    """
    caps = frozenset(caps)
    if TIER_F_REQUIRES <= caps:
        return Tier.F
    if TIER_L_REQUIRES <= caps:
        return Tier.L
    if Capability.PATH_DENY in caps:
        return Tier.S
    return Tier.D


@dataclass(frozen=True)
class RunRequest:
    """One session launch, as the runner asks for it.

    ``budget_cap`` is the USD cap *as text*, exactly as the runner (or the retry
    wrapper, which rewrites it for a retry, #561) wrote it, so the launched CLI sees
    the same characters it always has. ``None`` means no cap was passed.

    ``timeout_s`` is the run timeout in awake seconds (``scout.agent_runtimes.limits``),
    worked out by whoever builds the request; ``None`` means no timeout, and ``exec``
    then replaces itself with the CLI.
    """

    vault: Path
    mode: str
    run_name: str
    prompt: str
    model: str
    budget_cap: str | None
    reader: str
    binary: Path | None = None  # the CLI the runner names; None: the adapter's locate()
    timeout_s: int | None = None


# Environment variables that carry an Anthropic credential or Claude Code's own settings, such as the
# CLAUDE_CODE_OAUTH_TOKEN the retry wrapper exports. ``exec`` removes them for every runtime whose provider
# isn't Anthropic, whatever its adapter says, so they never reach another vendor's CLI or the shells it starts
# (#652 S5). fnmatch patterns, matched case-sensitively.
ANTHROPIC_ENV: tuple[str, ...] = ("CLAUDE_*", "ANTHROPIC_*")


@dataclass(frozen=True)
class Launch:
    """What to exec: the argv (argv[0] is the binary), the environment, the working directory and stdin text.

    The CLI's environment is the runner's, minus every variable matching an ``env_unset`` pattern (fnmatch,
    case-sensitive), plus ``env``. ``cwd`` ``None`` keeps the runner's working directory (the vault).
    """

    argv: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict)
    stdin: str | None = None
    env_unset: tuple[str, ...] = ()
    cwd: Path | None = None


@dataclass(frozen=True)
class SandboxPaths:
    """What a runtime adds to the scheduled runs' OS write sandbox (``scout.sandbox.profile``).

    ``protected``: trees a run may not write, such as where the CLI reads its hooks and settings.
    ``writable``: folders under a protected tree that the CLI itself must write, such as its session store.
    A protected tree under a writable one stays protected.
    """

    protected: tuple[Path, ...] = ()
    writable: tuple[Path, ...] = ()


class FailureKind(enum.StrEnum):
    TRANSIENT = "transient"
    AUTH = "auth"
    USAGE_LIMIT = "usage_limit"
    BUDGET = "budget"
    OTHER = "other"


@dataclass(frozen=True)
class Failure:
    kind: FailureKind
    resets_at: datetime | None
    detail: str


@dataclass(frozen=True)
class ProcessMarkers:
    """How ``live_run`` tells this runtime's run process: argv words that are all present, plus env to set."""

    argv_words: tuple[str, ...]
    env: Mapping[str, str]


CostSource = Literal["reported", "priced", "unpriced"]


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    cost_source: CostSource
    model: str | None


@dataclass(frozen=True)
class RuntimeAuth:
    """Whether the runtime can sign in for a scheduled run. Never carries a credential."""

    signed_in: bool | None  # None: can't tell without starting a session
    mode: str  # e.g. "token" / "login" for Claude Code
    detail: str


@runtime_checkable
class AgentRuntime(Protocol):
    """One local agent CLI (spec §8.2), minus the Phase 2 members (see the module docstring)."""

    id: str
    display_name: str
    provider: str  # "anthropic", "openai", "google", …
    # What the CLI does whatever its hooks do, known without starting it, e.g. Claude Code's
    # --max-budget-usd (budget_cap). ``capabilities`` returns these plus whatever a probe proves.
    declared_capabilities: frozenset[Capability]
    # The skills' preamble runs inside this CLI's own interactive session, whose hooks are its own (G4),
    # so ``check`` has nothing to add for an interactive check.
    interactive_host: bool

    def locate(self, hint: Path | None) -> Path | None: ...
    def version(self, binary: Path) -> str | None: ...
    def auth_status(self, binary: Path) -> RuntimeAuth: ...
    def capabilities(self, vault: Path, binary: Path, version: str | None) -> frozenset[Capability]: ...
    def sandbox_paths(self, env: Mapping[str, str]) -> SandboxPaths: ...

    def validate_model(self, value: object, *, where: str) -> str: ...
    def default_model(self) -> str: ...
    def known_models(self) -> tuple[str, ...]: ...

    def headless_argv(self, req: RunRequest) -> Launch: ...
    def classify_failure(self, log_tail: str, exit_code: int) -> Failure: ...
    def process_markers(self, req: RunRequest | None = None) -> ProcessMarkers: ...


def caps_own_spend(runtime: AgentRuntime) -> bool:
    """The CLI enforces a spend cap itself. A runtime that doesn't gets the run timeout and the daily token
    ceiling by default (spec §8.6, ``scout.agent_runtimes.limits``). Declared, so it needs no probe."""
    return Capability.BUDGET_CAP in runtime.declared_capabilities


__all__ = [
    "ANTHROPIC_ENV",
    "HOOK_CAPABILITIES",
    "TIER_F_REQUIRES",
    "TIER_L_REQUIRES",
    "AgentRuntime",
    "Capability",
    "CostSource",
    "Failure",
    "FailureKind",
    "Launch",
    "ProcessMarkers",
    "RunRequest",
    "RuntimeAuth",
    "SandboxPaths",
    "Tier",
    "Usage",
    "caps_own_spend",
    "tier_for",
]
