"""Connected MCP servers that no connector reads — what setup offers as "Also connected" (spec §4.1).

No LLM: `claude mcp list`, minus servers the merged probe registry covers (shipped,
overlay and enabled custom-connector probes all land there), minus servers any
custom connector names (enabled or not), minus tooling servers.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
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


def custom_connector_servers(vault: Path) -> set[str]:
    """Every ``server`` a custom connector in the vault names, enabled or not.

    The merged registry only carries enabled custom connectors; a disabled one
    still covers its server (spec §4.1), so re-offering it would draft a duplicate.
    Read through #324's loader; every entry counts, including ones that fail
    validation, since they still name the server. Never raises.
    """
    from scout import custom_connectors as cc

    raw = cc.load(vault).raw
    return {body["server"] for body in raw.values() if isinstance(body, dict) and isinstance(body.get("server"), str)}


def find_uncovered(
    mcp_list_output: str | None, *, registry: dict[str, Probe], custom_servers: Collection[str] = ()
) -> dict[str, Any]:
    if mcp_list_output is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "servers": [],
            "error": "`claude mcp list` failed, timed out, or Claude Code is not installed",
        }
    covered = covered_server_keys(registry) | {_normalize(s) for s in custom_servers}
    servers = [
        {"name": name, "slug": server_slug(name), "status": status.value, "evidence": line}
        for name, (status, line) in parse_mcp_list(mcp_list_output).items()
        if _normalize(name) not in covered and not is_tooling(name)
    ]
    servers.sort(key=lambda s: s["name"].lower())
    return {"schema_version": SCHEMA_VERSION, "servers": servers, "error": None}
