"""Draft custom-connector definitions for one MCP server (spec §4.2).

A headless `claude -p` reads the server's tool list and drafts; this module never
trusts that output. ``check_definitions`` is the gate: every tool belongs to the
server, none writes, and each definition passes the same validation as
`connectors custom add`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from scout import custom_connectors as cc
from scout.scripts.connector_detect import _normalize, tool_server_slug

SCHEMA_VERSION = 1

WRITE_VERBS = frozenset(
    {
        "send",
        "post",
        "create",
        "update",
        "delete",
        "remove",
        "write",
        "reply",
        "forward",
        "archive",
        "move",
        "edit",
        "upload",
        "share",
        "trash",
        "mark",
        "respond",
        "cancel",
    }
)


def action_verb(tool: str) -> str:
    """First word of a tool's action segment: ``mcp__s__sendMessage`` → ``send``."""
    action = tool.rsplit("__", 1)[-1]
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", action).lower()
    return re.split(r"[^a-z0-9]+", words)[0]


def is_write_tool(tool: str) -> bool:
    return action_verb(tool) in WRITE_VERBS


def _tools_schema() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4}


def _activity_schema(guidance: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"tools": _tools_schema(), guidance: {"type": "string"}},
        "required": ["tools"],
        "additionalProperties": False,
    }


def draft_schema(preset_names: list[str]) -> dict[str, Any]:
    definition = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "display_name": {"type": "string"},
            "probe": {"type": "string"},
            "preset": {"type": "string", "enum": sorted(preset_names)},
            "inbound": _activity_schema("focus"),
            "outbound": _activity_schema("focus"),
            "lookup": _activity_schema("when"),
            "needs_user_input": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
        },
        "required": ["key", "display_name", "probe"],
        "additionalProperties": False,
    }
    summary = {
        "type": "object",
        "properties": {"key": {"type": "string"}, "scans": {"type": "string"}, "looks_up": {"type": "string"}},
        "required": ["key", "scans", "looks_up"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "no_read_tools": {"type": "boolean"},
            "definitions": {"type": "array", "items": definition},
            "summary": {"type": "array", "items": summary},
        },
        "required": ["definitions", "summary"],
        "additionalProperties": False,
    }


PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "error": {"type": "string"}},
    "required": ["ok"],
}


def _refs(body: dict[str, Any]) -> list[Any]:
    refs: list[Any] = [body.get("probe")]
    for name in cc.ACTIVITIES:
        block = body.get(name)
        if isinstance(block, dict) and isinstance(block.get("tools"), list):
            refs += block["tools"]
    return refs


def check_definitions(
    raw: Any, *, server_name: str, plugin_root: Path, taken: set[str]
) -> tuple[list[dict[str, Any]], list[cc.Issue]]:
    """The drafts the engine accepts, or ``([], issues)`` if any draft is unacceptable."""
    if not isinstance(raw, list) or not raw:
        return [], [cc.Issue("definitions", "the draft has no definitions")]
    want = _normalize(server_name)
    reserved = cc.reserved_keys(plugin_root)
    presets = cc.load_presets(plugin_root)
    seen = set(taken)
    issues: list[cc.Issue] = []
    out: list[dict[str, Any]] = []
    for i, d in enumerate(raw):
        if not isinstance(d, dict) or not isinstance(d.get("key"), str) or not d["key"]:
            issues.append(cc.Issue(f"definitions[{i}]", "needs a key"))
            continue
        key = d["key"]
        base = f"connectors.{key}"
        body = {k: v for k, v in d.items() if k not in ("key", "server")}
        if key in seen:
            issues.append(cc.Issue(base, f"{key!r} is already taken; pick another key"))
        seen.add(key)
        server = None
        for ref in _refs(body):
            if not isinstance(ref, str):
                issues.append(cc.Issue(base, "drafts read MCP tools only"))
                continue
            slug = tool_server_slug(ref)
            if slug is None or _normalize(slug) != want:
                issues.append(cc.Issue(base, f"{ref!r} is not a tool of {server_name}"))
                continue
            server = server or slug
            if is_write_tool(ref):
                issues.append(cc.Issue(base, f"{ref!r} changes data; a draft may only read"))
        if server is not None:
            body["server"] = server
        _, problems = cc.parse_connector(key, body, reserved=reserved, presets=presets)
        issues += problems
        out.append({"key": key, **body})
    return ([], issues) if issues else (out, [])
