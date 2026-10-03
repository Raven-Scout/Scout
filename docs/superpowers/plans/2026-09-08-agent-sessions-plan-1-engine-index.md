# Agent Sessions — Plan 1: Engine Session Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Scout engine one `scoutctl session index` command that merges the Claude desktop app's per-session records, CLI transcripts, live PIDs and `gh` PR state into `.scout-cache/sessions-index.json` with a derived state per session, plus a state-first `cc-sessions.md` digest that replaces today's flat 24-hour list.

**Architecture:** A new `scout/sessions/` package with one loader per source (desktop records, `~/.claude`, transcripts, GitHub), a pure derivation module (project resolution + state rules), an orchestrator that merges and writes atomically, and a renderer for the LLM digest. The existing `scout/scripts/cc_session_cache.py` shrinks to a compatibility shim: its extractors move into `scout/sessions/transcript.py` and `scoutctl session cc-cache` becomes an alias for `session index --render`.

**Tech Stack:** Python ≥ 3.11, Typer CLI, PyYAML config, pytest (+ `typer.testing.CliRunner`), ruff (line length 120), no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-08-agent-sessions-design.md` (this repo, scout-app) — sections 3, 4, 7 (engine), 8. Read §4 in full before starting; every rule below is copied from it.

**Repo:** All code in this plan lands in **`~/scout-plugin`** (the engine), not in scout-app where this plan file lives. Paths below are relative to `~/scout-plugin/engine/` unless prefixed.

## Global Constraints

- Python `>=3.11`; ruff `line-length = 120`, rules `E,F,W,I,B,UP`; run `ruff check scout tests && ruff format --check scout tests` before every commit.
- `scout/cli.py` must import nothing heavy at module top: every `scout.sessions.*` import lives **inside** the command function (perf test `tests/perf/test_no_heavy_imports.py`).
- Tests are hermetic: the autouse `_hermetic_env` fixture points `HOME` at a tmp dir and scrubs `SCOUT_*`. Never touch the real home; never call the real `gh` or `git` from tests — always inject fakes.
- Fixtures and inline test strings are anonymised per `~/scout-plugin/CLAUDE.md`: people `Alex`/`Priya`/`Sam`, repos `example-org/<repo>`, no real Linear prefixes, Slack ids, or vendor names.
- Config block is `agent_sessions:` (the `sessions:` key is already the budget block).
- Timestamps in the index are ISO-8601 UTC strings with a trailing `Z`; rendering to the configured zone happens only in `render.py` via `scout.config.resolve_timezone` / `timezone_or_default`.
- Scout never writes to the desktop app's store. Every loader is read-only.
- The digest keeps the filename `cc-sessions.md`; the index is `sessions-index.json`; both live in `paths.cache_dir()`.
- Exit codes: `0` success (including partial data), `1` output could not be written, `2` bad arguments; `--strict` promotes any `source_error` to exit `1`.
- Commit after every task with a conventional-commit subject and the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Working setup (do once)

```bash
cd ~/scout-plugin
git checkout main && git pull --ff-only
git checkout -b feat/agent-sessions-index
cd engine
[ -x .venv/bin/python ] || (uv venv && uv pip install -e ".[dev,full]")
.venv/bin/pytest tests/unit/test_cc_session_cache.py -q     # baseline: all pass
```

Run tests as `.venv/bin/pytest <path> -q` from `~/scout-plugin/engine`. Run lint as `.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests`.

## File structure

| Path (under `engine/`) | Responsibility |
|---|---|
| `scout/sessions/__init__.py` | Package marker + docstring. |
| `scout/sessions/settings.py` | `AgentSessionsSettings` dataclass; `from_config()` reads the `agent_sessions` block tolerantly. |
| `scout/sessions/model.py` | Dataclasses for the index (`Index`, `Project`, `AgentSession`, `PRInfo`, `TranscriptInfo`, `LastTurn`, `WorktreeInfo`, `SourceError`), `SCHEMA_VERSION`, time helpers `ms_to_iso`, `ns_to_iso`, `parse_iso`. |
| `scout/sessions/desktop.py` | Read-only loaders for the desktop app store: `load_desktop_records`, `load_groups`, `load_worktree_leases`, `default_support_dir`. |
| `scout/sessions/cli_home.py` | Read-only loaders for `~/.claude`: `load_live_processes`, `pid_alive`, `transcript_paths`, `project_path_from_dirname`, `default_claude_home`. |
| `scout/sessions/transcript.py` | One-pass transcript parse → `TranscriptInfo`; the moved `extract_first_message` / `extract_files_touched`; mtime-keyed cache load/write. |
| `scout/sessions/github.py` | `gh pr view` refresh with TTL cache and fetch cap; `summarize_checks`; injectable runner. |
| `scout/sessions/derive.py` | `resolve_project_key`, `is_scout_run`, `choose_pr`, `derive_state`, relative-time formatters. |
| `scout/sessions/index.py` | `build_index` orchestrator, `write_index`, `run`, `main`, `list_main`. |
| `scout/sessions/render.py` | `render_digest` → `cc-sessions.md`. |
| `scout/scripts/cc_session_cache.py` | Shrinks to re-exports of the moved extractors + a `main()` that delegates to `scout.sessions.index`. |
| `scout/defaults/scout-config.yaml` | Gains the `agent_sessions:` block. |
| `scout/cli.py` | `session index`, `session list`; `session cc-cache` becomes the alias. |
| `tests/unit/sessions_helpers.py` | Builders for fake desktop store / claude home trees (not collected: no `test_` prefix). |
| `tests/unit/test_sessions_*.py` | One test module per source module. |
| `tests/unit/test_cli_session_subapp.py` | CLI smoke tests. |
| `tests/fixtures/sessions/digest-golden.md` | Golden digest. |
| `tests/perf/test_sessions_index_perf.py` | Budget test, `@pytest.mark.perf`. |
| `CHANGELOG.md` (repo root) | `[Unreleased] → Added` entry. |

---

### Task 1: Settings and packaged defaults

**Files:**
- Create: `scout/sessions/__init__.py`
- Create: `scout/sessions/settings.py`
- Modify: `scout/defaults/scout-config.yaml` (append block)
- Test: `tests/unit/test_sessions_settings.py`

**Interfaces:**
- Produces: `AgentSessionsSettings` (frozen dataclass) with fields `stale_after_days:int=3`, `running_window_seconds:int=120`, `pr_refresh_minutes:int=10`, `pr_fetch_cap:int=25`, `transcript_window_days:int=14`, `done_visible_hours:int=24`, `render_max_per_bucket:int=15`, `use_gh:bool=True`, `desktop_support_dir:str|None=None`, `claude_home:str|None=None`; classmethod `from_config(cfg: dict) -> AgentSessionsSettings`; module function `load_settings(data_dir: Path|None=None) -> AgentSessionsSettings`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_sessions_settings.py
"""Unit tests for scout.sessions.settings."""

from __future__ import annotations

from pathlib import Path

from scout.config import load_config
from scout.sessions.settings import AgentSessionsSettings, load_settings


def test_defaults_match_spec() -> None:
    s = AgentSessionsSettings()
    assert (s.stale_after_days, s.running_window_seconds, s.pr_refresh_minutes) == (3, 120, 10)
    assert (s.pr_fetch_cap, s.transcript_window_days, s.done_visible_hours) == (25, 14, 24)
    assert s.render_max_per_bucket == 15
    assert s.use_gh is True
    assert s.desktop_support_dir is None and s.claude_home is None


def test_packaged_defaults_carry_agent_sessions_block(fake_data_dir: Path) -> None:
    cfg = load_config(fake_data_dir)
    block = cfg["agent_sessions"]
    assert block["stale_after_days"] == 3
    assert block["use_gh"] is True
    assert block["desktop_support_dir"] is None


def test_from_config_reads_overrides_and_coerces_ints() -> None:
    s = AgentSessionsSettings.from_config(
        {"agent_sessions": {"stale_after_days": "5", "use_gh": False, "claude_home": "/tmp/ch"}}
    )
    assert s.stale_after_days == 5
    assert s.use_gh is False
    assert s.claude_home == "/tmp/ch"
    assert s.pr_refresh_minutes == 10  # untouched default


def test_from_config_ignores_garbage_values() -> None:
    s = AgentSessionsSettings.from_config({"agent_sessions": {"stale_after_days": "soon", "pr_fetch_cap": -4}})
    assert s.stale_after_days == 3  # non-int falls back
    assert s.pr_fetch_cap == 25  # negative falls back


def test_from_config_tolerates_missing_or_non_mapping_block() -> None:
    assert AgentSessionsSettings.from_config({}) == AgentSessionsSettings()
    assert AgentSessionsSettings.from_config({"agent_sessions": "nope"}) == AgentSessionsSettings()


def test_load_settings_uses_vault_override(fake_data_dir: Path) -> None:
    (fake_data_dir / "scout-config.yaml").write_text("agent_sessions:\n  stale_after_days: 7\n", encoding="utf-8")
    assert load_settings(fake_data_dir).stale_after_days == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_settings.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.sessions'`

- [ ] **Step 3: Create the package and settings module**

```python
# scout/sessions/__init__.py
"""Agent-session index: one picture of every local Claude Code session.

Read-only over the desktop app's store, ``~/.claude`` and ``gh``; writes only
``.scout-cache/sessions-index.json`` and the ``cc-sessions.md`` digest.
Spec: scout-app ``docs/superpowers/specs/2026-09-08-agent-sessions-design.md``.
"""
```

```python
# scout/sessions/settings.py
"""The ``agent_sessions`` config block (spec §4.11)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from scout import config as scout_config

_INT_FIELDS = (
    "stale_after_days",
    "running_window_seconds",
    "pr_refresh_minutes",
    "pr_fetch_cap",
    "transcript_window_days",
    "done_visible_hours",
    "render_max_per_bucket",
)
_STR_FIELDS = ("desktop_support_dir", "claude_home")


@dataclass(frozen=True)
class AgentSessionsSettings:
    stale_after_days: int = 3
    running_window_seconds: int = 120
    pr_refresh_minutes: int = 10
    pr_fetch_cap: int = 25
    transcript_window_days: int = 14
    done_visible_hours: int = 24
    render_max_per_bucket: int = 15
    use_gh: bool = True
    desktop_support_dir: str | None = None
    claude_home: str | None = None

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> AgentSessionsSettings:
        """Build from a merged config dict. Bad values fall back to the default
        with a one-line stderr warning — a mangled vault file must never block a run."""
        block = cfg.get("agent_sessions")
        if not isinstance(block, dict):
            return cls()
        out = cls()
        for name in _INT_FIELDS:
            if name not in block:
                continue
            try:
                value = int(block[name])
            except (TypeError, ValueError):
                _warn(f"agent_sessions.{name}: expected an integer, got {block[name]!r} — using default")
                continue
            if value < 0:
                _warn(f"agent_sessions.{name}: must be >= 0, got {value} — using default")
                continue
            out = replace(out, **{name: value})
        if "use_gh" in block:
            out = replace(out, use_gh=bool(block["use_gh"]))
        for name in _STR_FIELDS:
            raw = block.get(name)
            if isinstance(raw, str) and raw.strip():
                out = replace(out, **{name: raw.strip()})
        return out


def load_settings(data_dir: Path | None = None) -> AgentSessionsSettings:
    return AgentSessionsSettings.from_config(scout_config.load_config(data_dir))


def _warn(msg: str) -> None:
    print(f"scout-config: {msg}", file=sys.stderr)


__all__ = ["AgentSessionsSettings", "load_settings"]
_ = fields  # keep dataclasses.fields importable for callers that introspect
```

Append to `scout/defaults/scout-config.yaml` (after the `auto_update:` block):

```yaml

# Agent-session index (scoutctl session index). Spec: scout-app
# docs/superpowers/specs/2026-09-08-agent-sessions-design.md §4.11.
# NOTE: `sessions:` is the budget block in older vaults — this is a separate key.
agent_sessions:
  stale_after_days: 3
  running_window_seconds: 120
  pr_refresh_minutes: 10
  pr_fetch_cap: 25
  transcript_window_days: 14
  done_visible_hours: 24
  render_max_per_bucket: 15
  use_gh: true
  desktop_support_dir: null
  claude_home: null
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_settings.py tests/unit/test_config.py -q`
Expected: all PASS (the existing config tests still pass because unknown-to-user keys merge through).

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests
git add scout/sessions/__init__.py scout/sessions/settings.py scout/defaults/scout-config.yaml tests/unit/test_sessions_settings.py
git commit -m "feat(sessions): agent_sessions settings block and packaged defaults

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Index data model and time helpers

**Files:**
- Create: `scout/sessions/model.py`
- Test: `tests/unit/test_sessions_model.py`

**Interfaces:**
- Produces: `SCHEMA_VERSION = 1`; `STATES = ("needs_you", "running", "waiting", "parked", "stale", "done")` (severity order); dataclasses `WorktreeInfo(path, name, branch, source_branch, dirty)`, `PRInfo(number, repo, url, state, is_draft, review_decision, review_requested, checks, merge_state, fetched_at, stale, updated_at)`, `LastTurn(at, kind)`, `TranscriptInfo(path, first_prompt, files_touched, tool_calls, last_turn, mtime_ns)`, `AgentSession(...)` (fields listed in code), `Project(key, name, group_id, counts)`, `SourceError(source, message)`, `Index(generated_at, source_counts, source_errors, display, projects, sessions, schema_version)` with `to_dict()` placing `schema_version` first; helpers `ms_to_iso(ms) -> str|None`, `ns_to_iso(ns) -> str`, `parse_iso(s) -> datetime|None`, `now_utc() -> datetime`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_sessions_model.py
"""Unit tests for scout.sessions.model."""

from __future__ import annotations

from datetime import UTC, datetime

from scout.sessions.model import (
    SCHEMA_VERSION,
    STATES,
    AgentSession,
    Index,
    LastTurn,
    PRInfo,
    Project,
    SourceError,
    TranscriptInfo,
    WorktreeInfo,
    ms_to_iso,
    ns_to_iso,
    parse_iso,
)


def _session(**over: object) -> AgentSession:
    base = dict(
        id="local_abc",
        cli_session_id="11111111-1111-1111-1111-111111111111",
        title="Fix the parser",
        title_source="auto",
        project_key="/Users/alex/code/example-repo",
        group_name="Example Repo",
        cwd="/Users/alex/code/example-repo/.claude/worktrees/w1",
        origin_cwd="/Users/alex/code/example-repo",
        worktree=WorktreeInfo(path="/Users/alex/code/example-repo/.claude/worktrees/w1", name="w1",
                              branch="claude/w1", source_branch="main", dirty=False),
        created_at="2026-09-01T10:00:00Z",
        last_activity_at="2026-09-08T10:00:00Z",
        model="claude-opus-5",
        effort="high",
        turns=4,
        is_archived=False,
        is_open=False,
        is_scout_run=False,
        parent_session_id=None,
        spawned_task_id=None,
        scheduled_task_id=None,
        prs=[],
        pr=None,
        transcript=None,
    )
    base.update(over)
    return AgentSession(**base)  # type: ignore[arg-type]


def test_states_are_in_severity_order() -> None:
    assert STATES == ("needs_you", "running", "waiting", "parked", "stale", "done")


def test_time_helpers_round_trip_utc() -> None:
    assert ms_to_iso(1_788_895_143_313) == "2026-09-08T19:19:03Z"
    assert ms_to_iso(None) is None
    assert ns_to_iso(1_788_895_143_000_000_000) == "2026-09-08T19:19:03Z"
    assert parse_iso("2026-09-08T19:19:03Z") == datetime(2026, 9, 8, 19, 19, 3, tzinfo=UTC)
    assert parse_iso("garbage") is None and parse_iso(None) is None


def test_index_to_dict_puts_schema_version_first_and_nests_dataclasses() -> None:
    pr = PRInfo(number=7, repo="example-org/example-repo", url="https://github.com/example-org/example-repo/pull/7",
                state="OPEN", is_draft=False, review_decision="", review_requested=False, checks="passing",
                merge_state="CLEAN", fetched_at="2026-09-08T10:00:00Z", stale=False, updated_at=None)
    tr = TranscriptInfo(path="~/.claude/projects/x/1.jsonl", first_prompt="hi", files_touched=["~/a.py"],
                        tool_calls=3, last_turn=LastTurn(at="2026-09-08T09:59:00Z", kind="end_turn"), mtime_ns=1)
    s = _session(pr=pr, prs=[pr], transcript=tr, state="waiting", state_reasons=["PR #7 awaiting review"])
    idx = Index(
        generated_at="2026-09-08T10:00:00Z",
        source_counts={"desktop": 1},
        source_errors=[SourceError(source="gh", message="not found")],
        display={"done_visible_hours": 24, "stale_after_days": 3},
        projects=[Project(key=s.project_key, name="Example Repo", group_id="cg-1", counts={"waiting": 1})],
        sessions=[s],
    )
    d = idx.to_dict()
    assert list(d)[0] == "schema_version" and d["schema_version"] == SCHEMA_VERSION
    assert d["sessions"][0]["pr"]["checks"] == "passing"
    assert d["sessions"][0]["transcript"]["last_turn"] == {"at": "2026-09-08T09:59:00Z", "kind": "end_turn"}
    assert d["source_errors"] == [{"source": "gh", "message": "not found"}]
    assert d["sessions"][0]["state_reasons"] == ["PR #7 awaiting review"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_model.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.sessions.model'`

- [ ] **Step 3: Write the model module**

```python
# scout/sessions/model.py
"""Index data model (spec §4.8). Plain dataclasses; ``Index.to_dict`` is the
JSON shape scout-app decodes, so field names here are a contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1

# Severity order — the app and the digest sort by position in this tuple.
STATES: tuple[str, ...] = ("needs_you", "running", "waiting", "parked", "stale", "done")

TERMINAL_PR_STATES = frozenset({"MERGED", "CLOSED"})


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms_to_iso(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    try:
        return _iso(datetime.fromtimestamp(float(ms) / 1000.0, tz=UTC))
    except (OverflowError, OSError, ValueError):
        return None


def ns_to_iso(ns: int) -> str:
    return _iso(datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC))


def dt_to_iso(dt: datetime) -> str:
    return _iso(dt)


def parse_iso(s: str | None) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


@dataclass
class WorktreeInfo:
    path: str | None
    name: str | None
    branch: str | None
    source_branch: str | None
    dirty: bool


@dataclass
class PRInfo:
    number: int
    repo: str
    url: str | None
    state: str  # OPEN | MERGED | CLOSED | unknown
    is_draft: bool
    review_decision: str  # "" | APPROVED | CHANGES_REQUESTED | REVIEW_REQUIRED | unknown
    review_requested: bool
    checks: str  # passing | failing | pending | none | unknown
    merge_state: str  # gh mergeStateStatus, e.g. CLEAN | DIRTY | BLOCKED | unknown
    fetched_at: str | None
    stale: bool
    updated_at: str | None

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"


@dataclass
class LastTurn:
    at: str | None
    kind: str  # end_turn | tool_use | question | unknown


@dataclass
class TranscriptInfo:
    path: str
    first_prompt: str
    files_touched: list[str]
    tool_calls: int
    last_turn: LastTurn
    mtime_ns: int


@dataclass
class AgentSession:
    id: str
    cli_session_id: str | None
    title: str | None
    title_source: str | None
    project_key: str
    group_name: str | None
    cwd: str
    origin_cwd: str
    worktree: WorktreeInfo | None
    created_at: str | None
    last_activity_at: str | None
    model: str | None
    effort: str | None
    turns: int | None
    is_archived: bool
    is_open: bool
    is_scout_run: bool
    parent_session_id: str | None
    spawned_task_id: str | None
    scheduled_task_id: str | None
    prs: list[PRInfo]
    pr: PRInfo | None
    transcript: TranscriptInfo | None
    state: str = "parked"
    state_reasons: list[str] = field(default_factory=list)


@dataclass
class Project:
    key: str
    name: str
    group_id: str | None
    counts: dict[str, int]


@dataclass
class SourceError:
    source: str
    message: str


@dataclass
class Index:
    generated_at: str
    source_counts: dict[str, int]
    source_errors: list[SourceError]
    display: dict[str, int]
    projects: list[Project]
    sessions: list[AgentSession]
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body.pop("schema_version")
        return {"schema_version": self.schema_version, **body}


__all__ = [
    "SCHEMA_VERSION",
    "STATES",
    "TERMINAL_PR_STATES",
    "AgentSession",
    "Index",
    "LastTurn",
    "PRInfo",
    "Project",
    "SourceError",
    "TranscriptInfo",
    "WorktreeInfo",
    "dt_to_iso",
    "ms_to_iso",
    "now_utc",
    "ns_to_iso",
    "parse_iso",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_model.py -q`
Expected: 3 passed

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests
git add scout/sessions/model.py tests/unit/test_sessions_model.py
git commit -m "feat(sessions): index data model and UTC time helpers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Desktop store loaders (records, groups, worktree leases)

**Files:**
- Create: `scout/sessions/desktop.py`
- Create: `tests/unit/sessions_helpers.py` (fake-tree builders reused by later tasks)
- Test: `tests/unit/test_sessions_desktop.py`

**Interfaces:**
- Produces: `default_support_dir() -> Path` (= `Path.home()/"Library"/"Application Support"/"Claude"`); dataclasses `PRRef(number:int, repo:str, url:str|None, legacy_state:str|None)`, `DesktopRecord(session_id, cli_session_id, title, title_source, cwd, origin_cwd, worktree_path, worktree_name, branch, source_branch, created_at_ms, last_activity_at_ms, model, effort, is_archived, completed_turns, prs:list[PRRef], parent_session_id, spawned_task_id, scheduled_task_id, kept_dirty_worktree, transcript_unavailable)`, `Groups(names:dict[str,str], assignments:dict[str,str])`, `WorktreeLease(path, branch, source_branch, base_repo)`; functions `load_desktop_records(support_dir) -> tuple[list[DesktopRecord], list[SourceError]]`, `load_groups(support_dir) -> tuple[Groups, list[SourceError]]`, `load_worktree_leases(support_dir) -> tuple[dict[str, WorktreeLease], list[SourceError]]` (keyed by leasing session id).
- Test helpers produced (used by Tasks 4–10): `support_dir()`, `claude_home()`, `write_desktop_record(support, session_id, **fields) -> Path`, `write_desktop_config(support, groups: dict[str,str], assignments: dict[str,str])`, `write_worktrees(support, leases: dict[str, dict])`, `write_transcript(home, encoded_dir, uuid, rows, *, mtime_ago_hours=1.0) -> Path`, `write_pid_file(home, pid, uuid, cwd)`, `ORG`, `USER` constants.

- [ ] **Step 1: Write the helpers module**

```python
# tests/unit/sessions_helpers.py
"""Builders for fake Claude desktop-store and ~/.claude trees.

Everything is rooted at ``Path.home()``, which the autouse ``_hermetic_env``
fixture points at a per-test tmp dir. All identifiers are synthetic
(``example-org/…``, ``Alex``) per CLAUDE.md.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ORG = "org-0000"
USER = "user-0000"


def support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "Claude"


def claude_home() -> Path:
    return Path.home() / ".claude"


def write_desktop_record(support: Path, session_id: str, **fields: Any) -> Path:
    """Write one ``local_<id>.json``. Unspecified fields get plausible defaults."""
    record: dict[str, Any] = {
        "sessionId": session_id,
        "cliSessionId": fields.pop("cliSessionId", f"{session_id[-8:]:0>8}-0000-0000-0000-000000000000"),
        "cwd": "/Users/alex/code/example-repo",
        "originCwd": "/Users/alex/code/example-repo",
        "createdAt": 1_788_400_000_000,
        "lastActivityAt": 1_788_800_000_000,
        "model": "claude-opus-5",
        "effort": "high",
        "isArchived": False,
        "permissionMode": "auto",
        "title": "Fix the parser",
        "titleSource": "auto",
        "completedTurns": 4,
        "enabledMcpTools": {"": True},
    }
    record.update(fields)
    d = support / "claude-code-sessions" / ORG / USER
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session_id}.json"
    p.write_text(json.dumps(record), encoding="utf-8")
    return p


def write_desktop_config(support: Path, groups: dict[str, str], assignments: dict[str, str]) -> Path:
    """``groups`` maps group id → name; ``assignments`` maps session id → group id."""
    support.mkdir(parents=True, exist_ok=True)
    payload = {
        "mcpServers": {},
        "preferences": {
            "epitaxyPrefs": {
                "dframe-group-scopes": {
                    f"{ORG}/{USER}": {
                        "groups": [{"id": gid, "name": name} for gid, name in groups.items()],
                        "assignments": {f"code:{sid}": gid for sid, gid in assignments.items()},
                    }
                }
            }
        },
    }
    p = support / "claude_desktop_config.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def write_worktrees(support: Path, leases: dict[str, dict[str, Any]]) -> Path:
    support.mkdir(parents=True, exist_ok=True)
    p = support / "git-worktrees.json"
    p.write_text(json.dumps({"worktrees": leases}), encoding="utf-8")
    return p


def write_transcript(
    home: Path, encoded_dir: str, uuid: str, rows: list[dict[str, Any]], *, mtime_ago_hours: float = 1.0
) -> Path:
    p = home / "projects" / encoded_dir / f"{uuid}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    ts = (datetime.now(tz=UTC) - timedelta(hours=mtime_ago_hours)).timestamp()
    os.utime(p, (ts, ts))
    return p


def write_pid_file(home: Path, pid: int, uuid: str, cwd: str) -> Path:
    d = home / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{pid}.json"
    p.write_text(
        json.dumps({"pid": pid, "sessionId": uuid, "cwd": cwd, "startedAt": 1_788_800_000_000, "entrypoint": "cli"}),
        encoding="utf-8",
    )
    return p
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/unit/test_sessions_desktop.py
"""Unit tests for scout.sessions.desktop (read-only loaders over the desktop app store)."""

from __future__ import annotations

from pathlib import Path

from scout.sessions.desktop import (
    default_support_dir,
    load_desktop_records,
    load_groups,
    load_worktree_leases,
)
from tests.unit.sessions_helpers import (
    support_dir,
    write_desktop_config,
    write_desktop_record,
    write_worktrees,
)


def test_default_support_dir_is_under_home() -> None:
    assert default_support_dir() == Path.home() / "Library" / "Application Support" / "Claude"


def test_missing_store_is_not_an_error() -> None:
    records, errors = load_desktop_records(support_dir())
    assert records == [] and errors == []


def test_loads_records_and_skips_tombstones() -> None:
    s = support_dir()
    write_desktop_record(
        s,
        "local_aaa",
        prs=[{"prNumber": 98, "repo": "example-org/example-repo", "url": "https://github.com/example-org/example-repo/pull/98"}],
    )
    write_desktop_record(
        s,
        "local_bbb",
        prNumber=64,
        prRepository="example-org/other",
        prState="MERGED",
        prUrl="https://github.com/example-org/other/pull/64",
        spawnedFrom={"sessionId": "local_aaa", "taskId": "task_1"},
        worktreePath="/Users/alex/code/other/.claude/worktrees/w9",
        worktreeName="w9",
        branch="claude/w9",
        sourceBranch="main",
        keptDirtyWorktree=True,
        transcriptUnavailable=True,
        scheduledTaskId="scout-research",
    )
    tomb = s / "claude-code-sessions" / "org-0000" / "user-0000" / "deleted_ccc"
    tomb.write_text("1788800000000", encoding="utf-8")

    records, errors = load_desktop_records(s)
    assert errors == []
    by_id = {r.session_id: r for r in records}
    assert set(by_id) == {"local_aaa", "local_bbb"}

    a = by_id["local_aaa"]
    assert a.title == "Fix the parser" and a.model == "claude-opus-5" and a.completed_turns == 4
    assert a.prs[0].number == 98 and a.prs[0].repo == "example-org/example-repo" and a.prs[0].legacy_state is None
    assert a.worktree_path is None and a.kept_dirty_worktree is False

    b = by_id["local_bbb"]
    assert b.prs[0].number == 64 and b.prs[0].repo == "example-org/other" and b.prs[0].legacy_state == "MERGED"
    assert b.parent_session_id == "local_aaa" and b.spawned_task_id == "task_1"
    assert b.worktree_name == "w9" and b.branch == "claude/w9" and b.source_branch == "main"
    assert b.kept_dirty_worktree is True and b.transcript_unavailable is True
    assert b.scheduled_task_id == "scout-research"


def test_malformed_record_is_reported_not_fatal() -> None:
    s = support_dir()
    write_desktop_record(s, "local_ok")
    bad = s / "claude-code-sessions" / "org-0000" / "user-0000" / "local_bad.json"
    bad.write_text("{not json", encoding="utf-8")
    records, errors = load_desktop_records(s)
    assert [r.session_id for r in records] == ["local_ok"]
    assert len(errors) == 1 and errors[0].source == "desktop" and "local_bad.json" in errors[0].message


def test_load_groups_reads_names_and_strips_code_prefix() -> None:
    s = support_dir()
    write_desktop_config(s, {"cg-1": "Example Repo", "cg-2": "Archived"}, {"local_aaa": "cg-1", "local_bbb": "cg-2"})
    groups, errors = load_groups(s)
    assert errors == []
    assert groups.names == {"cg-1": "Example Repo", "cg-2": "Archived"}
    assert groups.assignments == {"local_aaa": "cg-1", "local_bbb": "cg-2"}


def test_load_groups_without_config_is_empty_and_quiet() -> None:
    groups, errors = load_groups(support_dir())
    assert groups.names == {} and groups.assignments == {} and errors == []


def test_load_worktree_leases_keyed_by_leasing_session() -> None:
    s = support_dir()
    write_worktrees(
        s,
        {
            "w9": {
                "name": "w9",
                "path": "/Users/alex/code/other/.claude/worktrees/w9",
                "baseRepo": "/Users/alex/code/other",
                "branch": "claude/w9",
                "sourceBranch": "main",
                "leasedBy": "local_bbb",
            },
            "orphan": {"name": "orphan", "path": "/tmp/x", "baseRepo": "/tmp"},
        },
    )
    leases, errors = load_worktree_leases(s)
    assert errors == []
    assert set(leases) == {"local_bbb"}
    assert leases["local_bbb"].path.endswith("/w9") and leases["local_bbb"].branch == "claude/w9"
    assert leases["local_bbb"].base_repo == "/Users/alex/code/other"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_desktop.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.sessions.desktop'`

- [ ] **Step 4: Write the desktop loaders**

```python
# scout/sessions/desktop.py
"""Read-only loaders for the Claude desktop app's on-disk session store (spec §1 table, §4.1).

Layout (macOS)::

    ~/Library/Application Support/Claude/
      claude-code-sessions/<org>/<user>/local_<uuid>.json   one record per session
      claude-code-sessions/<org>/<user>/deleted_<uuid>       tombstone (skipped)
      claude_desktop_config.json                            sidebar groups + assignments
      git-worktrees.json                                    worktree leases

Nothing here writes. Every loader returns ``(data, errors)``; a missing
directory or file is *not* an error, a malformed file is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout.sessions.model import SourceError


def default_support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "Claude"


@dataclass(frozen=True)
class PRRef:
    number: int
    repo: str
    url: str | None
    legacy_state: str | None  # only the pre-`prs[]` schema carried `prState`


@dataclass(frozen=True)
class DesktopRecord:
    session_id: str
    cli_session_id: str | None
    title: str | None
    title_source: str | None
    cwd: str
    origin_cwd: str
    worktree_path: str | None
    worktree_name: str | None
    branch: str | None
    source_branch: str | None
    created_at_ms: int | None
    last_activity_at_ms: int | None
    model: str | None
    effort: str | None
    is_archived: bool
    completed_turns: int | None
    prs: list[PRRef]
    parent_session_id: str | None
    spawned_task_id: str | None
    scheduled_task_id: str | None
    kept_dirty_worktree: bool
    transcript_unavailable: bool


@dataclass(frozen=True)
class Groups:
    names: dict[str, str]  # group id -> display name
    assignments: dict[str, str]  # session id -> group id


@dataclass(frozen=True)
class WorktreeLease:
    path: str
    branch: str | None
    source_branch: str | None
    base_repo: str | None


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _prs(raw: dict[str, Any]) -> list[PRRef]:
    out: list[PRRef] = []
    seen: set[str] = set()
    for item in raw.get("prs") or []:
        if not isinstance(item, dict):
            continue
        number, repo = _int(item.get("prNumber")), _str(item.get("repo"))
        if number is None or repo is None or f"{repo}#{number}" in seen:
            continue
        seen.add(f"{repo}#{number}")
        out.append(PRRef(number=number, repo=repo, url=_str(item.get("url")), legacy_state=None))
    number, repo = _int(raw.get("prNumber")), _str(raw.get("prRepository"))
    if number is not None and repo is not None and f"{repo}#{number}" not in seen:
        out.append(PRRef(number=number, repo=repo, url=_str(raw.get("prUrl")), legacy_state=_str(raw.get("prState"))))
    return out


def _record(raw: dict[str, Any], fallback_id: str) -> DesktopRecord:
    spawned = raw.get("spawnedFrom") if isinstance(raw.get("spawnedFrom"), dict) else {}
    cwd = _str(raw.get("cwd")) or ""
    return DesktopRecord(
        session_id=_str(raw.get("sessionId")) or fallback_id,
        cli_session_id=_str(raw.get("cliSessionId")),
        title=_str(raw.get("title")),
        title_source=_str(raw.get("titleSource")),
        cwd=cwd,
        origin_cwd=_str(raw.get("originCwd")) or cwd,
        worktree_path=_str(raw.get("worktreePath")),
        worktree_name=_str(raw.get("worktreeName")),
        branch=_str(raw.get("branch")),
        source_branch=_str(raw.get("sourceBranch")),
        created_at_ms=_int(raw.get("createdAt")),
        last_activity_at_ms=_int(raw.get("lastActivityAt")),
        model=_str(raw.get("model")),
        effort=_str(raw.get("effort")),
        is_archived=bool(raw.get("isArchived", False)),
        completed_turns=_int(raw.get("completedTurns")),
        prs=_prs(raw),
        parent_session_id=_str(spawned.get("sessionId")),
        spawned_task_id=_str(spawned.get("taskId")),
        scheduled_task_id=_str(raw.get("scheduledTaskId")),
        kept_dirty_worktree=bool(raw.get("keptDirtyWorktree", False)),
        transcript_unavailable=bool(raw.get("transcriptUnavailable", False)),
    )


def load_desktop_records(support_dir: Path) -> tuple[list[DesktopRecord], list[SourceError]]:
    root = support_dir / "claude-code-sessions"
    records: list[DesktopRecord] = []
    errors: list[SourceError] = []
    if not root.is_dir():
        return records, errors
    for path in sorted(root.glob("*/*/local_*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            errors.append(SourceError(source="desktop", message=f"{path.name}: {e}"))
            continue
        if not isinstance(raw, dict):
            errors.append(SourceError(source="desktop", message=f"{path.name}: not a JSON object"))
            continue
        records.append(_record(raw, fallback_id=path.stem))
    return records, errors


def _read_json(path: Path, source: str, errors: list[SourceError]) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        errors.append(SourceError(source=source, message=f"{path.name}: {e}"))
        return None
    return raw if isinstance(raw, dict) else None


def load_groups(support_dir: Path) -> tuple[Groups, list[SourceError]]:
    errors: list[SourceError] = []
    raw = _read_json(support_dir / "claude_desktop_config.json", "desktop-config", errors)
    names: dict[str, str] = {}
    assignments: dict[str, str] = {}
    prefs = raw.get("preferences") if raw else None
    epitaxy = prefs.get("epitaxyPrefs") if isinstance(prefs, dict) else None
    scopes = epitaxy.get("dframe-group-scopes") if isinstance(epitaxy, dict) else None
    if isinstance(scopes, dict):
        for scope in scopes.values():
            if not isinstance(scope, dict):
                continue
            for g in scope.get("groups") or []:
                if isinstance(g, dict) and _str(g.get("id")) and _str(g.get("name")):
                    names[g["id"]] = g["name"]
            for key, gid in (scope.get("assignments") or {}).items():
                if isinstance(key, str) and isinstance(gid, str):
                    assignments[key.removeprefix("code:")] = gid
    return Groups(names=names, assignments=assignments), errors


def load_worktree_leases(support_dir: Path) -> tuple[dict[str, WorktreeLease], list[SourceError]]:
    errors: list[SourceError] = []
    raw = _read_json(support_dir / "git-worktrees.json", "desktop-worktrees", errors)
    leases: dict[str, WorktreeLease] = {}
    for entry in ((raw or {}).get("worktrees") or {}).values():
        if not isinstance(entry, dict):
            continue
        holder, path = _str(entry.get("leasedBy")), _str(entry.get("path"))
        if holder is None or path is None:
            continue
        leases[holder] = WorktreeLease(
            path=path,
            branch=_str(entry.get("branch")),
            source_branch=_str(entry.get("sourceBranch")),
            base_repo=_str(entry.get("baseRepo")),
        )
    return leases, errors


__all__ = [
    "DesktopRecord",
    "Groups",
    "PRRef",
    "WorktreeLease",
    "default_support_dir",
    "load_desktop_records",
    "load_groups",
    "load_worktree_leases",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_desktop.py -q`
Expected: 7 passed

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests
git add scout/sessions/desktop.py tests/unit/sessions_helpers.py tests/unit/test_sessions_desktop.py
git commit -m "feat(sessions): read-only loaders for the Claude desktop session store

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `~/.claude` loaders (live PIDs, transcript discovery)

**Files:**
- Create: `scout/sessions/cli_home.py`
- Test: `tests/unit/test_sessions_cli_home.py`

**Interfaces:**
- Produces: `default_claude_home() -> Path` (= `Path.home()/".claude"`); `pid_alive(pid:int) -> bool`; dataclass `LiveProcess(pid:int, cli_session_id:str, cwd:str|None, started_at_ms:int|None)`; `load_live_processes(claude_home, *, is_alive=pid_alive) -> tuple[dict[str, LiveProcess], list[SourceError]]` keyed by CLI session uuid; `transcript_paths(claude_home) -> dict[str, Path]` (uuid → newest JSONL); `project_path_from_dirname(dirname:str) -> str` (moved from `cc_session_cache`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_sessions_cli_home.py
"""Unit tests for scout.sessions.cli_home."""

from __future__ import annotations

import os
from pathlib import Path

from scout.sessions.cli_home import (
    default_claude_home,
    load_live_processes,
    pid_alive,
    project_path_from_dirname,
    transcript_paths,
)
from tests.unit.sessions_helpers import claude_home, write_pid_file, write_transcript

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"


def test_default_claude_home() -> None:
    assert default_claude_home() == Path.home() / ".claude"


def test_pid_alive_for_self_and_dead_pid() -> None:
    assert pid_alive(os.getpid()) is True
    assert pid_alive(2**22 - 1) is False  # far above pid_max on macOS/Linux


def test_load_live_processes_drops_dead_pids_and_bad_files() -> None:
    h = claude_home()
    write_pid_file(h, 111, U1, "/Users/alex/code/example-repo")
    write_pid_file(h, 222, U2, "/Users/alex/code/other")
    (h / "sessions" / "333.json").write_text("nope", encoding="utf-8")
    live, errors = load_live_processes(h, is_alive=lambda pid: pid == 111)
    assert set(live) == {U1}
    assert live[U1].pid == 111 and live[U1].cwd == "/Users/alex/code/example-repo"
    assert len(errors) == 1 and errors[0].source == "claude-home" and "333.json" in errors[0].message


def test_load_live_processes_missing_dir_is_quiet() -> None:
    live, errors = load_live_processes(claude_home())
    assert live == {} and errors == []


def test_transcript_paths_maps_uuid_to_newest_file() -> None:
    h = claude_home()
    old = write_transcript(h, "-Users-alex-code-example-repo", U1, [{"type": "user"}], mtime_ago_hours=30)
    new = write_transcript(h, "-Users-alex-code-example-repo--claude-worktrees-w1", U1, [{"type": "user"}])
    write_transcript(h, "-Users-alex-code-other", U2, [{"type": "user"}])
    paths = transcript_paths(h)
    assert paths[U1] == new and paths[U1] != old
    assert paths[U2].parent.name == "-Users-alex-code-other"


def test_project_path_from_dirname() -> None:
    assert project_path_from_dirname("-Users-alex-code-repo") == "/Users/alex/code/repo"
    assert project_path_from_dirname("opaque") == "opaque"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_cli_home.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.sessions.cli_home'`

- [ ] **Step 3: Write the module**

```python
# scout/sessions/cli_home.py
"""Read-only loaders for ``~/.claude`` (spec §4.4, §4.1).

* ``sessions/<pid>.json`` exists only while that Claude Code process is alive;
  we double-check with a zero signal so a crash-leftover file is not "open".
* ``projects/<encoded-cwd>/<uuid>.jsonl`` is the transcript; the same uuid can
  appear under two encoded dirs when a session moved cwd — the newest wins.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scout.sessions.model import SourceError


def default_claude_home() -> Path:
    return Path.home() / ".claude"


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass(frozen=True)
class LiveProcess:
    pid: int
    cli_session_id: str
    cwd: str | None
    started_at_ms: int | None


def load_live_processes(
    claude_home: Path, *, is_alive: Callable[[int], bool] = pid_alive
) -> tuple[dict[str, LiveProcess], list[SourceError]]:
    root = claude_home / "sessions"
    live: dict[str, LiveProcess] = {}
    errors: list[SourceError] = []
    if not root.is_dir():
        return live, errors
    for path in sorted(root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            errors.append(SourceError(source="claude-home", message=f"{path.name}: {e}"))
            continue
        if not isinstance(raw, dict):
            continue
        pid, sid = raw.get("pid"), raw.get("sessionId")
        if not isinstance(pid, int) or not isinstance(sid, str) or not sid:
            continue
        if not is_alive(pid):
            continue
        cwd = raw.get("cwd") if isinstance(raw.get("cwd"), str) else None
        started = raw.get("startedAt") if isinstance(raw.get("startedAt"), int) else None
        live[sid] = LiveProcess(pid=pid, cli_session_id=sid, cwd=cwd, started_at_ms=started)
    return live, errors


def transcript_paths(claude_home: Path) -> dict[str, Path]:
    root = claude_home / "projects"
    out: dict[str, Path] = {}
    if not root.is_dir():
        return out
    for path in root.glob("*/*.jsonl"):
        uuid = path.stem
        prev = out.get(uuid)
        if prev is None:
            out[uuid] = path
            continue
        try:
            if path.stat().st_mtime_ns > prev.stat().st_mtime_ns:
                out[uuid] = path
        except OSError:
            continue
    return out


def project_path_from_dirname(dirname: str) -> str:
    """Decode Claude Code's project-dir naming back into a filesystem path.

    ``-Users-alex-code-repo`` → ``/Users/alex/code/repo``. Lossy for folder
    names that contain hyphens — callers prefer the desktop record's ``cwd``
    whenever one exists.
    """
    if not dirname.startswith("-"):
        return dirname
    return "/" + dirname[1:].replace("-", "/")


__all__ = [
    "LiveProcess",
    "default_claude_home",
    "load_live_processes",
    "pid_alive",
    "project_path_from_dirname",
    "transcript_paths",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_cli_home.py -q`
Expected: 6 passed

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests
git add scout/sessions/cli_home.py tests/unit/test_sessions_cli_home.py
git commit -m "feat(sessions): live-process and transcript discovery under ~/.claude

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Transcript facts in one pass, with the mtime cache

**Files:**
- Create: `scout/sessions/transcript.py`
- Modify: `scout/scripts/cc_session_cache.py` (replace the two extractor bodies + their regex constants with re-exports; everything else untouched for now — Task 10 retires the rest)
- Test: `tests/unit/test_sessions_transcript.py`

**Interfaces:**
- Produces: `extract_first_message(path) -> str`, `extract_files_touched(path, home=None) -> list[str]` (moved verbatim); `parse_transcript(path, *, st: os.stat_result|None=None, home: Path|None=None) -> TranscriptInfo`; `load_transcript_cache(path) -> dict[str, TranscriptInfo]`; `write_transcript_cache(path, entries) -> None`; `transcript_info(path, *, cache: dict[str, TranscriptInfo], home=None) -> TranscriptInfo` (reuses the cached entry when `mtime_ns` matches, else re-parses and updates `cache` in place); constant `TRANSCRIPT_CACHE_FILENAME = "sessions-transcripts.cache.json"`.
- `LastTurn.kind` rules (spec §4.5): `question` if the final assistant message has an `AskUserQuestion` tool_use with no later `tool_result` for its id, or its last text block ends with `?`; `tool_use` if it contains any other tool_use; `end_turn` otherwise; `unknown` when there is no assistant message.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_sessions_transcript.py
"""Unit tests for scout.sessions.transcript."""

from __future__ import annotations

import json
import os
from pathlib import Path

from scout.sessions.model import TranscriptInfo
from scout.sessions.transcript import (
    TRANSCRIPT_CACHE_FILENAME,
    extract_files_touched,
    extract_first_message,
    load_transcript_cache,
    parse_transcript,
    transcript_info,
    write_transcript_cache,
)
from tests.unit.sessions_helpers import claude_home, write_transcript

U1 = "11111111-1111-1111-1111-111111111111"


def _user(text: str, ts: str) -> dict:
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _assistant(blocks: list[dict], ts: str) -> dict:
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": blocks}}


def _tool_result(tool_use_id: str, ts: str) -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}]},
    }


def test_parse_transcript_end_turn_counts_tools_and_files() -> None:
    p = write_transcript(
        claude_home(),
        "-Users-alex-code-example-repo",
        U1,
        [
            {"type": "custom-title", "customTitle": "scratch"},
            _user("please fix the parser", "2026-09-08T10:00:00.000Z"),
            _assistant(
                [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/Users/alex/code/example-repo/a.py"}}],
                "2026-09-08T10:00:05.000Z",
            ),
            _tool_result("t1", "2026-09-08T10:00:06.000Z"),
            _assistant(
                [{"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "/Users/alex/code/example-repo/a.py"}}],
                "2026-09-08T10:00:07.000Z",
            ),
            _tool_result("t2", "2026-09-08T10:00:08.000Z"),
            _assistant([{"type": "text", "text": "Done. The parser now handles blanks."}], "2026-09-08T10:00:09.000Z"),
        ],
    )
    info = parse_transcript(p)
    assert info.first_prompt == "please fix the parser"
    assert info.files_touched == ["/Users/alex/code/example-repo/a.py"]
    assert info.tool_calls == 2
    assert info.last_turn.kind == "end_turn"
    assert info.last_turn.at == "2026-09-08T10:00:09Z"
    assert info.mtime_ns == p.stat().st_mtime_ns


def test_parse_transcript_question_via_ask_user_question_tool() -> None:
    p = write_transcript(
        claude_home(),
        "-Users-alex-code-example-repo",
        U1,
        [
            _user("go", "2026-09-08T10:00:00.000Z"),
            _assistant(
                [{"type": "tool_use", "id": "q1", "name": "AskUserQuestion", "input": {"questions": []}}],
                "2026-09-08T10:00:01.000Z",
            ),
        ],
    )
    assert parse_transcript(p).last_turn.kind == "question"


def test_parse_transcript_answered_question_is_tool_use_not_question() -> None:
    p = write_transcript(
        claude_home(),
        "-Users-alex-code-example-repo",
        U1,
        [
            _user("go", "2026-09-08T10:00:00.000Z"),
            _assistant(
                [{"type": "tool_use", "id": "q1", "name": "AskUserQuestion", "input": {"questions": []}}],
                "2026-09-08T10:00:01.000Z",
            ),
            _tool_result("q1", "2026-09-08T10:00:30.000Z"),
        ],
    )
    assert parse_transcript(p).last_turn.kind == "tool_use"


def test_parse_transcript_question_via_trailing_question_mark() -> None:
    p = write_transcript(
        claude_home(),
        "-Users-alex-code-example-repo",
        U1,
        [_user("go", "2026-09-08T10:00:00.000Z"), _assistant([{"type": "text", "text": "Which repo do you mean?"}], "2026-09-08T10:00:01.000Z")],
    )
    assert parse_transcript(p).last_turn.kind == "question"


def test_parse_transcript_without_assistant_is_unknown_and_tolerates_garbage() -> None:
    p = write_transcript(claude_home(), "-Users-alex-code-example-repo", U1, [_user("hi", "2026-09-08T10:00:00.000Z")])
    p.write_text(p.read_text(encoding="utf-8") + "not json at all\n", encoding="utf-8")
    info = parse_transcript(p)
    assert info.last_turn.kind == "unknown" and info.tool_calls == 0


def test_moved_extractors_keep_their_contract(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(
        json.dumps({"type": "user", "message": {"content": "found it"}})
        + "\n"
        + '{"file_path":"/Users/me/.claude/plugins/cache/abc"}\n'
        + '{"file_path":"/Users/me/repo/src/main.py"}\n',
        encoding="utf-8",
    )
    assert extract_first_message(jsonl) == "found it"
    assert extract_files_touched(jsonl) == ["/Users/me/repo/src/main.py"]


def test_transcript_cache_round_trip_and_mtime_reuse(tmp_path: Path) -> None:
    p = write_transcript(
        claude_home(), "-Users-alex-code-example-repo", U1, [_user("warm one", "2026-09-08T10:00:00.000Z")]
    )
    cache: dict[str, TranscriptInfo] = {}
    first = transcript_info(p, cache=cache)
    assert first.first_prompt == "warm one" and str(p) in cache

    cache_path = tmp_path / TRANSCRIPT_CACHE_FILENAME
    write_transcript_cache(cache_path, cache)
    reloaded = load_transcript_cache(cache_path)
    assert reloaded[str(p)] == first

    # Corrupt the file but keep mtime: the cached entry must be served.
    mtime = p.stat().st_mtime_ns
    p.write_bytes(b"garbage\n")
    os.utime(p, ns=(mtime, mtime))
    assert transcript_info(p, cache=reloaded).first_prompt == "warm one"

    # Bump mtime: re-parse.
    os.utime(p, ns=(mtime + 1_000_000_000, mtime + 1_000_000_000))
    assert "could not extract" in transcript_info(p, cache=reloaded).first_prompt


def test_load_transcript_cache_missing_or_corrupt_is_empty(tmp_path: Path) -> None:
    assert load_transcript_cache(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("[1,2", encoding="utf-8")
    assert load_transcript_cache(bad) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_transcript.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.sessions.transcript'`

- [ ] **Step 3: Write the transcript module**

```python
# scout/sessions/transcript.py
"""One-pass transcript facts (spec §4.5) plus the mtime-keyed cache.

``extract_first_message`` / ``extract_files_touched`` moved here verbatim from
``scout.scripts.cc_session_cache`` (that module re-exports them). ``parse_transcript``
walks the file once and returns everything the index needs.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scout.sessions.model import LastTurn, TranscriptInfo, parse_iso, dt_to_iso

TRANSCRIPT_CACHE_FILENAME = "sessions-transcripts.cache.json"

_HEAD_LINES_FOR_FIRST_MSG = 50
_MAX_FILES_TOUCHED = 10
_FIRST_MSG_MAX_CHARS = 500
_FILES_NOISE_RE = re.compile(
    r"(/\.claude/projects/.*/tool-results/"
    r"|/\.claude/projects/.*/tasks/"
    r"|/\.claude/plugins/cache/"
    r"|/node_modules/"
    r"|^/private/tmp/claude-"
    r"|/\.claude/projects/.*/memory/)"
)
_FILE_PATH_LINE_RE = re.compile(r'"file_path"\s*:\s*"([^"]+)"')


# ----- moved extractors (unchanged behaviour) --------------------------------


def extract_first_message(jsonl_path: Path) -> str:
    """Return the first user-typed prompt from a CC JSONL (first 50 lines, 500 chars)."""
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            for i, raw in enumerate(f):
                if i >= _HEAD_LINES_FOR_FIRST_MSG:
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                kind = obj.get("type") or obj.get("role")
                if kind not in ("user", "human"):
                    continue
                msg = obj.get("message")
                content: Any
                content = msg.get("content") if isinstance(msg, dict) else obj.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = (part.get("text") or "")[:_FIRST_MSG_MAX_CHARS]
                            if text:
                                return text
                elif isinstance(content, str) and content.strip():
                    return content[:_FIRST_MSG_MAX_CHARS]
    except OSError:
        return "(parse error)"
    return "(could not extract first message)"


def extract_files_touched(jsonl_path: Path, home: Path | None = None) -> list[str]:
    """Return up to 10 unique user-meaningful files referenced in the JSONL."""
    home_str = str(home or Path.home())
    seen: set[str] = set()
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                for m in _FILE_PATH_LINE_RE.finditer(line):
                    path = m.group(1)
                    if _FILES_NOISE_RE.search(path):
                        continue
                    if path.startswith(home_str + "/"):
                        path = "~/" + path[len(home_str) + 1 :]
                    seen.add(path)
    except OSError:
        return []
    return sorted(seen)[:_MAX_FILES_TOUCHED]


# ----- one-pass parse --------------------------------------------------------


def _blocks(obj: dict[str, Any]) -> list[dict[str, Any]]:
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _last_turn_kind(last_assistant: dict[str, Any] | None, answered: set[str]) -> str:
    if last_assistant is None:
        return "unknown"
    blocks = _blocks(last_assistant)
    tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
    for b in tool_uses:
        if b.get("name") == "AskUserQuestion" and str(b.get("id")) not in answered:
            return "question"
    if tool_uses:
        return "tool_use"
    texts = [b.get("text") for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str)]
    if texts and texts[-1].rstrip().endswith("?"):
        return "question"
    return "end_turn"


def parse_transcript(path: Path, *, st: os.stat_result | None = None, home: Path | None = None) -> TranscriptInfo:
    """Walk the JSONL once: first prompt, files touched, tool-call count, last-turn shape."""
    stat = st or path.stat()
    home_str = str(home or Path.home())
    files: set[str] = set()
    tool_calls = 0
    last_assistant: dict[str, Any] | None = None
    answered: set[str] = set()
    last_ts: str | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                for m in _FILE_PATH_LINE_RE.finditer(line):
                    p = m.group(1)
                    if _FILES_NOISE_RE.search(p):
                        continue
                    if p.startswith(home_str + "/"):
                        p = "~/" + p[len(home_str) + 1 :]
                    files.add(p)
                if '"tool_use"' not in line and '"assistant"' not in line and '"user"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                kind = obj.get("type")
                if kind not in ("assistant", "user"):
                    continue
                ts = obj.get("timestamp")
                if isinstance(ts, str) and parse_iso(ts) is not None:
                    last_ts = ts
                blocks = _blocks(obj)
                if kind == "assistant":
                    last_assistant = obj
                    tool_calls += sum(1 for b in blocks if b.get("type") == "tool_use")
                else:
                    for b in blocks:
                        if b.get("type") == "tool_result" and b.get("tool_use_id") is not None:
                            answered.add(str(b["tool_use_id"]))
    except OSError:
        pass
    at = parse_iso(last_ts)
    return TranscriptInfo(
        path=str(path),
        first_prompt=extract_first_message(path),
        files_touched=sorted(files)[:_MAX_FILES_TOUCHED],
        tool_calls=tool_calls,
        last_turn=LastTurn(at=dt_to_iso(at) if at else None, kind=_last_turn_kind(last_assistant, answered)),
        mtime_ns=stat.st_mtime_ns,
    )


# ----- cache -------------------------------------------------------------------


def load_transcript_cache(cache_path: Path) -> dict[str, TranscriptInfo]:
    if not cache_path.exists():
        return {}
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, TranscriptInfo] = {}
    for key, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        try:
            lt = payload.get("last_turn") or {}
            out[key] = TranscriptInfo(
                path=str(payload["path"]),
                first_prompt=str(payload["first_prompt"]),
                files_touched=[str(x) for x in payload.get("files_touched") or []],
                tool_calls=int(payload.get("tool_calls", 0)),
                last_turn=LastTurn(at=lt.get("at"), kind=str(lt.get("kind", "unknown"))),
                mtime_ns=int(payload["mtime_ns"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def write_transcript_cache(cache_path: Path, entries: dict[str, TranscriptInfo]) -> None:
    """Atomically replace the cache file. Best-effort — never raises."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps({k: asdict(v) for k, v in entries.items()}), encoding="utf-8")
        os.replace(tmp, cache_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def transcript_info(path: Path, *, cache: dict[str, TranscriptInfo], home: Path | None = None) -> TranscriptInfo:
    """Cached lookup keyed by path; re-parses only when ``mtime_ns`` changed."""
    st = path.stat()
    prior = cache.get(str(path))
    if prior is not None and prior.mtime_ns == st.st_mtime_ns:
        return prior
    info = parse_transcript(path, st=st, home=home)
    cache[str(path)] = info
    return info


__all__ = [
    "TRANSCRIPT_CACHE_FILENAME",
    "extract_files_touched",
    "extract_first_message",
    "load_transcript_cache",
    "parse_transcript",
    "transcript_info",
    "write_transcript_cache",
]
```

In `scout/scripts/cc_session_cache.py`, delete the bodies of `extract_first_message` and `extract_files_touched` **and** the constants `_HEAD_LINES_FOR_FIRST_MSG`, `_MAX_FILES_TOUCHED`, `_FIRST_MSG_MAX_CHARS`, `_FILES_NOISE_RE`, `_FILE_PATH_LINE_RE`, then add directly under the imports:

```python
from scout.sessions.transcript import extract_files_touched, extract_first_message  # moved (Agent Sessions plan 1)
```

Leave `re` imported only if still used (ruff will tell you); `build_session_entry` keeps calling the two names unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_transcript.py tests/unit/test_cc_session_cache.py -q`
Expected: all PASS (the old extractor tests now exercise the re-exports).

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests
git add scout/sessions/transcript.py scout/scripts/cc_session_cache.py tests/unit/test_sessions_transcript.py
git commit -m "feat(sessions): one-pass transcript facts with last-turn detection and mtime cache

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: PR state via `gh` with TTL cache and fetch cap

**Files:**
- Create: `scout/sessions/github.py`
- Test: `tests/unit/test_sessions_github.py`

**Interfaces:**
- Produces: `Runner = Callable[[list[str]], str | None]` (argv after `gh`; returns stdout or `None` on any failure); `default_runner(argv) -> str|None` (10 s timeout); `gh_available() -> bool`; `GH_FIELDS` constant; `summarize_checks(rollup) -> str`; `pr_info_from_payload(ref: PRRef, payload: dict, fetched_at: str) -> PRInfo`; `unknown_pr_info(ref, *, stale=False) -> PRInfo`; `terminal_pr_info(ref, state) -> PRInfo`; `load_pr_cache(path) -> dict[str, PRInfo]`; `write_pr_cache(path, cache)`; `refresh_pr_states(refs: list[PRRef], *, cache: dict[str, PRInfo], now: datetime, ttl: timedelta, cap: int, runner: Runner) -> tuple[dict[str, PRInfo], list[SourceError], int]` (mapping `repo#number → PRInfo`, errors, fetch count); `PR_CACHE_FILENAME = "sessions-pr.cache.json"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_sessions_github.py
"""Unit tests for scout.sessions.github — never calls the real gh."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout.sessions.desktop import PRRef
from scout.sessions.github import (
    PR_CACHE_FILENAME,
    load_pr_cache,
    pr_info_from_payload,
    refresh_pr_states,
    summarize_checks,
    terminal_pr_info,
    unknown_pr_info,
    write_pr_cache,
)

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
TTL = timedelta(minutes=10)
REF = PRRef(number=98, repo="example-org/example-repo", url="https://github.com/example-org/example-repo/pull/98", legacy_state=None)


def _payload(**over: object) -> str:
    base = {
        "state": "OPEN",
        "isDraft": False,
        "reviewDecision": "CHANGES_REQUESTED",
        "reviewRequests": [],
        "statusCheckRollup": [{"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"}],
        "mergeStateStatus": "CLEAN",
        "updatedAt": "2026-09-08T11:00:00Z",
        "url": REF.url,
    }
    base.update(over)
    return json.dumps(base)


def test_summarize_checks() -> None:
    assert summarize_checks([]) == "none"
    assert summarize_checks([{"status": "COMPLETED", "conclusion": "SUCCESS"}, {"status": "COMPLETED", "conclusion": "SKIPPED"}]) == "passing"
    assert summarize_checks([{"status": "COMPLETED", "conclusion": "SUCCESS"}, {"status": "IN_PROGRESS", "conclusion": None}]) == "pending"
    assert summarize_checks([{"status": "COMPLETED", "conclusion": "FAILURE"}, {"status": "IN_PROGRESS"}]) == "failing"
    # Older gh payloads use `state` instead of status/conclusion.
    assert summarize_checks([{"state": "SUCCESS"}, {"state": "ERROR"}]) == "failing"


def test_pr_info_from_payload_maps_fields() -> None:
    info = pr_info_from_payload(REF, json.loads(_payload(reviewRequests=[{"login": "priya"}])), fetched_at="2026-09-08T12:00:00Z")
    assert info.key == "example-org/example-repo#98"
    assert (info.state, info.is_draft, info.review_decision) == ("OPEN", False, "CHANGES_REQUESTED")
    assert info.review_requested is True and info.checks == "passing" and info.merge_state == "CLEAN"
    assert info.updated_at == "2026-09-08T11:00:00Z" and info.stale is False


def test_refresh_fetches_uncached_and_caches_result() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> str | None:
        calls.append(argv)
        return _payload()

    cache: dict = {}
    out, errors, fetched = refresh_pr_states([REF], cache=cache, now=NOW, ttl=TTL, cap=25, runner=runner)
    assert fetched == 1 and errors == []
    assert calls == [["pr", "view", "98", "--repo", "example-org/example-repo", "--json",
                      "state,isDraft,reviewDecision,reviewRequests,statusCheckRollup,mergeStateStatus,updatedAt,url"]]
    assert out[REF.key].review_decision == "CHANGES_REQUESTED"
    assert cache[REF.key] == out[REF.key]


def test_refresh_respects_ttl_and_never_refetches_terminal() -> None:
    fresh = pr_info_from_payload(REF, json.loads(_payload()), fetched_at="2026-09-08T11:55:00Z")
    merged_ref = PRRef(number=7, repo="example-org/example-repo", url=None, legacy_state=None)
    merged = terminal_pr_info(merged_ref, "MERGED")
    old_ref = PRRef(number=8, repo="example-org/example-repo", url=None, legacy_state=None)
    old = pr_info_from_payload(old_ref, json.loads(_payload()), fetched_at="2026-09-08T09:00:00Z")
    cache = {fresh.key: fresh, merged.key: merged, old.key: old}
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> str | None:
        calls.append(argv)
        return _payload(state="MERGED")

    out, errors, fetched = refresh_pr_states([REF, merged_ref, old_ref], cache=cache, now=NOW, ttl=TTL, cap=25, runner=runner)
    assert fetched == 1 and [c[2] for c in calls] == ["8"]  # only the stale open one
    assert out[REF.key] is fresh and out[merged.key] is merged
    assert out[old.key].state == "MERGED"


def test_refresh_failure_keeps_cached_value_marked_stale_or_unknown() -> None:
    cached = pr_info_from_payload(REF, json.loads(_payload()), fetched_at="2026-09-08T09:00:00Z")
    other = PRRef(number=9, repo="example-org/example-repo", url=None, legacy_state=None)
    cache = {cached.key: cached}
    out, errors, fetched = refresh_pr_states([REF, other], cache=cache, now=NOW, ttl=TTL, cap=25, runner=lambda argv: None)
    assert fetched == 0
    assert out[REF.key].stale is True and out[REF.key].review_decision == "CHANGES_REQUESTED"
    assert out[other.key].state == "unknown" and out[other.key].checks == "unknown"
    assert len(errors) == 2 and all(e.source == "gh" for e in errors)


def test_refresh_honours_cap_oldest_first() -> None:
    refs = [PRRef(number=n, repo="example-org/example-repo", url=None, legacy_state=None) for n in (1, 2, 3)]
    cache = {
        refs[0].key: pr_info_from_payload(refs[0], json.loads(_payload()), fetched_at="2026-09-08T10:00:00Z"),
        refs[1].key: pr_info_from_payload(refs[1], json.loads(_payload()), fetched_at="2026-09-08T08:00:00Z"),
    }  # refs[2] never fetched → oldest of all
    calls: list[str] = []
    out, _, fetched = refresh_pr_states(refs, cache=cache, now=NOW, ttl=TTL, cap=2, runner=lambda a: (calls.append(a[2]), _payload())[1])
    assert fetched == 2 and calls == ["3", "2"]
    assert out[refs[0].key].stale is False  # not refetched, still within-cache value, not marked stale by cap


def test_legacy_terminal_state_seeds_without_gh() -> None:
    legacy = PRRef(number=64, repo="example-org/other", url=None, legacy_state="MERGED")
    out, errors, fetched = refresh_pr_states([legacy], cache={}, now=NOW, ttl=TTL, cap=25, runner=lambda a: None)
    assert fetched == 0 and errors == [] and out[legacy.key].state == "MERGED"


def test_unknown_and_cache_round_trip(tmp_path: Path) -> None:
    info = unknown_pr_info(REF)
    assert info.state == "unknown" and info.fetched_at is None
    path = tmp_path / PR_CACHE_FILENAME
    write_pr_cache(path, {info.key: info})
    assert load_pr_cache(path)[info.key] == info
    assert load_pr_cache(tmp_path / "missing.json") == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_github.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.sessions.github'`

- [ ] **Step 3: Write the module**

```python
# scout/sessions/github.py
"""PR review/CI state through the local ``gh`` CLI (spec §4.6).

Sequential, bounded, cached: at most ``cap`` fetches per run, each with a 10 s
timeout; entries younger than ``ttl`` and terminal states (MERGED/CLOSED) are
never refetched; a failure keeps the cached value flagged ``stale``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scout.sessions.desktop import PRRef
from scout.sessions.model import TERMINAL_PR_STATES, PRInfo, SourceError, dt_to_iso, parse_iso

PR_CACHE_FILENAME = "sessions-pr.cache.json"
GH_FIELDS = "state,isDraft,reviewDecision,reviewRequests,statusCheckRollup,mergeStateStatus,updatedAt,url"
GH_TIMEOUT_SECONDS = 10
MAX_CONSECUTIVE_FAILURES = 3  # after this many gh failures in a row, stop calling gh for the rest of the run

Runner = Callable[[list[str]], str | None]

_FAILING = {"FAILURE", "ERROR", "TIMED_OUT", "STARTUP_FAILURE"}
_PASSING = {"SUCCESS", "SKIPPED", "NEUTRAL"}


def gh_available() -> bool:
    return shutil.which("gh") is not None


def default_runner(argv: list[str]) -> str | None:
    try:
        proc = subprocess.run(["gh", *argv], capture_output=True, text=True, check=False, timeout=GH_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def summarize_checks(rollup: list[dict[str, Any]] | None) -> str:
    if not rollup:
        return "none"
    saw_pending = False
    for c in rollup:
        if not isinstance(c, dict):
            continue
        conclusion = str(c.get("conclusion") or c.get("state") or "").upper()
        status = str(c.get("status") or "").upper()
        if conclusion in _FAILING:
            return "failing"
        if (status and status != "COMPLETED") or (not conclusion and not status):
            saw_pending = True
        elif conclusion and conclusion not in _PASSING and conclusion not in _FAILING:
            saw_pending = True  # e.g. ACTION_REQUIRED, STALE
    return "pending" if saw_pending else "passing"


def pr_info_from_payload(ref: PRRef, payload: dict[str, Any], fetched_at: str) -> PRInfo:
    decision = payload.get("reviewDecision")
    return PRInfo(
        number=ref.number,
        repo=ref.repo,
        url=payload.get("url") or ref.url,
        state=str(payload.get("state") or "unknown").upper(),
        is_draft=bool(payload.get("isDraft", False)),
        review_decision=str(decision) if isinstance(decision, str) else "",
        review_requested=bool(payload.get("reviewRequests")) or decision == "REVIEW_REQUIRED",
        checks=summarize_checks(payload.get("statusCheckRollup")),
        merge_state=str(payload.get("mergeStateStatus") or "unknown").upper(),
        fetched_at=fetched_at,
        stale=False,
        updated_at=payload.get("updatedAt") if isinstance(payload.get("updatedAt"), str) else None,
    )


def unknown_pr_info(ref: PRRef, *, stale: bool = False) -> PRInfo:
    return PRInfo(
        number=ref.number, repo=ref.repo, url=ref.url, state="unknown", is_draft=False, review_decision="unknown",
        review_requested=False, checks="unknown", merge_state="unknown", fetched_at=None, stale=stale, updated_at=None,
    )


def terminal_pr_info(ref: PRRef, state: str) -> PRInfo:
    return PRInfo(
        number=ref.number, repo=ref.repo, url=ref.url, state=state.upper(), is_draft=False, review_decision="",
        review_requested=False, checks="none", merge_state="unknown", fetched_at=None, stale=False, updated_at=None,
    )


def load_pr_cache(cache_path: Path) -> dict[str, PRInfo]:
    if not cache_path.exists():
        return {}
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, PRInfo] = {}
    for key, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        try:
            out[key] = PRInfo(**{k: payload[k] for k in PRInfo.__dataclass_fields__})
        except (KeyError, TypeError):
            continue
    return out


def write_pr_cache(cache_path: Path, cache: dict[str, PRInfo]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps({k: asdict(v) for k, v in cache.items()}), encoding="utf-8")
        os.replace(tmp, cache_path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _fetched_sort_key(info: PRInfo | None) -> float:
    if info is None or info.fetched_at is None:
        return float("-inf")
    dt = parse_iso(info.fetched_at)
    return dt.timestamp() if dt else float("-inf")


def refresh_pr_states(
    refs: list[PRRef],
    *,
    cache: dict[str, PRInfo],
    now: datetime,
    ttl: timedelta,
    cap: int,
    runner: Runner,
) -> tuple[dict[str, PRInfo], list[SourceError], int]:
    """Resolve every ref to a PRInfo, fetching from gh only where the cache is cold.

    Mutates ``cache`` with fresh results so callers can persist it.
    """
    out: dict[str, PRInfo] = {}
    errors: list[SourceError] = []
    pending: list[PRRef] = []
    seen: set[str] = set()
    for ref in refs:
        key = f"{ref.repo}#{ref.number}"
        if key in seen:
            continue
        seen.add(key)
        cached = cache.get(key)
        if cached is not None and cached.state in TERMINAL_PR_STATES:
            out[key] = cached
            continue
        if cached is None and ref.legacy_state and ref.legacy_state.upper() in TERMINAL_PR_STATES:
            out[key] = cache[key] = terminal_pr_info(ref, ref.legacy_state)
            continue
        fetched_dt = parse_iso(cached.fetched_at) if cached else None
        if cached is not None and fetched_dt is not None and now - fetched_dt < ttl:
            out[key] = cached
            continue
        pending.append(ref)

    pending.sort(key=lambda r: _fetched_sort_key(cache.get(f"{r.repo}#{r.number}")))
    attempts = 0  # every gh call counts against the cap, success or not
    fetched = 0  # successful fetches (reported in source_counts)
    consecutive_failures = 0
    for ref in pending:
        key = f"{ref.repo}#{ref.number}"
        cached = cache.get(key)
        if attempts >= cap or consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            # Out of budget, or gh is clearly down (offline / not authed):
            # serve what we have without paying another 10 s timeout.
            out[key] = cached if cached is not None else unknown_pr_info(ref)
            continue
        attempts += 1
        raw = runner(["pr", "view", str(ref.number), "--repo", ref.repo, "--json", GH_FIELDS])
        payload: dict[str, Any] | None = None
        if raw is not None:
            try:
                parsed = json.loads(raw)
                payload = parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                payload = None
        if payload is None:
            consecutive_failures += 1
            errors.append(SourceError(source="gh", message=f"pr view failed: {key}"))
            if cached is not None:
                out[key] = PRInfo(**{**asdict(cached), "stale": True})
            else:
                out[key] = unknown_pr_info(ref, stale=True)
            continue
        consecutive_failures = 0
        fetched += 1
        info = pr_info_from_payload(ref, payload, fetched_at=dt_to_iso(now))
        out[key] = cache[key] = info
    return out, errors, fetched


__all__ = [
    "GH_FIELDS",
    "PR_CACHE_FILENAME",
    "Runner",
    "default_runner",
    "gh_available",
    "load_pr_cache",
    "pr_info_from_payload",
    "refresh_pr_states",
    "summarize_checks",
    "terminal_pr_info",
    "unknown_pr_info",
    "write_pr_cache",
]
```

Semantics: `attempts` (every `gh` call) is what the cap bounds; `fetched` (successes) is what `source_counts.prs_refreshed` reports. Three consecutive failures short-circuit the rest of the run so an offline machine pays at most 30 s, not 250 s.

- [ ] **Step 4: Add the short-circuit test and run everything**

Append to `tests/unit/test_sessions_github.py`:

```python
def test_three_consecutive_failures_stop_calling_gh() -> None:
    refs = [PRRef(number=n, repo="example-org/example-repo", url=None, legacy_state=None) for n in range(1, 7)]
    calls: list[str] = []

    def runner(argv: list[str]) -> str | None:
        calls.append(argv[2])
        return None

    out, errors, fetched = refresh_pr_states(refs, cache={}, now=NOW, ttl=TTL, cap=25, runner=runner)
    assert calls == ["1", "2", "3"]  # stopped after MAX_CONSECUTIVE_FAILURES
    assert fetched == 0 and len(errors) == 3
    assert all(out[f"{r.repo}#{r.number}"].state == "unknown" for r in refs)
```

Run: `.venv/bin/pytest tests/unit/test_sessions_github.py -q`
Expected: 9 passed

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests
git add scout/sessions/github.py tests/unit/test_sessions_github.py
git commit -m "feat(sessions): PR review and CI state via gh with TTL cache and fetch cap

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Derivation — project key, Scout-run flag, PR choice, state rules

**Files:**
- Create: `scout/sessions/derive.py`
- Test: `tests/unit/test_sessions_derive.py`

**Interfaces:**
- Produces: `SCOUT_RUN_TITLE_RE`; `is_scout_run(*, origin_cwd:str, title:str|None, scheduled_task_id:str|None, vault:Path) -> bool`; `strip_worktree(path:str) -> str`; `git_toplevel(path:str) -> str|None` (memoised, 2 s timeout); `resolve_project_key(origin_cwd:str, *, toplevel: Callable[[str], str|None]) -> str`; `choose_pr(prs: list[PRInfo]) -> PRInfo|None`; `fmt_ago(td: timedelta|None) -> str`; `fmt_days(td: timedelta) -> str`; `derive_state(session: AgentSession, *, now: datetime, stale_after: timedelta, running_window: timedelta) -> tuple[str, list[str]]`.
- Consumes: `AgentSession`, `PRInfo`, `parse_iso` from `model`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_sessions_derive.py
"""Table-driven tests for scout.sessions.derive (spec §4.2, §4.3, §4.6 choice, §4.7 rules)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.sessions.derive import (
    choose_pr,
    derive_state,
    fmt_ago,
    is_scout_run,
    resolve_project_key,
    strip_worktree,
)
from scout.sessions.model import AgentSession, LastTurn, PRInfo, TranscriptInfo, WorktreeInfo

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
STALE = timedelta(days=3)
RUNNING = timedelta(seconds=120)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(**over: object) -> PRInfo:
    base = dict(
        number=98, repo="example-org/example-repo", url=None, state="OPEN", is_draft=False, review_decision="",
        review_requested=False, checks="passing", merge_state="CLEAN", fetched_at=_iso(NOW), stale=False,
        updated_at=_iso(NOW - timedelta(days=5)),
    )
    base.update(over)
    return PRInfo(**base)  # type: ignore[arg-type]


def _session(*, last_active: datetime = NOW - timedelta(hours=2), **over: object) -> AgentSession:
    base = dict(
        id="local_x", cli_session_id="u", title="t", title_source="auto", project_key="/r", group_name=None,
        cwd="/r", origin_cwd="/r", worktree=None, created_at=_iso(NOW - timedelta(days=9)),
        last_activity_at=_iso(last_active), model="claude-opus-5", effort="high", turns=3, is_archived=False,
        is_open=False, is_scout_run=False, parent_session_id=None, spawned_task_id=None, scheduled_task_id=None,
        prs=[], pr=None, transcript=None,
    )
    base.update(over)
    return AgentSession(**base)  # type: ignore[arg-type]


def _question_transcript() -> TranscriptInfo:
    return TranscriptInfo(path="p", first_prompt="x", files_touched=[], tool_calls=1,
                          last_turn=LastTurn(at=_iso(NOW), kind="question"), mtime_ns=1)


# ----- project + scout-run -----------------------------------------------------


def test_strip_worktree() -> None:
    assert strip_worktree("/Users/alex/code/repo/.claude/worktrees/w1") == "/Users/alex/code/repo"
    assert strip_worktree("/Users/alex/code/repo/.claude/worktrees/w1/sub") == "/Users/alex/code/repo"
    assert strip_worktree("/Users/alex/code/repo") == "/Users/alex/code/repo"


def test_resolve_project_key_prefers_git_toplevel_then_stripping() -> None:
    assert resolve_project_key("/a/b/.claude/worktrees/w", toplevel=lambda p: "/a/b") == "/a/b"
    assert resolve_project_key("/a/b/.claude/worktrees/w", toplevel=lambda p: None) == "/a/b"
    assert resolve_project_key("/plain", toplevel=lambda p: None) == "/plain"


def test_is_scout_run_needs_vault_cwd_and_a_run_signature(tmp_path: Path) -> None:
    vault = tmp_path / "Scout"
    assert is_scout_run(origin_cwd=str(vault), title="scout-morning-briefing-20260908-1150", scheduled_task_id=None, vault=vault)
    assert is_scout_run(origin_cwd=str(vault), title="Scout research", scheduled_task_id="scout-research", vault=vault)
    assert not is_scout_run(origin_cwd=str(vault), title="Tidy the release notes", scheduled_task_id=None, vault=vault)
    assert not is_scout_run(origin_cwd="/elsewhere", title="scout-dreaming-20260908-1830", scheduled_task_id=None, vault=vault)


# ----- PR choice -------------------------------------------------------------------


def test_choose_pr_prefers_most_recently_updated_open() -> None:
    old_open = _pr(number=1, updated_at=_iso(NOW - timedelta(days=9)))
    new_open = _pr(number=2, updated_at=_iso(NOW - timedelta(days=1)))
    merged = _pr(number=3, state="MERGED", updated_at=_iso(NOW))
    assert choose_pr([old_open, merged, new_open]) is new_open
    assert choose_pr([merged]) is merged
    assert choose_pr([]) is None


# ----- state rules, first match wins -----------------------------------------------


@pytest.mark.parametrize(
    ("session", "expected_state", "expected_reason_fragment"),
    [
        pytest.param(_session(is_archived=True, is_open=True, last_active=NOW), "done", "archived", id="1-archived-wins"),
        pytest.param(_session(is_open=True, last_active=NOW - timedelta(seconds=40)), "running", "active 40s ago", id="2-running"),
        pytest.param(_session(is_open=True, last_active=NOW - timedelta(minutes=12)), "parked", "open, idle 12m", id="2b-open-idle-is-parked"),
        pytest.param(_session(pr=_pr(state="MERGED")), "done", "PR #98 merged", id="3-merged"),
        pytest.param(_session(pr=_pr(state="CLOSED")), "done", "PR #98 closed", id="3-closed"),
        pytest.param(_session(pr=_pr(review_decision="CHANGES_REQUESTED")), "needs_you", "changes requested on PR #98", id="4-changes-requested"),
        pytest.param(_session(pr=_pr(checks="failing", review_requested=True)), "needs_you", "CI failing", id="4-ci-failing"),
        pytest.param(_session(pr=_pr(merge_state="DIRTY", review_requested=True)), "needs_you", "merge conflict", id="4-conflict"),
        pytest.param(_session(transcript=_question_transcript()), "needs_you", "ended on a question", id="4-question"),
        pytest.param(_session(pr=_pr()), "needs_you", "PR #98 ready to merge", id="4-ready-no-review-requested"),
        pytest.param(_session(pr=_pr(review_decision="APPROVED", review_requested=True)), "needs_you", "PR #98 ready to merge", id="4-ready-approved"),
        pytest.param(_session(pr=_pr(review_requested=True)), "waiting", "PR #98 awaiting review 5d", id="5-awaiting-review"),
        pytest.param(_session(pr=_pr(review_decision="REVIEW_REQUIRED", review_requested=True)), "waiting", "awaiting review", id="5-review-required"),
        pytest.param(_session(pr=_pr(checks="pending")), "waiting", "checks pending", id="5-checks-pending"),
        pytest.param(_session(pr=_pr(is_draft=True), last_active=NOW - timedelta(hours=1)), "parked", "draft PR #98", id="draft-falls-through"),
        pytest.param(_session(last_active=NOW - timedelta(days=4)), "stale", "idle 4d", id="6-stale"),
        pytest.param(
            _session(last_active=NOW - timedelta(days=6),
                     worktree=WorktreeInfo(path="/w", name="w", branch="b", source_branch="main", dirty=True)),
            "stale", "dirty worktree, idle 6d", id="6-dirty-worktree",
        ),
        pytest.param(_session(pr=_pr(state="unknown", checks="unknown", merge_state="unknown", review_decision="unknown")),
                     "parked", "PR #98 open (state unknown)", id="7-unknown-pr-parked"),
        pytest.param(_session(), "parked", "last active 2h ago", id="7-parked-closed"),
    ],
)
def test_derive_state_table(session: AgentSession, expected_state: str, expected_reason_fragment: str) -> None:
    state, reasons = derive_state(session, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == expected_state, reasons
    assert any(expected_reason_fragment in r for r in reasons), reasons


def test_needs_you_lists_every_matched_signal() -> None:
    s = _session(pr=_pr(review_decision="CHANGES_REQUESTED", checks="failing"), transcript=_question_transcript())
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "needs_you"
    assert reasons == ["changes requested on PR #98", "CI failing", "ended on a question"]


def test_waiting_beats_stale_for_an_old_pr() -> None:
    s = _session(pr=_pr(review_requested=True), last_active=NOW - timedelta(days=10))
    assert derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)[0] == "waiting"


def test_fmt_ago() -> None:
    assert fmt_ago(timedelta(seconds=40)) == "40s ago"
    assert fmt_ago(timedelta(minutes=12)) == "12m ago"
    assert fmt_ago(timedelta(hours=3, minutes=5)) == "3h ago"
    assert fmt_ago(timedelta(days=4, hours=2)) == "4d ago"
    assert fmt_ago(None) == "unknown"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_derive.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.sessions.derive'`

- [ ] **Step 3: Write the module**

```python
# scout/sessions/derive.py
"""Pure derivations: project key, Scout-run flag, PR choice, state (spec §4.2, §4.3, §4.7)."""

from __future__ import annotations

import functools
import re
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from scout.sessions.model import TERMINAL_PR_STATES, AgentSession, PRInfo, parse_iso

SCOUT_RUN_TITLE_RE = re.compile(r"^scout-[a-z-]+-\d{8}-\d{4}$")
_WORKTREE_RE = re.compile(r"^(.*?)/\.claude/worktrees/[^/]+(?:/.*)?$")


# ----- project ------------------------------------------------------------------


def strip_worktree(path: str) -> str:
    m = _WORKTREE_RE.match(path)
    return m.group(1) if m else path


@functools.lru_cache(maxsize=256)
def git_toplevel(path: str) -> str | None:
    """`git rev-parse --show-toplevel` for *path*, memoised per path; None when not a repo."""
    if not Path(path).is_dir():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    top = proc.stdout.strip()
    return top or None


def resolve_project_key(origin_cwd: str, *, toplevel: Callable[[str], str | None]) -> str:
    top = toplevel(origin_cwd)
    if top:
        return top
    return strip_worktree(origin_cwd)


def is_scout_run(*, origin_cwd: str, title: str | None, scheduled_task_id: str | None, vault: Path) -> bool:
    try:
        in_vault = Path(origin_cwd).resolve() == vault.resolve()
    except OSError:
        in_vault = origin_cwd.rstrip("/") == str(vault).rstrip("/")
    if not in_vault:
        return False
    if scheduled_task_id and scheduled_task_id.startswith("scout-"):
        return True
    return bool(title and SCOUT_RUN_TITLE_RE.match(title))


# ----- PR choice -------------------------------------------------------------------


def _updated_key(pr: PRInfo) -> float:
    dt = parse_iso(pr.updated_at) or parse_iso(pr.fetched_at)
    return dt.timestamp() if dt else float("-inf")


def choose_pr(prs: list[PRInfo]) -> PRInfo | None:
    """The most recently updated OPEN PR; else the most recently updated of any; else None."""
    if not prs:
        return None
    open_prs = [p for p in prs if p.state == "OPEN"]
    pool = open_prs or prs
    return max(pool, key=_updated_key)


# ----- formatting ------------------------------------------------------------------


def fmt_ago(td: timedelta | None) -> str:
    if td is None:
        return "unknown"
    s = int(td.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def fmt_days(td: timedelta) -> str:
    return f"{int(td.total_seconds()) // 86400}d"


# ----- state -------------------------------------------------------------------------


def derive_state(
    session: AgentSession, *, now: datetime, stale_after: timedelta, running_window: timedelta
) -> tuple[str, list[str]]:
    """Spec §4.7, first match wins. ``reasons`` lists every matched signal of the winning rule."""
    last = parse_iso(session.last_activity_at)
    idle = (now - last) if last else None
    pr = session.pr

    if session.is_archived:  # 1
        return "done", ["archived"]

    if session.is_open and idle is not None and idle <= running_window:  # 2
        return "running", [f"active {fmt_ago(idle)}"]

    if pr is not None and pr.state in TERMINAL_PR_STATES:  # 3
        return "done", [f"PR #{pr.number} {pr.state.lower()}"]

    needs: list[str] = []  # 4
    live_pr = pr is not None and pr.state == "OPEN" and not pr.is_draft
    if live_pr:
        assert pr is not None
        if pr.review_decision == "CHANGES_REQUESTED":
            needs.append(f"changes requested on PR #{pr.number}")
        if pr.checks == "failing":
            needs.append("CI failing")
        if pr.merge_state == "DIRTY":
            needs.append("merge conflict")
        ready_review = pr.review_decision == "APPROVED" or (pr.review_decision == "" and not pr.review_requested)
        if pr.checks in ("passing", "none") and pr.merge_state == "CLEAN" and ready_review:
            needs.append(f"PR #{pr.number} ready to merge")
    if session.transcript is not None and session.transcript.last_turn.kind == "question":
        needs.append("ended on a question")
    if needs:
        return "needs_you", needs

    if live_pr:  # 5
        assert pr is not None
        waiting: list[str] = []
        if pr.review_requested or pr.review_decision == "REVIEW_REQUIRED":
            age = parse_iso(pr.updated_at)
            suffix = f" {fmt_days(now - age)}" if age else ""
            waiting.append(f"PR #{pr.number} awaiting review{suffix}")
        if pr.checks == "pending":
            waiting.append("checks pending")
        if waiting:
            return "waiting", waiting

    extra: list[str] = []
    if pr is not None and pr.is_draft:
        extra.append(f"draft PR #{pr.number}")
    elif pr is not None and pr.state not in TERMINAL_PR_STATES and pr.state != "OPEN":
        extra.append(f"PR #{pr.number} open (state unknown)")
    elif live_pr:
        assert pr is not None
        extra.append(f"PR #{pr.number} open")

    if idle is not None and idle > stale_after:  # 6
        if session.worktree is not None and session.worktree.dirty:
            return "stale", [f"dirty worktree, idle {fmt_days(idle)}", *extra]
        return "stale", [f"idle {fmt_days(idle)}", *extra]

    if session.is_open:  # 7
        return "parked", [f"open, idle {fmt_ago(idle)}", *extra]
    return "parked", [f"last active {fmt_ago(idle)}", *extra]


__all__ = [
    "SCOUT_RUN_TITLE_RE",
    "choose_pr",
    "derive_state",
    "fmt_ago",
    "fmt_days",
    "git_toplevel",
    "is_scout_run",
    "resolve_project_key",
    "strip_worktree",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_derive.py -q`
Expected: 26 passed (7 plain tests + the 19-case table)

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests
git add scout/sessions/derive.py tests/unit/test_sessions_derive.py
git commit -m "feat(sessions): project resolution, Scout-run flag and state derivation rules

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Index orchestrator — merge, derive, write atomically

**Files:**
- Create: `scout/sessions/index.py`
- Test: `tests/unit/test_sessions_index.py`

**Interfaces:**
- Produces: `INDEX_FILENAME = "sessions-index.json"`; dataclass `BuildOptions(data_dir, settings, claude_home, support_dir, now, use_gh=True, gh_runner=github.default_runner, gh_available=github.gh_available, toplevel=git_toplevel, pid_alive=cli_home.pid_alive)`; `default_options(data_dir=None, *, settings=None, now=None, use_gh=True) -> BuildOptions`; `index_path(data_dir) -> Path`; `build_index(opts) -> Index`; `write_index(index, path) -> None` (atomic, raises `OSError`); `run(*, data_dir=None, use_gh=True, now=None, opts=None) -> tuple[Index, Path]` (builds + writes the index and both caches).
- Consumes: everything from Tasks 1–7 by the names listed in their Interfaces blocks.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_sessions_index.py
"""Integration-style unit tests for scout.sessions.index against a fake HOME tree."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.sessions.index import INDEX_FILENAME, BuildOptions, build_index, index_path, run, write_index
from scout.sessions.model import Index, dt_to_iso
from scout.sessions.settings import AgentSessionsSettings
from tests.unit.sessions_helpers import (
    claude_home,
    support_dir,
    write_desktop_config,
    write_desktop_record,
    write_pid_file,
    write_transcript,
)

# Transcript mtimes come from the real clock (write_transcript), so the fixed
# "now" must be the real clock too — otherwise a test run weeks later would see
# every transcript as newer than the desktop records.
NOW = datetime.now(tz=UTC).replace(microsecond=0)
MS = int(NOW.timestamp() * 1000)
UA = "aaaaaaaa-0000-0000-0000-000000000000"
UB = "bbbbbbbb-0000-0000-0000-000000000000"
UC = "cccccccc-0000-0000-0000-000000000000"
UE = "eeeeeeee-0000-0000-0000-000000000000"
UF = "ffffffff-0000-0000-0000-000000000000"
UG = "99999999-0000-0000-0000-000000000000"
REPO_DIR = "-Users-alex-code-example-repo"


def _user(text: str, ts: str) -> dict:
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _assistant_text(text: str, ts: str) -> dict:
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _gh(argv: list[str]) -> str | None:
    number = argv[2]
    if number == "98":
        return json.dumps({"state": "OPEN", "isDraft": False, "reviewDecision": "CHANGES_REQUESTED", "reviewRequests": [],
                           "statusCheckRollup": [], "mergeStateStatus": "CLEAN", "updatedAt": "2026-09-08T11:00:00Z"})
    return None


def _world(fake_data_dir: Path, *, gh_ok: bool = True, use_gh: bool = True) -> BuildOptions:
    s, h = support_dir(), claude_home()
    # A: open PR with changes requested, live process, transcript ended on a question.
    write_desktop_record(s, "local_A", cliSessionId=UA, lastActivityAt=MS - 3_600_000,
                         prs=[{"prNumber": 98, "repo": "example-org/example-repo", "url": "https://github.com/example-org/example-repo/pull/98"}],
                         cwd="/Users/alex/code/example-repo/.claude/worktrees/w1", worktreePath="/Users/alex/code/example-repo/.claude/worktrees/w1",
                         worktreeName="w1", branch="claude/w1", sourceBranch="main")
    write_transcript(h, REPO_DIR + "--claude-worktrees-w1", UA,
                     [_user("fix it", "2026-09-08T10:00:00.000Z"), _assistant_text("Which file?", "2026-09-08T10:00:05.000Z")], mtime_ago_hours=2)
    write_pid_file(h, 4242, UA, "/Users/alex/code/example-repo/.claude/worktrees/w1")
    # B: spawned child of A, in the Archived group.
    write_desktop_record(s, "local_B", cliSessionId=UB, spawnedFrom={"sessionId": "local_A", "taskId": "task_9"}, lastActivityAt=MS - 7_200_000)
    # C: transcript unavailable, legacy merged PR.
    write_desktop_record(s, "local_C", cliSessionId=UC, transcriptUnavailable=True, prNumber=64, prRepository="example-org/example-repo",
                         prState="MERGED", lastActivityAt=MS - 86_400_000)
    # D: fork sharing A's cli id, older — must be deduped away.
    write_desktop_record(s, "local_D", cliSessionId=UA, title="older fork", lastActivityAt=MS - 90_000_000)
    # Groups: A → "Example Repo", B → "Archived".
    write_desktop_config(s, {"cg-1": "Example Repo", "cg-arch": "Archived"}, {"local_A": "cg-1", "local_B": "cg-arch"})
    # E: CLI-only session, recent.
    write_transcript(h, "-Users-alex-code-other", UE, [_user("cli only work", "2026-09-08T09:00:00.000Z")], mtime_ago_hours=3)
    # F: Scout's own scheduled run inside the vault (custom-title first line).
    # Claude Code encodes a cwd by replacing both "/" and "." with "-".
    vault_dir = re.sub(r"[/.]", "-", str(fake_data_dir))
    write_transcript(h, vault_dir, UF, [{"type": "custom-title", "customTitle": "scout-morning-briefing-20260908-1150", "sessionId": UF},
                                        _user("You are Scout…", "2026-09-08T11:50:00.000Z")], mtime_ago_hours=0.2)
    # G: CLI-only but 20 days old → outside transcript window, excluded.
    write_transcript(h, "-Users-alex-code-old", UG, [_user("ancient", "2026-08-19T09:00:00.000Z")], mtime_ago_hours=20 * 24)
    return BuildOptions(
        data_dir=fake_data_dir, settings=AgentSessionsSettings(), claude_home=h, support_dir=s, now=NOW,
        use_gh=use_gh, gh_runner=_gh, gh_available=lambda: gh_ok, toplevel=lambda p: None, pid_alive=lambda pid: pid == 4242,
    )


def test_build_index_merges_sources_and_derives_state(fake_data_dir: Path) -> None:
    idx = build_index(_world(fake_data_dir))
    by_id = {s.id: s for s in idx.sessions}
    assert set(by_id) == {"local_A", "local_B", "local_C", f"cli:{UE}", f"cli:{UF}"}  # D deduped, G too old

    a = by_id["local_A"]
    assert a.is_open is True and a.state == "needs_you"
    assert a.state_reasons == ["changes requested on PR #98", "ended on a question"]
    assert a.project_key == "/Users/alex/code/example-repo" and a.group_name == "Example Repo"
    assert a.worktree is not None and a.worktree.name == "w1" and a.transcript is not None
    assert a.last_activity_at == dt_to_iso(NOW - timedelta(hours=1))  # desktop lastActivityAt beats the older transcript mtime

    b = by_id["local_B"]
    assert b.parent_session_id == "local_A" and b.is_archived is True and b.state == "done"

    c = by_id["local_C"]
    assert c.transcript is None and c.pr is not None and c.pr.state == "MERGED" and c.state == "done"

    e = by_id[f"cli:{UE}"]
    assert e.title is None and e.origin_cwd == "/Users/alex/code/other" and e.transcript is not None
    assert e.transcript.first_prompt == "cli only work" and e.state == "parked"

    f = by_id[f"cli:{UF}"]
    assert f.origin_cwd == str(fake_data_dir)  # matched by encoded name against the vault, not lossily decoded
    assert f.is_scout_run is True and f.title == "scout-morning-briefing-20260908-1150"

    assert idx.source_errors == []
    assert idx.source_counts["desktop"] == 4 and idx.source_counts["cli_only"] == 2
    assert idx.source_counts["open"] == 1 and idx.source_counts["running"] == 0 and idx.source_counts["prs_refreshed"] == 1
    assert idx.display == {"done_visible_hours": 24, "stale_after_days": 3}

    projects = {p.key: p for p in idx.projects}
    repo = projects["/Users/alex/code/example-repo"]
    assert repo.name == "Example Repo" and repo.group_id == "cg-1"
    assert repo.counts["needs_you"] == 1 and repo.counts["done"] == 2
    assert projects["/Users/alex/code/other"].name == "other" and projects["/Users/alex/code/other"].group_id is None

    # Sorted by severity then recency: needs_you first, done last.
    assert idx.sessions[0].id == "local_A" and idx.sessions[-1].state == "done"


def test_gh_missing_marks_prs_unknown_with_one_error(fake_data_dir: Path) -> None:
    idx = build_index(_world(fake_data_dir, gh_ok=False))
    a = next(s for s in idx.sessions if s.id == "local_A")
    assert a.pr is not None and a.pr.state == "unknown"
    assert a.state == "needs_you" and a.state_reasons == ["ended on a question"]  # question still detected
    assert [e.source for e in idx.source_errors] == ["gh"] and "not found" in idx.source_errors[0].message
    assert idx.source_counts["prs_refreshed"] == 0


def test_use_gh_false_is_silent(fake_data_dir: Path) -> None:
    idx = build_index(_world(fake_data_dir, use_gh=False))
    assert idx.source_errors == []


def test_malformed_desktop_record_is_a_source_error_not_a_crash(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    (support_dir() / "claude-code-sessions" / "org-0000" / "user-0000" / "local_bad.json").write_text("{", encoding="utf-8")
    idx = build_index(opts)
    assert any(e.source == "desktop" and "local_bad.json" in e.message for e in idx.source_errors)
    assert len(idx.sessions) == 5


def test_run_writes_index_and_caches_atomically(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    idx, path = run(opts=opts)
    assert path == index_path(fake_data_dir) == fake_data_dir / ".scout-cache" / INDEX_FILENAME
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert list(on_disk)[0] == "schema_version" and on_disk["schema_version"] == 1
    assert len(on_disk["sessions"]) == len(idx.sessions)
    assert not list((fake_data_dir / ".scout-cache").glob("*.tmp"))
    assert (fake_data_dir / ".scout-cache" / "sessions-transcripts.cache.json").exists()
    assert (fake_data_dir / ".scout-cache" / "sessions-pr.cache.json").exists()


def test_run_removes_legacy_transcript_cache(fake_data_dir: Path) -> None:
    legacy = fake_data_dir / ".scout-cache" / "cc-sessions.cache.json"
    legacy.write_text("{}", encoding="utf-8")
    run(opts=_world(fake_data_dir))
    assert not legacy.exists()


def test_second_run_reuses_pr_cache_within_ttl(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    run(opts=opts)
    calls: list[list[str]] = []

    def counting(argv: list[str]) -> str | None:
        calls.append(argv)
        return _gh(argv)

    opts.gh_runner = counting
    opts.now = NOW + timedelta(minutes=5)
    idx, _ = run(opts=opts)
    assert calls == [] and idx.source_counts["prs_refreshed"] == 0


def test_write_index_raises_when_directory_is_a_file(tmp_path: Path) -> None:
    blocker = tmp_path / "cache"
    blocker.write_text("not a dir", encoding="utf-8")
    idx = Index(generated_at="x", source_counts={}, source_errors=[], display={}, projects=[], sessions=[])
    with pytest.raises(OSError):
        write_index(idx, blocker / INDEX_FILENAME)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_index.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.sessions.index'`

- [ ] **Step 3: Write the orchestrator**

```python
# scout/sessions/index.py
"""Build and write the session index (spec §3, §4.1–§4.4, §4.8, §4.12).

Every loader is independent; a source that is missing is silent, a source
that is malformed contributes a ``source_error`` and the run continues. The
index and both caches are written atomically.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout import paths
from scout.sessions import cli_home, desktop, github
from scout.sessions.derive import choose_pr, derive_state, git_toplevel, is_scout_run, resolve_project_key
from scout.sessions.model import (
    STATES,
    AgentSession,
    Index,
    PRInfo,
    Project,
    SourceError,
    WorktreeInfo,
    dt_to_iso,
    ms_to_iso,
    ns_to_iso,
    now_utc,
    parse_iso,
)
from scout.sessions.settings import AgentSessionsSettings, load_settings
from scout.sessions.transcript import (
    TRANSCRIPT_CACHE_FILENAME,
    load_transcript_cache,
    transcript_info,
    write_transcript_cache,
)

INDEX_FILENAME = "sessions-index.json"
LEGACY_CACHE_FILENAME = "cc-sessions.cache.json"  # pre-plan-1 cache; deleted on first run
_CUSTOM_TITLE_HEAD_LINES = 5


@dataclass
class BuildOptions:
    data_dir: Path
    settings: AgentSessionsSettings
    claude_home: Path
    support_dir: Path
    now: datetime
    use_gh: bool = True
    gh_runner: github.Runner = github.default_runner
    gh_available: Callable[[], bool] = github.gh_available
    toplevel: Callable[[str], str | None] = git_toplevel
    pid_alive: Callable[[int], bool] = cli_home.pid_alive


def default_options(
    data_dir: Path | None = None,
    *,
    settings: AgentSessionsSettings | None = None,
    now: datetime | None = None,
    use_gh: bool = True,
) -> BuildOptions:
    d = data_dir or paths.data_dir()
    s = settings or load_settings(d)
    home = Path(s.claude_home).expanduser() if s.claude_home else cli_home.default_claude_home()
    support = Path(s.desktop_support_dir).expanduser() if s.desktop_support_dir else desktop.default_support_dir()
    return BuildOptions(data_dir=d, settings=s, claude_home=home, support_dir=support, now=now or now_utc(), use_gh=use_gh and s.use_gh)


def index_path(data_dir: Path | None = None) -> Path:
    return paths.cache_dir(data_dir) / INDEX_FILENAME


# ----- session construction -----------------------------------------------------------


def _worktree(rec: desktop.DesktopRecord, lease: desktop.WorktreeLease | None) -> WorktreeInfo | None:
    path = rec.worktree_path or (lease.path if lease else None)
    branch = rec.branch or (lease.branch if lease else None)
    if path is None and branch is None:
        return None
    return WorktreeInfo(
        path=path,
        name=rec.worktree_name or (Path(path).name if path else None),
        branch=branch,
        source_branch=rec.source_branch or (lease.source_branch if lease else None),
        dirty=rec.kept_dirty_worktree,
    )


def _session_from_record(
    rec: desktop.DesktopRecord, *, groups: desktop.Groups, leases: dict[str, desktop.WorktreeLease]
) -> AgentSession:
    group_id = groups.assignments.get(rec.session_id)
    group_name = groups.names.get(group_id) if group_id else None
    return AgentSession(
        id=rec.session_id,
        cli_session_id=rec.cli_session_id,
        title=rec.title,
        title_source=rec.title_source,
        project_key=rec.origin_cwd,  # refined in build_index
        group_name=group_name,
        cwd=rec.cwd,
        origin_cwd=rec.origin_cwd or rec.cwd,
        worktree=_worktree(rec, leases.get(rec.session_id)),
        created_at=ms_to_iso(rec.created_at_ms),
        last_activity_at=ms_to_iso(rec.last_activity_at_ms),
        model=rec.model,
        effort=rec.effort,
        turns=rec.completed_turns,
        is_archived=rec.is_archived or group_name == "Archived",
        is_open=False,
        is_scout_run=False,
        parent_session_id=rec.parent_session_id,
        spawned_task_id=rec.spawned_task_id,
        scheduled_task_id=rec.scheduled_task_id,
        prs=[],
        pr=None,
        transcript=None,
    )


def _custom_title(path: Path) -> str | None:
    """Scout's ``claude -p`` runs write a ``custom-title`` row first; read only the head."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= _CUSTOM_TITLE_HEAD_LINES:
                    break
                if '"custom-title"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                title = obj.get("customTitle") if isinstance(obj, dict) else None
                if isinstance(title, str) and title:
                    return title
    except OSError:
        return None
    return None


def _encode_dirname(path: str) -> str:
    """Claude Code's project-dir encoding: every ``/`` and ``.`` becomes ``-``."""
    return re.sub(r"[/.]", "-", path)


def _cli_only_session(uuid: str, path: Path, st: os.stat_result, known_dirs: dict[str, str]) -> AgentSession:
    # Exact match against directories we know (the vault, every desktop cwd) beats
    # the lossy decode — hyphens and dots in real folder names round-trip only this way.
    cwd = known_dirs.get(path.parent.name) or cli_home.project_path_from_dirname(path.parent.name)
    title = _custom_title(path)
    return AgentSession(
        id=f"cli:{uuid}",
        cli_session_id=uuid,
        title=title,
        title_source="custom" if title else None,
        project_key=cwd,
        group_name=None,
        cwd=cwd,
        origin_cwd=cwd,
        worktree=None,
        created_at=None,
        last_activity_at=ns_to_iso(st.st_mtime_ns),
        model=None,
        effort=None,
        turns=None,
        is_archived=False,
        is_open=False,
        is_scout_run=False,
        parent_session_id=None,
        spawned_task_id=None,
        scheduled_task_id=None,
        prs=[],
        pr=None,
        transcript=None,
    )


def _projects(sessions: list[AgentSession], groups: desktop.Groups) -> list[Project]:
    name_to_id = {name: gid for gid, name in groups.names.items()}
    buckets: dict[str, list[AgentSession]] = {}
    for s in sessions:
        buckets.setdefault(s.project_key, []).append(s)
    out: list[Project] = []
    for key, members in buckets.items():
        named = Counter(m.group_name for m in members if m.group_name and m.group_name != "Archived")
        name = named.most_common(1)[0][0] if named else (Path(key).name or key)
        counts = {state: 0 for state in STATES}
        for m in members:
            counts[m.state] = counts.get(m.state, 0) + 1
        out.append(Project(key=key, name=name, group_id=name_to_id.get(name), counts=counts))
    out.sort(key=lambda p: p.name.lower())
    return out


def _recency(s: AgentSession) -> float:
    dt = parse_iso(s.last_activity_at)
    return dt.timestamp() if dt else 0.0


# ----- build ---------------------------------------------------------------------------


def build_index(opts: BuildOptions) -> Index:
    s = opts.settings
    errors: list[SourceError] = []
    records, e1 = desktop.load_desktop_records(opts.support_dir)
    groups, e2 = desktop.load_groups(opts.support_dir)
    leases, e3 = desktop.load_worktree_leases(opts.support_dir)
    live, e4 = cli_home.load_live_processes(opts.claude_home, is_alive=opts.pid_alive)
    errors.extend([*e1, *e2, *e3, *e4])
    tpaths = cli_home.transcript_paths(opts.claude_home)
    cache_dir = paths.cache_dir(opts.data_dir)
    tcache = load_transcript_cache(cache_dir / TRANSCRIPT_CACHE_FILENAME)
    window = timedelta(days=s.transcript_window_days)

    # 1. Desktop records → sessions; forks sharing a cliSessionId dedupe to the most recent.
    by_cli: dict[str, desktop.DesktopRecord] = {}
    without_cli: list[desktop.DesktopRecord] = []
    for rec in records:
        if rec.cli_session_id is None:
            without_cli.append(rec)
            continue
        prev = by_cli.get(rec.cli_session_id)
        if prev is None or (rec.last_activity_at_ms or 0) > (prev.last_activity_at_ms or 0):
            by_cli[rec.cli_session_id] = rec
    sessions: list[AgentSession] = []
    refs_by_session: dict[str, list[desktop.PRRef]] = {}
    no_transcript: set[str] = set()
    for rec in [*by_cli.values(), *without_cli]:
        sess = _session_from_record(rec, groups=groups, leases=leases)
        sessions.append(sess)
        refs_by_session[sess.id] = list(rec.prs)
        if rec.transcript_unavailable:
            no_transcript.add(sess.id)

    # 2. CLI-only sessions (transcript, no desktop record) within the transcript window.
    known_dirs = {_encode_dirname(str(opts.data_dir)): str(opts.data_dir)}
    for rec in records:
        for p in (rec.cwd, rec.origin_cwd):
            if p:
                known_dirs.setdefault(_encode_dirname(p), p)
    cli_only = 0
    for uuid, path in tpaths.items():
        if uuid in by_cli:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        if opts.now - datetime.fromtimestamp(st.st_mtime_ns / 1e9, tz=UTC) > window:
            continue
        sessions.append(_cli_only_session(uuid, path, st, known_dirs))
        cli_only += 1

    # 3. Transcript facts, liveness, last activity.
    for sess in sessions:
        uuid = sess.cli_session_id
        path = tpaths.get(uuid) if uuid else None
        if path is not None:
            try:
                st = path.stat()
            except OSError:
                st = None
            if st is not None:
                mtime_iso = ns_to_iso(st.st_mtime_ns)
                if sess.last_activity_at is None or mtime_iso > sess.last_activity_at:
                    sess.last_activity_at = mtime_iso
                last = parse_iso(sess.last_activity_at)
                if sess.id not in no_transcript and last is not None and opts.now - last <= window:
                    try:
                        sess.transcript = transcript_info(path, cache=tcache)
                    except OSError as exc:
                        errors.append(SourceError(source="transcript", message=f"{path.name}: {exc}"))
        if uuid is not None and uuid in live:
            sess.is_open = True

    # 4. PR state.
    all_refs = [ref for refs in refs_by_session.values() for ref in refs]
    pr_cache = github.load_pr_cache(cache_dir / github.PR_CACHE_FILENAME)
    fetched = 0
    resolved: dict[str, PRInfo] = {}
    if all_refs:
        if opts.use_gh and opts.gh_available():
            resolved, e5, fetched = github.refresh_pr_states(
                all_refs, cache=pr_cache, now=opts.now, ttl=timedelta(minutes=s.pr_refresh_minutes),
                cap=s.pr_fetch_cap, runner=opts.gh_runner,
            )
            errors.extend(e5)
        else:
            if opts.use_gh:
                errors.append(SourceError(source="gh", message="gh not found on PATH — PR states unknown"))
            # cap=0: serve cached / legacy-terminal / unknown without calling anything.
            resolved, _, fetched = github.refresh_pr_states(
                all_refs, cache=pr_cache, now=opts.now, ttl=timedelta(days=36500), cap=0, runner=lambda argv: None
            )
    for sess in sessions:
        sess.prs = [resolved[f"{r.repo}#{r.number}"] for r in refs_by_session.get(sess.id, []) if f"{r.repo}#{r.number}" in resolved]
        sess.pr = choose_pr(sess.prs)

    # 5. Project key, Scout-run flag, state.
    stale_after = timedelta(days=s.stale_after_days)
    running_window = timedelta(seconds=s.running_window_seconds)
    for sess in sessions:
        sess.project_key = resolve_project_key(sess.origin_cwd, toplevel=opts.toplevel)
        sess.is_scout_run = is_scout_run(
            origin_cwd=sess.origin_cwd, title=sess.title, scheduled_task_id=sess.scheduled_task_id, vault=opts.data_dir
        )
        sess.state, sess.state_reasons = derive_state(sess, now=opts.now, stale_after=stale_after, running_window=running_window)

    sessions.sort(key=lambda x: (STATES.index(x.state), -_recency(x)))
    write_transcript_cache(cache_dir / TRANSCRIPT_CACHE_FILENAME, tcache)
    github.write_pr_cache(cache_dir / github.PR_CACHE_FILENAME, pr_cache)

    return Index(
        generated_at=dt_to_iso(opts.now),
        source_counts={
            "desktop": len(records),
            "cli_only": cli_only,
            "open": sum(1 for x in sessions if x.is_open),
            "running": sum(1 for x in sessions if x.state == "running"),
            "prs_refreshed": fetched,
        },
        source_errors=errors,
        display={"done_visible_hours": s.done_visible_hours, "stale_after_days": s.stale_after_days},
        projects=_projects(sessions, groups),
        sessions=sessions,
    )


# ----- write / run -----------------------------------------------------------------------


def write_index(index: Index, path: Path) -> None:
    """Atomic replace. Raises OSError when the target cannot be written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sessions-index.", suffix=".json.tmp", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(index.to_dict(), f, indent=1)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def run(
    *,
    data_dir: Path | None = None,
    use_gh: bool = True,
    now: datetime | None = None,
    opts: BuildOptions | None = None,
) -> tuple[Index, Path]:
    """Build the index and write it (plus both caches). Returns ``(index, path)``."""
    o = opts or default_options(data_dir, now=now, use_gh=use_gh)
    index = build_index(o)
    legacy = paths.cache_dir(o.data_dir) / LEGACY_CACHE_FILENAME
    if legacy.exists():
        try:
            legacy.unlink()
        except OSError:
            pass
    path = index_path(o.data_dir)
    write_index(index, path)
    return index, path


__all__ = [
    "INDEX_FILENAME",
    "BuildOptions",
    "build_index",
    "default_options",
    "index_path",
    "run",
    "write_index",
]
```

Note: `test_write_index_raises_when_directory_is_a_file` relies on `path.parent.mkdir` raising `FileExistsError` (an `OSError`) because the parent is a regular file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_index.py -q`
Expected: 8 passed

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests
git add scout/sessions/index.py tests/unit/test_sessions_index.py
git commit -m "feat(sessions): index orchestrator merging desktop, transcripts, PIDs and gh into sessions-index.json

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: The state-first digest (`cc-sessions.md`)

**Files:**
- Create: `scout/sessions/render.py`
- Modify: `scout/sessions/index.py` (`run()` gains `render`, `hours`, `instance_name`, `tz_name`; writes the digest)
- Create: `tests/fixtures/sessions/digest-golden.md`
- Test: `tests/unit/test_sessions_render.py`

**Interfaces:**
- Produces: `DIGEST_FILENAME = "cc-sessions.md"`; `render_digest(index: Index, *, now: datetime, tz: ZoneInfo, hours: int, instance_name: str, max_per_bucket: int) -> str`; `run(..., render: bool = False, hours: int = 24, instance_name: str = "Scout", tz_name: str | None = None)` now returns `(index, index_path)` and, when `render` is true, also writes `paths.cache_dir()/cc-sessions.md`.
- Digest shape (spec §4.9): header line with counts; sections **Needs you**, **Running now**, **Waiting on others**, **Stale** (each capped, most recent first, Scout runs and archived excluded); then **Activity — last {hours}h, by project** with today's per-session format (first prompt, files touched). Empty sections print `_none_`.

- [ ] **Step 1: Write the failing test and the golden file**

```python
# tests/unit/test_sessions_render.py
"""Golden test for the cc-sessions.md digest."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scout.sessions.model import AgentSession, Index, LastTurn, PRInfo, Project, TranscriptInfo
from scout.sessions.render import DIGEST_FILENAME, render_digest

GOLDEN = Path(__file__).parent.parent / "fixtures" / "sessions" / "digest-golden.md"
NOW = datetime(2026, 9, 8, 16, 0, tzinfo=UTC)  # 12:00 EDT


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _s(id_: str, title: str, state: str, reasons: list[str], *, hours_ago: float, project: str = "/Users/alex/code/example-repo",
       group: str | None = "Example Repo", pr: PRInfo | None = None, prompt: str | None = None, files: list[str] | None = None,
       scout: bool = False, archived: bool = False) -> AgentSession:
    tr = None
    if prompt is not None:
        tr = TranscriptInfo(path="p", first_prompt=prompt, files_touched=files or [], tool_calls=1,
                            last_turn=LastTurn(at=_iso(NOW), kind="end_turn"), mtime_ns=1)
    return AgentSession(
        id=id_, cli_session_id="u", title=title, title_source="auto", project_key=project, group_name=group, cwd=project,
        origin_cwd=project, worktree=None, created_at=None, last_activity_at=_iso(NOW - timedelta(hours=hours_ago)),
        model="claude-opus-5", effort="high", turns=2, is_archived=archived, is_open=state == "running", is_scout_run=scout,
        parent_session_id=None, spawned_task_id=None, scheduled_task_id=None, prs=[pr] if pr else [], pr=pr,
        transcript=tr, state=state, state_reasons=reasons,
    )


def _pr(number: int, **over: object) -> PRInfo:
    base = dict(number=number, repo="example-org/example-repo", url=f"https://github.com/example-org/example-repo/pull/{number}",
                state="OPEN", is_draft=False, review_decision="", review_requested=True, checks="passing", merge_state="CLEAN",
                fetched_at=_iso(NOW), stale=False, updated_at=_iso(NOW - timedelta(days=5)))
    base.update(over)
    return PRInfo(**base)  # type: ignore[arg-type]


def _index() -> Index:
    sessions = [
        _s("a", "Fix the parser", "needs_you", ["changes requested on PR #98"], hours_ago=1,
           pr=_pr(98, review_decision="CHANGES_REQUESTED", review_requested=False), prompt="please fix the parser", files=["~/code/example-repo/a.py"]),
        _s("b", "Ship the cache", "running", ["active 30s ago"], hours_ago=0, prompt="ship it"),
        _s("c", "Docs pass", "waiting", ["PR #102 awaiting review 5d"], hours_ago=30, pr=_pr(102)),
        _s("d", "Old spike", "stale", ["dirty worktree, idle 6d"], hours_ago=6 * 24, project="/Users/alex/code/other", group=None),
        _s("e", "scout-morning-briefing-20260908-0800", "parked", ["last active 4h ago"], hours_ago=4, project="/Users/alex/Scout", group=None, scout=True, prompt="You are Scout"),
        _s("f", "Archived thing", "done", ["archived"], hours_ago=2, archived=True),
    ]
    projects = [
        Project(key="/Users/alex/code/example-repo", name="Example Repo", group_id="cg-1", counts={}),
        Project(key="/Users/alex/code/other", name="other", group_id=None, counts={}),
        Project(key="/Users/alex/Scout", name="Scout", group_id=None, counts={}),
    ]
    return Index(generated_at=_iso(NOW), source_counts={}, source_errors=[], display={}, projects=projects, sessions=sessions)


def test_digest_matches_golden() -> None:
    out = render_digest(_index(), now=NOW, tz=ZoneInfo("America/New_York"), hours=24, instance_name="Scout", max_per_bucket=15)
    assert DIGEST_FILENAME == "cc-sessions.md"
    assert out == GOLDEN.read_text(encoding="utf-8"), "run with UPDATE_GOLDEN=1 to regenerate after an intentional change"


def test_digest_caps_buckets_and_reports_overflow() -> None:
    idx = _index()
    idx.sessions = [_s(f"n{i}", f"Needs {i}", "needs_you", ["CI failing"], hours_ago=i) for i in range(4)]
    out = render_digest(idx, now=NOW, tz=ZoneInfo("UTC"), hours=24, instance_name="Scout", max_per_bucket=2)
    assert "## Needs you (4)" in out and "Needs 0" in out and "Needs 1" in out and "Needs 2" not in out
    assert "_…and 2 more_" in out
```

Create `tests/fixtures/sessions/digest-golden.md` with exactly this content (trailing newline at the end):

```markdown
# Claude Code Sessions — state digest
Generated 2026-09-08 12:00 EDT · 4 sessions · 1 running · 1 need you · 1 waiting · 1 stale · Scout's own runs and archived sessions excluded

## Needs you (1)
- **Fix the parser** — Example Repo — changes requested on PR #98 — https://github.com/example-org/example-repo/pull/98 — last active 1h ago

## Running now (1)
- **Ship the cache** — Example Repo — active 30s ago — last active 0s ago

## Waiting on others (1)
- **Docs pass** — Example Repo — PR #102 awaiting review 5d — https://github.com/example-org/example-repo/pull/102 — last active 1d ago

## Stale (1)
- **Old spike** — other — dirty worktree, idle 6d — last active 6d ago

## Activity — last 24h, by project

### Example Repo

#### Ship the cache
**Last active:** 2026-09-08 12:00 EDT | **State:** running | **ID:** `b`

**First message/context:**
> ship it

**Files touched:**
- (none detected)

#### Fix the parser
**Last active:** 2026-09-08 11:00 EDT | **State:** needs_you | **ID:** `a`

**First message/context:**
> please fix the parser

**Files touched:**
- ~/code/example-repo/a.py

**Total:** 2 session(s) active in the last 24h.
```

The activity section lists only sessions active within `hours` **that have a transcript** (the first prompt is the point), most recent first, excluding Scout runs and archived sessions — so `c` (30 h) and `d` (no transcript) are out, `e` is a Scout run, `f` is archived.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_render.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scout.sessions.render'`

- [ ] **Step 3: Write the renderer**

```python
# scout/sessions/render.py
"""Render the LLM-facing digest, ``cc-sessions.md`` (spec §4.9).

State buckets first (what needs the user, what is working, what waits, what
rotted), then the per-project activity list the consolidation narrative uses.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from scout.sessions.derive import fmt_ago
from scout.sessions.model import AgentSession, Index, parse_iso

DIGEST_FILENAME = "cc-sessions.md"

_BUCKETS: tuple[tuple[str, str], ...] = (
    ("needs_you", "Needs you"),
    ("running", "Running now"),
    ("waiting", "Waiting on others"),
    ("stale", "Stale"),
)


def _recency(s: AgentSession) -> float:
    dt = parse_iso(s.last_activity_at)
    return dt.timestamp() if dt else 0.0


def _ago(s: AgentSession, now: datetime) -> str:
    dt = parse_iso(s.last_activity_at)
    return fmt_ago(now - dt) if dt else "unknown"


def _bucket_line(s: AgentSession, project_name: str, now: datetime) -> str:
    parts = [f"**{s.title or '(untitled)'}**", project_name, "; ".join(s.state_reasons) or s.state]
    if s.pr is not None and s.pr.url:
        parts.append(s.pr.url)
    parts.append(f"last active {_ago(s, now)}")
    return "- " + " — ".join(parts)


def _local(s: AgentSession, tz: ZoneInfo) -> str:
    dt = parse_iso(s.last_activity_at)
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z") if dt else "unknown"


def render_digest(
    index: Index, *, now: datetime, tz: ZoneInfo, hours: int, instance_name: str, max_per_bucket: int
) -> str:
    names = {p.key: p.name for p in index.projects}
    visible = [s for s in index.sessions if not s.is_scout_run and not s.is_archived]
    counts = {state: sum(1 for s in visible if s.state == state) for state, _ in _BUCKETS}

    out: list[str] = [
        "# Claude Code Sessions — state digest",
        (
            f"Generated {now.astimezone(tz).strftime('%Y-%m-%d %H:%M %Z')} · {len(visible)} sessions"
            f" · {counts['running']} running · {counts['needs_you']} need you · {counts['waiting']} waiting"
            f" · {counts['stale']} stale · {instance_name}'s own runs and archived sessions excluded"
        ),
        "",
    ]
    for state, heading in _BUCKETS:
        members = sorted((s for s in visible if s.state == state), key=_recency, reverse=True)
        out.append(f"## {heading} ({len(members)})")
        if not members:
            out.append("_none_")
        for s in members[:max_per_bucket]:
            out.append(_bucket_line(s, names.get(s.project_key, s.project_key), now))
        if len(members) > max_per_bucket:
            out.append(f"_…and {len(members) - max_per_bucket} more_")
        out.append("")

    cutoff = now.timestamp() - hours * 3600
    active = [s for s in visible if s.transcript is not None and _recency(s) >= cutoff]
    out.append(f"## Activity — last {hours}h, by project")
    out.append("")
    by_project: dict[str, list[AgentSession]] = {}
    for s in active:
        by_project.setdefault(s.project_key, []).append(s)
    for key in sorted(by_project, key=lambda k: names.get(k, k).lower()):
        out.append(f"### {names.get(key, key)}")
        out.append("")
        for s in sorted(by_project[key], key=_recency, reverse=True):
            assert s.transcript is not None
            files = "\n".join(f"- {p}" for p in s.transcript.files_touched) or "- (none detected)"
            out.extend(
                [
                    f"#### {s.title or '(untitled)'}",
                    f"**Last active:** {_local(s, tz)} | **State:** {s.state} | **ID:** `{s.id}`",
                    "",
                    "**First message/context:**",
                    f"> {s.transcript.first_prompt}",
                    "",
                    "**Files touched:**",
                    files,
                    "",
                ]
            )
    if not active:
        out.append(f"*No non-{instance_name} Claude Code sessions with transcripts in the last {hours} hours.*")
        out.append("")
    out.append(f"**Total:** {len(active)} session(s) active in the last {hours}h.")
    return "\n".join(out) + "\n"


__all__ = ["DIGEST_FILENAME", "render_digest"]
```

Then extend `run()` in `scout/sessions/index.py`:

```python
def run(
    *,
    data_dir: Path | None = None,
    use_gh: bool = True,
    now: datetime | None = None,
    opts: BuildOptions | None = None,
    render: bool = False,
    hours: int = 24,
    instance_name: str = "Scout",
    tz_name: str | None = None,
) -> tuple[Index, Path]:
    """Build the index and write it (plus both caches); optionally render the digest."""
    o = opts or default_options(data_dir, now=now, use_gh=use_gh)
    index = build_index(o)
    legacy = paths.cache_dir(o.data_dir) / LEGACY_CACHE_FILENAME
    if legacy.exists():
        try:
            legacy.unlink()
        except OSError:
            pass
    path = index_path(o.data_dir)
    write_index(index, path)
    if render:
        from scout import config as scout_config
        from scout.sessions.render import DIGEST_FILENAME, render_digest

        tz = scout_config.timezone_or_default(tz_name) if tz_name else scout_config.resolve_timezone(o.data_dir)
        digest = render_digest(
            index, now=o.now, tz=tz, hours=hours, instance_name=instance_name,
            max_per_bucket=o.settings.render_max_per_bucket,
        )
        (paths.cache_dir(o.data_dir) / DIGEST_FILENAME).write_text(digest, encoding="utf-8")
    return index, path
```

Add to `tests/unit/test_sessions_index.py`:

```python
def test_run_with_render_writes_digest(fake_data_dir: Path) -> None:
    run(opts=_world(fake_data_dir), render=True, tz_name="UTC")
    digest = (fake_data_dir / ".scout-cache" / "cc-sessions.md").read_text(encoding="utf-8")
    assert digest.startswith("# Claude Code Sessions — state digest")
    assert "## Needs you (1)" in digest
    # Session A: record title "Fix the parser", group "Example Repo", both matched signals, PR url, 1h idle.
    assert (
        "- **Fix the parser** — Example Repo — changes requested on PR #98; ended on a question"
        " — https://github.com/example-org/example-repo/pull/98 — last active 1h ago"
    ) in digest
    assert "scout-morning-briefing" not in digest  # Scout's own run excluded from the digest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_render.py tests/unit/test_sessions_index.py -q`
Expected: all PASS. If the golden diff shows only an intentional change, regenerate it by printing `render_digest(...)` from a REPL and saving; never hand-edit whitespace.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests
git add scout/sessions/render.py scout/sessions/index.py tests/fixtures/sessions/digest-golden.md tests/unit/test_sessions_render.py tests/unit/test_sessions_index.py
git commit -m "feat(sessions): state-first cc-sessions.md digest rendered from the index

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: CLI — `session index`, `session list`, `cc-cache` alias; retire the old module

**Files:**
- Modify: `scout/sessions/index.py` (add `main()` and `list_main()`)
- Modify: `scout/cli.py:145-176` (the `session` group)
- Modify: `scout/scripts/cc_session_cache.py` (becomes a shim)
- Modify: `tests/unit/test_cc_session_cache.py` (drop the retired tests)
- Create: `tests/unit/test_cli_session_subapp.py`

**Interfaces:**
- Produces: `main(*, json_out: bool, render: bool, use_gh: bool, hours: int, instance_name: str, tz_name: str|None, strict: bool) -> int`; `list_main(*, states: list[str], project: str|None, include_archived: bool, include_scout_runs: bool, json_out: bool) -> int`; CLI `scoutctl session index [--json] [--render] [--no-gh] [--hours N] [--instance-name S] [--timezone TZ] [--strict]`, `scoutctl session list [--state S]… [--project P] [--include-archived] [--include-scout-runs] [--json]`, `scoutctl session cc-cache [--hours N] [--instance-name S] [--timezone TZ]` (unchanged flags).
- Exit codes: `0` success incl. partial; `1` index could not be written, or `--strict` with any `source_error`; `2` bad arguments (Typer's own usage error exit code).

- [ ] **Step 1: Write the failing CLI tests**

```python
# tests/unit/test_cli_session_subapp.py
"""CLI smoke tests for `scoutctl session {index,list,cc-cache}`. Never calls the real gh."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import scout.sessions.github as gh
from scout.cli import app
from tests.unit.sessions_helpers import support_dir, write_desktop_record

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_real_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gh, "default_runner", lambda argv: None)


def test_index_json_prints_schema_and_writes_file(fake_data_dir: Path) -> None:
    write_desktop_record(support_dir(), "local_A")
    result = runner.invoke(app, ["session", "index", "--json", "--no-gh"])
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1 and len(payload["sessions"]) == 1
    assert (fake_data_dir / ".scout-cache" / "sessions-index.json").exists()
    assert not (fake_data_dir / ".scout-cache" / "cc-sessions.md").exists()


def test_index_render_writes_digest_and_prints_summary_line(fake_data_dir: Path) -> None:
    write_desktop_record(support_dir(), "local_A")
    result = runner.invoke(app, ["session", "index", "--render", "--no-gh", "--timezone", "UTC"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "sessions-index.json" in result.stdout and "1 sessions" in result.stdout
    assert (fake_data_dir / ".scout-cache" / "cc-sessions.md").read_text(encoding="utf-8").startswith("# Claude Code Sessions")


def test_index_strict_fails_on_source_error(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_desktop_record(support_dir(), "local_A", prs=[{"prNumber": 1, "repo": "example-org/example-repo"}])
    monkeypatch.setattr(gh, "gh_available", lambda: False)  # use_gh on, gh missing → one source_error
    ok = runner.invoke(app, ["session", "index"])
    assert ok.exit_code == 0
    strict = runner.invoke(app, ["session", "index", "--strict"])
    assert strict.exit_code == 1 and "gh not found" in strict.stderr


def test_index_returns_1_when_cache_dir_unwritable(fake_data_dir: Path) -> None:
    cache = fake_data_dir / ".scout-cache"
    for p in cache.iterdir():
        p.unlink()
    cache.rmdir()
    cache.write_text("blocker", encoding="utf-8")
    result = runner.invoke(app, ["session", "index", "--no-gh"])
    assert result.exit_code == 1 and "could not write" in result.stderr


def test_list_filters_by_state_and_project(fake_data_dir: Path) -> None:
    s = support_dir()
    write_desktop_record(s, "local_A", title="Alpha work")
    write_desktop_record(s, "local_B", title="Beta work", isArchived=True)
    write_desktop_record(s, "local_C", title="Gamma work", cwd="/Users/alex/code/other", originCwd="/Users/alex/code/other")
    runner.invoke(app, ["session", "index", "--no-gh"])

    default = runner.invoke(app, ["session", "list"])
    assert default.exit_code == 0 and "Alpha work" in default.stdout and "Gamma work" in default.stdout
    assert "Beta work" not in default.stdout  # archived hidden by default

    archived = runner.invoke(app, ["session", "list", "--include-archived", "--state", "done"])
    assert "Beta work" in archived.stdout and "Alpha work" not in archived.stdout

    by_project = runner.invoke(app, ["session", "list", "--project", "other", "--json"])
    rows = json.loads(by_project.stdout)
    assert [r["title"] for r in rows] == ["Gamma work"]


def test_list_builds_index_when_missing(fake_data_dir: Path) -> None:
    write_desktop_record(support_dir(), "local_A", title="Fresh")
    result = runner.invoke(app, ["session", "list"])
    assert result.exit_code == 0 and "Fresh" in result.stdout
    assert (fake_data_dir / ".scout-cache" / "sessions-index.json").exists()


def test_cc_cache_alias_still_writes_digest_with_legacy_flags(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gh, "gh_available", lambda: False)
    result = runner.invoke(app, ["session", "cc-cache", "--hours", "12", "--instance-name", "Scout", "--timezone", "UTC"])
    assert result.exit_code == 0, result.stdout + result.stderr
    digest = (fake_data_dir / ".scout-cache" / "cc-sessions.md").read_text(encoding="utf-8")
    assert "last 12h" in digest
    assert "CC session cache written to" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_cli_session_subapp.py -q`
Expected: FAIL — `No such command 'index'` / `'list'`, and the alias test fails on `"last 12h"` because the old renderer still writes the legacy digest.

- [ ] **Step 3: Add `main()` and `list_main()` to `scout/sessions/index.py`**

Append after `run()`:

```python
# ----- CLI entry points ----------------------------------------------------------------


def main(
    *,
    json_out: bool = False,
    render: bool = False,
    use_gh: bool = True,
    hours: int = 24,
    instance_name: str = "Scout",
    tz_name: str | None = None,
    strict: bool = False,
) -> int:
    """`scoutctl session index`. Exit 0 on success (even partial), 1 when the index
    cannot be written or `--strict` sees a source error."""
    import sys

    try:
        index, path = run(use_gh=use_gh, render=render, hours=hours, instance_name=instance_name, tz_name=tz_name)
    except OSError as exc:
        print(f"session index: could not write the index: {exc}", file=sys.stderr)
        return 1
    if json_out:
        print(json.dumps(index.to_dict(), indent=1))
    else:
        counts = index.source_counts
        print(
            f"session index: {path} — {len(index.sessions)} sessions"
            f" ({counts.get('running', 0)} running, {counts.get('open', 0)} open,"
            f" {counts.get('prs_refreshed', 0)} PRs refreshed, {len(index.source_errors)} source errors)"
        )
    if strict and index.source_errors:
        for err in index.source_errors:
            print(f"session index: [{err.source}] {err.message}", file=sys.stderr)
        return 1
    return 0


def _load_index_dict(data_dir: Path | None = None) -> dict:
    path = index_path(data_dir)
    if not path.exists():
        run(use_gh=False)
    return json.loads(path.read_text(encoding="utf-8"))


def list_main(
    *,
    states: list[str],
    project: str | None,
    include_archived: bool,
    include_scout_runs: bool,
    json_out: bool,
) -> int:
    """`scoutctl session list` — a human table (or JSON rows) from the written index."""
    import sys

    try:
        payload = _load_index_dict()
    except (OSError, json.JSONDecodeError) as exc:
        print(f"session list: could not read the index: {exc}", file=sys.stderr)
        return 1
    names = {p["key"]: p["name"] for p in payload.get("projects", [])}
    rows = []
    for s in payload.get("sessions", []):
        if s["is_archived"] and not include_archived:
            continue
        if s["is_scout_run"] and not include_scout_runs:
            continue
        if states and s["state"] not in states:
            continue
        pname = names.get(s["project_key"], s["project_key"])
        if project and project.lower() not in (pname.lower(), s["project_key"].lower(), Path(s["project_key"]).name.lower()):
            continue
        rows.append({**s, "project_name": pname})
    if json_out:
        print(json.dumps(rows, indent=1))
        return 0
    if not rows:
        print("no sessions match")
        return 0
    print("STATE      PROJECT               TITLE                                     PR      LAST ACTIVE")
    for s in rows:
        pr = f"#{s['pr']['number']}" if s.get("pr") else ""
        title = (s.get("title") or (s.get("transcript") or {}).get("first_prompt") or "(untitled)")[:41]
        print(f"{s['state']:<10} {s['project_name'][:21]:<21} {title:<41} {pr:<7} {s.get('last_activity_at') or ''}")
    return 0
```

Add `"list_main"` and `"main"` to the module's `__all__`.

- [ ] **Step 4: Rewire the `session` group in `scout/cli.py`**

Replace the whole `session_cc_cache_cmd` definition (keep the `session_app` Typer and its `add_typer` line) with:

```python
@session_app.command("index")
def session_index_cmd(
    json_out: bool = typer.Option(False, "--json", help="Print the index JSON to stdout."),
    render: bool = typer.Option(False, "--render", help="Also write the cc-sessions.md digest."),
    no_gh: bool = typer.Option(False, "--no-gh", help="Skip gh; PR states come from cache or read 'unknown'."),
    hours: int = typer.Option(24, "--hours", "-h", help="Activity window for the digest (default 24h)."),
    instance_name: str = typer.Option("Scout", "--instance-name", help="Instance name used in the digest header."),
    timezone: str = typer.Option(None, "--timezone", help="IANA zone for rendered timestamps (default: vault's)."),
    strict: bool = typer.Option(False, "--strict", help="Exit 1 if any source reported an error."),
) -> None:
    """Build .scout-cache/sessions-index.json from desktop records, transcripts, PIDs and gh."""
    from scout.sessions.index import main as index_main

    raise typer.Exit(
        index_main(
            json_out=json_out, render=render, use_gh=not no_gh, hours=hours,
            instance_name=instance_name, tz_name=timezone, strict=strict,
        )
    )


@session_app.command("list")
def session_list_cmd(
    state: list[str] = typer.Option([], "--state", help="Only these states (repeatable)."),
    project: str = typer.Option(None, "--project", help="Project name, folder name or key."),
    include_archived: bool = typer.Option(False, "--include-archived"),
    include_scout_runs: bool = typer.Option(False, "--include-scout-runs"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List sessions from the written index (builds it first if missing)."""
    from scout.sessions.index import list_main

    raise typer.Exit(
        list_main(
            states=list(state), project=project, include_archived=include_archived,
            include_scout_runs=include_scout_runs, json_out=json_out,
        )
    )


@session_app.command("cc-cache")
def session_cc_cache_cmd(
    hours: int = typer.Option(24, "--hours", "-h", help="Activity window for the digest (default 24h)."),
    instance_name: str = typer.Option("Scout", "--instance-name", help="Instance name used in the digest header."),
    timezone: str = typer.Option(None, "--timezone", help="IANA zone for rendered timestamps (default: vault's)."),
) -> None:
    """Back-compat alias for `session index --render` (vault scripts/cc-session-cache.sh calls this)."""
    from scout.scripts.cc_session_cache import main as cc_main

    raise typer.Exit(cc_main(hours=hours, instance_name=instance_name, tz_name=timezone))
```

Also update the comment block above `session_app` to say the group now builds the session index and that `cc-cache` is the alias.

- [ ] **Step 5: Shrink `scout/scripts/cc_session_cache.py` to a shim**

Replace the entire file with:

```python
"""Back-compat shim for ``scoutctl session cc-cache`` (Agent Sessions plan 1).

The original module walked ``~/.claude/projects/*/*.jsonl`` and rendered a flat
24-hour list. That work now lives in :mod:`scout.sessions` — the index builder
plus the state-first digest — and this module only keeps the historical entry
point (and the two extractor names) importable so nothing in an installed vault
breaks: ``scripts/cc-session-cache.sh`` still calls ``scoutctl session cc-cache``.
"""

from __future__ import annotations

import sys

from scout.sessions.transcript import extract_files_touched, extract_first_message

DEFAULT_HOURS_LOOKBACK = 24
OUTPUT_FILENAME = "cc-sessions.md"


def main(
    *,
    hours: int = DEFAULT_HOURS_LOOKBACK,
    instance_name: str = "Scout",
    tz_name: str | None = None,
) -> int:
    """CLI entry — never raises. Prints the digest path so runner logs show where it landed."""
    from scout import paths
    from scout.sessions.index import run

    try:
        index, _ = run(use_gh=True, render=True, hours=hours, instance_name=instance_name, tz_name=tz_name)
    except Exception as exc:  # noqa: BLE001 — the pre-session phase must never break on this
        print(f"cc-session-cache: {exc}", file=sys.stderr)
        return 0
    output_path = paths.cache_dir() / OUTPUT_FILENAME
    active = sum(1 for s in index.sessions if not s.is_scout_run and not s.is_archived)
    print(f"CC session cache written to {output_path} ({active} sessions, {hours}h lookback)")
    return 0


__all__ = ["DEFAULT_HOURS_LOOKBACK", "OUTPUT_FILENAME", "extract_files_touched", "extract_first_message", "main"]
```

If ruff does not know `BLE001` under the configured rule set, drop the `# noqa` comment.

- [ ] **Step 6: Trim `tests/unit/test_cc_session_cache.py`**

Delete every test that imported the retired names (`SessionEntry`, `_project_path_from_dirname`, `build_session_entry`, `iter_session_jsonls`, `render_markdown`, `run`, `CACHE_FILENAME`) — i.e. the "name decoding", "discovery", "caching", "markdown rendering", "build_session_entry" and "end-to-end output path" sections. Keep the four `extract_first_message` tests and the two `extract_files_touched` tests, importing from `scout.scripts.cc_session_cache` (which re-exports them), and update the module docstring to say the module is now a shim. Add one test:

```python
def test_main_delegates_to_the_index_and_never_raises(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scout.sessions.github as gh
    from scout.scripts.cc_session_cache import main

    monkeypatch.setattr(gh, "gh_available", lambda: False)
    assert main(hours=6, tz_name="UTC") == 0
    assert (fake_data_dir / ".scout-cache" / "cc-sessions.md").exists()
```

(Keep `from pathlib import Path`, `import pytest`, and the `_write_jsonl` helper; delete `_make_cc_project` and the now-unused imports.)

- [ ] **Step 7: Run the full unit suite, lint, and the perf guard**

Run: `.venv/bin/pytest tests/unit -q && .venv/bin/pytest tests/perf/test_no_heavy_imports.py -q`
Expected: all PASS. `scout.sessions` is only imported inside command bodies, so the heavy-import guard stays green.

- [ ] **Step 8: Commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests
git add scout/sessions/index.py scout/cli.py scout/scripts/cc_session_cache.py tests/unit/test_cc_session_cache.py tests/unit/test_cli_session_subapp.py
git commit -m "feat(cli): scoutctl session index/list; cc-cache becomes an alias for index --render

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Performance budget test

**Files:**
- Create: `tests/perf/test_sessions_index_perf.py`

**Interfaces:**
- Consumes: `BuildOptions`, `build_index` (Task 8); helpers from `tests/unit/sessions_helpers.py`.
- Budget (spec §4.12): cold build without `gh` over 200 desktop records + 450 transcripts < 5 s; warm rebuild (nothing changed) < 1 s.

- [ ] **Step 1: Write the test**

```python
# tests/perf/test_sessions_index_perf.py
"""Budget test for `scoutctl session index` (spec §4.12): cold < 5 s, warm < 1 s, no gh."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scout.sessions.index import BuildOptions, build_index
from scout.sessions.settings import AgentSessionsSettings
from tests.unit.sessions_helpers import claude_home, support_dir, write_desktop_record, write_transcript

RECORDS = 200
TRANSCRIPTS = 450
TURNS_PER_TRANSCRIPT = 40


def _rows(n: int) -> list[dict]:
    rows: list[dict] = [{"type": "user", "timestamp": "2026-09-08T10:00:00.000Z",
                         "message": {"role": "user", "content": [{"type": "text", "text": "do the thing"}]}}]
    for i in range(n):
        rows.append({"type": "assistant", "timestamp": "2026-09-08T10:00:01.000Z", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Read", "input": {"file_path": f"/Users/alex/code/repo-{i % 7}/src/f{i}.py"}}]}})
        rows.append({"type": "user", "timestamp": "2026-09-08T10:00:02.000Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": "x" * 400}]}})
    rows.append({"type": "assistant", "timestamp": "2026-09-08T10:00:03.000Z",
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}})
    return rows


@pytest.mark.perf
@pytest.mark.slow
def test_index_build_stays_within_budget(fake_data_dir: Path) -> None:
    s, h = support_dir(), claude_home()
    rows = _rows(TURNS_PER_TRANSCRIPT)
    for i in range(TRANSCRIPTS):
        uuid = f"{i:08d}-0000-0000-0000-000000000000"
        write_transcript(h, f"-Users-alex-code-repo-{i % 7}", uuid, rows, mtime_ago_hours=1 + (i % 24))
        if i < RECORDS:
            write_desktop_record(s, f"local_{i:04d}", cliSessionId=uuid, cwd=f"/Users/alex/code/repo-{i % 7}",
                                 originCwd=f"/Users/alex/code/repo-{i % 7}")
    opts = BuildOptions(data_dir=fake_data_dir, settings=AgentSessionsSettings(), claude_home=h, support_dir=s,
                        now=datetime.now(tz=UTC), use_gh=False, toplevel=lambda p: None, pid_alive=lambda pid: False)

    t0 = time.perf_counter()
    cold = build_index(opts)
    cold_s = time.perf_counter() - t0
    assert len(cold.sessions) == TRANSCRIPTS  # 200 desktop + 250 cli-only

    # Persist the caches the way run() does, then rebuild with nothing changed.
    from scout.sessions import github
    from scout.sessions.transcript import TRANSCRIPT_CACHE_FILENAME, load_transcript_cache

    assert (fake_data_dir / ".scout-cache" / TRANSCRIPT_CACHE_FILENAME).exists()
    assert len(load_transcript_cache(fake_data_dir / ".scout-cache" / TRANSCRIPT_CACHE_FILENAME)) == TRANSCRIPTS
    assert (fake_data_dir / ".scout-cache" / github.PR_CACHE_FILENAME).exists()

    t1 = time.perf_counter()
    warm = build_index(opts)
    warm_s = time.perf_counter() - t1
    assert len(warm.sessions) == TRANSCRIPTS

    assert cold_s < 5.0, f"cold build took {cold_s:.2f}s"
    assert warm_s < 1.0, f"warm build took {warm_s:.2f}s"
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/pytest tests/perf/test_sessions_index_perf.py -q -m perf`
Expected: 1 passed, printed timings well inside budget on a laptop. If the cold build is over budget, the first thing to profile is `parse_transcript` (it JSON-decodes every user/assistant line); cut cost by skipping `json.loads` for lines that contain none of `"tool_use"`, `"tool_result"`, `"assistant"` — the filter already in place — before touching anything else.

- [ ] **Step 3: Commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests
git add tests/perf/test_sessions_index_perf.py
git commit -m "test(sessions): perf budget for the session index build

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Changelog, manifest flag, and end-to-end verification on this machine

**Files:**
- Modify: `CHANGELOG.md` (repo root) — `[Unreleased] → Added`
- Modify: `scout/manifest.py:65-76` — add a feature flag
- Test: `tests/unit/test_cli.py` (the existing manifest test keeps passing; no new test file)

- [ ] **Step 1: Add the manifest flag**

In `build_manifest()`'s `features` dict add, after `"schedule_v2": True,`:

```python
            # Agent-session index (scoutctl session index / list). scout-app's
            # Sessions page gates on this flag before shelling out.
            "agent_sessions_v1": True,
```

Run: `.venv/bin/pytest tests/unit/test_cli.py -q` — Expected: PASS.

- [ ] **Step 2: Add the changelog entry**

Under `## [Unreleased]`, add a `### Added` section above the existing `### Fixed` with this bullet (one paragraph, house style):

```markdown
### Added
- **Agent-session index — `scoutctl session index` / `session list`** (`engine/scout/sessions/`) — one machine-readable picture of every local Claude Code session, desktop or CLI, at `.scout-cache/sessions-index.json`. Merges the desktop app's per-session records (title, worktree, branch, linked PRs, parent session, archive flag), sidebar group assignments, `~/.claude` live-process files, transcript facts (first prompt, files touched, tool calls, whether the last turn ended on a question) and `gh` PR review/CI state (10-minute TTL cache, 25-fetch cap, three-strikes short-circuit when gh is down) into a derived **state** per session — `running`, `needs_you`, `waiting`, `parked`, `stale`, `done` — with the matched reasons spelled out. `cc-sessions.md` keeps its filename but becomes a state-first digest (Needs you · Running now · Waiting on others · Stale, then the per-project 24 h activity list), so consolidation can surface "changes requested on your agent's PR" with evidence. `scoutctl session cc-cache` is now an alias for `session index --render`; configuration lives under a new `agent_sessions:` block. Read-only over every source — Scout never writes to the desktop app's store. Spec: scout-app `docs/superpowers/specs/2026-09-08-agent-sessions-design.md`.
```

- [ ] **Step 3: Run the whole engine suite and lint**

Run (from `~/scout-plugin/engine`):

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests
.venv/bin/mypy scout/sessions
.venv/bin/pytest tests/ -q
```

Expected: ruff clean, mypy clean for the new package, all tests pass (perf tests included).

- [ ] **Step 4: Verify against the real machine (manual acceptance, spec §7)**

```bash
~/scout-plugin/engine/.venv/bin/scoutctl session index --json | python3 -c '
import json,sys; d=json.load(sys.stdin)
print(d["source_counts"], len(d["source_errors"]), "errors")
for s in d["sessions"]:
    if s["is_open"] or s["state"] in ("needs_you","running"):
        print(s["state"], "|", s["title"], "|", s["state_reasons"])'
~/scout-plugin/engine/.venv/bin/scoutctl session list --state needs_you --state waiting
sed -n '1,25p' ~/Scout/.scout-cache/cc-sessions.md
```

Check, and record the outcome in the PR description:
1. `source_counts.desktop` is about 196 and `open` equals the number of Claude Code windows currently open.
2. Every session you know has "changes requested" or red CI shows as `needs_you` with that reason; a green, unreviewed PR of yours shows `PR #n ready to merge`.
3. `cc-sessions.md` starts with the state digest and still ends with the per-project activity list.
4. Run `~/Scout/scripts/cc-session-cache.sh` (the vault's unchanged pre-session hook) and confirm it prints `CC session cache written to …` and exits 0.

- [ ] **Step 5: Commit and open the PR**

```bash
git add CHANGELOG.md scout/manifest.py
git commit -m "docs(changelog): agent-session index; manifest flag agent_sessions_v1

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push -u origin feat/agent-sessions-index
gh pr create --repo Raven-Scout/scout-plugin --title "feat(sessions): agent-session index (scoutctl session index/list, state-first digest)" --body-file - <<'BODY'
Implements plan 1 of the Agent Sessions design (scout-app `docs/superpowers/specs/2026-09-08-agent-sessions-design.md`).

- New `scout/sessions/` package: desktop-store, `~/.claude`, transcript and `gh` loaders; pure state derivation; atomic index writer; state-first `cc-sessions.md` digest.
- `scoutctl session index [--json|--render|--no-gh|--strict]`, `scoutctl session list`; `session cc-cache` is now an alias.
- `agent_sessions:` config block with packaged defaults; manifest flag `agent_sessions_v1`.
- Read-only over every source. Fixtures anonymised per CLAUDE.md.

Manual acceptance on the author's machine: <paste the four checks from Task 12 step 4>.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
```

Releasing (`scripts/release.sh minor`) is a separate decision for Jordan after the PR merges; plan 2 (phase docs) and plan 3 (the app) depend on this release landing.

---

## Self-review against the spec

- **§3 refresh triggers** — engine side covered (`session index`, `cc-cache` alias, `--render`); the app-side FSEvents trigger is plan 3.
- **§4.1 identity/merge** — Task 8 (`local_` ids, `cli:<uuid>`, dedupe on `cliSessionId`, tombstones skipped in Task 3, `transcriptUnavailable` → `transcript: null`).
- **§4.2 project resolution** — Task 7 (`git_toplevel` memoised, worktree stripping) + Task 8 (`_projects`: group name wins, `Archived` group ⇒ archived, group_id echoed).
- **§4.3 Scout runs** — Task 7 `is_scout_run` + Task 8 `_custom_title` for CLI-only vault runs; excluded from the digest in Task 9.
- **§4.4 liveness** — Task 4 (`pid_alive`, dead-pid filter) + Task 8 (`is_open`, `last_activity_at = max(desktop, mtime)`) + Task 7 (running = open ∧ ≤ 120 s).
- **§4.5 transcript facts + cache** — Task 5.
- **§4.6 PR state** — Task 6 (fields, TTL, cap, terminal never refetched, stale flag, legacy seed, short-circuit) + Task 7 `choose_pr`.
- **§4.7 state rules** — Task 7, table-driven, including the ready-to-merge and draft refinements from the spec's decision log.
- **§4.8 schema** — Task 2 model + `display` block (Task 8); `schema_version` first in the JSON.
- **§4.9 digest** — Task 9, same filename, buckets capped, activity section preserved.
- **§4.10 CLI + exit codes** — Task 10.
- **§4.11 config** — Task 1 (`agent_sessions`, packaged defaults, tolerant parsing).
- **§4.12 failure handling + budget** — every loader returns errors (Tasks 3–6, 8), atomic writes (Tasks 5, 6, 8), perf test (Task 11).
- **§7 engine tests** — one module per source module plus CLI, golden, hermeticity preserved (no real `gh`/`git`/HOME), perf.
- **§8** — this plan is "1 · Engine index"; changelog and manifest flag in Task 12; release deferred to Jordan.

Type/name consistency spot-checks: `PRRef.legacy_state` (Task 3) is what Task 6 reads; `SourceError(source, message)` everywhere; `TranscriptInfo.mtime_ns` is the cache key in Task 5 and serialised in Task 2; `BuildOptions.toplevel` / `pid_alive` / `gh_runner` / `gh_available` are the injection points every test uses; `STATES` order drives sorting in Task 8 and bucket order in Task 9.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-08-agent-sessions-plan-1-engine-index.md` (scout-app). Code lands in `~/scout-plugin` on branch `feat/agent-sessions-index`. Two execution options:

1. **Subagent-driven (recommended)** — a fresh subagent per task with review between tasks (`superpowers:subagent-driven-development`).
2. **Inline** — execute in one session with checkpoints (`superpowers:executing-plans`).
