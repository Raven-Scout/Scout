"""Custom connectors — vault-owned definitions in ``<vault>/connectors.custom.yaml``.

A custom connector is any tool Scout does not ship a phase file for. Its entry
says which of Scout's three activities it serves (inbound, outbound, lookup),
which tools to call for each, and a sentence of guidance (or a preset that
supplies it). Assembly renders the shipped activity templates in
``plugin/phases/custom/`` once per enabled connector; the probe registry, the health
roster, and the connector-log hook derive their entries from the same file.

See docs/superpowers/specs/2026-10-02-custom-connectors-design.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CUSTOM_FILE = "connectors.custom.yaml"
SCHEMA_VERSION = 1
ACTIVITIES: tuple[str, ...] = ("inbound", "outbound", "lookup")
GUIDANCE_FIELD: dict[str, str] = {"inbound": "focus", "outbound": "focus", "lookup": "when"}
SLOT_TYPES = frozenset({"briefing", "consolidation", "dreaming", "research"})

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_INPUT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Also applied to `--input` values by custom_connector_ops.add: inputs are
# rendered verbatim into SKILL.md, so they must never be secrets either.
CREDENTIAL_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:xox[abp]-|ghp_|gho_|github_pat_|sk-|lin_api_)[A-Za-z0-9_-]{8,}"
    r"|Bearer\s+[A-Za-z0-9._~+/-]{12,}"
)
_FIELDS = frozenset(
    {"display_name", "server", "probe", "preset", "notes", "needs_user_input", "required_in_types", *ACTIVITIES}
)
# Binaries too generic to identify a connector: mapping them would relabel
# every unrelated call (curl, python3 …) in a session as that connector.
_GENERIC_BINARIES = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "env",
        "sudo",
        "cd",
        "curl",
        "wget",
        "python",
        "python3",
        "node",
        "npx",
        "uv",
        "uvx",
        "jq",
        "git",
    }
)
_HEADER = (
    "# Custom connectors — managed by `scoutctl connectors custom add/remove`.\n"
    "# Hand edits take effect at the next `scoutctl bootstrap upgrade` (or /scout-update);\n"
    "# `scoutctl connectors custom list` reports any problems.\n"
    "# Never put credentials here or in inputs: sign each tool in through its own MCP connector or CLI.\n"
)


@dataclass(frozen=True)
class ToolRef:
    kind: str  # "mcp" | "bash"
    value: str  # MCP tool name, or shell command

    @property
    def server(self) -> str | None:
        return self.value.split("__")[1] if self.kind == "mcp" else None

    @property
    def binary(self) -> str | None:
        return first_binary(self.value) if self.kind == "bash" else None


@dataclass(frozen=True)
class Activity:
    tools: tuple[ToolRef, ...]
    guidance: str  # `focus` (inbound/outbound) or `when` (lookup), preset-filled


@dataclass(frozen=True)
class CustomConnector:
    key: str
    display_name: str
    server: str | None
    probe: ToolRef
    preset: str | None
    activities: dict[str, Activity]
    notes: str = ""
    needs_user_input: tuple[str, ...] = ()
    required_in_types: tuple[str, ...] = ()

    @property
    def health_key(self) -> str:
        """The connector key connector_log.classify emits for this connector's calls."""
        return f"mcp:{self.server}" if self.server else self.key


@dataclass(frozen=True)
class Issue:
    path: str  # e.g. "connectors.outlook.inbound.tools[0]"
    message: str


@dataclass
class CustomLoad:
    connectors: dict[str, CustomConnector] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)  # every entry as written, valid or not


def first_binary(cmd: str) -> str | None:
    """Basename of the first command word, skipping ``FOO=bar`` prefixes."""
    tokens = cmd.split()
    idx = 0
    while idx < len(tokens) and "=" in tokens[idx] and not tokens[idx].startswith("-"):
        idx += 1
    return tokens[idx].rsplit("/", 1)[-1] if idx < len(tokens) else None


def _tool_ref(value: Any, path: str, issues: list[Issue]) -> ToolRef | None:
    if isinstance(value, str):
        parts = value.split("__")
        if value.startswith("mcp__") and len(parts) >= 3 and all(parts[1:]):
            return ToolRef("mcp", value)
        issues.append(
            Issue(path, f"{value!r} is not an MCP tool name (mcp__<server>__<tool>); write a command as {{bash: ...}}")
        )
        return None
    if isinstance(value, dict) and set(value) == {"bash"} and isinstance(value["bash"], str) and value["bash"].strip():
        return ToolRef("bash", value["bash"].strip())
    issues.append(Issue(path, 'must be an MCP tool name or {bash: "<command>"}'))
    return None


def _strings(value: Any) -> list[str]:
    """Every string anywhere inside a parsed YAML value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def parse_connector(
    key: str, body: Any, *, reserved: set[str], presets: dict[str, dict[str, str]]
) -> tuple[CustomConnector | None, list[Issue]]:
    """Validate one entry. Returns the connector only when there are no issues."""
    base = f"connectors.{key}"
    issues: list[Issue] = []
    if not _KEY_RE.match(key):
        issues.append(Issue(base, "key must be lowercase letters, digits and _ (2-32 chars, starting with a letter)"))
    if key in reserved:
        issues.append(Issue(base, f"{key!r} is a built-in connector key; pick another name"))
    if not isinstance(body, dict):
        issues.append(Issue(base, "must be a mapping"))
        return None, issues
    for unknown in sorted(set(body) - _FIELDS):
        issues.append(Issue(f"{base}.{unknown}", "unknown field"))
    if any(CREDENTIAL_RE.search(s) for s in _strings(body)):
        msg = (
            "looks like it contains a credential; Scout never stores credentials — sign the tool in "
            "through its own MCP connector or CLI instead"
        )
        issues.append(Issue(base, msg))

    display_name = body.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        issues.append(Issue(f"{base}.display_name", "required"))
        display_name = ""

    probe: ToolRef | None = None
    if "probe" in body:
        probe = _tool_ref(body["probe"], f"{base}.probe", issues)
    else:
        issues.append(Issue(f"{base}.probe", "required"))

    preset = body.get("preset")
    if preset is not None and not isinstance(preset, str):
        # Checked before the `in presets` lookup: a list/mapping is unhashable.
        issues.append(Issue(f"{base}.preset", "must be a preset name"))
        preset = None
    elif preset is not None and preset not in presets:
        known = ", ".join(sorted(presets)) or "none"
        issues.append(Issue(f"{base}.preset", f"unknown preset {preset!r} (known: {known})"))
        preset = None

    activities: dict[str, Activity] = {}
    for name in ACTIVITIES:
        if name not in body:
            continue
        apath = f"{base}.{name}"
        block = body[name]
        if not isinstance(block, dict):
            issues.append(Issue(apath, "must be a mapping with `tools`"))
            continue
        gfield = GUIDANCE_FIELD[name]
        for unknown in sorted(set(block) - {"tools", gfield}):
            issues.append(Issue(f"{apath}.{unknown}", f"unknown field (this activity takes `tools` and `{gfield}`)"))
        raw_tools = block.get("tools")
        if not isinstance(raw_tools, list) or not raw_tools:
            issues.append(Issue(f"{apath}.tools", "must be a non-empty list"))
            continue
        tools = [_tool_ref(t, f"{apath}.tools[{i}]", issues) for i, t in enumerate(raw_tools)]
        guidance = block.get(gfield)
        if guidance is None and preset is not None:
            guidance = presets[preset].get(name)
        if not isinstance(guidance, str) or not guidance.strip():
            msg = "required (a sentence on what matters) unless a preset supplies it"
            issues.append(Issue(f"{apath}.{gfield}", msg))
            continue
        valid_tools = tuple(t for t in tools if t is not None)
        if len(valid_tools) == len(tools):
            activities[name] = Activity(valid_tools, guidance.strip())
    if not any(name in body for name in ACTIVITIES):
        issues.append(Issue(base, "declare at least one of inbound, outbound, lookup"))

    refs = [probe, *(t for a in activities.values() for t in a.tools)]
    mcp_refs = [r for r in refs if r is not None and r.kind == "mcp"]
    server = body.get("server")
    if mcp_refs:
        if not isinstance(server, str) or not server:
            issues.append(Issue(f"{base}.server", "required when any tool is an MCP tool"))
        else:
            for r in mcp_refs:
                if r.server != server:
                    msg = f"{r.value!r} belongs to server {r.server!r}, not {server!r}"
                    issues.append(Issue(f"{base}.server", msg))
    else:
        server = None

    # Absent or empty (YAML null) means "none"; any other value must have the right
    # type — `needs_user_input: false` or `notes: 0` is an issue, not silently dropped.
    needs = body.get("needs_user_input")
    if needs is None:
        needs = []
    if not isinstance(needs, list) or not all(isinstance(n, str) and _INPUT_RE.match(n) for n in needs):
        issues.append(Issue(f"{base}.needs_user_input", "must be a list of lowercase names like workspace_id"))
        needs = []

    types = body.get("required_in_types")
    if types is None:
        types = []
    # Items are type-checked before set(): a nested list is unhashable.
    if not isinstance(types, list) or not all(isinstance(t, str) and t in SLOT_TYPES for t in types):
        issues.append(Issue(f"{base}.required_in_types", f"must be a list drawn from {sorted(SLOT_TYPES)}"))
        types = []

    notes = body.get("notes")
    if notes is None:
        notes = ""
    if not isinstance(notes, str):
        issues.append(Issue(f"{base}.notes", "must be text"))
        notes = ""

    if issues or probe is None:
        return None, issues
    return (
        CustomConnector(
            key=key,
            display_name=display_name.strip(),
            server=server,
            probe=probe,
            preset=preset,
            activities=activities,
            notes=notes.strip(),
            needs_user_input=tuple(needs),
            required_in_types=tuple(types),
        ),
        [],
    )


def parse_file(raw: Any, *, reserved: set[str], presets: dict[str, dict[str, str]]) -> CustomLoad:
    """Validate a whole file. Invalid entries are dropped with issues; valid ones survive."""
    if raw is None:
        return CustomLoad()
    if not isinstance(raw, dict):
        return CustomLoad(issues=[Issue(CUSTOM_FILE, "must be a mapping with schema_version and connectors")])
    if raw.get("schema_version") != SCHEMA_VERSION:
        return CustomLoad(issues=[Issue(f"{CUSTOM_FILE}.schema_version", f"must be {SCHEMA_VERSION}")])
    conns = raw.get("connectors")
    if conns is None:
        conns = {}
    if not isinstance(conns, dict):
        return CustomLoad(issues=[Issue(f"{CUSTOM_FILE}.connectors", "must be a mapping of key → definition")])
    out = CustomLoad(raw={str(k): v for k, v in conns.items()})
    for key, body in out.raw.items():
        try:
            connector, issues = parse_connector(key, body, reserved=reserved, presets=presets)
        except Exception as e:  # noqa: BLE001 — a validator bug costs this entry, never the whole file
            connector, issues = None, [Issue(f"connectors.{key}", f"could not be validated: {e}")]
        out.issues += issues
        if connector is not None:
            out.connectors[key] = connector
    return out


def default_plugin_root() -> Path:
    """Plugin root of the running engine (same derivation as connector_probes)."""
    import scout

    return Path(scout.__file__).parent.parent.parent


def load_presets(plugin_root: Path) -> dict[str, dict[str, str]]:
    """``plugin/phases/presets/<name>.yaml`` → {name: {summary, inbound, outbound, lookup}}. Bad files are skipped."""
    out: dict[str, dict[str, str]] = {}
    for path in sorted((plugin_root / "phases" / "presets").glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if isinstance(raw, dict):
            out[path.stem] = {str(k): str(v).strip() for k, v in raw.items() if isinstance(v, str)}
    return out


def reserved_keys(plugin_root: Path) -> set[str]:
    """Keys a custom connector may not use: shipped probes, phase `requires:`, aliases."""
    from scout.scripts.connector_probes import CONNECTOR_KEY_ALIASES, load_registry
    from scout.scripts.phase_assembly import parse_phase_file

    keys: set[str] = set(CONNECTOR_KEY_ALIASES) | set(CONNECTOR_KEY_ALIASES.values())
    try:
        keys |= set(load_registry(plugin_root / "templates" / "connector-probes.yaml"))
    except (OSError, ValueError, yaml.YAMLError):
        pass
    for phase_file in (plugin_root / "phases").rglob("*.md"):
        try:
            keys |= {s.requires for s in parse_phase_file(phase_file) if s.requires}
        except (OSError, ValueError, yaml.YAMLError):
            continue
    return keys


def load(vault: Path, *, plugin_root: Path | None = None) -> CustomLoad:
    """Read and validate ``<vault>/connectors.custom.yaml``. Never raises; a missing file is empty."""
    path = vault / CUSTOM_FILE
    if not path.exists():
        return CustomLoad()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        return CustomLoad(issues=[Issue(CUSTOM_FILE, f"could not be read: {e}")])
    root = plugin_root or default_plugin_root()
    return parse_file(raw, reserved=reserved_keys(root), presets=load_presets(root))


def dump(raw_connectors: dict[str, Any]) -> str:
    body = yaml.safe_dump(
        {"schema_version": SCHEMA_VERSION, "connectors": raw_connectors}, sort_keys=False, allow_unicode=True
    )
    return _HEADER + body


def write(vault: Path, raw_connectors: dict[str, Any]) -> None:
    """Atomically write the custom file (the only writer of it)."""
    path = vault / CUSTOM_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dump(raw_connectors), encoding="utf-8")
    tmp.replace(path)


def bash_binaries(vault: Path) -> dict[str, str]:
    """Binary → connector key for every bash probe/tool. Unvalidated and never raises (hook path)."""
    try:
        raw = yaml.safe_load((vault / CUSTOM_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    conns = raw.get("connectors") if isinstance(raw, dict) else None
    if not isinstance(conns, dict):
        return {}
    out: dict[str, str] = {}
    for key, body in conns.items():
        if not isinstance(body, dict):
            continue
        refs: list[Any] = [body.get("probe")]
        for name in ACTIVITIES:
            block = body.get(name)
            if isinstance(block, dict) and isinstance(block.get("tools"), list):
                refs += block["tools"]
        for ref in refs:
            if isinstance(ref, dict) and isinstance(ref.get("bash"), str):
                binary = first_binary(ref["bash"])
                if binary and binary not in _GENERIC_BINARIES and binary not in out:
                    out[binary] = str(key)
    return out
