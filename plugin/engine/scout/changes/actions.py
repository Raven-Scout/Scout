"""What a scheduled run must do about the changes map (``scoutctl changes due``), and the engine half of doing it.

``due`` reads the same rows ``scoutctl changes --json`` shows (``scout.changes.report.changes_report``): change rows
re-derived from git, verdicts computed now, owner decisions from the owner store. It lists the rollbacks the owner
asked for (``undo:<id>``) or an auto-rollback mode would make once calibration has ended, the candidates that may not
be rolled back (``blocked``), the governance changes the owner approved or rejected, and the suspects no wrap has
announced yet. It never raises; a broken input reads as nothing to do.

``rollback`` restores, in the working tree only, each file a change's commit touched to its content in the commit's
parent. ``apply_governance`` patches the working tree with an approved ``governance-pending/`` diff. Neither stages
or commits anything: the run commits the returned paths through ``scripts/git-safe-commit.sh``. Every git call goes
through ``scout.changes.scan``'s runner (``GIT_OPTIONAL_LOCKS=0``, a timeout)."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from scout.changes.models import CHANGE_ID_RE, SCHEMA_VERSION, Change
from scout.changes.scan import _git, _git_bytes

ANNOUNCED_NAME = "changes-announced.json"
_ANNOUNCED_SCHEMA = 1
CALIBRATION_REPORT = "docs/changes-calibration-report.md"
# The calibration report is due this long after the first verdict came in.
CALIBRATION_REPORT_AFTER = timedelta(days=28)
CACHE_DIR = ".scout-cache"
APPLIED_DIR = "applied"
# How far back `due` looks for changes: everything the ledger can hold (as `scoutctl changes undo` reads it).
_HISTORY = timedelta(days=3650)
_HISTORY_LIMIT = 1_000_000
_FULL_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
# Modes a rollback can restore (a regular file, executable or not) or remove (those, or a symlink).
_RESTORABLE_MODES = frozenset({"100644", "100755"})
_REMOVABLE_MODES = _RESTORABLE_MODES | {"120000"}
_AUTO_KEYS = {"targeted": "targeted", "one-at-a-time": "one_at_a_time"}


class ActionRefused(Exception):  # noqa: N818 - the name the run instructions and the CLI use
    """The action may not run; nothing was written. ``reason`` is the short why the CLI prints."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- announced suspects ---------------------------------------------------------------------------------------------


def _announced_path(vault: Path) -> Path:
    return vault / ".scout-logs" / ANNOUNCED_NAME


def read_announced(vault: Path) -> set[str]:
    """Change ids a wrap already announced as suspect. Never raises: anything unreadable is none."""
    try:
        data = json.loads(_announced_path(vault).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return set()
    if not isinstance(data, dict) or data.get("schema") != _ANNOUNCED_SCHEMA or not isinstance(data.get("ids"), list):
        return set()
    return {i for i in data["ids"] if isinstance(i, str) and CHANGE_ID_RE.match(i)}


def mark_announced(vault: Path, ids: Sequence[str]) -> None:
    """Add ``ids`` to ``.scout-logs/changes-announced.json``. Run-writable on purpose: forging it only hides a wrap
    line, and the map still shows the verdict. Raises ValueError for a non-change id, OSError when it didn't save."""
    from scout.changes.store import atomic_write_json

    bad = [i for i in ids if not CHANGE_ID_RE.match(i)]
    if bad:
        raise ValueError(f"not a change id: {bad[0]!r}")
    merged = sorted(read_announced(vault) | set(ids))
    if not atomic_write_json(_announced_path(vault), {"schema": _ANNOUNCED_SCHEMA, "ids": merged}):
        raise OSError(f"{ANNOUNCED_NAME}: could not save")


# --- what a commit touched ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Entry:
    status: str  # A, D, M or T: the commit's change to the path, against its parent
    path: str  # repository-relative (the vault is the repository's top level)
    parent_mode: str  # the path's mode in the parent; 000000 when the parent doesn't have it


@dataclass(frozen=True)
class _Touched:
    parent: str
    entries: tuple[_Entry, ...]


def _at_top(vault: Path) -> bool:
    """The vault is its repository's top level, so git's paths are vault-relative."""
    prefix = _git(vault, "rev-parse", "--show-prefix")
    return prefix is not None and not prefix.strip()


def _touched(vault: Path, commit: str) -> _Touched | str:
    """What ``commit`` changed against its first parent, one entry per path (a rename is its two sides), or the
    problem: ``root-commit`` or ``git-unreadable`` (git failed, the commit isn't on HEAD, or a path isn't a file)."""
    if not _FULL_SHA_RE.match(commit) or not _at_top(vault):
        return "git-unreadable"
    line = _git(vault, "rev-list", "--parents", "-n", "1", commit, "--")
    if line is None or not line.split():
        return "git-unreadable"
    shas = line.split()
    if len(shas) == 1:
        return "root-commit"
    parent = shas[1]
    if _git(vault, "merge-base", "--is-ancestor", commit, "HEAD") is None:
        return "git-unreadable"
    raw = _git_bytes(vault, "diff-tree", "-r", "-z", "--no-renames", "--no-commit-id", parent, commit, "--")
    if raw is None:
        return "git-unreadable"
    # Paths go back to git and the filesystem as given: fsdecode round-trips any bytes (surrogateescape).
    fields = os.fsdecode(raw).split("\0")
    entries: list[_Entry] = []
    i = 0
    while i < len(fields):
        meta = fields[i]
        if not meta:
            i += 1
            continue
        cols = meta[1:].split(" ") if meta.startswith(":") else []
        if len(cols) != 5 or i + 1 >= len(fields) or not fields[i + 1]:
            return "git-unreadable"
        old_mode, new_mode, _, _, status = cols
        status = status[:1]
        path = fields[i + 1]
        i += 2
        if status not in ("A", "D", "M", "T"):
            return "git-unreadable"
        if status != "A" and old_mode not in _RESTORABLE_MODES:
            return "git-unreadable"  # a symlink or submodule in the parent: not a file to write back
        if status != "D" and new_mode not in _REMOVABLE_MODES:
            return "git-unreadable"
        entries.append(_Entry(status, path, old_mode if status != "A" else "000000"))
    if not entries:
        return "git-unreadable"
    return _Touched(parent, tuple(entries))


def rollback_problem(vault: Path, change: Change, test: Mapping[str, Any]) -> str | None:
    """None when ``change`` can be rolled back, else why not: ``governance``, ``not-a-run-change`` (its test state is
    ``not-tested``), ``root-commit``, ``edited-since`` (a later commit, or an uncommitted edit, touched one of its
    paths) or ``git-unreadable``."""
    if change.governance:
        return "governance"
    if test.get("state") == "not-tested":
        return "not-a-run-change"
    touched = _touched(vault, change.commit)
    if isinstance(touched, str):
        return touched
    paths = [e.path for e in touched.entries]
    later = _git(vault, "--literal-pathspecs", "log", "--format=%H", f"{change.commit}..HEAD", "--", *paths)
    if later is None:
        return "git-unreadable"
    if later.strip():
        return "edited-since"
    # Uncommitted work on these paths (a run mid-edit, a rollback not yet committed) would be overwritten too.
    dirty = _git_bytes(vault, "--literal-pathspecs", "status", "--porcelain", "-z", "--", *paths)
    if dirty is None:
        return "git-unreadable"
    if dirty.strip(b"\0"):
        return "edited-since"
    return None


# --- due ------------------------------------------------------------------------------------------------------------


def empty_due() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "verified": False,
        "rollbacks": [],
        "blocked": [],
        "apply": [],
        "discard": [],
        "suspect_unannounced": [],
        "engagement_tests_running": 0,
        "calibration_report_due": False,
    }


def _first_review(changes: Sequence[Mapping[str, Any]]) -> date | None:
    """The earliest ``review_on`` among changes whose verdict is in (state ``done``)."""
    days = []
    for c in changes:
        test = c["test"]
        if test.get("state") == "done" and isinstance(test.get("review_on"), str):
            try:
                days.append(date.fromisoformat(test["review_on"]))
            except ValueError:
                continue
    return min(days, default=None)


def _compute(
    vault: Path, env: Mapping[str, str], *, now: datetime, tz: ZoneInfo, owner_vault: Path | None
) -> tuple[dict[str, Any], dict[str, Change]]:
    from scout.changes import report
    from scout.changes.governance import decision, read_pending
    from scout.changes.store import read_signals

    doc = report.changes_report(
        vault, now=now, since=now - _HISTORY, env=env, tz=tz, limit=_HISTORY_LIMIT, owner_vault=owner_vault
    )
    signals, _ = read_signals(vault, env, owner_vault=owner_vault)
    out = empty_due()
    verified = bool(doc.get("verified"))
    out["verified"] = verified
    rows = list(doc["changes"])
    by_id = {r["id"]: r for r in rows}
    changes = {r["id"]: Change.from_row(r) for r in rows}

    # Governance comes from governance-pending/ and the owner's decisions, not from change rows: listed either way.
    pending, _ = read_pending(vault)
    for p in pending:
        state = decision(signals, p.id)
        if state == "approved":
            out["apply"].append(p.id)
        elif state == "rejected":
            out["discard"].append(p.id)

    out["engagement_tests_running"] = sum(
        1 for r in rows if r["test"].get("state") == "testing" and r["test"].get("engagement_targeted")
    )
    first = _first_review(rows)
    today = now.astimezone(tz).date()
    out["calibration_report_due"] = bool(
        doc.get("calibrating")
        and first is not None
        and today >= first + CALIBRATION_REPORT_AFTER
        and not (vault / CALIBRATION_REPORT).exists()
    )
    if not verified:
        # Change rows (and so rolled_back_by) can't be trusted, and a rollback needs git anyway.
        return out, changes

    # Candidates, each once: the owner's requests first (oldest request first), then what the auto mode matches.
    candidates: dict[str, str] = {}
    for s in signals:
        if s.kind != "approval" or not s.ref or not s.ref.startswith("undo:"):
            continue
        cid = s.ref[len("undo:") :]
        row = by_id.get(cid)
        if row is not None and not row["test"].get("rolled_back_by"):
            candidates.setdefault(cid, "owner-undo")
    mode = doc.get("auto_rollback", "off")
    if mode in _AUTO_KEYS and not doc.get("calibrating", True):
        for r in sorted(rows, key=lambda r: (r["ts"], r["id"])):
            test = r["test"]
            if test.get("would_have_done", {}).get(_AUTO_KEYS[mode]) and not test.get("rolled_back_by"):
                candidates.setdefault(r["id"], f"auto-{mode}")
    for cid, reason in candidates.items():
        problem = rollback_problem(vault, changes[cid], by_id[cid]["test"])
        summary = by_id[cid]["summary"]
        if problem is None:
            out["rollbacks"].append({"id": cid, "summary": summary, "reason": reason})
        else:
            out["blocked"].append({"id": cid, "summary": summary, "why_blocked": problem})

    announced = read_announced(vault)
    out["suspect_unannounced"] = [
        {"id": r["id"], "summary": r["summary"], "reasons": list(r["test"].get("reasons", []))}
        for r in rows
        if r["test"].get("suspect") and r["id"] not in announced
    ]
    return out, changes


def _safe_compute(
    vault: Path, env: Mapping[str, str], *, now: datetime, tz: ZoneInfo, owner_vault: Path | None
) -> tuple[dict[str, Any], dict[str, Change]]:
    try:
        return _compute(vault, env, now=now, tz=tz, owner_vault=owner_vault)
    except Exception:  # noqa: BLE001 - a run reads this every time: a broken input is nothing to do, never a crash
        return empty_due(), {}


def due(
    vault: Path, env: Mapping[str, str], *, now: datetime, tz: ZoneInfo, owner_vault: Path | None = None
) -> dict[str, Any]:
    """What a run must do now (``scoutctl changes due --json``). Never raises.

    Git, ``.scout-logs/`` and ``governance-pending/`` are read from ``vault``; owner rows and settings are keyed to
    ``owner_vault`` (default ``vault``), as ``changes --json`` reads them. When git can't verify the change rows
    (``verified`` false), ``rollbacks``, ``blocked`` and ``suspect_unannounced`` are empty."""
    return _safe_compute(vault, env, now=now, tz=tz, owner_vault=owner_vault)[0]


# --- doing it -------------------------------------------------------------------------------------------------------


def _inside(vault: Path, rel: str) -> bool:
    """``rel`` names a file inside the vault: relative, no ``..``, not under ``.git``, and its folder resolves into the
    vault (a symlinked folder could otherwise take a write outside it)."""
    pure = PurePosixPath(rel)
    if not rel or pure.is_absolute() or ".." in pure.parts or pure.parts[0] == ".git":
        return False
    try:
        root = vault.resolve()
        (vault / rel).parent.resolve().relative_to(root)
    except (OSError, ValueError, RuntimeError):
        return False
    return True


def _write_file(target: Path, data: bytes, mode: str) -> None:
    """Atomic: a temp file beside ``target``, then a rename over it (which replaces a symlink, never follows it)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o755 if mode == "100755" else 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def rollback(
    vault: Path,
    env: Mapping[str, str],
    change_id: str,
    *,
    now: datetime,
    tz: ZoneInfo,
    owner_vault: Path | None = None,
) -> list[str]:
    """Undo ``change_id`` in the working tree: each path its commit touched gets its content in the commit's parent,
    or is removed when the parent doesn't have it. Returns the sorted vault-relative paths (both sides of a rename)
    for the run to commit through ``git-safe-commit.sh``.

    Raises :class:`ActionRefused` unless the id is in ``due(...)["rollbacks"]``. All or nothing on reads: every
    parent version is read before anything is written, so a git failure writes nothing."""
    doc, changes = _safe_compute(vault, env, now=now, tz=tz, owner_vault=owner_vault)
    if change_id not in {r["id"] for r in doc["rollbacks"]}:
        why = next((b["why_blocked"] for b in doc["blocked"] if b["id"] == change_id), "not-due")
        raise ActionRefused(why)
    touched = _touched(vault, changes[change_id].commit)
    if isinstance(touched, str):
        raise ActionRefused(touched)
    for e in touched.entries:
        if not _inside(vault, e.path):
            raise ActionRefused("path outside the vault")
    plan: list[tuple[_Entry, bytes | None]] = []
    for e in touched.entries:
        if e.status == "A":
            plan.append((e, None))
            continue
        data = _git_bytes(vault, "show", f"{touched.parent}:{e.path}")
        if data is None:
            raise ActionRefused("git-unreadable")
        plan.append((e, data))
    for e, data in plan:
        target = vault / e.path
        if data is None:
            target.unlink(missing_ok=True)
        else:
            _write_file(target, data, e.parent_mode)
    return sorted(e.path for e in touched.entries)


def _pending_item(vault: Path, env: Mapping[str, str], gov_id: str, want: str, owner_vault: Path | None) -> Any:
    from scout.changes.governance import decision, read_pending
    from scout.changes.store import read_signals

    item = next((p for p in read_pending(vault)[0] if p.id == gov_id), None)
    if item is None:
        raise ActionRefused(f"no pending governance change {gov_id!r}")
    if not _inside(vault, item.path):  # governance-pending/ symlinked out of the vault: never move or delete there
        raise ActionRefused("path outside the vault")
    signals, _ = read_signals(vault, env, owner_vault=owner_vault)
    state = decision(signals, gov_id)
    if state != want:
        raise ActionRefused(f"{gov_id} is {state}, not {want}")
    return item


def _patched_paths(vault: Path, diff_file: Path) -> list[str] | None:
    """The paths ``git apply`` would touch (both sides of a rename), without applying. None when git can't parse."""
    raw = _git_bytes(vault, "apply", "--numstat", "-z", "--", str(diff_file))
    if raw is None:
        return None
    fields = os.fsdecode(raw).split("\0")
    out: list[str] = []
    i = 0
    while i < len(fields):
        cols = fields[i].split("\t")
        if not fields[i]:
            i += 1
        elif len(cols) == 3 and cols[2]:
            out.append(cols[2])
            i += 1
        elif len(cols) == 3 and i + 2 < len(fields):  # a rename: "added\tdeleted\t\0old\0new\0"
            out += [fields[i + 1], fields[i + 2]]
            i += 3
        else:
            return None
    return out


def apply_governance(vault: Path, env: Mapping[str, str], gov_id: str, *, owner_vault: Path | None = None) -> list[str]:
    """Apply an approved pending governance change to the working tree (``git apply``, never the index), then move
    its file to ``governance-pending/applied/<slug>.md``. Returns the sorted patched paths and both file paths.

    Raises :class:`ActionRefused` unless the owner's decision is ``approved``; a patch that doesn't apply leaves the
    pending file where it is (``patch does not apply``). The temp diff in ``.scout-cache/`` is always removed."""
    from scout.changes.governance import PENDING_DIR

    item = _pending_item(vault, env, gov_id, "approved", owner_vault)
    applied_rel = f"{PENDING_DIR}/{APPLIED_DIR}/{item.slug}.md"
    if (vault / applied_rel).exists():
        raise ActionRefused(f"{applied_rel} already exists")
    if not _at_top(vault):
        raise ActionRefused("git-unreadable")
    cache = vault / CACHE_DIR
    diff_file = cache / f"{item.slug}.diff"
    try:
        cache.mkdir(parents=True, exist_ok=True)
        diff_file.write_text(item.diff, encoding="utf-8")
        paths = _patched_paths(vault, diff_file)
        if paths is None:
            raise ActionRefused("patch does not apply")
        if not all(_inside(vault, p) for p in paths):
            raise ActionRefused("path outside the vault")
        if _git(vault, "apply", "--whitespace=nowarn", "--", str(diff_file)) is None:
            raise ActionRefused("patch does not apply")
    finally:
        diff_file.unlink(missing_ok=True)
    (vault / applied_rel).parent.mkdir(parents=True, exist_ok=True)
    os.rename(vault / item.path, vault / applied_rel)
    return sorted({*paths, item.path, applied_rel})


def discard_governance(
    vault: Path, env: Mapping[str, str], gov_id: str, *, owner_vault: Path | None = None
) -> list[str]:
    """Delete a rejected pending governance change's file. Raises :class:`ActionRefused` unless it was rejected."""
    item = _pending_item(vault, env, gov_id, "rejected", owner_vault)
    (vault / item.path).unlink()
    return [item.path]
