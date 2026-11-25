"""May this runtime run Scout here? The refusal rule (spec 2026-11-13 §8.3, §8.4, §8.6).

``check`` returns ``None`` to allow, or a ``Refusal``. Codes, in the order they are tried:

- ``version_unverified``: the binary is missing, or its version can't be read (rule 4).
- ``tier_too_low``: a run on the canonical vault needs Tier F or L (rule 1). Tier S
  isn't built (Q10) and Tier D is delegate-mirror-only (rule 2), so both are refused.
- ``restrict_tools_unenforceable``: the vault's custom connectors use ``restrict_tools``
  and the runtime has neither ``pre_tool_block`` nor ``mcp_tool_allowlist`` (rule 1).
- ``provider_not_consented``: the runtime's provider isn't one the owner consented
  to (rule 3, §8.4).
- ``local_only_unenforced``: a non-Anthropic run on the vault, before the read gate
  can keep local-only paths from it (§8.4; lifted in Phase 2).
- ``token_ceiling``: the runtime used its daily token ceiling (§8.6). The runner
  records it as ``skipped_budget``, not as a refused runtime.
- ``check_failed``: anything above couldn't be evaluated (rule 5). Fail closed.

Each rule reads only what it needs, so for Claude Code, which caps its own spend and
whose provider is Anthropic, a scheduled-run check is the gate probe the runner has
just passed and nothing more: no new way for today's runs to be skipped.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from scout.agent_runtimes import consent, limits
from scout.agent_runtimes.base import AgentRuntime, Capability, Tier, tier_for

EXIT_ALLOW = 0
EXIT_REFUSE = 3
EXIT_BUDGET = 4  # the runner records skipped_budget

REFUSAL_CODES = (
    "version_unverified",
    "tier_too_low",
    "restrict_tools_unenforceable",
    "provider_not_consented",
    "local_only_unenforced",
    "token_ceiling",
    "check_failed",
)


@dataclass(frozen=True)
class Refusal:
    code: str
    reason: str

    @property
    def exit_code(self) -> int:
        return EXIT_BUDGET if self.code == "token_ceiling" else EXIT_REFUSE


@dataclass(frozen=True)
class CheckReport:
    runtime: str
    provider: str
    binary: str | None
    version: str | None
    tier: str | None
    refusal: Refusal | None


def _uses_restrict_tools(vault: Path) -> bool:
    from scout.custom_connectors import tool_gate

    return bool(tool_gate(vault))


def evaluate(
    vault: Path,
    runtime: AgentRuntime,
    *,
    reader: str,
    mode: str | None,
    interactive: bool,
    binary: Path | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> CheckReport:
    """Run the refusal rule and say what it saw. Never raises: an error is ``check_failed``."""
    version: str | None = None
    tier: Tier | None = None
    found: Path | None = None
    try:
        # The skills' preamble runs inside the host CLI. For a runtime that hosts them (Claude Code)
        # that is the session already running, and its hooks are its own (hooks.json, G4).
        if interactive and runtime.interactive_host:
            return CheckReport(runtime.id, runtime.provider, None, None, None, None)

        found = runtime.locate(binary)
        if found is None:
            return _report(runtime, None, None, None, "version_unverified", f"{runtime.display_name} isn't installed")
        version = runtime.version(found)
        if version is None:
            return _report(
                runtime, found, None, None, "version_unverified", f"can't read the {runtime.display_name} version"
            )

        caps = runtime.capabilities(vault, found, version)
        tier = tier_for(caps)
        if tier not in (Tier.F, Tier.L):
            return _report(
                runtime,
                found,
                version,
                tier,
                "tier_too_low",
                f"{runtime.display_name} {version} is Tier {tier.value}: Scout's privacy gates aren't proven to "
                f"load in it, so it may not read this vault (a run on the vault needs Tier F or L)",
            )

        if not caps & {Capability.PRE_TOOL_BLOCK, Capability.MCP_TOOL_ALLOWLIST} and _uses_restrict_tools(vault):
            return _report(
                runtime,
                found,
                version,
                tier,
                "restrict_tools_unenforceable",
                "this vault's custom connectors use restrict_tools, which this runtime can't enforce",
            )

        if runtime.provider != consent.ALWAYS:
            if not consent.is_consented(vault, runtime.provider, env=env):
                return _report(
                    runtime,
                    found,
                    version,
                    tier,
                    "provider_not_consented",
                    f"the owner hasn't consented to sending this vault to {runtime.provider} "
                    f"(scoutctl privacy consent-provider {runtime.provider})",
                )
            # Phase 1 has no read-gate exclusion yet; Phase 2 lifts this with it.
            return _report(
                runtime,
                found,
                version,
                tier,
                "local_only_unenforced",
                f"local-only paths can't yet be kept from a {runtime.provider} run, so it may not read this vault",
            )

        if not interactive:
            lim = limits.limits_for(vault, runtime, mode)
            if (why := limits.over_ceiling(lim, vault, now=now)) is not None:
                return _report(runtime, found, version, tier, "token_ceiling", why)
    except Exception as e:  # noqa: BLE001 - rule 5: a check that can't run refuses
        why = f"the check couldn't run: {type(e).__name__}: {e}"
        return _report(runtime, found, version, tier, "check_failed", why)
    return CheckReport(runtime.id, runtime.provider, str(found), version, tier.value, None)


def _report(
    runtime: AgentRuntime, binary: Path | None, version: str | None, tier: Tier | None, code: str, reason: str
) -> CheckReport:
    return CheckReport(
        runtime.id,
        runtime.provider,
        str(binary) if binary else None,
        version,
        tier.value if tier else None,
        Refusal(code, reason),
    )


def check(
    vault: Path,
    runtime: AgentRuntime,
    *,
    reader: str,
    mode: str | None,
    interactive: bool,
    binary: Path | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> Refusal | None:
    """The refusal, or None to allow (plan Task 1.4's signature)."""
    return evaluate(
        vault, runtime, reader=reader, mode=mode, interactive=interactive, binary=binary, env=env, now=now
    ).refusal


__all__ = ["EXIT_ALLOW", "EXIT_BUDGET", "EXIT_REFUSE", "REFUSAL_CODES", "CheckReport", "Refusal", "check", "evaluate"]
