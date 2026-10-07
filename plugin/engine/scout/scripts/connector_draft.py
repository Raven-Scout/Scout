"""Draft custom-connector definitions for one MCP server (spec §4.2).

A headless `claude -p` reads the server's tool list and drafts; this module never
trusts that output. ``check_definitions`` is the gate: every tool belongs to the
server, none writes, and each definition passes the same validation as
`connectors custom add`.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
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
        "label",
        "apply",
        "save",
        "merge",
        "resolve",
        "restore",
        "retire",
        "add",
        "submit",
        "set",
        "put",
        "patch",
        "insert",
        "modify",
        "rename",
        "assign",
        "close",
        "approve",
        "reject",
        "publish",
        "schedule",
        "invite",
        "accept",
        "decline",
        "comment",
        "react",
        "pin",
        "star",
        "mute",
        "snooze",
        "copy",
        "import",
        "run",
        "execute",
        "trigger",
        "start",
        "stop",
        "deploy",
        "enable",
        "disable",
        "grant",
        "revoke",
        "transfer",
        "pay",
        "purchase",
        "book",
        "clear",
        "reset",
        "sync",
        "push",
        "commit",
        "drop",
        "purge",
        "empty",
        "block",
        "flag",
        "subscribe",
        "join",
        "leave",
        "kick",
        "ban",
    }
)

READ_VERBS = frozenset(
    {
        "list",
        "get",
        "search",
        "read",
        "find",
        "query",
        "fetch",
        "describe",
        "show",
        "lookup",
        "retrieve",
        "count",
        "view",
        "whoami",
        "browse",
        "check",
        "status",
    }
)

_WRITE_PREFIXES = ("un", "re", "de")


def action_words(tool: str) -> list[str]:
    """Lowercased words of a tool's action segment: ``mcp__s__sendMessage`` → ``["send", "message"]``."""
    action = tool.rsplit("__", 1)[-1]
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", action).lower()
    return [w for w in re.split(r"[^a-z0-9]+", words) if w]


def _is_write_word(word: str) -> bool:
    if word in WRITE_VERBS:
        return True
    for prefix in _WRITE_PREFIXES:
        if word.startswith(prefix) and word[len(prefix) :] in WRITE_VERBS:
            return True
    return False


def is_write_tool(tool: str) -> bool:
    return any(_is_write_word(w) for w in action_words(tool))


def is_read_tool(tool: str) -> bool:
    words = action_words(tool)
    return not any(_is_write_word(w) for w in words) and any(w in READ_VERBS for w in words)


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
            if not is_read_tool(ref):
                issues.append(cc.Issue(base, f"{ref!r} is not clearly a read tool; a draft may only read"))
        if server is not None:
            body["server"] = server
        _, problems = cc.parse_connector(key, body, reserved=reserved, presets=presets)
        issues += problems
        out.append({"key": key, **body})
    return ([], issues) if issues else (out, [])


EXIT_CODES = {"drafted": 0, "invalid": 2, "needs_auth": 3, "no_read_tools": 3, "timeout": 1, "error": 1}
_TEMPLATE = Path(__file__).parent.parent / "defaults" / "draft-connector.md"
_BASE_FLAGS = [
    "--tools",
    "ToolSearch",
    "--disable-slash-commands",
    "--no-session-persistence",
    "--output-format",
    "json",
    "--permission-mode",
    "dontAsk",
]


@dataclass(frozen=True)
class ClaudeResult:
    returncode: int
    stdout: str
    stderr: str = ""


ClaudeRunner = Callable[[list[str], str, float], ClaudeResult | None]


def run_claude(argv: list[str], stdin: str, timeout: float) -> ClaudeResult | None:
    """One headless call, from the temp dir so no project CLAUDE.md loads. None = timed out."""
    from scout.scripts.connector_detect import probe_env

    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=probe_env(),
            cwd=tempfile.gettempdir(),
        )
    except subprocess.TimeoutExpired:
        return None
    except OSError as e:
        return ClaudeResult(127, "", str(e))
    return ClaudeResult(proc.returncode, proc.stdout, proc.stderr)


def draft_argv(claude_bin: str, model: str, schema: dict[str, Any]) -> list[str]:
    return [
        claude_bin,
        "-p",
        "--model",
        model,
        *_BASE_FLAGS,
        "--json-schema",
        json.dumps(schema),
        "--max-budget-usd",
        "0.50",
        "--allowedTools",
        "ToolSearch",
    ]


def probe_argv(claude_bin: str, probe: str) -> list[str]:
    return [
        claude_bin,
        "-p",
        "--model",
        "haiku",
        *_BASE_FLAGS,
        "--json-schema",
        json.dumps(PROBE_SCHEMA),
        "--max-budget-usd",
        "0.20",
        "--allowedTools",
        "ToolSearch",
        probe,
    ]


_PRESET_PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")


def render_prompt(server_name: str, *, plugin_root: Path, taken: set[str]) -> str:
    from scout.scripts.connector_detect import server_slug

    presets = cc.load_presets(plugin_root)
    # Preset bodies carry the shipped brain files' own `{{USER_NAME}}`-style
    # placeholders (resolved later by phase assembly against the real user). Sent
    # unresolved, a drafting model may echo one verbatim into a definition's free
    # text, where nothing expands it. Describe the person generically instead.
    preset_text = "\n".join(
        f"- {name}: " + "; ".join(f"{k}: {_PRESET_PLACEHOLDER_RE.sub('the user', v)}" for k, v in sorted(body.items()))
        for name, body in sorted(presets.items())
    )
    taken_text = ", ".join(sorted(taken | cc.reserved_keys(plugin_root))) or "none"
    return (
        _TEMPLATE.read_text(encoding="utf-8")
        .replace("{{SERVER_NAME}}", server_name)
        .replace("{{SERVER_SLUG}}", server_slug(server_name))
        .replace("{{TAKEN_KEYS}}", taken_text)
        .replace("{{PRESETS}}", preset_text)
    )


def _envelope(res: ClaudeResult) -> dict[str, Any] | None:
    try:
        env = json.loads(res.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    return env if isinstance(env, dict) else None


def _structured(res: ClaudeResult) -> dict[str, Any] | None:
    env = _envelope(res)
    if env is None or env.get("is_error"):
        return None
    so = env.get("structured_output")
    if isinstance(so, dict):
        return so
    try:
        parsed = json.loads(str(env.get("result", "")))
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _error_text(res: ClaudeResult) -> str:
    env = _envelope(res) or {}
    errors = env.get("errors")
    if isinstance(errors, list) and errors:
        return "; ".join(str(e) for e in errors)
    if env.get("subtype"):
        return str(env["subtype"])
    return (res.stderr or res.stdout).strip()[:300] or f"claude exited {res.returncode}"


def _out(status: str, server_name: str, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "server": server_name,
        "definitions": fields.get("definitions", []),
        "summary": fields.get("summary", []),
        "issues": [{"path": i.path, "message": i.message} for i in fields.get("issues", [])],
        "message": fields.get("message", ""),
    }


def draft(
    server_name: str,
    *,
    plugin_root: Path,
    vault: Path,
    claude_bin: str,
    model: str = "sonnet",
    timeout: float = 120.0,
    runner: ClaudeRunner | None = None,
) -> dict[str, Any]:
    run = runner or run_claude
    taken = set(cc.load(vault, plugin_root=plugin_root).raw) if (vault / cc.CUSTOM_FILE).exists() else set()
    schema = draft_schema(list(cc.load_presets(plugin_root)))
    prompt = render_prompt(server_name, plugin_root=plugin_root, taken=taken)
    feedback = ""
    defs: list[dict[str, Any]] = []
    issues: list[cc.Issue] = []
    payload: dict[str, Any] = {}
    for _attempt in range(2):
        res = run(draft_argv(claude_bin, model, schema), prompt + feedback, timeout)
        if res is None:
            return _out("timeout", server_name, message=f"drafting took longer than {timeout:.0f}s")
        structured = _structured(res)
        if structured is None:
            return _out("error", server_name, message=_error_text(res))
        payload = structured
        if payload.get("no_read_tools"):
            return _out("no_read_tools", server_name)
        defs, issues = check_definitions(
            payload.get("definitions"), server_name=server_name, plugin_root=plugin_root, taken=taken
        )
        if not issues:
            break
        feedback = "\n\nYour previous draft had these problems. Fix every one:\n" + "\n".join(
            f"- {i.path}: {i.message}" for i in issues
        )
    if issues:
        return _out("invalid", server_name, issues=issues)
    for probe in sorted({d["probe"] for d in defs}):
        ask = (
            f"Load the tool {probe} with ToolSearch (query: select:{probe}), call it once with the smallest valid "
            "arguments, and report ok=true if it returned data, or ok=false with the error text."
        )
        res = run(probe_argv(claude_bin, probe), ask, timeout)
        result = _structured(res) if res is not None else None
        if not result or not result.get("ok"):
            reason = (result or {}).get("error") or (_error_text(res) if res is not None else "the check timed out")
            return _out("needs_auth", server_name, message=str(reason))
    keys = {d["key"] for d in defs}
    summary = [
        {"key": s["key"], "scans": str(s.get("scans", "")), "looks_up": str(s.get("looks_up", ""))}
        for s in payload.get("summary") or []
        if isinstance(s, dict) and s.get("key") in keys
    ]
    return _out("drafted", server_name, definitions=defs, summary=summary)
