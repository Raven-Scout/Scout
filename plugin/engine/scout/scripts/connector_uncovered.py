"""Connected MCP servers that no connector reads — what setup offers as "Also connected" (spec §4.1).

No LLM: `claude mcp list`, minus servers the merged probe registry covers (shipped,
overlay and custom-connector probes all land there), minus tooling servers.
"""

from __future__ import annotations

from typing import Any

from scout.scripts.connector_detect import _normalize, parse_mcp_list, server_slug, tool_server_slug
from scout.scripts.connector_probes import Probe, ProbeKind

SCHEMA_VERSION = 1

# Servers that are tooling for this machine or this app, not a source of the
# user's work. Matched against the words of the normalized display name.
TOOLING_WORDS = frozenset(
    {
        "browser",
        "chrome",
        "playwright",
        "puppeteer",
        "terminal",
        "shell",
        "session",
        "sessions",
        "sidebar",
        "window",
        "schedule",
        "scheduled",
        "scheduler",
        "cron",
        "visualize",
        "visualizer",
        "widget",
        "simulator",
        "preview",
        "registry",
        "computer",
    }
)


def is_tooling(name: str) -> bool:
    words = set(_normalize(name).split("_"))
    return bool(words & TOOLING_WORDS) or any(w.startswith("ccd") for w in words)


def covered_server_keys(registry: dict[str, Probe]) -> set[str]:
    """Normalized server keys any MCP probe in the registry reads."""
    keys: set[str] = set()
    for probe in registry.values():
        if probe.kind is not ProbeKind.MCP_TOOL:
            continue
        for tool in probe.tool_chain:
            slug = tool_server_slug(tool)
            if slug:
                keys.add(_normalize(slug))
    return keys


def find_uncovered(mcp_list_output: str | None, *, registry: dict[str, Probe]) -> dict[str, Any]:
    if mcp_list_output is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "servers": [],
            "error": "`claude mcp list` failed, timed out, or Claude Code is not installed",
        }
    covered = covered_server_keys(registry)
    servers = [
        {"name": name, "slug": server_slug(name), "status": status.value, "evidence": line}
        for name, (status, line) in parse_mcp_list(mcp_list_output).items()
        if _normalize(name) not in covered and not is_tooling(name)
    ]
    servers.sort(key=lambda s: s["name"].lower())
    return {"schema_version": SCHEMA_VERSION, "servers": servers, "error": None}
