# Agent Sessions 1b — Index Speed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `scoutctl session index` meet its budget on a large real machine: cold < 5 s, and warm < 1 s in the Mac app's steady state. Its output must stay exactly the same.

**Architecture:** Each loader in `engine/scout/sessions/` gets a cache keyed on file identity and keeps its contract.
- **Desktop records:** cached by (size, mtime_ns), with a last-good fallback for records caught mid-write.
- **Project roots:** come from a pure-Python walk up to `.git` instead of `git` subprocesses.
- **Transcripts:** checkpointed, so a growing transcript is parsed only from where the last build stopped. The full parse no longer decodes user rows that cannot change the answer.
- **Cache writes:** a cache is rewritten only when it changed.
- **Work counters:** a `BuildStats` collector records the work each build did, and CI tests assert on those counters instead of on time.

**Tech Stack:** Python ≥ 3.11 standard library only (`json`, `hashlib`, `pathlib`, `concurrent.futures`). Tests use pytest, lint uses ruff, and type checks use mypy.

**Spec:** `docs/superpowers/specs/2026-09-28-agent-sessions-index-speed-design.md` (this repo). It builds on the Agent Sessions design in [Raven-Scout/Scout#112](https://github.com/Raven-Scout/Scout/pull/112).

The code lives in **scout-plugin**, under `engine/scout/sessions/`, on branch `feat/agent-sessions-index-speed`. That branch is stacked on plan 1's `feat/agent-sessions-index` ([Raven-Scout/scout-plugin#243](https://github.com/Raven-Scout/scout-plugin/pull/243)).

In this plan:
- Every path is relative to the scout-plugin worktree root, and every command runs from its `engine/` directory.
- `$SCRATCH` means a scratch directory outside both repositories, such as the session scratchpad. Nothing in it is ever committed.

## Dry run

Before this plan went up for review, its code was applied mechanically to a scratch copy of plan 1's branch (34a3ee8) and run there. Nothing from that copy was committed.

- **Tests.** Tasks 1–7 pass their tests.
  - The full suite passes except for one test that fails only in the scratch copy. That test embeds the checkout path in an XML comment, and the scratch path contains `--`, which XML comments do not allow.
  - After `ruff format`, both `ruff check` and `mypy` are clean.
- **Exactness on real transcripts.**
  - The Task 3 script found no mismatch on 504 real transcripts. Plan 1's parse took 5.1 s in total; the new parse took 2.8 s.
  - The Task 4 script found no mismatch in 1,512 tail checks, for either the facts or the checkpoints.
- **Budget fixture.** The Task 6 fixture generated in 1.1 s locally, so Task 6 Step 4's CI restriction is not needed. Cold 2.1 s, unchanged 0.05 s, steady 0.07 s.
- **End to end on the author's machine.** Measured with `python -m scout session index --no-gh` into a scratch vault:

  | Build | Plan 1 | This plan, no pool | With the Task 7 pool |
  |---|---|---|---|
  | Cold (median of 3) | 5.48 s | 3.68 s | 1.49 s |
  | Warm, sessions running (median of 6) | 1.59 s | 0.16 s | 0.17 s |

  Task 7 should therefore find the pool not needed.
- **Output.** The 1b index matched plan 1's on all 369 sessions and all 34 project keys. The one exception was the session that was running between the two builds.

These numbers are a forecast. The implementation still follows each task's test-first steps, and Tasks 7 and 8 measure again.

## Global Constraints

- Python ≥ 3.11.
- ruff: line length 120, rules `E F W I B UP`. mypy runs on `scout/`. Run ruff, mypy and pytest from `engine/`: an absolute-path ruff invocation loses the per-file ignores.
- **No new dependencies** (spec §2 non-goal: "a faster JSON library, unless the budget cannot be met without one").
- **Identical output.** The following stay unchanged:
  - Index schema v1.
  - The `cc-sessions.md` digest.
  - The index contract key-set test, which stays green untouched.
  - "Every transcript's derived facts are identical to what a full parse produces" (spec §2.3).
- **Read-only over every source.** Never write, rename, lock or touch the desktop app's store or `~/.claude`.
- **The desktop cache stores only `DesktopRecord` fields**, never a raw record. The MCP configuration in a raw record can hold credentials.
- **Budgets** (spec §2):
  - Cold build < 5 s.
  - Warm build < 1 s in the app's steady state: up to 5 desktop records rewritten and up to 5 transcripts grown since the previous build.
  - A rebuild with nothing changed must also be < 1 s.
  - All measured end to end through `scoutctl session index --no-gh` on a large real machine.
  - CI asserts work counters. Its wall-clock ceilings are 60 s cold and 10 s per warm scenario.
- **Hermetic tests.**
  - HOME points at a tmp dir (autouse `_hermetic_env`), and real `gh` is blocked (autouse `_block_real_gh`).
  - No test reads the real `~/.claude`, the desktop store or `~/Scout`.
  - The only use of the `git` binary is the real-git agreement test, which runs on a tmp repository.
- **Anonymised fixtures** (repo `CLAUDE.md`):
  - People are Alex, Priya and Sam; repos are `example-org/<repo>`; tickets are `PROJ-1234`.
  - No real titles, paths, repos or vendor names.
  - One-off scripts that read real data print only counts, timings and field names, never content. They live in `$SCRATCH`.
- **Git safety.**
  - Work only in the new worktree. The primary scout-plugin checkout is the live install: never switch its branch.
  - Never use bare `git stash`.
  - End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

These are the inputs most likely to hurt a real user that the spec implies but does not spell out. Each one has a test in the task that owns the code.

1. **A transcript whose last line is half-written when a build runs, then completed.** The half line counts toward that build's facts, as plan 1 read it. It is not consumed into the checkpoint, and the next build reads it whole. Covered in Task 4: `test_a_half_written_last_line_is_read_whole_on_the_next_build`, plus mid-line splits in the property test.
2. **A linked worktree whose `.git` file holds a relative `gitdir:`** (`git worktree add --relative-paths`, git ≥ 2.48). It resolves to its main repository, as `git` does. Covered in Task 2: `test_repo_root_resolves_a_linked_worktree_to_its_main_repo[relative]` and `test_repo_root_agrees_with_git`.
3. **A project folder reached through a symlink.** It resolves to the real repository path, which is what `git rev-parse` reported in plan 1. Covered in Task 2: `test_repo_root_follows_a_symlinked_project_folder`.
4. **A plan-1 (version 1) transcript cache on disk after upgrading.** It is read as empty, the first build is cold, and the file is rewritten as version 2. It is never mis-read as fresh data. Covered in Task 4: `test_a_plan_1_transcript_cache_is_rebuilt_as_version_2`.
5. **A desktop record caught mid-write, then valid on the next build.**
   - The mid-write build silently serves the last good record.
   - If the same broken version is still there on a later build, that build reports it.
   - Once the file is valid, the new version replaces the cached one.

   Covered in Task 1: `test_a_record_caught_mid_write_is_served_from_its_last_good_version`.

## Assumptions the exactness rests on

State these in the PR; the equivalence runs in Tasks 3 and 4 check them on real data.

- **A tool result always follows its tool call.** So answers written before the final assistant row can never answer that row's questions. This is spec §3.3.
- **Claude Code writes compact JSON with literal keys.**
  - A user row therefore contains the bytes `"type":"user"`.
  - A row's own `type` is never escaped, so a row without the bytes `"assistant"` is not an assistant row.
  - Rows in any other serialisation still take the exact slow path, since they are decoded as plan 1 did.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `engine/scout/sessions/stats.py` | create | `BuildStats` work counters (spec §3.7) |
| `engine/scout/sessions/desktop.py` | modify | desktop record cache, last-good fallback (spec §3.1) |
| `engine/scout/sessions/derive.py` | modify | `repo_root` replaces `git_toplevel`; NUL guard in `is_scout_run` (spec §3.2) |
| `engine/scout/sessions/transcript.py` | rewrite | byte-level forward pass, checkpoints, cache v2, optional process pool (spec §3.3–§3.5) |
| `engine/scout/sessions/index.py` | modify | `stats` plumbing, per-build root memo, write-if-changed caches (spec §3.6) |
| `engine/scout/sessions/github.py` | modify | `write_pr_cache` returns whether it wrote |
| `engine/tests/unit/sessions_reference_parse.py` | create | plan 1's parser, frozen, as the exactness reference |
| `engine/tests/unit/sessions_transcript_cases.py` | create | synthetic transcripts covering every parser branch |
| `engine/tests/unit/test_sessions_transcript_exact.py` | create | full parse == plan 1 parse; user rows decoded lazily |
| `engine/tests/unit/test_sessions_transcript_incremental.py` | create | checkpoints: unchanged / grown / rewritten; tail == full property test |
| `engine/tests/unit/test_sessions_desktop.py` | modify | desktop cache tests |
| `engine/tests/unit/test_sessions_derive.py` | modify | `repo_root` tests, real-git agreement |
| `engine/tests/unit/test_sessions_transcript.py` | modify | cache v2 round-trip and validation |
| `engine/tests/unit/test_sessions_index.py` | modify | stats, memo, v2 cache, write-if-changed |
| `engine/tests/unit/sessions_helpers.py` | modify | `write_transcript` writes compact JSON, as Claude Code does |
| `engine/tests/perf/test_sessions_index_perf.py` | rewrite | realistic fixture; cold / unchanged / steady-state counters |
| `CHANGELOG.md` | modify | Unreleased entry |

---

### Task 1: Desktop record cache and build statistics

**Files:**
- Create: `engine/scout/sessions/stats.py`
- Modify: `engine/scout/sessions/desktop.py` (imports, `load_desktop_records`, new cache section, `__all__`)
- Modify: `engine/scout/sessions/index.py` (`build_index`, `run`)
- Test: `engine/tests/unit/test_sessions_desktop.py`, `engine/tests/unit/test_sessions_index.py`

**Interfaces:**
- Consumes: plan 1's `desktop._record(raw, fallback_id)`, `desktop._int(v)` and `_atomic.atomic_write_text(path, text, *, fsync=False)`.
- Produces:
  - `scout.sessions.stats.BuildStats`: a dataclass with the int fields `desktop_decoded`, `desktop_served_last_good`, `transcripts_full_parsed`, `transcripts_tail_parsed` and `transcript_bytes_read`, plus `caches_written: list[str]`.
  - `desktop.DESKTOP_CACHE_FILENAME = "sessions-desktop.cache.json"`.
  - `desktop.CachedRecord(size: int, mtime_ns: int, record: DesktopRecord, failed: tuple[int, int] | None = None)`, a frozen dataclass.
  - `desktop.load_desktop_records(support_dir: Path, *, cache: dict[str, CachedRecord] | None = None, stats: BuildStats | None = None) -> tuple[list[DesktopRecord], list[SourceError]]`. It updates `cache` in place.
  - `desktop.load_desktop_cache(cache_path: Path) -> dict[str, CachedRecord]`.
  - `desktop.write_desktop_cache(cache_path: Path, cache: dict[str, CachedRecord]) -> bool`. It returns False instead of raising.
  - `index.build_index(opts: BuildOptions, *, stats: BuildStats | None = None) -> Index`.
  - `index.run(..., stats: BuildStats | None = None)`.
  - The cache names that go into `caches_written` are `"desktop"`, `"transcripts"` and `"prs"`.

- [ ] **Step 0: Create the stacked worktree and a fresh venv**

From the scout-plugin checkout's root (do not switch the primary checkout's branch):

```bash
git fetch origin
git worktree add .claude/worktrees/agent-sessions-index-speed -b feat/agent-sessions-index-speed feat/agent-sessions-index
cd .claude/worktrees/agent-sessions-index-speed/engine
uv venv --python 3.12 && uv pip install -e ".[dev]"
.venv/bin/pytest tests/ -q
```

Expected: every test passes. Record the pass count. A fresh worktree and venv is green; a shell that points at a live vault is not, so always compare against the same environment.

- [ ] **Step 1: Write the failing desktop cache tests**

In `engine/tests/unit/test_sessions_desktop.py`, replace the import block with the following. Then add the helpers and tests below the existing tests.

```python
import json
import os
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from scout.sessions.desktop import (
    _RECORD_FIELDS,
    DESKTOP_CACHE_FILENAME,
    CachedRecord,
    DesktopRecord,
    PRRef,
    default_support_dir,
    load_desktop_cache,
    load_desktop_records,
    load_groups,
    load_worktree_leases,
    write_desktop_cache,
)
from scout.sessions.stats import BuildStats
from tests.unit.sessions_helpers import (
    support_dir,
    write_desktop_config,
    write_desktop_record,
    write_worktrees,
)
```

```python
# ----- desktop record cache (1b spec §3.1) ------------------------------------------


def _bump(path: Path, seconds: int = 1) -> None:
    """Move a file's mtime forward, so a rewrite shows even on a coarse-mtime filesystem."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 1_000_000_000))


def _rec(**overrides: Any) -> DesktopRecord:
    base: dict[str, Any] = {
        "session_id": "local_aaa",
        "cli_session_id": None,
        "title": "Fix the parser",
        "title_source": "auto",
        "cwd": "/Users/alex/code/example-repo",
        "origin_cwd": "/Users/alex/code/example-repo",
        "worktree_path": None,
        "worktree_name": None,
        "branch": None,
        "source_branch": None,
        "created_at_ms": 1_788_400_000_000,
        "last_activity_at_ms": 1_788_800_000_000,
        "model": "claude-opus-5",
        "effort": "high",
        "is_archived": False,
        "completed_turns": 4,
        "prs": [PRRef(number=98, repo="example-org/example-repo", url=None, legacy_state=None)],
        "parent_session_id": None,
        "spawned_task_id": None,
        "scheduled_task_id": None,
        "kept_dirty_worktree": False,
        "transcript_unavailable": False,
    }
    base.update(overrides)
    return DesktopRecord(**base)


def test_unchanged_records_are_served_from_the_cache() -> None:
    s = support_dir()
    write_desktop_record(s, "local_aaa")
    write_desktop_record(s, "local_bbb", title="Tidy the release notes")
    cache: dict[str, CachedRecord] = {}
    cold = BuildStats()
    first, errors = load_desktop_records(s, cache=cache, stats=cold)
    assert errors == [] and cold.desktop_decoded == 2 and len(cache) == 2

    warm = BuildStats()
    second, errors = load_desktop_records(s, cache=cache, stats=warm)
    assert errors == [] and warm.desktop_decoded == 0
    assert second == first


def test_a_rewritten_record_is_decoded_again() -> None:
    s = support_dir()
    path = write_desktop_record(s, "local_aaa")
    cache: dict[str, CachedRecord] = {}
    load_desktop_records(s, cache=cache)
    write_desktop_record(s, "local_aaa", title="Renamed")
    _bump(path)
    stats = BuildStats()
    records, _ = load_desktop_records(s, cache=cache, stats=stats)
    assert stats.desktop_decoded == 1 and [r.title for r in records] == ["Renamed"]


def test_a_record_caught_mid_write_is_served_from_its_last_good_version() -> None:
    s = support_dir()
    path = write_desktop_record(s, "local_aaa")
    cache: dict[str, CachedRecord] = {}
    load_desktop_records(s, cache=cache)

    path.write_text('{"sessionId": "local_aaa", "title": "Half wr', encoding="utf-8")  # the app mid-rewrite
    _bump(path)
    first = BuildStats()
    records, errors = load_desktop_records(s, cache=cache, stats=first)
    assert [r.title for r in records] == ["Fix the parser"] and errors == []  # silent the first time
    assert (first.desktop_served_last_good, first.desktop_decoded) == (1, 0)

    records, errors = load_desktop_records(s, cache=cache)  # the same broken version on the next build
    assert [r.title for r in records] == ["Fix the parser"]
    assert len(errors) == 1 and errors[0].source == "desktop" and "local_aaa.json" in errors[0].message

    write_desktop_record(s, "local_aaa", title="Finished the rewrite")
    _bump(path, 2)
    records, errors = load_desktop_records(s, cache=cache)
    assert [r.title for r in records] == ["Finished the rewrite"] and errors == []
    assert cache[str(path)].failed is None


def test_a_broken_record_with_no_cached_version_is_reported() -> None:
    s = support_dir()
    bad = s / "claude-code-sessions" / "org-0000" / "user-0000" / "local_bad.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")
    cache: dict[str, CachedRecord] = {}
    records, errors = load_desktop_records(s, cache=cache)
    assert records == [] and cache == {}
    assert len(errors) == 1 and "local_bad.json" in errors[0].message


def test_a_deleted_record_drops_out_of_the_cache() -> None:
    s = support_dir()
    gone = write_desktop_record(s, "local_aaa")
    kept = write_desktop_record(s, "local_bbb")
    cache: dict[str, CachedRecord] = {}
    load_desktop_records(s, cache=cache)
    gone.unlink()
    records, _ = load_desktop_records(s, cache=cache)
    assert [r.session_id for r in records] == ["local_bbb"] and set(cache) == {str(kept)}


def test_a_missing_store_empties_the_cache() -> None:
    cache = {"/gone/local_aaa.json": CachedRecord(size=1, mtime_ns=1, record=_rec())}
    assert load_desktop_records(support_dir(), cache=cache) == ([], []) and cache == {}


def test_desktop_cache_round_trip(tmp_path: Path) -> None:
    path = tmp_path / DESKTOP_CACHE_FILENAME
    cache = {
        "/s/local_aaa.json": CachedRecord(size=10, mtime_ns=20, record=_rec()),
        "/s/local_bbb.json": CachedRecord(size=1, mtime_ns=2, record=_rec(session_id="local_bbb", prs=[]), failed=(3, 4)),
    }
    assert write_desktop_cache(path, cache) is True
    assert load_desktop_cache(path) == cache


@pytest.mark.parametrize(
    "breakage",
    [
        lambda e: e.pop("size"),
        lambda e: e.update(size=True),
        lambda e: e.update(mtime_ns="1"),
        lambda e: e.update(failed=[1]),
        lambda e: e.update(failed=[1, "2"]),
        lambda e: e.update(record=None),
        lambda e: e["record"].pop("title"),
        lambda e: e["record"].update(session_id=None),
        lambda e: e["record"].update(title=5),
        lambda e: e["record"].update(last_activity_at_ms=True),
        lambda e: e["record"].update(is_archived="no"),
        lambda e: e["record"].update(prs={}),
        lambda e: e["record"]["prs"][0].update(number="98"),
        lambda e: e["record"]["prs"][0].update(url=5),
        lambda e: e["record"]["prs"][0].pop("legacy_state"),
    ],
)
def test_desktop_cache_skips_a_wrongly_typed_entry(tmp_path: Path, breakage: Any) -> None:
    path = tmp_path / DESKTOP_CACHE_FILENAME
    entry = CachedRecord(size=1, mtime_ns=2, record=_rec())
    write_desktop_cache(path, {"good": entry, "bad": entry})
    raw = json.loads(path.read_text(encoding="utf-8"))
    breakage(raw["entries"]["bad"])
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert set(load_desktop_cache(path)) == {"good"}


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[1,2",
        "[]",
        '{"entries": {}}',
        '{"version": 2, "entries": {}}',
        '{"version": true, "entries": {}}',
        '{"version": 1, "entries": []}',
    ],
)
def test_a_desktop_cache_of_another_version_or_shape_is_empty(tmp_path: Path, text: str) -> None:
    path = tmp_path / DESKTOP_CACHE_FILENAME
    path.write_text(text, encoding="utf-8")
    assert load_desktop_cache(path) == {}


def test_a_missing_desktop_cache_is_empty(tmp_path: Path) -> None:
    assert load_desktop_cache(tmp_path / DESKTOP_CACHE_FILENAME) == {}


def test_the_desktop_cache_never_stores_unlisted_fields(tmp_path: Path) -> None:
    s = support_dir()
    secret = {"server": {"headers": {"authorization": "Bearer not-a-real-token"}}}
    write_desktop_record(s, "local_aaa", remoteMcpServersConfig=secret)
    cache: dict[str, CachedRecord] = {}
    load_desktop_records(s, cache=cache)
    path = tmp_path / DESKTOP_CACHE_FILENAME
    write_desktop_cache(path, cache)
    text = path.read_text(encoding="utf-8")
    assert "not-a-real-token" not in text and "remoteMcpServersConfig" not in text


def test_the_cached_record_fields_cover_the_dataclass() -> None:
    # Adding a DesktopRecord field without teaching the cache validator about it fails here.
    assert {*_RECORD_FIELDS, "prs"} == {f.name for f in fields(DesktopRecord)}


def test_write_desktop_cache_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "cache"
    blocker.write_text("not a dir", encoding="utf-8")
    assert write_desktop_cache(blocker / DESKTOP_CACHE_FILENAME, {}) is False
    assert not list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 2: Write the failing index test**

In `engine/tests/unit/test_sessions_index.py`, add `from scout.sessions.stats import BuildStats` to the imports and this test after `test_run_writes_index_and_caches_atomically`:

```python
def test_a_rebuild_decodes_no_unchanged_desktop_record(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    cold = BuildStats()
    first = build_index(opts, stats=cold)
    assert cold.desktop_decoded == 4 and "desktop" in cold.caches_written
    assert (fake_data_dir / ".scout-cache" / "sessions-desktop.cache.json").exists()

    warm = BuildStats()
    second = build_index(opts, stats=warm)
    assert warm.desktop_decoded == 0 and "desktop" not in warm.caches_written
    assert second.to_dict()["sessions"] == first.to_dict()["sessions"]
    assert second.to_dict()["projects"] == first.to_dict()["projects"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_desktop.py tests/unit/test_sessions_index.py -q`

Expected: collection errors. Both `ModuleNotFoundError: No module named 'scout.sessions.stats'` and `ImportError: cannot import name '_RECORD_FIELDS'` are acceptable.

- [ ] **Step 4: Create `engine/scout/sessions/stats.py`**

```python
"""Work counters that one index build fills in (1b spec §3.7). Not part of the index schema."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BuildStats:
    desktop_decoded: int = 0  # desktop records read and decoded
    desktop_served_last_good: int = 0  # decode failures answered from the desktop cache
    transcripts_full_parsed: int = 0  # transcripts parsed from byte 0
    transcripts_tail_parsed: int = 0  # transcripts parsed from a checkpoint
    transcript_bytes_read: int = 0  # bytes those parses read (not the ≤ 4 KB identity check or first-prompt head)
    caches_written: list[str] = field(default_factory=list)  # "desktop" / "transcripts" / "prs"


__all__ = ["BuildStats"]
```

- [ ] **Step 5: Add the cache to `engine/scout/sessions/desktop.py`**

Change the module docstring's last paragraph to:

```python
"""...
Nothing here writes to the desktop store. The one file written is Scout's own
desktop cache (``.scout-cache/sessions-desktop.cache.json``), which holds only the
``DesktopRecord`` fields. Every loader returns ``(data, errors)``; a missing
directory or file is *not* an error, a malformed file is.
"""
```

Replace the imports with:

```python
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from scout.sessions._atomic import atomic_write_text
from scout.sessions.model import SourceError
from scout.sessions.stats import BuildStats

DESKTOP_CACHE_FILENAME = "sessions-desktop.cache.json"
_DESKTOP_CACHE_VERSION = 1
_DECODE_ERRORS = (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, AttributeError, ValueError, KeyError)
```

Replace `load_desktop_records` (the whole function) with:

```python
@dataclass(frozen=True)
class CachedRecord:
    """One desktop-cache entry (1b spec §3.1): the record as last decoded, keyed by file identity."""

    size: int
    mtime_ns: int
    record: DesktopRecord
    failed: tuple[int, int] | None = None  # (size, mtime_ns) of a version that would not decode


def _decode_record(path: Path) -> DesktopRecord:
    """Read and extract one record. Raises one of ``_DECODE_ERRORS`` when it cannot."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("not a JSON object")
    return _record(raw, fallback_id=path.stem)


def load_desktop_records(
    support_dir: Path,
    *,
    cache: dict[str, CachedRecord] | None = None,
    stats: BuildStats | None = None,
) -> tuple[list[DesktopRecord], list[SourceError]]:
    """Every ``local_*.json`` record, decoding only files whose (size, mtime_ns) changed (1b spec §3.1).

    *cache* is updated in place: changed files are decoded again and files that are gone are
    dropped. A file that fails to decode is served from its last good entry. That happens
    silently the first time, because the desktop app rewrites records mid-turn. If the same
    version is still unreadable on a later build, the build also reports a ``SourceError``.
    """
    if cache is None:
        cache = {}
    if stats is None:
        stats = BuildStats()
    root = support_dir / "claude-code-sessions"
    records: list[DesktopRecord] = []
    errors: list[SourceError] = []
    if not root.is_dir():
        cache.clear()
        return records, errors
    seen: set[str] = set()
    for path in sorted(root.glob("*/*/local_*.json")):
        key = str(path)
        try:
            st = path.stat()
        except OSError as e:  # gone between the glob and the stat
            errors.append(SourceError(source="desktop", message=f"{path.name}: {e}"))
            continue
        ident = (st.st_size, st.st_mtime_ns)
        prior = cache.get(key)
        if prior is not None and (prior.size, prior.mtime_ns) == ident:
            seen.add(key)
            records.append(prior.record)
            continue
        try:
            rec = _decode_record(path)
        except _DECODE_ERRORS as e:
            if prior is None:
                errors.append(SourceError(source="desktop", message=f"{path.name}: {e}"))
                continue
            if prior.failed == ident:  # the same unreadable version as last build: say so
                errors.append(SourceError(source="desktop", message=f"{path.name}: {e}"))
            cache[key] = replace(prior, failed=ident)
            seen.add(key)
            records.append(prior.record)
            stats.desktop_served_last_good += 1
            continue
        stats.desktop_decoded += 1
        cache[key] = CachedRecord(size=st.st_size, mtime_ns=st.st_mtime_ns, record=rec)
        seen.add(key)
        records.append(rec)
    for key in [k for k in cache if k not in seen]:
        del cache[key]
    return records, errors


# ----- desktop cache file ---------------------------------------------------------------

_REQUIRED_STR = ("session_id", "cwd", "origin_cwd")
_OPTIONAL_STR = (
    "cli_session_id",
    "title",
    "title_source",
    "worktree_path",
    "worktree_name",
    "branch",
    "source_branch",
    "model",
    "effort",
    "parent_session_id",
    "spawned_task_id",
    "scheduled_task_id",
)
_OPTIONAL_INT = ("created_at_ms", "last_activity_at_ms", "completed_turns")
_BOOL = ("is_archived", "kept_dirty_worktree", "transcript_unavailable")
_RECORD_FIELDS = (*_REQUIRED_STR, *_OPTIONAL_STR, *_OPTIONAL_INT, *_BOOL)  # every DesktopRecord field but prs


def _cached_pr(v: Any) -> PRRef | None:
    if not isinstance(v, dict) or not {"number", "repo", "url", "legacy_state"} <= v.keys():
        return None
    number, repo, url, legacy = v["number"], v["repo"], v["url"], v["legacy_state"]
    if _int(number) is None or not isinstance(repo, str):
        return None
    if not (url is None or isinstance(url, str)) or not (legacy is None or isinstance(legacy, str)):
        return None
    return PRRef(number=number, repo=repo, url=url, legacy_state=legacy)


def _cached_desktop_record(v: Any) -> DesktopRecord | None:
    """Rebuild a cached record, or None when a field is missing or fails the checks fresh records pass."""
    if not isinstance(v, dict) or not {*_RECORD_FIELDS, "prs"} <= v.keys():
        return None
    ok = (
        all(isinstance(v[k], str) for k in _REQUIRED_STR)
        and all(v[k] is None or isinstance(v[k], str) for k in _OPTIONAL_STR)
        and all(v[k] is None or _int(v[k]) is not None for k in _OPTIONAL_INT)
        and all(isinstance(v[k], bool) for k in _BOOL)
        and isinstance(v["prs"], list)
    )
    if not ok:
        return None
    prs = [_cached_pr(p) for p in v["prs"]]
    if any(p is None for p in prs):
        return None
    return DesktopRecord(**{k: v[k] for k in _RECORD_FIELDS}, prs=[p for p in prs if p is not None])


def _cached_entry(v: Any) -> CachedRecord | None:
    if not isinstance(v, dict) or _int(v.get("size")) is None or _int(v.get("mtime_ns")) is None:
        return None
    record = _cached_desktop_record(v.get("record"))
    if record is None:
        return None
    failed = v.get("failed")
    if failed is None:
        return CachedRecord(size=v["size"], mtime_ns=v["mtime_ns"], record=record)
    if isinstance(failed, list) and len(failed) == 2 and all(_int(x) is not None for x in failed):
        return CachedRecord(size=v["size"], mtime_ns=v["mtime_ns"], record=record, failed=(failed[0], failed[1]))
    return None


def load_desktop_cache(cache_path: Path) -> dict[str, CachedRecord]:
    """Load the desktop cache. A missing, corrupt or other-version file is empty; bad entries are skipped."""
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict) or _int(raw.get("version")) != _DESKTOP_CACHE_VERSION:
        return {}
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        return {}
    out: dict[str, CachedRecord] = {}
    for key, value in entries.items():
        entry = _cached_entry(value)
        if entry is not None:
            out[key] = entry
    return out


def write_desktop_cache(cache_path: Path, cache: dict[str, CachedRecord]) -> bool:
    """Atomically replace the desktop cache. Best-effort: returns False instead of raising.

    It stores only the ``DesktopRecord`` fields, never a raw record: a raw record's MCP
    configuration can hold credentials.
    """
    payload = {
        "version": _DESKTOP_CACHE_VERSION,
        "entries": {
            key: {
                "size": e.size,
                "mtime_ns": e.mtime_ns,
                "record": asdict(e.record),
                "failed": list(e.failed) if e.failed is not None else None,
            }
            for key, e in cache.items()
        },
    }
    try:
        atomic_write_text(cache_path, json.dumps(payload))
    except OSError:
        return False
    return True
```

Replace `__all__` with:

```python
__all__ = [
    "DESKTOP_CACHE_FILENAME",
    "CachedRecord",
    "DesktopRecord",
    "Groups",
    "PRRef",
    "WorktreeLease",
    "default_support_dir",
    "load_desktop_cache",
    "load_desktop_records",
    "load_groups",
    "load_worktree_leases",
    "write_desktop_cache",
]
```

- [ ] **Step 6: Wire the cache and the collector into `engine/scout/sessions/index.py`**

Add `from scout.sessions.stats import BuildStats` to the imports. Change `build_index`'s signature and first lines to the following, and delete the later `cache_dir = paths.cache_dir(opts.data_dir)` line that now duplicates it:

```python
def build_index(opts: BuildOptions, *, stats: BuildStats | None = None) -> Index:
    """Build the index from every source. *stats*, when given, is filled with the work done (1b spec §3.7)."""
    if stats is None:
        stats = BuildStats()
    s = opts.settings
    errors: list[SourceError] = []
    cache_dir = paths.cache_dir(opts.data_dir)
    dcache_path = cache_dir / desktop.DESKTOP_CACHE_FILENAME
    dcache = desktop.load_desktop_cache(dcache_path)
    dcache_loaded = dict(dcache)  # entries are replaced, never mutated, so a shallow copy is a snapshot
    records, e1 = desktop.load_desktop_records(opts.support_dir, cache=dcache, stats=stats)
    groups, e2 = desktop.load_groups(opts.support_dir)
```

Just before the `# Write back only what this run used…` comment near the end of `build_index`, add:

```python
    if dcache != dcache_loaded and desktop.write_desktop_cache(dcache_path, dcache):
        stats.caches_written.append("desktop")
```

In `run`, add a `stats: BuildStats | None = None` keyword parameter after `tz_name`, and pass it through: `index = build_index(o, stats=stats)`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_desktop.py tests/unit/test_sessions_index.py -q`
Expected: PASS.

- [ ] **Step 8: Run the gates and commit**

```bash
.venv/bin/ruff format scout tests && .venv/bin/ruff check --fix scout tests && .venv/bin/mypy scout
.venv/bin/pytest tests/ -q
git add scout/sessions/stats.py scout/sessions/desktop.py scout/sessions/index.py tests/unit/test_sessions_desktop.py tests/unit/test_sessions_index.py
git commit -m "perf(sessions): cache decoded desktop records by size and mtime

A record caught mid-write is served from its last good version and reported
only if the same version is still unreadable on the next build. The cache holds
only DesktopRecord fields. build_index takes an optional BuildStats collector.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Project roots without `git`

**Files:**
- Modify: `engine/scout/sessions/derive.py`:
  - imports;
  - replace `_git_rev_parse` and `git_toplevel` with `_root_at` and `repo_root`;
  - `is_scout_run`;
  - `__all__`.
- Modify: `engine/scout/sessions/index.py` (the `BuildOptions.toplevel` default and the step-5 memo)
- Test: `engine/tests/unit/test_sessions_derive.py`, `engine/tests/unit/test_sessions_index.py`

**Interfaces:**
- Consumes: `derive.resolve_project_key(origin_cwd, *, toplevel)`, which is unchanged.
- Produces: `derive.repo_root(path: str) -> str | None`, which never raises. `derive.git_toplevel` is removed. `BuildOptions.toplevel` defaults to `derive.repo_root`, resolved at construction time.

- [ ] **Step 1: Write the failing `repo_root` tests**

In `engine/tests/unit/test_sessions_derive.py`:
- Change the import of `git_toplevel` to `repo_root`.
- Rename `test_resolve_project_key_prefers_git_toplevel_then_stripping` to `test_resolve_project_key_prefers_the_repo_root_then_stripping`.
- Replace `test_git_toplevel_of_an_empty_path_is_none` and `test_git_toplevel_resolves_a_linked_worktree_to_its_main_repo` with the tests below.
- Keep the `_git` helper.

```python
def test_repo_root_of_an_empty_or_relative_path_is_none() -> None:
    # Path("") is ".", which used to resolve the caller's own repo; a relative path would too.
    assert repo_root("") is None
    assert repo_root("code/example-repo") is None


def _repo(root: Path) -> Path:
    (root / ".git").mkdir(parents=True)
    return root.resolve()


def _linked_worktree(repo: Path, name: str, *, relative: bool = False) -> Path:
    """What `git worktree add` lays out: <wt>/.git names <repo>/.git/worktrees/<name>, whose commondir is ../.."""
    gitdir = repo / ".git" / "worktrees" / name
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    wt = repo / ".claude" / "worktrees" / name
    wt.mkdir(parents=True)
    target = os.path.relpath(gitdir, wt) if relative else str(gitdir)
    (wt / ".git").write_text(f"gitdir: {target}\n", encoding="utf-8")
    return wt


def test_repo_root_finds_the_folder_holding_dot_git(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "example-repo")
    (repo / "src" / "deep").mkdir(parents=True)
    assert repo_root(str(repo)) == str(repo)
    assert repo_root(str(repo / "src" / "deep")) == str(repo)


@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_repo_root_resolves_a_linked_worktree_to_its_main_repo(tmp_path: Path, relative: bool) -> None:
    repo = _repo(tmp_path / "example-repo")
    wt = _linked_worktree(repo, "w1", relative=relative)
    (wt / "sub").mkdir()
    assert repo_root(str(wt)) == str(repo)
    assert repo_root(str(wt / "sub")) == str(repo)


def test_repo_root_of_a_submodule_is_the_submodule_folder(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "example-repo")
    (repo / ".git" / "modules" / "vendored").mkdir(parents=True)  # a submodule's git dir has no commondir
    sub = repo / "vendored"
    sub.mkdir()
    (sub / ".git").write_text("gitdir: ../.git/modules/vendored\n", encoding="utf-8")
    assert repo_root(str(sub)) == str(sub)


def test_repo_root_of_a_worktree_of_a_submodule_is_that_worktree(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "example-repo")
    gitdir = repo / ".git" / "modules" / "vendored" / "worktrees" / "w2"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")  # → .git/modules/vendored, not named .git
    wt = tmp_path / "vendored-w2"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    assert repo_root(str(wt)) == str(wt.resolve())


def test_repo_root_of_a_deleted_folder_uses_its_nearest_existing_ancestor(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "example-repo")
    assert repo_root(str(repo / ".claude" / "worktrees" / "gone")) == str(repo)


def test_repo_root_outside_any_repo_is_none(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    assert repo_root(str(tmp_path / "plain")) is None


@pytest.mark.parametrize("dot_git", ["", "not a gitdir line\n", "gitdir:\n", "gitdir: /nowhere/at/all\n"])
def test_repo_root_skips_a_broken_dot_git_file_and_keeps_walking(tmp_path: Path, dot_git: str) -> None:
    repo = _repo(tmp_path / "example-repo")
    inner = repo / "inner"
    inner.mkdir()
    (inner / ".git").write_text(dot_git, encoding="utf-8")
    assert repo_root(str(inner)) == str(repo)


def test_repo_root_follows_a_symlinked_project_folder(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "real" / "example-repo")
    link = tmp_path / "link-to-repo"
    link.symlink_to(repo, target_is_directory=True)
    assert repo_root(str(link)) == str(repo)  # the real path, as git reports it


def test_repo_root_never_raises_on_a_nul_byte(tmp_path: Path) -> None:
    assert repo_root(f"{tmp_path}/bad\x00name") is None


def _git_answer(path: Path) -> str | None:
    """What plan 1 asked git: the common dir's parent when it is <repo>/.git, else --show-toplevel."""

    def rev_parse(*args: str) -> str | None:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", *args], capture_output=True, text=True, check=False, timeout=30
        )
        return (proc.stdout.strip() or None) if proc.returncode == 0 else None

    common = rev_parse("--path-format=absolute", "--git-common-dir")
    if common is not None and common.endswith("/.git"):
        return str(Path(common).parent)
    return rev_parse("--show-toplevel")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs a git binary")
def test_repo_root_agrees_with_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    repo = tmp_path / "example-repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=repo)
    (repo / "sub").mkdir()
    worktree = repo / ".claude" / "worktrees" / "w1"
    _git("worktree", "add", "-q", "-b", "claude/w1", str(worktree), cwd=repo)
    relative = repo / ".claude" / "worktrees" / "w2"
    _git("worktree", "add", "-q", "-b", "claude/w2", str(relative), cwd=repo)
    gitfile = relative / ".git"
    target = gitfile.read_text(encoding="utf-8").split(":", 1)[1].strip()
    gitfile.write_text(f"gitdir: {os.path.relpath(target, relative)}\n", encoding="utf-8")  # as --relative-paths does
    lib = tmp_path / "example-lib"
    lib.mkdir()
    _git("init", "-q", cwd=lib)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=lib)
    _git("-c", "protocol.file.allow=always", "submodule", "--quiet", "add", str(lib), "vendored", cwd=repo)

    for p in (repo, repo / "sub", worktree, relative, repo / "vendored"):
        assert repo_root(str(p)) == _git_answer(p), p
    assert repo_root(str(worktree)) == str(repo.resolve())  # a worktree is its main repo, not itself


def test_is_scout_run_tolerates_a_nul_byte_in_a_recorded_path(tmp_path: Path) -> None:
    vault = tmp_path / "Scout"
    assert not is_scout_run(
        origin_cwd=f"{vault}\x00", title="scout-morning-briefing-20260908-1150", scheduled_task_id=None, vault=vault
    )
```

In `engine/tests/unit/test_sessions_index.py`, add:

```python
def test_project_roots_are_resolved_once_per_distinct_path(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    asked: list[str] = []

    def counting(path: str) -> str | None:
        asked.append(path)
        return None

    opts.toplevel = counting
    idx = build_index(opts)
    assert sorted(asked) == sorted({s.origin_cwd for s in idx.sessions if s.origin_cwd})
```

In `test_build_options_toplevel_honors_monkeypatch`, patch `repo_root` instead of `git_toplevel`:

```python
    monkeypatch.setattr(derive_mod, "repo_root", lambda p: "/patched")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_derive.py tests/unit/test_sessions_index.py -q`
Expected:
- `test_sessions_derive.py` fails to collect with `ImportError: cannot import name 'repo_root'`.
- The memo test fails because `asked` holds duplicates: sessions A, B and C share an `origin_cwd`.
- The monkeypatch test fails with `AttributeError`.

- [ ] **Step 3: Implement `repo_root` in `engine/scout/sessions/derive.py`**

Replace the imports with:

```python
import os
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
```

Replace `_git_rev_parse` and `git_toplevel` (the two functions and the `@functools.lru_cache` line) with:

```python
def _root_at(folder: Path) -> str | None:
    """The repository root that a ``.git`` directly inside *folder* names, or None if there is none
    or it cannot be used (unreadable, no ``gitdir:`` line, pointing at a git dir that is gone)."""
    dot = folder / ".git"
    try:
        if dot.is_dir():
            return str(folder)
        if not dot.is_file():
            return None
        first = dot.read_text(encoding="utf-8", errors="replace").partition("\n")[0].strip()
        if not first.startswith("gitdir:"):
            return None
        target = first.removeprefix("gitdir:").strip()
        if not target:
            return None
        gitdir = Path(target) if os.path.isabs(target) else folder / target
        if not gitdir.is_dir():
            return None
        commondir = gitdir / "commondir"
        common = commondir.read_text(encoding="utf-8", errors="replace").strip() if commondir.is_file() else ""
        if common:
            main = (gitdir / common).resolve()  # an absolute commondir replaces gitdir in the join
            if main.name == ".git":
                return str(main.parent)  # a linked worktree: its main repository
    except (OSError, RuntimeError):
        return None
    return str(folder)  # a submodule, a worktree of one, or a separate git dir: --show-toplevel's answer


def repo_root(path: str) -> str | None:
    """The main repository root for *path*, or None. Pure Python, no ``git`` (1b spec §3.2).

    The walk starts at the nearest existing ancestor, so a deleted worktree still names its
    repository, and goes up to the first usable ``.git``. It never raises. A relative path
    returns None, like the empty one (``Path("")`` is "."), rather than resolving against the
    caller's own working directory. The result is the real path, as ``git rev-parse`` reports.
    """
    if not path or not os.path.isabs(path):
        return None
    try:
        start = Path(path)
        while not start.exists():
            start = start.parent
        start = start.resolve()
        for folder in (start, *start.parents):
            root = _root_at(folder)
            if root is not None:
                return root
    except (OSError, RuntimeError, ValueError):
        return None
    return None
```

In `is_scout_run`, widen the `except`:

```python
    except (OSError, ValueError):  # ValueError: a NUL byte in a recorded path
```

In `__all__`, replace `"git_toplevel"` with `"repo_root"`.

- [ ] **Step 4: Use it from `engine/scout/sessions/index.py`, memoised per build**

Change the `BuildOptions.toplevel` default:

```python
    toplevel: Callable[[str], str | None] = field(default_factory=lambda: derive.repo_root)
```

In step 5 of `build_index`, replace `sess.project_key = resolve_project_key(sess.origin_cwd, toplevel=opts.toplevel)` with a memo owned by this build, so a long-lived caller never sees a stale root:

```python
    # 5. Project key, Scout-run flag, state.
    roots: dict[str, str | None] = {}

    def toplevel(path: str) -> str | None:
        if path not in roots:
            roots[path] = opts.toplevel(path)
        return roots[path]

    stale_after = timedelta(days=s.stale_after_days)
    running_window = timedelta(seconds=s.running_window_seconds)
    for sess in sessions:
        sess.project_key = resolve_project_key(sess.origin_cwd, toplevel=toplevel)
```

(The rest of the loop is unchanged.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_derive.py tests/unit/test_sessions_index.py -q`
Expected: PASS. `test_repo_root_agrees_with_git` runs wherever `git` is installed.

- [ ] **Step 6: Check nothing else used `git_toplevel`**

Run: `grep -rn "git_toplevel\|_git_rev_parse" scout tests`
Expected: no output.

- [ ] **Step 7: Run the gates and commit**

```bash
.venv/bin/ruff format scout tests && .venv/bin/ruff check --fix scout tests && .venv/bin/mypy scout
.venv/bin/pytest tests/ -q
git add scout/sessions/derive.py scout/sessions/index.py tests/unit/test_sessions_derive.py tests/unit/test_sessions_index.py
git commit -m "perf(sessions): resolve project roots in Python instead of git

repo_root walks up from the nearest existing ancestor to the first .git and
follows a linked worktree's commondir to its main repository, agreeing with
git rev-parse, including relative gitdirs and submodules. Memoised per build.
is_scout_run tolerates a NUL byte in a recorded path.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: A full parse that decodes only what can matter

**Files:**
- Create: `engine/tests/unit/sessions_reference_parse.py`, `engine/tests/unit/sessions_transcript_cases.py`, `engine/tests/unit/test_sessions_transcript_exact.py`
- Modify: `engine/tests/unit/sessions_helpers.py` (`write_transcript` writes compact JSON)
- Rewrite: `engine/scout/sessions/transcript.py` (the whole file; the cache section and `transcript_info` are unchanged in this task)
- Create (not committed): `$SCRATCH/transcript_equivalence.py`

**Interfaces:**
- Consumes: `model.LastTurn`, `model.TranscriptInfo`, `model.dt_to_iso` and `model.parse_iso`, all unchanged.
- Produces:
  - `parse_transcript(path, *, st=None, home=None) -> TranscriptInfo`, with the same signature and results.
  - Internals that Task 4 builds on:
    - `_State` (a dataclass with `files`, `tool_calls`, `last_assistant`, `pending`, `last_ts` and `held`, plus a `.copy()` method);
    - `_home_prefix(home: Path | None) -> str`;
    - `_consume(state, data: bytes, home_prefix: str) -> tuple[int, bytes]`, returning the bytes consumed and the trailing partial line;
    - `_info(path, st, state, partial: bytes, first_prompt: str, home_prefix: str) -> TranscriptInfo`;
    - `_first_message_of(data: bytes) -> str`;
    - `_decode(line: bytes) -> dict | None`;
    - `_unreadable(path, st) -> TranscriptInfo`.
  - Test helpers:
    - `reference_parse(path, *, st=None, home=None) -> TranscriptInfo`.
    - From `sessions_transcript_cases`: `HOME`, `REPO`, `ts`, `prompt`, `say`, `call`, `result`, `without_timestamp`, `jsonl(*rows, spaced=False, newline=b"\n") -> bytes` and `cases(*, spaced: bool) -> dict[str, bytes]`.

- [ ] **Step 1: Freeze plan 1's parser as the reference**

Create `engine/tests/unit/sessions_reference_parse.py`. It is a verbatim copy of plan 1's parser. Only the names `extract_first_message` and `parse_transcript` are changed, to `_extract_first_message` and `reference_parse`.

```python
"""Plan 1's transcript parser, frozen as the exactness reference for 1b (spec §3.4).

This is a verbatim copy of ``parse_transcript`` and ``extract_first_message`` (with their
helpers) from ``scout.sessions.transcript`` as of scout-plugin 34a3ee8, before the 1b rewrite.
Do not edit it to make a test pass: a difference from it is a regression. Tests only.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from scout.sessions.model import LastTurn, TranscriptInfo, dt_to_iso, parse_iso

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


def _extract_first_message(jsonl_path: Path) -> str:
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
                            raw_text = part.get("text")
                            if not isinstance(raw_text, str):
                                continue
                            text = raw_text[:_FIRST_MSG_MAX_CHARS]
                            if text:
                                return text
                elif isinstance(content, str) and content.strip():
                    return content[:_FIRST_MSG_MAX_CHARS]
    except OSError:
        return "(parse error)"
    return "(could not extract first message)"


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
    texts = [t for b in blocks if b.get("type") == "text" and isinstance(t := b.get("text"), str)]
    if texts and texts[-1].rstrip().endswith("?"):
        return "question"
    return "end_turn"


def reference_parse(path: Path, *, st: os.stat_result | None = None, home: Path | None = None) -> TranscriptInfo:
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
        first_prompt=_extract_first_message(path),
        files_touched=sorted(files)[:_MAX_FILES_TOUCHED],
        tool_calls=tool_calls,
        last_turn=LastTurn(at=dt_to_iso(at) if at else None, kind=_last_turn_kind(last_assistant, answered)),
        mtime_ns=stat.st_mtime_ns,
    )
```

- [ ] **Step 2: Write the synthetic cases module**

Create `engine/tests/unit/sessions_transcript_cases.py`:

```python
"""Synthetic transcripts that exercise every branch of the transcript parser (1b spec §3.4, §5).

Each case is a file's exact bytes: Claude Code's compact rows by default, json.dumps' spaced
rows with ``spaced=True``. Identifiers are synthetic (Alex, example-repo) per CLAUDE.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HOME = Path("/Users/alex")
REPO = "/Users/alex/code/example-repo"


def ts(second: int) -> str:
    return f"2026-09-08T10:{second // 60:02d}:{second % 60:02d}.000Z"


def prompt(text: str, second: int) -> dict[str, Any]:
    return {"type": "user", "timestamp": ts(second), "message": {"role": "user", "content": text}}


def say(text: str, second: int) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": ts(second),
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def call(tool_id: str, name: str, second: int, **tool_input: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": ts(second),
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}],
        },
    }


def result(tool_id: str, second: int, content: str = "ok") -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": ts(second),
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}]},
    }


def without_timestamp(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "timestamp"}


def jsonl(*rows: dict[str, Any] | bytes, spaced: bool = False, newline: bytes = b"\n") -> bytes:
    """One line per row; bytes rows are written as given."""
    seps = (", ", ": ") if spaced else (",", ":")
    return b"".join((r if isinstance(r, bytes) else json.dumps(r, separators=seps).encode()) + newline for r in rows)


def _compact(row: dict[str, Any]) -> bytes:
    return json.dumps(row, separators=(",", ":")).encode()


def cases(*, spaced: bool) -> dict[str, bytes]:
    def j(*rows: dict[str, Any] | bytes) -> bytes:
        return jsonl(*rows, spaced=spaced)

    def read(n: int, second: int) -> dict[str, Any]:
        return call(f"t{n}", "Read", second, file_path=f"{REPO}/src/f{n:02d}.py")

    many_paths = [f"{REPO}/src/m{n:02d}.py" for n in range(14, 0, -1)] + [
        "/etc/hosts",
        "/Users/alex/.claude/plugins/cache/plugin.js",
        f"{REPO}/node_modules/dep/index.js",
        f"{REPO}/src/m03.py",  # a repeat
    ]
    two_calls = {
        "type": "assistant",
        "timestamp": ts(1),
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": f"{REPO}/a.py"}},
                {"type": "tool_use", "id": "q1", "name": "AskUserQuestion", "input": {}},
            ],
        },
    }
    torn = (
        b'{"type":"assistant","timestamp":"2026-09-08T10:00:03.000Z","message":{"content":[{"type":"tool_use",'
        b'"id":"t2","name":"Read","input":{"file_path":"' + f"{REPO}/src/late.py".encode() + b'"'
    )
    return {
        "tool_loop": j(prompt("Tidy the parser", 0), read(1, 1), result("t1", 2), read(2, 3), result("t2", 4), say("Done.", 5)),
        "question_tool_pending": j(prompt("go", 0), call("q1", "AskUserQuestion", 1, questions=[])),
        "question_tool_answered": j(prompt("go", 0), call("q1", "AskUserQuestion", 1, questions=[]), result("q1", 30)),
        "question_mark": j(prompt("go", 0), say("Which repo do you mean?", 1)),
        "answered_then_asks_in_text": j(
            prompt("go", 0), call("q1", "AskUserQuestion", 1, questions=[]), result("q1", 2), say("And which branch?", 3)
        ),
        "question_beside_another_call": j(prompt("go", 0), two_calls, result("t1", 2)),
        "assistant_without_timestamp": j(prompt("go", 0), read(1, 1), result("t1", 2), without_timestamp(say("done", 3))),
        "assistant_with_a_bad_timestamp": j(
            prompt("go", 0), read(1, 1), result("t1", 2), {**say("done", 3), "timestamp": "not a time"}
        ),
        "user_row_with_a_bad_timestamp_last": j(prompt("go", 0), say("done", 1), {**prompt("thanks", 2), "timestamp": 5}),
        "no_assistant_row": j(prompt("hi", 0)),
        "odd_assistant_shapes": j(
            prompt("go", 0),
            {"type": "assistant", "timestamp": ts(1), "message": {"content": "plain string"}},
            {"type": "assistant", "timestamp": ts(2), "message": "x"},
            {"type": "assistant", "timestamp": ts(3), "message": {"content": [5, "x", {"type": "text", "text": {}}]}},
        ),
        "garbage_and_non_objects": j(
            prompt("go", 0),
            b"not json at all",
            b'"a bare string"',
            b"[1, 2]",
            b"",
            b"   ",
            b'{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1"',
            read(1, 1),
            say("done", 2),
        ),
        "user_row_that_mentions_the_assistant": j(
            prompt("go", 0),
            call("q1", "AskUserQuestion", 1, questions=[]),
            {**result("q1", 2), "toolUseResult": {"answers": {"role": "assistant"}}},
        ),
        "held_rows_before_a_decoded_user_row": j(
            prompt("go", 0),
            call("q1", "AskUserQuestion", 1, questions=[]),
            result("q1", 2),
            {**prompt("and then", 3), "toolUseResult": {"role": "assistant"}},
        ),
        "rows_neither_user_nor_assistant": j(
            prompt("go", 0),
            {"type": "progress", "data": {"message": {"type": "tool_use", "name": "Read"}}},
            {"type": "progress", "data": {"message": {"type": "assistant", "content": [{"type": "tool_use"}]}}},
            {"type": "file-history-snapshot", "snapshot": {}},
            say("done", 1),
        ),
        "file_paths_on_user_rows": j(
            prompt("go", 0),
            read(1, 1),
            {**result("t1", 2), "toolUseResult": {"file_path": f"{REPO}/src/from_result.py"}},
            say("done", 3),
        ),
        "more_than_ten_files": j(
            prompt("go", 0), *(call(f"t{n}", "Read", n, file_path=p) for n, p in enumerate(many_paths, 1)), say("ok", 30)
        ),
        "first_prompt_after_other_rows": j(
            {"type": "custom-title", "customTitle": "scratch"},
            {"type": "system", "content": "hook ran"},
            prompt("the real ask", 0),
            say("ok", 1),
        ),
        "first_prompt_past_line_fifty": j(
            *({"type": "system", "content": f"note {n}"} for n in range(55)), prompt("too late", 0), say("ok", 1)
        ),
        "half_written_last_line": j(prompt("go", 0), read(1, 1), result("t1", 2)) + torn,
        "crlf_line_endings": jsonl(
            prompt("go", 0), read(1, 1), result("t1", 2), say("done?", 3), spaced=spaced, newline=b"\r\n"
        ),
        "lone_carriage_return": j(prompt("go", 0)) + _compact(read(1, 1)) + b"\r" + _compact(result("t1", 2)) + b"\n"
        + j(say("done", 3)),
        "invalid_utf8": j(prompt("go", 0))
        + b'{"type":"assistant","timestamp":"2026-09-08T10:00:01.000Z","message":{"content":[{"type":"tool_use",'
        b'"id":"t1","name":"Read","input":{"file_path":"/Users/alex/code/\xffbad.py"}}]}}\n'
        + b'{"type":"user","timestamp":"2026-09-08T10:00:02.000Z","message":{"content":[{"type":"tool_result",'
        b'"tool_use_id":"t1","content":"\xfe\xfd"}]}}\n',
        "empty_file": b"",
    }
```

- [ ] **Step 3: Write the failing exactness tests**

Create `engine/tests/unit/test_sessions_transcript_exact.py`:

```python
"""The new full parse is plan 1's parse, only cheaper (1b spec §3.4).

Every synthetic case must give exactly what the frozen plan 1 parser gives. The pass must
also skip decoding user rows unless they can still change a fact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scout.sessions import transcript as tr
from tests.unit.sessions_reference_parse import reference_parse
from tests.unit.sessions_transcript_cases import (
    HOME,
    REPO,
    call,
    cases,
    jsonl,
    prompt,
    result,
    say,
    without_timestamp,
)


@pytest.mark.parametrize("spaced", [False, True], ids=["compact", "spaced"])
@pytest.mark.parametrize("name", sorted(cases(spaced=False)))
def test_full_parse_matches_the_plan_1_parser(tmp_path: Path, name: str, spaced: bool) -> None:
    p = tmp_path / "s.jsonl"
    p.write_bytes(cases(spaced=spaced)[name])
    assert tr.parse_transcript(p, home=HOME) == reference_parse(p, home=HOME)


def test_user_rows_are_decoded_only_when_they_can_matter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows: list[dict[str, Any]] = [prompt("go", 0)]
    for n in range(1, 21):
        rows += [call(f"t{n}", "Read", 2 * n, file_path=f"{REPO}/f{n}.py"), result(f"t{n}", 2 * n + 1)]
    rows += [say("done", 50), prompt("thanks", 51)]
    p = tmp_path / "s.jsonl"
    p.write_bytes(jsonl(*rows))
    decoded: list[str] = []
    real = tr._decode

    def counting(line: bytes) -> dict[str, Any] | None:
        obj = real(line)
        decoded.append(str((obj or {}).get("type")))
        return obj

    monkeypatch.setattr(tr, "_decode", counting)
    info = tr.parse_transcript(p, home=HOME)
    assert decoded == ["assistant"] * 21 + ["user"]  # tool results before the last assistant row are never decoded
    assert info == reference_parse(p, home=HOME)


def test_an_assistant_row_without_a_timestamp_takes_the_last_user_row_timestamp(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_bytes(
        jsonl(prompt("go", 0), call("t1", "Read", 1, file_path=f"{REPO}/a.py"), result("t1", 2), without_timestamp(say("done", 3)))
    )
    assert tr.parse_transcript(p, home=HOME).last_turn.at == "2026-09-08T10:00:02Z"
```

- [ ] **Step 4: Run the tests to verify which fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_transcript_exact.py -q`

Expected:
- Every `test_full_parse_matches_the_plan_1_parser[...]` case passes, because plan 1's parser *is* the reference. This confirms the cases and the reference agree before the rewrite.
- `test_user_rows_are_decoded_only_when_they_can_matter` fails with `AttributeError: <module 'scout.sessions.transcript'> has no attribute '_decode'`.
- The timestamp test passes.

- [ ] **Step 5: Make the test helper write compact JSON, as Claude Code does**

In `engine/tests/unit/sessions_helpers.py`, change `write_transcript`'s write line and add a docstring:

```python
def write_transcript(
    home: Path, encoded_dir: str, uuid: str, rows: list[dict[str, Any]], *, mtime_ago_hours: float = 1.0
) -> Path:
    """Write rows the way Claude Code does: one compact JSON object per line."""
    p = home / "projects" / encoded_dir / f"{uuid}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n", encoding="utf-8")
    ts = (datetime.now(tz=UTC) - timedelta(hours=mtime_ago_hours)).timestamp()
    os.utime(p, (ts, ts))
    return p
```

- [ ] **Step 6: Rewrite `engine/scout/sessions/transcript.py`**

Replace the whole file with the following. The extractors behave as before; `extract_first_message` is split so the parse can run it over bytes it already read. The cache section and `transcript_info` are unchanged from plan 1.

```python
"""Transcript facts (spec §4.5): one forward pass over the bytes, plus the mtime-keyed cache.

``extract_first_message`` / ``extract_files_touched`` moved here verbatim from
``scout.scripts.cc_session_cache`` (that module re-exports them).
"""

from __future__ import annotations

import io
import json
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from scout.sessions._atomic import atomic_write_text
from scout.sessions.model import LastTurn, TranscriptInfo, dt_to_iso, parse_iso

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


def _first_message(lines: Iterable[str]) -> str:
    for i, raw in enumerate(lines):
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
                    raw_text = part.get("text")
                    if not isinstance(raw_text, str):
                        continue  # malformed part (e.g. a dict) — skip it, keep looking
                    text = raw_text[:_FIRST_MSG_MAX_CHARS]
                    if text:
                        return text
        elif isinstance(content, str) and content.strip():
            return content[:_FIRST_MSG_MAX_CHARS]
    return "(could not extract first message)"


def extract_first_message(jsonl_path: Path) -> str:
    """Return the first user-typed prompt from a CC JSONL (first 50 lines, 500 chars)."""
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            return _first_message(f)
    except OSError:
        return "(parse error)"


def _first_message_of(data: bytes) -> str:
    """``extract_first_message`` over bytes already read, decoded exactly as ``open(..., "r")`` decodes."""
    return _first_message(io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="replace"))


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


# ----- one forward pass (1b spec §3.4) -------------------------------------------------
#
# The pass reads bytes. A line is decoded only when it can change a published fact, and
# the file-path scan still runs on every line. Claude Code writes compact JSON, so a user
# row carries the bytes "type":"user". Such a row is held undecoded until it can matter:
# it is released when it is among the rows after the final assistant row, or when the
# next assistant row has no usable timestamp. Assistant rows, and anything unusual, are
# decoded at once and dispatched on their real type, exactly as plan 1 did. The result
# is exact because a tool result always follows its tool call, and a row's own "type"
# is never written with escapes.


def _home_prefix(home: Path | None) -> str:
    return str(home or Path.home()) + "/"


def _lines(chunk: bytes) -> list[bytes]:
    """Split like text mode's universal newlines: ``\\r\\n``, ``\\r`` and ``\\n`` each end a line."""
    if b"\r" in chunk:
        chunk = chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return chunk.split(b"\n")


def _valid_ts(ts: Any) -> bool:
    return isinstance(ts, str) and parse_iso(ts) is not None


def _decode(line: bytes) -> dict[str, Any] | None:
    try:
        obj = json.loads(line.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _blocks(obj: dict[str, Any]) -> list[dict[str, Any]]:
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


@dataclass
class _State:
    """What a forward pass knows about the lines it has consumed."""

    files: set[str] = field(default_factory=set)
    tool_calls: int = 0
    last_assistant: str | None = None  # the last assistant row's own kind: tool_use | question | end_turn
    pending: set[str] = field(default_factory=set)  # its AskUserQuestion ids with no tool result yet
    last_ts: str | None = None  # the last user/assistant row timestamp that parses
    held: list[bytes] = field(default_factory=list)  # user rows after the last assistant row, undecoded

    def copy(self) -> _State:
        return replace(self, files=set(self.files), pending=set(self.pending), held=list(self.held))


def _assistant_kind(blocks: list[dict[str, Any]], tool_uses: list[dict[str, Any]]) -> str:
    """An assistant row's kind before any answer arrives: plan 1's rule minus the pending-question check."""
    if tool_uses:
        return "tool_use"
    texts = [t for b in blocks if b.get("type") == "text" and isinstance(t := b.get("text"), str)]
    return "question" if texts and texts[-1].rstrip().endswith("?") else "end_turn"


def _apply_user(state: _State, obj: dict[str, Any]) -> None:
    if _valid_ts(obj.get("timestamp")):
        state.last_ts = obj["timestamp"]
    for b in _blocks(obj):
        if b.get("type") == "tool_result" and b.get("tool_use_id") is not None:
            state.pending.discard(str(b["tool_use_id"]))


def _release(state: _State) -> None:
    """Decode the held user rows, oldest first, and apply them."""
    held, state.held = state.held, []
    for line in held:
        obj = _decode(line)
        if obj is not None and obj.get("type") == "user":
            _apply_user(state, obj)


def _apply(state: _State, obj: dict[str, Any] | None) -> None:
    if obj is None:
        return
    kind = obj.get("type")
    if kind == "assistant":
        if _valid_ts(obj.get("timestamp")):
            state.held.clear()  # every held row is older, so none can hold the last timestamp
            state.last_ts = obj["timestamp"]
        else:
            _release(state)  # the last usable timestamp may be on a held row
        blocks = _blocks(obj)
        tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
        state.tool_calls += len(tool_uses)
        state.pending = {str(b.get("id")) for b in tool_uses if b.get("name") == "AskUserQuestion"}
        state.last_assistant = _assistant_kind(blocks, tool_uses)
    elif kind == "user":
        _release(state)  # held rows come first in file order
        _apply_user(state, obj)


def _feed(state: _State, line: bytes, home_prefix: str) -> None:
    if b'"file_path"' in line:
        for m in _FILE_PATH_LINE_RE.finditer(line.decode("utf-8", "replace")):
            p = m.group(1)
            if _FILES_NOISE_RE.search(p):
                continue
            if p.startswith(home_prefix):
                p = "~/" + p[len(home_prefix) :]
            state.files.add(p)
    is_assistant = b'"assistant"' in line
    if not is_assistant and b'"user"' not in line and b'"tool_use"' not in line:
        return  # plan 1 never decoded these either
    if not is_assistant and b'"type":"user"' in line:
        state.held.append(line)
        return
    _apply(state, _decode(line))


def _consume(state: _State, data: bytes, home_prefix: str) -> tuple[int, bytes]:
    """Feed every complete line of *data*, then release held rows.

    Returns (bytes consumed, the trailing partial line). A partial line is not consumed.
    """
    end = data.rfind(b"\n") + 1
    for line in _lines(data[:end]):
        _feed(state, line, home_prefix)
    _release(state)
    return end, data[end:]


def _info(
    path: Path, st: os.stat_result, state: _State, partial: bytes, first_prompt: str, home_prefix: str
) -> TranscriptInfo:
    """The published facts: the consumed state plus the trailing partial line, which plan 1 read too."""
    view = state
    if partial:
        view = state.copy()
        for line in _lines(partial):
            _feed(view, line, home_prefix)
        _release(view)
    at = parse_iso(view.last_ts)
    kind = "unknown" if view.last_assistant is None else ("question" if view.pending else view.last_assistant)
    return TranscriptInfo(
        path=str(path),
        first_prompt=first_prompt,
        files_touched=sorted(view.files)[:_MAX_FILES_TOUCHED],
        tool_calls=view.tool_calls,
        last_turn=LastTurn(at=dt_to_iso(at) if at else None, kind=kind),
        mtime_ns=st.st_mtime_ns,
    )


def _unreadable(path: Path, st: os.stat_result) -> TranscriptInfo:
    """Plan 1 kept going with no facts when a transcript could not be read after its stat."""
    return TranscriptInfo(
        path=str(path),
        first_prompt=extract_first_message(path),
        files_touched=[],
        tool_calls=0,
        last_turn=LastTurn(at=None, kind="unknown"),
        mtime_ns=st.st_mtime_ns,
    )


def parse_transcript(path: Path, *, st: os.stat_result | None = None, home: Path | None = None) -> TranscriptInfo:
    """The full parse: first prompt, files touched, tool-call count, last-turn shape.

    Raises OSError if the transcript cannot be stat'ed (e.g. it vanished mid-scan); callers
    record a SourceError per file and continue.
    """
    stat = st or path.stat()
    prefix = _home_prefix(home)
    try:
        with path.open("rb") as f:
            data = f.read(stat.st_size)
    except OSError:
        return _unreadable(path, stat)
    state = _State()
    _, partial = _consume(state, data, prefix)
    return _info(path, stat, state, partial, _first_message_of(data), prefix)


# ----- cache -------------------------------------------------------------------


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _cached_entry(payload: dict[str, Any]) -> TranscriptInfo | None:
    """Rebuild one cache entry, or None when any field is missing or has the wrong type."""
    lt = payload.get("last_turn")
    files = payload.get("files_touched")
    if not (
        isinstance(payload.get("path"), str)
        and isinstance(payload.get("first_prompt"), str)
        and isinstance(files, list)
        and all(isinstance(x, str) for x in files)
        and _is_int(payload.get("tool_calls"))
        and isinstance(lt, dict)
        and isinstance(lt.get("kind"), str)
        and (lt.get("at") is None or isinstance(lt.get("at"), str))
        and _is_int(payload.get("mtime_ns"))
    ):
        return None
    return TranscriptInfo(
        path=payload["path"],
        first_prompt=payload["first_prompt"],
        files_touched=list(files),
        tool_calls=payload["tool_calls"],
        last_turn=LastTurn(at=lt.get("at"), kind=lt["kind"]),
        mtime_ns=payload["mtime_ns"],
    )


def load_transcript_cache(cache_path: Path) -> dict[str, TranscriptInfo]:
    """Load the cache; entries with a missing or wrongly-typed field are skipped (re-parsed later)."""
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
        entry = _cached_entry(payload)
        if entry is not None:
            out[key] = entry
    return out


def write_transcript_cache(cache_path: Path, entries: dict[str, TranscriptInfo]) -> None:
    """Atomically replace the cache file (unique temp + ``os.replace``). Best-effort — never raises."""
    try:
        atomic_write_text(cache_path, json.dumps({k: asdict(v) for k, v in entries.items()}))
    except OSError:
        pass


def transcript_info(path: Path, *, cache: dict[str, TranscriptInfo], home: Path | None = None) -> TranscriptInfo:
    """Cached lookup keyed by path; re-parses only when ``mtime_ns`` changed.

    Raises OSError if the transcript cannot be stat'ed (e.g. it vanished mid-scan); callers
    record a SourceError per file and continue.
    """
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

- [ ] **Step 7: Run the transcript and index tests**

Run: `.venv/bin/pytest tests/unit/test_sessions_transcript.py tests/unit/test_sessions_transcript_edges.py tests/unit/test_sessions_transcript_exact.py tests/unit/test_sessions_index.py tests/unit/test_cc_session_cache.py tests/unit/test_sessions_cli_home.py -q`
Expected: PASS, including every exactness case in both serialisations.

- [ ] **Step 8: Run the one-off equivalence check on real transcripts**

Create `$SCRATCH/transcript_equivalence.py`. Do not commit it:

```python
"""One-off exactness check (1b spec §3.4): the new full parse against plan 1's on every real transcript.

Read-only over ~/.claude/projects. Prints counts, timings and mismatching field names —
never transcript content.

usage, from the worktree's engine/: .venv/bin/python "$SCRATCH/transcript_equivalence.py"
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))  # engine/, so `tests` imports

from scout.sessions.transcript import parse_transcript  # noqa: E402
from tests.unit.sessions_reference_parse import reference_parse  # noqa: E402

files = sorted((Path.home() / ".claude" / "projects").glob("*/*.jsonl"))
mismatched: Counter[str] = Counter()
compared = growing = 0
t_old = t_new = 0.0
for p in files:
    for _attempt in range(3):
        st = p.stat()
        t0 = time.perf_counter()
        try:
            old: object = asdict(reference_parse(p, st=st))
        except Exception as exc:
            old = type(exc).__name__
        t1 = time.perf_counter()
        try:
            new: object = asdict(parse_transcript(p, st=st))
        except Exception as exc:
            new = type(exc).__name__
        t2 = time.perf_counter()
        if p.stat().st_size == st.st_size:
            break
    else:
        growing += 1
        continue
    compared += 1
    t_old += t1 - t0
    t_new += t2 - t1
    if isinstance(old, dict) and isinstance(new, dict):
        for name in old:
            if old[name] != new[name]:
                mismatched[name] += 1
    elif old != new:
        mismatched["exception"] += 1
print(f"transcripts={len(files)} compared={compared} still-growing={growing}")
print(f"plan-1 parse {t_old:.2f}s  new parse {t_new:.2f}s")
print(f"mismatched fields: {dict(mismatched) or 'none'}")
```

Run: `.venv/bin/python "$SCRATCH/transcript_equivalence.py"`

Expected: `mismatched fields: none`. Record the three output lines for the PR.

If any field mismatches:
- Find the offending file locally, but do not paste its content anywhere.
- Work out which row shape differs.
- Add a synthetic case with that shape to `sessions_transcript_cases.py`. It must fail against the new parser.
- Fix the parser and re-run both the tests and this script.

- [ ] **Step 9: Run the gates and commit**

```bash
.venv/bin/ruff format scout tests && .venv/bin/ruff check --fix scout tests && .venv/bin/mypy scout
.venv/bin/pytest tests/ -q
git add scout/sessions/transcript.py tests/unit/sessions_reference_parse.py tests/unit/sessions_transcript_cases.py tests/unit/test_sessions_transcript_exact.py tests/unit/sessions_helpers.py
git commit -m "perf(sessions): full transcript parse decodes only rows that can matter

The pass works on bytes; user rows are held undecoded and released only when
they can change the last timestamp or answer the final question. Plan 1's
parser is frozen under tests/ as the reference; every synthetic case matches it
in compact and spaced serialisations.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Transcript checkpoints and cache version 2

**Files:**
- Rewrite: `engine/scout/sessions/transcript.py` (the whole file; the extractors and the forward pass are the same as in Task 3)
- Modify: `engine/scout/sessions/index.py` (the `transcript_info` call passes `stats`)
- Modify: `engine/tests/unit/test_sessions_transcript.py`, `engine/tests/unit/test_sessions_index.py`
- Create: `engine/tests/unit/test_sessions_transcript_incremental.py`
- Create (not committed): `$SCRATCH/tail_equivalence.py`

**Interfaces:**
- Consumes the Task 3 internals: `_State`, `_consume`, `_info`, `_feed`, `_release`, `_first_message_of`, `_home_prefix`, `_unreadable`, `extract_first_message`. Also `BuildStats` from Task 1.
- Produces:
  - `Checkpoint`, a dataclass with these fields:
    - `dev`, `ino`, `size`, `mtime_ns`, `offset` and `lines` (ints);
    - `head_sha1: str` and `first_prompt_final: bool`;
    - `files_smallest: list[str]`, `tool_calls: int` and `last_assistant: str | None`;
    - `pending_questions: list[str]` and `last_ts: str | None`.
  - `CachedTranscript(info: TranscriptInfo, checkpoint: Checkpoint | None)`.
  - `transcript_info(path, *, cache: dict[str, CachedTranscript], home=None, stats: BuildStats | None = None) -> TranscriptInfo`.
  - `load_transcript_cache(path) -> dict[str, CachedTranscript]` and `write_transcript_cache(path, entries: dict[str, CachedTranscript]) -> bool`.
  - `_full(path, st, home_prefix, stats) -> CachedTranscript` and `_tail(path, st, cp, first_prompt, home_prefix, stats) -> CachedTranscript | None`.
  - `_route(prior: CachedTranscript | None, st) -> str`, which returns `"unchanged" | "tail" | "full"`. Task 7 reuses it.
  - The cache file format is `{"version": 2, "entries": {path: {"info": {…}, "checkpoint": {…} | null}}}`.

- [ ] **Step 1: Write the failing incremental tests**

Create `engine/tests/unit/test_sessions_transcript_incremental.py`:

```python
"""Checkpointed transcript parsing (1b spec §3.3): unchanged, grown and rewritten transcripts.

Every result is checked against the frozen plan 1 parser on the file as it stands.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scout.sessions import transcript as tr
from scout.sessions.model import TranscriptInfo
from scout.sessions.stats import BuildStats
from scout.sessions.transcript import CachedTranscript, transcript_info
from tests.unit.sessions_reference_parse import reference_parse
from tests.unit.sessions_transcript_cases import HOME, REPO, call, cases, jsonl, prompt, result, say

BASE_NS = 1_788_800_000_000_000_000


def _write(p: Path, data: bytes, step: int) -> None:
    """Write (step 0) or append, then move the mtime forward so every step is a visible change."""
    if step == 0:
        p.write_bytes(data)
    else:
        with p.open("ab") as f:
            f.write(data)
    t = BASE_NS + step * 1_000_000_000
    os.utime(p, ns=(t, t))


def _lookup(p: Path, cache: dict[str, CachedTranscript]) -> tuple[TranscriptInfo, BuildStats]:
    stats = BuildStats()
    return transcript_info(p, cache=cache, home=HOME, stats=stats), stats


def test_an_unchanged_transcript_is_not_read_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "s.jsonl"
    _write(p, jsonl(prompt("go", 0), say("done", 1)), 0)
    cache: dict[str, CachedTranscript] = {}
    first, cold = _lookup(p, cache)
    assert (cold.transcripts_full_parsed, cold.transcript_bytes_read) == (1, p.stat().st_size)

    def no_reads(*_a: object, **_k: object) -> object:
        raise AssertionError("an unchanged transcript was opened")

    monkeypatch.setattr(Path, "open", no_reads)
    again, warm = _lookup(p, cache)
    assert again == first and warm == BuildStats()


def test_appended_rows_are_parsed_from_the_checkpoint(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    _write(p, jsonl(prompt("go", 0), call("t1", "Read", 1, file_path=f"{REPO}/a.py")), 0)
    cache: dict[str, CachedTranscript] = {}
    _lookup(p, cache)
    more = jsonl(result("t1", 2), call("t2", "Edit", 3, file_path=f"{REPO}/b.py"), result("t2", 4), say("Done.", 5))
    _write(p, more, 1)
    info, stats = _lookup(p, cache)
    assert (stats.transcripts_full_parsed, stats.transcripts_tail_parsed) == (0, 1)
    assert stats.transcript_bytes_read == len(more)
    assert info == reference_parse(p, home=HOME)
    assert info.tool_calls == 2 and info.files_touched == ["~/code/example-repo/a.py", "~/code/example-repo/b.py"]


def test_a_half_written_last_line_is_read_whole_on_the_next_build(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    line = jsonl(call("t2", "Read", 3, file_path=f"{REPO}/late.py"))
    head, tail = line[: len(line) // 2], line[len(line) // 2 :]
    _write(p, jsonl(prompt("go", 0), say("thinking", 1)) + head, 0)
    cache: dict[str, CachedTranscript] = {}
    info, _ = _lookup(p, cache)
    assert info == reference_parse(p, home=HOME) and info.tool_calls == 0
    cp = cache[str(p)].checkpoint
    assert cp is not None and cp.offset == p.stat().st_size - len(head)  # the torn line is not consumed

    _write(p, tail, 1)
    info, stats = _lookup(p, cache)
    assert stats.transcripts_tail_parsed == 1 and stats.transcript_bytes_read == len(head) + len(tail)
    assert info == reference_parse(p, home=HOME)
    assert info.tool_calls == 1 and info.last_turn.kind == "tool_use"


def test_a_question_answered_in_a_later_tail(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    _write(p, jsonl(prompt("go", 0), call("q1", "AskUserQuestion", 1, questions=[])), 0)
    cache: dict[str, CachedTranscript] = {}
    assert _lookup(p, cache)[0].last_turn.kind == "question"
    _write(p, jsonl(result("q1", 30)), 1)
    info, stats = _lookup(p, cache)
    assert stats.transcripts_tail_parsed == 1 and info.last_turn.kind == "tool_use"
    assert info == reference_parse(p, home=HOME)


@pytest.mark.parametrize("change", ["truncated", "replaced", "head_rewritten", "touched"])
def test_anything_but_an_append_is_parsed_again_in_full(tmp_path: Path, change: str) -> None:
    p = tmp_path / "s.jsonl"
    first_row = jsonl(prompt("go", 0))
    original = first_row + jsonl(call("t1", "Read", 1, file_path=f"{REPO}/a.py"), result("t1", 2))
    _write(p, original, 0)
    cache: dict[str, CachedTranscript] = {}
    _lookup(p, cache)
    if change == "truncated":
        p.write_bytes(original[: len(original) // 2])
    elif change == "replaced":  # a different file at the same path, larger than before
        other = tmp_path / "other.jsonl"
        other.write_bytes(jsonl(prompt("something else", 0)) + original)
        os.replace(other, p)
    elif change == "head_rewritten":  # the same file, grown, but its first bytes changed
        p.write_bytes(jsonl(prompt("GO", 0)) + original[len(first_row) :] + jsonl(say("done", 3)))
    t = BASE_NS + 5_000_000_000  # "touched": same bytes, new mtime
    os.utime(p, ns=(t, t))
    info, stats = _lookup(p, cache)
    assert (stats.transcripts_full_parsed, stats.transcripts_tail_parsed) == (1, 0)
    assert info == reference_parse(p, home=HOME)


def test_a_failing_tail_parse_falls_back_to_a_full_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "s.jsonl"
    _write(p, jsonl(prompt("go", 0), say("thinking", 1)), 0)
    cache: dict[str, CachedTranscript] = {}
    _lookup(p, cache)
    _write(p, jsonl(say("done", 2)), 1)

    def broken(*_a: object, **_k: object) -> None:
        raise ValueError("unexpected checkpoint")

    monkeypatch.setattr(tr, "_tail", broken)
    info, stats = _lookup(p, cache)
    assert (stats.transcripts_full_parsed, stats.transcripts_tail_parsed) == (1, 0)
    assert info == reference_parse(p, home=HOME)


def test_the_first_prompt_is_found_once_its_line_is_complete(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    line = jsonl(prompt("the real ask", 0))
    _write(p, jsonl({"type": "custom-title", "customTitle": "scratch"}) + line[:10], 0)
    cache: dict[str, CachedTranscript] = {}
    assert "could not extract" in _lookup(p, cache)[0].first_prompt
    _write(p, line[10:] + jsonl(say("ok", 1)), 1)
    info, _ = _lookup(p, cache)
    assert info.first_prompt == "the real ask" and info == reference_parse(p, home=HOME)
    cp = cache[str(p)].checkpoint
    assert cp is not None and cp.first_prompt_final


def test_the_first_prompt_is_final_after_fifty_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "s.jsonl"
    _write(p, jsonl(*({"type": "system", "content": f"note {n}"} for n in range(55))), 0)
    cache: dict[str, CachedTranscript] = {}
    _lookup(p, cache)
    cp = cache[str(p)].checkpoint
    assert cp is not None and cp.first_prompt_final

    def not_again(_path: Path) -> str:
        raise AssertionError("the head was read again for a first prompt that is final")

    monkeypatch.setattr(tr, "extract_first_message", not_again)
    _write(p, jsonl(prompt("too late", 0)), 1)
    info, _ = _lookup(p, cache)
    assert "could not extract" in info.first_prompt
    assert info == reference_parse(p, home=HOME)  # the reference has its own extractor


def _split_points(data: bytes) -> list[int]:
    """Every line boundary, the middle of every line, and between every \\r\\n pair."""
    points: set[int] = set()
    start = 0
    while start < len(data):
        nl = data.find(b"\n", start)
        end = len(data) if nl == -1 else nl + 1
        points.update({start + (end - start) // 2, end})
        start = end
    points.update(i + 1 for i in range(len(data) - 1) if data[i : i + 2] == b"\r\n")
    return sorted(x for x in points if 0 < x < len(data))


@pytest.mark.parametrize("spaced", [False, True], ids=["compact", "spaced"])
@pytest.mark.parametrize("name", sorted(cases(spaced=False)))
def test_tail_parses_in_steps_equal_one_full_parse(tmp_path: Path, name: str, spaced: bool) -> None:
    data = cases(spaced=spaced)[name]
    p = tmp_path / "s.jsonl"
    for cut in _split_points(data):
        mid = (cut + len(data)) // 2
        pieces = [x for x in (data[:cut], data[cut:mid], data[mid:]) if x]
        cache: dict[str, CachedTranscript] = {}
        tails = 0
        for step, piece in enumerate(pieces):
            _write(p, piece, step)
            info, stats = _lookup(p, cache)
            tails += stats.transcripts_tail_parsed
        assert tails == len(pieces) - 1, (name, cut)
        assert info == reference_parse(p, home=HOME), (name, cut)
```

- [ ] **Step 2: Update the cache tests in `engine/tests/unit/test_sessions_transcript.py`**

Change the imports to:

```python
from scout.sessions.model import LastTurn, TranscriptInfo
from scout.sessions.transcript import (
    TRANSCRIPT_CACHE_FILENAME,
    CachedTranscript,
    extract_files_touched,
    extract_first_message,
    load_transcript_cache,
    parse_transcript,
    transcript_info,
    write_transcript_cache,
)
```

Replace `test_transcript_cache_round_trip_and_mtime_reuse`, `test_load_transcript_cache_missing_or_corrupt_is_empty`, `test_load_transcript_cache_skips_wrongly_typed_entries` and `_entry` with:

```python
def test_transcript_cache_round_trip_and_unchanged_reuse(tmp_path: Path) -> None:
    p = write_transcript(
        claude_home(), "-Users-alex-code-example-repo", U1, [_user("warm one", "2026-09-08T10:00:00.000Z")]
    )
    cache: dict[str, CachedTranscript] = {}
    first = transcript_info(p, cache=cache)
    assert first.first_prompt == "warm one" and cache[str(p)].checkpoint is not None

    cache_path = tmp_path / TRANSCRIPT_CACHE_FILENAME
    assert write_transcript_cache(cache_path, cache) is True
    reloaded = load_transcript_cache(cache_path)
    assert reloaded == cache

    # Same file, size and mtime but different bytes: served from the cache without reading.
    st = p.stat()
    p.write_bytes(b"x" * (st.st_size - 1) + b"\n")
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert transcript_info(p, cache=reloaded).first_prompt == "warm one"

    # A new mtime at the same size is not an append: full re-parse.
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert "could not extract" in transcript_info(p, cache=reloaded).first_prompt


@pytest.mark.parametrize(
    "text",
    [
        "[1,2",
        "[]",
        '{"version": 1, "entries": {}}',
        '{"version": true, "entries": {}}',
        '{"version": 2, "entries": []}',
    ],
)
def test_a_transcript_cache_of_another_version_or_shape_is_empty(tmp_path: Path, text: str) -> None:
    path = tmp_path / TRANSCRIPT_CACHE_FILENAME
    path.write_text(text, encoding="utf-8")
    assert load_transcript_cache(path) == {}


def test_a_missing_transcript_cache_is_empty(tmp_path: Path) -> None:
    assert load_transcript_cache(tmp_path / "nope.json") == {}


_GOOD_INFO = {
    "path": "/Users/alex/.claude/projects/-Users-alex-code-example-repo/good.jsonl",
    "first_prompt": "hi",
    "files_touched": ["~/code/example-repo/a.py"],
    "tool_calls": 2,
    "last_turn": {"at": "2026-09-08T10:00:00Z", "kind": "end_turn"},
    "mtime_ns": 5,
}


def test_a_plan_1_transcript_cache_is_empty(tmp_path: Path) -> None:
    path = tmp_path / TRANSCRIPT_CACHE_FILENAME
    path.write_text(json.dumps({_GOOD_INFO["path"]: _GOOD_INFO}), encoding="utf-8")  # plan 1's flat layout
    assert load_transcript_cache(path) == {}


def _checkpoint(**overrides: object) -> dict:
    base: dict = {
        "dev": 1,
        "ino": 2,
        "size": 10,
        "mtime_ns": 5,
        "offset": 10,
        "head_sha1": "0" * 40,
        "lines": 1,
        "first_prompt_final": True,
        "files_smallest": [],
        "tool_calls": 0,
        "last_assistant": None,
        "pending_questions": [],
        "last_ts": None,
    }
    base.update(overrides)
    return base


def test_load_transcript_cache_skips_wrongly_typed_entries(tmp_path: Path) -> None:
    good = _GOOD_INFO
    bad_info = {
        "last_turn_str": {**good, "last_turn": "x"},
        "last_turn_kind_int": {**good, "last_turn": {"at": None, "kind": 3}},
        "last_turn_at_int": {**good, "last_turn": {"at": 5, "kind": "end_turn"}},
        "path_int": {**good, "path": 5},
        "first_prompt_none": {**good, "first_prompt": None},
        "files_not_a_list": {**good, "files_touched": "~/a.py"},
        "files_not_str": {**good, "files_touched": [1]},
        "tool_calls_bool": {**good, "tool_calls": True},
        "tool_calls_str": {**good, "tool_calls": "2"},
        "mtime_bool": {**good, "mtime_ns": True},
        "mtime_float": {**good, "mtime_ns": 5.0},
        "missing_last_turn": {k: v for k, v in good.items() if k != "last_turn"},
        "info_not_a_dict": "x",
    }
    bad_checkpoints = {
        "cp_not_a_dict": "x",
        "cp_offset_past_size": _checkpoint(offset=11),
        "cp_negative_offset": _checkpoint(offset=-1),
        "cp_dev_bool": _checkpoint(dev=True),
        "cp_sha_int": _checkpoint(head_sha1=5),
        "cp_final_int": _checkpoint(first_prompt_final=1),
        "cp_too_many_files": _checkpoint(files_smallest=[f"f{n}" for n in range(11)]),
        "cp_pending_not_str": _checkpoint(pending_questions=[1]),
        "cp_unknown_kind": _checkpoint(last_assistant="thinking"),
        "cp_ts_int": _checkpoint(last_ts=5),
        "cp_missing_last_ts": {k: v for k, v in _checkpoint().items() if k != "last_ts"},
    }
    entries: dict = {
        "good": {"info": good, "checkpoint": _checkpoint()},
        "good_unread": {"info": good, "checkpoint": None},
        "no_checkpoint_key": {"info": good},
    }
    entries.update({k: {"info": v, "checkpoint": _checkpoint()} for k, v in bad_info.items()})
    entries.update({k: {"info": good, "checkpoint": v} for k, v in bad_checkpoints.items()})
    path = tmp_path / TRANSCRIPT_CACHE_FILENAME
    path.write_text(json.dumps({"version": 2, "entries": entries}), encoding="utf-8")
    loaded = load_transcript_cache(path)
    assert set(loaded) == {"good", "good_unread"}
    assert loaded["good"].info.tool_calls == 2 and loaded["good_unread"].checkpoint is None


def _entry(prompt: str) -> CachedTranscript:
    info = TranscriptInfo(
        path=f"/Users/alex/.claude/projects/-x/{prompt}.jsonl",
        first_prompt=prompt,
        files_touched=[],
        tool_calls=0,
        last_turn=LastTurn(at=None, kind="end_turn"),
        mtime_ns=1,
    )
    return CachedTranscript(info=info, checkpoint=None)
```

In `test_write_transcript_cache_never_raises_when_parent_is_a_file`, assert on the new return value:

```python
    assert write_transcript_cache(blocker / TRANSCRIPT_CACHE_FILENAME, {}) is False  # must not raise
```

- [ ] **Step 3: Update the index tests**

In `engine/tests/unit/test_sessions_index.py`, replace `test_wrongly_typed_transcript_cache_entry_is_ignored` with:

```python
def test_wrongly_typed_transcript_cache_entry_is_ignored(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    a_path = claude_home() / "projects" / (REPO_DIR + "--claude-worktrees-w1") / f"{UA}.jsonl"
    st = a_path.stat()
    info = {
        "path": str(a_path),
        "first_prompt": "stale",
        "files_touched": [],
        "tool_calls": 0,
        "last_turn": "x",  # used to raise AttributeError out of load_transcript_cache
        "mtime_ns": st.st_mtime_ns,
    }
    checkpoint = {  # matches the file exactly: only the bad info keeps the entry from being served
        "dev": st.st_dev,
        "ino": st.st_ino,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "offset": st.st_size,
        "head_sha1": "0" * 40,
        "lines": 2,
        "first_prompt_final": True,
        "files_smallest": [],
        "tool_calls": 0,
        "last_assistant": "end_turn",
        "pending_questions": [],
        "last_ts": None,
    }
    (fake_data_dir / ".scout-cache" / "sessions-transcripts.cache.json").write_text(
        json.dumps({"version": 2, "entries": {str(a_path): {"info": info, "checkpoint": checkpoint}}}),
        encoding="utf-8",
    )
    idx = build_index(opts)
    assert _ids(idx) == ALL_IDS and idx.source_errors == []
    a = next(s for s in idx.sessions if s.id == "local_A")
    assert a.transcript is not None and a.transcript.first_prompt == "fix it"  # re-parsed, not the bad entry


def test_a_plan_1_transcript_cache_is_rebuilt_as_version_2(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    a_path = claude_home() / "projects" / (REPO_DIR + "--claude-worktrees-w1") / f"{UA}.jsonl"
    plan_1 = {
        str(a_path): {
            "path": str(a_path),
            "first_prompt": "stale",
            "files_touched": [],
            "tool_calls": 0,
            "last_turn": {"at": None, "kind": "end_turn"},
            "mtime_ns": a_path.stat().st_mtime_ns,  # an mtime match was all plan 1 needed to serve it
        }
    }
    cache_file = fake_data_dir / ".scout-cache" / "sessions-transcripts.cache.json"
    cache_file.write_text(json.dumps(plan_1), encoding="utf-8")
    stats = BuildStats()
    idx = build_index(opts, stats=stats)
    a = next(s for s in idx.sessions if s.id == "local_A")
    assert a.transcript is not None and a.transcript.first_prompt == "fix it"
    assert stats.transcripts_full_parsed == 3  # A, E and F: the upgrade build is a cold one
    assert json.loads(cache_file.read_text(encoding="utf-8"))["version"] == 2
```

In `test_a_deleted_transcript_drops_out_of_the_transcript_cache`, read the entries from the new envelope. Change the two `set(json.loads(cache_file.read_text(encoding="utf-8")))` expressions to:

```python
set(json.loads(cache_file.read_text(encoding="utf-8"))["entries"])
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_transcript.py tests/unit/test_sessions_transcript_incremental.py tests/unit/test_sessions_index.py -q`
Expected: collection fails with `ImportError: cannot import name 'CachedTranscript'`.

- [ ] **Step 5: Add checkpoints to `engine/scout/sessions/transcript.py`**

Edit the file as Task 3 left it. Everything from `_first_message` through `_unreadable` (the extractors and the forward pass) stays exactly as it is.

1. Replace the module docstring with:

   ```python
   """Transcript facts (spec §4.5): one forward pass, checkpointed so a growing transcript is
   parsed only from where the last build stopped (1b spec §3.3–§3.4), plus the cache.

   ``extract_first_message`` / ``extract_files_touched`` moved here verbatim from
   ``scout.scripts.cc_session_cache`` (that module re-exports them).
   """
   ```

2. Add `import hashlib` as the first standard-library import. Add `from scout.sessions.stats import BuildStats` after the `scout.sessions.model` import.

3. Add these constants after `_FIRST_MSG_MAX_CHARS = 500`, and put `_CACHE_VERSION = 2` directly under `TRANSCRIPT_CACHE_FILENAME`:

   ```python
   _HEAD_CHECK_BYTES = 4096
   _NO_FIRST_MESSAGE = ("(could not extract first message)", "(parse error)")
   _ASSISTANT_KINDS = ("tool_use", "question", "end_turn")
   ```

4. Delete `parse_transcript` and everything after it: plan 1's cache section, `transcript_info` and `__all__`. Put this in their place:

```python
# ----- checkpoints (1b spec §3.3) ------------------------------------------------------


@dataclass
class Checkpoint:
    """Where a forward pass stopped in a file, and what it knew there."""

    dev: int
    ino: int
    size: int  # the file's size and mtime when the checkpoint was taken
    mtime_ns: int
    offset: int  # end of the last complete line; <= size (they differ after a half-written line)
    head_sha1: str  # SHA-1 of the first min(4096, offset) bytes: the grown-same-file check
    lines: int  # newlines before offset
    first_prompt_final: bool
    files_smallest: list[str]  # the 10 alphabetically smallest paths: all a merge needs
    tool_calls: int
    last_assistant: str | None
    pending_questions: list[str]
    last_ts: str | None


@dataclass
class CachedTranscript:
    info: TranscriptInfo
    checkpoint: Checkpoint | None  # None when the file could not be read: always parsed again


def _first_prompt_final(first: str, lines: int, partial: bytes) -> bool:
    """The first prompt can no longer change: 50 complete lines exist, or a prompt was found
    with no half-written line after it (the head of an append-only file is fixed)."""
    return lines >= _HEAD_LINES_FOR_FIRST_MSG or (not partial and first not in _NO_FIRST_MESSAGE)


def _checkpoint(
    state: _State, st: os.stat_result, *, offset: int, head: bytes, lines: int, first_final: bool
) -> Checkpoint:
    return Checkpoint(
        dev=st.st_dev,
        ino=st.st_ino,
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        offset=offset,
        head_sha1=hashlib.sha1(head).hexdigest(),
        lines=lines,
        first_prompt_final=first_final,
        files_smallest=sorted(state.files)[:_MAX_FILES_TOUCHED],
        tool_calls=state.tool_calls,
        last_assistant=state.last_assistant,
        pending_questions=sorted(state.pending),
        last_ts=state.last_ts,
    )


def _resume(cp: Checkpoint) -> _State:
    return _State(
        files=set(cp.files_smallest),
        tool_calls=cp.tool_calls,
        last_assistant=cp.last_assistant,
        pending=set(cp.pending_questions),
        last_ts=cp.last_ts,
    )


def _full(path: Path, st: os.stat_result, home_prefix: str, stats: BuildStats) -> CachedTranscript:
    """Parse from byte 0 and take a fresh checkpoint."""
    try:
        with path.open("rb") as f:
            data = f.read(st.st_size)
    except OSError:
        return CachedTranscript(info=_unreadable(path, st), checkpoint=None)
    stats.transcript_bytes_read += len(data)
    state = _State()
    end, partial = _consume(state, data, home_prefix)
    first = _first_message_of(data)
    lines = data.count(b"\n", 0, end)
    cp = _checkpoint(
        state,
        st,
        offset=end,
        head=data[: min(_HEAD_CHECK_BYTES, end)],
        lines=lines,
        first_final=_first_prompt_final(first, lines, partial),
    )
    return CachedTranscript(info=_info(path, st, state, partial, first, home_prefix), checkpoint=cp)


def _tail(
    path: Path, st: os.stat_result, cp: Checkpoint, first_prompt: str, home_prefix: str, stats: BuildStats
) -> CachedTranscript | None:
    """Parse only what was appended after *cp*; None when the file's head no longer matches."""
    with path.open("rb") as f:
        head = f.read(min(_HEAD_CHECK_BYTES, cp.offset))
        if hashlib.sha1(head).hexdigest() != cp.head_sha1:
            return None
        f.seek(cp.offset)
        data = f.read(st.st_size - cp.offset)
    stats.transcript_bytes_read += len(data)
    state = _resume(cp)
    end, partial = _consume(state, data, home_prefix)
    offset = cp.offset + end
    lines = cp.lines + data.count(b"\n", 0, end)
    if cp.first_prompt_final:
        first, final = first_prompt, True
    else:
        first = extract_first_message(path)
        final = _first_prompt_final(first, lines, partial)
    if len(head) < _HEAD_CHECK_BYTES:  # the identity check grows with the file, up to 4 KB
        head = (head + data)[: min(_HEAD_CHECK_BYTES, offset)]
    return CachedTranscript(
        info=_info(path, st, state, partial, first, home_prefix),
        checkpoint=_checkpoint(state, st, offset=offset, head=head, lines=lines, first_final=final),
    )


def _route(prior: CachedTranscript | None, st: os.stat_result) -> str:
    """unchanged | tail | full, from the file's identity against its checkpoint."""
    cp = prior.checkpoint if prior is not None else None
    if cp is None:
        return "full"
    if (cp.dev, cp.ino, cp.size, cp.mtime_ns) == (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns):
        return "unchanged"
    if (cp.dev, cp.ino) == (st.st_dev, st.st_ino) and st.st_size > cp.size:
        return "tail"
    return "full"


def parse_transcript(path: Path, *, st: os.stat_result | None = None, home: Path | None = None) -> TranscriptInfo:
    """The full parse: first prompt, files touched, tool-call count, last-turn shape.

    Raises OSError if the transcript cannot be stat'ed (e.g. it vanished mid-scan); callers
    record a SourceError per file and continue.
    """
    return _full(path, st or path.stat(), _home_prefix(home), BuildStats()).info


def transcript_info(
    path: Path,
    *,
    cache: dict[str, CachedTranscript],
    home: Path | None = None,
    stats: BuildStats | None = None,
) -> TranscriptInfo:
    """Reuse an unchanged transcript, parse only the appended tail of a grown one, and fully
    parse anything else (1b spec §3.3). Updates *cache* in place.

    Raises OSError if the transcript cannot be stat'ed (e.g. it vanished mid-scan); callers
    record a SourceError per file and continue.
    """
    if stats is None:
        stats = BuildStats()
    st = path.stat()
    key = str(path)
    prior = cache.get(key)
    route = _route(prior, st)
    if route == "unchanged" and prior is not None:
        return prior.info
    home_prefix = _home_prefix(home)
    entry: CachedTranscript | None = None
    if route == "tail" and prior is not None and prior.checkpoint is not None:
        try:
            entry = _tail(path, st, prior.checkpoint, prior.info.first_prompt, home_prefix, stats)
        except Exception:  # 1b spec §4: any failure in a tail parse falls back to a full parse
            entry = None
        if entry is not None:
            stats.transcripts_tail_parsed += 1
    if entry is None:
        entry = _full(path, st, home_prefix, stats)
        stats.transcripts_full_parsed += 1
    cache[key] = entry
    return entry.info


# ----- cache file (version 2) ----------------------------------------------------------


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _cached_info(payload: Any) -> TranscriptInfo | None:
    """Rebuild a cached TranscriptInfo, or None when any field is missing or has the wrong type."""
    if not isinstance(payload, dict):
        return None
    lt = payload.get("last_turn")
    files = payload.get("files_touched")
    if not (
        isinstance(payload.get("path"), str)
        and isinstance(payload.get("first_prompt"), str)
        and isinstance(files, list)
        and all(isinstance(x, str) for x in files)
        and _is_int(payload.get("tool_calls"))
        and isinstance(lt, dict)
        and isinstance(lt.get("kind"), str)
        and (lt.get("at") is None or isinstance(lt.get("at"), str))
        and _is_int(payload.get("mtime_ns"))
    ):
        return None
    return TranscriptInfo(
        path=payload["path"],
        first_prompt=payload["first_prompt"],
        files_touched=list(files),
        tool_calls=payload["tool_calls"],
        last_turn=LastTurn(at=lt.get("at"), kind=lt["kind"]),
        mtime_ns=payload["mtime_ns"],
    )


def _cached_checkpoint(v: Any) -> Checkpoint | None:
    """Rebuild a cached Checkpoint, or None when any field is missing, mistyped or inconsistent."""
    if not isinstance(v, dict) or "last_assistant" not in v or "last_ts" not in v:
        return None
    ints = ("dev", "ino", "size", "mtime_ns", "offset", "lines", "tool_calls")
    if not all(_is_int(v.get(k)) for k in ints) or not 0 <= v["offset"] <= v["size"]:
        return None
    files, pending, last_ts = v.get("files_smallest"), v.get("pending_questions"), v["last_ts"]
    if not (
        isinstance(v.get("head_sha1"), str)
        and isinstance(v.get("first_prompt_final"), bool)
        and isinstance(files, list)
        and len(files) <= _MAX_FILES_TOUCHED
        and all(isinstance(x, str) for x in files)
        and isinstance(pending, list)
        and all(isinstance(x, str) for x in pending)
        and (v["last_assistant"] is None or v["last_assistant"] in _ASSISTANT_KINDS)
        and (last_ts is None or isinstance(last_ts, str))
    ):
        return None
    return Checkpoint(
        dev=v["dev"],
        ino=v["ino"],
        size=v["size"],
        mtime_ns=v["mtime_ns"],
        offset=v["offset"],
        head_sha1=v["head_sha1"],
        lines=v["lines"],
        first_prompt_final=v["first_prompt_final"],
        files_smallest=list(files),
        tool_calls=v["tool_calls"],
        last_assistant=v["last_assistant"],
        pending_questions=list(pending),
        last_ts=last_ts,
    )


def _cached_transcript(v: Any) -> CachedTranscript | None:
    if not isinstance(v, dict) or "checkpoint" not in v:
        return None
    info = _cached_info(v.get("info"))
    if info is None:
        return None
    if v["checkpoint"] is None:
        return CachedTranscript(info=info, checkpoint=None)
    cp = _cached_checkpoint(v["checkpoint"])
    return None if cp is None else CachedTranscript(info=info, checkpoint=cp)


def load_transcript_cache(cache_path: Path) -> dict[str, CachedTranscript]:
    """Load the cache. A missing, corrupt or pre-1b (plan 1) file is empty; a bad entry is skipped."""
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict) or not _is_int(raw.get("version")) or raw["version"] != _CACHE_VERSION:
        return {}
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        return {}
    out: dict[str, CachedTranscript] = {}
    for key, value in entries.items():
        entry = _cached_transcript(value)
        if entry is not None:
            out[key] = entry
    return out


def write_transcript_cache(cache_path: Path, entries: dict[str, CachedTranscript]) -> bool:
    """Atomically replace the cache file (unique temp + ``os.replace``). Best-effort: False instead of raising."""
    payload = {"version": _CACHE_VERSION, "entries": {k: asdict(v) for k, v in entries.items()}}
    try:
        atomic_write_text(cache_path, json.dumps(payload))
    except OSError:
        return False
    return True


__all__ = [
    "TRANSCRIPT_CACHE_FILENAME",
    "CachedTranscript",
    "Checkpoint",
    "extract_files_touched",
    "extract_first_message",
    "load_transcript_cache",
    "parse_transcript",
    "transcript_info",
    "write_transcript_cache",
]
```

The public names keep their plan-1 signatures, except for three:
- `transcript_info` gains `stats`;
- the two cache functions now carry `CachedTranscript` values;
- `write_transcript_cache` returns a `bool`.

`parse_transcript` is now a thin wrapper over `_full`.

- [ ] **Step 6: Pass the collector from `engine/scout/sessions/index.py`**

In step 3 of `build_index`, change the call:

```python
                    sess.transcript = transcript_info(tpath, cache=tcache, stats=stats)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_transcript.py tests/unit/test_sessions_transcript_edges.py tests/unit/test_sessions_transcript_exact.py tests/unit/test_sessions_transcript_incremental.py tests/unit/test_sessions_index.py tests/perf -q`
Expected: PASS. The old perf test still passes: `load_transcript_cache` returns one entry per transcript.

- [ ] **Step 8: Run the one-off tail equivalence check on real transcripts**

Create `$SCRATCH/tail_equivalence.py`. Do not commit it:

```python
"""One-off (1b spec §3.3): for every real transcript, a checkpoint taken part-way through and a
tail parse to the end equal one full parse — info and checkpoint both.

Read-only over ~/.claude/projects. Prints counts and mismatching field names, never content.

usage, from the worktree's engine/: .venv/bin/python "$SCRATCH/tail_equivalence.py"
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path.cwd()))  # engine/: this worktree's code, not whatever the venv last installed

from scout.sessions import transcript as tr  # noqa: E402
from scout.sessions.stats import BuildStats  # noqa: E402

prefix = str(Path.home()) + "/"
files = sorted((Path.home() / ".claude" / "projects").glob("*/*.jsonl"))
mismatched: Counter[str] = Counter()
checked = growing = head_mismatch = 0
for p in files:
    st = p.stat()
    if st.st_size < 2:
        continue
    for cut in (st.st_size // 3, st.st_size // 2, st.st_size - 1):
        # The file as it was when it was only `cut` bytes long (only these four fields are read).
        early = SimpleNamespace(st_dev=st.st_dev, st_ino=st.st_ino, st_size=cut, st_mtime_ns=st.st_mtime_ns - 1)
        prior = tr._full(p, early, prefix, BuildStats())  # type: ignore[arg-type]
        if prior.checkpoint is None:
            continue
        grown = tr._tail(p, st, prior.checkpoint, prior.info.first_prompt, prefix, BuildStats())
        whole = tr._full(p, st, prefix, BuildStats())
        if p.stat().st_size != st.st_size:
            growing += 1
            break
        if grown is None:
            head_mismatch += 1
            continue
        checked += 1
        for part in ("info", "checkpoint"):
            a, b = asdict(grown)[part], asdict(whole)[part]
            for name in a:
                if a[name] != b[name]:
                    mismatched[f"{part}.{name}"] += 1
print(f"transcripts={len(files)} tail-checks={checked} still-growing={growing} head-mismatch={head_mismatch}")
print(f"mismatched fields: {dict(mismatched) or 'none'}")
```

Run: `.venv/bin/python "$SCRATCH/tail_equivalence.py"`

Expected: `head-mismatch=0` and `mismatched fields: none`. Record both lines for the PR. Handle mismatches the same way as in Task 3, Step 8.

- [ ] **Step 9: Run the gates and commit**

```bash
.venv/bin/ruff format scout tests && .venv/bin/ruff check --fix scout tests && .venv/bin/mypy scout
.venv/bin/pytest tests/ -q
git add scout/sessions/transcript.py scout/sessions/index.py tests/unit/test_sessions_transcript.py tests/unit/test_sessions_transcript_incremental.py tests/unit/test_sessions_index.py
git commit -m "perf(sessions): parse a growing transcript from its checkpoint

The transcript cache moves to version 2: each entry keeps the published facts
plus a checkpoint (file identity, where the last complete line ended, a 4 KB
head hash and the pass state). Unchanged transcripts are not read, grown ones
are parsed from the checkpoint, anything else in full. A plan-1 cache reads as
empty, so the first build after upgrading is cold.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Write only the caches that changed

**Files:**
- Modify: `engine/scout/sessions/index.py` (the end of `build_index`)
- Modify: `engine/scout/sessions/github.py` (`write_pr_cache` returns `bool`)
- Test: `engine/tests/unit/test_sessions_index.py`

**Interfaces:**
- Consumes: `write_transcript_cache(...) -> bool` (Task 4), `desktop.write_desktop_cache(...) -> bool` (Task 1), `BuildStats.caches_written`.
- Produces:
  - `github.write_pr_cache(cache_path: Path, cache: dict[str, PRInfo]) -> bool`.
  - Each cache (desktop, transcripts, PRs) is rewritten only when an entry was added, changed or removed during the build.

- [ ] **Step 1: Write the failing tests**

In `engine/tests/unit/test_sessions_index.py`, add:

```python
def test_an_unchanged_rebuild_rewrites_no_cache(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    first = BuildStats()
    run(opts=opts, stats=first)
    assert sorted(first.caches_written) == ["desktop", "prs", "transcripts"]
    cache_dir = fake_data_dir / ".scout-cache"
    before = {p.name: p.stat().st_mtime_ns for p in cache_dir.glob("sessions-*.cache.json")}
    assert len(before) == 3

    again = BuildStats()
    run(opts=opts, stats=again)
    assert again.caches_written == []
    assert {p.name: p.stat().st_mtime_ns for p in cache_dir.glob("sessions-*.cache.json")} == before


def test_a_grown_transcript_rewrites_only_the_transcript_cache(fake_data_dir: Path) -> None:
    opts = _world(fake_data_dir)
    run(opts=opts)
    a_path = claude_home() / "projects" / (REPO_DIR + "--claude-worktrees-w1") / f"{UA}.jsonl"
    row = _assistant_text("Found it: the parser skips blank lines.", "2026-09-08T10:00:09.000Z")
    with a_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
    st = a_path.stat()
    os.utime(a_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    stats = BuildStats()
    idx, _ = run(opts=opts, stats=stats)
    assert stats.caches_written == ["transcripts"] and stats.transcripts_tail_parsed == 1
    a = next(s for s in idx.sessions if s.id == "local_A")
    assert a.transcript is not None and a.transcript.last_turn.kind == "end_turn"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_index.py -q -k "rewrites"`
Expected: FAIL. The first test sees `['desktop']` instead of all three names. The second sees `[]` because the transcript cache write is not recorded yet.

- [ ] **Step 3: Return a result from `write_pr_cache` in `engine/scout/sessions/github.py`**

```python
def write_pr_cache(cache_path: Path, cache: dict[str, PRInfo]) -> bool:
    """Atomically replace the cache file (unique temp + ``os.replace``). Best-effort: False instead of raising."""
    try:
        atomic_write_text(cache_path, json.dumps({k: asdict(v) for k, v in cache.items()}))
    except OSError:
        return False
    return True
```

- [ ] **Step 4: Compare each cache with its loaded snapshot in `engine/scout/sessions/index.py`**

Right after `tcache = load_transcript_cache(...)`, add:

```python
    tcache_loaded = dict(tcache)
```

Right after `pr_cache = github.load_pr_cache(...)`, add:

```python
    pr_loaded = dict(pr_cache)  # refresh_pr_states inserts new PRInfo objects, never mutates old ones
```

Replace the write-back block at the end of `build_index` (from the desktop write added in Task 1 through `github.write_pr_cache(...)`) with:

```python
    # Write back only what this run used, so deleted transcripts and unlinked PRs drop out,
    # and only when something changed (1b spec §3.6).
    if dcache != dcache_loaded and desktop.write_desktop_cache(dcache_path, dcache):
        stats.caches_written.append("desktop")
    kept = {k: v for k, v in tcache.items() if k in looked_up}
    if kept != tcache_loaded and write_transcript_cache(cache_dir / TRANSCRIPT_CACHE_FILENAME, kept):
        stats.caches_written.append("transcripts")
    referenced = {ref.key for ref in all_refs}
    pr_kept = {k: v for k, v in pr_cache.items() if k in referenced}
    if pr_kept != pr_loaded and github.write_pr_cache(cache_dir / github.PR_CACHE_FILENAME, pr_kept):
        stats.caches_written.append("prs")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_index.py tests/unit/test_sessions_github.py -q`
Expected: PASS. That includes `test_run_writes_index_and_caches_atomically`: every cache changes on the first run, so all three are written.

- [ ] **Step 6: Run the gates and commit**

```bash
.venv/bin/ruff format scout tests && .venv/bin/ruff check --fix scout tests && .venv/bin/mypy scout
.venv/bin/pytest tests/ -q
git add scout/sessions/index.py scout/sessions/github.py tests/unit/test_sessions_index.py
git commit -m "perf(sessions): rewrite a cache only when it changed

Each cache is compared with the snapshot loaded at the start of the build; the
names of rewritten caches go into BuildStats.caches_written. sessions-index.json
is still written every build (generated_at changes).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Budget tests on a realistic fixture

**Files:**
- Rewrite: `engine/tests/perf/test_sessions_index_perf.py`
- Maybe modify: `.github/workflows/test.yml` (Step 4, only if the fixture is slow to generate)

**Interfaces:**
- Consumes: `build_index(opts, *, stats)`, `BuildStats`, `BuildOptions` with its default `toplevel` (`repo_root`), and `dataclasses.replace` on `BuildOptions`.
- Produces: `test_index_builds_do_only_the_work_that_changed`, the CI regression gate for spec §5.

- [ ] **Step 1: Replace the perf test**

Replace `engine/tests/perf/test_sessions_index_perf.py` with:

```python
"""Budget tests for the session index on a realistic fixture (1b spec §5).

CI asserts the work each build does, not how long it takes:
- a cold build decodes every desktop record and fully parses every transcript;
- an unchanged rebuild does neither;
- the app's steady state (5 records rewritten, 5 transcripts grown) touches exactly those.

The wall-clock ceilings are generous and only catch a gross slowdown on a slow runner. The
real budgets (cold < 5 s, warm < 1 s) are checked on a large real machine.

The fixture is about half a gigabyte, shaped like the author's machine:
- records padded with an unused many-object field, the way MCP configuration pads real ones;
- transcripts in Claude Code's row mix;
- project folders that are repositories, some with linked worktrees.

It is deleted when the test ends, so pytest's retained tmp dirs don't keep it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scout.sessions.index import BuildOptions, build_index
from scout.sessions.model import Index
from scout.sessions.settings import AgentSessionsSettings
from scout.sessions.stats import BuildStats

RECORDS = 260  # desktop records (the author's machine: 257)
CLI_ONLY = 40  # transcripts with no desktop record
REPOS = 30
WORKTREES = 5  # linked worktrees, one in each of the first five repositories
RECORD_BYTES = 650_000  # real records are ~600 KB, almost all MCP configuration the index never reads
TURNS = 110  # a tool call, its 2–20 KB result and an attachment per turn: ≈ 330 rows, ≈ 1.2 MB
STEADY = 5
COLD_CEILING_S = 60.0
WARM_CEILING_S = 10.0


def _compact(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


def _cc_encode(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _bump(path: Path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def _padding() -> str:
    """An unused field shaped like a real record's MCP configuration: thousands of small objects."""
    server = {
        "name": "server",
        "type": "http",
        "url": "https://mcp.example.com/v1",
        "tools": [{"name": f"tool_{n}", "enabled": True} for n in range(20)],
    }
    return _compact([server] * (RECORD_BYTES // (len(_compact(server)) + 1)))


def _transcript(cwd: str) -> bytes:
    def at(n: int) -> str:
        return f"2026-09-08T{10 + n // 3600:02d}:{n // 60 % 60:02d}:{n % 60:02d}.000Z"

    rows: list[dict[str, Any]] = [
        {"type": "user", "timestamp": at(0), "message": {"role": "user", "content": "Tidy the parser"}}
    ]
    for i in range(TURNS):
        path = f"{cwd}/src/module_{i % 40:02d}.py"
        rows.append(
            {
                "type": "assistant",
                "timestamp": at(3 * i + 1),
                "message": {
                    "model": "claude-opus-5",
                    "id": f"msg_{i:03d}",
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": f"toolu_{i:03d}", "name": "Read", "input": {"file_path": path}}],
                },
            }
        )
        rows.append(
            {
                "type": "user",
                "timestamp": at(3 * i + 2),
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "tool_use_id": f"toolu_{i:03d}",
                            "type": "tool_result",
                            "content": "    return parse(line)  # source\n" * (60 + (i % 12) * 50),
                        }
                    ],
                },
                "toolUseResult": {"type": "text", "file": {"filePath": path, "numLines": 200}},
            }
        )
        rows.append(
            {
                "type": "attachment",
                "timestamp": at(3 * i + 3),
                "attachment": {"type": "hook_success", "hookName": "PostToolUse", "content": ""},
            }
        )
    rows.append(
        {
            "type": "assistant",
            "timestamp": at(3 * TURNS + 1),
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Done. Blank lines are skipped."}]},
        }
    )
    return ("\n".join(_compact(r) for r in rows) + "\n").encode()


@dataclass
class World:
    opts: BuildOptions
    repos: list[Path]
    records: list[Path]
    transcripts: list[Path]


def _build_world(root: Path, data_dir: Path) -> World:
    support, home, code = root / "support", root / "claude", root / "code"
    store = support / "claude-code-sessions" / "org-0000" / "user-0000"
    store.mkdir(parents=True)
    repos: list[Path] = []
    for k in range(REPOS):
        repo = code / f"repo-{k:02d}"
        (repo / ".git").mkdir(parents=True)
        repos.append(repo)
    cwds = [str(r) for r in repos]
    for k in range(WORKTREES):
        gitdir = repos[k] / ".git" / "worktrees" / f"w{k}"
        gitdir.mkdir(parents=True)
        (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
        wt = repos[k] / ".claude" / "worktrees" / f"w{k}"
        wt.mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
        cwds.append(str(wt))
    pad = _padding()
    blobs = {cwd: _transcript(cwd) for cwd in cwds}
    now = datetime.now(tz=UTC).replace(microsecond=0)
    records: list[Path] = []
    transcripts: list[Path] = []
    for i in range(RECORDS + CLI_ONLY):
        cwd = cwds[i % len(cwds)]
        uuid = f"{i:08d}-0000-0000-0000-000000000000"
        t = home / "projects" / _cc_encode(cwd) / f"{uuid}.jsonl"
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(blobs[cwd])
        at = (now - timedelta(hours=1 + i % 24)).timestamp()
        os.utime(t, (at, at))
        transcripts.append(t)
        if i < RECORDS:
            fields = {
                "sessionId": f"local_{i:04d}",
                "cliSessionId": uuid,
                "cwd": cwd,
                "originCwd": cwd,
                "createdAt": int(at * 1000) - 3_600_000,
                "lastActivityAt": int(at * 1000),
                "model": "claude-opus-5",
                "effort": "high",
                "isArchived": False,
                "title": f"Session {i}",
                "titleSource": "auto",
                "completedTurns": TURNS,
            }
            r = store / f"local_{i:04d}.json"
            r.write_text(_compact(fields)[:-1] + ',"remoteMcpServersConfig":' + pad + "}", encoding="utf-8")
            records.append(r)
    opts = BuildOptions(
        data_dir=data_dir,
        settings=AgentSessionsSettings(),
        claude_home=home,
        support_dir=support,
        now=now,
        use_gh=False,
        pid_alive=lambda pid: False,
    )
    return World(opts=opts, repos=repos, records=records, transcripts=transcripts)


@pytest.fixture
def world_root(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "world"
    root.mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _timed(opts: BuildOptions) -> tuple[Index, BuildStats, float]:
    stats = BuildStats()
    t0 = time.perf_counter()
    index = build_index(opts, stats=stats)
    return index, stats, time.perf_counter() - t0


@pytest.mark.perf
@pytest.mark.slow
def test_index_builds_do_only_the_work_that_changed(
    fake_data_dir: Path, world_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t0 = time.perf_counter()
    world = _build_world(world_root, fake_data_dir)
    print(f"fixture generated in {time.perf_counter() - t0:.1f}s")

    def no_subprocess(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"an index build started a subprocess: {args[:1]}")

    monkeypatch.setattr(subprocess, "Popen", no_subprocess)  # project roots come from repo_root, never git

    # Cold: empty caches.
    cold, stats, secs = _timed(world.opts)
    print(f"cold {secs:.2f}s {stats}")
    assert len(cold.sessions) == RECORDS + CLI_ONLY and cold.source_errors == []
    assert (stats.desktop_decoded, stats.desktop_served_last_good) == (RECORDS, 0)
    assert (stats.transcripts_full_parsed, stats.transcripts_tail_parsed) == (RECORDS + CLI_ONLY, 0)
    assert sorted(stats.caches_written) == ["desktop", "transcripts"]
    assert {s.project_key for s in cold.sessions} == {str(r.resolve()) for r in world.repos}  # worktrees → main repo
    assert secs < COLD_CEILING_S

    # Unchanged: nothing decoded, parsed, read or written.
    again, stats, secs = _timed(world.opts)
    print(f"unchanged {secs:.2f}s {stats}")
    assert stats == BuildStats()
    assert again.to_dict() == cold.to_dict()
    assert secs < WARM_CEILING_S

    # Steady state: the desktop app rewrote 5 records and 5 sessions appended a turn.
    for r in world.records[:STEADY]:
        text = r.read_text(encoding="utf-8").replace('"title":"Session', '"title":"Renamed session', 1)
        r.write_text(text, encoding="utf-8")
        _bump(r)
    turn = {
        "type": "assistant",
        "timestamp": "2026-09-08T12:00:00.000Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "Anything else?"}]},
    }
    appended = (_compact(turn) + "\n").encode()
    for t in world.transcripts[:STEADY]:
        with t.open("ab") as f:
            f.write(appended)
        _bump(t)
    steady, stats, secs = _timed(world.opts)
    print(f"steady {secs:.2f}s {stats}")
    assert (stats.desktop_decoded, stats.desktop_served_last_good) == (STEADY, 0)
    assert (stats.transcripts_full_parsed, stats.transcripts_tail_parsed) == (0, STEADY)
    assert stats.transcript_bytes_read == STEADY * len(appended)
    assert sorted(stats.caches_written) == ["desktop", "transcripts"]
    assert sum(1 for s in steady.sessions if (s.title or "").startswith("Renamed session")) == STEADY
    assert secs < WARM_CEILING_S

    # Identical output: a from-scratch build over the same sources agrees with the incremental one.
    fresh_dir = world_root / "fresh-vault"
    fresh_dir.mkdir()
    assert build_index(replace(world.opts, data_dir=fresh_dir)).to_dict() == steady.to_dict()
```

- [ ] **Step 2: Run it and read the timings**

Run: `.venv/bin/pytest tests/perf/test_sessions_index_perf.py -q -s`

Expected: PASS, printing the fixture generation time and the three scenario lines. Record them.

- [ ] **Step 3: Run the whole suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS.

- [ ] **Step 4: Restrict the budget test to one CI matrix entry, only if generation is slow**

Spec §5 says: "If generating the fixture adds more than about 20 s to a CI job, the budget tests run on one matrix entry only." CI runners are roughly 2–3× slower than a recent Mac.

If the local `fixture generated in` time is **≤ 7 s**, skip this step and note it for the PR. After the PR's first CI run, compare the `test` job durations with #243's run. If they grew by more than 20 s, come back to this step.

Otherwise, add this after the imports in `engine/tests/perf/test_sessions_index_perf.py`:

```python
pytestmark = pytest.mark.skipif(
    os.environ.get("CI") == "true" and os.environ.get("INDEX_BUDGET_TESTS") != "1",
    reason="the half-gigabyte budget fixture runs on one CI matrix entry (INDEX_BUDGET_TESTS=1)",
)
```

Then give the `Pytest` step of the `test` job in `.github/workflows/test.yml` this env:

```yaml
      - name: Pytest
        env:
          INDEX_BUDGET_TESTS: ${{ matrix.os == 'ubuntu-latest' && matrix.python == '3.12' && '1' || '0' }}
        run: .venv/bin/pytest tests/ -v
```

- [ ] **Step 5: Run the gates and commit**

```bash
.venv/bin/ruff format scout tests && .venv/bin/ruff check --fix scout tests && .venv/bin/mypy scout
git add tests/perf/test_sessions_index_perf.py
git commit -m "test(sessions): budget tests assert work done on a realistic fixture

About 260 padded desktop records, 300 transcripts in Claude Code's row mix and
30 repositories with linked worktrees. Cold, unchanged and steady-state builds
are checked with BuildStats counters, a no-subprocess guard and generous
ceilings; the steady-state index equals a from-scratch build.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(If Step 4 changed `.github/workflows/test.yml`, add it to the commit.)

---

### Task 7: Measure on the real machine; parallel full parses only if needed

**Files:**
- Create (not committed): `$SCRATCH/measure_index.py`
- Only if the cold build is ≥ 5 s: modify `engine/scout/sessions/transcript.py` (add `prewarm`), `engine/scout/sessions/index.py` (step 3), and add tests to `engine/tests/unit/test_sessions_transcript_incremental.py` and `engine/tests/unit/test_sessions_index.py`.

**Interfaces:**
- Consumes: `_full`, `_route` and `_home_prefix` (Task 4); `transcript_info`; `index.run(opts=..., stats=...)` (Task 1).
- Produces, only if built:
  - `transcript.POOL_THRESHOLD = 50`.
  - `transcript.prewarm(paths: list[Path], *, cache: dict[str, CachedTranscript], home: Path | None = None, stats: BuildStats | None = None, threshold: int = POOL_THRESHOLD, max_workers: int | None = None) -> None`.

- [ ] **Step 1: Write the measurement script**

Create `$SCRATCH/measure_index.py`. Do not commit it:

```python
"""Measure `scoutctl session index --no-gh` on this machine's real sessions (1b spec §2, §5).

Read-only over ~/.claude and the desktop store. Writes only into the scratch vault given on
the command line, never ~/Scout. Prints timings and counters, never session content.

usage: <engine>/.venv/bin/python measure_index.py <engine-dir> <scratch-vault-dir>
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

engine, vault = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
if vault == (Path.home() / "Scout").resolve():
    sys.exit("refusing to measure against the live vault")
vault.mkdir(parents=True, exist_ok=True)
scoutctl = engine / ".venv" / "bin" / "scoutctl"
env = {**os.environ, "SCOUT_DATA_DIR": str(vault)}
caches = ("sessions-desktop.cache.json", "sessions-transcripts.cache.json", "sessions-pr.cache.json")


def cli() -> float:
    t0 = time.perf_counter()
    subprocess.run([str(scoutctl), "session", "index", "--no-gh"], env=env, check=True, capture_output=True)
    return time.perf_counter() - t0


def clear() -> None:
    for name in caches:
        (vault / ".scout-cache" / name).unlink(missing_ok=True)


colds = []
for _ in range(3):
    clear()
    colds.append(cli())
warms = [cli() for _ in range(10)]  # back to back; running sessions keep growing: the app's steady state
print(f"cold  median {statistics.median(colds):.2f}s  runs {[round(c, 2) for c in colds]}")
print(f"warm  median {statistics.median(warms):.2f}s  max {max(warms):.2f}s  over {len(warms)} runs")

sys.path.insert(0, str(engine))
os.environ["SCOUT_DATA_DIR"] = str(vault)
from scout.sessions import desktop  # noqa: E402
from scout.sessions.index import default_options, run  # noqa: E402
from scout.sessions.stats import BuildStats  # noqa: E402

opts = default_options(vault, use_gh=False)
t0 = time.perf_counter()
desktop.load_desktop_records(opts.support_dir)  # no cache: what a cold build pays for records
print(f"cold desktop records alone {time.perf_counter() - t0:.2f}s")
for label in ("cold in-process", "warm in-process"):
    if label.startswith("cold"):
        clear()
    stats = BuildStats()
    t0 = time.perf_counter()
    run(opts=default_options(vault, use_gh=False), stats=stats)
    print(f"{label} {time.perf_counter() - t0:.2f}s  {stats}")
```

- [ ] **Step 2: Measure**

Run: `.venv/bin/python "$SCRATCH/measure_index.py" "$PWD" "$SCRATCH/measure-vault"`

Record every line of output in `$SCRATCH/1b-measurements.md`.

- [ ] **Step 3: Decide**

There are three possible outcomes:
- **Warm median ≥ 1 s.** Stop. Profile before anything else, then bring the profile's top entries (function names only) to your human partner:

  ```bash
  SCOUT_DATA_DIR="$SCRATCH/measure-vault" .venv/bin/python -m cProfile -s cumtime -m scout session index --no-gh 2>&1 | head -40
  ```
- **Cold median < 5 s and warm median < 1 s.** The budget is met. Skip Steps 4–10, record "parallel parses not needed (cold X s)" for the PR, and go to Task 8. Spec §3.5 holds the pool back unless it is needed.
- **Cold median ≥ 5 s.** Do Steps 4–10.

- [ ] **Step 4: Write the failing pool tests**

Append to `engine/tests/unit/test_sessions_transcript_incremental.py`:

```python
def _transcripts(tmp_path: Path, n: int) -> list[Path]:
    out = []
    for k in range(n):
        p = tmp_path / f"s{k}.jsonl"
        p.write_bytes(
            jsonl(prompt(f"task {k}", 0), call("t1", "Read", 1, file_path=f"{REPO}/f{k}.py"), result("t1", 2), say("done", 3))
        )
        out.append(p)
    return out


def test_prewarm_parses_in_a_pool_above_the_threshold(tmp_path: Path) -> None:
    paths = _transcripts(tmp_path, 3)
    cache: dict[str, CachedTranscript] = {}
    stats = BuildStats()
    tr.prewarm(paths, cache=cache, home=HOME, stats=stats, threshold=2, max_workers=2)
    assert stats.transcripts_full_parsed == 3 and set(cache) == {str(p) for p in paths}
    for p in paths:
        info, again = _lookup(p, cache)
        assert info == reference_parse(p, home=HOME)
        assert again == BuildStats()  # found unchanged: the pool's parse is the build's parse


def test_prewarm_stays_in_process_at_or_below_the_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_pool(*_a: object, **_k: object) -> None:
        raise AssertionError("a pool was started for a small build")

    monkeypatch.setattr(tr, "ProcessPoolExecutor", no_pool)
    cache: dict[str, CachedTranscript] = {}
    tr.prewarm(_transcripts(tmp_path, 3), cache=cache, home=HOME, threshold=3)
    assert cache == {}


def test_prewarm_leaves_out_a_transcript_whose_worker_failed(tmp_path: Path) -> None:
    paths = _transcripts(tmp_path, 2)
    deep = tmp_path / "deep.jsonl"
    deep.write_bytes(b'{"type":"assistant","message":' + b"[" * 100_000 + b"\n")  # json raises RecursionError
    cache: dict[str, CachedTranscript] = {}
    stats = BuildStats()
    tr.prewarm([*paths, deep], cache=cache, home=HOME, stats=stats, threshold=0, max_workers=2)
    assert set(cache) == {str(p) for p in paths} and stats.transcripts_full_parsed == 2
    with pytest.raises(RecursionError):  # parsed again in-process; the index reports it as a SourceError
        transcript_info(deep, cache=cache, home=HOME)
```

Append to `engine/tests/unit/test_sessions_index.py`:

```python
def test_every_looked_up_transcript_goes_through_prewarm(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    real = index_mod.prewarm

    def spy(paths: list[Path], **kw: object) -> None:
        seen.append(sorted(p.name for p in paths))
        real(paths, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(index_mod, "prewarm", spy)
    build_index(_world(fake_data_dir))
    assert seen == [sorted([f"{UA}.jsonl", f"{UE}.jsonl", f"{UF}.jsonl"])]
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_transcript_incremental.py tests/unit/test_sessions_index.py -q -k "prewarm"`
Expected: FAIL with `AttributeError: ... has no attribute 'prewarm'`.

- [ ] **Step 6: Add `prewarm` to `engine/scout/sessions/transcript.py`**

Add `import multiprocessing` and `from concurrent.futures import ProcessPoolExecutor` to the imports. Put this section before the cache-file section:

```python
# ----- parallel full parses (1b spec §3.5) ---------------------------------------------

POOL_THRESHOLD = 50  # a build with more full parses than this runs them in a process pool


def _full_parse_job(path: str, home_prefix: str) -> tuple[CachedTranscript | None, int]:
    """One worker task: a full parse and the bytes it read, or (None, 0) if anything failed."""
    try:
        p = Path(path)
        stats = BuildStats()
        return _full(p, p.stat(), home_prefix, stats), stats.transcript_bytes_read
    except Exception:  # the build parses it again in-process and reports the error there
        return None, 0


def prewarm(
    paths: list[Path],
    *,
    cache: dict[str, CachedTranscript],
    home: Path | None = None,
    stats: BuildStats | None = None,
    threshold: int = POOL_THRESHOLD,
    max_workers: int | None = None,
) -> None:
    """Fully parse, in a process pool, every transcript in *paths* that needs a full parse.

    This only happens when there are more than *threshold* of them, so steady-state builds
    never pay a pool's start-up. The results go into *cache*, where the build's own
    ``transcript_info`` calls find them unchanged. A transcript whose worker failed is left
    out, and ``transcript_info`` parses it in-process as usual.
    """
    if stats is None:
        stats = BuildStats()
    todo: list[str] = []
    for path in paths:
        try:
            st = path.stat()
        except OSError:
            continue
        if _route(cache.get(str(path)), st) == "full":
            todo.append(str(path))
    if len(todo) <= threshold:
        return
    workers = max(1, min(len(todo), max_workers or os.cpu_count() or 1))
    prefixes = [_home_prefix(home)] * len(todo)
    try:
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            done = list(pool.map(_full_parse_job, todo, prefixes, chunksize=4))
    except Exception:  # no pool here (sandbox, process limits): the in-process path does it all
        return
    for key, (entry, read) in zip(todo, done, strict=True):
        if entry is not None:
            cache[key] = entry
            stats.transcripts_full_parsed += 1
            stats.transcript_bytes_read += read
```

Add `"POOL_THRESHOLD"` and `"prewarm"` to `__all__`.

- [ ] **Step 7: Split step 3 of `build_index` so the pool sees every lookup first**

In `engine/scout/sessions/index.py`, import `prewarm` alongside `transcript_info`. Replace step 3 (from `# 3. Transcript facts, liveness, last activity.` to the end of that loop) with:

```python
    # 3. Transcript facts, liveness, last activity.
    wanted: list[tuple[AgentSession, Path]] = []
    for sess in sessions:
        cli_uuid = sess.cli_session_id
        tpath = tpaths.get(cli_uuid) if cli_uuid else None
        if tpath is not None:
            try:
                mtime_iso = ns_to_iso(tpath.stat().st_mtime_ns)
                if sess.last_activity_at is None or mtime_iso > sess.last_activity_at:
                    sess.last_activity_at = mtime_iso
                last = parse_iso(sess.last_activity_at)
                if sess.id not in no_transcript and last is not None and opts.now - last <= window:
                    wanted.append((sess, tpath))
            except Exception as exc:  # spec §4.12: one bad transcript never aborts the build
                errors.append(SourceError(source="transcript", message=f"{tpath.name}: {exc}"))
        if cli_uuid is not None and cli_uuid in live:
            sess.is_open = True
    prewarm([p for _, p in wanted], cache=tcache, stats=stats)  # a pool only for many full parses (1b §3.5)
    looked_up: set[str] = set()  # transcript-cache keys used this run; only these are written back
    for sess, tpath in wanted:
        looked_up.add(str(tpath))
        try:
            sess.transcript = transcript_info(tpath, cache=tcache, stats=stats)
        except Exception as exc:  # spec §4.12: one bad transcript never aborts the build
            errors.append(SourceError(source="transcript", message=f"{tpath.name}: {exc}"))
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_transcript_incremental.py tests/unit/test_sessions_index.py tests/perf -q`
Expected: PASS. The perf test's cold scenario now runs 300 full parses through the pool, and its counters are unchanged.

- [ ] **Step 9: Measure again**

Run: `.venv/bin/python "$SCRATCH/measure_index.py" "$PWD" "$SCRATCH/measure-vault"`

Append the output to `$SCRATCH/1b-measurements.md`. If the cold median is still ≥ 5 s, stop and bring the measurements to your human partner. A faster JSON library is a spec §2 non-goal, and adding one is their call.

- [ ] **Step 10: Run the gates and commit**

```bash
.venv/bin/ruff format scout tests && .venv/bin/ruff check --fix scout tests && .venv/bin/mypy scout
.venv/bin/pytest tests/ -q
git add scout/sessions/transcript.py scout/sessions/index.py tests/unit/test_sessions_transcript_incremental.py tests/unit/test_sessions_index.py
git commit -m "perf(sessions): run a cold build's full parses in a process pool

Only when a build needs more than 50 full parses, so steady-state builds never
start a pool. A failed worker leaves its transcript to the in-process path,
which reports errors as before.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Changelog and acceptance

**Files:**
- Modify: `CHANGELOG.md` (under `## [Unreleased]` → `### Added`, after the agent-session index entry)

**Interfaces:**
- Consumes: the measurements in `$SCRATCH/1b-measurements.md` (Task 7) and the equivalence results (Tasks 3 and 4).
- Produces: `$SCRATCH/1b-acceptance.md`, the numbers the PR description quotes.

- [ ] **Step 1: Take the acceptance measurements**

On the author's machine, with sessions running, run:

```bash
.venv/bin/python "$SCRATCH/measure_index.py" "$PWD" "$SCRATCH/measure-vault"
```

Write `$SCRATCH/1b-acceptance.md` with:
- cold median;
- warm median and maximum;
- the in-process counters line for a warm build;
- the Task 3 and Task 4 equivalence results;
- the local fixture generation and scenario times from Task 6, Step 2;
- whether Task 7's pool was built.

All four budgets must hold: cold < 5 s, warm < 1 s, unchanged < 1 s, steady < 1 s. If any does not, stop and report it.

- [ ] **Step 2: Add the changelog entry**

Insert this bullet directly after the "Agent-session index — `scoutctl session index` / `session list`" bullet. Replace `<cold>` and `<warm>` with the medians from Step 1.

```markdown
- **The session index is incremental** (`engine/scout/sessions/`) — a rebuild re-reads only what changed, so the Mac app can refresh it every couple of seconds while sessions run. Desktop records are cached by size and mtime in a new `.scout-cache/sessions-desktop.cache.json`, which holds only the fields the index uses, never the raw record, whose MCP configuration can hold credentials. A record caught mid-write is served from its last good version and reported only if it is still unreadable on the next build. A growing transcript is parsed from a checkpoint instead of from the start (the transcript cache moves to version 2, so the first build after upgrading is a cold one); full parses no longer decode tool results that cannot change the answer; project roots are found without running `git`; and each cache is rewritten only when it changed. The index schema and `cc-sessions.md` are unchanged, and every transcript fact matches a from-scratch parse. On a machine with ~260 desktop sessions and ~500 transcripts: cold <cold> s, warm <warm> s (was 7.7 s / 1.8 s). Spec: scout-app `docs/superpowers/specs/2026-09-28-agent-sessions-index-speed-design.md`.
```

- [ ] **Step 3: Run every gate**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
.venv/bin/python -m scout.scripts.versioning check
.venv/bin/pytest tests/ -q
```

Expected: all clean, and the test count equals the Task 1 Step 0 count plus the new tests.

- [ ] **Step 4: Commit**

```bash
git add ../CHANGELOG.md
git commit -m "docs(changelog): incremental agent-session index

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Hand off**

Use superpowers:finishing-a-development-branch. The PR's base is `feat/agent-sessions-index` (#243), not `main`. Its description quotes `$SCRATCH/1b-acceptance.md` and the two exactness assumptions above, and links this plan, the spec and #243. Once #243 merges, rebase the branch onto `main` and retarget the PR.
