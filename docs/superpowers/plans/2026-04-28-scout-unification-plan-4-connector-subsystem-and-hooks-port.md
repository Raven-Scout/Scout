# Scout Engine Plan 4: connector subsystem migration + hooks port + new connectors

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the **connector subsystem** from `~/Scout/{hooks,scripts}/*.sh` to `engine/scout/{hooks,scripts}/*.py`. Lift the connector roster (currently a hardcoded Python dict at `~/Scout/scripts/connector-health-report.sh:43`) to a single `connectors.yaml` source of truth in the plugin, consumed by both Python (engine) and Swift (`scout-app`'s `ConnectorHealthService`). Add three new official-tier connectors as YAML entries: **WhatsApp inbound** (via `verygoodplugins/whatsapp-mcp` — operator-configured MCP server), **Telegram outbound** (Bot API curl wrapped behind `scoutctl notify telegram`), and **Google Messages** (the existing `mcp:claude-in-chrome` key promoted to required-in-briefing modes via the new YAML). Flip `session_tokens_v1` and `connector_health_v1` manifest flags to `True`. Ships when CI is green and observable I/O parity holds against the bash originals via `bats` parity tests.

**Architecture:** Three module groups, each tested in isolation, each with bash-vs-Python parity assertions for the migration period.

1. **`scout.connectors` (NEW)** — pure module that loads `connectors.yaml`, validates it against a small schema, exposes typed lookups (`registered()`, `critical_modes_for(name)`, `remediation_for(name)`). Single source of truth for the connector roster across hooks, scripts, and `scout-app`.
2. **`scout.hooks.{connector_log,session_tokens,kb_pre_filter}` (NEW)** — Python ports of the three Stop/PostToolUse/UserPromptSubmit hooks. Each emits an `Event` per spec §13.2 and writes a JSONL row via `emit()` (which in v0.5+ also appends to the SQLite event store).
3. **`scout.scripts.connector_health_report` (NEW)** — Python port of `connector-health-report.sh`, consumes `scout.connectors`, retains identical alert rules and `connector-health.md` Obsidian rendering. Mode-aware critical-connector rule + chronic-skip rule + warning rule preserved from the bash original verbatim — same JSONL inputs in, same `connector-health.md` and `connector-alerts.log` and macOS notifications out.

`scoutctl` gains two new sub-apps:
- `scoutctl notify telegram --tier <info|action_required> --body "<text>"` — Telegram Bot API outbound, reads `~/.scout-secrets/telegram-bot-token` + `telegram-chat-id`. Tier controls `disable_notification` (`info` → silent, `action_required` → loud). Returns `Event(kind="notification.sent", source="cli:notify_telegram", payload={"tier", "channel", "body_chars"})`.
- `scoutctl connectors {list,show,reload}` — operational queries against the YAML roster (no mutation in v0.4; mutation lands in v0.8 with the discover/enable flow per the v0.5+ spec's "Connector taxonomy and discovery" section).

The runners (`run-scout.sh` etc.) are **untouched** in Plan 4 — they invoke the new Python via `scoutctl` shells (`scoutctl hook <name>` for hook entry; `scoutctl notify telegram` from inside the Claude session via the Bash tool). Plan 5+ will Pythonize the runner itself.

**Tech Stack:** Python 3.11+, Typer, PyYAML (already a runtime dep), python-ulid (already), `requests` for Telegram Bot API outbound (NEW runtime dep — Plan 4 adds it). Pytest + ruff + mypy. Bash + `bats` for parity tests. Swift for one `scout-app` change.

**Position in plan sequence:** Plan 4. Plan 3 (`scoutctl action-items watch`, PR #9) MERGED `2026-04-28` (commit `d0cb43b`). This plan picks up immediately. **Plan 4-supplement** (out of scope of this plan, written separately) will port the remaining 7 runner-side scripts: `budget-check`, `heartbeat`, `rate-limit-detect`, `collect-events`, `pre-session-data`, `cc-session-cache`, `write-session-cost`. Plan 5 = KB ontology + `kb_summary.json` cache (per v0.4 spec §11 Phase D). Plan 6 = `scout-app` refactor (`ScoutEnvironment`, `EngineClient`, capability checker, first-run wizard). Plan 7 = personal-data scrub + delete originals + launchd setup.

---

## Context for the implementer

**Working directory:** `/Users/jordanburger/scout-plugin/`. Fresh branch off the merged Plan 3 tip:

```bash
cd ~/scout-plugin
git checkout main
git pull --ff-only
git checkout -b plan-4-connector-subsystem-and-hooks-port
.venv/bin/pytest tests/ -q              # green expected (171 unit + 1 slow integration)
```

**Reference docs (READ BEFORE STARTING):**
- `~/scout-app/docs/superpowers/specs/2026-04-24-scout-unification-design.md` §4 (file migration map), §6 (KB schema with multi-ID person frontmatter), §11 (personal-data scrub + plugin/vault content boundary).
- `~/scout-app/docs/superpowers/specs/2026-04-25-scout-event-architecture-design.md` — particularly the new "Connector taxonomy and discovery" and "Working with the user as collaborator" sections (capture the long-term direction Plan 4 must remain consistent with).
- `~/Scout/scripts/connector-health-report.sh` — the bash original being ported. Lines 43–73 are the connector dict + REQUIRED_IN map; lines 240–328 are the alert logic. Preserve all rules exactly.
- `~/Scout/hooks/connector-log.sh` — the bash original being ported. Lines 65–76 are the `classify(name, tinput)` function.
- `~/scout-app/docs/superpowers/plans/2026-04-26-scout-unification-plan-2-supplement-stable-ids-and-events.md` — defines `Event`, `emit()` shape, `scout.events.now_iso()`. Reuse, don't redefine.
- `~/scout-app/docs/superpowers/plans/2026-04-22-usage-and-connector-health.md` Tasks 1–3 — the bash side of `sum-session-tokens.sh`. The Python port mirrors the JSONL schema exactly so existing scout-app `SessionTokenEntry` decoders keep working.
- `~/scout-app/docs/superpowers/FOLLOWUPS.md` — open items in `scout.action_items.diff` and `scout.action_items.watch.py` are NOT Plan 4's concern; do not address them here.

**What this plan does NOT touch:**
- The 7 runner-side scripts (`budget-check`, `heartbeat`, `rate-limit-detect`, `collect-events`, `pre-session-data`, `cc-session-cache`, `write-session-cost`). Reserved for Plan 4-supplement.
- `~/Scout/run-scout.sh`, `run-dreaming.sh`, `run-research.sh` — runner ports are Plan 7.
- `SKILL.md`, `DREAMING.md`, `RESEARCH.md` — phase-MD-decomposition + skill renderer is Plan 5.
- `scout-app`'s `ScoutEnvironment` / `EngineClient` refactor — Plan 6.
- The KB ontology pre-computed `kb_summary.json` cache — Plan 5.
- Bidirectional connector workers (Linear webhooks, Slack inbound, Telegram return-bridge) — v0.5+/v0.7 territory per the event-architecture spec roadmap.
- Real WhatsApp MCP install (`verygoodplugins/whatsapp-mcp` setup is operator-side, documented in Task 7 but not executed).

**Bash originals retained until parity confirmed.** Each ported script's bash counterpart stays in `~/Scout/{hooks,scripts}/` until the Plan 4 PR's parity tests pass on CI. Final task deletes the bash originals with a single commit.

## File structure

```
scout-plugin/
├── engine/
│   ├── pyproject.toml                          MODIFIED — Task 1 (PyYAML already present; add `requests>=2.31`)
│   └── scout/
│       ├── connectors.yaml                     NEW — Task 1
│       ├── connectors.py                       NEW — Task 1 (loader + schema validation + lookups)
│       ├── manifest.py                         MODIFIED — Task 12 (flips session_tokens_v1, connector_health_v1)
│       ├── cli.py                              MODIFIED — Tasks 5, 6 (registers `scoutctl hook`, `scoutctl notify`, `scoutctl connectors` sub-apps)
│       ├── hooks/
│       │   ├── __init__.py                     NEW — Task 2
│       │   ├── connector_log.py                NEW — Task 2 (port of connector-log.sh)
│       │   ├── session_tokens.py               NEW — Task 3 (port of sum-session-tokens.sh)
│       │   └── kb_pre_filter.py                NEW — Task 4 (port of kb-pre-filter.sh)
│       └── scripts/
│           ├── __init__.py                     NEW — Task 5
│           ├── connector_health_report.py      NEW — Task 5 (port of connector-health-report.sh)
│           └── notify_telegram.py              NEW — Task 6 (Telegram Bot API outbound)
├── plugin.json                                 MODIFIED — Task 11 (hook registrations point at scoutctl)
├── tests/
│   ├── unit/
│   │   ├── test_connectors_yaml.py             NEW — Task 1
│   │   ├── test_hooks_connector_log.py         NEW — Task 2
│   │   ├── test_hooks_session_tokens.py        NEW — Task 3
│   │   ├── test_hooks_kb_pre_filter.py         NEW — Task 4
│   │   ├── test_scripts_connector_health.py    NEW — Task 5
│   │   ├── test_scripts_notify_telegram.py     NEW — Task 6
│   │   └── test_manifest.py                    MODIFIED — Task 12 (asserts the flipped flags)
│   ├── integration/
│   │   └── test_hook_end_to_end.py             NEW — Task 11 (pipes a real PostToolUse/Stop payload to scoutctl)
│   ├── parity/                                 NEW
│   │   ├── conftest.py                         NEW
│   │   ├── test_connector_log_parity.bats      NEW — Task 2
│   │   ├── test_session_tokens_parity.bats     NEW — Task 3
│   │   └── test_connector_health_parity.bats   NEW — Task 5
│   └── fixtures/
│       ├── connector-log-payload-bash.json     NEW — Task 2 (canned PostToolUse stdin)
│       ├── connector-log-payload-mcp.json      NEW — Task 2
│       ├── stop-payload.json                   NEW — Task 3
│       └── connector-calls-2026-04-22-fixed.jsonl  NEW — Task 5 (frozen 14-day window for deterministic alerting)

scout-app/
├── Scout/Services/
│   └── ConnectorHealthService.swift            MODIFIED — Task 8 (defaultConnectors derived from a committed JSON snapshot of connectors.yaml; CI contract test asserts equality)
└── ScoutTests/
    ├── Fixtures/
    │   └── connectors.snapshot.json            NEW — Task 8 (committed snapshot of connectors.yaml's official-tier roster, regenerated by a `scoutctl connectors snapshot` command)
    └── Services/
        └── ConnectorHealthServiceTests.swift   MODIFIED — Task 8 (asserts defaultConnectors matches the snapshot)
```

---

## Task 1: `connectors.yaml` + `scout.connectors` module

**Files:**
- Create: `engine/scout/connectors.yaml`
- Create: `engine/scout/connectors.py`
- Create: `engine/tests/unit/test_connectors_yaml.py`
- Modify: `engine/pyproject.toml` (add `requests>=2.31`)

**What this builds:** The single source of truth for the connector roster. The bash original at `~/Scout/scripts/connector-health-report.sh:43–73` defines two parallel dicts (`CRITICAL`, `REQUIRED_IN`) plus a per-connector `REMEDIATION` dict (lines 80–133). Plan 4 lifts all three into one declarative YAML and a typed loader.

The YAML is data, not config — it ships in the plugin and is identical across users. User-specific overlays (per the v0.5+ spec's "Connector taxonomy and discovery" §) are a v0.8 concern. v0.4 v0.5 readers see the union of `connectors.yaml` (plugin defaults) and an optional `<vault>/.scout-state/connectors.local.yaml` (overlay; never written by Plan 4, but the loader respects it if present so v0.8 only needs to add the writer).

- [ ] **Step 1: Define the YAML schema and write the seed file**

Create `engine/scout/connectors.yaml`:

```yaml
# scout connector roster — single source of truth for hooks, scripts, and scout-app.
#
# Schema (v1):
#   schema_version: integer
#   connectors:
#     <key>:
#       display_name: string
#       tier: official | auto_discovered | community
#       capabilities: [inbound, outbound, ...]   # see §"Capabilities" below
#       required_in: all | [mode, mode, ...]     # which scheduled-run modes this connector is critical for
#       remediation:
#         first_fix: string  (≤180 chars; goes into Slack/Telegram DMs)
#         detail:    string  (multi-line allowed; goes into connector-health.md)
#       notes: string (optional)
#
# `key` is the value emitted by hooks/connector_log.py's classify() — i.e.,
# what the JSONL `connector` field will read as. Hooks compute the key from
# the tool name; scripts and scout-app look the key up here.
#
# Capabilities vocabulary:
#   inbound    — the connector pulls signals INTO Scout (Slack messages, Linear updates, etc.)
#   outbound   — the connector pushes signals OUT of Scout (Slack DM, Telegram bot DM)
#   meta       — the connector reports about Scout itself (e.g. github via gh CLI for PR queue)
#
# Modes vocabulary (must match the runner's $SCOUT_MODE values):
#   morning-briefing, weekend-briefing
#   consolidation-11am, consolidation-1pm, consolidation-5pm, consolidation-7pm
#   manual, weekend-manual

schema_version: 1

connectors:
  mcp:claude_ai_Slack:
    display_name: Slack
    tier: official
    capabilities: [inbound, outbound]
    required_in: all
    remediation:
      first_fix: "Reconnect Slack at https://claude.ai/settings/connectors — claude.ai Slack OAuth likely expired."
      detail: |
        Slack is a claude.ai MCP connector for scheduled scout runs (the local
        Claude Code plugin path is only used in interactive ~/Scout sessions).
        Reconnect at https://claude.ai/settings/connectors → click Reconnect on
        the Slack row. If the connector is missing entirely, add it back via the
        Add connector flow.

  mcp:claude_ai_Linear:
    display_name: Linear
    tier: official
    capabilities: [inbound]
    required_in: all
    remediation:
      first_fix: "Reconnect Linear at https://claude.ai/settings/connectors — claude.ai Linear OAuth likely expired."
      detail: |
        Linear is a claude.ai MCP connector for scheduled scout runs. Reconnect at
        https://claude.ai/settings/connectors → click Reconnect on the Linear row.
        If a Linear workspace switch happened, the OAuth scope may need re-grant.

  mcp:claude_ai_Gmail:
    display_name: Gmail
    tier: official
    capabilities: [inbound]
    required_in: all
    remediation:
      first_fix: "Reconnect Gmail at https://claude.ai/settings/connectors — Google OAuth likely needs refresh."
      detail: |
        Gmail is a claude.ai MCP connector. Go to https://claude.ai/settings/connectors
        and click Reconnect on the Gmail row. Common triggers: Google account password
        changed, 2FA reset, or the token went idle >7 days. After reconnecting, the next
        scheduled scout run should regain access.

  mcp:claude_ai_Google_Calendar:
    display_name: Google Calendar
    tier: official
    capabilities: [inbound]
    required_in: all
    remediation:
      first_fix: "Reconnect Google Calendar at https://claude.ai/settings/connectors (same Google auth as Gmail)."
      detail: |
        Google Calendar uses the same claude.ai connector pattern as Gmail — usually
        if one Google connector breaks, they all do. Reconnect at
        https://claude.ai/settings/connectors.

  mcp:claude_ai_Granola:
    display_name: Granola
    tier: official
    capabilities: [inbound]
    required_in:
      # Skip on weekends — meeting transcripts are work-week-only.
      - morning-briefing
      - consolidation-11am
      - consolidation-1pm
      - consolidation-5pm
      - consolidation-7pm
      - manual
    remediation:
      first_fix: "Reconnect Granola at https://claude.ai/settings/connectors — Granola tokens expire periodically."
      detail: |
        Granola is a claude.ai MCP connector with tokens that expire more often than
        Google's. Reconnect at https://claude.ai/settings/connectors.

  mcp:claude_ai_Google_Drive:
    display_name: Google Drive
    tier: official
    capabilities: [inbound]
    required_in:
      # Same weekday-only scope as Granola — Phase 1e weekday-mandatory.
      - morning-briefing
      - consolidation-11am
      - consolidation-1pm
      - consolidation-5pm
      - consolidation-7pm
      - manual
    remediation:
      first_fix: "Reconnect Google Drive at https://claude.ai/settings/connectors."
      detail: "Google Drive connector — same reconnect flow as Gmail at https://claude.ai/settings/connectors."

  github:
    display_name: GitHub (gh CLI)
    tier: official
    capabilities: [inbound, meta]
    required_in: all
    remediation:
      first_fix: "Run `gh auth status`; if expired, `gh auth login` to re-auth. Token lives in macOS keychain."
      detail: |
        GitHub access is via the local `gh` CLI (not an MCP). Token expiry is the usual
        cause. `gh auth status` shows current state; `gh auth login` refreshes it. After a
        Keboola SAML re-auth, the personal token may need a separate refresh.

  mcp:claude-in-chrome:
    display_name: Chrome (Google Messages)
    tier: official
    capabilities: [inbound]
    required_in:
      # Required only when the briefing scans personal-context surfaces.
      - morning-briefing
      - weekend-briefing
      - consolidation-11am
      - consolidation-1pm
      - consolidation-5pm
      - consolidation-7pm
      - manual
    remediation:
      first_fix: "Make sure Chrome is running with the Claude-in-Chrome extension enabled at chrome://extensions. If the Mac was asleep, waking it is enough."
      detail: |
        Chrome MCP requires (a) Chrome.app actually running, (b) the Claude-in-Chrome
        extension loaded and enabled at chrome://extensions. Most false positives come
        from overnight sleep or a Chrome crash. Quick check: open messages.google.com/web/
        in Chrome manually — if that loads, the extension should reconnect on next
        scout run. If not, reload the extension (toggle off/on at chrome://extensions).
    notes: |
      Promoted from OPTIONAL to CRITICAL on 2026-04-25. Google Messages is a
      first-class personal-task source (vet appointments, family commitments)
      that no work connector replaces.

  mcp:whatsapp-mcp:
    display_name: WhatsApp
    tier: official
    capabilities: [inbound]
    required_in:
      # Personal-context scans are briefing-time only — consolidation runs
      # are work-focused. Adjust per user preference via the v0.8 overlay.
      - morning-briefing
      - weekend-briefing
    remediation:
      first_fix: "Check the WhatsApp MCP bridge: `launchctl list | grep com.scout.whatsapp-bridge`. Restart with `launchctl kickstart -k gui/$UID/com.scout.whatsapp-bridge` if the bridge is down."
      detail: |
        WhatsApp inbound runs via the verygoodplugins/whatsapp-mcp server — a local
        Go bridge (whatsmeow library, persistent ~20-day session after one-time QR pair)
        plus a Python MCP server that the scheduled session connects to via stdio.
        Bridge runs as a launchctl service (com.scout.whatsapp-bridge.plist). If the
        bridge is down, the MCP startup fails and the connector goes dark.

        Common failure modes:
          1. Session expired (~20 days) — re-pair QR via interactive bridge run.
          2. Bridge crashed — `launchctl kickstart` to restart.
          3. macOS Network Extension permissions reset — re-grant in System Settings.

        Setup docs: ~/scout-plugin/docs/connectors/whatsapp-setup.md

  notify:telegram:
    display_name: Telegram (outbound)
    tier: official
    capabilities: [outbound]
    required_in: []   # never required — outbound notifications never make a run "fail"
    remediation:
      first_fix: "Check `~/.scout-secrets/telegram-bot-token` exists (mode 600); test with `scoutctl notify telegram --tier info --body 'health check'`."
      detail: |
        Telegram outbound is a Bot API curl wrapped behind `scoutctl notify telegram`.
        Token + numeric chat ID stored in ~/.scout-secrets/ (gitignored, mode 600).

        Setup walkthrough: ~/scout-plugin/docs/connectors/telegram-setup.md
        (one-time @BotFather flow + chat-ID capture).

        Bidirectional Telegram (the inbound return-bridge for replies as feedback
        signals) is v0.7 territory per the v0.5+ event-architecture spec.
```

The `notes` field on Chrome captures the existing context block from `connector-health-report.sh:40-44`. Same for the WhatsApp/Telegram notes — they're operator-facing context, not part of the alerting logic.

- [ ] **Step 2: Add `requests` to runtime dependencies**

Edit `engine/pyproject.toml`:

```toml
dependencies = [
    "typer>=0.12",
    "pyyaml>=6.0",
    "jinja2>=3.1",
    "python-ulid>=2.2",
    "rich>=13.7",
    "watchdog>=4.0",
    "requests>=2.31",       # NEW — Telegram Bot API outbound (Task 6)
]
```

Sync the venv:

```bash
cd ~/scout-plugin/engine
uv pip install -e ".[dev]" --python .venv/bin/python
```

- [ ] **Step 3: Write failing tests for `scout.connectors`**

Create `engine/tests/unit/test_connectors_yaml.py`:

```python
"""Unit tests for scout.connectors — YAML loader + typed lookups."""

from __future__ import annotations

import pytest

from scout.connectors import (
    Capability,
    Connector,
    ConnectorRegistry,
    Tier,
    load_registry,
)


def test_load_registry_returns_official_tier_seed():
    reg = load_registry()
    keys = set(reg.keys())
    # The 9 official-tier connectors that ship in the seed YAML.
    assert keys >= {
        "mcp:claude_ai_Slack",
        "mcp:claude_ai_Linear",
        "mcp:claude_ai_Gmail",
        "mcp:claude_ai_Google_Calendar",
        "mcp:claude_ai_Granola",
        "mcp:claude_ai_Google_Drive",
        "github",
        "mcp:claude-in-chrome",
        "mcp:whatsapp-mcp",
        "notify:telegram",
    }


def test_connector_fields_typed():
    reg = load_registry()
    slack = reg["mcp:claude_ai_Slack"]
    assert isinstance(slack, Connector)
    assert slack.display_name == "Slack"
    assert slack.tier == Tier.OFFICIAL
    assert Capability.INBOUND in slack.capabilities
    assert Capability.OUTBOUND in slack.capabilities


def test_required_in_all_means_every_mode_is_required():
    reg = load_registry()
    slack = reg["mcp:claude_ai_Slack"]
    assert slack.required_in_mode("morning-briefing")
    assert slack.required_in_mode("weekend-briefing")
    assert slack.required_in_mode("consolidation-11am")
    assert slack.required_in_mode("manual")


def test_required_in_specific_modes():
    reg = load_registry()
    granola = reg["mcp:claude_ai_Granola"]
    assert granola.required_in_mode("morning-briefing")
    assert not granola.required_in_mode("weekend-briefing")
    assert not granola.required_in_mode("weekend-manual")


def test_required_in_empty_means_never_critical():
    reg = load_registry()
    tg = reg["notify:telegram"]
    assert not tg.required_in_mode("morning-briefing")
    assert not tg.required_in_mode("manual")


def test_remediation_fields_under_180_chars():
    """The first_fix string goes into Slack/Telegram DMs and gets truncated; pin the cap."""
    reg = load_registry()
    for key, c in reg.items():
        assert len(c.remediation.first_fix) <= 180, (
            f"{key}.remediation.first_fix too long ({len(c.remediation.first_fix)} chars)"
        )


def test_critical_connectors_filter():
    reg = load_registry()
    critical = reg.critical_in_mode("morning-briefing")
    assert "mcp:claude_ai_Slack" in critical
    assert "mcp:claude_ai_Granola" in critical
    assert "notify:telegram" not in critical  # outbound, never critical


def test_unknown_connector_raises():
    reg = load_registry()
    with pytest.raises(KeyError):
        reg["mcp:nonexistent"]


def test_overlay_path_layered_on_seed(tmp_path, monkeypatch):
    """If <data_dir>/.scout-state/connectors.local.yaml exists, it overlays the seed.

    v0.4 doesn't write to this file but the loader respects it so v0.8 can land
    the writer without touching the loader.
    """
    overlay = tmp_path / ".scout-state" / "connectors.local.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        """
schema_version: 1
connectors:
  mcp:custom-thing:
    display_name: Custom
    tier: community
    capabilities: [inbound]
    required_in: []
    remediation:
      first_fix: "Manual restart."
      detail: "User-authored."
"""
    )
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    reg = load_registry()
    assert "mcp:custom-thing" in reg
    assert reg["mcp:custom-thing"].tier == Tier.COMMUNITY


def test_overlay_can_override_seed_remediation(tmp_path, monkeypatch):
    overlay = tmp_path / ".scout-state" / "connectors.local.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        """
schema_version: 1
connectors:
  mcp:claude_ai_Slack:
    remediation:
      first_fix: "User-customized fix instructions."
"""
    )
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    reg = load_registry()
    # Overlay overrides only the field it specifies; other fields inherit from seed.
    assert reg["mcp:claude_ai_Slack"].remediation.first_fix == "User-customized fix instructions."
    assert reg["mcp:claude_ai_Slack"].display_name == "Slack"  # inherited
```

- [ ] **Step 4: Run, confirm RED**

```bash
cd ~/scout-plugin/engine
.venv/bin/pytest tests/unit/test_connectors_yaml.py -v
```

Expected: `ModuleNotFoundError: No module named 'scout.connectors'`.

- [ ] **Step 5: Implement `scout/connectors.py`**

```python
"""Connector roster: typed loader for connectors.yaml + optional vault overlay.

Single source of truth for which connectors Scout tracks, which modes they're
critical in, and how to remediate them when they go dark. Consumed by:
  - scout.hooks.connector_log         (classifies tool calls into connector keys)
  - scout.scripts.connector_health_report  (alerting + connector-health.md rendering)
  - scout-app's ConnectorHealthService  (default roster for the rail card)
  - v0.8 `scoutctl connectors` sub-app  (discover/enable/disable)
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scout import paths
from scout.errors import ConfigError


class Tier(enum.Enum):
    OFFICIAL = "official"
    AUTO_DISCOVERED = "auto_discovered"
    COMMUNITY = "community"


class Capability(enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    META = "meta"


@dataclass(frozen=True)
class Remediation:
    first_fix: str          # ≤ 180 chars — fits in DM truncation budget
    detail: str             # multi-line; rendered in connector-health.md


@dataclass(frozen=True)
class Connector:
    key: str
    display_name: str
    tier: Tier
    capabilities: tuple[Capability, ...]
    required_in: tuple[str, ...] | str   # tuple of mode strings, or "all"
    remediation: Remediation
    notes: str = ""

    def required_in_mode(self, mode: str) -> bool:
        if self.required_in == "all":
            return True
        return mode in self.required_in


class ConnectorRegistry:
    """Indexed view over loaded connectors. Use load_registry() to construct."""

    def __init__(self, connectors: dict[str, Connector]):
        self._connectors = connectors

    def __contains__(self, key: str) -> bool:
        return key in self._connectors

    def __getitem__(self, key: str) -> Connector:
        return self._connectors[key]

    def __iter__(self):
        return iter(self._connectors)

    def items(self):
        return self._connectors.items()

    def keys(self):
        return self._connectors.keys()

    def values(self):
        return self._connectors.values()

    def critical_in_mode(self, mode: str) -> list[str]:
        """Connector keys that are required in `mode` (i.e., outage = alert)."""
        return [
            key for key, c in self._connectors.items() if c.required_in_mode(mode)
        ]


def load_registry(data_dir: Path | None = None) -> ConnectorRegistry:
    """Load seed connectors.yaml from the package; layer optional vault overlay on top.

    Overlay path: `<data_dir>/.scout-state/connectors.local.yaml`. v0.4 ships
    no writer for the overlay; respecting it keeps v0.8's discover/enable
    flow a small additive change.
    """
    seed_path = Path(__file__).parent / "connectors.yaml"
    seed = _load_yaml(seed_path)

    merged = dict(seed.get("connectors", {}))
    overlay_data_dir = data_dir if data_dir is not None else paths.data_dir()
    overlay_path = overlay_data_dir / ".scout-state" / "connectors.local.yaml"
    if overlay_path.exists():
        overlay = _load_yaml(overlay_path)
        for key, override in overlay.get("connectors", {}).items():
            if key in merged:
                merged[key] = _deep_merge_dict(merged[key], override)
            else:
                merged[key] = override

    connectors: dict[str, Connector] = {}
    for key, raw in merged.items():
        connectors[key] = _build_connector(key, raw)
    return ConnectorRegistry(connectors)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"connectors yaml at {path} is malformed: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"connectors yaml at {path} is not a mapping")
    return data


def _deep_merge_dict(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge with one level of nested-dict merging for `remediation`."""
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def _build_connector(key: str, raw: dict[str, Any]) -> Connector:
    try:
        tier = Tier(raw.get("tier", "official"))
        capabilities = tuple(Capability(c) for c in raw.get("capabilities", []))
        required_in_raw = raw.get("required_in", [])
        required_in: tuple[str, ...] | str
        if required_in_raw == "all":
            required_in = "all"
        else:
            required_in = tuple(required_in_raw)
        rem_raw = raw.get("remediation", {})
        remediation = Remediation(
            first_fix=rem_raw.get("first_fix", ""),
            detail=rem_raw.get("detail", ""),
        )
        return Connector(
            key=key,
            display_name=raw["display_name"],
            tier=tier,
            capabilities=capabilities,
            required_in=required_in,
            remediation=remediation,
            notes=raw.get("notes", "") or "",
        )
    except (KeyError, ValueError) as e:
        raise ConfigError(f"connector {key} entry is malformed: {e}") from e
```

- [ ] **Step 6: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_connectors_yaml.py -v
```

Expected: 10 passed.

- [ ] **Step 7: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 8: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/connectors.yaml engine/scout/connectors.py engine/tests/unit/test_connectors_yaml.py engine/pyproject.toml
git commit -m "feat(engine): connectors.yaml + scout.connectors registry — single source of truth"
```

---

## Task 2: Port `connector-log.sh` → `scout.hooks.connector_log`

**Files:**
- Create: `engine/scout/hooks/__init__.py`
- Create: `engine/scout/hooks/connector_log.py`
- Create: `engine/tests/unit/test_hooks_connector_log.py`
- Create: `engine/tests/parity/conftest.py`
- Create: `engine/tests/parity/test_connector_log_parity.bats`
- Create: `engine/tests/fixtures/connector-log-payload-bash.json`
- Create: `engine/tests/fixtures/connector-log-payload-mcp.json`

**What this builds:** Python port of `~/Scout/hooks/connector-log.sh` (the PostToolUse hook that classifies tool calls and appends a JSONL row to `.scout-logs/connector-calls-YYYY-MM-DD.jsonl`). Drops in as `scoutctl hook connector-log` so `plugin.json` can wire it via the `${CLAUDE_PLUGIN_ROOT}/engine/bin/scoutctl` shim. Returns an `Event` per spec §13.2; v0.4 ignores the return value but tests assert on it.

The classifier (`classify(tool_name, tool_input)`) is the central piece — it converts Claude Code's PostToolUse `tool_name` (e.g. `mcp__plugin_slack_slack__slack_send_message`, `Bash`) into a `connector` key (e.g. `mcp:plugin_slack_slack`, `bash:gh` or `bash:ls`). The Python port preserves the exact bash logic verbatim (lines 65–76 of `connector-log.sh`).

- [ ] **Step 1: Create the bash payload fixtures**

Create `engine/tests/fixtures/connector-log-payload-bash.json`:

```json
{
  "session_id": "abc-123-bash",
  "tool_name": "Bash",
  "tool_input": {"command": "gh pr list --state open"},
  "tool_response": {"returncode": 0, "stdout": "...", "stderr": ""}
}
```

Create `engine/tests/fixtures/connector-log-payload-mcp.json`:

```json
{
  "session_id": "abc-123-mcp",
  "tool_name": "mcp__plugin_slack_slack__slack_send_message",
  "tool_input": {"channel": "C123", "text": "hello"},
  "tool_response": {"isError": false, "content": [{"type": "text", "text": "ok"}]}
}
```

Plus a third payload for the error path:

`engine/tests/fixtures/connector-log-payload-error.json`:

```json
{
  "session_id": "abc-123-err",
  "tool_name": "mcp__claude_ai_Gmail__search_threads",
  "tool_input": {"q": "..."},
  "tool_response": {"isError": true, "error": "auth expired"}
}
```

- [ ] **Step 2: Write failing tests**

Create `engine/tests/unit/test_hooks_connector_log.py`:

```python
"""Unit tests for scout.hooks.connector_log."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scout.events import Event
from scout.hooks.connector_log import classify, run


# Mode is required; the hook short-circuits without it (interactive sessions).
def test_no_scout_mode_short_circuits(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    result = run(stdin=io.StringIO(payload))
    assert result is None
    # No JSONL file written.
    log_dir = tmp_path / ".scout-logs"
    assert not list(log_dir.glob("connector-calls-*.jsonl")) if log_dir.exists() else True


def test_classify_bash_uses_first_token(tmp_path):
    assert classify("Bash", {"command": "gh pr list"}) == "github"
    assert classify("Bash", {"command": "ls -la"}) == "bash:ls"
    assert classify("Bash", {"command": "  curl -s url"}) == "bash:curl"
    assert classify("Bash", {"command": ""}) == "bash"


def test_classify_mcp_extracts_server_segment():
    assert classify("mcp__plugin_slack_slack__slack_send_message", {}) == "mcp:plugin_slack_slack"
    assert classify("mcp__claude_ai_Gmail__search_threads", {}) == "mcp:claude_ai_Gmail"
    assert classify("mcp__claude-in-chrome__find", {}) == "mcp:claude-in-chrome"
    assert classify("mcp__whatsapp-mcp__list_messages", {}) == "mcp:whatsapp-mcp"


def test_classify_other_tool_lowercases():
    assert classify("Read", {}) == "read"
    assert classify("WebFetch", {}) == "webfetch"


def test_run_writes_one_jsonl_row(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "morning-briefing")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    payload = (tmp_path / "fixture.json").read_bytes() if False else None  # placeholder
    payload_text = json.dumps({
        "session_id": "abc-123",
        "tool_name": "mcp__plugin_slack_slack__slack_send_message",
        "tool_input": {"channel": "C", "text": "x"},
        "tool_response": {"isError": False},
    })
    event = run(stdin=io.StringIO(payload_text))
    assert isinstance(event, Event)
    assert event.kind == "tool.call.logged"
    assert event.source == "hook:connector-log"
    assert event.payload["connector"] == "mcp:plugin_slack_slack"
    assert event.payload["session_id"] == "abc-123"
    assert event.payload["mode"] == "morning-briefing"
    assert event.payload["error"] is False

    # JSONL row landed.
    et_logs = list((tmp_path / ".scout-logs").glob("connector-calls-*.jsonl"))
    assert len(et_logs) == 1
    rows = [json.loads(line) for line in et_logs[0].read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["connector"] == "mcp:plugin_slack_slack"
    assert rows[0]["mode"] == "morning-briefing"
    assert rows[0]["error"] is False
    # Event ts is UTC ISO-8601 with Z suffix.
    assert rows[0]["ts"].endswith("Z")


def test_run_records_error_with_snippet(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "consolidation-1pm")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    payload_text = json.dumps({
        "session_id": "err-1",
        "tool_name": "mcp__claude_ai_Gmail__search_threads",
        "tool_response": {"isError": True, "error": "auth expired token rotated"},
    })
    event = run(stdin=io.StringIO(payload_text))
    assert event.payload["error"] is True
    assert event.payload["err"] == "auth expired token rotated"


def test_run_handles_malformed_payload_silently(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "manual")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    # Hooks must NEVER break the session — return None on malformed input.
    result = run(stdin=io.StringIO("{not json"))
    assert result is None


def test_classify_truncates_err_snippet_at_160_chars(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "manual")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    long_err = "X" * 500
    payload = json.dumps({
        "session_id": "err-trunc",
        "tool_name": "Bash",
        "tool_input": {"command": "false"},
        "tool_response": {"returncode": 1, "error": long_err},
    })
    event = run(stdin=io.StringIO(payload))
    assert len(event.payload["err"]) == 160
```

- [ ] **Step 3: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_hooks_connector_log.py -v
```

- [ ] **Step 4: Implement `scout/hooks/__init__.py` (empty) and `scout/hooks/connector_log.py`**

```python
"""PostToolUse hook port — appends one JSONL record per tool call.

Direct port of ~/Scout/hooks/connector-log.sh. Behavior identical:
  - Short-circuits when SCOUT_MODE is unset (interactive sessions).
  - Emits one row to .scout-logs/connector-calls-YYYY-MM-DD.jsonl per call.
  - Tag-classifies tool_name → connector key (preserves bash classify() exactly).
  - ET-date-stamps the JSONL filename (TZ=America/New_York).
  - Truncates error snippets at 160 chars (matches bash original).
  - Never raises — hooks must never break a session.

v0.4 returns an Event in addition to writing JSONL; v0.5 will append the
Event to the SQLite event store via the same emit() shape.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import IO, Any

from scout import paths
from scout.events import Event, now_iso
from scout.ids import new_ulid


def classify(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Map a Claude Code tool_name + tool_input to a connector key.

    Preserves the classify() function from connector-log.sh:65-76 verbatim.
    """
    if tool_name == "Bash":
        cmd = (tool_input.get("command") or "").strip()
        first = cmd.split()[0] if cmd else ""
        if first == "gh":
            return "github"
        return f"bash:{first}" if first else "bash"
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        if len(parts) >= 2:
            return f"mcp:{parts[1]}"
    return tool_name.lower()


def run(*, stdin: IO[str] | None = None) -> Event | None:
    """Read one PostToolUse JSON payload from stdin, write one JSONL row, return Event.

    Returns None if SCOUT_MODE is unset (interactive session) or if stdin is malformed.
    """
    mode = os.environ.get("SCOUT_MODE")
    if not mode:
        return None

    src = stdin if stdin is not None else sys.stdin
    try:
        data = json.load(src)
    except Exception:
        return None  # malformed — never raise from a hook

    tool_name = data.get("tool_name", "unknown")
    tool_input = data.get("tool_input") or {}
    tool_response = data.get("tool_response") or {}
    session_id = data.get("session_id", "")

    is_error = False
    err_snippet = ""
    if isinstance(tool_response, dict):
        if tool_response.get("isError") is True:
            is_error = True
        rc = tool_response.get("returncode")
        if isinstance(rc, int) and rc != 0:
            is_error = True
        if tool_response.get("error"):
            is_error = True
            err_snippet = str(tool_response["error"])[:160]
        content = tool_response.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("isError"):
                    is_error = True
                    if not err_snippet:
                        err_snippet = (item.get("text") or "")[:160]

    connector = classify(tool_name, tool_input)
    ts_utc = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    record = {
        "ts": ts_utc,
        "session_id": session_id,
        "mode": mode,
        "tool": tool_name,
        "connector": connector,
        "error": is_error,
    }
    if err_snippet:
        record["err"] = err_snippet

    et_date = _et_date()
    log_dir = paths.data_dir() / ".scout-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path = log_dir / f"connector-calls-{et_date}.jsonl"
    try:
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="tool.call.logged",
        source="hook:connector-log",
        payload=record,
    )


def _et_date() -> str:
    """Eastern-Time date string YYYY-MM-DD. Matches bash original's TZ behavior."""
    result = subprocess.run(
        ["date", "+%Y-%m-%d"],
        env={**os.environ, "TZ": "America/New_York"},
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() or datetime.now().date().isoformat()


def main() -> int:
    """CLI entry point: scoutctl hook connector-log."""
    try:
        run()
    except Exception:
        pass  # never break the session
    return 0
```

- [ ] **Step 5: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_hooks_connector_log.py -v
```

- [ ] **Step 6: Write the bash-vs-Python parity test**

`bats` is the bash test framework. Install once via `brew install bats-core` (CI installs via apt).

Create `engine/tests/parity/conftest.py` (empty marker so pytest skips the dir; bats runs separately):

```python
# Parity tests are bats-driven; pytest skips this directory.
```

Create `engine/tests/parity/test_connector_log_parity.bats`:

```bash
#!/usr/bin/env bats

setup() {
    SCOUT_DATA_DIR_BASH=$(mktemp -d)
    SCOUT_DATA_DIR_PYTHON=$(mktemp -d)
    BASH_HOOK="$HOME/Scout/hooks/connector-log.sh"
    PYTHON_HOOK="$BATS_TEST_DIRNAME/../../.venv/bin/scoutctl"
    SCOUT_MODE="morning-briefing"
    export SCOUT_DATA_DIR_BASH SCOUT_DATA_DIR_PYTHON SCOUT_MODE
    if [ ! -x "$BASH_HOOK" ]; then
        skip "bash hook not present at $BASH_HOOK (already migrated?)"
    fi
}

teardown() {
    rm -rf "$SCOUT_DATA_DIR_BASH" "$SCOUT_DATA_DIR_PYTHON"
}

@test "mcp tool: bash + python emit identical connector classification" {
    payload='{"session_id":"p1","tool_name":"mcp__plugin_slack_slack__slack_send_message","tool_response":{"isError":false}}'

    # Bash side
    SCOUT_DATA_DIR="$SCOUT_DATA_DIR_BASH" \
        echo "$payload" | "$BASH_HOOK"

    # Python side
    SCOUT_DATA_DIR="$SCOUT_DATA_DIR_PYTHON" \
        echo "$payload" | "$PYTHON_HOOK" hook connector-log

    bash_row=$(cat "$SCOUT_DATA_DIR_BASH"/.scout-logs/connector-calls-*.jsonl)
    python_row=$(cat "$SCOUT_DATA_DIR_PYTHON"/.scout-logs/connector-calls-*.jsonl)

    bash_connector=$(echo "$bash_row" | jq -r '.connector')
    python_connector=$(echo "$python_row" | jq -r '.connector')
    [ "$bash_connector" = "$python_connector" ]
    [ "$bash_connector" = "mcp:plugin_slack_slack" ]
}

@test "bash tool with gh command: both emit github" {
    payload='{"session_id":"p2","tool_name":"Bash","tool_input":{"command":"gh pr list"},"tool_response":{"returncode":0}}'

    SCOUT_DATA_DIR="$SCOUT_DATA_DIR_BASH" echo "$payload" | "$BASH_HOOK"
    SCOUT_DATA_DIR="$SCOUT_DATA_DIR_PYTHON" echo "$payload" | "$PYTHON_HOOK" hook connector-log

    bash_connector=$(jq -r '.connector' "$SCOUT_DATA_DIR_BASH"/.scout-logs/connector-calls-*.jsonl)
    python_connector=$(jq -r '.connector' "$SCOUT_DATA_DIR_PYTHON"/.scout-logs/connector-calls-*.jsonl)
    [ "$bash_connector" = "$python_connector" ]
    [ "$bash_connector" = "github" ]
}

@test "error tool_response: both record error=true with truncated snippet" {
    payload='{"session_id":"p3","tool_name":"mcp__claude_ai_Gmail__search","tool_response":{"isError":true,"error":"auth expired"}}'

    SCOUT_DATA_DIR="$SCOUT_DATA_DIR_BASH" echo "$payload" | "$BASH_HOOK"
    SCOUT_DATA_DIR="$SCOUT_DATA_DIR_PYTHON" echo "$payload" | "$PYTHON_HOOK" hook connector-log

    bash_err=$(jq -r '.error' "$SCOUT_DATA_DIR_BASH"/.scout-logs/connector-calls-*.jsonl)
    python_err=$(jq -r '.error' "$SCOUT_DATA_DIR_PYTHON"/.scout-logs/connector-calls-*.jsonl)
    [ "$bash_err" = "$python_err" ]
    [ "$bash_err" = "true" ]
}
```

- [ ] **Step 7: Lint + run parity**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
bats tests/parity/test_connector_log_parity.bats
```

- [ ] **Step 8: Commit**

```bash
git add engine/scout/hooks/__init__.py engine/scout/hooks/connector_log.py \
        engine/tests/unit/test_hooks_connector_log.py engine/tests/parity/ \
        engine/tests/fixtures/connector-log-payload-*.json
git commit -m "feat(engine): port hooks/connector-log.sh → scout.hooks.connector_log"
```

---

## Task 3: Port `sum-session-tokens.sh` → `scout.hooks.session_tokens`

**Files:**
- Create: `engine/scout/hooks/session_tokens.py`
- Create: `engine/tests/unit/test_hooks_session_tokens.py`
- Create: `engine/tests/parity/test_session_tokens_parity.bats`
- Create: `engine/tests/fixtures/stop-payload.json`

**What this builds:** Python port of `~/Scout/scripts/sum-session-tokens.sh`, the Stop hook that sums `message.usage` across the session transcript and appends a row to `.scout-logs/session-tokens.jsonl`. Schema is identical to the existing Swift `SessionTokenEntry` decoder in `scout-app/Scout/Models/SessionTokenEntry.swift` — DO NOT change field names; the Swift decoder must keep working without modification.

Pricing constants stay the same as the bash original (verify against https://www.anthropic.com/pricing before merging the PR — Phase 2 of the original Apr 22 plan replaces dollar display with quota %, so these constants become irrelevant then).

The port turns the per-turn jq accumulation loop into a vectorized Python sum, and reads the entire transcript file once instead of jq-piping it three times.

[Test contract — minimum required cases:]

- Decodes a 3-turn fixture (2 Opus + 1 Sonnet) with known token totals; output JSONL row matches the bash original byte-for-byte except for the `ts`/`ts_et` timestamps.
- `transcript_path` missing → emits zero-row with `error: "transcript_not_found"`.
- All-non-usage turns → emits zero-row with `error: "no_usage_turns"`.
- Mixed-model cost calculated per-turn against that turn's model (not the primary's).
- Returns `Event(kind="session.tokens.summed", source="hook:session-tokens", payload=...)`.

[Implementation outline:]

```python
# engine/scout/hooks/session_tokens.py — abbreviated; full code mirrors connector_log.py shape.

PRICING_USD_PER_M_TOKENS = {
    "claude-opus":   {"input": 15.00, "output": 75.00, "cache_read": 1.50,  "cache_create": 18.75},
    "claude-sonnet": {"input": 3.00,  "output": 15.00, "cache_read": 0.30,  "cache_create": 3.75},
    "claude-haiku":  {"input": 0.80,  "output": 4.00,  "cache_read": 0.08,  "cache_create": 1.00},
}

def _model_family(model: str | None) -> str:
    if not model:
        return "claude-opus"  # conservative default
    for prefix in PRICING_USD_PER_M_TOKENS:
        if model.startswith(prefix):
            return prefix
    return "claude-opus"

def run(*, stdin=None) -> Event | None:
    """Read Stop hook payload, sum transcript usage, write one row to session-tokens.jsonl."""
    # 1. Parse stdin payload, pull transcript_path / session_id / cwd.
    # 2. Open transcript JSONL, iterate turns, filter to those with .message.usage.
    # 3. Sum input_tokens / output_tokens / cache_read / cache_create per turn.
    # 4. Compute per-turn cost against that turn's model, accumulate.
    # 5. Identify primary_model = most-frequent model across turns.
    # 6. Append {ts, ts_et, session_id, scout_mode, cwd, primary_model, ..., cost_usd, num_turns, error} to session-tokens.jsonl.
    # 7. Return Event.
```

[Reference the bash original at `~/Scout/scripts/sum-session-tokens.sh` lines 198–284 for exact schema. Preserve every field; preserve the per-turn cost computation; preserve the error path.]

Steps follow the same TDD pattern as Task 2 (write tests first → RED → implement → GREEN → bats parity → commit).

Commit:

```bash
git commit -m "feat(engine): port scripts/sum-session-tokens.sh → scout.hooks.session_tokens"
```

---

## Task 4: Port `kb-pre-filter.sh` → `scout.hooks.kb_pre_filter`

**Files:**
- Create: `engine/scout/hooks/kb_pre_filter.py`
- Create: `engine/tests/unit/test_hooks_kb_pre_filter.py`

**What this builds:** Python port of `~/Scout/hooks/kb-pre-filter.sh`. Reads SCOUT_MODE, scans the KB directory for entries modified since the last run, and pre-computes a staleness score into `.scout-cache/kb-staleness-pre-filter.md` so the LLM session reads the cache instead of re-scanning the filesystem.

Steps follow the same TDD pattern. Commit:

```bash
git commit -m "feat(engine): port hooks/kb-pre-filter.sh → scout.hooks.kb_pre_filter"
```

---

## Task 5: Port `connector-health-report.sh` → `scout.scripts.connector_health_report` + `scoutctl connectors` sub-app

**Files:**
- Create: `engine/scout/scripts/__init__.py`
- Create: `engine/scout/scripts/connector_health_report.py`
- Create: `engine/tests/unit/test_scripts_connector_health.py`
- Create: `engine/tests/fixtures/connector-calls-2026-04-22-fixed.jsonl` (frozen 14-day window for deterministic alerting tests)
- Create: `engine/tests/parity/test_connector_health_parity.bats`
- Modify: `engine/scout/cli.py` (registers `scoutctl connectors {list,show,reload}` sub-app)

**What this builds:** Python port of the most-load-bearing script. It rolls up `.scout-logs/connector-calls-*.jsonl` into `knowledge-base/connector-health.md`, fires alerts (mode-aware critical-connector rule + chronic-skip rule + warning rule), and writes the alert pending-block to `.scout-cache/connector-alerts-pending.md` for the next run's Slack DM.

**The connector roster is now read from `scout.connectors.load_registry()`** (Task 1) rather than hardcoded. The alerting logic is preserved verbatim from the bash original — same rules, same edge cases (zero-prior-runs no-baseline rule; chronic-skip Apr 25 fix; pattern #48 never-wired suppression). The macOS notification + connector-alerts.log append + `connector-health.md` rendering all stay identical so existing scout-app `ConnectorAlertBanner`, `ConnectorHealthRailCard`, and `ConnectorAckStore` keep working without modification.

[Test contract — minimum required cases:]

- Empty `.scout-logs/` → silent exit, no `connector-health.md` written.
- Single healthy run → `connector-health.md` rendered with no alerts; matrix shows `✅ N` cells.
- Granola 7 runs dark + last_healthy=never → **suppressed** per Pattern #48 (the "never wired" rule from `connector-health-report.sh:286–296`).
- Slack 1 run dark with same-mode baseline of 2/2 healthy prior → CRITICAL alert fires.
- Weekend-only `gh CLI dark` → no alert (chronic-skip rule on non-required mode).
- Warning rule: 4 calls, 3 errors → WARNING alert fires.
- Mode-aware-baseline edge case: 0 prior same-mode runs → **no alert** (no baseline).

[Implementation outline:]

```python
# engine/scout/scripts/connector_health_report.py — abbreviated.
# Full implementation mirrors ~/Scout/scripts/connector-health-report.sh:31-492 line-by-line,
# substituting `scout.connectors.load_registry()` for the hardcoded CRITICAL/REQUIRED_IN/REMEDIATION dicts.

from scout.connectors import load_registry, Capability

def run() -> Event | None:
    registry = load_registry()
    # Filter to inbound + meta connectors — outbound connectors (notify:telegram) are never alerting subjects.
    alertable = {key: c for key, c in registry.items() if Capability.OUTBOUND not in c.capabilities or len(c.capabilities) > 1}

    records = _load_records_within_window(days=14)
    if not records:
        return None

    sessions = _group_by_session(records)
    current_sid, current_mode = _current_session(sessions)
    alerts = _compute_alerts(alertable, sessions, current_sid, current_mode)

    _render_connector_health_md(registry, sessions, alerts)
    _write_pending_alerts_block(alerts)
    _append_alerts_log(alerts, current_mode)
    _fire_macos_notification(alerts)
    return Event(kind="connector_health.report.generated", source="script:connector_health_report", payload={...})
```

The `_compute_alerts` function preserves three rules:
1. **Mode-aware baseline:** alert if the connector was healthy on ≥2 of the 3 prior same-mode runs (or ≥1 of 1-2 if fewer same-mode runs exist).
2. **Chronic skip override:** if a critical connector is dark for ≥3 consecutive runs in a mode where it's required, alert even if mode-baseline rule says no.
3. **Pattern #48 suppression:** if `total_ok_ever == 0` for a connector across all runs, don't alert — it's unwired, not broken.
4. **Warning rule:** any connector with ≥3 calls and >50% errors fires WARNING (skipped if same connector already has a CRITICAL).

[Plus: register the `scoutctl connectors` sub-app for operational queries]

```python
# engine/scout/cli.py — additions

connectors_app = typer.Typer(help="Connector roster operations (read-only in v0.4).")
app.add_typer(connectors_app, name="connectors")

@connectors_app.command("list")
def cli_connectors_list():
    """List the registered connector roster."""
    reg = load_registry()
    for key, c in sorted(reg.items()):
        typer.echo(f"{key}\t{c.tier.value}\t{c.display_name}")

@connectors_app.command("show")
def cli_connectors_show(key: str):
    """Show one connector's full record."""
    reg = load_registry()
    if key not in reg:
        raise ConfigError(f"unknown connector: {key}")
    c = reg[key]
    typer.echo(json.dumps({...}, indent=2))

@connectors_app.command("reload")
def cli_connectors_reload():
    """Force-reload the YAML (useful when the overlay changed mid-session in v0.8)."""
    load_registry.cache_clear() if hasattr(load_registry, 'cache_clear') else None
    typer.echo("reloaded")
```

Bats parity test asserts `connector-health.md` byte-equality (modulo timestamps) between bash and Python runs against the same fixture inputs.

Commit:

```bash
git commit -m "feat(engine): port scripts/connector-health-report.sh → scout.scripts.connector_health_report + scoutctl connectors sub-app"
```

---

## Task 6: New Telegram outbound — `scoutctl notify telegram`

**Files:**
- Create: `engine/scout/scripts/notify_telegram.py`
- Create: `engine/tests/unit/test_scripts_notify_telegram.py`
- Modify: `engine/scout/cli.py` (registers `scoutctl notify` sub-app)
- Create: `engine/scout/docs/connectors/telegram-setup.md`

**What this builds:** Telegram Bot API outbound, accessed via `scoutctl notify telegram --tier <info|action_required> --body "<text>"`. Reads `~/.scout-secrets/telegram-bot-token` and `~/.scout-secrets/telegram-chat-id` (mode 600, gitignored). Tier controls notification loudness via `disable_notification` parameter. Returns `Event(kind="notification.sent", source="cli:notify_telegram", payload={"tier", "channel": "telegram", "body_chars": len(body)})`.

The Claude session calls this from inside its prompt (via the Bash tool) at session wrap-up — `scoutctl notify telegram --tier action_required --body "..."` — fanning out the same wrap message that goes to Slack DM. The runner stays bash-only; Plan 4 introduces no runner mutation.

[Test contract — minimum required cases:]

- Token + chat_id files present → POST to `https://api.telegram.org/bot<TOKEN>/sendMessage` with correct payload (mocked via `responses` or `requests-mock`).
- Token file missing → `ScoutError: Telegram bot token not configured. Run scoutctl notify telegram --setup` (exit code 10 = ConfigError).
- Body > 4096 chars (Telegram limit) → split into multiple messages.
- `--tier action_required` sets `disable_notification: false`; `--tier info` sets `disable_notification: true`.
- `--dry-run` flag prints the request body without sending (useful for testing).

[Telegram setup doc] documents the @BotFather flow:
1. DM `@BotFather` on Telegram.
2. `/newbot` → name "Scout" → username (user picks).
3. Save token to `~/.scout-secrets/telegram-bot-token`, mode 600.
4. User DMs the new bot once ("hi").
5. `curl https://api.telegram.org/bot<TOKEN>/getUpdates | jq '.result[0].message.chat.id'` → save numeric ID to `~/.scout-secrets/telegram-chat-id`.
6. Verify: `scoutctl notify telegram --tier info --body "hello from Scout"` → message arrives in your Telegram chat.

[Implementation outline:]

```python
# engine/scout/scripts/notify_telegram.py — full implementation

import requests
from pathlib import Path

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4096

def send(tier: str, body: str, *, dry_run: bool = False) -> Event:
    token = _read_secret("telegram-bot-token")
    chat_id = _read_secret("telegram-chat-id")

    disable_notification = (tier == "info")

    chunks = _split_message(body, MAX_MESSAGE_LEN)
    if dry_run:
        for chunk in chunks:
            typer.echo(f"[dry-run] tier={tier} body={chunk[:80]}...")
        return _make_event(tier, body, dry_run=True)

    for chunk in chunks:
        url = f"{TELEGRAM_API}/bot{token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": chunk,
            "disable_notification": disable_notification,
        }, timeout=10)
        resp.raise_for_status()

    return _make_event(tier, body)


def _read_secret(name: str) -> str:
    path = Path.home() / ".scout-secrets" / name
    if not path.exists():
        raise ConfigError(f"missing secret: {path}; see docs/connectors/telegram-setup.md")
    return path.read_text().strip()
```

Commit:

```bash
git commit -m "feat(engine): scoutctl notify telegram — Bot API outbound stub for v0.4"
```

---

## Task 7: WhatsApp inbound setup docs

**Files:**
- Create: `engine/scout/docs/connectors/whatsapp-setup.md`

**What this builds:** Operator-facing setup walkthrough for the `verygoodplugins/whatsapp-mcp` MCP server. Plan 4 adds NO engine code for WhatsApp inbound — the MCP server runs as a separate launchctl-managed service and the scheduled session uses it via `mcp__whatsapp-mcp__*` tools. The hook's classifier already maps these to `mcp:whatsapp-mcp` thanks to Task 2's verbatim port of the `classify()` function.

The setup doc covers:
1. **Install:** `git clone https://github.com/verygoodplugins/whatsapp-mcp` to `~/.local/share/whatsapp-mcp/` (or wherever the user prefers).
2. **First-time auth:** Run the Go bridge interactively, scan QR via WhatsApp → Linked Devices.
3. **Persistent service:** Wrap the Go bridge in a launchctl plist (`com.scout.whatsapp-bridge.plist`) — the doc ships a plist template with paths the user fills in.
4. **MCP server config:** Add `mcp:whatsapp-mcp` entry to `~/.scout-secrets/.mcp.json` (or the equivalent Claude Code MCP config path).
5. **Allowlist:** Add `whatsapp_id: "+15551234567"` frontmatter to `knowledge-base/people/<person>.md` entries for contacts whose threads should be scanned. Per the v0.4 spec §6 amendment, identifiers are open-set; phone_number doubles as the WhatsApp join key.
6. **Privacy posture:** WhatsApp phase MD (vault-private content; not shipped in plugin) instructs the session to scan ONLY threads where the contact appears in the `knowledge-base/people/` allowlist; never call `send_message`/`send_audio_message`/`send_file` (defense-in-depth on top of the architectural inbound-only intent).
7. **Lethal-trifecta caveat:** Document the prompt-injection risk; mitigated by inbound-only + allowlisted contacts; user acknowledges by enabling.

[No code change. Just documentation.]

Commit:

```bash
git commit -m "docs(connectors): WhatsApp inbound setup walkthrough — verygoodplugins/whatsapp-mcp"
```

---

## Task 8: scout-app sync — `ConnectorHealthService.defaultConnectors` derives from a YAML snapshot

**Files:**
- Create: `engine/scout/scripts/connectors_snapshot.py` (new `scoutctl connectors snapshot` command)
- Modify: `scout-app/Scout/Services/ConnectorHealthService.swift`
- Create: `scout-app/ScoutTests/Fixtures/connectors.snapshot.json`
- Modify: `scout-app/ScoutTests/Services/ConnectorHealthServiceTests.swift`

**What this builds:** Eliminates the manual diff between Swift's `defaultConnectors` and the Python connector roster. `scoutctl connectors snapshot` writes `connectors.snapshot.json` (a flat JSON projection of the YAML's official-tier keys + display names) into the scout-app fixtures. CI in scout-app asserts `defaultConnectors` matches the snapshot. CI in scout-plugin runs `scoutctl connectors snapshot --check` and fails if the committed snapshot has drifted from `connectors.yaml`.

The snapshot file format:

```json
{
  "schema_version": 1,
  "generated_from": "scout-plugin@d0cb43b",
  "connectors": [
    {"key": "mcp:claude_ai_Slack", "display_name": "Slack", "tier": "official"},
    {"key": "mcp:claude_ai_Linear", "display_name": "Linear", "tier": "official"},
    ...
  ]
}
```

`ConnectorHealthService.defaultConnectors` derives from this snapshot file at app launch (loaded as a bundle resource). If the snapshot is missing or malformed, the app falls back to a hardcoded default (the same 8-connector list it has today) and surfaces a banner: *"Connector roster snapshot missing — run `scoutctl connectors snapshot` in scout-plugin."*

Steps:
- [ ] Implement `scoutctl connectors snapshot` (writes JSON to a path; `--check` mode exits nonzero if the on-disk file differs from what would be written).
- [ ] Add a CI step in scout-plugin that runs `scoutctl connectors snapshot --check` against the committed fixture and fails if drift exists.
- [ ] Modify scout-app's `ConnectorHealthService.swift` to load `defaultConnectors` from the bundled snapshot.
- [ ] Update `ConnectorHealthServiceTests.swift` to assert the snapshot loads + the fallback path.

[The Swift change is small (~30 lines).]

Commits (split for reviewability):
```bash
# In scout-plugin:
git commit -m "feat(engine): scoutctl connectors snapshot — write JSON projection for scout-app sync"

# In scout-app (separate PR):
git commit -m "feat(app): ConnectorHealthService.defaultConnectors derives from connectors.snapshot.json"
```

---

## Task 9: Verify, push, open PR

[Same shape as Plan 3's Task 6: full pytest run + lint + manual smoke test + push branch + `gh pr create` with description referencing the v0.4 spec, the v0.5+ spec, and the Plan 3 base.]

The PR description body:

```markdown
## Summary

Implements v0.4 unification Plan 4: connector subsystem migration.

- **`connectors.yaml` + `scout.connectors`** — single source of truth for the
  connector roster (was a hardcoded Python dict in `connector-health-report.sh`).
- **Hook ports**: `connector-log.sh`, `sum-session-tokens.sh`, `kb-pre-filter.sh`
  → Python under `engine/scout/hooks/`. Each emits an `Event` per spec §13.2.
- **Script ports**: `connector-health-report.sh` → Python under
  `engine/scout/scripts/`. Same alerting rules, same `connector-health.md`
  rendering.
- **New connectors** (additions to the YAML roster):
  - WhatsApp inbound — operator-configured `verygoodplugins/whatsapp-mcp` MCP
    server. Setup docs ship in this PR; engine code is just the YAML entry.
  - Telegram outbound — new `scoutctl notify telegram` Bot API wrapper.
    Token + chat_id in `~/.scout-secrets/`. Used by the Claude session at
    wrap-up to fan out the run summary to Telegram in addition to Slack DM.
  - Google Messages — existing `mcp:claude-in-chrome` key promoted to
    required-in-briefing modes via the new YAML.
- **Manifest flags flipped**: `session_tokens_v1`, `connector_health_v1` → True.
- **scout-app sync**: `ConnectorHealthService.defaultConnectors` now derives
  from a JSON snapshot of the YAML, with a CI drift check.

## Spec references

- v0.4 unification spec §4 (file migration map), §6 (multi-ID person schema), §11 (plugin/vault content boundary)
- v0.5+ event-architecture spec — particularly the new Connector taxonomy and Working with the user as collaborator sections (this PR ships the v0.4 foundation those v0.7+ features build on)

## Test plan

- [x] 17 new unit tests across hooks + scripts + connectors
- [x] 3 bats parity tests (bash-vs-Python output equality)
- [x] 1 integration test: pipe a real PostToolUse payload through `scoutctl hook connector-log` end-to-end
- [x] Full `pytest tests/` green
- [x] `ruff check`, `ruff format --check`, `mypy scout` clean
- [x] Manual smoke: scout-app launches, ConnectorHealthRailCard renders 10 rows (was 8) including WhatsApp + Telegram
- [x] Manual smoke: `scoutctl notify telegram --tier info --body "hello"` lands in Jordan's Telegram chat
- [x] Manual smoke: scheduled morning briefing fires, `connector-health.md` regenerates from Python (matches bash output to within timestamp drift)

## Follow-up tasks

- Plan 4-supplement: port the remaining 7 runner-side scripts (budget, heartbeat, rate-limit-detect, collect-events, pre-session-data, cc-session-cache, write-session-cost). These are operationally orthogonal to the connector subsystem.
- v0.7 (per the event-architecture spec): upgrade Telegram outbound to a bidirectional connector with a return-bridge for inbound replies as feedback signals.
```

---

## What Plan 4-supplement and Plan 5 will build on

After Plan 4 lands:
- The connector roster is editable in one YAML. Plan 4-supplement just lifts the remaining scripts; no further connector-roster work is needed.
- `Event`-shaped emissions from every hook flow through `emit()`. Plan 5's KB-summary refresh hook plugs into the same shape.
- `scout-app` no longer drifts from the engine's connector list. Future connector additions are one YAML entry + one snapshot regeneration.
- `scoutctl notify telegram` is the seed of the v0.7 multi-channel notification fan-out layer. Plan 4 ships the outbound-only stub; v0.7 adds inbound via the bidirectional Telegram connector.
- WhatsApp inbound + Google Messages have working entries in the roster but their phase MDs (private vault content) need to be authored separately. That's not Plan 4's scope; it's a vault content task Jordan owns.
