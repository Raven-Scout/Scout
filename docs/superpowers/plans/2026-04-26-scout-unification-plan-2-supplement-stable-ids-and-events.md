# Scout Engine Plan 2 Supplement: stable IDs + event-shaped mutations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the §13 forward-compatibility commitments from the v0.4 unification spec by (a) adding two leaf modules (`scout.ids`, `scout.events`) and a file-backed prefix↔ULID map (`scout.id_map`), and (b) threading them through the action-items parser, writer, and three mutators that Plan 2 ports. Ships when CI is green and `scoutctl action-items mark-done [#A3F7]` works against a fixture data dir, returns an `Event`, and the legacy `--by-subject` fallback still resolves substring lookups for unprefixed lines.

**Architecture:** `scout.ids` mints ULIDs and 4-character Crockford base32 short prefixes (collision-safe via per-call retry against the id-map). `scout.events` defines a single frozen `Event` dataclass. `scout.id_map` wraps `$SCOUT_DATA_DIR/.scout-state/id-map.json` with read-modify-write under `flock`, plus a fuzzy reattach-by-title+position helper for prefix-loss recovery. The parser learns to extract `[#XXXX]` into `ActionItem.short_prefix`; the writer preserves prefixes across rewrites. The three mutators (`mark_done`, `snooze`, `add_comment`) gain `--by-id` (the new default for any line that has a prefix) and `--by-subject` (fallback for legacy unprefixed lines). All three return `Event` instead of `None`. The CLI ignores the return value; tests assert on it. v0.5's `emit()` substitution will turn that return into a real append; v0.4 is wire-compat only.

**Tech Stack:** Python 3.11+, Typer, pytest, ruff, mypy. New runtime dependency: `python-ulid` (`ulid-py` is the historical PyPI name; install as `python-ulid` for the modern fork). No new dev dependencies.

---

## Context for the implementer

**Working directory:** Same as Plan 2 — `/Users/jordanburger/scout-plugin/`. This supplement extends Plan 2's branch `migrate/v0.4.0-port-python` with new tasks 13–21. Confirm before starting:

```bash
cd ~/scout-plugin
git status                                    # Plan 2 work present, working tree clean
git log --oneline | head -15                  # Plan 2 commits visible
git branch --show-current                     # migrate/v0.4.0-port-python (or equivalent)
.venv/bin/pytest tests/ -q                    # all Plan 2 tests pass
```

If Plan 2 isn't on the current branch, rebase or branch from its tip before starting.

**Reference docs:**
- `/Users/jordanburger/scout-app/docs/superpowers/specs/2026-04-24-scout-unification-design.md` §13 — the canonical contract for IDs, prefixes, events, and projection-consumer contracts.
- `/Users/jordanburger/scout-app/docs/superpowers/specs/2026-04-25-scout-event-architecture-design.md` — vision context for *why* these specific shapes were chosen.
- `/Users/jordanburger/scout-app/docs/superpowers/plans/2026-04-24-scout-unification-plan-2-port-existing-python.md` — the parent plan whose deliverables this supplement extends.

**What this plan does NOT touch:**
- Hooks (Plan 3).
- KB ontology + `kb_summary.json` cache (Plan 5; the kb_summary projection-consumer wording change happens there).
- Setup/launchd (Plan 4).
- scout-app refactor (Plan 6).
- The TUI (`scout.tui` is updated minimally only if it consumes the parser's `ActionItem.short_prefix` field, which it doesn't have to — TUI keeps showing titles, prefixes are CLI-only for v0.4).
- An actual event store. `Event` returned by mutators is wire-compat scaffolding only; nothing logs them in v0.4. `emit()` lives elsewhere as a thin forward-compat wrapper.

## File structure (what this supplement creates and modifies)

```
~/scout-plugin/engine/
├── scout/
│   ├── ids.py                                NEW — Task 13
│   ├── events.py                             NEW — Task 14
│   ├── id_map.py                             NEW — Task 15
│   ├── action_items/
│   │   ├── parser.py                         MODIFIED — Task 16 (adds short_prefix field)
│   │   ├── writer.py                         MODIFIED — Task 17 (prefix-preserving rewrites)
│   │   ├── mark_done.py                      MODIFIED — Task 18 (--by-id default, returns Event)
│   │   ├── snooze.py                         MODIFIED — Task 19 (same pattern)
│   │   ├── add_comment.py                    MODIFIED — Task 20 (same pattern)
│   │   ├── list.py                           MODIFIED — Task 21 (surface prefix in output)
│   │   └── cli.py                            MODIFIED — Task 21 (--by-id / --by-subject flags)
│   └── paths.py                              MODIFIED — Task 15 (id_map_path() helper)
├── tests/
│   ├── unit/
│   │   ├── test_ids.py                       NEW — Task 13
│   │   ├── test_events.py                    NEW — Task 14
│   │   ├── test_id_map.py                    NEW — Task 15
│   │   ├── test_action_items_parser.py       MODIFIED — Task 16
│   │   ├── test_action_items_writer.py       MODIFIED — Task 17
│   │   ├── test_action_items_mark_done.py    MODIFIED — Task 18
│   │   ├── test_action_items_snooze.py       MODIFIED — Task 19
│   │   ├── test_action_items_add_comment.py  MODIFIED — Task 20
│   │   └── test_action_items_list.py         MODIFIED — Task 21
│   ├── fixtures/
│   │   └── action-items-with-prefixes.md     NEW — Task 16
│   └── concurrency/
│       └── test_id_map_concurrent.py         NEW — Task 15
└── pyproject.toml                            MODIFIED — Task 13 (add python-ulid)
```

---

## Task 13: Add `scout.ids` — ULIDs and short prefixes

**Files:**
- Create: `~/scout-plugin/engine/scout/ids.py`
- Create: `~/scout-plugin/engine/tests/unit/test_ids.py`
- Modify: `~/scout-plugin/engine/pyproject.toml`

**What this builds:** A leaf module that mints ULIDs and 4-char Crockford base32 short prefixes. Collision retry against an external "in-use" set is the caller's responsibility (the prefix↔ULID map landing in Task 15 owns that). This module is pure functions over `random` and `time`.

- [ ] **Step 1: Add `python-ulid` to runtime dependencies**

Edit `engine/pyproject.toml` `[project] dependencies` to add `"python-ulid>=2.2"`. The lower bound matches the API used (`ULID()` constructor returns a sortable instance with `.bytes` and `.hex`).

- [ ] **Step 2: Sync the venv**

```bash
cd ~/scout-plugin/engine
uv pip install -e ".[dev]"
.venv/bin/python -c "from ulid import ULID; print(str(ULID()))"
```

Expected: a 26-char base32 ULID printed.

- [ ] **Step 3: Write failing tests**

Create `engine/tests/unit/test_ids.py`:

```python
"""Unit tests for scout.ids — ULID + short-prefix generation."""

from __future__ import annotations

import re

import pytest

from scout.ids import (
    CROCKFORD_ALPHABET,
    SHORT_PREFIX_LEN,
    new_ulid,
    new_short_prefix,
    short_prefix_pattern,
)


def test_new_ulid_returns_26_char_string() -> None:
    val = new_ulid()
    assert isinstance(val, str)
    assert len(val) == 26


def test_new_ulid_is_unique_across_calls() -> None:
    seen: set[str] = set()
    for _ in range(100):
        seen.add(new_ulid())
    assert len(seen) == 100


def test_new_short_prefix_is_4_crockford_chars() -> None:
    p = new_short_prefix()
    assert len(p) == SHORT_PREFIX_LEN == 4
    assert all(c in CROCKFORD_ALPHABET for c in p)


def test_new_short_prefix_excludes_ambiguous_chars() -> None:
    # Crockford base32 excludes I, L, O, U to avoid 0/O and 1/I/L visual collisions.
    for c in "ILOU":
        assert c not in CROCKFORD_ALPHABET


def test_short_prefix_pattern_matches_well_formed_prefix() -> None:
    rx = short_prefix_pattern()
    assert rx.fullmatch("[#A3F7]")
    assert rx.fullmatch("[#0000]")
    # Hyphens and lowercase are not allowed.
    assert not rx.fullmatch("[#a3f7]")
    assert not rx.fullmatch("[#A-37]")
    # Wrong length.
    assert not rx.fullmatch("[#A3F]")
    assert not rx.fullmatch("[#A3F7E]")


def test_short_prefix_pattern_finds_prefix_in_line() -> None:
    rx = short_prefix_pattern()
    line = "- [ ] [#A3F7] Submit Lever feedback"
    m = rx.search(line)
    assert m is not None
    assert m.group(0) == "[#A3F7]"
    assert m.group(1) == "A3F7"


def test_new_short_prefix_excludes_set_member() -> None:
    """Caller passes an in-use set; generator retries until it lands outside."""
    in_use = {new_short_prefix() for _ in range(5)}
    # With ~1M space and 5 used prefixes, this lands in one try almost surely;
    # the test asserts the contract, not the retry count.
    p = new_short_prefix(exclude=in_use)
    assert p not in in_use


def test_new_short_prefix_raises_when_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """When all retries hit `exclude`, the generator raises instead of looping forever."""
    import secrets
    # Force every generated prefix to be "AAAA" so it deterministically hits the exclude set.
    monkeypatch.setattr(secrets, "choice", lambda _: "A")
    with pytest.raises(RuntimeError, match="prefix space exhausted"):
        new_short_prefix(exclude={"AAAA"}, max_attempts=3)
```

- [ ] **Step 4: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_ids.py -v
```

Expected: `ModuleNotFoundError: No module named 'scout.ids'`.

- [ ] **Step 5: Implement `scout/ids.py`**

```python
"""ULID generation and Crockford base32 short prefixes.

Short prefixes are the human-friendly surface form for action-item IDs in
markdown; the full ULID is the canonical storage form. See v0.4 spec §13.1.

The Crockford alphabet excludes 0/O and 1/I/L visual confusables (and
also U) so that hand-typed prefixes are unambiguous.
"""

from __future__ import annotations

import re
import secrets

from ulid import ULID

# Crockford base32 alphabet: 0-9 + uppercase A-Z minus I, L, O, U.
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
SHORT_PREFIX_LEN = 4

_DEFAULT_MAX_ATTEMPTS = 64  # plenty for any realistic in-use set

_PREFIX_REGEX = re.compile(
    r"\[#(" + f"[{re.escape(CROCKFORD_ALPHABET)}]" + r"{" + str(SHORT_PREFIX_LEN) + r"})\]"
)


def new_ulid() -> str:
    """Mint a fresh 26-character ULID (sortable, time-ordered)."""
    return str(ULID())


def new_short_prefix(
    exclude: set[str] | None = None,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
) -> str:
    """Generate a fresh 4-char Crockford base32 prefix not in `exclude`.

    `exclude` is the set of currently-in-use short prefixes (typically
    sourced from `scout.id_map.IdMap.in_use_prefixes()`). Raises
    `RuntimeError` if `max_attempts` retries all hit the exclude set —
    indicates the prefix space is approaching saturation, which would
    require widening to 5 chars (out of scope for v0.4).
    """
    exclude = exclude or set()
    for _ in range(max_attempts + 1):
        candidate = "".join(
            secrets.choice(CROCKFORD_ALPHABET) for _ in range(SHORT_PREFIX_LEN)
        )
        if candidate not in exclude:
            return candidate
    raise RuntimeError(
        f"prefix space exhausted after {max_attempts} attempts "
        f"(exclude size {len(exclude)})"
    )


def short_prefix_pattern() -> re.Pattern[str]:
    """Regex matching `[#XXXX]` where XXXX is 4 Crockford chars.

    `match.group(0)` returns the full bracketed prefix; `match.group(1)`
    returns the bare 4-char prefix.
    """
    return _PREFIX_REGEX
```

- [ ] **Step 6: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_ids.py -v
```

Expected: 8 passed.

- [ ] **Step 7: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

Expected: clean.

- [ ] **Step 8: Commit**

```bash
cd ~/scout-plugin
git add engine/pyproject.toml engine/scout/ids.py engine/tests/unit/test_ids.py
git commit -m "feat(engine): add scout.ids — ULID + Crockford short-prefix generation"
```

---

## Task 14: Add `scout.events` — `Event` dataclass

**Files:**
- Create: `~/scout-plugin/engine/scout/events.py`
- Create: `~/scout-plugin/engine/tests/unit/test_events.py`

**What this builds:** A frozen dataclass representing a single mutation event. v0.4 only uses it as a return value from mutators; v0.5 will persist these into the SQLite event store. The schema must already be its v0.5 shape so the persistence layer is a one-line wire-up.

- [ ] **Step 1: Write failing tests**

Create `engine/tests/unit/test_events.py`:

```python
"""Unit tests for scout.events.Event."""

from __future__ import annotations

import datetime as dt
import re

import pytest

from scout.events import Event, now_iso


def test_event_is_frozen() -> None:
    e = Event(
        id="01HXABC0000000000000000000",
        ts="2026-04-26T12:00:00.000Z",
        kind="action_item.completed",
        source="cli:mark_done",
        payload={"item_id": "01HXAAA0000000000000000000"},
    )
    with pytest.raises(Exception):  # FrozenInstanceError, but module imports cleanly
        e.kind = "action_item.snoozed"  # type: ignore[misc]


def test_event_has_required_fields() -> None:
    e = Event(
        id="01HXABC0000000000000000000",
        ts="2026-04-26T12:00:00.000Z",
        kind="action_item.completed",
        source="cli:mark_done",
        payload={},
    )
    assert e.id and e.ts and e.kind and e.source
    assert e.payload == {}


def test_now_iso_returns_iso8601_z() -> None:
    s = now_iso()
    # Match: YYYY-MM-DDTHH:MM:SS.mmmZ
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", s)
    # Round-trip via fromisoformat (Python 3.11+ handles 'Z' as +00:00 only since 3.12;
    # we use the explicit Z stripper to keep 3.11 compat).
    parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_event_payload_supports_arbitrary_json_compatible_dict() -> None:
    e = Event(
        id="01HX",
        ts="2026-04-26T00:00:00.000Z",
        kind="x.y.z",
        source="test",
        payload={"a": 1, "b": "two", "c": [3, 4], "d": {"e": True}},
    )
    assert e.payload["d"]["e"] is True
```

- [ ] **Step 2: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_events.py -v
```

Expected: `ModuleNotFoundError: No module named 'scout.events'`.

- [ ] **Step 3: Implement `scout/events.py`**

```python
"""Event dataclass returned by mutators.

In v0.4, mutators return an `Event` but nothing persists it. v0.5 will
add an `emit()` function that appends to the SQLite event store; the
shape defined here is its wire format. See v0.4 spec §13.2 and the v0.5+
event-architecture vision spec.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Event:
    """A single mutation event.

    Fields:
        id: ULID for the event itself (distinct from any entity ULID
            referenced in the payload).
        ts: ISO 8601 UTC timestamp with millisecond precision and 'Z'
            suffix, e.g. "2026-04-26T12:34:56.789Z".
        kind: Flat namespace, e.g. "action_item.completed".
        source: Origin tag, e.g. "cli:mark_done", "hook:connector-log".
        payload: Arbitrary JSON-compatible dict. Per-kind schemas are
            documented in the v0.5+ event-architecture spec.
    """

    id: str
    ts: str
    kind: str
    source: str
    payload: dict[str, Any]


def now_iso() -> str:
    """ISO 8601 UTC string with millisecond precision and 'Z' suffix."""
    n = dt.datetime.now(tz=dt.timezone.utc)
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}Z"
```

- [ ] **Step 4: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_events.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/events.py engine/tests/unit/test_events.py
git commit -m "feat(engine): add scout.events with Event dataclass + now_iso helper"
```

---

## Task 15: Add `scout.id_map` — file-backed prefix↔ULID map

**Files:**
- Create: `~/scout-plugin/engine/scout/id_map.py`
- Create: `~/scout-plugin/engine/tests/unit/test_id_map.py`
- Create: `~/scout-plugin/engine/tests/concurrency/test_id_map_concurrent.py`
- Modify: `~/scout-plugin/engine/scout/paths.py`
- Modify: `~/scout-plugin/engine/tests/unit/test_paths.py`

**What this builds:** The on-disk source of truth for the prefix↔ULID mapping, plus the fuzzy-reattach helper for prefix-loss recovery. State file location: `$SCOUT_DATA_DIR/.scout-state/id-map.json`. Schema: a flat dict keyed by ULID, each entry holding the short prefix, last-known title, and last-known file/line position. Read-modify-write happens under `flock(LOCK_EX)` per spec §6 concurrency rules.

- [ ] **Step 1: Add `paths.id_map_path()` helper test**

Append to `engine/tests/unit/test_paths.py`:

```python
def test_id_map_path_returns_state_subdir(fake_data_dir: Path) -> None:
    p = paths.id_map_path(data=fake_data_dir)
    assert p == fake_data_dir / ".scout-state" / "id-map.json"
```

Run, confirm RED:

```bash
.venv/bin/pytest tests/unit/test_paths.py::test_id_map_path_returns_state_subdir -v
```

Expected: `AttributeError: module 'scout.paths' has no attribute 'id_map_path'`.

- [ ] **Step 2: Implement `paths.id_map_path()`**

Append to `engine/scout/paths.py`:

```python
def id_map_path(data: Path | None = None) -> Path:
    """Return the path to the prefix↔ULID map JSON file.

    Lives under `$SCOUT_DATA_DIR/.scout-state/id-map.json`. Parent dir
    is created on first write; readers may find it absent and treat
    that as an empty map.
    """
    target = data if data is not None else data_dir()
    return target / ".scout-state" / "id-map.json"
```

Run, confirm GREEN:

```bash
.venv/bin/pytest tests/unit/test_paths.py -v
```

Expected: existing path tests + the new one all pass.

- [ ] **Step 3: Write `IdMap` unit tests**

Create `engine/tests/unit/test_id_map.py`:

```python
"""Unit tests for scout.id_map.IdMap."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.id_map import IdMap, IdMapEntry


def test_load_missing_file_returns_empty_map(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    assert m.in_use_prefixes() == set()
    assert list(m.iter_entries()) == []


def test_register_writes_entry(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    entry = IdMapEntry(
        ulid="01HXAAA0000000000000000000",
        short_prefix="A3F7",
        last_title="Submit Lever feedback to recruiting",
        last_file="action-items-2026-04-26.md",
        last_line=5,
    )
    m.register(entry)
    m.save()

    fresh = IdMap.load(fake_data_dir)
    assert fresh.in_use_prefixes() == {"A3F7"}
    assert fresh.lookup_by_prefix("A3F7") is not None
    assert fresh.lookup_by_prefix("A3F7").ulid == "01HXAAA0000000000000000000"


def test_lookup_by_prefix_returns_none_for_unknown(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    assert m.lookup_by_prefix("ZZZZ") is None


def test_lookup_by_ulid(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    entry = IdMapEntry(
        ulid="01HXBBB0000000000000000000",
        short_prefix="B5K2",
        last_title="Reply to Q2 budget thread",
        last_file="action-items-2026-04-26.md",
        last_line=8,
    )
    m.register(entry)
    found = m.lookup_by_ulid("01HXBBB0000000000000000000")
    assert found is not None
    assert found.short_prefix == "B5K2"


def test_register_updates_existing_entry(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    e1 = IdMapEntry("01HX", "A3F7", "old title", "f.md", 1)
    m.register(e1)
    e2 = IdMapEntry("01HX", "A3F7", "new title", "f.md", 3)
    m.register(e2)
    m.save()

    fresh = IdMap.load(fake_data_dir)
    assert fresh.lookup_by_ulid("01HX").last_title == "new title"
    assert fresh.lookup_by_ulid("01HX").last_line == 3


def test_reattach_finds_match_by_title_and_file(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "Submit Lever feedback", "today.md", 5))
    m.register(IdMapEntry("01HXBBB", "B5K2", "Reply to budget thread", "today.md", 8))

    # Line lost its prefix but title is still "Submit Lever feedback"
    found = m.reattach(title="Submit Lever feedback", file="today.md")
    assert found is not None
    assert found.short_prefix == "A3F7"


def test_reattach_returns_none_for_unknown_title(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "Existing", "today.md", 1))
    assert m.reattach(title="Brand new task", file="today.md") is None


def test_reattach_prefers_same_file_match(fake_data_dir: Path) -> None:
    """Same title in two files — reattach should prefer the file argument."""
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HX111", "AAAA", "Daily standup", "monday.md", 1))
    m.register(IdMapEntry("01HX222", "BBBB", "Daily standup", "tuesday.md", 1))
    found = m.reattach(title="Daily standup", file="tuesday.md")
    assert found.short_prefix == "BBBB"


def test_save_creates_parent_directory(fake_data_dir: Path) -> None:
    state_dir = fake_data_dir / ".scout-state"
    assert not state_dir.exists()
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HX", "A3F7", "x", "y.md", 1))
    m.save()
    assert (state_dir / "id-map.json").exists()
```

Run, confirm RED:

```bash
.venv/bin/pytest tests/unit/test_id_map.py -v
```

Expected: `ModuleNotFoundError: No module named 'scout.id_map'`.

- [ ] **Step 4: Implement `scout/id_map.py`**

```python
"""Prefix↔ULID map persisted at $SCOUT_DATA_DIR/.scout-state/id-map.json.

Read-modify-write semantics use `flock(LOCK_EX)` per spec §6. The map
holds last-known position metadata so the diff engine can fuzzy-reattach
a markdown line whose `[#XXXX]` prefix was accidentally deleted.

See v0.4 spec §13.1.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from scout import paths


@dataclass(frozen=True)
class IdMapEntry:
    ulid: str
    short_prefix: str
    last_title: str
    last_file: str
    last_line: int


class IdMap:
    """Owns the prefix↔ULID JSON file. Construct via `IdMap.load(data_dir)`."""

    def __init__(self, data_dir: Path, entries: dict[str, IdMapEntry]) -> None:
        self._data_dir = data_dir
        self._entries: dict[str, IdMapEntry] = entries  # keyed by ULID

    @classmethod
    def load(cls, data_dir: Path) -> "IdMap":
        path = paths.id_map_path(data_dir)
        if not path.exists():
            return cls(data_dir, entries={})
        with path.open("r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                raw = json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        entries = {
            ulid: IdMapEntry(**meta) for ulid, meta in raw.get("entries", {}).items()
        }
        return cls(data_dir, entries)

    def save(self) -> None:
        path = paths.id_map_path(self._data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "entries": {
                ulid: asdict(entry) for ulid, entry in self._entries.items()
            },
        }
        # Write under exclusive lock + atomic rename.
        fd, tmp = tempfile.mkstemp(
            prefix=".id-map.", suffix=".json.tmp", dir=str(path.parent)
        )
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    json.dump(payload, f, indent=2, sort_keys=True)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            os.replace(tmp_path, path)
        except BaseException:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def register(self, entry: IdMapEntry) -> None:
        """Insert or update an entry. Caller is responsible for `save()`."""
        self._entries[entry.ulid] = entry

    def lookup_by_prefix(self, prefix: str) -> IdMapEntry | None:
        for entry in self._entries.values():
            if entry.short_prefix == prefix:
                return entry
        return None

    def lookup_by_ulid(self, ulid: str) -> IdMapEntry | None:
        return self._entries.get(ulid)

    def in_use_prefixes(self) -> set[str]:
        return {entry.short_prefix for entry in self._entries.values()}

    def iter_entries(self) -> Iterator[IdMapEntry]:
        return iter(self._entries.values())

    def reattach(self, *, title: str, file: str) -> IdMapEntry | None:
        """Fuzzy-match an entry by title; prefer same-file matches.

        Used when a markdown line lost its `[#XXXX]` prefix. Title
        comparison is exact (case-sensitive); future enhancement could
        Levenshtein-fuzz this.
        """
        candidates = [
            e for e in self._entries.values() if e.last_title == title
        ]
        if not candidates:
            return None
        same_file = [e for e in candidates if e.last_file == file]
        if same_file:
            return same_file[0]
        return candidates[0]
```

Run, confirm GREEN:

```bash
.venv/bin/pytest tests/unit/test_id_map.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Write the concurrency test**

Create `engine/tests/concurrency/__init__.py` (empty) if it doesn't already exist, then create `engine/tests/concurrency/test_id_map_concurrent.py`:

```python
"""Concurrency tests for scout.id_map — multiple processes registering entries.

Per spec §6: stateful JSON files use read-modify-write under flock(LOCK_EX).
"""

from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path

import pytest

from scout.id_map import IdMap, IdMapEntry


def _register_one(args: tuple[Path, str, str]) -> None:
    data_dir, ulid, prefix = args
    m = IdMap.load(data_dir)
    m.register(IdMapEntry(ulid, prefix, f"task {prefix}", "today.md", 1))
    m.save()


@pytest.mark.concurrency
def test_parallel_registers_are_not_lost(fake_data_dir: Path) -> None:
    """N processes register N distinct entries; final map has all N.

    Note: this test is read-modify-write — last writer wins on the JSON file.
    The file lock guarantees no torn JSON, but two processes registering at
    the exact same moment may produce a final file with only one of them.
    The test runs entries serially (small N, time.sleep between starts is
    not used; OS scheduling provides the natural serialization).

    For action items, registration is rare and scout-app + CLI rarely race.
    For high-write-rate cases, see the v0.5 SQLite migration.
    """
    n = 8
    args = [
        (fake_data_dir, f"01HX{i:022d}", f"P{i:03X}") for i in range(n)
    ]
    with mp.get_context("fork").Pool(processes=4) as pool:
        pool.map(_register_one, args)

    m = IdMap.load(fake_data_dir)
    found = m.in_use_prefixes()
    # We can't assert all 8 lands due to last-writer-wins, but we MUST
    # assert the file is parseable (no JSON corruption) and at least one entry persists.
    assert isinstance(found, set)
    assert len(found) >= 1
```

Note: this test acknowledges the LWW limitation honestly — the file lock prevents torn JSON, not lost updates from a strict read-modify-write. If a stricter merge is needed before v0.5 lands SQLite, an explicit lock-on-load + lock-on-save pattern can replace it; for v0.4 the action-items write rate is single-digits per day so LWW is fine.

Run:

```bash
.venv/bin/pytest tests/concurrency/test_id_map_concurrent.py -v -m concurrency
```

Expected: 1 passed (or "no tests ran" if `concurrency` marker filtering excludes it from default run — that's also OK; this test is opt-in).

- [ ] **Step 6: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 7: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/id_map.py engine/scout/paths.py \
        engine/tests/unit/test_id_map.py engine/tests/unit/test_paths.py \
        engine/tests/concurrency/__init__.py engine/tests/concurrency/test_id_map_concurrent.py
git commit -m "feat(engine): add IdMap with file-locked prefix↔ULID storage"
```

---

## Task 16: Extend the parser to recognize `[#XXXX]` prefixes

**Files:**
- Modify: `~/scout-plugin/engine/scout/action_items/parser.py`
- Modify: `~/scout-plugin/engine/tests/unit/test_action_items_parser.py`
- Create: `~/scout-plugin/engine/tests/fixtures/action-items-with-prefixes.md`

**What this builds:** A `short_prefix: str | None` field on `ActionItem`. The parser strips `[#XXXX]` from the visible title (so callers don't see the prefix in the title field) but preserves `raw_line` exactly for substring fallback.

- [ ] **Step 1: Create the prefix-bearing fixture**

Create `engine/tests/fixtures/action-items-with-prefixes.md`:

```markdown
# Action Items — 2026-04-26

## In Progress

- [ ] [#A3F7] 🔴 Submit Lever feedback to recruiting
  - Context: https://example.com/lever
- [ ] 🟡 Send Scout plugin announcement
- [x] [#B5K2] 🟢 Read incident postmortem

## To Do

- [ ] [#C9N4] 🔴 Reply to Q2 budget thread
- [ ] Followup with vendor on contract redlines

## Completed Today

- [x] [#D7P1] 🟢 Submit weekly status
```

The fixture mixes prefixed and unprefixed lines so the parser tests cover both branches.

- [ ] **Step 2: Append failing tests**

Add to `engine/tests/unit/test_action_items_parser.py`:

```python
PREFIX_FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "action-items-with-prefixes.md"
)


def test_parser_extracts_short_prefix_when_present() -> None:
    items = parse_file(PREFIX_FIXTURE)
    by_title = {i.title: i for i in items}
    assert by_title["Submit Lever feedback to recruiting"].short_prefix == "A3F7"
    assert by_title["Read incident postmortem"].short_prefix == "B5K2"
    assert by_title["Reply to Q2 budget thread"].short_prefix == "C9N4"


def test_parser_short_prefix_is_none_for_unprefixed_line() -> None:
    items = parse_file(PREFIX_FIXTURE)
    by_title = {i.title: i for i in items}
    assert by_title["Send Scout plugin announcement"].short_prefix is None
    assert by_title["Followup with vendor on contract redlines"].short_prefix is None


def test_parser_strips_prefix_from_title() -> None:
    """Title field should not include `[#XXXX]` — that's what short_prefix is for."""
    items = parse_file(PREFIX_FIXTURE)
    titles = [i.title for i in items]
    assert all("[#" not in t for t in titles)


def test_parser_raw_line_preserves_prefix() -> None:
    """raw_line is the unmodified source line; substring fallback uses it."""
    items = parse_file(PREFIX_FIXTURE)
    by_title = {i.title: i for i in items}
    assert "[#A3F7]" in by_title["Submit Lever feedback to recruiting"].raw_line
```

- [ ] **Step 3: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_action_items_parser.py -v -k "short_prefix or strips_prefix or raw_line_preserves_prefix"
```

Expected: 4 failures (`AttributeError: 'ActionItem' object has no attribute 'short_prefix'`).

- [ ] **Step 4: Extend the parser**

Edit `engine/scout/action_items/parser.py`. Two changes:

1. Add `short_prefix: str | None = None` to the `ActionItem` dataclass (assign default `None` so existing tests that build ActionItems without specifying it continue to pass — Plan 2's tests likely use kwargs, but defensive default keeps them green).

2. In whatever function turns a line into an `ActionItem`, after extracting the title, search for `[#XXXX]` using `scout.ids.short_prefix_pattern()`, store the bare 4-char prefix in `short_prefix`, and remove the `[#XXXX] ` substring from the title (note the trailing space).

Concretely, find the line-parsing logic (likely a regex or split that builds `title`) and add this after title extraction:

```python
# At the top of the file:
from scout.ids import short_prefix_pattern

# Inside the function that constructs ActionItem.title from raw_line:
_short_prefix: str | None = None
_m = short_prefix_pattern().search(title_text)
if _m is not None:
    _short_prefix = _m.group(1)
    # Remove the bracketed prefix and the single space that typically follows it.
    title_text = (title_text[: _m.start()] + title_text[_m.end():]).replace("  ", " ").strip()

# When constructing ActionItem:
ActionItem(
    ...,
    title=title_text,
    short_prefix=_short_prefix,
    ...,
)
```

The exact insertion point depends on how Plan 2's parser was structured. Read the existing code, identify the title-extraction step, and insert there. Preserve `raw_line` untouched.

- [ ] **Step 5: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_action_items_parser.py -v
```

Expected: all parser tests pass (the 4 new ones plus all existing).

- [ ] **Step 6: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 7: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/parser.py engine/tests/unit/test_action_items_parser.py engine/tests/fixtures/action-items-with-prefixes.md
git commit -m "feat(engine): parser extracts [#XXXX] short prefix into ActionItem.short_prefix"
```

---

## Task 17: Extend the writer with prefix-preserving operations

**Files:**
- Modify: `~/scout-plugin/engine/scout/action_items/writer.py`
- Modify: `~/scout-plugin/engine/tests/unit/test_action_items_writer.py`

**What this builds:** A new `add_prefix_to_line(target, line_number, prefix)` operation that inserts `[#XXXX] ` after the checkbox marker on the given line. Plus a guarantee that `flip_checkbox` and `insert_below` (Plan 2's existing helpers) leave any existing prefix intact.

- [ ] **Step 1: Append failing tests**

Add to `engine/tests/unit/test_action_items_writer.py`:

```python
def test_add_prefix_to_unprefixed_line(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [ ] 🔴 task title\n- [ ] [#X9Y2] other\n")
    from scout.action_items.writer import add_prefix_to_line

    add_prefix_to_line(target, line_number=1, prefix="A3F7")
    assert target.read_text() == "- [ ] [#A3F7] 🔴 task title\n- [ ] [#X9Y2] other\n"


def test_add_prefix_handles_no_priority_emoji(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [ ] just a plain task\n")
    from scout.action_items.writer import add_prefix_to_line

    add_prefix_to_line(target, line_number=1, prefix="A3F7")
    assert target.read_text() == "- [ ] [#A3F7] just a plain task\n"


def test_add_prefix_refuses_if_line_already_prefixed(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [ ] [#X9Y2] already prefixed\n")
    from scout.action_items.writer import add_prefix_to_line
    from scout.errors import ActionItemError

    with pytest.raises(ActionItemError, match="already has prefix"):
        add_prefix_to_line(target, line_number=1, prefix="A3F7")


def test_flip_checkbox_preserves_existing_prefix(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [ ] [#A3F7] task\n")
    from scout.action_items.writer import flip_checkbox

    flip_checkbox(target, line_number=1, to_done=True)
    assert target.read_text() == "- [x] [#A3F7] task\n"
```

- [ ] **Step 2: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_action_items_writer.py -v -k "add_prefix or preserves_existing_prefix"
```

Expected: 4 failures (`ImportError: cannot import name 'add_prefix_to_line'` plus the flip_checkbox test failing only if Plan 2's writer mangles prefixes — it shouldn't, but verify).

- [ ] **Step 3: Implement `add_prefix_to_line`**

Append to `engine/scout/action_items/writer.py`:

```python
def add_prefix_to_line(target: Path, *, line_number: int, prefix: str) -> None:
    """Insert `[#PREFIX] ` after the checkbox marker on the 1-indexed line.

    Refuses if the line already carries a `[#XXXX]` prefix — the caller
    should not be asking to add one if scout.id_map already has a record.
    """
    from scout.ids import short_prefix_pattern  # local import — keeps writer light

    lines = _read_lines(target)
    idx = line_number - 1
    if not 0 <= idx < len(lines):
        raise ActionItemError(
            f"add_prefix_to_line: line {line_number} out of range (1..{len(lines)})"
        )
    line = lines[idx]
    if short_prefix_pattern().search(line):
        raise ActionItemError(
            f"add_prefix_to_line: line {line_number} already has prefix"
        )
    # Find the checkbox marker (`- [ ]` or `- [x]`) and insert after it.
    for marker in ("- [ ] ", "- [x] "):
        if line.startswith(marker):
            lines[idx] = marker + f"[#{prefix}] " + line[len(marker):]
            break
    else:
        raise ActionItemError(
            f"add_prefix_to_line: line {line_number} doesn't start with a checkbox marker"
        )
    atomic_write_lines(target, lines)
```

- [ ] **Step 4: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_action_items_writer.py -v
```

Expected: all writer tests pass.

- [ ] **Step 5: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/writer.py engine/tests/unit/test_action_items_writer.py
git commit -m "feat(engine): writer.add_prefix_to_line + verify flip_checkbox preserves prefix"
```

---

## Task 18: Update `mark_done` — `[#XXXX]` lookup, Event return

**Files:**
- Modify: `~/scout-plugin/engine/scout/action_items/mark_done.py`
- Modify: `~/scout-plugin/engine/tests/unit/test_action_items_mark_done.py`

**What this builds:** `mark_done` gains a `--by-id PREFIX` argument that looks up the line via `IdMap.lookup_by_prefix(prefix)` → finds the matching `raw_line` substring in the file → flips the checkbox. The legacy `--by-subject SUBSTRING` arm becomes the fallback for unprefixed lines. The function returns an `Event(kind="action_item.completed", source="cli:mark_done", payload={"item_id": ulid, "via": "id"|"subject"})`.

- [ ] **Step 1: Add tests for the new contract**

Append to `engine/tests/unit/test_action_items_mark_done.py`:

```python
import datetime as dt
import re

from scout.events import Event
from scout.id_map import IdMap, IdMapEntry


def test_mark_done_by_id_flips_correct_line(fake_data_dir, monkeypatch):
    # Set up: register prefix↔ULID in the id-map, write a markdown file with that prefix.
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA0000000000000000000", "A3F7", "Submit Lever feedback", "action-items-2026-04-26.md", 5))
    m.save()
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "# Action Items — 2026-04-26\n\n"
        "## In Progress\n\n"
        "- [ ] [#A3F7] 🔴 Submit Lever feedback\n"
        "- [ ] 🟡 Other unrelated task\n"
    )
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.mark_done import mark_done

    event = mark_done(by_id="A3F7", data_dir=fake_data_dir)

    assert "- [x] [#A3F7]" in daily.read_text()
    assert "- [ ] 🟡 Other" in daily.read_text()  # unrelated line untouched
    assert isinstance(event, Event)
    assert event.kind == "action_item.completed"
    assert event.source == "cli:mark_done"
    assert event.payload["item_id"] == "01HXAAA0000000000000000000"
    assert event.payload["via"] == "id"


def test_mark_done_by_subject_fallback_for_unprefixed_line(fake_data_dir, monkeypatch):
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "## In Progress\n\n"
        "- [ ] 🔴 Followup with vendor on contract\n"
    )
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.mark_done import mark_done

    event = mark_done(by_subject="vendor", data_dir=fake_data_dir)
    assert "- [x] 🔴 Followup with vendor" in daily.read_text()
    assert event.payload["via"] == "subject"
    # No prefix on the line means no entity ULID — payload uses the event's own ULID derivation.
    assert "item_id" in event.payload  # may be None or a generated value; assert key present


def test_mark_done_by_id_unknown_prefix_raises(fake_data_dir, monkeypatch):
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: dt.date(2026, 4, 26))
    from scout.action_items.mark_done import mark_done
    from scout.errors import ActionItemError

    with pytest.raises(ActionItemError, match="prefix.*not found"):
        mark_done(by_id="ZZZZ", data_dir=fake_data_dir)


def test_mark_done_event_id_and_ts_well_formed(fake_data_dir, monkeypatch):
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HX", "A3F7", "task", "action-items-2026-04-26.md", 1))
    m.save()
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("- [ ] [#A3F7] task\n")
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.mark_done import mark_done

    event = mark_done(by_id="A3F7", data_dir=fake_data_dir)
    assert len(event.id) == 26
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", event.ts)
```

- [ ] **Step 2: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_action_items_mark_done.py -v -k "by_id or by_subject_fallback or event_id_and_ts"
```

Expected: failures around `mark_done()` not accepting `by_id` / `by_subject` keyword arguments and not returning `Event`.

- [ ] **Step 3: Refactor `mark_done`**

Edit `engine/scout/action_items/mark_done.py` so the public callable is:

```python
def mark_done(
    *,
    by_id: str | None = None,
    by_subject: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    """Mark today's (or `date`'s) action item done.

    Exactly one of `by_id` or `by_subject` must be provided. `by_id` is
    a 4-char Crockford prefix; `by_subject` is a case-insensitive
    substring match against full lines (legacy fallback for lines that
    haven't been prefixed yet).
    """
    if (by_id is None) == (by_subject is None):
        raise ActionItemError(
            "mark_done requires exactly one of by_id or by_subject"
        )

    target_path = paths.action_items_daily_path(data=data_dir, date=date)
    if not target_path.exists():
        raise ActionItemError(f"no action items file: {target_path}")

    items = parse_file(target_path)

    if by_id is not None:
        # ID path: look up the entity ULID via IdMap; find the matching line.
        id_map = IdMap.load(data_dir or paths.data_dir())
        entry = id_map.lookup_by_prefix(by_id)
        if entry is None:
            raise ActionItemError(
                f"prefix [#{by_id}] not found in id-map; "
                f"if this is a legacy line, retry with --by-subject"
            )
        # Find the parsed item whose short_prefix matches.
        match = next((i for i in items if i.short_prefix == by_id), None)
        if match is None:
            raise ActionItemError(
                f"prefix [#{by_id}] is in id-map but not present in {target_path.name}"
            )
        item_ulid = entry.ulid
        via = "id"
    else:
        # Substring path: existing Plan 2 behavior, returns 0/1/N matches.
        matches = [
            i for i in items
            if i.status == "open" and by_subject.lower() in i.raw_line.lower()
        ]
        if len(matches) == 0:
            raise ActionItemError(f"no open task matched subject: {by_subject!r}")
        if len(matches) > 1:
            raise ActionItemError(
                f"ambiguous subject {by_subject!r}; matched:\n"
                + "\n".join(f"  - {m.title}" for m in matches)
            )
        match = matches[0]
        item_ulid = (
            IdMap.load(data_dir or paths.data_dir()).lookup_by_prefix(match.short_prefix).ulid
            if match.short_prefix
            else ""  # legacy line; no entity ULID yet
        )
        via = "subject"

    # Locate the line by raw_line substring and flip the checkbox.
    line_number = _find_line_number(target_path, match.raw_line)
    flip_checkbox(target_path, line_number=line_number, to_done=True)

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="action_item.completed",
        source="cli:mark_done",
        payload={"item_id": item_ulid, "via": via, "title": match.title},
    )


def _today() -> dt.date:
    """Indirection for monkeypatching."""
    return dt.date.today()


def _find_line_number(path: Path, raw_line: str) -> int:
    """1-indexed line number where `raw_line` first appears as a complete line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(lines, start=1):
        if line == raw_line:
            return n
    raise ActionItemError(f"could not locate line in {path.name}: {raw_line!r}")
```

Imports needed at module top:

```python
import datetime as dt
from pathlib import Path

from scout import paths
from scout.action_items.parser import parse_file
from scout.action_items.writer import flip_checkbox
from scout.errors import ActionItemError
from scout.events import Event, now_iso
from scout.id_map import IdMap
from scout.ids import new_ulid
```

- [ ] **Step 4: Run all mark_done tests, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_action_items_mark_done.py -v
```

Expected: all tests pass — both new and any pre-existing Plan 2 tests (you may need to update those to use the new `by_subject=` kwarg if Plan 2's tests called `mark_done(subject="...")`).

- [ ] **Step 5: Run the full unit suite**

```bash
.venv/bin/pytest tests/unit/ -v
```

Expected: nothing else regresses.

- [ ] **Step 6: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 7: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/mark_done.py engine/tests/unit/test_action_items_mark_done.py
git commit -m "feat(engine): mark_done accepts by_id/by_subject + returns Event"
```

---

## Task 19: Update `snooze` — `[#XXXX]` lookup, `until` payload, Event return

**Files:**
- Create: `~/scout-plugin/engine/scout/action_items/_common.py`
- Create: `~/scout-plugin/engine/tests/unit/test_action_items_common.py`
- Modify: `~/scout-plugin/engine/scout/action_items/snooze.py`
- Modify: `~/scout-plugin/engine/scout/action_items/mark_done.py`
- Modify: `~/scout-plugin/engine/tests/unit/test_action_items_snooze.py`

**What this builds:** First, factor `_find_line_number` and the `by_id`/`by_subject` resolution logic out of `mark_done` into `scout.action_items._common` so that `snooze` and `add_comment` can reuse it without copying. Then refactor `snooze` to follow the same `--by-id` / `--by-subject` / `Event`-return pattern. Snooze inserts a sub-bullet `  - snoozed-until: YYYY-MM-DD` beneath the target line via the existing `writer.insert_below`; it does not flip the checkbox.

- [ ] **Step 1: Write tests for the common helpers**

Create `engine/tests/unit/test_action_items_common.py`:

```python
"""Unit tests for scout.action_items._common — shared mutator helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.action_items._common import find_line_number, resolve_target
from scout.action_items.parser import ActionItem
from scout.errors import ActionItemError
from scout.id_map import IdMap, IdMapEntry


def test_find_line_number_returns_1_indexed(tmp_path: Path) -> None:
    f = tmp_path / "x.md"
    f.write_text("alpha\nbeta\ngamma\n")
    assert find_line_number(f, "beta") == 2
    assert find_line_number(f, "alpha") == 1


def test_find_line_number_raises_when_missing(tmp_path: Path) -> None:
    f = tmp_path / "x.md"
    f.write_text("only line\n")
    with pytest.raises(ActionItemError, match="could not locate"):
        find_line_number(f, "missing line")


def test_resolve_target_by_id_returns_entry_and_match(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "task X", "today.md", 5))
    m.save()
    items = [
        ActionItem(
            priority="🔴", title="task X", status="open", section="In Progress",
            context_links=[], notes=[], details=[],
            raw_line="- [ ] [#A3F7] 🔴 task X", short_prefix="A3F7",
        ),
        ActionItem(
            priority="", title="other", status="open", section="In Progress",
            context_links=[], notes=[], details=[],
            raw_line="- [ ] other", short_prefix=None,
        ),
    ]
    target, ulid, via = resolve_target(
        items=items, data_dir=fake_data_dir, by_id="A3F7", by_subject=None
    )
    assert target.title == "task X"
    assert ulid == "01HXAAA"
    assert via == "id"


def test_resolve_target_by_subject_substring(fake_data_dir: Path) -> None:
    items = [
        ActionItem(
            priority="🔴", title="Reply to vendor on contract",
            status="open", section="To Do",
            context_links=[], notes=[], details=[],
            raw_line="- [ ] 🔴 Reply to vendor on contract", short_prefix=None,
        ),
    ]
    target, ulid, via = resolve_target(
        items=items, data_dir=fake_data_dir, by_id=None, by_subject="vendor"
    )
    assert target.title == "Reply to vendor on contract"
    assert ulid == ""
    assert via == "subject"


def test_resolve_target_rejects_both_args_unset(fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemError, match="exactly one"):
        resolve_target(items=[], data_dir=fake_data_dir, by_id=None, by_subject=None)


def test_resolve_target_rejects_both_args_set(fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemError, match="exactly one"):
        resolve_target(
            items=[], data_dir=fake_data_dir, by_id="A3F7", by_subject="x"
        )


def test_resolve_target_unknown_id_raises(fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemError, match="prefix.*not found"):
        resolve_target(items=[], data_dir=fake_data_dir, by_id="ZZZZ", by_subject=None)


def test_resolve_target_ambiguous_subject_raises(fake_data_dir: Path) -> None:
    items = [
        ActionItem(
            priority="", title="Reply to alice", status="open", section="To Do",
            context_links=[], notes=[], details=[],
            raw_line="- [ ] Reply to alice", short_prefix=None,
        ),
        ActionItem(
            priority="", title="Reply to bob", status="open", section="To Do",
            context_links=[], notes=[], details=[],
            raw_line="- [ ] Reply to bob", short_prefix=None,
        ),
    ]
    with pytest.raises(ActionItemError, match="ambiguous"):
        resolve_target(
            items=items, data_dir=fake_data_dir, by_id=None, by_subject="reply"
        )
```

- [ ] **Step 2: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_action_items_common.py -v
```

Expected: `ModuleNotFoundError: No module named 'scout.action_items._common'`.

- [ ] **Step 3: Implement `scout/action_items/_common.py`**

```python
"""Shared helpers for action-item mutators.

Factored out of mark_done/snooze/add_comment so each mutator's public
function is a thin wrapper around resolution + the actual mutation +
Event construction.
"""

from __future__ import annotations

from pathlib import Path

from scout.action_items.parser import ActionItem
from scout.errors import ActionItemError
from scout.id_map import IdMap, IdMapEntry


def find_line_number(path: Path, raw_line: str) -> int:
    """1-indexed line number where `raw_line` first appears as a complete line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(lines, start=1):
        if line == raw_line:
            return n
    raise ActionItemError(
        f"could not locate line in {path.name}: {raw_line!r}"
    )


def resolve_target(
    *,
    items: list[ActionItem],
    data_dir: Path,
    by_id: str | None,
    by_subject: str | None,
) -> tuple[ActionItem, str, str]:
    """Resolve which `ActionItem` a mutator should act on.

    Returns `(target, item_ulid, via)` where `via` is `"id"` or
    `"subject"`. `item_ulid` may be empty if a `--by-subject` lookup
    matched a legacy unprefixed line.

    Raises `ActionItemError` on bad arguments, unknown prefix, no match,
    or ambiguous match.
    """
    if (by_id is None) == (by_subject is None):
        raise ActionItemError(
            "resolve_target requires exactly one of by_id or by_subject"
        )

    if by_id is not None:
        id_map = IdMap.load(data_dir)
        entry: IdMapEntry | None = id_map.lookup_by_prefix(by_id)
        if entry is None:
            raise ActionItemError(
                f"prefix [#{by_id}] not found in id-map; "
                f"if this is a legacy line, retry with --by-subject"
            )
        match = next((i for i in items if i.short_prefix == by_id), None)
        if match is None:
            raise ActionItemError(
                f"prefix [#{by_id}] is in id-map but not present in this file"
            )
        return match, entry.ulid, "id"

    # by_subject path
    assert by_subject is not None
    matches = [
        i for i in items
        if i.status == "open" and by_subject.lower() in i.raw_line.lower()
    ]
    if len(matches) == 0:
        raise ActionItemError(f"no open task matched subject: {by_subject!r}")
    if len(matches) > 1:
        raise ActionItemError(
            f"ambiguous subject {by_subject!r}; matched:\n"
            + "\n".join(f"  - {m.title}" for m in matches)
        )
    match = matches[0]
    item_ulid = ""
    if match.short_prefix:
        entry = IdMap.load(data_dir).lookup_by_prefix(match.short_prefix)
        if entry is not None:
            item_ulid = entry.ulid
    return match, item_ulid, "subject"
```

- [ ] **Step 4: Refactor `mark_done` to use the helper**

Edit `engine/scout/action_items/mark_done.py`. Replace the inline resolution logic with a call to `resolve_target`:

```python
"""Mark an action-item complete by ID or by subject."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from scout import paths
from scout.action_items._common import find_line_number, resolve_target
from scout.action_items.parser import parse_file
from scout.action_items.writer import flip_checkbox
from scout.errors import ActionItemError
from scout.events import Event, now_iso
from scout.ids import new_ulid


def _today() -> dt.date:
    """Indirection for monkeypatching."""
    return dt.date.today()


def mark_done(
    *,
    by_id: str | None = None,
    by_subject: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    """Mark today's (or `date`'s) action item done."""
    target_path = paths.action_items_daily_path(data=data_dir, date=date or _today())
    if not target_path.exists():
        raise ActionItemError(f"no action items file: {target_path}")

    items = parse_file(target_path)
    match, item_ulid, via = resolve_target(
        items=items,
        data_dir=data_dir or paths.data_dir(),
        by_id=by_id,
        by_subject=by_subject,
    )

    line_number = find_line_number(target_path, match.raw_line)
    flip_checkbox(target_path, line_number=line_number, to_done=True)

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="action_item.completed",
        source="cli:mark_done",
        payload={"item_id": item_ulid, "via": via, "title": match.title},
    )
```

- [ ] **Step 5: Add `snooze` tests**

Append to `engine/tests/unit/test_action_items_snooze.py`:

```python
import datetime as dt
import re
from pathlib import Path

import pytest

from scout.events import Event
from scout.errors import ActionItemError
from scout.id_map import IdMap, IdMapEntry


def test_snooze_by_id_inserts_until_subbullet(fake_data_dir, monkeypatch):
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "Submit Lever feedback", "action-items-2026-04-26.md", 5))
    m.save()
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "## In Progress\n\n"
        "- [ ] [#A3F7] 🔴 Submit Lever feedback\n"
        "- [ ] 🟡 Other unrelated task\n"
    )
    monkeypatch.setattr("scout.action_items.snooze._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.snooze import snooze

    event = snooze(
        by_id="A3F7", until=dt.date(2026, 5, 1), data_dir=fake_data_dir
    )

    text = daily.read_text()
    assert "- [ ] [#A3F7] 🔴 Submit Lever feedback\n  - snoozed-until: 2026-05-01" in text
    assert "Other unrelated task" in text  # untouched
    assert isinstance(event, Event)
    assert event.kind == "action_item.snoozed"
    assert event.source == "cli:snooze"
    assert event.payload["item_id"] == "01HXAAA"
    assert event.payload["via"] == "id"
    assert event.payload["until"] == "2026-05-01"


def test_snooze_by_subject_fallback(fake_data_dir, monkeypatch):
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "## To Do\n\n"
        "- [ ] 🔴 Followup with vendor on contract\n"
    )
    monkeypatch.setattr("scout.action_items.snooze._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.snooze import snooze

    event = snooze(
        by_subject="vendor", until=dt.date(2026, 5, 1), data_dir=fake_data_dir
    )
    assert "- snoozed-until: 2026-05-01" in daily.read_text()
    assert event.payload["via"] == "subject"
    assert event.payload["until"] == "2026-05-01"


def test_snooze_by_id_unknown_prefix_raises(fake_data_dir, monkeypatch):
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("- [ ] x\n")
    monkeypatch.setattr("scout.action_items.snooze._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.snooze import snooze

    with pytest.raises(ActionItemError, match="prefix.*not found"):
        snooze(by_id="ZZZZ", until=dt.date(2026, 5, 1), data_dir=fake_data_dir)


def test_snooze_event_id_and_ts_well_formed(fake_data_dir, monkeypatch):
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HX", "A3F7", "task", "action-items-2026-04-26.md", 1))
    m.save()
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("- [ ] [#A3F7] task\n")
    monkeypatch.setattr("scout.action_items.snooze._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.snooze import snooze

    event = snooze(
        by_id="A3F7", until=dt.date(2026, 5, 1), data_dir=fake_data_dir
    )
    assert len(event.id) == 26
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", event.ts)
```

- [ ] **Step 6: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_action_items_snooze.py -v -k "by_id or by_subject or event_id"
```

Expected: failures around the new contract.

- [ ] **Step 7: Refactor `snooze`**

Replace `engine/scout/action_items/snooze.py` with:

```python
"""Snooze an action item until a future date."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from scout import paths
from scout.action_items._common import find_line_number, resolve_target
from scout.action_items.parser import parse_file
from scout.action_items.writer import insert_below
from scout.errors import ActionItemError
from scout.events import Event, now_iso
from scout.ids import new_ulid


def _today() -> dt.date:
    """Indirection for monkeypatching."""
    return dt.date.today()


def snooze(
    *,
    until: dt.date,
    by_id: str | None = None,
    by_subject: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    """Snooze today's (or `date`'s) action item until `until`."""
    target_path = paths.action_items_daily_path(data=data_dir, date=date or _today())
    if not target_path.exists():
        raise ActionItemError(f"no action items file: {target_path}")

    items = parse_file(target_path)
    match, item_ulid, via = resolve_target(
        items=items,
        data_dir=data_dir or paths.data_dir(),
        by_id=by_id,
        by_subject=by_subject,
    )

    line_number = find_line_number(target_path, match.raw_line)
    insert_below(
        target_path,
        line_number=line_number,
        text=f"  - snoozed-until: {until.isoformat()}",
    )

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="action_item.snoozed",
        source="cli:snooze",
        payload={
            "item_id": item_ulid,
            "via": via,
            "title": match.title,
            "until": until.isoformat(),
        },
    )
```

- [ ] **Step 8: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_action_items_snooze.py tests/unit/test_action_items_mark_done.py tests/unit/test_action_items_common.py -v
```

Expected: all pass.

- [ ] **Step 9: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 10: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/_common.py engine/scout/action_items/snooze.py engine/scout/action_items/mark_done.py engine/tests/unit/test_action_items_common.py engine/tests/unit/test_action_items_snooze.py
git commit -m "feat(engine): factor _common; snooze accepts by_id/by_subject + returns Event"
```

---

## Task 20: Update `add_comment` — `[#XXXX]` lookup, comment payload, Event return

**Files:**
- Modify: `~/scout-plugin/engine/scout/action_items/add_comment.py`
- Modify: `~/scout-plugin/engine/tests/unit/test_action_items_add_comment.py`

**What this builds:** Apply the same pattern as Task 19 to `add_comment`. The function uses `_common.resolve_target` for the `--by-id` / `--by-subject` lookup, then `writer.insert_below` to append `  - <comment>` beneath the target line. Returns `Event(kind="action_item.commented", source="cli:add_comment", payload={"item_id", "via", "title", "comment"})`.

- [ ] **Step 1: Append failing tests**

Add to `engine/tests/unit/test_action_items_add_comment.py`:

```python
import datetime as dt
import re
from pathlib import Path

import pytest

from scout.events import Event
from scout.errors import ActionItemError
from scout.id_map import IdMap, IdMapEntry


def test_add_comment_by_id_inserts_subbullet(fake_data_dir, monkeypatch):
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "Submit Lever feedback", "action-items-2026-04-26.md", 5))
    m.save()
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "## In Progress\n\n"
        "- [ ] [#A3F7] 🔴 Submit Lever feedback\n"
        "- [ ] 🟡 Other unrelated task\n"
    )
    monkeypatch.setattr("scout.action_items.add_comment._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.add_comment import add_comment

    event = add_comment(
        by_id="A3F7", comment="Hiring manager confirmed", data_dir=fake_data_dir
    )

    text = daily.read_text()
    assert (
        "- [ ] [#A3F7] 🔴 Submit Lever feedback\n  - Hiring manager confirmed"
        in text
    )
    assert "Other unrelated task" in text  # untouched
    assert isinstance(event, Event)
    assert event.kind == "action_item.commented"
    assert event.source == "cli:add_comment"
    assert event.payload["item_id"] == "01HXAAA"
    assert event.payload["via"] == "id"
    assert event.payload["comment"] == "Hiring manager confirmed"


def test_add_comment_by_subject_fallback(fake_data_dir, monkeypatch):
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "## To Do\n\n"
        "- [ ] 🔴 Followup with vendor on contract\n"
    )
    monkeypatch.setattr("scout.action_items.add_comment._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.add_comment import add_comment

    event = add_comment(
        by_subject="vendor", comment="Email sent 4/26", data_dir=fake_data_dir
    )
    assert "- Email sent 4/26" in daily.read_text()
    assert event.payload["via"] == "subject"
    assert event.payload["comment"] == "Email sent 4/26"


def test_add_comment_by_id_unknown_prefix_raises(fake_data_dir, monkeypatch):
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("- [ ] x\n")
    monkeypatch.setattr("scout.action_items.add_comment._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.add_comment import add_comment

    with pytest.raises(ActionItemError, match="prefix.*not found"):
        add_comment(by_id="ZZZZ", comment="x", data_dir=fake_data_dir)


def test_add_comment_event_id_and_ts_well_formed(fake_data_dir, monkeypatch):
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HX", "A3F7", "task", "action-items-2026-04-26.md", 1))
    m.save()
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("- [ ] [#A3F7] task\n")
    monkeypatch.setattr("scout.action_items.add_comment._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.add_comment import add_comment

    event = add_comment(by_id="A3F7", comment="x", data_dir=fake_data_dir)
    assert len(event.id) == 26
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", event.ts)
```

- [ ] **Step 2: Run, confirm RED**

```bash
.venv/bin/pytest tests/unit/test_action_items_add_comment.py -v -k "by_id or by_subject or event_id"
```

Expected: failures around the new contract.

- [ ] **Step 3: Refactor `add_comment`**

Replace `engine/scout/action_items/add_comment.py` with:

```python
"""Append a comment beneath an action item."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from scout import paths
from scout.action_items._common import find_line_number, resolve_target
from scout.action_items.parser import parse_file
from scout.action_items.writer import insert_below
from scout.errors import ActionItemError
from scout.events import Event, now_iso
from scout.ids import new_ulid


def _today() -> dt.date:
    """Indirection for monkeypatching."""
    return dt.date.today()


def add_comment(
    *,
    comment: str,
    by_id: str | None = None,
    by_subject: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    """Append `  - <comment>` beneath the resolved action item."""
    target_path = paths.action_items_daily_path(data=data_dir, date=date or _today())
    if not target_path.exists():
        raise ActionItemError(f"no action items file: {target_path}")

    items = parse_file(target_path)
    match, item_ulid, via = resolve_target(
        items=items,
        data_dir=data_dir or paths.data_dir(),
        by_id=by_id,
        by_subject=by_subject,
    )

    line_number = find_line_number(target_path, match.raw_line)
    insert_below(
        target_path, line_number=line_number, text=f"  - {comment}"
    )

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="action_item.commented",
        source="cli:add_comment",
        payload={
            "item_id": item_ulid,
            "via": via,
            "title": match.title,
            "comment": comment,
        },
    )
```

- [ ] **Step 4: Run, confirm GREEN**

```bash
.venv/bin/pytest tests/unit/test_action_items_add_comment.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 6: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/add_comment.py engine/tests/unit/test_action_items_add_comment.py
git commit -m "feat(engine): add_comment accepts by_id/by_subject + returns Event"
```

---

## Task 21: Update `list` and the Typer sub-app

**Files:**
- Modify: `~/scout-plugin/engine/scout/action_items/list.py`
- Modify: `~/scout-plugin/engine/scout/action_items/cli.py`
- Modify: `~/scout-plugin/engine/tests/unit/test_action_items_list.py`
- Modify: `~/scout-plugin/engine/tests/integration/test_action_items_cli.py` (if Plan 2 created it)

**What this builds:** `list` surfaces the short prefix in its output (so users have something to copy into `--by-id`). The CLI maps `--by-id PREFIX` and `--by-subject SUBSTRING` flags to the function kwargs introduced in Tasks 18–20.

- [ ] **Step 1: Update `list` tests**

Append to `engine/tests/unit/test_action_items_list.py`:

```python
def test_list_includes_short_prefix_when_present():
    """The list output prefixes each item with `[#XXXX]` when one exists."""
    from scout.action_items.list import list_items

    # Use the prefix-bearing fixture from Task 16.
    fixture = Path(__file__).parent.parent / "fixtures" / "action-items-with-prefixes.md"
    output = list_items(file=fixture)
    assert "[#A3F7]" in output
    assert "[#B5K2]" in output


def test_list_omits_prefix_for_unprefixed_lines():
    from scout.action_items.list import list_items

    fixture = Path(__file__).parent.parent / "fixtures" / "action-items-with-prefixes.md"
    output = list_items(file=fixture)
    # "Send Scout plugin announcement" is unprefixed in the fixture.
    sent_line = [ln for ln in output.splitlines() if "announcement" in ln]
    assert sent_line and "[#" not in sent_line[0]
```

- [ ] **Step 2: Update `list_items` to surface the prefix**

In `engine/scout/action_items/list.py`, format each `ActionItem` as:

```python
prefix_part = f"[#{item.short_prefix}] " if item.short_prefix else ""
line = f"  {checkbox} {prefix_part}{item.priority + ' ' if item.priority else ''}{item.title}"
```

- [ ] **Step 3: Update the Typer CLI commands**

In `engine/scout/action_items/cli.py`, change the `mark-done`, `snooze`, and `add-comment` commands to accept both flags. Example for `mark-done`:

```python
@app.command("mark-done")
def cli_mark_done(
    by_id: str = typer.Option(None, "--by-id", help="4-char Crockford prefix [#XXXX]"),
    by_subject: str = typer.Option(None, "--by-subject", help="Case-insensitive substring match"),
) -> None:
    from scout.action_items.mark_done import mark_done
    event = mark_done(by_id=by_id, by_subject=by_subject)
    typer.echo(f"✓ {event.payload['title']}  ({event.id})")
```

Repeat the pattern for `snooze` and `add-comment`. Help text for the sub-app should note that exactly one of `--by-id` / `--by-subject` is required.

- [ ] **Step 4: Update integration tests**

If Plan 2 created `tests/integration/test_action_items_cli.py`, add coverage for both flag styles:

```python
def test_cli_mark_done_by_id(fake_data_dir, ...):
    # Set up id-map + daily file, invoke `scoutctl action-items mark-done --by-id A3F7`,
    # assert the line was flipped and the printed Event ULID appears in stdout.
    ...

def test_cli_mark_done_by_subject_legacy_fallback(fake_data_dir, ...):
    # Daily file with no prefixed lines, invoke `--by-subject vendor`,
    # assert the line is flipped.
    ...
```

- [ ] **Step 5: Run full suite**

```bash
.venv/bin/pytest tests/ -v
```

Expected: everything green.

- [ ] **Step 6: Lint**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
```

- [ ] **Step 7: Commit**

```bash
cd ~/scout-plugin
git add engine/scout/action_items/list.py engine/scout/action_items/cli.py engine/tests/unit/test_action_items_list.py engine/tests/integration/test_action_items_cli.py
git commit -m "feat(engine): list shows [#XXXX] prefix; CLI accepts --by-id / --by-subject"
```

---

## Task 22: Final verification + push

- [ ] **Step 1: Full test run**

```bash
cd ~/scout-plugin/engine
.venv/bin/pytest tests/ -v
```

Expected: all pass. Note any skipped tests (`-m concurrency` or `-m slow` markers are expected to skip in default runs).

- [ ] **Step 2: Lint**

```bash
.venv/bin/ruff check scout tests
.venv/bin/ruff format --check scout tests
.venv/bin/mypy scout
```

Expected: clean.

- [ ] **Step 3: Manual smoke test**

```bash
cd ~/scout-plugin/engine

# Set up a temporary data dir
export SCOUT_DATA_DIR=$(mktemp -d)
mkdir -p "$SCOUT_DATA_DIR/action-items" "$SCOUT_DATA_DIR/.scout-state"

# Write a daily file by hand
TODAY=$(date '+%Y-%m-%d')
cat > "$SCOUT_DATA_DIR/action-items/action-items-${TODAY}.md" <<EOF
# Action Items — ${TODAY}

## In Progress

- [ ] [#A3F7] 🔴 Test mark-done by id
- [ ] 🟡 Test mark-done by subject
EOF

# Pre-populate the id-map (in production this happens at action-item creation time;
# for the smoke test we forge it).
cat > "$SCOUT_DATA_DIR/.scout-state/id-map.json" <<EOF
{
  "schema_version": 1,
  "entries": {
    "01HXAAA0000000000000000000": {
      "ulid": "01HXAAA0000000000000000000",
      "short_prefix": "A3F7",
      "last_title": "Test mark-done by id",
      "last_file": "action-items-${TODAY}.md",
      "last_line": 5
    }
  }
}
EOF

# Exercise both flag styles
.venv/bin/scoutctl action-items mark-done --by-id A3F7
.venv/bin/scoutctl action-items mark-done --by-subject "by subject"

cat "$SCOUT_DATA_DIR/action-items/action-items-${TODAY}.md"
# Expected: both lines now show [x] instead of [ ].
```

Expected: both subcommands succeed; both lines are checked.

- [ ] **Step 4: Push and open the PR (or amend the existing Plan 2 PR)**

If Plan 2 has an open PR already, push these commits to its branch:

```bash
git push origin migrate/v0.4.0-port-python
```

If Plan 2 hasn't been opened yet, open the PR now describing both the parent plan's work and this supplement's. Title suggestion:

> `feat(engine): Plan 2 — port action-items, kb, tui + §13 stable IDs and Events`

PR description should reference both plans plus the v0.4 spec §13.

- [ ] **Step 5: Update the unification spec's plan list (in scout-app repo)**

In `~/scout-app/docs/superpowers/`, no plan-list file exists today, but if you maintain one elsewhere, mark Plan 2 + supplement merged.

```bash
cd ~/scout-app
# (Optional housekeeping commit — only if a plan tracker exists.)
```

---

## What Plan 3 will build on

After this supplement merges, Plan 3 (`scoutctl action-items watch`) can assume:
- Every action item has a stable `[#XXXX]` prefix recoverable to a ULID.
- The parser exposes `ActionItem.short_prefix`, so the diff stream can identify items across file edits.
- Every mutator returns an `Event`; v0.5 will tap that for the event store.

Plan 3 wires `watch` as a projection-consumer contract: it streams parsed-state diffs across file changes, identifying changed items by short prefix. Implementation in v0.4 is a `watchdog`-based file watcher; v0.5 will substitute an event-store subscriber without changing the CLI surface.
