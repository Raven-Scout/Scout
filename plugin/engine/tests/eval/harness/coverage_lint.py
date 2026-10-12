"""Stub coverage lint (spec 2026-10-12 §5.5).

A tool missing from a persona catalog is never in ``tools/list``, so an eval
model can't see it and silently works around it; nothing at run time says
so. This lint is the guard: every tool a phase names must be served by a
catalog, or listed in ``tests/eval/stubs/unserved.txt`` with a reason.

It scans every ``plugin/phases/**/*.md`` for:

- backticked tool-like names, bare (`` `list_issues` ``) or called
  (`` `slack_read_channel(id, limit=20)` ``);
- full ``mcp__<server>__<tool>`` names;
- ``gh`` commands inside fenced code or backticks (flags before the
  subcommand are skipped).

A name is assigned to a persona by its prefix (``slack_``, ``gcal_``,
``gmail_``), by its MCP server, or by the connector phase it appears in.
Generic names (``get_project``) outside a connector phase go to ``any``:
served when any persona serves them.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ENGINE = Path(__file__).resolve().parents[3]
PHASES_DIR = ENGINE.parent / "phases"
CATALOG_DIR = ENGINE / "tests" / "eval" / "stubs" / "catalog"

PHASE_PERSONAS = {"slack.md": "slack", "linear.md": "linear", "calendar.md": "calendar", "email.md": "email"}
PREFIX_PERSONAS = {"slack_": "slack", "gcal_": "calendar", "gmail_": "email"}
SERVER_PERSONAS = {
    "claude_ai_Slack": "slack",
    "plugin_slack_slack": "slack",
    "claude_ai_Linear": "linear",
    "plugin_linear_linear": "linear",
    "claude_ai_Google_Calendar": "calendar",
    "claude_ai_Gmail": "email",
}
_VERBS = "list|get|save|search|read|send|create|update|delete|fetch|find|query"
_BACKTICKED = re.compile(r"`([a-z][a-z0-9_]*)(?=\(|`)")
_TOOL_LIKE = re.compile(rf"^(?:(?:slack|gcal|gmail)_[a-z0-9_]+|(?:{_VERBS})_[a-z0-9_]+)$")
_FULL_NAME = re.compile(r"mcp__([A-Za-z0-9_]+?)__([a-z0-9_]+)")
_FENCE = re.compile(r"```.*?```", re.S)
_INLINE = re.compile(r"`[^`\n]+`")
_GH = re.compile(
    r"(?:^|[ \t;&|(`$])gh"
    r"((?:[ \t]+-[A-Za-z][ \t]+\S+|[ \t]+--[\w-]+(?:=\S+)?)*)"  # flags before the subcommand
    r"[ \t]+([a-z][a-z-]*)(?:[ \t]+([a-z][a-z-]*))?",
    re.M,
)
_GH_GROUPS = {"pr", "search", "repo", "issue", "run", "release", "workflow", "label", "auth", "gist", "project"}


def _persona_for(tool: str, file_persona: str | None) -> str:
    for prefix, persona in PREFIX_PERSONAS.items():
        if tool.startswith(prefix):
            return persona
    return file_persona or "any"


def _gh_commands(text: str) -> set[str]:
    code = "\n".join(m.group(0).strip("`") for m in [*_FENCE.finditer(text), *_INLINE.finditer(_FENCE.sub("", text))])
    out = set()
    for _, first, second in _GH.findall(code):
        out.add(f"{first} {second}" if first in _GH_GROUPS and second else first)
    return out


def referenced_tools(phases_dir: Path) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for path in sorted(phases_dir.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        file_persona = PHASE_PERSONAS.get(path.name) if path.parent.name == "connectors" else None
        for tok in _BACKTICKED.findall(text):
            if _TOOL_LIKE.match(tok):
                refs.setdefault(_persona_for(tok, file_persona), set()).add(tok)
        for server, tool in _FULL_NAME.findall(text):
            if server in SERVER_PERSONAS:
                refs.setdefault(SERVER_PERSONAS[server], set()).add(tool)
        gh = _gh_commands(text)
        if gh:
            refs.setdefault("gh", set()).update(gh)
    return refs


def served_tools(catalog_dir: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in sorted(catalog_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out[path.stem] = {t["name"] for t in data.get("tools", [])} | set(data.get("commands", []))
    out["any"] = set().union(*(v for k, v in out.items() if k != "gh"))
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
        for tool in tools - served.get(persona, set()):
            if f"{persona}: {tool}" not in allowed:
                problems.append(f"{persona}: {tool}")
    return sorted(problems)
