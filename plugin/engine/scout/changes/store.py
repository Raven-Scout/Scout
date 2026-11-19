"""The two stores. Run-writable rows live in ``<vault>/.scout-logs/``; owner-sourced rows live only in the
owner-only ``~/.local/state/scout/scout-change-ledger-owner/`` (a protected engine root a run can't write).

A reliance row's ``session`` is the first 16 hex digits of an *unsalted* SHA-256 of the Claude session id
(scout.changes.reliance). That keeps the id out of the clear in a local-only ledger; it is not anonymisation
against anyone who has the transcripts, since hashing a known session id reproduces it."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from scout import safe_write
from scout.changes.models import OWNER_KINDS, Change, Signal
from scout.kb import hook_paths

CHANGES_NAME = "changes.jsonl"
ENGAGEMENT_NAME = "engagement.jsonl"
# The HEAD the last ``scoutctl changes scan`` read up to (scout.changes.scan.scan_new), beside the ledger it feeds.
SCAN_MARK_NAME = "changes-scan.json"
_SCAN_MARK_SCHEMA = 1
# A run can write .scout-logs/, so the mark is handed to git only when it is a bare object id: never an option.
_SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def owner_dir(env: Mapping[str, str]) -> Path:
    """``~/.local/state/scout/scout-change-ledger-owner/``: owner-sourced engagement rows. A protected engine root."""
    return Path(env.get("HOME") or Path.home()) / ".local" / "state" / "scout" / hook_paths.CHANGES_OWNER_NAME


def vault_key(vault: Path) -> str:
    try:
        resolved = vault.resolve()
    except OSError:
        resolved = vault.absolute()
    return hashlib.sha256(str(resolved).encode()).hexdigest()[:16]


def is_owner_session(env: Mapping[str, str]) -> bool:
    return not env.get("SCOUT_MODE") and env.get("SCOUT_READER") in (None, "", "owner")


def is_owner_caller(env: Mapping[str, str], vault: Path) -> bool:
    """:func:`is_owner_session`, and the process table shows this process is not a live run of ``vault``.

    A command has no transcript to read a run's mode from, and a run can unset ``SCOUT_MODE``; it can't leave
    the processes it runs in, so ask :func:`scout.kb.live_run.commit_source_now` the way kb-lint does. Fails
    closed: a check that raises means "not the owner"."""
    if not is_owner_session(env):
        return False
    try:
        from scout.kb import live_run

        source = live_run.commit_source_now(vault)
    except Exception:  # noqa: BLE001 - any failure refuses the owner-only write
        return False
    return source is None or not source.runs


def _logs(vault: Path) -> Path:
    return vault / ".scout-logs"


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = (json.dumps(row, separators=(",", ":")) + "\n").encode()
    with safe_write.open_append(path, mode=0o600, binary=True) as fh:
        fh.write(line)


def _read(path: Path) -> tuple[list[dict[str, Any]], int]:
    """The file's object rows and how many lines were skipped. Never raises: an unreadable file is one skip."""
    try:
        if not path.exists():
            return [], 0
        text = path.read_text(errors="replace")
    except OSError:
        return [], 1
    rows: list[dict[str, Any]] = []
    skipped = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):  # RecursionError: a line nested deeper than the decoder goes
            skipped += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            skipped += 1
    return rows, skipped


def append_change(vault: Path, change: Change) -> None:
    _append(_logs(vault) / CHANGES_NAME, change.to_row())


def read_changes(vault: Path) -> tuple[list[Change], int]:
    raw, skipped = _read(_logs(vault) / CHANGES_NAME)
    out: list[Change] = []
    for row in raw:
        try:
            out.append(Change.from_row(row))
        except ValueError:
            skipped += 1
    return out, skipped


def read_scan_mark(vault: Path) -> str | None:
    """The commit the last watermark scan reached; None when never scanned or the record isn't a bare sha."""
    try:
        data = json.loads((_logs(vault) / SCAN_MARK_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != _SCAN_MARK_SCHEMA:
        return None
    head = data.get("head")
    return head if isinstance(head, str) and _SHA_RE.fullmatch(head) else None


def write_scan_mark(vault: Path, head: str, now: datetime) -> None:
    """Atomic 0600 write. Best-effort: a mark that didn't save means the next scan re-reads the same range."""
    path = _logs(vault) / SCAN_MARK_NAME
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"schema": _SCAN_MARK_SCHEMA, "head": head, "at": now.isoformat()}) + "\n")
        os.replace(tmp, path)
    except OSError:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def append_signal(signal: Signal, *, vault: Path, env: Mapping[str, str]) -> None:
    if signal.kind in OWNER_KINDS:
        if not is_owner_session(env):
            raise PermissionError(f"{signal.kind!r} is owner-sourced; a run may not write it")
        row = {**signal.to_row(), "vault_key": vault_key(vault)}
        _append(owner_dir(env) / ENGAGEMENT_NAME, row)
    else:
        _append(_logs(vault) / ENGAGEMENT_NAME, signal.to_row())


def read_signals(vault: Path, env: Mapping[str, str]) -> tuple[list[Signal], int]:
    key = vault_key(vault)
    out: list[Signal] = []
    skipped = 0
    for path, owned in ((owner_dir(env) / ENGAGEMENT_NAME, True), (_logs(vault) / ENGAGEMENT_NAME, False)):
        raw, bad = _read(path)
        skipped += bad
        for row in raw:
            if owned and row.get("vault_key") != key:
                continue
            kind = row.get("kind")
            if not owned and isinstance(kind, str) and kind in OWNER_KINDS:
                # A run writes this file; an owner-sourced kind here is a forgery, never engagement.
                skipped += 1
                continue
            try:
                out.append(Signal.from_row(row))
            except ValueError:
                skipped += 1
    out.sort(key=lambda s: s.ts)
    return out, skipped
