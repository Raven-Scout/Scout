"""The connector-call ledger: ``.scout-logs/connector-calls-YYYY-MM-DD.jsonl``.

One JSON row per tool call in a scheduled run, one file per configured-zone day.
Two writers append to it, often at the same moment: the PostToolUse hook
(``hooks/connector_log``) and the Stop hook's transcript replay
(``hooks/session_tool_log``). Its only reader is the 14-day connector health
report (``scripts/connector_health_report``); run health reads the run ledger
and runner logs, not these files.

Bounds (#66), the same for every vault:

- **Retention:** files dated more than :data:`RETAIN_DAYS` (30) days ago are
  deleted, which keeps more than twice the report's 14-day window. A writer
  prunes when it starts a new day's file, and the report prunes on every run,
  so retention holds even when the report is turned off.
- **Daily cap:** a day's file stops growing at :data:`MAX_DAY_BYTES` (64 MiB).
  A normal day is 0.2–0.4 MB, so only a runaway loop reaches it. The first
  row that doesn't fit is replaced by one marker row (connector
  :data:`CAP_CONNECTOR`, recording when the cap was hit), which may take the
  file a few hundred bytes past the cap; from then on that day's file is
  closed and every later row is dropped, with a stderr line each time. The
  health report lifts the marker out of its counts and shows it as a notice,
  so a capped day's low numbers are never silent.

Every append happens under an exclusive ``flock`` on the file, and the cap is
checked under that lock, so concurrent writers never interleave rows or
overshoot the cap.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

try:
    import fcntl
except ImportError:  # non-POSIX (Windows) — advisory locks unavailable.
    fcntl = None  # type: ignore[assignment]

PREFIX = "connector-calls-"
SUFFIX = ".jsonl"
GLOB = f"{PREFIX}*{SUFFIX}"
RETAIN_DAYS = 30
MAX_DAY_BYTES = 64 * 1024 * 1024
CAP_CONNECTOR = "connector-log:cap"
# Rows are a few hundred bytes (error snippets are cut at 160 chars), so a
# file within this distance of the cap may already end in the marker.
_CAP_TAIL_BYTES = 64 * 1024


def lock_exclusive(f: IO[str]) -> None:
    """Take an exclusive advisory lock; no-op where fcntl is unavailable.

    Released implicitly when the file is closed.
    """
    if fcntl is not None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)


def day_path(log_dir: Path, day: str) -> Path:
    """The ledger file for ``day`` (``YYYY-MM-DD``)."""
    return log_dir / f"{PREFIX}{day}{SUFFIX}"


def prune(log_dir: Path, *, retain_days: int = RETAIN_DAYS, now: datetime | None = None) -> list[Path]:
    """Delete ledger files dated before ``now - retain_days``; return what went.

    The date comes from the file name, read as UTC midnight (the bash
    original's rule). A name that isn't a date (``connector-calls-backup.jsonl``)
    is left alone, and so is anything that vanishes or can't be removed —
    another writer may be pruning at the same moment.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retain_days)
    removed: list[Path] = []
    for path in log_dir.glob(GLOB):
        try:
            fdate = datetime.strptime(path.name[len(PREFIX) : -len(SUFFIX)], "%Y-%m-%d").replace(tzinfo=UTC)
            if fdate < cutoff:
                os.remove(path)
                removed.append(path)
        except (ValueError, OSError):
            continue
    return removed


def _capped(path: Path, size: int, max_bytes: int) -> bool:
    """True when the file already ends in the cap marker (it is closed for the day)."""
    if size <= max_bytes - _CAP_TAIL_BYTES:
        return False
    try:
        with path.open("rb") as r:
            r.seek(max(0, size - 4096))
            tail = r.read(4096)
    except OSError:
        return False
    last = tail.rstrip(b"\n").rsplit(b"\n", 1)[-1]
    return CAP_CONNECTOR.encode() in last


def _cap_marker(dropped_line: str, max_bytes: int) -> str:
    """The one row that records the cap, attributed to the first dropped row's run."""
    try:
        row = json.loads(dropped_line)
        session_id, mode = str(row.get("session_id", "unknown")), str(row.get("mode") or "connector-log")
    except (ValueError, AttributeError):
        session_id, mode = "unknown", "connector-log"
    marker = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "session_id": session_id,
        "mode": mode,
        "tool": "connector-log",
        "connector": CAP_CONNECTOR,
        "error": True,
        "err": f"daily cap of {max_bytes} bytes reached; later calls this day are not logged",
    }
    return json.dumps(marker) + "\n"


def append_rows(path: Path, lines: Sequence[str], *, max_bytes: int = MAX_DAY_BYTES) -> int:
    """Append newline-terminated ``lines`` to ``path`` under the file lock.

    Writes the rows that fit under ``max_bytes``. The first time a row doesn't
    fit, one cap marker row is written in its place and the day's file is
    closed: this and every later row are dropped, with a stderr line. Returns
    how many of ``lines`` were written. Raises ``OSError`` if the file can't
    be opened or written; callers on a hook path catch it.

    When this append creates the file — the first row of a new day — old
    files are pruned afterwards, outside the lock.
    """
    if not lines:
        return 0
    new_day = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        # Hooks fire concurrently for parallel tool calls. O_APPEND only
        # guarantees atomicity up to PIPE_BUF (~512B on some macOS FS), so
        # large rows can interleave. An exclusive advisory lock serializes
        # the append; the lock releases on close. (#38)
        lock_exclusive(f)
        size = os.fstat(f.fileno()).st_size
        kept: list[str] = []
        if not _capped(path, size, max_bytes):
            for line in lines:
                n = len(line.encode("utf-8"))
                if size + n > max_bytes:
                    break
                kept.append(line)
                size += n
            if len(kept) < len(lines):
                f.write("".join(kept) + _cap_marker(lines[len(kept)], max_bytes))
            else:
                f.write("".join(kept))
    dropped = len(lines) - len(kept)
    if dropped:
        print(
            f"connector-log: {path.name} reached its {max_bytes} byte daily cap; dropped {dropped} row(s)",
            file=sys.stderr,
        )
    if new_day:
        prune(path.parent)  # swallows per-file errors itself
    return len(kept)
