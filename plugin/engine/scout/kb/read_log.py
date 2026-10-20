"""The ``scoutctl kb`` audit log (#453, spec §7.4).

Every ``scoutctl kb`` call by a reader other than ``owner``, and every blocked
check (``check-outbound`` / ``check-query`` failures, any reader), appends one
JSON line to ``<vault>/.scout-logs/kb-reads.jsonl`` with exactly
:data:`LOG_FIELDS`.

The log holds no content: the arguments are a SHA-256 hash, and the paths a
call returned, was denied, or returned as private are counts, not paths, so not
even a private note's slug reaches the log. A failure to write it never fails
the read: it costs a warning on stderr.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

LOG_NAME = "kb-reads.jsonl"
LOG_FIELDS = (
    "ts",
    "reader",
    "command",
    "surface",
    "args_hash",
    "paths_returned",
    "paths_denied",
    "private_paths",
    "blocked",
    "scout_mode",
)


def args_hash(args: Sequence[str]) -> str:
    """SHA-256 hex of the arguments joined with NUL."""
    return hashlib.sha256("\x00".join(args).encode("utf-8", errors="surrogatepass")).hexdigest()


def log_event(
    vault: Path,
    *,
    reader: str,
    command: str,
    surface: str | None,
    args: Sequence[str],
    returned: int,
    denied: int,
    private_paths: int,
    blocked: bool,
) -> None:
    """Append one audit line, unless this is an unblocked ``owner`` call."""
    if reader == "owner" and not blocked:
        return
    row = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "reader": reader,
        "command": command,
        "surface": surface,
        "args_hash": args_hash(args),
        "paths_returned": int(returned),
        "paths_denied": int(denied),
        "private_paths": int(private_paths),
        "blocked": bool(blocked),
        "scout_mode": os.environ.get("SCOUT_MODE") or None,
    }
    path = vault / ".scout-logs" / LOG_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # One write per line, O_APPEND: concurrent runs don't interleave a line.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as e:
        print(f"warning: couldn't write the kb audit log {path}: {e.strerror or e}", file=sys.stderr)
