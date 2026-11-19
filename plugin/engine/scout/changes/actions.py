"""What a scheduled run must do about the changes map (``scoutctl changes due``), and the engine half of doing it.

``due`` reads the same rows ``scoutctl changes --json`` shows (``scout.changes.report.changes_report``): change rows
re-derived from git, verdicts computed now, owner decisions from the owner store. It lists the rollbacks the owner
asked for (``undo:<id>``) or an auto-rollback mode would make once calibration has ended, the candidates that may not
be rolled back (``blocked``), the governance changes the owner approved or rejected, and the suspects no wrap has
announced yet. It never raises; a broken input reads as nothing to do.

``rollback`` reverse-applies, in the working tree only, the change's own diff over its own paths (brain files,
``scripts/``, ``docs/wishlist/``, both sides of a ``knowledge-base/`` rename), so later edits elsewhere in those files
stay. ``apply_governance`` patches the working tree with an approved ``governance-pending/`` diff. Neither stages or
commits anything: the run commits the returned paths through ``scripts/git-safe-commit.sh``. Every git call goes
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
from scout.changes.scan import _git, _git_bytes, name_status, own_paths

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


def _read_announced_doc(vault: Path) -> dict[str, set[str]]:
    try:
        data = json.loads(_announced_path(vault).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {"ids": set(), "blocked": set()}
    if not isinstance(data, dict) or data.get("schema") != _ANNOUNCED_SCHEMA:
        return {"ids": set(), "blocked": set()}
    out: dict[str, set[str]] = {}
    for key in ("ids", "blocked"):
        raw = data.get(key)
        out[key] = {i for i in raw if isinstance(i, str) and CHANGE_ID_RE.match(i)} if isinstance(raw, list) else set()
    return out


def read_announced(vault: Path, *, blocked: bool = False) -> set[str]:
    """Change ids a wrap already announced: as suspect, or (``blocked``) as an undo that may not run. The two lists
    are apart so an announced suspect whose undo the owner then asks for, and which is blocked, is still reported.
    Never raises: anything unreadable is none."""
    return _read_announced_doc(vault)["blocked" if blocked else "ids"]


def mark_announced(vault: Path, ids: Sequence[str], *, blocked: bool = False) -> None:
    """Add ``ids`` to ``.scout-logs/changes-announced.json`` (the suspect list, or with ``blocked`` the blocked list).
    Run-writable on purpose: forging it only hides a wrap line, and the map still shows the verdict. Raises
    ValueError for a non-change id, OSError when it didn't save."""
    from scout.changes.store import atomic_write_json

    bad = [i for i in ids if not CHANGE_ID_RE.match(i)]
    if bad:
        raise ValueError(f"not a change id: {bad[0]!r}")
    doc = _read_announced_doc(vault)
    doc["blocked" if blocked else "ids"] |= set(ids)
    data = {"schema": _ANNOUNCED_SCHEMA, "ids": sorted(doc["ids"]), "blocked": sorted(doc["blocked"])}
    if not atomic_write_json(_announced_path(vault), data):
        raise OSError(f"{ANNOUNCED_NAME}: could not save")


# --- what undoing a change restores ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Plan:
    """How to undo a change: reverse-apply ``diff`` (the commit's own diff over ``paths``) to the working tree."""

    paths: tuple[str, ...]  # the change's own paths (scout.changes.scan.own_paths), both sides of a rename
    diff: bytes


def _at_top(vault: Path) -> bool:
    """The vault is its repository's top level, so git's paths are vault-relative."""
    prefix = _git(vault, "rev-parse", "--show-prefix")
    return prefix is not None and not prefix.strip()


def _modes_ok(vault: Path, parent: str, commit: str, paths: Sequence[str]) -> bool:
    """Every own path is a regular file on both sides, except that the commit may have added or changed a symlink
    (undoing that removes or replaces it). A symlink or submodule in the parent would be written back: refused."""
    raw = _git_bytes(
        vault, "--literal-pathspecs", "diff-tree", "-r", "-z", "--no-renames", "--no-commit-id", parent, commit, "--",
        *paths,
    )  # fmt: skip
    if raw is None:
        return False
    fields = os.fsdecode(raw).split("\0")
    i = 0
    while i < len(fields):
        meta = fields[i]
        if not meta:
            i += 1
            continue
        cols = meta[1:].split(" ") if meta.startswith(":") else []
        if len(cols) != 5 or i + 1 >= len(fields) or not fields[i + 1]:
            return False
        old_mode, new_mode, _, _, status = cols
        status = status[:1]
        i += 2
        if status not in ("A", "D", "M", "T"):
            return False
        if status != "A" and old_mode not in _RESTORABLE_MODES:
            return False
        if status != "D" and new_mode not in _REMOVABLE_MODES:
            return False
    return True


def _plan(vault: Path, commit: str) -> _Plan | str:
    """The undo for ``commit``, or the problem: ``root-commit`` or ``git-unreadable`` (git failed, the commit isn't on
    HEAD, it has no own paths, or one of them isn't a regular file)."""
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
    entries = name_status(vault, commit)
    paths = own_paths(entries or [])
    if not paths or not _modes_ok(vault, parent, commit, paths):
        return "git-unreadable"
    # Plumbing, with explicit prefixes: a user's diff.noprefix, colour or external-diff settings never reach it.
    diff = _git_bytes(
        vault, "--literal-pathspecs", "diff-tree", "-p", "--binary", "-M", "--no-commit-id", "--no-color",
        "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/", parent, commit, "--", *paths,
    )  # fmt: skip
    if not diff or not diff.strip():
        return "git-unreadable"
    return _Plan(tuple(paths), diff)


def _reverse_apply(vault: Path, plan: _Plan, *, check: bool) -> bool:
    """``git apply -R`` of the plan's diff to the working tree (never the index); all or nothing."""
    args = ["apply", "-R", "--whitespace=nowarn"] + (["--check"] if check else [])
    return _git_bytes(vault, *args, stdin=plan.diff) is not None


def rollback_problem(vault: Path, change: Change, test: Mapping[str, Any]) -> str | None:
    """None when ``change`` can be rolled back, else why not: ``governance``, ``not-a-run-change`` (its test state is
    ``not-tested``), ``root-commit``, ``edited-since`` (its own diff no longer reverses cleanly: a later edit changed
    the same lines, or its own paths have uncommitted edits) or ``git-unreadable``.

    Hunk-level: a later commit that changed other lines of the same file does not block."""
    if change.governance:
        return "governance"
    if test.get("state") == "not-tested":
        return "not-a-run-change"
    plan = _plan(vault, change.commit)
    if isinstance(plan, str):
        return plan
    # Uncommitted work on these paths (a run mid-edit, a rollback not yet committed) would be mixed into the undo.
    dirty = _git_bytes(vault, "--literal-pathspecs", "status", "--porcelain", "-z", "--", *plan.paths)
    if dirty is None:
        return "git-unreadable"
    if dirty.strip(b"\0"):
        return "edited-since"
    if not _reverse_apply(vault, plan, check=True):
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
            days.append(date.fromisoformat(test["review_on"]))  # written by scout.changes.verdicts: always ISO
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
        state = decision(signals, p.id, p.hash)
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
            if (
                test.get("would_have_done", {}).get(_AUTO_KEYS[mode])
                and not test.get("rolled_back_by")
                and not test.get("marked_wrong")
            ):
                candidates.setdefault(r["id"], f"auto-{mode}")
    # A blocked undo is reported once (the wrap runs `changes announced --blocked <id>`), never re-listed every run.
    announced_blocked = read_announced(vault, blocked=True)
    for cid, reason in candidates.items():
        problem = rollback_problem(vault, changes[cid], by_id[cid]["test"])
        summary = by_id[cid]["summary"]
        if problem is None:
            out["rollbacks"].append({"id": cid, "summary": summary, "reason": reason})
        elif cid not in announced_blocked:
            out["blocked"].append({"id": cid, "summary": summary, "why_blocked": problem})

    announced = read_announced(vault)
    out["suspect_unannounced"] = [
        {
            "id": r["id"],
            "summary": r["summary"],
            "reasons": list(r["test"].get("reasons", [])),
            # Why `scoutctl changes undo` would not help (governance, edited-since, ...), so the wrap words it.
            "undo_blocked": rollback_problem(vault, changes[r["id"]], r["test"]),
        }
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


def _real_dir_inside(vault: Path, rel: str) -> bool:
    """``rel`` is absent or a real folder (not a symlink, not a file), and its parent resolves inside the vault, so it
    does too."""
    path = vault / rel
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        return False
    return _inside(vault, rel)


def rollback(
    vault: Path,
    env: Mapping[str, str],
    change_id: str,
    *,
    now: datetime,
    tz: ZoneInfo,
    owner_vault: Path | None = None,
) -> list[str]:
    """Undo ``change_id`` in the working tree: reverse-apply the change's own diff (its brain files, ``scripts/``,
    ``docs/wishlist/`` and both sides of a ``knowledge-base/`` rename; never session logs or audit rows) with
    ``git apply -R``. Later edits to other lines of those files stay. Returns the sorted vault-relative paths for the
    run to commit through ``git-safe-commit.sh``.

    Raises :class:`ActionRefused` unless the id is in ``due(...)["rollbacks"]``. All or nothing: ``git apply`` checks
    every hunk before it writes, so a hunk that no longer applies (``edited-since``) writes nothing."""
    doc, changes = _safe_compute(vault, env, now=now, tz=tz, owner_vault=owner_vault)
    if change_id not in {r["id"] for r in doc["rollbacks"]}:
        why = next((b["why_blocked"] for b in doc["blocked"] if b["id"] == change_id), "not-due")
        raise ActionRefused(why)
    plan = _plan(vault, changes[change_id].commit)
    if isinstance(plan, str):
        raise ActionRefused(plan)
    if not all(_inside(vault, p) for p in plan.paths):
        raise ActionRefused("path outside the vault")
    if not _reverse_apply(vault, plan, check=False):
        raise ActionRefused("edited-since")
    return list(plan.paths)


def _pending_item(vault: Path, env: Mapping[str, str], gov_id: str, want: str, owner_vault: Path | None) -> Any:
    from scout.changes.governance import decision, read_pending
    from scout.changes.store import read_signals

    item = next((p for p in read_pending(vault)[0] if p.id == gov_id), None)
    if item is None:
        raise ActionRefused(f"no pending governance change {gov_id!r}")
    md = vault / item.path
    # governance-pending/ symlinked out of the vault, or the md a link: never move or delete through it.
    if md.is_symlink() or not _inside(vault, item.path):
        raise ActionRefused("path outside the vault")
    signals, _ = read_signals(vault, env, owner_vault=owner_vault)
    state = decision(signals, gov_id, item.hash)
    if state != want:
        raise ActionRefused(f"{gov_id} is {state}, not {want}")
    return item


def apply_governance(vault: Path, env: Mapping[str, str], gov_id: str, *, owner_vault: Path | None = None) -> list[str]:
    """Apply an approved pending governance change to the working tree (``git apply``, never the index), then move
    its file to ``governance-pending/applied/<slug>.md`` (that move is what makes the commit a governance change). A
    ``governance: false`` item (a harness fallback) is an ordinary improvement: its file is deleted instead, so the
    commit is tested and undoable like any other. Returns the sorted patched paths (from the diff's headers, both
    sides of a rename) and the file paths.

    Raises :class:`ActionRefused` unless the owner approved this exact diff; a patch that doesn't apply leaves the
    pending file where it is (``patch does not apply``). The temp diff in ``.scout-cache/`` is always removed."""
    from scout.changes.governance import PENDING_DIR

    item = _pending_item(vault, env, gov_id, "approved", owner_vault)
    applied_dir = f"{PENDING_DIR}/{APPLIED_DIR}"
    applied_rel = f"{applied_dir}/{item.slug}.md"
    paths = list(item.paths)  # from the diff's headers, checked when the file was read
    if not all(_inside(vault, p) for p in paths):
        raise ActionRefused("path outside the vault")
    # Both folders are run-writable: a planted symlink must not carry the temp diff or the md out of the vault.
    if not (_real_dir_inside(vault, applied_dir) and _inside(vault, applied_rel)):
        raise ActionRefused("path outside the vault")
    if item.governance and os.path.lexists(vault / applied_rel):
        raise ActionRefused(f"{applied_rel} already exists")
    if not _at_top(vault):
        raise ActionRefused("git-unreadable")
    cache = vault / CACHE_DIR
    try:
        cache.mkdir(exist_ok=True)  # a no-op on a planted link to a folder; checked right after
    except FileExistsError:  # a file, or a link to nothing, by that name
        pass
    if not _real_dir_inside(vault, CACHE_DIR):
        raise ActionRefused("path outside the vault")
    # A fresh file (O_EXCL), never a pre-existing name a run could have linked elsewhere; this exact file is removed.
    fd, tmp = tempfile.mkstemp(prefix=f"{item.slug}.", suffix=".diff", dir=cache)
    diff_file = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(item.diff)
        # git apply checks every hunk before it writes: a patch that doesn't apply writes nothing.
        if _git(vault, "apply", "--whitespace=nowarn", "--", str(diff_file)) is None:
            raise ActionRefused("patch does not apply")
    finally:
        diff_file.unlink(missing_ok=True)
    if not item.governance:
        (vault / item.path).unlink()
        return sorted({*paths, item.path})
    (vault / applied_dir).mkdir(parents=True, exist_ok=True)
    os.rename(vault / item.path, vault / applied_rel)
    return sorted({*paths, item.path, applied_rel})


def discard_governance(
    vault: Path, env: Mapping[str, str], gov_id: str, *, owner_vault: Path | None = None
) -> list[str]:
    """Delete a rejected pending governance change's file. Raises :class:`ActionRefused` unless it was rejected."""
    item = _pending_item(vault, env, gov_id, "rejected", owner_vault)
    (vault / item.path).unlink()
    return [item.path]
