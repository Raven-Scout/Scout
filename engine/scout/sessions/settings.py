"""The ``agent_sessions`` config block (spec §4.11)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from scout import config as scout_config

_INT_FIELDS = (
    "stale_after_days",
    "running_window_seconds",
    "pr_refresh_minutes",
    "pr_fetch_cap",
    "transcript_window_days",
    "done_visible_hours",
    "render_max_per_bucket",
)
_STR_FIELDS = ("desktop_support_dir", "claude_home")


@dataclass(frozen=True)
class AgentSessionsSettings:
    stale_after_days: int = 3
    running_window_seconds: int = 120
    pr_refresh_minutes: int = 10
    pr_fetch_cap: int = 25
    transcript_window_days: int = 14
    done_visible_hours: int = 24
    render_max_per_bucket: int = 15
    use_gh: bool = True
    desktop_support_dir: str | None = None
    claude_home: str | None = None

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> AgentSessionsSettings:
        """Build from a merged config dict. Bad values fall back to the default
        with a one-line stderr warning — a mangled vault file must never block a run."""
        block = cfg.get("agent_sessions")
        if not isinstance(block, dict):
            return cls()
        out = cls()
        for name in _INT_FIELDS:
            if name not in block:
                continue
            try:
                value = int(block[name])
            except (TypeError, ValueError):
                _warn(f"agent_sessions.{name}: expected an integer, got {block[name]!r} — using default")
                continue
            if value < 0:
                _warn(f"agent_sessions.{name}: must be >= 0, got {value} — using default")
                continue
            out = replace(out, **cast(dict[str, Any], {name: value}))
        if "use_gh" in block:
            out = replace(out, use_gh=bool(block["use_gh"]))
        for name in _STR_FIELDS:
            raw = block.get(name)
            if isinstance(raw, str) and raw.strip():
                out = replace(out, **cast(dict[str, Any], {name: raw.strip()}))
        return out


def load_settings(data_dir: Path | None = None) -> AgentSessionsSettings:
    return AgentSessionsSettings.from_config(scout_config.load_config(data_dir))


def _warn(msg: str) -> None:
    print(f"scout-config: {msg}", file=sys.stderr)


__all__ = ["AgentSessionsSettings", "load_settings"]
