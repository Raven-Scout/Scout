"""The agent runtimes Scout knows, by id (spec §8.2).

Phase 1 registers only ``claude-code``. A runtime gets an adapter only once it can
be probed (spec §14 Q11), so this list stays short on purpose.
"""

from __future__ import annotations

from collections.abc import Callable

from scout.agent_runtimes.base import AgentRuntime

BUILTIN_DEFAULT = "claude-code"
ENV_VAR = "SCOUT_AGENT_RUNTIME"


def _claude_code() -> AgentRuntime:
    from scout.agent_runtimes.claude_code import ClaudeCode

    return ClaudeCode()


# id -> factory, so importing the registry doesn't import every adapter.
_FACTORIES: dict[str, Callable[[], AgentRuntime]] = {BUILTIN_DEFAULT: _claude_code}
_INSTANCES: dict[str, AgentRuntime] = {}


def ids() -> tuple[str, ...]:
    """Every registered runtime id, sorted."""
    return tuple(sorted(_FACTORIES))


def get(runtime_id: str) -> AgentRuntime:
    """The adapter for *runtime_id*. ``KeyError`` names the known ids."""
    if runtime_id not in _FACTORIES:
        raise KeyError(f"unknown agent runtime {runtime_id!r}; known: {', '.join(ids())}")
    if runtime_id not in _INSTANCES:
        _INSTANCES[runtime_id] = _FACTORIES[runtime_id]()
    return _INSTANCES[runtime_id]


def register(adapter: AgentRuntime) -> None:
    """Add an adapter instance (tests register dummies this way; see ``unregister``)."""
    _FACTORIES[adapter.id] = lambda: adapter
    _INSTANCES[adapter.id] = adapter


def unregister(runtime_id: str) -> None:
    """Remove an adapter added with ``register``. The built-in default can't be removed."""
    if runtime_id == BUILTIN_DEFAULT:
        raise ValueError(f"{BUILTIN_DEFAULT} is built in")
    _FACTORIES.pop(runtime_id, None)
    _INSTANCES.pop(runtime_id, None)


__all__ = ["BUILTIN_DEFAULT", "ENV_VAR", "get", "ids", "register", "unregister"]
