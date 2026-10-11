"""Optional per-task board fields carried as machine sub-bullets.

    - [ ] [#REPLYX] **Reply to Alex about her purchase question**
      - Topic: #STRM
      - State: waiting
      - Depends: #XREF, #QBLK
      - Session: https://claude.ai/code/session_0123
      - Priority: 2
      - Cycle: current

Every field is optional; a task without them parses exactly as before. The
keys are reserved: a sub-bullet using one is metadata, never a comment, even
when its value is invalid (the value is then ignored).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

TASK_STATES = frozenset({"mine", "doing", "waiting", "blocked", "hold"})
TASK_FIELD_KEYS = frozenset({"topic", "state", "depends", "session", "priority", "cycle"})
PRIORITY_RANGE = range(0, 5)

_FIELD_LINE_RE = re.compile(r"^\s+-\s+(?P<key>[A-Za-z]+)\s*:\s*(?P<value>.*?)\s*$")
_DEPENDS_TOKEN_RE = re.compile(r"\[?#?(?P<tag>(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{2,8})\]?")
_DEPENDS_SPLIT_RE = re.compile(r"\s*(?:,|·|\s)\s*")


@dataclass(frozen=True)
class TaskFields:
    topic: str | None = None
    state: str | None = None
    depends: tuple[str, ...] = field(default_factory=tuple)
    session: str | None = None
    priority: int | None = None
    cycle: str | None = None


def match_field_line(line: str) -> tuple[str, str] | None:
    """Return `(key, value)` when `line` is an indented reserved-key sub-bullet."""
    m = _FIELD_LINE_RE.match(line)
    if m is None:
        return None
    key = m.group("key").lower()
    if key not in TASK_FIELD_KEYS:
        return None
    return key, m.group("value")


def parse_state(value: str) -> str | None:
    state = value.strip().lower()
    return state if state in TASK_STATES else None


def parse_depends(value: str) -> tuple[str, ...]:
    tokens = (_DEPENDS_TOKEN_RE.fullmatch(t) for t in _DEPENDS_SPLIT_RE.split(value.strip()) if t)
    return tuple(m.group("tag").upper() for m in tokens if m is not None)


def parse_topic(value: str) -> str | None:
    return next(iter(parse_depends(value)), None)


def parse_priority(value: str) -> int | None:
    stripped = value.strip()
    if not (stripped.isascii() and stripped.isdigit()):
        return None
    priority = int(stripped)
    return priority if priority in PRIORITY_RANGE else None


def parse_text(value: str) -> str | None:
    return value.strip() or None


def parse_cycle(value: str) -> str | None:
    return value.strip().lower() or None


def apply_field(fields: TaskFields, key: str, value: str) -> TaskFields:
    """Return `fields` with `key` set from its raw markdown `value`."""
    match key:
        case "topic":
            return replace(fields, topic=parse_topic(value))
        case "state":
            return replace(fields, state=parse_state(value))
        case "depends":
            return replace(fields, depends=parse_depends(value))
        case "session":
            return replace(fields, session=parse_text(value))
        case "priority":
            return replace(fields, priority=parse_priority(value))
        case "cycle":
            return replace(fields, cycle=parse_cycle(value))
    raise KeyError(key)
