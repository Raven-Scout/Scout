# Retiring `/scout-setup`: Implementation Plan (engine, terminal, commands, release, site)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `scoutctl setup` (run by `install.sh`) and three new engine commands (`connectors uncovered`, `connectors custom draft`, `connectors setup`) do everything `/scout-setup` did, add `/scout-connect`, then retire `/scout-setup` to a stub and put a **Download for Mac** button on the website.

**Architecture:** All setup logic lives in the engine (Python). `connectors uncovered` lists connected MCP servers no connector reads. `connectors custom draft` drafts definitions for one server through a locked-down headless `claude -p`, then validates them in the engine. `scoutctl setup` is a terminal wizard (`scout/setup_wizard.py`) that asks the questions, then calls the existing `bootstrap auto` in-process and applies kept drafts with `custom add`. `install.sh`, `/scout-connect` and (later, in the app plan) Scout.app are front ends over those commands.

**Tech Stack:** Python 3.12 + Typer (engine), pytest, bash (`install.sh`, `release.sh`), Markdown slash commands, static HTML (`docs/index.html`).

**Spec:** `docs/superpowers/specs/2026-10-06-scout-setup-retirement-design.md` (Raven-Scout/Scout#326). Read it with this plan.

**Not in this plan:** the app half (spec §6: the Connectors step's "Also connected" section, applying drafts in the Vault step, Settings ▸ Connectors, the `ConnectorHealthService` merge, the app's `/scout-setup` strings). That code changes `OnboardingViewModel`, which exists only on #319's branch, and #319's wiring task C8 hasn't started. It gets its own plan, `docs/superpowers/plans/YYYY-MM-DD-scout-setup-retirement-app.md`, written after #319 merges. Tasks 13–15 here are gated on it (see each task).

## Global Constraints

- Engine commands run from `plugin/engine`. One-time setup: `cd plugin/engine && uv venv --python 3.12 && uv pip install -e '.[dev]'`.
- The gate before every commit: `.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout` (run `ruff format scout tests` first to fix formatting).
- Fixtures are public: use `Alex` / `alex@example.com`, servers `example_suite` / `claude.ai Example Suite` / `claude_ai_Example_Suite`, `example-org/<repo>`. No real server, workspace or person names (root `CLAUDE.md`).
- **No test ever runs a real `claude`.** Every test injects a runner or stubs the binary on `PATH`. Task 1 is the only step that calls the real CLI, and the implementer runs it by hand.
- `custom` subcommands always print exactly one JSON object and exit with a stable code. New statuses: `busy` (exit 4). Draft statuses and codes: `drafted` 0, `invalid` 2, `needs_auth` 3, `no_read_tools` 3, `timeout` 1, `error` 1.
- The hard rule (from #261): no prose, output or message ever tells a user a tool "isn't supported", "can't be read" or "has no probe". A tool ends as **added**, **skipped by the user**, or **sign in first**.
- Drafting calls use `--model sonnet` by default, `--tools ToolSearch`, `--disable-slash-commands`, `--no-session-persistence`, `--permission-mode dontAsk`, the prompt on **stdin**, and `cwd` = the system temp dir. Measured 2026-10-06: a bare `claude -p` loads ~51k tokens on Opus ($0.51 before a $0.05 cap stopped it; the cap is checked after the fact). With `--model haiku --tools ToolSearch --disable-slash-commands` it was 12.8k tokens and $0.026.
- `claude -p --output-format json --json-schema …` returns an envelope with `is_error`, `subtype`, `total_cost_usd`, `result` (the JSON as text) and `structured_output` (the parsed object). Read `structured_output` first.
- Every user-facing "run `/scout-setup`" becomes `open Scout.app or run \`scoutctl setup\`` — one constant, `scout.paths.SETUP_HINT` (Task 15).
- Coverage: once #323 merges, the plugin coverage floor is exactly 98.00% (`precision = 2`). Every Phase 1 task covers all of its new lines, error branches included. Check with `.venv/bin/pytest --cov=scout --cov-report=term-missing -q` before each commit.
- Always pass `--repo Raven-Scout/Scout` to `gh`. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **`claude mcp list` hangs or fails** (a stdio server that never answers its health check). `uncovered` must return `error` within `--timeout`, never an empty "nothing else connected". Pinned in Task 2.
2. **Plugin servers with hyphens.** `plugin:example-kit:search-tool` lists tools as `mcp__plugin_example-kit_search-tool__…`. The draft gate must accept them and reject a different server's tool. Pinned in Task 3.
3. **Write-tool spellings.** `sendMessage`, `create-issue`, `mark_read` and `archive_thread` are writes; `list_labels`, `get_message` and `search_threads` are reads. Pinned in Task 3.
4. **`curl … | bash` with no terminal and no `--yes`** (CI, a provisioning script). `scoutctl setup` must exit 2 with the headless flags listed, never block on a prompt. Pinned in Task 7.
5. **Ctrl-D or Ctrl-C at a prompt.** The wizard must stop with "Setup cancelled; nothing was written." and must not have created the vault. Pinned in Task 7.

---

## Phase 1 — engine (one PR, additive; merges any time)

### Task 1: Spike — can a locked-down headless session read a server's tools?

This is a feasibility check the implementer runs by hand on a machine with Claude Code signed in. No code is kept. **If any check fails, stop and report to Jordan. Do not widen `--allowedTools`.**

**Files:**
- Modify: this plan, the "Spike results" section at the end.

- [ ] **Step 1: Pick a connected server**

Run: `claude mcp list`
Pick one claude.ai connector that shows `✔ Connected` (for example a calendar). Note its display name (`<NAME>`) and slug (`<SLUG>` = the name with every run of non-alphanumerics replaced by `_`, e.g. `claude.ai Example Suite` → `claude_ai_Example_Suite`).

- [ ] **Step 2: Check that ToolSearch can see the server's tools**

```bash
cd "$(mktemp -d)" && printf '%s' 'Call ToolSearch with the query "+<SLUG>". Report every tool name it returns. Do not call any other tool.' \
  | claude -p --model sonnet --tools ToolSearch --disable-slash-commands --no-session-persistence \
      --output-format json \
      --json-schema '{"type":"object","properties":{"tools":{"type":"array","items":{"type":"string"}}},"required":["tools"]}' \
      --permission-mode dontAsk --allowedTools ToolSearch --max-budget-usd 0.50 \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["is_error"], d["total_cost_usd"]); print(d.get("structured_output"))'
```

Pass: `is_error` is `False`, `structured_output.tools` lists names starting with `mcp__<SLUG>__`, and the cost is under $0.15.

- [ ] **Step 3: Check that a tool call is denied, not prompted**

```bash
cd "$(mktemp -d)" && printf '%s' 'Load one read tool of server <SLUG> with ToolSearch and call it once. Report ok=true if it returned data.' \
  | claude -p --model sonnet --tools ToolSearch --disable-slash-commands --no-session-persistence \
      --output-format json \
      --json-schema '{"type":"object","properties":{"ok":{"type":"boolean"},"error":{"type":"string"}},"required":["ok"]}' \
      --permission-mode dontAsk --allowedTools ToolSearch --max-budget-usd 0.50 \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("permission_denials")); print(d.get("structured_output"))'
```

Pass: the command returns on its own, without waiting for input; `permission_denials` names the `mcp__<SLUG>__…` tool; `structured_output.ok` is `false`.

- [ ] **Step 4: Check that the probe call works when that one tool is allowed**

Take one tool name `<TOOL>` from Step 2 that only reads (list/get/search).

```bash
cd "$(mktemp -d)" && printf '%s' 'Load the tool <TOOL> with ToolSearch (query: select:<TOOL>), call it once with the smallest valid arguments, and report ok=true if it returned data, or ok=false with the error text.' \
  | claude -p --model haiku --tools ToolSearch --disable-slash-commands --no-session-persistence \
      --output-format json \
      --json-schema '{"type":"object","properties":{"ok":{"type":"boolean"},"error":{"type":"string"}},"required":["ok"]}' \
      --permission-mode dontAsk --allowedTools ToolSearch <TOOL> --max-budget-usd 0.20 \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("permission_denials"), d["total_cost_usd"]); print(d.get("structured_output"))'
```

Pass: `permission_denials` is empty and `structured_output.ok` is `true`.

- [ ] **Step 5: Record the results and commit**

Fill in "Spike results" at the end of this plan: the date, the Claude Code version (`claude --version`), pass or fail per step, the cost per call, and any flag that behaved differently from this plan. If Step 4 needed a different `--allowedTools` spelling (for example comma-joined), write the spelling that worked; Task 4's `probe_argv` uses it.

```bash
git add docs/superpowers/plans/2026-10-06-scout-setup-retirement.md
git commit -m "docs(setup): record the headless drafting spike results"
```

---

### Task 2: `scoutctl connectors uncovered`

**Files:**
- Create: `plugin/engine/scout/scripts/connector_uncovered.py`
- Modify: `plugin/engine/scout/cli.py` (inside `_register_connectors`, after `cli_connectors_detect`)
- Test: `plugin/engine/tests/unit/test_connector_uncovered.py`

**Interfaces:**
- Consumes: `connector_detect.parse_mcp_list`, `connector_detect.server_slug`, `connector_detect.tool_server_slug`, `connector_detect._normalize`, `connector_detect.run_claude_mcp_list`, `connector_probes.resolve_registry`, `bootstrap.resolve_claude_bin`.
- Produces: `TOOLING_WORDS: frozenset[str]`; `is_tooling(name: str) -> bool`; `covered_server_keys(registry: dict[str, Probe]) -> set[str]`; `find_uncovered(mcp_list_output: str | None, *, registry: dict[str, Probe]) -> dict[str, Any]` returning `{"schema_version": 1, "servers": [{"name", "slug", "status", "evidence"}], "error": str | None}`. CLI: `scoutctl connectors uncovered [--json] [--claude-bin PATH] [--timeout 60]`.

- [ ] **Step 1: Write the failing tests**

```python
"""connectors uncovered: connected MCP servers that no connector reads (spec §4.1)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from scout.cli import app
from scout.scripts import connector_uncovered as cu
from scout.scripts.connector_probes import Probe, ProbeKind

MCP_LIST = """\
Checking MCP server health...

claude.ai Example Suite: https://mcp.example.com/suite - ✔ Connected
claude.ai Example Calendar: https://mcp.example.com/cal - ✔ Connected
claude.ai Example CRM: https://mcp.example.com/crm - ! Needs authentication
plugin:example-kit:search-tool: npx example-kit - ✘ Failed to connect
plugin:playwright:playwright: npx @playwright/mcp - ✔ Connected
Claude_Browser: builtin - ✔ Connected
ccd_session: builtin - ✔ Connected
custom_tickets: https://mcp.example.com/tix - ✔ Connected
[mcp-sdk] warning: something noisy
"""

REGISTRY = {
    "calendar": Probe("calendar", ProbeKind.MCP_TOOL, tool_chain=["mcp__claude_ai_Example_Calendar__list_calendars"]),
    "tickets": Probe("tickets", ProbeKind.MCP_TOOL, tool_chain=["mcp__custom_tickets__whoami"]),  # a custom connector's probe
    "github": Probe("github", ProbeKind.BASH, bash_command="gh auth status"),
}


def test_lists_only_uncovered_work_servers_sorted():
    out = cu.find_uncovered(MCP_LIST, registry=REGISTRY)
    assert out["error"] is None and out["schema_version"] == 1
    assert [s["name"] for s in out["servers"]] == [
        "claude.ai Example CRM",
        "claude.ai Example Suite",
        "plugin:example-kit:search-tool",
    ]
    suite = next(s for s in out["servers"] if s["name"] == "claude.ai Example Suite")
    assert suite == {
        "name": "claude.ai Example Suite",
        "slug": "claude_ai_Example_Suite",
        "status": "connected",
        "evidence": "claude.ai Example Suite: https://mcp.example.com/suite - ✔ Connected",
    }
    statuses = {s["name"]: s["status"] for s in out["servers"]}
    assert statuses["claude.ai Example CRM"] == "needs_auth"
    assert statuses["plugin:example-kit:search-tool"] == "unavailable"


def test_a_failed_mcp_list_is_an_error_never_an_empty_success():
    out = cu.find_uncovered(None, registry=REGISTRY)
    assert out["servers"] == []
    assert out["error"] and "claude mcp list" in out["error"]


def test_hyphenated_plugin_server_is_covered_by_its_tool_slug():
    reg = {"kit": Probe("kit", ProbeKind.MCP_TOOL, tool_chain=["mcp__plugin_example-kit_search-tool__find"])}
    out = cu.find_uncovered(MCP_LIST, registry=reg)
    assert "plugin:example-kit:search-tool" not in [s["name"] for s in out["servers"]]


def test_tooling_names():
    for name in ("Claude_Browser", "plugin:playwright:playwright", "ccd_session", "claude-in-chrome", "Terminal"):
        assert cu.is_tooling(name), name
    for name in ("claude.ai Example Suite", "claude.ai Gmail", "plugin:linear:linear"):
        assert not cu.is_tooling(name), name


def test_cli_emits_json(monkeypatch):
    monkeypatch.setattr("scout.scripts.connector_detect.run_claude_mcp_list", lambda *a, **k: MCP_LIST)
    monkeypatch.setattr("scout.scripts.connector_probes.resolve_registry", lambda **k: REGISTRY)
    result = CliRunner().invoke(app, ["connectors", "uncovered", "--json", "--claude-bin", "/bin/echo"])
    assert result.exit_code == 0, result.output
    assert [s["slug"] for s in json.loads(result.output)["servers"]][0] == "claude_ai_Example_CRM"


def test_cli_exits_1_when_listing_failed(monkeypatch):
    monkeypatch.setattr("scout.scripts.connector_detect.run_claude_mcp_list", lambda *a, **k: None)
    result = CliRunner().invoke(app, ["connectors", "uncovered", "--json", "--claude-bin", "/bin/echo"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_connector_uncovered.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.scripts.connector_uncovered'`.

- [ ] **Step 3: Implement the module**

```python
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
```

- [ ] **Step 4: Add the CLI command**

In `cli.py`, inside `_register_connectors()`, directly after `cli_connectors_detect`:

```python
    @connectors_app.command("uncovered")
    def cli_connectors_uncovered(
        json_out: bool = typer.Option(False, "--json", help="Emit JSON (consumed by Scout.app and scoutctl setup)."),
        claude_bin: str = typer.Option("", "--claude-bin", help="Claude Code binary. Default: auto-detect."),
        timeout: float = typer.Option(60.0, "--timeout", help="Seconds to wait for `claude mcp list`."),
    ) -> None:
        """Connected MCP servers that no connector reads yet (no LLM)."""
        import json as _json

        from scout.scripts import connector_detect, connector_probes
        from scout.scripts.bootstrap import resolve_claude_bin
        from scout.scripts.connector_uncovered import find_uncovered

        listing = connector_detect.run_claude_mcp_list(resolve_claude_bin(claude_bin), timeout=timeout)
        payload = find_uncovered(listing, registry=connector_probes.resolve_registry())
        if json_out:
            typer.echo(_json.dumps(payload, indent=2))
        else:
            for s in payload["servers"]:
                typer.echo(f"{s['name']}\t{s['status']}")
        if payload["error"]:
            if not json_out:
                typer.echo(f"error: {payload['error']}", err=True)
            raise typer.Exit(code=1)
```

The monkeypatches in the CLI tests target the module attributes, which is why the command imports the modules (not the functions).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_connector_uncovered.py tests/unit/test_cli_surface.py -q`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add scout/scripts/connector_uncovered.py scout/cli.py tests/unit/test_connector_uncovered.py
git commit -m "feat(connectors): uncovered — connected servers no connector reads"
```

---

### Task 3: The draft gate — what the engine accepts from a model

**Files:**
- Create: `plugin/engine/scout/scripts/connector_draft.py` (the pure half; Task 4 adds the runner)
- Test: `plugin/engine/tests/unit/test_connector_draft_gate.py`

**Interfaces:**
- Consumes: `custom_connectors.parse_connector`, `reserved_keys`, `load_presets`, `Issue`; `connector_detect._normalize`, `tool_server_slug`.
- Produces: `WRITE_VERBS: frozenset[str]`; `action_verb(tool: str) -> str`; `is_write_tool(tool: str) -> bool`; `draft_schema(preset_names: list[str]) -> dict[str, Any]`; `PROBE_SCHEMA: dict[str, Any]`; `check_definitions(raw: Any, *, server_name: str, plugin_root: Path, taken: set[str]) -> tuple[list[dict[str, Any]], list[cc.Issue]]`. Every returned definition has `key` and `server` (the tool-name segment) set by the engine.

- [ ] **Step 1: Write the failing tests**

```python
"""The engine-side gate on a model's connector drafts (spec §4.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts import connector_draft as cd

PLUGIN = Path(__file__).parent.parent.parent.parent
SERVER = "claude.ai Example Suite"
T = "mcp__claude_ai_Example_Suite__"


def _mail(**over):
    d = {
        "key": "suite_mail",
        "display_name": "Mail suite",
        "probe": f"{T}list_folders",
        "preset": "mail",
        "inbound": {"tools": [f"{T}search_messages", f"{T}get_message"]},
    }
    d.update(over)
    return d


@pytest.mark.parametrize(
    "tool",
    [f"{T}send_message", f"{T}sendMessage", f"{T}create-issue", f"{T}mark_read", f"{T}archive_thread", f"{T}Delete"],
)
def test_write_tools_are_detected(tool):
    assert cd.is_write_tool(tool)


@pytest.mark.parametrize("tool", [f"{T}list_labels", f"{T}get_message", f"{T}search_threads", f"{T}listFolders"])
def test_read_tools_pass(tool):
    assert not cd.is_write_tool(tool)


def test_a_valid_draft_comes_back_with_the_server_set():
    defs, issues = cd.check_definitions([_mail()], server_name=SERVER, plugin_root=PLUGIN, taken=set())
    assert issues == []
    assert defs[0]["server"] == "claude_ai_Example_Suite"
    assert defs[0]["key"] == "suite_mail"


def test_a_write_tool_rejects_the_whole_draft():
    bad = _mail(inbound={"tools": [f"{T}search_messages", f"{T}send_message"]})
    defs, issues = cd.check_definitions([bad], server_name=SERVER, plugin_root=PLUGIN, taken=set())
    assert defs == []
    assert any("send_message" in i.message for i in issues)


def test_a_tool_of_another_server_is_rejected():
    bad = _mail(inbound={"tools": ["mcp__claude_ai_Other__search"]})
    defs, issues = cd.check_definitions([bad], server_name=SERVER, plugin_root=PLUGIN, taken=set())
    assert defs == [] and any("not a tool of" in i.message for i in issues)


def test_hyphenated_plugin_server_tools_are_accepted():
    t = "mcp__plugin_example-kit_search-tool__"
    d = {"key": "kit_search", "display_name": "Kit", "probe": f"{t}whoami", "lookup": {"tools": [f"{t}find"], "when": "When a question names a kit item."}}
    defs, issues = cd.check_definitions([d], server_name="plugin:example-kit:search-tool", plugin_root=PLUGIN, taken=set())
    assert issues == [] and defs[0]["server"] == "plugin_example-kit_search-tool"


def test_bash_tools_and_missing_keys_are_rejected():
    bad = _mail(probe={"bash": "suite whoami"})
    _, issues = cd.check_definitions([bad, {"display_name": "x"}], server_name=SERVER, plugin_root=PLUGIN, taken=set())
    paths = [i.path for i in issues]
    assert any(p.startswith("connectors.suite_mail") for p in paths)
    assert "definitions[1]" in paths


def test_a_taken_key_and_a_duplicate_key_are_rejected():
    _, issues = cd.check_definitions([_mail()], server_name=SERVER, plugin_root=PLUGIN, taken={"suite_mail"})
    assert issues
    _, issues = cd.check_definitions([_mail(), _mail()], server_name=SERVER, plugin_root=PLUGIN, taken=set())
    assert issues


def test_an_empty_draft_is_an_issue():
    defs, issues = cd.check_definitions([], server_name=SERVER, plugin_root=PLUGIN, taken=set())
    assert defs == [] and issues


def test_schema_lists_the_shipped_presets():
    schema = cd.draft_schema(["calendar", "chat", "mail"])
    preset = schema["properties"]["definitions"]["items"]["properties"]["preset"]
    assert preset["enum"] == ["calendar", "chat", "mail"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_connector_draft_gate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.scripts.connector_draft'`.

- [ ] **Step 3: Implement the gate**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_connector_draft_gate.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add scout/scripts/connector_draft.py tests/unit/test_connector_draft_gate.py
git commit -m "feat(connectors): the draft gate — only this server's read tools pass"
```

---

### Task 4: `scoutctl connectors custom draft`

**Files:**
- Modify: `plugin/engine/scout/scripts/connector_draft.py` (add the runner)
- Create: `plugin/engine/scout/defaults/draft-connector.md`
- Modify: `plugin/engine/scout/cli.py` (inside `_register_connectors`, after `cli_custom_list`)
- Test: `plugin/engine/tests/unit/test_connector_draft_run.py`

**Interfaces:**
- Consumes: Task 3's `check_definitions`, `draft_schema`, `PROBE_SCHEMA`; `connector_detect.probe_env`; `custom_connectors.load`, `load_presets`, `CUSTOM_FILE`.
- Produces: `ClaudeResult(returncode: int, stdout: str, stderr: str = "")`; `ClaudeRunner = Callable[[list[str], str, float], ClaudeResult | None]` (`None` = timed out); `run_claude(argv, stdin, timeout) -> ClaudeResult | None`; `draft_argv(claude_bin: str, model: str, schema: dict) -> list[str]`; `probe_argv(claude_bin: str, probe: str) -> list[str]`; `render_prompt(server_name: str, *, plugin_root: Path, taken: set[str]) -> str`; `draft(server_name: str, *, plugin_root: Path, vault: Path, claude_bin: str, model: str = "sonnet", timeout: float = 120.0, runner: ClaudeRunner | None = None) -> dict[str, Any]` (None means `run_claude`, looked up at call time); `EXIT_CODES: dict[str, int]`. CLI: `scoutctl connectors custom draft --server NAME [--json] [--claude-bin PATH] [--model sonnet] [--timeout 120]`.

- [ ] **Step 1: Write the failing tests**

```python
"""connectors custom draft: two locked-down headless calls, then the gate (spec §4.2)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from scout.cli import app
from scout.scripts import connector_draft as cd

PLUGIN = Path(__file__).parent.parent.parent.parent
SERVER = "claude.ai Example Suite"
T = "mcp__claude_ai_Example_Suite__"
GOOD = {
    "definitions": [
        {
            "key": "suite_mail",
            "display_name": "Mail suite",
            "probe": f"{T}list_folders",
            "preset": "mail",
            "inbound": {"tools": [f"{T}search_messages"]},
        }
    ],
    "summary": [{"key": "suite_mail", "scans": "new mail that may need a reply", "looks_up": "nothing"}],
}


def _envelope(structured, *, is_error=False):
    return json.dumps({"type": "result", "is_error": is_error, "result": json.dumps(structured), "structured_output": structured})


class FakeClaude:
    """Answers draft calls from `drafts` in order and probe calls with `probe_ok`."""

    def __init__(self, drafts, probe_ok=True):
        self.drafts = list(drafts)
        self.probe_ok = probe_ok
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, argv, stdin, timeout):
        self.calls.append((argv, stdin))
        if "haiku" in argv:  # the probe call
            return cd.ClaudeResult(0, _envelope({"ok": self.probe_ok, "error": "" if self.probe_ok else "401"}))
        nxt = self.drafts.pop(0)
        return nxt if nxt is None or isinstance(nxt, cd.ClaudeResult) else cd.ClaudeResult(0, _envelope(nxt))


def _draft(fake, tmp_path):
    return cd.draft(SERVER, plugin_root=PLUGIN, vault=tmp_path / "Scout", claude_bin="claude", runner=fake)


def test_drafted(tmp_path):
    fake = FakeClaude([GOOD])
    out = _draft(fake, tmp_path)
    assert out["status"] == "drafted", out
    assert out["definitions"][0]["server"] == "claude_ai_Example_Suite"
    assert out["summary"][0]["scans"] == "new mail that may need a reply"
    assert out["server"] == SERVER and out["schema_version"] == 1


def test_the_draft_call_is_locked_down_and_takes_the_prompt_on_stdin(tmp_path):
    fake = FakeClaude([GOOD])
    _draft(fake, tmp_path)
    argv, stdin = fake.calls[0]
    joined = " ".join(argv)
    for flag in ("--permission-mode dontAsk", "--allowedTools ToolSearch", "--tools ToolSearch", "--disable-slash-commands",
                 "--no-session-persistence", "--model sonnet", "--max-budget-usd"):
        assert flag in joined, flag
    assert "claude_ai_Example_Suite" in stdin and SERVER in stdin
    probe_argv, _ = fake.calls[1]
    assert probe_argv[probe_argv.index("--allowedTools") + 2] == f"{T}list_folders"


def test_one_retry_with_feedback_then_invalid(tmp_path):
    bad = {**GOOD, "definitions": [{**GOOD["definitions"][0], "inbound": {"tools": [f"{T}send_message"]}}]}
    fake = FakeClaude([bad, bad])
    out = _draft(fake, tmp_path)
    assert out["status"] == "invalid" and out["issues"]
    assert "send_message" in fake.calls[1][1]  # the retry prompt carries the issues
    assert len(fake.calls) == 2  # no probe call after an invalid draft


def test_retry_can_succeed(tmp_path):
    bad = {**GOOD, "definitions": [{**GOOD["definitions"][0], "inbound": {"tools": [f"{T}send_message"]}}]}
    assert _draft(FakeClaude([bad, GOOD]), tmp_path)["status"] == "drafted"


def test_failing_probe_is_needs_auth(tmp_path):
    out = _draft(FakeClaude([GOOD], probe_ok=False), tmp_path)
    assert out["status"] == "needs_auth" and "401" in out["message"]


def test_no_read_tools(tmp_path):
    out = _draft(FakeClaude([{"no_read_tools": True, "definitions": [], "summary": []}]), tmp_path)
    assert out["status"] == "no_read_tools"


def test_timeout_and_error(tmp_path):
    assert _draft(FakeClaude([None]), tmp_path)["status"] == "timeout"
    err = cd.ClaudeResult(1, json.dumps({"is_error": True, "subtype": "error_max_budget_usd", "errors": ["Reached maximum budget"]}))
    out = _draft(FakeClaude([err]), tmp_path)
    assert out["status"] == "error" and "maximum budget" in out["message"]


def test_keys_already_in_the_vault_are_taken(tmp_path):
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "connectors.custom.yaml").write_text("schema_version: 1\nconnectors:\n  suite_mail: {display_name: X}\n")
    fake = FakeClaude([GOOD, GOOD])
    out = _draft(fake, tmp_path)
    assert out["status"] == "invalid"
    assert "suite_mail" in fake.calls[0][1]  # the prompt lists taken keys


def test_prompt_template_carries_the_rules():
    text = cd.render_prompt(SERVER, plugin_root=PLUGIN, taken={"slack"})
    assert "+claude_ai_Example_Suite" in text and "1–4 read tools per activity" in text and "slack" in text
    assert "{{" not in text


def test_cli_emits_json_and_exit_code(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "Scout"))
    monkeypatch.setattr(cd, "run_claude", FakeClaude([GOOD], probe_ok=False))
    result = CliRunner().invoke(app, ["connectors", "custom", "draft", "--server", SERVER, "--json", "--claude-bin", "/bin/echo"])
    assert result.exit_code == 3
    assert json.loads(result.stdout)["status"] == "needs_auth"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_connector_draft_run.py -q`
Expected: FAIL with `AttributeError: module 'scout.scripts.connector_draft' has no attribute 'ClaudeResult'`.

- [ ] **Step 3: Write the prompt template**

`plugin/engine/scout/defaults/draft-connector.md`:

```markdown
You are drafting Scout custom-connector definitions for one MCP server: {{SERVER_NAME}}. Its tool names start with `mcp__{{SERVER_SLUG}}__`.

1. Load the server's tools: call ToolSearch with the query `+{{SERVER_SLUG}}`. If the server may have more tools than one search returns, search again with other keywords (`+{{SERVER_SLUG}} list`, `+{{SERVER_SLUG}} search`, `+{{SERVER_SLUG}} get`). Read each tool's name and description. You cannot call the server's tools, so don't try.
2. Decide which surfaces the server covers. One server can be several connectors: a productivity suite can be mail, calendar and chat. Give each its own key.
3. For each connector:
   - `key`: lowercase letters, digits and `_`, 2–32 characters, starting with a letter. Not one of: {{TAKEN_KEYS}}.
   - `display_name`: what the user calls it.
   - Activities. `inbound`: new things that may need the user's action. `outbound`: what the user did there; only where the tools record the user's own actions (mail sent, messages posted, tickets closed). `lookup`: something to query on demand, with a `when` sentence.
   - `tools`: 1–4 read tools per activity (search, list, get, read). Never a tool that sends, posts, creates, updates, deletes, moves, archives or marks anything.
   - `preset`: `mail`, `chat` or `calendar` when the surface is one of those, and then leave out `focus`/`when`. Otherwise write `focus` (inbound, outbound) or `when` (lookup): one or two sentences on what matters.
   - `probe`: the cheapest read tool (list folders, whoami, get profile).
   - `needs_user_input`: names (lowercase_with_underscores) of values only the user knows that the tools need, such as a workspace id. Usually empty.
4. `summary`: one entry per connector, with `scans` (what inbound and outbound scan, under ten words) and `looks_up` (what lookup answers, under ten words, or "nothing").
5. If the server has no read tools at all, return `no_read_tools: true` with empty `definitions` and `summary`.

The presets' text:
{{PRESETS}}

Return only the structured output.
```

- [ ] **Step 4: Implement the runner**

Append to `connector_draft.py` (and add `import json`, `import subprocess`, `import tempfile`, `from collections.abc import Callable`, `from dataclasses import dataclass` to its imports):

```python
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


def render_prompt(server_name: str, *, plugin_root: Path, taken: set[str]) -> str:
    from scout.scripts.connector_detect import server_slug

    presets = cc.load_presets(plugin_root)
    preset_text = "\n".join(
        f"- {name}: " + "; ".join(f"{k}: {v}" for k, v in sorted(body.items())) for name, body in sorted(presets.items())
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
```

`draft` reads `runner or run_claude` at call time, so the CLI test's `monkeypatch.setattr(cd, "run_claude", …)` reaches it. If Task 1 recorded a different `--allowedTools` spelling, change `probe_argv` (and the test's index arithmetic) to match.

- [ ] **Step 5: Add the CLI command**

In `cli.py`, inside `_register_connectors()`, after `cli_custom_list`:

```python
    @custom_app.command("draft")
    def cli_custom_draft(
        server: str = typer.Option(..., "--server", help="The server's name as `claude mcp list` shows it."),
        json_out: bool = typer.Option(True, "--json", hidden=True, help="Always JSON; accepted for symmetry."),
        claude_bin: str = typer.Option("", "--claude-bin", help="Claude Code binary. Default: auto-detect."),
        model: str = typer.Option("sonnet", "--model", help="Model for the drafting call."),
        timeout: float = typer.Option(120.0, "--timeout", help="Seconds per headless call."),
    ) -> None:
        """Draft custom-connector definitions for one connected server (headless claude -p, read-only)."""
        from scout import paths as _paths
        from scout.scripts import connector_draft
        from scout.scripts.bootstrap import resolve_claude_bin

        payload = connector_draft.draft(
            server,
            plugin_root=_plugin_root(),
            vault=_paths.data_dir(),
            claude_bin=resolve_claude_bin(claude_bin),
            model=model,
            timeout=timeout,
        )
        _emit(payload, connector_draft.EXIT_CODES[payload["status"]])
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_connector_draft_run.py tests/unit/test_connector_draft_gate.py tests/unit/test_cli_surface.py -q`
Expected: all PASS.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add scout/scripts/connector_draft.py scout/defaults/draft-connector.md scout/cli.py tests/unit/test_connector_draft_run.py
git commit -m "feat(connectors): custom draft — a locked-down headless draft, then the gate"
```

---

### Task 5: `custom add|remove` — a `busy` status and `--no-wait`

> **On hold (coordinator, 2026-10-06):** #321's owner may already have built this. Don't start until the coordination session confirms whether to reuse that work or do it here.

**Files:**
- Modify: `plugin/engine/scout/scripts/custom_connector_ops.py` (`_EXIT_CODES`, `_under_lock`, `add`, `_add`, `remove`, `_remove`)
- Modify: `plugin/engine/scout/cli.py` (`cli_custom_add`, `cli_custom_remove`)
- Test: `plugin/engine/tests/unit/test_custom_connector_busy.py`

**Interfaces:**
- Produces: `add(..., wait: bool = True)`, `remove(..., wait: bool = True)`; status `"busy"` with exit code 4; CLI flag `--no-wait` on `custom add` and `custom remove`.

- [ ] **Step 1: Write the failing tests**

```python
"""custom add/remove report `busy` instead of a generic error; --no-wait answers at once (#321)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.scripts import custom_connector_ops as ops
from scout.scripts.bootstrap import BootstrapConfig, install
from scout.scripts.bootstrap_lock import LockBusyError

PLUGIN = Path(__file__).parent.parent.parent.parent
SUITE = {
    "key": "suite_mail",
    "display_name": "Mail suite",
    "server": "example_suite",
    "probe": "mcp__example_suite__list_folders",
    "preset": "mail",
    "inbound": {"tools": ["mcp__example_suite__search_messages"]},
}


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    install(
        BootstrapConfig(
            vault=v, plugin_root=PLUGIN, instance_name="TestScout", instance_name_lower="testscout",
            user_name="Alex", user_email="alex@example.com", timezone="America/New_York", platform="macos",
            plugin_version="0.0.0", enabled_connectors={"slack"}, connector_inputs={}, skip_jobs=True, skip_claude=True,
        )
    )
    return v


def _hold_lock(vault: Path) -> None:
    lock = vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()))  # a live pid: busy, never stale


def test_no_wait_returns_busy_at_once(vault: Path):
    _hold_lock(vault)
    out = ops.add(vault, dict(SUITE), plugin_root=PLUGIN, plugin_version="0.0.0", inputs={}, wait=False)
    assert (out.status, out.exit_code) == ("busy", 4)
    assert "session is running" in out.message


def test_waiting_that_times_out_is_busy_too(vault: Path, monkeypatch: pytest.MonkeyPatch):
    def timed_out(lock, **kw):
        raise LockBusyError(lock, 4242)

    monkeypatch.setattr(ops, "acquire_lock_with_wait", timed_out)
    out = ops.add(vault, dict(SUITE), plugin_root=PLUGIN, plugin_version="0.0.0", inputs={})
    assert out.status == "busy"


def test_remove_no_wait_is_busy(vault: Path):
    ops.add(vault, dict(SUITE), plugin_root=PLUGIN, plugin_version="0.0.0", inputs={})
    _hold_lock(vault)
    out = ops.remove(vault, "suite_mail", plugin_root=PLUGIN, plugin_version="0.0.0", wait=False)
    assert out.status == "busy"


def test_cli_no_wait(vault: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    _hold_lock(vault)
    result = CliRunner().invoke(app, ["connectors", "custom", "add", "--file", "-", "--no-wait"], input=json.dumps(SUITE))
    assert result.exit_code == 4
    assert json.loads(result.stdout)["status"] == "busy"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_custom_connector_busy.py -q`
Expected: FAIL with `TypeError: add() got an unexpected keyword argument 'wait'`.

- [ ] **Step 3: Implement**

In `custom_connector_ops.py`:

1. Add `"busy": 4,` to `_EXIT_CODES`, and add `acquire_lock` to the `bootstrap_lock` import.
2. Replace `_under_lock` with:

```python
def _under_lock(vault: Path, key: str, change: Callable[[], Outcome], *, wait: bool = True) -> Outcome:
    """Run ``change`` holding the session lock.

    ``change`` must do every read of vault state (scout-config.yaml,
    connectors.custom.yaml, the snapshot) itself: a read made before the lock
    is stale by the time a concurrent add/remove has committed, and writing
    whole-file replacements from it would erase that other change.
    ``wait=False`` answers ``busy`` at once instead of polling for up to 300 s.
    """
    lock = vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if wait:
            acquire_lock_with_wait(lock)
        else:
            acquire_lock(lock)
    except LockBusyError:
        return Outcome("busy", key, message="A Scout session is running; try again when it finishes.")
    try:
        return change()
    finally:
        release_lock(lock)
```

3. Add a `wait: bool = True` keyword to `add`, `_add`, `remove` and `_remove`; pass it through each call, and end `_add` and `_remove` with `return _under_lock(vault, key, change, wait=wait)`.

In `cli.py`, add to both `cli_custom_add` and `cli_custom_remove`:

```python
        no_wait: bool = typer.Option(False, "--no-wait", help="Answer `busy` at once if a Scout session holds the lock."),
```

and pass `wait=not no_wait` to `add(...)` and `remove(...)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_custom_connector_busy.py tests/unit/test_custom_connector_ops.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add scout/scripts/custom_connector_ops.py scout/cli.py tests/unit/test_custom_connector_busy.py
git commit -m "feat(connectors): custom add/remove say busy, and --no-wait answers at once"
```

---

### Task 6: `custom list` returns full definitions

> **On hold (coordinator, 2026-10-06):** same as Task 5.

**Files:**
- Modify: `plugin/engine/scout/scripts/custom_connector_ops.py` (`_list_custom`)
- Modify: `plugin/engine/tests/unit/test_custom_connector_ops.py` (`test_list_custom_reports_definitions_and_issues`)

**Interfaces:**
- Produces: each valid row gains `"valid": True`, `"probe": str`, `"tools": {activity: [str]}`, `"guidance": {activity: str}`, `"needs_user_input": [str]`, `"required_in_types": [str]`, `"notes": str`. Each invalid entry in `connectors.custom.yaml` becomes a row `{"key": k, "valid": False, "enabled": bool, "issues": [{"path", "message"}]}`. The top-level `issues` list is unchanged.

- [ ] **Step 1: Update the existing test (it fails first)**

Replace the body of `test_list_custom_reports_definitions_and_issues` from `row = listing["connectors"][0]` on with:

```python
    row = next(r for r in listing["connectors"] if r["key"] == "suite_mail")
    assert row == {
        "key": "suite_mail",
        "valid": True,
        "display_name": "Mail suite",
        "enabled": True,
        "server": "example_suite",
        "health_key": "mcp:example_suite",
        "preset": "mail",
        "activities": ["inbound"],
        "probe": "mcp__example_suite__list_folders",
        "tools": {"inbound": ["mcp__example_suite__search_messages"]},
        "guidance": {"inbound": row["guidance"]["inbound"]},
        "needs_user_input": [],
        "required_in_types": [],
        "notes": "",
    }
    assert row["guidance"]["inbound"]  # the mail preset's text
    broken = next(r for r in listing["connectors"] if r["key"] == "broken")
    assert broken["valid"] is False and broken["enabled"] is False
    assert broken["issues"] and all(i["path"].startswith("connectors.broken") for i in broken["issues"])
    assert any(i["path"].startswith("connectors.broken") for i in listing["issues"])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_custom_connector_ops.py::test_list_custom_reports_definitions_and_issues -q`
Expected: FAIL (the row has no `valid` key).

- [ ] **Step 3: Implement**

Replace the `rows = [...]` block and the return in `_list_custom` with:

```python
    rows: list[dict[str, Any]] = [
        {
            "key": c.key,
            "valid": True,
            "display_name": c.display_name,
            "enabled": c.key in enabled,
            "server": c.server,
            "health_key": c.health_key,
            "preset": c.preset,
            "activities": [a for a in cc.ACTIVITIES if a in c.activities],
            "probe": c.probe.value,
            "tools": {a: [t.value for t in c.activities[a].tools] for a in cc.ACTIVITIES if a in c.activities},
            "guidance": {a: c.activities[a].guidance for a in cc.ACTIVITIES if a in c.activities},
            "needs_user_input": list(c.needs_user_input),
            "required_in_types": list(c.required_in_types),
            "notes": c.notes,
        }
        for c in current.connectors.values()
    ]
    for key in current.raw:
        if key not in current.connectors:
            prefix = f"connectors.{key}"
            mine = [i for i in current.issues if i.path == prefix or i.path.startswith(prefix + ".")]
            rows.append(
                {
                    "key": key,
                    "valid": False,
                    "enabled": key in enabled,
                    "issues": [{"path": i.path, "message": i.message} for i in mine],
                }
            )
    rows.sort(key=lambda r: r["key"])
    return {"connectors": rows, "issues": [{"path": i.path, "message": i.message} for i in issues]}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_custom_connector_ops.py -q`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add scout/scripts/custom_connector_ops.py tests/unit/test_custom_connector_ops.py
git commit -m "feat(connectors): custom list returns full definitions and invalid rows"
```

---

### Task 7: `scoutctl setup` — the terminal wizard

**Files:**
- Create: `plugin/engine/scout/setup_wizard.py`
- Modify: `plugin/engine/scout/cli.py` (a top-level `setup` command, next to the other `@app.command`s near the end of the file)
- Test: `plugin/engine/tests/unit/test_setup_wizard.py`

**Interfaces:**
- Consumes: `bootstrap_auto.detect`, `AutoAction`, `Plan`; `connector_detect.detect`, `Detection`, `DetectStatus`, `run_claude_mcp_list`, `run_bash_probe`; `connector_probes.resolve_registry`; `engine_pointer.read_pointer`; `config.host_timezone_name`; `scout.schedule.load_schedule`, `load_default_schedule`.
- Produces:
  - `Prompter` (protocol: `say(text)`, `ask(question, default="") -> str`, `confirm(question, default: bool) -> bool`), `TtyPrompter.open() -> TtyPrompter | None`, `HeadlessPrompter`.
  - `SetupOptions` (fields below), `Answers`, `KeptDraft(definition: dict, inputs: dict[str, str])`.
  - `SetupDeps` (injectable callables below), `default_deps(claude_bin: str = "") -> SetupDeps`.
  - `auto_argv(a: Answers, managed_by: str) -> list[str]`, `invoke_scoutctl(args: list[str]) -> int`.
  - `run_setup(opts: SetupOptions, prompter: Prompter, deps: SetupDeps) -> int`.
  - `CANCELLED = "Setup cancelled; nothing was written."`
  - Task 8 adds `offer_uncovered` and `run_connectors_setup` to this module.

- [ ] **Step 1: Write the failing tests**

```python
"""scoutctl setup: the terminal front end of setup (spec §4.3)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import setup_wizard as sw
from scout.cli import app
from scout.scripts.bootstrap_auto import AutoAction, Plan
from scout.scripts.connector_detect import Detection, DetectStatus


class Scripted:
    """A Prompter that answers from a list; '' takes the default. Records everything said."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.said: list[str] = []

    def say(self, text):
        self.said.append(text)

    def ask(self, question, default=""):
        self.said.append(question)
        if not self.answers:
            raise EOFError("no more scripted answers")
        return self.answers.pop(0) or default

    def confirm(self, question, default):
        a = self.ask(question).lower()
        return default if a == "" else a.startswith("y")


DETECTIONS = {
    "slack": Detection("slack", DetectStatus.CONNECTED, ["user_slack_id"], "line"),
    "calendar": Detection("calendar", DetectStatus.CONNECTED, [], "line"),
    "gmail": Detection("gmail", DetectStatus.NEEDS_AUTH, [], "line"),
}


def _deps(tmp_path, *, plan=AutoAction.INSTALL, manager=None, codes=None):
    calls: list[list[str]] = []
    codes = codes or {}

    def invoke(args):
        calls.append(args)
        return codes.get(args[0], 0)

    deps = sw.SetupDeps(
        plan=lambda vault: Plan(plan, "test"),
        pointer_manager=lambda: manager,
        git_identity=lambda: ("Alex", "alex@example.com"),
        host_zone=lambda: "America/New_York",
        detect_connectors=lambda: DETECTIONS,
        list_uncovered=lambda: {"schema_version": 1, "servers": [], "error": None},
        draft=lambda server: {"status": "error", "message": "unused"},
        invoke=invoke,
        add_custom=lambda definition, inputs: {"status": "applied", "message": "Live"},
        first_briefing_slot=lambda: "briefing-am",
    )
    return deps, calls


def _opts(tmp_path, **kw):
    return replace(sw.SetupOptions(vault=tmp_path / "Scout"), **kw)


def test_fresh_install_asks_and_calls_bootstrap_auto(tmp_path):
    deps, calls = _deps(tmp_path)
    # instance, name, email, keep tz, toggle (enter), slack id, per-session, daily, first run
    p = Scripted(["", "", "", "", "", "U0123", "", "20", "y"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 0
    auto = calls[0]
    assert auto[:6] == ["bootstrap", "auto", "--no-interactive", "--yes", "--managed-by", "claude-code"]
    assert auto[auto.index("--user-name") + 1] == "Alex"
    assert auto[auto.index("--user-email") + 1] == "alex@example.com"
    assert auto[auto.index("--connectors") + 1] == "calendar,slack"
    assert auto[auto.index("--user-slack-id") + 1] == "U0123"
    assert auto[auto.index("--max-budget") + 1] == "5.00"
    assert "--timezone" not in auto
    assert ["budget", "set", "--daily-usd", "20"] in calls
    assert ["schedule", "fire-now", "briefing-am"] in calls


def test_toggling_and_a_pinned_timezone(tmp_path):
    deps, calls = _deps(tmp_path)
    # instance, name, email, keep tz? n, zone (bad, then good), toggle 1 (calendar off) then enter,
    # slack id, per-session, daily (blank), first run n
    p = Scripted(["", "", "", "n", "Mars/Olympus", "Europe/Berlin", "1", "", "U1", "", "", "n"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 0
    auto = calls[0]
    assert auto[auto.index("--connectors") + 1] == "slack"
    assert auto[auto.index("--timezone") + 1] == "Europe/Berlin"
    assert not any(c[0] == "budget" for c in calls)
    assert not any(c[0] == "schedule" for c in calls)


def test_existing_vault_upgrades_without_questions(tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    p = Scripted([])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 0
    assert calls == [["bootstrap", "auto", "--no-interactive", "--yes", "--managed-by", "preserve"]]
    assert any("upgrading" in s for s in p.said)


def test_explicit_managed_by_wins_on_upgrade(tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.UPGRADE)
    sw.run_setup(_opts(tmp_path, managed_by="install.sh"), Scripted([]), deps)
    assert calls[0][-1] == "install.sh"


def test_app_managed_engine_is_refused(tmp_path):
    deps, calls = _deps(tmp_path, manager="scout-app")
    p = Scripted([])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 2
    assert calls == [] and any("Scout.app" in s for s in p.said)


def test_refused_plan_exits_2(tmp_path):
    deps, calls = _deps(tmp_path, plan=AutoAction.REFUSED)
    assert sw.run_setup(_opts(tmp_path), Scripted([]), deps) == 2
    assert calls == []


def test_headless_needs_name_and_email(tmp_path):
    deps, calls = _deps(tmp_path)
    p = sw.HeadlessPrompter()
    assert sw.run_setup(_opts(tmp_path, yes=True), p, deps) == 2
    assert calls == []


def test_headless_with_flags_asks_nothing(tmp_path):
    deps, calls = _deps(tmp_path)
    opts = _opts(tmp_path, yes=True, name="Alex", email="alex@example.com", connectors="slack,github",
                 github_username="alex", github_repos="example-org/app", first_run=False)
    assert sw.run_setup(opts, sw.HeadlessPrompter(), deps) == 0
    auto = calls[0]
    assert auto[auto.index("--connectors") + 1] == "github,slack"
    assert auto[auto.index("--github-repos") + 1] == "example-org/app"


def test_headless_without_connectors_enables_what_is_connected(tmp_path):
    deps, calls = _deps(tmp_path)
    opts = _opts(tmp_path, yes=True, name="Alex", email="alex@example.com", first_run=False)
    sw.run_setup(opts, sw.HeadlessPrompter(), deps)
    assert calls[0][calls[0].index("--connectors") + 1] == "calendar,slack"


def test_end_of_input_cancels_before_anything_is_written(tmp_path):
    deps, calls = _deps(tmp_path)
    p = Scripted(["", "Alex"])  # runs out at the email question
    assert sw.run_setup(_opts(tmp_path), p, deps) == 1
    assert calls == []
    assert sw.CANCELLED in p.said


def test_a_red_bootstrap_stops_before_budget_and_first_run(tmp_path):
    deps, calls = _deps(tmp_path, codes={"bootstrap": 2})
    p = Scripted(["", "", "", "", "", "U1", "", "20", "y"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 2
    assert [c[0] for c in calls] == ["bootstrap"]


def test_cli_without_a_terminal_and_without_yes_exits_2(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "Scout"))
    monkeypatch.setattr(sw.TtyPrompter, "open", classmethod(lambda cls: None))
    result = CliRunner().invoke(app, ["setup"])
    assert result.exit_code == 2
    assert "--yes" in result.output and "--name" in result.output and "--email" in result.output


def test_invoke_scoutctl_returns_the_exit_code():
    assert sw.invoke_scoutctl(["connectors", "custom", "remove", "--help"]) == 0
    assert sw.invoke_scoutctl(["no-such-command"]) == 2
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_setup_wizard.py -q`
Expected: FAIL with `ImportError: cannot import name 'setup_wizard' from 'scout'`.

- [ ] **Step 3: Implement the module**

`plugin/engine/scout/setup_wizard.py`:

```python
"""`scoutctl setup` — the terminal front end of Scout's setup (spec §4.3, §4.4).

Asks the questions /scout-setup used to ask, then calls `bootstrap auto`
in-process. Every outside effect goes through ``SetupDeps`` so the flow is
tested without a vault, a terminal or a real `claude`.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from scout.scripts.bootstrap_auto import AutoAction, Plan
from scout.scripts.connector_detect import Detection, DetectStatus

CANCELLED = "Setup cancelled; nothing was written."
HEADLESS_FLAGS = (
    "--yes --name NAME --email EMAIL [--connectors a,b] [--slack-id ID] [--github-username U] "
    "[--github-repos owner/repo,…] [--max-budget 5.00] [--daily-budget USD] [--timezone ZONE] [--no-first-run]"
)
# needs_user_input name → (bootstrap auto flag, question)
_INPUTS = {
    "user_slack_id": ("--user-slack-id", "Your Slack member ID (Slack → your profile → ⋮ → Copy member ID)"),
    "github_username": ("--github-username", "Your GitHub username"),
    "github_repos": ("--github-repos", "GitHub repos to watch (owner/repo, comma-separated)"),
}


class Prompter(Protocol):
    def say(self, text: str) -> None: ...

    def ask(self, question: str, default: str = "") -> str: ...

    def confirm(self, question: str, default: bool) -> bool: ...


class TtyPrompter:
    """Prompts on /dev/tty: under `curl | bash` stdin is the pipe, not the user."""

    def __init__(self, tty: TextIO) -> None:
        self._tty = tty

    @classmethod
    def open(cls) -> TtyPrompter | None:
        try:
            return cls(open("/dev/tty", "r+", encoding="utf-8"))  # noqa: SIM115 — lives for the whole run
        except OSError:
            return None

    def say(self, text: str) -> None:
        self._tty.write(text + "\n")
        self._tty.flush()

    def ask(self, question: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        self._tty.write(f"{question}{suffix}: ")
        self._tty.flush()
        line = self._tty.readline()
        if line == "":
            raise EOFError("the terminal closed")
        return line.strip() or default

    def confirm(self, question: str, default: bool) -> bool:
        hint = "[Y/n]" if default else "[y/N]"
        while True:
            answer = self.ask(f"{question} {hint}").lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False


class HeadlessPrompter:
    """--yes: print, never ask; every question takes its default."""

    def say(self, text: str) -> None:
        print(text)

    def ask(self, question: str, default: str = "") -> str:
        return default

    def confirm(self, question: str, default: bool) -> bool:
        return default


@dataclass
class SetupOptions:
    vault: Path
    instance_name: str = ""
    name: str = ""
    email: str = ""
    timezone: str = ""
    connectors: str | None = None  # None: detect (and ask unless --yes)
    slack_id: str = ""
    github_username: str = ""
    github_repos: str = ""
    max_budget: str = ""
    daily_budget: str = ""
    first_run: bool | None = None
    managed_by: str = ""  # "": claude-code on install, preserve on upgrade
    yes: bool = False


@dataclass
class KeptDraft:
    definition: dict[str, Any]
    inputs: dict[str, str]


@dataclass
class Answers:
    instance_name: str = "Scout"
    name: str = ""
    email: str = ""
    timezone: str = ""
    connectors: set[str] = field(default_factory=set)
    slack_id: str = ""
    github_username: str = ""
    github_repos: str = ""
    max_budget: str = "5.00"
    daily_budget: str = ""
    drafts: list[KeptDraft] = field(default_factory=list)


@dataclass
class SetupDeps:
    plan: Callable[[Path], Plan]
    pointer_manager: Callable[[], str | None]
    git_identity: Callable[[], tuple[str, str]]
    host_zone: Callable[[], str | None]
    detect_connectors: Callable[[], dict[str, Detection]]
    list_uncovered: Callable[[], dict[str, Any]]
    draft: Callable[[str], dict[str, Any]]
    invoke: Callable[[list[str]], int]
    add_custom: Callable[[dict[str, Any], dict[str, str]], dict[str, Any]]
    first_briefing_slot: Callable[[], str | None]


def invoke_scoutctl(args: list[str]) -> int:
    """Run a scoutctl command in this process; its output goes to the terminal."""
    from typer.main import get_command

    from scout.cli import app

    try:
        rv = get_command(app).main(args=args, prog_name="scoutctl", standalone_mode=False)
    except Exception as e:  # Click usage errors carry an exit_code
        code = getattr(e, "exit_code", None)
        if code is None:
            raise
        sys.stderr.write(f"error: {e}\n")
        return int(code)
    return rv if isinstance(rv, int) else 0


def _git_identity() -> tuple[str, str]:
    def get(key: str) -> str:
        try:
            out = subprocess.run(["git", "config", "--global", key], capture_output=True, text=True, check=False)
        except OSError:
            return ""
        return out.stdout.strip()

    return get("user.name"), get("user.email")


def default_deps(claude_bin: str = "") -> SetupDeps:
    from scout import __version__
    from scout.config import host_timezone_name
    from scout.scripts import connector_detect, connector_draft, connector_probes, connector_uncovered
    from scout.scripts.bootstrap import resolve_claude_bin
    from scout.scripts.bootstrap_auto import detect
    from scout.scripts.custom_connector_ops import add
    from scout.scripts.engine_pointer import read_pointer

    claude = resolve_claude_bin(claude_bin)
    plugin_root = Path(__file__).parent.parent.parent

    def vault() -> Path:
        from scout import paths

        return paths.data_dir()

    def pointer_manager() -> str | None:
        p = read_pointer(home=Path.home())
        return p.managed_by if p else None

    def detect_connectors() -> dict[str, Detection]:
        return connector_detect.detect(
            connector_probes.resolve_registry(),
            mcp_list_output=connector_detect.run_claude_mcp_list(claude),
            run_bash=connector_detect.run_bash_probe,
        )

    def list_uncovered() -> dict[str, Any]:
        return connector_uncovered.find_uncovered(
            connector_detect.run_claude_mcp_list(claude), registry=connector_probes.resolve_registry()
        )

    def draft(server: str) -> dict[str, Any]:
        return connector_draft.draft(server, plugin_root=plugin_root, vault=vault(), claude_bin=claude)

    def add_custom(definition: dict[str, Any], inputs: dict[str, str]) -> dict[str, Any]:
        return add(vault(), dict(definition), plugin_root=plugin_root, plugin_version=__version__, inputs=inputs).to_json()

    def first_briefing_slot() -> str | None:
        from scout.schedule import load_default_schedule, load_schedule

        path = vault() / ".scout-state" / "schedule.yaml"
        sched = load_schedule(path) if path.exists() else load_default_schedule()
        return next((k for k in sorted(sched.keys()) if sched[k].type.value == "briefing"), None)

    return SetupDeps(
        plan=detect,
        pointer_manager=pointer_manager,
        git_identity=_git_identity,
        host_zone=host_timezone_name,
        detect_connectors=detect_connectors,
        list_uncovered=list_uncovered,
        draft=draft,
        invoke=invoke_scoutctl,
        add_custom=add_custom,
        first_briefing_slot=first_briefing_slot,
    )


def auto_argv(a: Answers, managed_by: str) -> list[str]:
    argv = [
        "bootstrap",
        "auto",
        "--no-interactive",
        "--yes",
        "--managed-by",
        managed_by,
        "--instance-name",
        a.instance_name,
        "--user-name",
        a.name,
        "--user-email",
        a.email,
        "--connectors",
        ",".join(sorted(a.connectors)),
    ]
    if a.timezone:
        argv += ["--timezone", a.timezone]
    for flag, value in (
        ("--user-slack-id", a.slack_id),
        ("--github-username", a.github_username),
        ("--github-repos", a.github_repos),
    ):
        if value:
            argv += [flag, value]
    return [*argv, "--max-budget", a.max_budget]


def _ask_required(p: Prompter, question: str, default: str, check: Callable[[str], bool], why: str) -> str:
    while True:
        value = p.ask(question, default)
        if check(value):
            return value
        p.say(why)


def _valid_zone(zone: str) -> bool:
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _ask_timezone(p: Prompter, opts: SetupOptions, deps: SetupDeps) -> str:
    if opts.timezone or opts.yes:
        return opts.timezone
    host = deps.host_zone()
    if host and p.confirm(f"Scout follows this computer's timezone ({host}), so it stays right when you travel. Keep it?", True):
        return ""
    return _ask_required(
        p, "Timezone to pin (IANA name, e.g. Europe/Berlin)", "", _valid_zone, "That isn't an IANA zone name."
    )


def _float_ok(text: str, *, allow_blank: bool) -> bool:
    if not text:
        return allow_blank
    try:
        return float(text) > 0
    except ValueError:
        return False


def _ask_connectors(p: Prompter, opts: SetupOptions, deps: SetupDeps, a: Answers) -> None:
    if opts.connectors is not None:
        a.connectors = {c.strip() for c in opts.connectors.split(",") if c.strip()}
    else:
        dets = deps.detect_connectors()
        a.connectors = {k for k, d in dets.items() if d.status is DetectStatus.CONNECTED}
        if not opts.yes:
            names = sorted(dets)
            while True:
                p.say("Connected tools:")
                for i, k in enumerate(names, 1):
                    mark = "✓" if k in a.connectors else ("?" if dets[k].status is DetectStatus.UNKNOWN else "✗")
                    hint = " (sign in through /mcp in Claude Code)" if dets[k].status is DetectStatus.NEEDS_AUTH else ""
                    p.say(f"  {i} [{mark}] {k}{hint}")
                raw = p.ask("Toggle by number, or press enter to continue")
                if not raw:
                    break
                for part in raw.replace(",", " ").split():
                    if part.isdigit() and 1 <= int(part) <= len(names):
                        a.connectors ^= {names[int(part) - 1]}
    given = {"user_slack_id": opts.slack_id, "github_username": opts.github_username, "github_repos": opts.github_repos}
    needs = {
        "slack": ["user_slack_id"],
        "github": ["github_username", "github_repos"],
    }
    values = dict(given)
    for connector in sorted(a.connectors):
        for name in needs.get(connector, []):
            if not values[name] and not opts.yes:
                values[name] = p.ask(_INPUTS[name][1])
    a.slack_id, a.github_username, a.github_repos = (
        values["user_slack_id"],
        values["github_username"],
        values["github_repos"],
    )


def _gather(opts: SetupOptions, p: Prompter, deps: SetupDeps) -> Answers | None:
    a = Answers()
    git_name, git_email = deps.git_identity()
    if opts.yes:
        a.instance_name = opts.instance_name or "Scout"
        a.name, a.email = opts.name, opts.email
        if not a.name or not a.email:
            p.say(f"--yes needs --name and --email. Headless flags: {HEADLESS_FLAGS}")
            return None
    else:
        a.instance_name = opts.instance_name or p.ask("Name this Scout instance", "Scout")
        a.name = opts.name or _ask_required(p, "Your name", git_name, bool, "Scout needs a name for commits.")
        a.email = opts.email or _ask_required(
            p, "Your email", git_email, lambda v: "@" in v, "That doesn't look like an email address."
        )
    a.timezone = _ask_timezone(p, opts, deps)
    _ask_connectors(p, opts, deps, a)
    a.max_budget = opts.max_budget or (
        "5.00"
        if opts.yes
        else _ask_required(
            p,
            "Budget per session, USD",
            "5.00",
            lambda v: _float_ok(v, allow_blank=False),
            "Enter an amount above 0.",
        )
    )
    a.daily_budget = opts.daily_budget or (
        ""
        if opts.yes
        else _ask_required(
            p,
            "Daily budget, USD (blank for none)",
            "",
            lambda v: _float_ok(v, allow_blank=True),
            "Enter an amount above 0, or leave it blank.",
        )
    )
    return a


def _apply_drafts(a: Answers, p: Prompter, deps: SetupDeps) -> None:
    """Task 8 fills this in; Task 7 has no drafts to apply."""


def run_setup(opts: SetupOptions, prompter: Prompter, deps: SetupDeps) -> int:
    p = prompter
    if deps.pointer_manager() == "scout-app":
        p.say("Scout.app manages this engine — open Scout.app to set up or change Scout.")
        return 2
    plan = deps.plan(opts.vault)
    if plan.action is AutoAction.REFUSED:
        p.say(f"Can't set up Scout in {opts.vault}: {plan.reason}")
        return 2
    if plan.action is AutoAction.UPGRADE:
        p.say(f"Found your vault at {opts.vault}, upgrading.")
        return deps.invoke(
            ["bootstrap", "auto", "--no-interactive", "--yes", "--managed-by", opts.managed_by or "preserve"]
        )
    try:
        answers = _gather(opts, p, deps)
    except (EOFError, KeyboardInterrupt):
        p.say(CANCELLED)
        return 1
    if answers is None:
        return 2
    code = deps.invoke(auto_argv(answers, opts.managed_by or "claude-code"))
    if code == 2:
        return 2
    if answers.daily_budget:
        deps.invoke(["budget", "set", "--daily-usd", answers.daily_budget])
    _apply_drafts(answers, p, deps)
    first = opts.first_run
    if first is None:
        first = False if opts.yes else p.confirm("Run your first briefing now?", False)
    if first:
        slot = deps.first_briefing_slot()
        if slot:
            deps.invoke(["schedule", "fire-now", slot])
        else:
            p.say("No briefing slot in your schedule; the first scheduled run will be the first briefing.")
    else:
        p.say("Your first scheduled run fires at the next slot in .scout-state/schedule.yaml.")
    return code
```

`test_toggling_and_a_pinned_timezone` answers "Mars/Olympus" first: `_ask_required` says "That isn't an IANA zone name." and asks again.

- [ ] **Step 4: Add the CLI command**

In `cli.py`, next to the other top-level `@app.command`s:

```python
@app.command("setup")
def setup_cmd(
    vault: Path | None = typer.Option(None, "--vault", help="Vault folder. Default: $SCOUT_DATA_DIR or ~/Scout."),
    instance_name: str = typer.Option("", "--instance-name"),
    name: str = typer.Option("", "--name", help="Your name (commits, the KB)."),
    email: str = typer.Option("", "--email", help="Your email (git config)."),
    timezone: str = typer.Option("", "--timezone", help="IANA zone to pin. Default: follow the computer."),
    connectors: str | None = typer.Option(None, "--connectors", help="Comma-separated. Default: detect."),
    slack_id: str = typer.Option("", "--slack-id"),
    github_username: str = typer.Option("", "--github-username"),
    github_repos: str = typer.Option("", "--github-repos"),
    max_budget: str = typer.Option("", "--max-budget", help="USD per session. Default 5.00."),
    daily_budget: str = typer.Option("", "--daily-budget", help="USD per day. Default: none."),
    first_run: bool | None = typer.Option(None, "--first-run/--no-first-run"),
    managed_by: str = typer.Option("", "--managed-by", help="install.sh passes install.sh."),
    claude_bin: str = typer.Option("", "--claude-bin"),
    yes: bool = typer.Option(False, "--yes", help="Ask nothing; take the flags and defaults."),
) -> None:
    """Set up Scout in a terminal: your details, connectors, the vault, the schedule."""
    import os

    from scout import paths as _paths
    from scout.setup_wizard import HEADLESS_FLAGS, HeadlessPrompter, SetupOptions, TtyPrompter, default_deps, run_setup

    if vault is not None:
        os.environ["SCOUT_DATA_DIR"] = str(vault.expanduser())
    prompter = HeadlessPrompter() if yes else TtyPrompter.open()
    if prompter is None:
        typer.echo(f"error: no terminal to ask questions on. Run it headless: scoutctl setup {HEADLESS_FLAGS}", err=True)
        raise typer.Exit(code=2)
    opts = SetupOptions(
        vault=_paths.data_dir(),
        instance_name=instance_name,
        name=name,
        email=email,
        timezone=timezone,
        connectors=connectors,
        slack_id=slack_id,
        github_username=github_username,
        github_repos=github_repos,
        max_budget=max_budget,
        daily_budget=daily_budget,
        first_run=first_run,
        managed_by=managed_by,
        yes=yes,
    )
    raise typer.Exit(code=run_setup(opts, prompter, default_deps(claude_bin)))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_setup_wizard.py tests/unit/test_cli_surface.py -q`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add scout/setup_wizard.py scout/cli.py tests/unit/test_setup_wizard.py
git commit -m "feat(setup): scoutctl setup — the terminal wizard over bootstrap auto"
```

---

### Task 8: "Also connected" in the wizard, and `scoutctl connectors setup`

**Files:**
- Modify: `plugin/engine/scout/setup_wizard.py` (`offer_uncovered`, `_apply_drafts`, `run_connectors_setup`; call `offer_uncovered` from `_gather`)
- Modify: `plugin/engine/scout/cli.py` (inside `_register_connectors`, after `cli_connectors_uncovered`)
- Modify: `plugin/CHANGELOG.md` (`## [Unreleased]`, `### Added`)
- Test: `plugin/engine/tests/unit/test_setup_wizard_uncovered.py`

**Interfaces:**
- Consumes: Task 7's `Prompter`, `SetupDeps`, `Answers`, `KeptDraft`, `CANCELLED`; Task 2's JSON; Task 4's JSON; Task 5's `busy`.
- Produces: `offer_uncovered(p: Prompter, deps: SetupDeps) -> list[KeptDraft]`; `apply_drafts(drafts: list[KeptDraft], p: Prompter, deps: SetupDeps) -> int` (the number applied); `run_connectors_setup(vault: Path, p: Prompter, deps: SetupDeps) -> int`. CLI: `scoutctl connectors setup`.

- [ ] **Step 1: Write the failing tests**

```python
"""The "Also connected" step: every uncovered server is offered; none is dead-ended (spec §4.3, §4.4)."""

from __future__ import annotations

from dataclasses import replace

from scout import setup_wizard as sw
from tests.unit.test_setup_wizard import Scripted, _deps, _opts

T = "mcp__claude_ai_Example_Suite__"
SERVERS = {
    "schema_version": 1,
    "error": None,
    "servers": [
        {"name": "claude.ai Example CRM", "slug": "claude_ai_Example_CRM", "status": "needs_auth", "evidence": ""},
        {"name": "claude.ai Example Suite", "slug": "claude_ai_Example_Suite", "status": "connected", "evidence": ""},
    ],
}
DRAFTED = {
    "status": "drafted",
    "definitions": [
        {"key": "suite_mail", "display_name": "Mail suite", "server": "claude_ai_Example_Suite",
         "probe": f"{T}list_folders", "preset": "mail", "inbound": {"tools": [f"{T}search_messages"]},
         "needs_user_input": ["mailbox"]},
    ],
    "summary": [{"key": "suite_mail", "scans": "new mail", "looks_up": "nothing"}],
}
DEAD_ENDS = ("not supported", "isn't supported", "can't read", "cannot read", "no probe")


def test_offer_drafts_keeps_and_signs_in(tmp_path):
    deps, _ = _deps(tmp_path)
    deps = replace(deps, list_uncovered=lambda: SERVERS, draft=lambda s: DRAFTED)
    p = Scripted(["y", "team", "y"])  # add Suite? mailbox, keep?
    kept = sw.offer_uncovered(p, deps)
    assert [k.definition["key"] for k in kept] == ["suite_mail"]
    assert kept[0].inputs == {"mailbox": "team"}
    said = "\n".join(p.said)
    assert "Example CRM" in said and "/mcp" in said
    assert "Mail suite · new mail · nothing" in said
    assert not any(d in said.lower() for d in DEAD_ENDS)


def test_a_failed_draft_offers_a_retry_never_a_dead_end(tmp_path):
    deps, _ = _deps(tmp_path)
    for status in ("no_read_tools", "invalid", "timeout", "error"):
        d = replace(deps, list_uncovered=lambda: SERVERS, draft=lambda s, st=status: {"status": st, "message": "x"})
        p = Scripted(["y"])
        assert sw.offer_uncovered(p, d) == []
        said = "\n".join(p.said).lower()
        assert "scoutctl connectors setup" in said
        assert not any(dead in said for dead in DEAD_ENDS)


def test_listing_error_is_said_not_hidden(tmp_path):
    deps, _ = _deps(tmp_path)
    deps = replace(deps, list_uncovered=lambda: {"schema_version": 1, "servers": [], "error": "boom"})
    p = Scripted([])
    assert sw.offer_uncovered(p, deps) == []
    assert any("couldn't list" in s.lower() for s in p.said)


def test_setup_applies_kept_drafts_after_the_vault_exists(tmp_path):
    added = []
    deps, calls = _deps(tmp_path)
    deps = replace(
        deps,
        list_uncovered=lambda: SERVERS,
        draft=lambda s: DRAFTED,
        add_custom=lambda d, i: added.append((d["key"], i, [c[0] for c in calls])) or {"status": "applied", "message": "Live"},
    )
    # instance, name, email, keep tz, toggle, slack id, add Suite?, mailbox, keep?, per-session, daily, first run
    p = Scripted(["", "", "", "", "", "U1", "y", "team", "y", "", "", "n"])
    assert sw.run_setup(_opts(tmp_path), p, deps) == 0
    assert added == [("suite_mail", {"mailbox": "team"}, ["bootstrap"])]  # applied after bootstrap auto


def test_headless_setup_skips_drafting_and_prints_the_hint(tmp_path):
    deps, _ = _deps(tmp_path)
    drafted = []
    deps = replace(deps, list_uncovered=lambda: SERVERS, draft=lambda s: drafted.append(s) or DRAFTED)
    opts = _opts(tmp_path, yes=True, name="Alex", email="alex@example.com", first_run=False)
    p = Scripted([])
    p_headless = sw.HeadlessPrompter()
    p_headless.say = p.say  # capture
    assert sw.run_setup(opts, p_headless, deps) == 0
    assert drafted == []
    assert any("scoutctl connectors setup" in s for s in p.said)


def test_busy_apply_says_to_retry(tmp_path):
    deps, _ = _deps(tmp_path)
    deps = replace(deps, add_custom=lambda d, i: {"status": "busy", "message": "A Scout session is running"})
    p = Scripted([])
    assert sw.apply_drafts([sw.KeptDraft(DRAFTED["definitions"][0], {})], p, deps) == 0
    assert any("scoutctl connectors setup" in s for s in p.said)


def test_connectors_setup_needs_a_vault(tmp_path):
    deps, _ = _deps(tmp_path)
    p = Scripted([])
    assert sw.run_connectors_setup(tmp_path / "missing", p, deps) == 2
    assert any("scoutctl setup" in s for s in p.said)


def test_connectors_setup_on_a_vault(tmp_path):
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text("instance: {name: Scout}\n")
    added = []
    deps, _ = _deps(tmp_path)
    deps = replace(deps, list_uncovered=lambda: SERVERS, draft=lambda s: DRAFTED,
                   add_custom=lambda d, i: added.append(d["key"]) or {"status": "applied", "message": "Live"})
    assert sw.run_connectors_setup(vault, Scripted(["y", "team", "y"]), deps) == 0
    assert added == ["suite_mail"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_setup_wizard_uncovered.py -q`
Expected: FAIL with `AttributeError: module 'scout.setup_wizard' has no attribute 'offer_uncovered'`.

- [ ] **Step 3: Implement**

In `setup_wizard.py`, add:

```python
_SIGN_IN = (
    "sign in first: in Claude Code run /mcp (or claude.ai ▸ Settings ▸ Connectors), "
    "then run `scoutctl connectors setup`."
)


def offer_uncovered(p: Prompter, deps: SetupDeps) -> list[KeptDraft]:
    """Offer every connected server no connector reads; return the drafts the user keeps."""
    listing = deps.list_uncovered()
    if listing.get("error"):
        p.say(f"Couldn't list your other connected tools ({listing['error']}). Run `scoutctl connectors setup` later.")
        return []
    servers = listing.get("servers") or []
    if not servers:
        return []
    p.say("Also connected: " + ", ".join(s["name"] for s in servers) + ".")
    kept: list[KeptDraft] = []
    for s in servers:
        name = s["name"]
        if s["status"] != "connected":
            p.say(f"  {name}: {_SIGN_IN}")
            continue
        if not p.confirm(f"  Add {name}?", True):
            continue
        p.say("    drafting (about 30 s)…")
        result = deps.draft(name)
        status = result.get("status")
        if status == "needs_auth":
            p.say(f"    {name}: {_SIGN_IN}")
            continue
        if status != "drafted":
            reason = result.get("message") or status
            p.say(f"    Scout couldn't find anything to read in {name} yet ({reason}). Retry with `scoutctl connectors setup`.")
            continue
        summary = {x["key"]: x for x in result.get("summary", [])}
        for d in result.get("definitions", []):
            line = summary.get(d["key"], {})
            p.say(f"    {d['display_name']} · {line.get('scans', '')} · {line.get('looks_up', '')}")
            inputs = {n: p.ask(f"    {n.replace('_', ' ')} for {d['display_name']}") for n in d.get("needs_user_input", [])}
            if p.confirm("    Keep it?", True):
                kept.append(KeptDraft(d, inputs))
    return kept


def apply_drafts(drafts: list[KeptDraft], p: Prompter, deps: SetupDeps) -> int:
    applied = 0
    for k in drafts:
        out = deps.add_custom(k.definition, k.inputs)
        label = k.definition.get("display_name", k.definition.get("key", ""))
        status = out.get("status")
        if status in ("applied", "unchanged"):
            applied += 1
            p.say(f"  Added {label}.")
        elif status == "busy":
            p.say(f"  {label}: a Scout session is running. Run `scoutctl connectors setup` again when it finishes.")
        else:
            issues = "; ".join(f"{i['path']}: {i['message']}" for i in out.get("issues", []))
            p.say(f"  {label}: {out.get('message') or status}{(' — ' + issues) if issues else ''}")
    return applied


def run_connectors_setup(vault: Path, p: Prompter, deps: SetupDeps) -> int:
    if not (vault / "scout-config.yaml").exists():
        p.say(f"No Scout vault at {vault}. Set Scout up first: open Scout.app or run `scoutctl setup`.")
        return 2
    try:
        kept = offer_uncovered(p, deps)
    except (EOFError, KeyboardInterrupt):
        p.say(CANCELLED)
        return 1
    apply_drafts(kept, p, deps)
    return 0
```

Replace the placeholder `_apply_drafts` with a call: in `run_setup`, change `_apply_drafts(answers, p, deps)` to `apply_drafts(answers.drafts, p, deps)` and delete `_apply_drafts`. In `_gather`, directly after `_ask_connectors(p, opts, deps, a)`:

```python
    if opts.yes:
        listing = deps.list_uncovered()
        names = [s["name"] for s in listing.get("servers") or []]
        if names:
            p.say(f"You also have {', '.join(names)} connected — run `scoutctl connectors setup` to add them.")
    else:
        a.drafts = offer_uncovered(p, deps)
```

Task 7's tests need no change: their `list_uncovered` returns no servers, so no "Also connected" question is asked. Step 5 re-runs them to confirm.

In `cli.py`, inside `_register_connectors()`, after `cli_connectors_uncovered`:

```python
    @connectors_app.command("setup")
    def cli_connectors_setup(
        claude_bin: str = typer.Option("", "--claude-bin", help="Claude Code binary. Default: auto-detect."),
    ) -> None:
        """Offer every connected tool Scout doesn't read yet, draft it, and add the ones you keep."""
        from scout import paths as _paths
        from scout.setup_wizard import TtyPrompter, default_deps, run_connectors_setup

        prompter = TtyPrompter.open()
        if prompter is None:
            typer.echo("error: needs a terminal. In Claude Code, run /scout-connect instead.", err=True)
            raise typer.Exit(code=2)
        raise typer.Exit(code=run_connectors_setup(_paths.data_dir(), prompter, default_deps(claude_bin)))
```

- [ ] **Step 4: Add the changelog entry**

Under `## [Unreleased]` → `### Added` in `plugin/CHANGELOG.md`:

```markdown
- **`scoutctl setup`, a terminal setup wizard** (`engine/scout/setup_wizard.py`, `engine/scout/cli.py`) — asks what `/scout-setup` asked (name, email, timezone, connectors, budget), then runs `bootstrap auto` in-process; on an existing vault it upgrades without questions. `--yes` with `--name`/`--email` runs it headless. Every other tool you have connected is offered too: **`scoutctl connectors uncovered`** lists connected MCP servers no connector reads (no LLM), and **`scoutctl connectors custom draft --server NAME`** drafts their definitions through a locked-down headless `claude -p` (`--permission-mode dontAsk`, ToolSearch only), which the engine then checks: only that server's tools, no tool that writes. Kept drafts are added with `custom add` after the vault exists. **`scoutctl connectors setup`** does the same for an existing vault. `custom add|remove` now answer `busy` (exit 4) instead of a generic error while a session holds the lock, and `--no-wait` answers at once; `custom list` returns full definitions and invalid entries as rows. (#326, #321)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_setup_wizard_uncovered.py tests/unit/test_setup_wizard.py tests/unit/test_cli_surface.py -q`
Expected: all PASS.

- [ ] **Step 6: Run the whole engine suite, lint, commit, and open the phase-1 PR**

Run: `.venv/bin/pytest -q -x -n auto` (or without `-n auto` if `pytest-xdist` isn't installed).
Expected: all PASS.

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add scout/setup_wizard.py scout/cli.py tests/unit/test_setup_wizard_uncovered.py ../CHANGELOG.md
git commit -m "feat(setup): offer every uncovered server; scoutctl connectors setup"
git push -u origin HEAD
gh pr create --repo Raven-Scout/Scout --draft --title "feat(setup): scoutctl setup + connector drafting (setup retirement, phase 1)" --body "Phase 1 of docs/superpowers/plans/2026-10-06-scout-setup-retirement.md (spec #326). Unblocks #321's app contract."
```

---

## Phase 2 — terminal surface and commands (one PR; `/scout-setup` still works)

### Task 9: `install.sh` runs `scoutctl setup`

**Files:**
- Modify: `install.sh` (repo root)
- Test: `plugin/engine/tests/unit/test_install_sh_runs_setup.py`

**Interfaces:**
- Consumes: Task 7's `scoutctl setup --managed-by install.sh`.
- Produces: `install.sh [--check] [setup flags…]`. Every argument except a leading `--check` goes to `scoutctl setup`.

- [ ] **Step 1: Write the failing test**

```python
"""install.sh ends by running scoutctl setup with every argument it was given (spec §5)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
INSTALL_SH = REPO / "install.sh"


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)


def _world(tmp_path: Path) -> tuple[dict[str, str], Path]:
    home = tmp_path / "home"
    home.mkdir()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    root = tmp_path / "plugin-root"
    (root / "scripts").mkdir(parents=True)
    log = tmp_path / "setup.log"
    _stub(
        root / "scripts" / "install-venv.sh",
        f'd="$(cd "$(dirname "$0")/.." && pwd)/.venv/bin"; mkdir -p "$d"\n'
        f'printf \'#!/bin/bash\\n[ "$1" = --help ] && exit 0\\necho "$*" >> {log}\\n\' > "$d/scoutctl"\n'
        'chmod +x "$d/scoutctl"\n',
    )
    listing = json.dumps([{"id": "scout@scout-plugin", "installPath": str(root)}])
    _stub(bin_ / "claude", f"[ \"$1 $2\" = 'plugin list' ] && echo '{listing}'\nexit 0\n")
    _stub(bin_ / "uv", f'while [ $# -gt 0 ] && [ "$1" != python ]; do shift; done; shift; exec {sys.executable} "$@"\n')
    _stub(bin_ / "xcode-select", "exit 0\n")
    _stub(bin_ / "git", "exit 0\n")
    env = {"HOME": str(home), "PATH": f"{bin_}:/usr/bin:/bin", "SCOUT_KNOWN_MARKETPLACES": str(tmp_path / "none.json")}
    return env, log


def test_install_sh_hands_every_argument_to_setup(tmp_path):
    env, log = _world(tmp_path)
    done = subprocess.run(
        ["bash", str(INSTALL_SH), "--yes", "--name", "Alex Example", "--email", "alex@example.com"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert log.read_text().strip() == "setup --managed-by install.sh --yes --name Alex Example --email alex@example.com"
    assert "/scout-setup" not in done.stdout + done.stderr


def test_check_mode_never_runs_setup(tmp_path):
    env, log = _world(tmp_path)
    done = subprocess.run(["bash", str(INSTALL_SH), "--check"], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert not log.exists()


def test_the_closing_text_mentions_the_app_only_on_macos():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert 'exec "$ROOT/.venv/bin/scoutctl" setup --managed-by install.sh "$@"' in text
    assert "/scout-setup" not in text
```

The stubbed `scoutctl` writes `"$*"`, which joins arguments with spaces, so `Alex Example` appears unquoted in the log; the test checks that order and content survive.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_install_sh_runs_setup.py -q`
Expected: FAIL (`setup.log` doesn't exist; `install.sh` still prints the `/scout-setup` hand-off).

- [ ] **Step 3: Implement**

In `install.sh`:

1. Replace the header comment's lines 4–6 with:

```bash
# Installs the plugin + engine, then runs `scoutctl setup` (your details, connectors,
# the vault, the schedule). Re-running it on an existing vault upgrades it.
#
# Flags: --check  (verify preconditions only; make no changes)
# Anything else goes to `scoutctl setup`, e.g. headless:
#   curl -fsSL …/install.sh | bash -s -- --yes --name "Alex" --email alex@example.com
```

2. Replace `[ "${1:-}" = "--check" ] && CHECK_ONLY=1` with:

```bash
if [ "${1:-}" = "--check" ]; then CHECK_ONLY=1; shift; fi
```

3. Replace the final `cat <<'DONE' … DONE` block with:

```bash
echo
echo "✅ Scout plugin + engine installed."
if [ "$(uname -s)" = "Darwin" ]; then
  echo "   Prefer a Mac app? Download Scout.app from https://raven-scout.github.io/Scout/ — it adopts this install."
fi
echo
exec "$ROOT/.venv/bin/scoutctl" setup --managed-by install.sh "$@"
```

- [ ] **Step 4: Run the tests and shellcheck**

Run: `.venv/bin/pytest tests/unit/test_install_sh_runs_setup.py tests/unit/test_plugin_update_switches_version.py tests/unit/test_plugin_root_resolver.py -q && shellcheck -S error ../../install.sh`
Expected: all PASS; shellcheck prints nothing.

- [ ] **Step 5: Commit**

```bash
git add ../../install.sh tests/unit/test_install_sh_runs_setup.py
git commit -m "feat(install): install.sh ends in scoutctl setup; re-running it upgrades"
```

---

### Task 10: `/scout-connect`

**Files:**
- Create: `plugin/commands/scout-connect.md`
- Test: `plugin/engine/tests/unit/test_scout_connect_prose.py`

**Interfaces:**
- Consumes: `scoutctl connectors uncovered --json` (Task 2), `scoutctl connectors custom draft --server NAME --json` (Task 4), `custom add --file - [--input N=V]`, `custom remove KEY` and the `busy` status (Task 5).

- [ ] **Step 1: Write the failing prose guard**

```python
"""The Claude Code commands route every tool to custom connectors and never dead-end the user."""

from __future__ import annotations

from pathlib import Path

COMMANDS = Path(__file__).parent.parent.parent.parent / "commands"
TEMPLATE = Path(__file__).parent.parent.parent / "scout" / "defaults" / "draft-connector.md"
DEAD_ENDS = ("not supported", "isn't supported", "doesn't come with", "does not come with", "no probe for it", "can't read")


def _text(name: str) -> str:
    return (COMMANDS / name).read_text(encoding="utf-8")


def test_scout_connect_calls_the_engine_contract():
    text = _text("scout-connect.md")
    assert text.startswith("---\nname: scout-connect\n")
    for call in ("connectors uncovered --json", "connectors custom draft --server", "connectors custom add --file -",
                 "connectors custom remove", "busy"):
        assert call in text, call


def test_scout_connect_does_not_restate_the_drafting_rules():
    assert "1–4 read tools per activity" in TEMPLATE.read_text(encoding="utf-8")
    assert "1–4 read tools per activity" not in _text("scout-connect.md")


def test_no_command_dead_ends_the_user():
    for md in sorted(COMMANDS.glob("*.md")):
        lowered = md.read_text(encoding="utf-8").lower()
        for phrase in DEAD_ENDS:
            assert phrase not in lowered, f"{md.name} contains {phrase!r}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_scout_connect_prose.py -q`
Expected: FAIL with `FileNotFoundError: … commands/scout-connect.md`. (If `test_no_command_dead_ends_the_user` also fails on an existing command, fix that wording in this task too.)

- [ ] **Step 3: Write `plugin/commands/scout-connect.md`**

````markdown
---
name: scout-connect
description: Add or remove a tool Scout reads — any connected MCP server Scout doesn't read yet (a mail suite, a chat tool, a CRM…). Drafts the definition through the engine, shows it to you, and makes it live.
---

# Scout Connect

Usage: `/scout-connect` lists the connected tools Scout doesn't read yet; `/scout-connect <server>` adds one; `/scout-connect --remove <key>` removes one.

Resolve the engine at the top of every shell block (each block is a fresh process). A custom connector is vault data, so this works however the engine was installed, Scout.app included: the `scoutctl` shim on `PATH` follows the engine pointer.

```bash
SCOUTCTL="$(command -v scoutctl)"
[ -n "$SCOUTCTL" ] || { echo "SCOUTCTL_NOT_FOUND"; exit 1; }
```

If it prints `SCOUTCTL_NOT_FOUND`, Scout isn't set up yet: tell the user to open Scout.app, or run `scoutctl setup` in a terminal (which offers every connected tool), and stop.

## Hard rule

A tool ends in exactly one of three states: **added**, **skipped because the user chose to**, or **sign in first**. For the last, say: *"Sign in through `/mcp` (or claude.ai → Settings → Connectors), then run `/scout-connect <server>` again."* Never tell the user a tool is out of Scout's reach.

## 1. Find the server

```bash
"$SCOUTCTL" connectors uncovered --json
```

- `error` is set: show it, and suggest re-running after checking `claude mcp list` in a terminal.
- With no argument: list `servers[].name` with their `status`, and ask which to add. A `needs_auth` server gets the sign-in sentence.
- With an argument: match it against `servers[].name` (case-insensitive, substring). If nothing matches, the server is either already read by Scout (say so; `"$SCOUTCTL" connectors custom list` shows the custom ones) or not connected: use the sign-in sentence.

## 2. Draft

```bash
"$SCOUTCTL" connectors custom draft --server "<server name exactly as listed>" --json
```

It takes up to a minute. Act on `status`:

- `drafted`: show each definition in three lines, from `summary`: *display name · what it scans · what it looks up*. Ask the user to confirm, or to adjust it: drop an activity, narrow the focus (write a `focus` or `when` sentence in place of the preset), or skip a connector. Ask for each name in a definition's `needs_user_input`.
- `needs_auth`: the sign-in sentence.
- `no_read_tools`, `invalid`, `timeout`, `error`: "Scout couldn't find anything to read in <server> yet (<message>)." Offer to retry the draft once.

## 3. Add

For each confirmed definition, pipe its JSON (with any adjustment the user asked for) to the engine, which validates it again:

```bash
"$SCOUTCTL" connectors custom add --file - --input <name>=<value> <<'EOF'
<the definition JSON>
EOF
```

Read the JSON it prints:

- `applied` / `unchanged`: the next scheduled run reads it.
- `invalid`: fix each `issues[].path` and retry; don't show raw errors unless you can't fix them.
- `busy`: a Scout session is running; try again in a minute.
- `deferred`: saved. If `waiting` is non-empty, those brain files have a pending review: help the user finish it, then `"$SCOUTCTL" bootstrap resolve <file>`. Otherwise it goes live at the next upgrade.
- `conflict`: saved; walk the user through merging the sidecar in `sidecars`, then `"$SCOUTCTL" bootstrap resolve <file>`.
- `error`: show `message`.

## Remove

`/scout-connect --remove <key>`: confirm with the user, then run `"$SCOUTCTL" connectors custom remove <key>` and report the status the same way.
````

- [ ] **Step 4: Run the guard and validate the plugin**

Run: `.venv/bin/pytest tests/unit/test_scout_connect_prose.py -q && (cd ../.. && claude plugin validate plugin)`
Expected: PASS; `claude plugin validate` reports no errors. (If `claude` isn't installed where you run this, say so in the PR rather than skipping silently.)

- [ ] **Step 5: Commit**

```bash
git add ../commands/scout-connect.md tests/unit/test_scout_connect_prose.py
git commit -m "feat(commands): /scout-connect — a chat front end over connector drafting"
```

---

### Task 11: `/scout-update` and `/scout-status` lose the auto-update prompts; `/scout-update` gains the pointer

**Files:**
- Modify: `plugin/commands/scout-update.md` (the description line, line 10, Step 0's `INSTALL_INCOMPLETE` and `NO_VAULT` bullets, Step 3's last bullet, the whole "Auto-update nudge" section)
- Modify: `plugin/commands/scout-status.md` (§3f, "Update status")
- Modify: `plugin/engine/tests/unit/test_auto_update_config.py` (replace `test_the_update_nudge_uses_the_engine_writer`)
- Test: `plugin/engine/tests/unit/test_scout_connect_prose.py` (add two tests)

- [ ] **Step 1: Write the failing tests**

Append to `test_scout_connect_prose.py`:

```python
def test_scout_update_points_at_scout_connect_and_has_no_nudge():
    text = _text("scout-update.md")
    assert "/scout-connect" in text and "connectors uncovered --json" in text
    assert "set-auto-update" not in text and "Auto-update nudge" not in text
    assert "/scout-setup" not in text


def test_scout_status_no_longer_reports_auto_update():
    text = _text("scout-status.md")
    assert "AUTO_UPDATE_ON" not in text and "Auto-update:" not in text
```

In `test_auto_update_config.py`, replace `test_the_update_nudge_uses_the_engine_writer` with:

```python
def test_no_command_asks_about_auto_update() -> None:
    """The auto-update question is retired (spec S2): no command sets the preference any more."""
    for md in sorted((REPO_ROOT / "commands").glob("*.md")):
        assert "set-auto-update" not in md.read_text(encoding="utf-8"), md.name
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_scout_connect_prose.py tests/unit/test_auto_update_config.py -q`
Expected: FAIL on both new tests.

- [ ] **Step 3: Edit `scout-update.md`**

1. Description line → `description: Upgrade an existing Scout vault to the current plugin version. Idempotent — re-runs converge to the same state. For a first-time install, open Scout.app or run scoutctl setup.`
2. Line 10 → `This command is for **existing vaults only**. If no vault exists, refuse and tell the user to open Scout.app or run \`scoutctl setup\` in a terminal.`
3. `INSTALL_INCOMPLETE` bullet → `- \`INSTALL_INCOMPLETE\`: "The Scout install at \`~/Scout/\` was interrupted before it finished, so there is nothing to upgrade yet. Run \`scoutctl setup\` in a terminal (or open Scout.app) to finish it — it resumes the interrupted install rather than starting over." Stop here (\`bootstrap upgrade\` refuses a vault in this state).`
4. `NO_VAULT` bullet → `- \`NO_VAULT\`: "No Scout vault found at \`~/Scout/\`. Open Scout.app, or run \`scoutctl setup\` in a terminal, for a fresh install."`
5. Replace Step 3's last bullet (`- \`~/Scout/connector-probes.local.yaml\` …`) with:

````markdown
- `~/Scout/connectors.custom.yaml` (custom connectors) and
  `~/Scout/connector-probes.local.yaml` (custom probes) are user files, never
  templated, so upgrades leave them untouched — and every upgrade re-renders the
  custom connectors' sections from the current templates.

## Step 4: Offer the tools Scout doesn't read yet

```bash
NEW_ROOT=""
if [ -e "$HOME/scout-plugin/.git" ] && [ -f "$HOME/scout-plugin/.claude-plugin/plugin.json" ]; then
  NEW_ROOT="$HOME/scout-plugin"           # legacy single-repo scout-plugin clone
elif [ -e "$HOME/scout-plugin/.git" ] && [ -f "$HOME/scout-plugin/plugin/.claude-plugin/plugin.json" ]; then
  NEW_ROOT="$HOME/scout-plugin/plugin"    # Raven-Scout/Scout monorepo clone: plugin under plugin/
fi
[ -n "$NEW_ROOT" ] || NEW_ROOT="$(claude plugin list --json 2>/dev/null \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);e=d if isinstance(d,list) else [p for ps in d.get("plugins",{}).values() for p in ps];print(next((p["installPath"] for p in e if p.get("id")=="scout@scout-plugin"),""))' 2>/dev/null)"
[ -n "$NEW_ROOT" ] || NEW_ROOT="$(ls -d "$HOME"/.claude/plugins/cache/scout-plugin/scout/*/ 2>/dev/null | sort -V | tail -1)"
NEW_ROOT="${NEW_ROOT%/}"
[ -n "$NEW_ROOT" ] || { echo "PLUGIN_ROOT_NOT_FOUND"; exit 1; }
SCOUTCTL="$NEW_ROOT/.venv/bin/scoutctl"
"$SCOUTCTL" connectors uncovered --json || true
```

For each entry in `servers`, say once: "You also have <name> connected — run `/scout-connect <name>` to have Scout read it." Skip this when `servers` is empty or `error` is set.
````

(The resolver lines are copied verbatim from the canonical resolver at the top of the file; `test_plugin_root_resolver.py` checks every copy is identical and followed by the `PLUGIN_ROOT_NOT_FOUND` stop.)

6. Delete the whole `## Auto-update nudge` section, from its heading to the end of the file.

- [ ] **Step 4: Edit `scout-status.md`**

1. In §3f, change the intro sentence to `Check the installed plugin version against the latest available:` and delete the paragraph `Then read \`auto_update.enabled\` …` together with the bash block under it.
2. Change `Store all three results` to `Store both results`.
3. In "Update status", change `Using the version info and auto-update flag collected in step 3f` to `Using the version info collected in step 3f`, and delete both `  Auto-update:  on  /  off` lines.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_scout_connect_prose.py tests/unit/test_auto_update_config.py tests/unit/test_plugin_root_resolver.py tests/unit/test_runbook_plugin_root.py tests/unit/test_runbook_install_incomplete.py tests/unit/test_plugin_update_switches_version.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add ../commands/scout-update.md ../commands/scout-status.md tests/unit/test_scout_connect_prose.py tests/unit/test_auto_update_config.py
git commit -m "feat(commands): /scout-update offers /scout-connect; auto-update prompts retired"
```

---

### Task 12: Plugin README — terminal install and custom connectors; phase-2 PR

**Files:**
- Modify: `plugin/README.md` (sections "1. Install the plugin + engine", "2. Create your vault", the Quick Start's two `/scout-setup` sentences; a new "Custom connectors" section after the connectors table that line 234 introduces)
- Modify: `plugin/CHANGELOG.md`

- [ ] **Step 1: Rewrite "1." and "2." as one step**

Replace from `### 1. Install the plugin + engine` up to (not including) `### 3. (Optional) Install the Mac app` with:

````markdown
### 1. Install Scout

In Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash
```

It checks prerequisites, installs [uv](https://docs.astral.sh/uv) if needed, adds the Scout marketplace to Claude Code, installs the plugin and builds the engine. Then it asks a few questions: your name and email (Scout follows your computer's timezone; you can pin another), which detected tools to use, your Slack member ID if Slack is on (Slack → your profile → ⋮ → *Copy member ID*), and a budget. Every other tool you have connected is offered as a custom connector. It then creates `~/Scout/`, installs the schedule, and offers to run your first briefing.

To install without questions (a provisioning script, CI):

```bash
curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash -s -- --yes --name "Alex" --email alex@example.com
```

`scoutctl setup --help` lists every flag. Re-running the installer on an existing vault upgrades it.
````

Renumber `### 3. (Optional) Install the Mac app` to `### 2. (Optional) Install the Mac app`. Leave its text and the "Updating later" paragraph as they are; Task 14 rewrites them for v0.15.0.

- [ ] **Step 2: Fix the Quick Start sentences**

- `Installed this way, the engine is built the first time you run \`/scout-setup\` (about a minute).` → `Installed this way, run the one-line installer above to build the engine and set Scout up — it finds the plugin you just installed.`
- Replace the paragraph beginning `Then run \`/scout-setup\` in any Claude Code session.` with: `\`scoutctl setup\` detects your connected tools (MCP connectors, \`gh\` CLI), collects your details (name, Slack ID, email), scaffolds the Scout directory with a knowledge graph ontology, assembles personalized skill files from phase modules matching your connectors, and configures scheduling. On an existing vault it upgrades instead.`

- [ ] **Step 3: Add the "Custom connectors" section**

After the paragraph at line 234 (`Scout works with any subset of connectors. …`), insert:

```markdown
### Custom connectors

Scout reads more than the tools it ships instructions for. Anything you have connected to Claude Code (a mail suite, a chat tool, a CRM, a support desk) can become a **custom connector**:

- During setup, `scoutctl setup` (and Scout.app's onboarding) offers every connected server no connector reads yet.
- Later, run `/scout-connect` in Claude Code, `scoutctl connectors setup` in a terminal, or use Scout.app's Settings ▸ Connectors.

Scout drafts each definition for you through a read-only headless Claude call: it reads the server's tool list and picks a few **read** tools for what's new (inbound), what you did (outbound) and what to look up on demand. The engine rejects any tool that sends, posts, creates, updates or deletes. You confirm the draft; it's saved in `~/Scout/connectors.custom.yaml` and survives upgrades. Tools that need you to sign in first say so, and pick up where they left off once you have.
```

- [ ] **Step 4: Changelog, then commit and open the phase-2 PR**

Add under `## [Unreleased]` → `### Added` in `plugin/CHANGELOG.md`:

```markdown
- **`install.sh` now finishes setup itself** (`install.sh`) — after installing the plugin and engine it runs `scoutctl setup`, so the terminal path no longer needs `/scout-setup`. Arguments after `bash -s --` pass through (`--yes --name … --email …` for a headless install), and re-running it on an existing vault upgrades it. **`/scout-connect`** (`commands/scout-connect.md`) adds or removes a custom connector from Claude Code through the same engine contract, and `/scout-update` now points at it for every connected tool Scout doesn't read yet. The auto-update question is gone from `/scout-update` and `/scout-status`: nothing acted on it. (#326)
```

```bash
git add ../README.md ../CHANGELOG.md
git commit -m "docs(plugin): install.sh is the whole terminal install; custom connectors section"
git push -u origin HEAD
gh pr create --repo Raven-Scout/Scout --draft --title "feat(install): install.sh runs scoutctl setup; /scout-connect (setup retirement, phase 2)" --body "Phase 2 of docs/superpowers/plans/2026-10-06-scout-setup-retirement.md (spec #326). /scout-setup still works; it becomes a stub in phase 4."
```

---

## Phase 4 — retire (gated; see each task)

Phase 3 is the app plan. Task 13 needs #317 merged and a review by the "Scout monorepo consolidation" session. Task 14 goes in the v0.15.0 release PR. Task 15 lands when the app plan has merged (v0.15.0 if it's in time, otherwise v0.16.0). Task 16 is the minor after Task 15 ships.

### Task 13: `release.sh` attaches `Scout.dmg` (after #317 merges)

> **Review:** the "Scout monorepo consolidation" session owns #317's `release.sh`. Ask it to review this task's diff before merging.
>
> **Reviewer requirements (relayed 2026-10-06). These supersede spec §8 on two points:**
> - Only a final release gets the stable name. An rc (`--prerelease --latest=false`) doesn't, because `/releases/latest/download/Scout.dmg` resolves to the Latest release anyway.
> - There is no post-publish `gh release view` check, because tests may use only the existing stub harness. The tests below cover the one `gh release create` call instead.

**Files:**
- Modify: `scripts/release.sh` (`build_and_publish`: where the asset list is built with `set -- "$dmg"`)
- Modify: `plugin/engine/tests/unit/test_release_script.py` (the existing stub harness only; `release_harness.py` is unchanged)

- [ ] **Step 1: Write the failing tests**

In `test_finalize_publishes_once_after_notarization`, change `assert "Scout-0.15.0.dmg" in line and "appcast.xml" not in line` to:

```python
    assert "Scout-0.15.0.dmg" in line and "appcast.xml" not in line
    assert any(w.endswith("/Scout.dmg") for w in line.split())
```

In `test_finalize_attaches_the_appcast_when_present`, after its assert, add:

```python
    assert any(w.endswith("/Scout.dmg") for w in next(c for c in r.calls() if c.startswith("gh release create")).split())
```

In `test_rc_is_a_prerelease_never_latest`, after its last assert, add:

```python
    assert not any(w.endswith("/Scout.dmg") for w in line.split())  # an rc isn't Latest; no stable name
```

Add:

```python
def test_the_stable_dmg_is_a_copy_never_signed_or_notarized_again(tmp_path):
    r = make_repo(tmp_path)
    _merge_release(r)
    done = r.run("finalize", "v0.15.0")
    assert done.returncode == 0, done.stderr
    calls = r.calls()
    assert sum(c.startswith("gh release create") for c in calls) == 1
    touched = [c for c in calls if c.split()[0] in ("codesign", "xcrun", "spctl", "hdiutil")]
    assert not any(w.endswith("/Scout.dmg") for c in touched for w in c.split())
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_release_script.py -q`
Expected: the finalize and appcast tests FAIL, because there is no `/Scout.dmg` asset yet. The rc test and the new copy test already pass; they guard the implementation.

- [ ] **Step 3: Implement**

In `scripts/release.sh`, `build_and_publish`, replace

```bash
  set -- "$dmg"
  [ ! -f "$build/appcast.xml" ] || set -- "$@" "$build/appcast.xml"
```

with

```bash
  set -- "$dmg"
  if [ "$kind" = release ]; then
    # The website's Download for Mac button links to /releases/latest/download/Scout.dmg.
    # A plain copy of the DMG notarized and stapled above: never re-signed. An rc isn't
    # Latest, so it doesn't get one. Sparkle's appcast keeps the versioned name.
    cp "$dmg" "$build/release/Scout.dmg"
    set -- "$@" "$build/release/Scout.dmg"
  fi
  [ ! -f "$build/appcast.xml" ] || set -- "$@" "$build/appcast.xml"
```

The publish stays the single `gh release create "$tag" "$@" …` call after notarization.

- [ ] **Step 4: Run the tests and shellcheck**

Run: `.venv/bin/pytest tests/unit/test_release_script.py -q && shellcheck -S error ../../scripts/release.sh`
Expected: all PASS; shellcheck prints nothing.

- [ ] **Step 5: Commit, then ask for the review**

```bash
git add ../../scripts/release.sh tests/unit/test_release_script.py
git commit -m "feat(release): attach the final DMG as Scout.dmg too, for the website's download button"
```

Send the diff to the "Scout monorepo consolidation" session for review before merging.

---

### Task 14: The website's **Download for Mac** button and the READMEs (in the v0.15.0 release PR)

Do this on the `release/v0.15.0` branch after `release.sh prepare`, so it merges with the release PR and `finalize` follows within minutes.

**Files:**
- Modify: `docs/index.html` (the hero's `.ctas`, the `#install` block)
- Modify: `README.md` (root, the "Install" section)
- Modify: `plugin/README.md` ("2. (Optional) Install the Mac app" and "Updating later")
- Test: `plugin/engine/tests/unit/test_site_download.py`

- [ ] **Step 1: Write the failing test**

```python
"""The website's download button points at the stable DMG name (spec §8)."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
DMG = "https://github.com/Raven-Scout/Scout/releases/latest/download/Scout.dmg"


def test_site_has_the_download_button_and_no_slash_setup():
    html = (REPO / "docs" / "index.html").read_text(encoding="utf-8")
    assert html.count(f'href="{DMG}"') >= 2  # hero + #install
    assert "Download for Mac" in html
    assert "/scout-setup" not in html
    assert 'href="https://github.com/Raven-Scout/Scout/releases/latest"' not in html


def test_readmes_lead_with_the_app():
    for path in (REPO / "README.md", REPO / "plugin" / "README.md"):
        text = path.read_text(encoding="utf-8")
        assert DMG in text, path
        assert "run `/scout-setup`" not in text and "/scout-setup\n" not in text, path
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_site_download.py -q`
Expected: FAIL.

- [ ] **Step 3: Edit `docs/index.html`**

1. In the hero's `.ctas`, replace `<a class="btn btn-p" href="#install">Install Scout</a>` with:

```html
      <a class="btn btn-p" href="https://github.com/Raven-Scout/Scout/releases/latest/download/Scout.dmg">Download for Mac</a>
```

2. Replace the `#install` block's content, from `<p class="lead reveal"><strong>Before you start:</strong>` through the closing `</div>` of its `.ctas` (the two buttons), with:

```html
      <div class="ctas reveal" style="margin-top:8px;">
        <a class="btn btn-p" href="https://github.com/Raven-Scout/Scout/releases/latest/download/Scout.dmg">Download for Mac</a>
      </div>
      <p class="lead reveal" style="font-size:14px;">macOS 13+, needs a Claude account. Open the DMG, drag Scout into Applications, and open it: Scout checks for Claude Code, installs its engine, asks a few questions and runs your first briefing. Turn on the tools you use at <a href="https://claude.ai/settings/connectors">claude.ai/settings/connectors</a> — Slack recommended, since it carries Scout's daily summary and your feedback.</p>
      <p class="lead reveal" style="font-size:14px;margin-top:24px;"><strong>Terminal or Linux?</strong> One command installs the plugin and engine and walks you through setup:</p>
      <div class="term reveal"><div class="h">Terminal</div><pre><code>curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash</code></pre></div>
      <div class="ctas reveal" style="margin-top:24px;">
        <a class="btn btn-s" href="https://github.com/Raven-Scout/Scout">Get Scout on GitHub</a>
      </div>
```

The `<h2>` heading above the old "Before you start" paragraph stays.

- [ ] **Step 4: Edit the READMEs**

Root `README.md`, replace the "Install" section's body (from `One command sets up the plugin and engine:` through the paragraph ending `the CLI without it.`) with:

````markdown
**On a Mac:** [download Scout](https://github.com/Raven-Scout/Scout/releases/latest/download/Scout.dmg), drag it into Applications and open it. Onboarding checks for Claude Code, installs the engine, asks a few questions and runs your first briefing.

**In a terminal (or on Linux):**

```bash
curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash
```

It installs the plugin and engine, then walks you through setup. Scout.app adopts a terminal install if you add it later.
````

`plugin/README.md`, replace `### 2. (Optional) Install the Mac app` and its paragraph with:

```markdown
### Or: the Mac app

[Download Scout](https://github.com/Raven-Scout/Scout/releases/latest/download/Scout.dmg), drag **Scout.app** into Applications, and open it. It's signed and notarized. On a new Mac it does the whole install itself (no terminal); on a Mac where you ran the installer above, it adopts that install. It shows your action items, upcoming runs, costs and schedule on top of `~/Scout/`.
```

and the "Updating later" paragraph with:

```markdown
**Updating later:** Scout.app updates itself, engine included. A terminal install updates by re-running the installer, or with `/scout-update` in Claude Code — both refresh the plugin and upgrade your vault without overwriting your edits (conflicts are left as sidecar files for you to review).
```

- [ ] **Step 5: Run the test, check the page renders, commit**

Run: `.venv/bin/pytest tests/unit/test_site_download.py -q`
Expected: PASS. Open `docs/index.html` in a browser at desktop and 375 px widths: both buttons are visible and the terminal block doesn't scroll the page sideways.

```bash
git add ../../docs/index.html ../../README.md ../README.md tests/unit/test_site_download.py
git commit -m "docs(site): Download for Mac button; READMEs lead with the app"
```

After `finalize v0.15.0`: click the button on `https://raven-scout.github.io/Scout/` and check it downloads v0.15.0's DMG, and `spctl --assess --type open --context context:primary-signature -v ~/Downloads/Scout.dmg` accepts it. That is the spec's C10 addition.

---

### Task 15: `/scout-setup` becomes a stub; every engine string re-pointed (after the app plan merges)

**Files:**
- Modify: `plugin/commands/scout-setup.md` (rewrite)
- Modify: `plugin/engine/scout/paths.py` (add `SETUP_HINT`)
- Modify: `plugin/engine/scout/cli.py` (lines that say `run /scout-setup`: the `bootstrap upgrade` refusal and the two `no vault at … — run /scout-setup` echoes; the `probe-registry` help text; the `_emit` docstring; the `# Per-connector inputs collected by /scout-setup` comment)
- Modify: `plugin/engine/scout/scripts/bootstrap.py` (the two `FileNotFoundError` messages, the module docstring, the `auto_update` comment)
- Modify: `plugin/engine/scout/scripts/auto_update_config.py` (the `no vault config` message)
- Modify: comments and docstrings in `config.py`, `paths.py`, `heartbeat.py`, `connector_detect.py`, `connector_probes.py`; `plugin/templates/connector-probes.yaml` line 1; `plugin/templates/scout-config.yaml.tmpl` line 62; `plugin/templates/knowledge-base/knowledge-base.md.tmpl` line 22
- Modify: `plugin/commands/scout-status.md` (three `/scout-setup` lines)
- Modify: `plugin/README.md` (the remaining `/scout-setup` mentions), `plugin/engine/README.md` line 50
- Modify tests that assert the old text: `test_cli_surface.py` (two `run /scout-setup` asserts), `test_cli_bootstrap_subapp.py:355`, `test_bootstrap_migrate_legacy.py:168`, `test_auto_update_config.py:304` and `:432`
- Modify: `plugin/CHANGELOG.md`
- Test: `plugin/engine/tests/unit/test_scout_setup_retired.py`

**Interfaces:**
- Produces: `scout.paths.SETUP_HINT = "open Scout.app or run `scoutctl setup`"`.

- [ ] **Step 1: Write the failing tests**

```python
"""/scout-setup is retired to a stub; nothing else in plugin/ sends anyone to it (spec §7.1, §4.6)."""

from __future__ import annotations

import subprocess
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[3]
STUB = PLUGIN / "commands" / "scout-setup.md"


def test_the_stub_runs_no_install():
    text = STUB.read_text(encoding="utf-8")
    assert text.startswith("---\nname: scout-setup\ndescription: Retired")
    for forbidden in ("bootstrap install", "bootstrap auto", "install-venv.sh"):
        assert forbidden not in text, forbidden
    for route in ("Scout.app", "scoutctl setup", "/scout-update", "install.sh", "raven-scout.github.io/Scout"):
        assert route in text, route


def test_nothing_else_in_plugin_sends_people_to_scout_setup():
    out = subprocess.run(
        ["git", "grep", "-nE", "/scout-setup([^.a-z-]|$)", "--", ".", ":!CHANGELOG.md", ":!commands/scout-setup.md", ":!engine/tests"],
        cwd=PLUGIN, capture_output=True, text=True, check=False,
    )
    assert out.stdout == "", out.stdout


def test_setup_hint_is_the_one_message():
    from scout.paths import SETUP_HINT

    assert SETUP_HINT == "open Scout.app or run `scoutctl setup`"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_scout_setup_retired.py -q`
Expected: FAIL on all three.

- [ ] **Step 3: Write the stub**

Replace `plugin/commands/scout-setup.md` entirely with:

````markdown
---
name: scout-setup
description: Retired — points you to Scout.app or `scoutctl setup`. Setup now happens in the Mac app or in a terminal.
---

# /scout-setup has retired

Setup now happens in **Scout.app** (macOS) or with **`scoutctl setup`** in a terminal. This command only tells the user where to go. It runs nothing else: a Claude Code shell has no terminal to ask setup's questions on.

## Step 0: Pre-flight

```bash
bash <<'EOF'
set -e
grep -q '"managed_by": "scout-app"' "$HOME/.local/state/scout/engine.json" 2>/dev/null && echo "APP_MANAGED" && exit 0
test -f "$HOME/Scout/.scout-state/install-incomplete" && echo "INSTALL_INCOMPLETE" && exit 0
test -f "$HOME/Scout/scout-config.yaml" && echo "VAULT_EXISTS" && exit 0
test -d "$HOME/Scout/.scout-state" && echo "VAULT_EXISTS" && exit 0
ls "$HOME/Library/LaunchAgents/com.scout."*.plist 2>/dev/null && echo "ORPHAN_JOBS" && exit 0
echo "FRESH"
EOF
```

Then tell the user, by the last line of output:

- `APP_MANAGED`: "Scout.app manages this install. Open Scout.app to change your setup."
- `VAULT_EXISTS`: "Your vault is already set up at `~/Scout/`. To update, run `/scout-update`."
- `FRESH` or `INSTALL_INCOMPLETE`:
  - on macOS: "Download Scout.app from https://raven-scout.github.io/Scout/ and open it — it does the whole setup. Or, in a terminal, run `scoutctl setup` (or the one-line installer below if `scoutctl` isn't installed)."
  - on Linux: "In a terminal, run the one-line installer below. It is safe to re-run, and it picks up an interrupted install."

  ```
  curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash
  ```
- `ORPHAN_JOBS`: "Found Scout's scheduled jobs but no vault — a half-reset state. Run this to clean up, then set Scout up again:" and show the Manual Reset snippet below.

## Manual Reset

```bash
# macOS
launchctl bootout gui/$UID/com.scout.schedule-tick gui/$UID/com.scout.heartbeat 2>/dev/null
rm -f ~/Library/LaunchAgents/com.scout.*.plist

# Linux
crontab -l | sed '/# >>> scout-managed >>>/,/# <<< scout-managed <<</d' | crontab -

# Both
rm -rf ~/Scout
```
````

`test_runbook_install_incomplete.py` still runs this Step 0 block; it keeps passing because the block is unchanged.

- [ ] **Step 4: Re-point the strings**

1. `plugin/engine/scout/paths.py`, near the top-level constants:

```python
# Where to send someone who has no vault yet. /scout-setup retired in favour of these two.
SETUP_HINT = "open Scout.app or run `scoutctl setup`"
```

2. Every user-facing message that says `run /scout-setup` uses it. For example, `cli.py`'s `msg = f"no vault at {vault} — run /scout-setup"` becomes `msg = f"no vault at {vault} — {_paths.SETUP_HINT}"` (import `from scout import paths as _paths` where the function doesn't already). Do the same for the two `typer.echo(f"no vault at {vault} — run /scout-setup", err=True)` lines, both `FileNotFoundError` messages in `bootstrap.py` (`f"no vault at {cfg.vault} — {SETUP_HINT}."` and `f"no vault at {cfg.vault} (no .scout-state/ directory) — {SETUP_HINT} for a fresh install."`), and `auto_update_config.py` (`f"no vault config at {config_path} — {SETUP_HINT} first"`).
3. Comments and docstrings: replace `/scout-setup` with `setup (Scout.app or \`scoutctl setup\`)` or simply `setup`, whichever reads naturally in the sentence. `connector-probes.yaml` line 1 → `# Declarative probe registry for connector detection (scoutctl connectors detect).` The template line 62 → `# Whether Scout keeps itself up to date (no longer asked during setup); toggle via`. The KB template row → `Knowledge base created via Scout setup`.
4. `scout-status.md`: `Run \`/scout-setup\` to create one.` → `Open Scout.app, or run \`scoutctl setup\` in a terminal, to create one.`; `Run \`/scout-setup\` and choose "Reconfigure" to set up scheduling.` → `Run \`scoutctl bootstrap upgrade\` (or open Scout.app ▸ Settings ▸ Engine ▸ Repair) to reinstall the scheduled jobs.`; `Run /scout-setup to set up the ontology.` → `Run \`/scout-update\` to set up the ontology.`
5. `plugin/README.md`: line 317's tree entry → `scout-setup.md          -- Retired: points to Scout.app or scoutctl setup`; line 345 → `connector-probes.yaml   -- How setup detects each connector`; line 524's Manual Reset pointer stays (the stub keeps that section). `plugin/engine/README.md` line 50 → `- **Slash commands (\`/scout-update\`, \`/scout-connect\`)**: use`.
6. Update the tests that assert the old text: each `"run /scout-setup"` assertion becomes `"scoutctl setup"`; `match="run /scout-setup"` becomes `match="scoutctl setup"`; `match="/scout-setup"` becomes `match="scoutctl setup"`.

- [ ] **Step 5: Changelog**

Under `## [Unreleased]` → `### Changed`:

```markdown
- **`/scout-setup` has retired** (`commands/scout-setup.md`) — it now only tells you where setup happens: Scout.app on a Mac, `scoutctl setup` (or the one-line installer) in a terminal, or `/scout-update` when a vault already exists. Every engine message that said "run /scout-setup" now says to open Scout.app or run `scoutctl setup`. The stub is removed in the next minor release. (#326)
```

- [ ] **Step 6: Run the whole suite, lint, commit**

Run: `.venv/bin/pytest -q`
Expected: all PASS.

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add -A ..
git status --short   # check that only plugin/ files you meant to change are staged
git commit -m "feat(setup): retire /scout-setup to a stub; every message points to Scout.app or scoutctl setup"
```

---

### Task 16: Delete the stub (the minor after Task 15 ships)

**Files:**
- Delete: `plugin/commands/scout-setup.md`
- Modify: `plugin/engine/tests/unit/test_runbook_install_incomplete.py` (delete the two `scout-setup.md` tests and the module docstring's `/scout-setup and` wording)
- Modify: `plugin/engine/tests/unit/test_scout_setup_retired.py` (delete `test_the_stub_runs_no_install`; drop the `:!commands/scout-setup.md` exclusion)
- Modify: `plugin/README.md` (the tree entry and the Manual Reset pointer, which moves into the README itself)
- Modify: `plugin/CHANGELOG.md`

- [ ] **Step 1: Move the Manual Reset snippet into the README**

In `plugin/README.md`, replace the sentence that begins `See *Manual Reset* at the bottom of [\`commands/scout-setup.md\`]` with a `### Manual reset` subsection holding the same snippet as the stub's (macOS / Linux / Both blocks). Remove the tree line for `scout-setup.md`.

- [ ] **Step 2: Delete the stub and its tests**

```bash
git rm ../commands/scout-setup.md
```

Delete `test_setup_routes_an_interrupted_install_to_resume` and `test_setup_still_refuses_a_finished_vault` from `test_runbook_install_incomplete.py`, and `test_the_stub_runs_no_install` from `test_scout_setup_retired.py`. In the latter's `git grep`, remove `":!commands/scout-setup.md"`. (The pattern skips `commands/scout-setup.md` file paths, which the README's tree may still mention.)

- [ ] **Step 3: Changelog, test, commit**

Under `### Removed`: `- **\`/scout-setup\`** — retired in the previous release; open Scout.app or run \`scoutctl setup\`. (#326)`

Run: `.venv/bin/pytest -q && (cd ../.. && claude plugin validate plugin)`
Expected: all PASS.

```bash
git add -A .. && git commit -m "chore(setup): remove the retired /scout-setup stub"
```

---

## Spike results

_Filled in by Task 1._

| Check | Result | Cost | Notes |
|---|---|---|---|
| ToolSearch lists a claude.ai connector's tools under `dontAsk` + `--allowedTools ToolSearch` | | | |
| A server tool call is denied, not prompted | | | |
| The probe call succeeds with the probe tool allowed | | | |

Claude Code version: … · Date: …
