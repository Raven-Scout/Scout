"""Render custom-connector sections from the shipped activity templates.

One template per activity lives in ``plugin/phases/custom/``. For each enabled custom
connector and each activity it declares, the template is filled in two passes:
first the ``{{CONNECTOR_*}}`` values (which may themselves contain ``{{USER_NAME}}``
from preset text), then the usual brain-file vars plus the connector's
``{{INPUT_*}}`` values. Shared by bootstrap assembly and phase backport so the
two can never drift.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from scout.custom_connectors import ACTIVITIES, CustomConnector, ToolRef, stored_input_issues
from scout.scripts.phase_assembly import PhaseSection, parse_phase_file, render_template

# Which modes each brain file is consumed by — mirrors bootstrap._assemble.
TARGET_MODES: dict[str, set[str]] = {
    "SKILL": {"briefing", "consolidation"},
    "DREAMING": {"dreaming"},
    "RESEARCH": {"research"},
}

_CONNECTOR_VAR_RE = re.compile(r"\{\{(CONNECTOR_[A-Z_]+)\}\}")


@dataclass(frozen=True)
class CustomSection:
    connector_key: str
    activity: str
    template: Path
    raw_body: str  # CONNECTOR_* filled, brain-file vars still as {{VARS}}
    rendered_body: str  # fully rendered — what lands in the brain file


def _load_templates(plugin_root: Path) -> dict[str, tuple[Path, PhaseSection]]:
    out: dict[str, tuple[Path, PhaseSection]] = {}
    for activity in ACTIVITIES:
        path = plugin_root / "phases" / "custom" / f"{activity}.md"
        try:
            sections = parse_phase_file(path)
        except (OSError, ValueError, yaml.YAMLError) as e:
            print(f"warning: custom-connector template {path} unusable: {e}", file=sys.stderr)
            continue
        if len(sections) != 1:
            print(f"warning: custom-connector template {path} must hold exactly one section", file=sys.stderr)
            continue
        out[activity] = (path, sections[0])
    return out


_BACKTICK_RUN_RE = re.compile(r"`+")


def _fenced(command: str) -> str:
    """A multi-line command as a ```bash block nested in its list item (#344).

    Inside a fence a column-0 ``# comment`` stays shell, not a markdown heading. The
    fence is longer than any backtick run in the command, so nothing in it can close
    the block early; blank lines carry no indent."""
    fence = "`" * max(3, 1 + max((len(m) for m in _BACKTICK_RUN_RE.findall(command)), default=0))
    lines = [fence + "bash", *command.splitlines(), fence]
    return "\n".join(f"  {line}" if line.strip() else "" for line in lines)


def _tool_line(t: ToolRef) -> str:
    if t.kind == "mcp":
        return f"- `{t.value}` — call as an MCP tool"
    if "\n" in t.value or "\r" in t.value:
        return f"- run with Bash:\n{_fenced(t.value)}"
    # One-line commands keep their 0.15.0 inline form, so their sections don't drift.
    return f"- `{t.value}` — run with Bash"


def _tool_lines(tools: tuple[ToolRef, ...]) -> str:
    return "\n".join(_tool_line(t) for t in tools)


def _connector_values(c: CustomConnector, activity: str) -> dict[str, str]:
    act = c.activities[activity]
    return {
        "CONNECTOR_NAME": c.display_name,
        "CONNECTOR_TOOLS": _tool_lines(act.tools),
        "CONNECTOR_GUIDANCE": act.guidance,
        "CONNECTOR_NOTES": f"**Notes from {{{{USER_NAME}}}}:** {c.notes}" if c.notes else "",
    }


def _input_vars(c: CustomConnector, inputs: Mapping[str, object]) -> dict[str, str]:
    values = {name: inputs.get(f"{c.key}__{name}") for name in c.needs_user_input}
    return {f"INPUT_{name.upper()}": "" if v is None else str(v) for name, v in values.items()}


def _inputs_ok(c: CustomConnector, inputs: Mapping[str, object]) -> bool:
    """False, with a warning per problem, when a stored input would break the brain file:
    the connector is then skipped, like an invalid entry in connectors.custom.yaml (#344)."""
    issues = stored_input_issues({c.key: c}, dict(inputs))
    for issue in issues:
        print(f"warning: {issue.path}: {issue.message} ({c.key} skipped until fixed)", file=sys.stderr)
    return not issues


def render_custom_sections(
    plugin_root: Path,
    kind: str,
    connectors: dict[str, CustomConnector],
    enabled: set[str],
    vars_: dict[str, str],
    inputs: Mapping[str, object],
) -> list[CustomSection]:
    """Sections for every enabled connector, ordered by key then inbound → outbound → lookup.

    A connector whose stored ``needs_user_input`` value fails ``input_issues`` is skipped."""
    active = {k: c for k, c in connectors.items() if k in enabled and _inputs_ok(c, inputs)}
    if not active:
        return []
    modes = TARGET_MODES[kind]
    templates = _load_templates(plugin_root)
    out: list[CustomSection] = []
    for key in sorted(active):
        c = active[key]
        for activity in ACTIVITIES:
            if activity not in c.activities or activity not in templates:
                continue
            path, template = templates[activity]
            if template.mode and not set(template.mode) & modes:
                continue
            values = _connector_values(c, activity)

            def _fill(m: re.Match[str], v: dict[str, str] = values) -> str:
                return v.get(m.group(1), m.group(0))

            raw = _CONNECTOR_VAR_RE.sub(_fill, template.body)
            rendered = render_template(raw, {**vars_, **_input_vars(c, inputs)})
            out.append(CustomSection(key, activity, path, raw, rendered))
    return out
