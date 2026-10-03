"""Headless connector detection for Scout.app onboarding (spec E4).

Replaces the LLM-mediated probe loop in /scout-setup: ``bash`` probes run
directly; ``mcp_tool`` probes are answered by ``claude mcp list``, whose one
line per server carries a status glyph. Detection is a hint the user confirms
in the UI — anything we cannot map is ``unknown``, never ``unavailable``.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from scout.scripts.connector_probes import Probe, ProbeKind


class DetectStatus(Enum):
    CONNECTED = "connected"
    NEEDS_AUTH = "needs_auth"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Detection:
    connector: str
    status: DetectStatus
    needs_user_input: list[str]
    evidence: str


# One `claude mcp list` server line: "<name>: <target> - <glyph> <text>".
# <name> runs to the first ": " (colon-space) so plugin-scoped names like
# "plugin:linear:linear" (whose own colons have no trailing space) survive;
# <glyph>'s status is the *last* " - <glyph>" so a "text" tail containing
# " - " or embedded colons (e.g. "CONNECTION_CLOSED: Connection closed")
# does not get mistaken for the name/target split.
# Banner lines and "[mcp-sdk] …" warnings have no " - <glyph>" and so do not
# match and are ignored.
_LINE = re.compile(r"^(?P<name>.+?): (?P<target>.*) - (?P<glyph>[✔✓!✘✗⏸])\s*(?P<text>.*)$")
_GLYPH = {
    "✔": DetectStatus.CONNECTED,
    "✓": DetectStatus.CONNECTED,
    "!": DetectStatus.NEEDS_AUTH,
    "✘": DetectStatus.UNAVAILABLE,
    "✗": DetectStatus.UNAVAILABLE,
    "⏸": DetectStatus.NEEDS_AUTH,  # "Pending approval" needs a user action, like auth.
}

# Shell exit codes for "command not executable" (126) and "command not found" (127).
_NOT_RUNNABLE = frozenset({126, 127})


def parse_mcp_list(text: str) -> dict[str, tuple[DetectStatus, str]]:
    """Server display name → (status, the raw line it came from)."""
    out: dict[str, tuple[DetectStatus, str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        m = _LINE.match(line)
        if m:
            out[m["name"].strip()] = (_GLYPH[m["glyph"]], line)
    return out


def server_slug(display_name: str) -> str:
    """Claude Code's tool-name segment for a server: runs of non-alphanumerics → '_'.

    Observed: tool ``mcp__claude_ai_Google_Calendar__list_calendars`` belongs to
    the server listed as ``claude.ai Google Calendar``.
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", display_name).strip("_")


def tool_server_slug(tool: str) -> str | None:
    """``mcp__<server>__<tool>`` → ``<server>``; None for anything else."""
    if not tool.startswith("mcp__"):
        return None
    slug, sep, _tool = tool[len("mcp__") :].rpartition("__")
    return slug if sep else None


def _normalize(s: str) -> str:
    """Lowercase; collapse runs of non-alphanumerics to '_'; strip leading/trailing '_'.

    Lets a tool-chain segment and a `claude mcp list` display name compare
    equal regardless of which separator (':', '-', '.', '_') either side
    uses — e.g. ``plugin:kbl-ui-platform:validate-ui`` (display name) and
    ``plugin_kbl-ui-platform_validate-ui`` (tool-name segment, hyphens kept)
    both normalize to ``plugin_kbl_ui_platform_validate_ui``.
    """
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _match_server(tool: str, servers: dict[str, tuple[DetectStatus, str]]) -> tuple[DetectStatus, str] | None:
    """Match a probe's tool-chain entry to a `claude mcp list` server line.

    Compares a normalized form of both sides (see `_normalize`) instead of
    `server_slug`'s exact/case-insensitive lookup, so a hyphenated plugin
    server (whose tool-name segment keeps the hyphen, e.g.
    ``plugin_example-kit_search-tool``) matches its display name
    (``plugin:example-kit:search-tool``).
    """
    want = tool_server_slug(tool)
    if not want:
        return None
    key = _normalize(want)
    by_key: dict[str, tuple[DetectStatus, str]] = {}
    for name, status in servers.items():
        norm = _normalize(name)
        if norm not in by_key:  # first listed server wins on a normalization collision
            by_key[norm] = status
    return by_key.get(key)


def detect(
    registry: dict[str, Probe],
    *,
    mcp_list_output: str | None,
    run_bash: Callable[[str], int],
) -> dict[str, Detection]:
    servers = parse_mcp_list(mcp_list_output) if mcp_list_output is not None else None
    out: dict[str, Detection] = {}
    for name in sorted(registry):
        probe = registry[name]
        needs = list(probe.needs_user_input)
        if probe.kind is ProbeKind.BASH:
            rc = run_bash(probe.bash_command)
            if rc in _NOT_RUNNABLE:
                # The shell could not run the command at all — that says
                # nothing about the connector itself.
                out[name] = Detection(
                    name, DetectStatus.UNKNOWN, needs, f"`{probe.bash_command}` not runnable (exit {rc})"
                )
                continue
            status = DetectStatus.CONNECTED if rc == 0 else DetectStatus.UNAVAILABLE
            out[name] = Detection(name, status, needs, f"`{probe.bash_command}` exit {rc}")
            continue
        if servers is None:
            out[name] = Detection(name, DetectStatus.UNKNOWN, needs, "`claude mcp list` unavailable")
            continue
        best: tuple[DetectStatus, str] | None = None
        for tool in probe.tool_chain:
            hit = _match_server(tool, servers)
            if hit is None:
                continue
            if best is None or hit[0] is DetectStatus.CONNECTED:
                best = hit
            if hit[0] is DetectStatus.CONNECTED:
                break
        if best is None:
            out[name] = Detection(name, DetectStatus.UNKNOWN, needs, "no matching MCP server in `claude mcp list`")
        else:
            out[name] = Detection(name, best[0], needs, best[1])
    return out


def probe_env() -> dict[str, str]:
    """The inherited environment with the launchd plists' PATH dirs in front.

    Scout.app spawns ``connectors detect`` with the GUI PATH (``/usr/bin:/bin:
    /usr/sbin:/sbin``), where Homebrew's ``gh`` and the stdio MCP servers that
    ``claude mcp list`` health-checks are not found. Prepend the same dirs the
    plists put first (``engine/scout/defaults/*.plist``) so detection sees what
    a scheduled run will see.
    """
    env = dict(os.environ)
    prefix = f"{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/local/bin"
    inherited = env.get("PATH", "")
    env["PATH"] = f"{prefix}:{inherited}" if inherited else prefix
    return env


def run_claude_mcp_list(claude_bin: str, *, timeout: float = 60.0) -> str | None:
    """stdout of `claude mcp list`, or None when the CLI is missing, fails, or hangs."""
    try:
        proc = subprocess.run(
            [claude_bin, "mcp", "list"], capture_output=True, text=True, timeout=timeout, check=False, env=probe_env()
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def run_bash_probe(command: str, *, timeout: float = 15.0) -> int:
    try:
        return subprocess.run(
            command, shell=True, capture_output=True, timeout=timeout, check=False, env=probe_env()
        ).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def to_json_dict(dets: dict[str, Detection]) -> dict[str, dict[str, Any]]:
    return {
        name: {"status": d.status.value, "needs_user_input": d.needs_user_input, "evidence": d.evidence}
        for name, d in dets.items()
    }
