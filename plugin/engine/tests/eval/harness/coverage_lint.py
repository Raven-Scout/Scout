"""Stub coverage lint (spec 2026-10-12 §5.5).

A phase that starts calling a tool the fixture doesn't serve would make every
eval sample hit an ``unstubbed`` error there, so tier 0 fails first. Each
connector phase's tool references (backticked tool-like names, full
``mcp__<server>__<tool>`` names, and ``gh`` subcommands) must be served by a
persona catalog, or listed in ``tests/eval/stubs/unserved.txt`` with a reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ENGINE = Path(__file__).resolve().parents[3]
PHASES_DIR = ENGINE.parent / "phases" / "connectors"
CATALOG_DIR = ENGINE / "tests" / "eval" / "stubs" / "catalog"

PHASE_PERSONAS = {"slack.md": "slack", "linear.md": "linear", "calendar.md": "calendar", "email.md": "email"}
SERVER_PERSONAS = {
    "claude_ai_Slack": "slack",
    "plugin_slack_slack": "slack",
    "claude_ai_Linear": "linear",
    "plugin_linear_linear": "linear",
    "claude_ai_Google_Calendar": "calendar",
    "claude_ai_Gmail": "email",
}
_BACKTICKED = re.compile(r"`([a-z][a-z0-9_]*)`")
_TOOL_LIKE = re.compile(r"^(?:(?:slack|gcal|gmail)_[a-z0-9_]+|(?:list|get|save|search|read|send)_[a-z0-9_]+)$")
_FULL_NAME = re.compile(r"mcp__([A-Za-z0-9_]+?)__([a-z0-9_]+)")
_GH_GROUPS = "pr|search|repo|issue|run|release|workflow|label|auth|gist|project"
_GH = re.compile(rf"\bgh (?:(api)\b|({_GH_GROUPS}) ([a-z][a-z-]*))")


def referenced_tools(phases_dir: Path) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for path in sorted(phases_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        persona = PHASE_PERSONAS.get(path.name)
        if persona:
            for tok in _BACKTICKED.findall(text):
                if _TOOL_LIKE.match(tok):
                    refs.setdefault(persona, set()).add(tok)
        for server, tool in _FULL_NAME.findall(text):
            if server in SERVER_PERSONAS:
                refs.setdefault(SERVER_PERSONAS[server], set()).add(tool)
        if path.name == "github.md":
            for api, group, sub in _GH.findall(text):
                refs.setdefault("gh", set()).add(api or f"{group} {sub}")
    return refs


def served_tools(catalog_dir: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in sorted(catalog_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        names = {t["name"] for t in data.get("tools", [])} | set(data.get("commands", []))
        out[path.stem] = names
    return out


def _unserved(path: Path) -> set[str]:
    if not path.exists():
        return set()
    lines = (ln.split("#", 1)[0].strip() for ln in path.read_text(encoding="utf-8").splitlines())
    return {ln for ln in lines if ln}


def lint(phases_dir: Path, catalog_dir: Path, unserved: Path) -> list[str]:
    served, allowed = served_tools(catalog_dir), _unserved(unserved)
    problems = []
    for persona, tools in referenced_tools(phases_dir).items():
        for tool in sorted(tools - served.get(persona, set())):
            if f"{persona}: {tool}" not in allowed:
                problems.append(f"{persona}: {tool}")
    return sorted(problems)
