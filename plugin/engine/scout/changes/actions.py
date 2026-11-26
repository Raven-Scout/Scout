"""What a scheduled run must do about the changes map (``scoutctl changes due``), and the engine half of doing it.

``due`` reads the same rows ``scoutctl changes --json`` shows (``scout.changes.report.changes_report``): change rows
re-derived from git, verdicts computed now, owner decisions from the owner store. It lists the rollbacks the owner
asked for (``undo:<id>``) or an auto-rollback mode would make once calibration has ended, the candidates that may not
be rolled back (``blocked``), the governance changes the owner approved or rejected, and the suspects no wrap has
announced yet. It never raises; a broken input reads as nothing to do.

``rollback`` undoes a change in the working tree only, over its own paths (brain files, ``scripts/``,
``docs/wishlist/``, both sides of a ``knowledge-base/`` rename): a positional 3-way merge of each file (``git
merge-file``), so later edits elsewhere in those files stay and an identical block elsewhere is never taken for the
change's own lines. ``apply_governance`` patches the working tree with an approved ``governance-pending/`` diff.
Neither stages or commits anything: the run commits the returned paths through ``scripts/git-safe-commit.sh``. Every
git call goes through ``scout.changes.scan``'s runner (``GIT_OPTIONAL_LOCKS=0``, a timeout)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
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
_LINK_MODE = "120000"
_REMOVABLE_MODES = _RESTORABLE_MODES | {_LINK_MODE}
# git's own binary test (what `git merge-file` refuses to merge): a NUL in the first this-many bytes.
_BINARY_PROBE = 8000
# <unistd.h>: the per-user temp folder macOS gives an account (Python doesn't name it).
_CS_DARWIN_USER_TEMP_DIR = 65537
_AUTO_KEYS = {"targeted": "targeted", "one-at-a-time": "one_at_a_time"}
# The automatic rollback modes stay inert until the unapproved-governance-move and undo-race gaps are closed: `due`
# lists no auto rollback whatever the owner's settings say (an older engine's `scoutctl changes auto-rollback`, or a
# hand-edited settings.json), as the CLI refuses to set one. Verdicts still say what each mode would have done.
AUTO_ROLLBACK_LIVE = False


def auto_eligible(sha: str, ledger: Any = None) -> bool:
    """Whether an automatic rollback may act on the commit: only one the run broker recorded as a run's
    (scout.changes.run_commits), never one attributed from git's state, which a run without its broker can steer,
    nor one classified by its subject. The owner's own undo requests don't go through this."""
    from scout.changes import run_commits

    return run_commits.is_broker_recorded(sha, ledger)


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
class _Side:
    """One side of an own path in the change: its git mode and blob."""

    mode: str
    blob: str


@dataclass(frozen=True)
class _Step:
    """Undoing one file. ``read`` is where the change left it (a rename's new side), ``write`` where the undo puts
    the version before it (a rename's old side; otherwise the same path). ``before`` is the parent's side (None: the
    change added the file), ``after`` the commit's (None: the change deleted it)."""

    read: str
    write: str
    before: _Side | None
    after: _Side | None


@dataclass(frozen=True)
class _Plan:
    """How to undo a change: one step per file, both sides of a rename being one step. It holds no file content:
    the undo is worked out against the working tree each time it is checked or done (:func:`_undo`)."""

    paths: tuple[str, ...]  # the change's own paths (scout.changes.scan.own_paths), both sides of a rename
    steps: tuple[_Step, ...]


@dataclass(frozen=True)
class _Op:
    """One write of a worked-out undo: ``data`` with permission bits ``mode`` at ``path``, or (``data`` None) remove
    ``path``."""

    path: str
    data: bytes | None
    mode: int = 0o644


def _at_top(vault: Path) -> bool:
    """The vault is its repository's top level, so git's paths are vault-relative."""
    prefix = _git(vault, "rev-parse", "--show-prefix")
    return prefix is not None and not prefix.strip()


def _sides(
    vault: Path, parent: str, commit: str, paths: Sequence[str]
) -> tuple[dict[str, _Side], dict[str, _Side]] | None:
    """Each own path's side in the parent and in the commit (``before``, ``after``), from git's raw diff without
    rename detection (a rename is a delete plus an add here). None, refused, when git failed, the output doesn't
    parse, or a path isn't a regular file on both sides, except that the commit may have added or changed a symlink
    (undoing that removes or replaces it): a symlink or submodule in the parent would be written back."""
    raw = _git_bytes(
        vault, "--literal-pathspecs", "diff-tree", "-r", "-z", "--no-renames", "--no-commit-id", parent, commit, "--",
        *paths,
    )  # fmt: skip
    if raw is None:
        return None
    before: dict[str, _Side] = {}
    after: dict[str, _Side] = {}
    fields = os.fsdecode(raw).split("\0")
    i = 0
    while i < len(fields):
        meta = fields[i]
        if not meta:
            i += 1
            continue
        cols = meta[1:].split(" ") if meta.startswith(":") else []
        if len(cols) != 5 or i + 1 >= len(fields) or not fields[i + 1]:
            return None
        old_mode, new_mode, old_blob, new_blob, status = cols
        path = fields[i + 1]
        status = status[:1]
        i += 2
        if status not in ("A", "D", "M", "T"):
            return None
        if status != "A":
            if old_mode not in _RESTORABLE_MODES:
                return None
            before[path] = _Side(old_mode, old_blob)
        if status != "D":
            if new_mode not in _REMOVABLE_MODES:
                return None
            after[path] = _Side(new_mode, new_blob)
    return before, after


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
    entries = name_status(vault, commit) or []
    paths = own_paths(entries)
    sides = _sides(vault, parent, commit, paths) if paths else None
    if sides is None:
        return "git-unreadable"
    before, after = sides
    # name_status detects renames against the same first parent; own_paths holds both sides of each.
    renamed = {sides_[0]: sides_[1] for status, sides_ in entries if status[:1] == "R" and sides_[0] in paths}
    steps = [_Step(new, old, before.get(old), after.get(new)) for old, new in renamed.items()]
    paired = {*renamed, *renamed.values()}
    steps += [_Step(p, p, before.get(p), after.get(p)) for p in paths if p not in paired]
    # A file needs a side to undo from, a rename both: a path git's raw diff didn't list is unreadable.
    if any(sum(s is not None for s in (st.before, st.after)) < (2 if st.read != st.write else 1) for st in steps):
        return "git-unreadable"
    return _Plan(tuple(paths), tuple(steps))


def _blob(vault: Path, side: _Side | None) -> bytes | None:
    """A side's bytes as git stores them (no filters), b"" for no side, None when git can't read it."""
    return b"" if side is None else _git_bytes(vault, "cat-file", "blob", side.blob)


def _current(path: Path) -> tuple[str, bytes, int]:
    """What stands at ``path`` now, never followed, with its permission bits: ``("file", bytes, bits)``,
    ``("link", target, 0)``, ``("absent", b"", 0)``, or ``("other", b"", 0)`` (a folder, anything else, or
    unreadable: a path under something that is no longer a folder)."""
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            return "link", os.readlink(os.fsencode(path)), 0
        if stat.S_ISREG(st.st_mode):
            return "file", path.read_bytes(), stat.S_IMODE(st.st_mode)
    except FileNotFoundError:
        return "absent", b"", 0
    except OSError:
        pass
    return "other", b"", 0


def _binary(data: bytes) -> bool:
    return b"\0" in data[:_BINARY_PROBE]


def _temp_root() -> str:
    """Where the merge's private folder is made: the account's own temp folder, never one the environment names (a
    run sets ``TMPDIR``, and could point it into the vault, or at a folder it watches to swap an input before
    ``merge-file`` reads it). macOS: the per-user folder the system gives (``confstr`` of
    ``_CS_DARWIN_USER_TEMP_DIR``); elsewhere, or if it gives none, ``/tmp``."""
    if sys.platform == "darwin":
        try:
            found = os.confstr(_CS_DARWIN_USER_TEMP_DIR)
        except (OSError, ValueError):
            found = None
        if found:
            return found
    return "/tmp"  # noqa: S108 - the fallback root; the folder made in it is a fresh, private mkdtemp


def _reversed_lines(data: bytes) -> bytes:
    """The lines in reverse order; exact and its own inverse (a missing final newline included)."""
    return b"\n".join(reversed(data.split(b"\n")))


def _merge_in(vault: Path, folder: str, tag: str, current: bytes, base: bytes, other: bytes) -> bytes | None:
    names = []
    for role, data in (("current", current), ("base", base), ("other", other)):
        name = os.path.join(folder, f"{tag}-{role}")
        with open(name, "xb") as f:  # a fresh name in a fresh private folder
            f.write(data)
        names.append(name)
    return _git_bytes(vault, "merge-file", "-p", "--", *names)


def _merge(vault: Path, current: bytes, base: bytes, other: bytes) -> bytes | str:
    """``git merge-file -p <current> <base> <other>``, git's default resolution (never ours/theirs/union): the merged
    bytes, or ``edited-since``.

    The merge runs twice, on the files and on their lines reversed, and counts only when both are clean and agree.
    Where the file repeats a block (adjacent twins, or twins whose separators repeat too) and a later edit deleted
    the change's own copy, the bytes say nothing about which copy went; xdiff picks one by position, and a forward
    merge alone could take back the copy that was never the change's. Reversed, xdiff picks the other one, so the
    two disagree or one conflicts. ``edited-since`` covers a conflict, a merge-file error (the runner can't tell
    them apart) and a disagreement: the undo can't be worked out from the file as it is.

    The inputs go to a fresh private folder (``mkdtemp``) under :func:`_temp_root`, always removed;
    ``git-unreadable`` when it can't be made or written, or when it would be inside the vault."""
    try:
        folder = tempfile.mkdtemp(prefix="scout-undo.", dir=_temp_root())
    except OSError:
        return "git-unreadable"
    try:
        if Path(folder).resolve().is_relative_to(vault.resolve()):
            return "git-unreadable"
        forward = _merge_in(vault, folder, "forward", current, base, other)
        backward = _merge_in(vault, folder, "reversed", *(_reversed_lines(b) for b in (current, base, other)))
    except OSError:
        return "git-unreadable"
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if forward is None or backward is None or _reversed_lines(backward) != forward:
        return "edited-since"
    return forward


# What stands at a vault-relative path in some tree (the working tree, or a commit's): see :func:`_current`.
_Reader = Callable[[str], tuple[str, bytes, int]]


def _tree_reader(vault: Path, rev: str) -> _Reader:
    """:func:`_current` for the tree of commit ``rev``: a regular file's bits are 0o644 or 0o755, a path under a file
    (git can't list it) is ``other`` as in the working tree, and anything git can't read is ``other`` (fail closed)."""

    def entries(raw: bytes) -> list[tuple[str, str, str, str]]:
        out = []
        for rec in os.fsdecode(raw).split("\0"):
            meta, _, path = rec.partition("\t")
            cols = meta.split(" ")
            if path and len(cols) == 3:
                out.append((cols[0], cols[1], cols[2], path))
        return out

    def read(rel: str) -> tuple[str, bytes, int]:
        raw = _git_bytes(vault, "--literal-pathspecs", "ls-tree", "-z", rev, "--", rel)
        if raw is None:
            return "other", b"", 0
        found = [e for e in entries(raw) if e[3] == rel]
        if not found:
            folders = [str(p) for p in PurePosixPath(rel).parents if str(p) != "."]
            up = _git_bytes(vault, "--literal-pathspecs", "ls-tree", "-z", rev, "--", *folders) if folders else b""
            # Only the folders asked about: to look for a missing one, git also lists what is beside it.
            if up is None or any(kind != "tree" for _, kind, _, path in entries(up) if path in folders):
                return "other", b"", 0
            return "absent", b"", 0
        mode, _, oid, _ = found[0]
        data = _git_bytes(vault, "cat-file", "blob", oid) if mode in (*_RESTORABLE_MODES, _LINK_MODE) else None
        if data is None:
            return "other", b"", 0
        if mode == _LINK_MODE:
            return "link", data, 0
        return "file", data, _file_mode(mode)

    return read


def _undo_step(vault: Path, step: _Step, current: _Reader) -> list[_Op] | str:
    before = _blob(vault, step.before)
    after = _blob(vault, step.after)
    if before is None or after is None:
        return "git-unreadable"
    kind, now, bits = current(step.read)
    if step.after is None:  # the change deleted it: put it back, unless something stands there now
        if kind != "absent" or step.before is None:
            return "edited-since"
        return [_Op(step.write, before, _file_mode(step.before.mode))]
    if kind != ("link" if step.after.mode == _LINK_MODE else "file"):
        return "edited-since"
    if step.before is None:  # the change added it (a symlink: its target): remove it, unless edited since
        return [_Op(step.read, None)] if now == after else "edited-since"
    if step.read != step.write and current(step.write)[0] != "absent":
        # A rename whose old path was made again since, or can't be made (a file now stands where a folder on its way
        # was): either way the undo couldn't write it.
        return "edited-since"
    if step.after.mode == _LINK_MODE or any(_binary(b) for b in (before, after, now)):
        if now != after:  # a changed symlink or a binary file: only exactly as the change left it
            return "edited-since"
        result: bytes = before
    else:
        merged = _merge(vault, now, after, before)
        if isinstance(merged, str):
            return merged
        if merged == now and before != after and step.read == step.write:
            # Every line the change made is already back as it was (undone by hand, or by a commit the report
            # doesn't count as its rollback): an undo would write nothing and stay due forever.
            return "edited-since"
        result = merged
    # The mode merges like the content: P's where the change set it and the file still has the change's, else the
    # file's own (a later chmod stays). W's kind matches C's (checked above) and a file has two modes, so the three
    # can't all differ.
    now_mode = _LINK_MODE if kind == "link" else ("100755" if bits & stat.S_IXUSR else "100644")
    mode = _file_mode(step.before.mode) if now_mode == step.after.mode != step.before.mode else bits
    ops = [_Op(step.write, result, mode)]
    if step.read != step.write:
        ops.append(_Op(step.read, None))
    return ops


def _file_mode(mode: str) -> int:
    return 0o755 if mode == "100755" else 0o644


def _undo(vault: Path, plan: _Plan) -> list[_Op] | str:
    """Every write that undoes ``plan`` in the working tree as it is now, or why not (``edited-since``,
    ``git-unreadable``). Writes nothing. Per file, with ``P`` the parent's version, ``C`` the commit's and ``W`` the
    working tree's:

    - a text file the change modified: ``git merge-file`` of ``W`` (current), ``C`` (base), ``P`` (other). Positional:
      only the change's own lines are taken back, and a conflict (a later edit touched them) refuses, as does a
      merge that would change nothing (every line the change made is already back as it was);
    - a binary file, or a symlink the change made of a file: undone only when ``W`` is exactly ``C``;
    - a file the change added: removed only when ``W`` is exactly ``C`` (a symlink: the same target);
    - a file it deleted: restored only when nothing stands at that path now;
    - a rename: merged as modified (``W`` read at the new path), written at the old path (refused if something stands
      there now), the new path removed;
    - a file and a folder that traded places (the change turned a file into a folder, or the reverse): refused as
      ``edited-since``. Undoing it would mean removing a folder before writing a file there, which the undo doesn't
      do; no truer reason exists among the ones ``due`` reports, so it fails closed under that one.

    The mode merges 3-way like the content: a restored file gets ``P``'s; a merged one gets ``P``'s where the change
    changed it and the file still has ``C``'s, and otherwise keeps its own (a later ``chmod +x`` stays)."""
    # Uncommitted work on these paths (a run mid-edit, a rollback not yet committed) would be mixed into the undo.
    dirty = _git_bytes(vault, "--literal-pathspecs", "status", "--porcelain", "-z", "--", *plan.paths)
    if dirty is None:
        return "git-unreadable"
    if dirty.strip(b"\0"):
        return "edited-since"
    return _ops(vault, plan, lambda rel: _current(vault / rel))


def _ops(vault: Path, plan: _Plan, current: _Reader) -> list[_Op] | str:
    """:func:`_undo` against the tree ``current`` reads, without the uncommitted-edits check."""
    ops: list[_Op] = []
    for step in plan.steps:
        got = _undo_step(vault, step, current)
        if isinstance(got, str):
            return got
        ops += got
    return ops


def restores(vault: Path, target: str, rollback: str) -> bool:
    """Commit ``rollback`` undid commit ``target`` and nothing more: working the undo of ``target`` out against
    ``rollback``'s parent (as :func:`_undo` would against the working tree) gives exactly what ``rollback`` left at
    every path it writes or removes, executable bit included, and ``rollback`` touched no other self-change path (so
    a rollback can't carry an untested edit). What makes a commit a rollback (scout.changes.scan): a trailer or a
    ``Revert`` subject only names the candidate, so a commit can't claim to revert a change without putting its
    lines back, and the claim can't hide an edit from its test or its undo. Both commits are immutable, so a yes is
    cached in-process (a no may be a passing git failure); never on disk, where a run could write it. A ``git
    revert`` passes too (the same 3-way merge), unless its conflicts were resolved by hand. False whenever anything
    can't be read."""
    if not (_FULL_SHA_RE.match(target) and _FULL_SHA_RE.match(rollback)):
        return False
    key = (str(vault.resolve()), target, rollback)
    if key not in _RESTORED:
        if not _restores(vault, target, rollback):
            return False
        _RESTORED.add(key)
    return True


_RESTORED: set[tuple[str, str, str]] = set()


def _restores(vault: Path, target: str, rollback: str) -> bool:
    plan = _plan(vault, target)
    if isinstance(plan, str):
        return False
    shas = (_git(vault, "rev-list", "--parents", "-n", "1", rollback, "--") or "").split()
    if len(shas) < 2 or _git(vault, "merge-base", "--is-ancestor", target, shas[1]) is None:
        return False
    ops = _ops(vault, plan, _tree_reader(vault, shas[1]))
    if isinstance(ops, str) or not ops:
        return False
    touched = name_status(vault, rollback)
    if touched is None or not set(own_paths(touched)) <= {op.path for op in ops}:
        return False
    left = _tree_reader(vault, rollback)
    for op in ops:
        kind, data, bits = left(op.path)
        if op.data is None:
            if kind != "absent":
                return False
        elif kind != "file" or data != op.data or (bits & stat.S_IXUSR) != (op.mode & stat.S_IXUSR):
            return False
    return True


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


def _stage(target: Path, data: bytes, mode: int) -> Path:
    """``data`` with bits ``mode`` in a fresh temp file beside ``target`` (``O_EXCL | O_NOFOLLOW``: never a name that
    was already there), synced; its path."""
    for _ in range(16):
        tmp = target.with_name(f".{target.name}.{os.urandom(6).hex()}.scout-undo")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o600)
        except FileExistsError:
            continue
        try:
            os.fchmod(fd, mode)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            tmp.unlink(missing_ok=True)
            raise
        os.close(fd)
        return tmp
    raise FileExistsError(f"no free temporary name beside {target.name}")  # pragma: no cover - 16 collisions


# A temp file :func:`_stage` left behind (a crash between staging and renaming) is swept once it is this old, so a
# rollback running now never removes another one's.
_STALE_STAGE = timedelta(minutes=10)


def _sweep(vault: Path, rels: Sequence[str], *, now: datetime) -> None:
    """Remove stale ``.<name>.<hex>.scout-undo`` regular files from the folders of ``rels`` (never following a link)."""
    for folder in sorted({(vault / rel).parent for rel in rels}):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not (name.startswith(".") and name.endswith(".scout-undo")):
                continue
            path = folder / name
            with contextlib.suppress(OSError):
                st = os.lstat(path)
                if stat.S_ISREG(st.st_mode) and now.timestamp() - st.st_mtime > _STALE_STAGE.total_seconds():
                    path.unlink()


def _make_folders(vault: Path, folder: Path) -> list[Path]:
    """Make ``folder`` and the missing folders above it (never above the vault); the ones made, outermost first."""
    missing = []
    while folder != vault and not os.path.lexists(folder):
        missing.append(folder)
        folder = folder.parent
    made = []
    for f in reversed(missing):
        f.mkdir()
        made.append(f)
    return made


def _put_back(target: Path, was: tuple[str, bytes, int]) -> None:
    """Best effort: make ``target`` what :func:`_current` read there before a write."""
    from scout.safe_write import atomic_write_bytes

    kind, data, bits = was
    with contextlib.suppress(OSError):
        if kind == "file":
            atomic_write_bytes(target, data, mode=bits)
        else:
            target.unlink(missing_ok=True)
            if kind == "link":
                os.symlink(data, target)


def _unmake(made: Sequence[Path]) -> None:
    """Remove the folders :func:`_make_folders` made, innermost first, each only while empty."""
    for folder in reversed(made):
        with contextlib.suppress(OSError):
            folder.rmdir()


def _prune(vault: Path, folder: Path) -> None:
    """As git does when it deletes a file: folders left empty go, never the vault itself."""
    while folder != vault:
        try:
            folder.rmdir()
        except OSError:
            break
        folder = folder.parent


def _write(vault: Path, ops: Sequence[_Op]) -> None:
    """Write an undo worked out in full, all or nothing as far as the filesystem allows. First every new file is
    written in full to a fresh temp file beside its target (making any folder it needs); a failure there removes the
    temp files and those folders, so nothing changed. Then the temp files are renamed into place and the removals
    done (a rename's old side is back before its new side goes); a failure there puts back what it had already
    replaced or removed. Either way the error is raised. A symlink at a target is replaced, never written through."""
    made: list[Path] = []
    staged: list[tuple[Path, Path]] = []
    try:
        for op in ops:
            if op.data is not None:
                target = vault / op.path
                made += _make_folders(vault, target.parent)
                staged.append((_stage(target, op.data, op.mode), target))
    except BaseException:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)
        _unmake(made)
        raise
    done: list[tuple[Path, tuple[str, bytes, int]]] = []
    removed: list[Path] = []
    try:
        for tmp, target in staged:
            was = _current(target)
            os.replace(tmp, target)
            done.append((target, was))
        for op in ops:
            if op.data is None:
                target = vault / op.path
                was = _current(target)
                target.unlink()
                done.append((target, was))
                removed.append(target)
    except BaseException:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)  # the ones not renamed yet
        for target, was in reversed(done):
            _put_back(target, was)
        _unmake(made)
        raise
    for target in removed:
        _prune(vault, target.parent)


def rollback_problem(vault: Path, change: Change, test: Mapping[str, Any]) -> str | None:
    """None when ``change`` can be rolled back, else why not: ``governance``, ``not-a-run-change`` (its test state is
    ``not-tested``), ``root-commit``, ``edited-since`` (a later edit changed the change's own lines, so the 3-way
    merge conflicts; a file it added, a binary or a symlink is no longer exactly as it left it; something stands
    where a file it deleted or renamed away would go back; or its own paths have uncommitted edits) or
    ``git-unreadable``.

    Positional: a later commit that changed other lines of the same file does not block, and an identical block
    elsewhere in the file is never mistaken for the change's own lines. Works the undo out in full and writes
    nothing."""
    if change.governance:
        return "governance"
    if test.get("state") == "not-tested":
        return "not-a-run-change"
    plan = _plan(vault, change.commit)
    if isinstance(plan, str):
        return plan
    ops = _undo(vault, plan)
    return ops if isinstance(ops, str) else None


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
    from scout.changes.governance import read_pending, state_of
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
        state = state_of(vault, signals, p)
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
    if AUTO_ROLLBACK_LIVE and mode in _AUTO_KEYS and not doc.get("calibrating", True):
        from scout.changes import run_commits

        ledger = run_commits.read()
        for r in sorted(rows, key=lambda r: (r["ts"], r["id"])):
            test = r["test"]
            if (
                test.get("would_have_done", {}).get(_AUTO_KEYS[mode])
                and not test.get("rolled_back_by")
                and not test.get("marked_wrong")
                and auto_eligible(r["commit"], ledger)
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
    """Undo ``change_id`` in the working tree (never the index) over the change's own paths (its brain files,
    ``scripts/``, ``docs/wishlist/`` and both sides of a ``knowledge-base/`` rename; never session logs or audit
    rows): each text file is a positional 3-way merge (``git merge-file``; see :func:`_undo`), so later edits to other
    lines of those files stay and an identical block elsewhere is never rewritten. Returns the sorted vault-relative
    paths for the run to commit through ``git-safe-commit.sh``.

    Raises :class:`ActionRefused` unless the id is in ``due(...)["rollbacks"]``, and works the undo out again (the
    tree may have changed since ``due``). All or nothing: every file is worked out and every path checked before
    anything is written, so a refusal (``edited-since``, ...) writes nothing."""
    doc, changes = _safe_compute(vault, env, now=now, tz=tz, owner_vault=owner_vault)
    if change_id not in {r["id"] for r in doc["rollbacks"]}:
        why = next((b["why_blocked"] for b in doc["blocked"] if b["id"] == change_id), "not-due")
        raise ActionRefused(why)
    plan = _plan(vault, changes[change_id].commit)
    if isinstance(plan, str):
        raise ActionRefused(plan)
    if not all(_inside(vault, p) for p in plan.paths):
        raise ActionRefused("path outside the vault")
    ops = _undo(vault, plan)
    if isinstance(ops, str):
        raise ActionRefused(ops)
    _sweep(vault, plan.paths, now=now)
    _write(vault, ops)
    return list(plan.paths)


def _pending_item(vault: Path, env: Mapping[str, str], gov_id: str, want: str, owner_vault: Path | None) -> Any:
    from scout.changes.governance import read_pending, state_of
    from scout.changes.store import read_signals

    item = next((p for p in read_pending(vault)[0] if p.id == gov_id), None)
    if item is None:
        raise ActionRefused(f"no pending governance change {gov_id!r}")
    md = vault / item.path
    # governance-pending/ symlinked out of the vault, or the md a link: never move or delete through it.
    if md.is_symlink() or not _inside(vault, item.path):
        raise ActionRefused("path outside the vault")
    signals, _ = read_signals(vault, env, owner_vault=owner_vault)
    state = state_of(vault, signals, item)
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
