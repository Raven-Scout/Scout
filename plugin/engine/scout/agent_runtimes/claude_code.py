"""The ``claude-code`` adapter: today's scheduled-run launch, behind the interface.

Nothing here changes how a run starts. ``headless_argv`` is the runners' launch line
(``--permission-mode auto --model M --max-budget-usd N --name R -p PROMPT``, spec G4),
and a golden test pins it. ``classify_failure`` uses the retry wrapper's own patterns
(``templates/scripts/claude-with-retry.sh.tmpl``), and a test reads them out of that
file so the two can't drift.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from scout import models
from scout.agent_runtimes.base import (
    HOOK_CAPABILITIES,
    Capability,
    Failure,
    FailureKind,
    Launch,
    ProcessMarkers,
    RunRequest,
    RuntimeAuth,
    SandboxPaths,
)

# Copied from claude-with-retry.sh.tmpl (grep -E there; the same text is a valid
# Python regex). test_agent_runtime_claude_code reads the template and fails if these drift.
TRANSIENT_PATTERNS = (
    r"socket connection was closed unexpectedly|Unable to connect to API \(ECONNRESET\)|"
    r"Unable to connect to API \(ConnectionRefused\)|Unable to connect to API \(ENOTFOUND\)|"
    r"Unable to connect to API \(FailedToOpenSocket\)|Can.t reach the API server|Stream idle timeout|"
    r"Connection closed mid-response|went to sleep mid-response|529 Overloaded|^API Error: 500 |"
    r"API Error: 403 Attention Required! \| Cloudflare|"
    r"^(Error: )?(Claude Code )?couldn.{1,3}t verify your sign-in with Anthropic yet|"
    r"^(Error: )?Claude Code .{0,80}sign-in token was still being refreshed"
)
AUTH_PATTERNS = (
    r"^(Failed to authenticate([.:]|$)|Invalid authentication credentials|API Error: 40[13]|"
    r"OAuth (session|token)( has)? expired|Not logged in)"
)
USAGE_LIMIT_PATTERN = r"limit.{0,20}resets"
# The CLI's message when --max-budget-usd stops a session (health.py and run-outcome.sh match it too).
BUDGET_PATTERN = r"Exceeded USD budget"

_TRANSIENT_RE = re.compile(TRANSIENT_PATTERNS, re.MULTILINE)
_AUTH_RE = re.compile(AUTH_PATTERNS, re.MULTILINE)
_USAGE_LIMIT_RE = re.compile(USAGE_LIMIT_PATTERN, re.IGNORECASE)
_BUDGET_RE = re.compile(BUDGET_PATTERN, re.IGNORECASE)

# Everything Claude Code does for Scout once its hooks are proven to load. Not
# path_deny (Scout's OS sandbox is Scout's, not the runtime's) and not
# mcp_tool_allowlist (restrict_tools is enforced by Scout's hook, i.e. pre_tool_block).
FULL_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.PRE_TOOL_BLOCK,
        Capability.PRE_TOOL_FAIL_CLOSED,
        Capability.HOOK_ENV,
        Capability.TOOL_COVERAGE,
        Capability.GATE_CONFIG_OUTSIDE_SESSION,
        Capability.POST_TOOL_CONTEXT,
        Capability.MCP_STDIO,
        Capability.HEADLESS_EVENTS,
        Capability.USAGE_TOKENS,
        Capability.COST_ESTIMATE,
        Capability.BUDGET_CAP,
        Capability.CUSTOM_ENDPOINT,
        Capability.SKILLS,
        Capability.COMMANDS,
        Capability.CONTEXT_FILE,
    }
)
# What Claude Code does whatever its hooks do: everything above but what the gate probe has to prove.
DECLARED_CAPABILITIES: frozenset[Capability] = FULL_CAPABILITIES - HOOK_CAPABILITIES

# The argv words live_run.py and kb/hook.py look for in a run's claude process (#595).
MARKER_WORDS: tuple[str, ...] = ("--name", "-p", "--max-budget-usd")
# Claude Code otherwise retitles its process "claude", hiding the argv from ps.
MARKER_ENV: dict[str, str] = {"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"}


class ClaudeCode:
    id = "claude-code"
    display_name = "Claude Code"
    provider = "anthropic"
    declared_capabilities = DECLARED_CAPABILITIES  # includes budget_cap: --max-budget-usd
    interactive_host = True  # the skills run inside the session already running, under its own hooks

    # -- the binary ---------------------------------------------------------

    def locate(self, hint: Path | None) -> Path | None:
        from scout.scripts.bootstrap import resolve_claude_bin

        found = Path(resolve_claude_bin(str(hint) if hint else ""))
        return found if os.access(found, os.X_OK) else None

    def version(self, binary: Path) -> str | None:
        from scout.agent_runtimes.gate_probe import claude_version

        return claude_version(binary)

    def auth_status(self, binary: Path) -> RuntimeAuth:
        from scout import claude_auth

        st = claude_auth.status()
        if st.mode == "token":
            ok = not st.problems and (st.days_left is None or st.days_left >= 0)
            return RuntimeAuth(signed_in=ok, mode="token", detail=st.expiry_text() or st.location)
        # The interactive login: only a session can tell whether it still works.
        return RuntimeAuth(signed_in=None, mode="login", detail=st.location)

    def capabilities(self, vault: Path, binary: Path, version: str | None) -> frozenset[Capability]:
        """The full set when the gate probe passes for this claude, else the set without the hook capabilities.

        Uses the runners' own probe cache (``scoutctl gates probe --cached``), which re-probes, free and
        locally, when the version, hooks, flags or settings changed (spec §8.3 rule 4). Without the
        hooks the tier is D, so ``check`` refuses a run on the canonical vault.
        """
        from scout.agent_runtimes import gate_probe

        try:
            result = gate_probe.cached_probe(vault, binary, plugin_root=gate_probe.default_plugin_root())
        except Exception:  # noqa: BLE001 - a probe that can't run proves nothing
            return DECLARED_CAPABILITIES
        if result.ok and (version is None or result.claude_version == version):
            return FULL_CAPABILITIES
        return DECLARED_CAPABILITIES

    def sandbox_paths(self, env: Mapping[str, str]) -> SandboxPaths:
        """Nothing extra: ~/.claude and the vault's .claude/ are in the sandbox's own list (``kernel_trees``)."""
        return SandboxPaths()

    # -- models -------------------------------------------------------------

    def validate_model(self, value: object, *, where: str) -> str:
        return models.validate_model(value, where=where)

    def default_model(self) -> str:
        return models.BUILTIN_DEFAULT

    def known_models(self) -> tuple[str, ...]:
        return models.ALIASES

    # -- a run ----------------------------------------------------------------

    def headless_argv(self, req: RunRequest) -> Launch:
        """Today's runner launch, word for word. The cap is passed through as text, never re-formatted."""
        binary = req.binary or self.locate(None)
        if binary is None:
            raise FileNotFoundError("claude not found (pass --bin, or install Claude Code)")
        argv: list[str] = [str(binary), "--permission-mode", "auto", "--model", req.model]
        if req.budget_cap is not None:
            argv += ["--max-budget-usd", req.budget_cap]
        argv += ["--name", req.run_name, "-p", req.prompt]
        return Launch(argv=tuple(argv), env=dict(MARKER_ENV), stdin=None)

    def classify_failure(self, log_tail: str, exit_code: int) -> Failure:
        """The retry wrapper's classification, in its order: transient, then auth, then usage limit."""
        if exit_code == 0:
            return Failure(FailureKind.OTHER, None, "exit 0")
        if m := _TRANSIENT_RE.search(log_tail):
            return Failure(FailureKind.TRANSIENT, None, m.group(0))
        if m := _AUTH_RE.search(log_tail):
            return Failure(FailureKind.AUTH, None, m.group(0))
        limit_lines = [line for line in log_tail.splitlines() if _USAGE_LIMIT_RE.search(line)]
        if limit_lines:
            from scout.scripts.rate_limit_reset import find_reset

            line = limit_lines[-1]
            return Failure(FailureKind.USAGE_LIMIT, find_reset(line, now=datetime.now(UTC).astimezone()), line)
        if m := _BUDGET_RE.search(log_tail):
            return Failure(FailureKind.BUDGET, None, m.group(0))
        return Failure(FailureKind.OTHER, None, f"exit {exit_code}")

    def process_markers(self, req: RunRequest | None = None) -> ProcessMarkers:
        return ProcessMarkers(argv_words=MARKER_WORDS, env=dict(MARKER_ENV))


__all__ = [
    "AUTH_PATTERNS",
    "BUDGET_PATTERN",
    "DECLARED_CAPABILITIES",
    "FULL_CAPABILITIES",
    "MARKER_ENV",
    "MARKER_WORDS",
    "TRANSIENT_PATTERNS",
    "USAGE_LIMIT_PATTERN",
    "ClaudeCode",
]
