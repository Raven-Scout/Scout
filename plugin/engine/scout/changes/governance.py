"""Pending governance changes and the owner's decisions about them.

A governance change (one that edits what Scout is allowed to do) waits for the owner's OK as one
``governance-pending/<slug>.md`` file: YAML frontmatter (``summary``, ``why``, optional ``targets``, optional
``governance: false`` for an ordinary improvement a run's harness wouldn't let it commit directly) and exactly one
fenced ``diff`` block. The folder is the source of truth. A decision is an owner-store ``approval`` row whose ref is
``approve:<id>@<hash>`` or ``reject:<id>@<hash>``, ``<hash>`` being :func:`item_hash` of the item the owner saw (its
diff and its frontmatter); the newest one for the file as it is now wins, so a file edited after the decision is
waiting again. Readers never
raise: a file that doesn't fit is counted."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scout.changes.models import SUMMARY_MAX, Signal
from scout.yaml_safe import load_yaml

PENDING_DIR = "governance-pending"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
ID_PREFIX = "gov-"
_MAX_BYTES = 256 * 1024
CONTEXT_MAX = 2000
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)(.*)\Z", re.DOTALL)
_FENCE_RE = re.compile(r"^```[ \t]*diff[ \t]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)
_GIT_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_PATH_PREFIXES = ("--- ", "+++ ", "rename from ", "rename to ", "copy from ", "copy to ")


@dataclass(frozen=True)
class Pending:
    id: str
    slug: str
    summary: str
    why: str
    targets: tuple[str, ...]
    path: str  # vault-relative
    diff: str
    paths: tuple[str, ...] = ()  # what the diff patches (diff_paths): both sides of a rename, never /dev/null
    # False for a harness-fallback item: applying it deletes the file instead of moving it to applied/, so the
    # commit is an ordinary, testable change rather than a governance one.
    governance: bool = True
    # The prose around the diff block (the run's own explanation), at most CONTEXT_MAX characters; "" when there is
    # none. Not part of what the owner's decision is bound to: it explains the diff, it doesn't change it.
    context: str = ""

    @property
    def hash(self) -> str:
        return item_hash(
            self.diff, governance=self.governance, targets=self.targets, summary=self.summary, why=self.why
        )


def item_hash(diff: str, *, governance: bool, targets: Sequence[str], summary: str, why: str) -> str:
    """The full SHA-256 (64 hex digits) of everything the owner saw in a pending file: its diff block and its
    frontmatter (``governance``, ``targets``, ``summary``, ``why``), canonically encoded. An owner decision is bound
    to it, so flipping ``governance`` or adding a target after the OK voids the OK, as editing the diff does."""
    doc = {"diff": diff, "governance": governance, "summary": summary, "targets": list(targets), "why": why}
    canonical = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_path(raw: str) -> bool:
    """A header path that stays inside the vault; fail closed on anything odd (whitespace, quotes, controls)."""
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    if not raw or re.search(r"[\s\"\\\x00-\x1f\x7f]", raw) or raw.startswith(("/", "~")):
        return False
    return ".." not in raw.split("/")


def _header_path_ok(raw: str, *, timestamped: bool) -> bool:
    if timestamped:
        raw = raw.split("\t", 1)[0]  # `--- a/x<TAB>2026-01-01 ...`
    return raw == "/dev/null" or _safe_path(raw)


def diff_paths(diff: str) -> list[str] | None:
    """The vault-relative paths the diff patches, sorted, once each (both sides of a rename, never ``/dev/null``),
    or None when a header names a path outside the vault, there is no header, or the diff doesn't parse.

    Only lines outside hunks are headers; a hunk's lines are counted off its ``@@`` line, so a removed line such as
    ``-- foo`` (which reads ``--- foo``) is content."""
    headers = 0
    found: set[str] = set()
    old_left = new_left = 0
    for line in diff.split("\n"):
        if old_left or new_left:
            if line.startswith("\\"):
                continue  # "\ No newline at end of file"
            tag = line[:1]
            if tag == "-":
                old_left -= 1
            elif tag == "+":
                new_left -= 1
            elif tag in (" ", ""):
                old_left -= 1
                new_left -= 1
            else:
                return None
            if old_left < 0 or new_left < 0:
                return None
            continue
        hunk = _HUNK_RE.match(line)
        if hunk:
            old_left, new_left = (int(g) if g is not None else 1 for g in hunk.groups())
            continue
        if line.startswith("@@"):
            return None
        if line.startswith("diff --git "):
            m = _GIT_HEADER_RE.match(line)
            if not (m and _safe_path(m.group(1)) and _safe_path(m.group(2))):
                return None
            found.update(m.groups())
            headers += 1
            continue
        for prefix in _PATH_PREFIXES:
            if line.startswith(prefix):
                timestamped = prefix in ("--- ", "+++ ")
                raw = line[len(prefix) :]
                if not _header_path_ok(raw, timestamped=timestamped):
                    return None
                side = raw.split("\t", 1)[0] if timestamped else raw
                if timestamped and side != "/dev/null":
                    if not side.startswith(("a/", "b/")):
                        return None  # git apply strips one leading folder (-p1): unprefixed, it patches another path
                    side = side[2:]
                if side != "/dev/null":
                    found.add(side)
                headers += timestamped
                break
    if headers == 0 or old_left or new_left:
        return None
    return sorted(found)


def _parse(path: Path, slug: str) -> Pending | None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
        return None
    return _parse_text(path.read_text(encoding="utf-8"), slug)


def _parse_text(text: str, slug: str) -> Pending | None:
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        return None
    meta = load_yaml(m.group(1))
    if not isinstance(meta, dict):
        return None
    summary, why, targets = meta.get("summary"), meta.get("why"), meta.get("targets", [])
    governance = meta.get("governance", True)
    if not isinstance(governance, bool):
        return None
    if not isinstance(summary, str) or not summary.strip() or len(summary) > SUMMARY_MAX:
        return None
    if not isinstance(why, str) or not why.strip():
        return None
    if targets is None:
        targets = []
    if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
        return None
    blocks = _FENCE_RE.findall(m.group(2))
    patched = diff_paths(blocks[0]) if len(blocks) == 1 else None
    if patched is None:
        return None
    context = re.sub(r"\n{3,}", "\n\n", _FENCE_RE.sub("", m.group(2))).strip()
    if len(context) > CONTEXT_MAX:
        context = context[: CONTEXT_MAX - 1].rstrip() + "…"
    return Pending(
        id=ID_PREFIX + slug,
        slug=slug,
        summary=summary,
        why=why,
        targets=tuple(targets),
        path=f"{PENDING_DIR}/{slug}.md",
        diff=blocks[0],
        paths=tuple(patched),
        governance=governance,
        context=context,
    )


def read_pending(vault: Path) -> tuple[list[Pending], int]:
    """Each readable ``governance-pending/<slug>.md`` (sorted by slug) and how many files didn't fit. Never raises.

    Only files directly in the folder count: ``applied/`` and any other subfolder are skipped. A missing folder is
    no files."""
    folder = vault / PENDING_DIR
    out: list[Pending] = []
    bad = 0
    try:
        entries = sorted(p for p in folder.iterdir() if p.is_file() or p.is_symlink())
    except OSError:
        return [], 0
    for path in entries:
        if path.suffix != ".md":
            continue  # not a pending item (a .gitkeep, say)
        if not SLUG_RE.match(path.stem):
            bad += 1
            continue
        try:
            item = _parse(path, path.stem)
        except Exception:  # noqa: BLE001 - unreadable, not UTF-8, malformed YAML: counted, never raised
            item = None
        if item is None:
            bad += 1
        else:
            out.append(item)
    return out, bad


def decision_ref(verb: str, target_id: str, digest: str, filed: str | None = None) -> str:
    """The ref an owner decision is recorded under: ``<verb>:<id>@<hash of the item the owner saw>``, and
    ``@<sha>`` of the commit that filed it (:func:`filed_by`) when it is committed, which binds the decision to that
    one filing."""
    return f"{verb}:{target_id}@{digest}" + (f"@{filed}" if filed else "")


def decision(
    signals: Sequence[Signal],
    target_id: str,
    digest: str,
    *,
    filed: str | None = None,
    spent: datetime | None = None,
) -> str:
    """``approved``, ``rejected`` or ``awaiting-ok``: the newest matching ``approve`` / ``reject`` row wins. A row for
    another digest (the file was changed after the decision) or with none counts for nothing. A decision is used up
    once acted on:

    - a row bound to a filing (``…@<digest>@<sha>``) counts only while ``filed`` is that sha, so once the file has
      left ``governance-pending/`` and come back, by any commit, it waits for a new decision. Commit order, not a
      clock: a run can't forge it;
    - an unbound row (an older engine's, or a file not yet committed) counts only after ``spent`` (:func:`spent_at`),
      compared in whole seconds because git keeps no finer time."""
    base = {
        decision_ref("approve", target_id, digest): "approved",
        decision_ref("reject", target_id, digest): "rejected",
    }
    bound = (
        {
            decision_ref(v, target_id, digest, filed): state
            for v, state in (("approve", "approved"), ("reject", "rejected"))
        }
        if filed
        else {}
    )
    state, newest = "awaiting-ok", None
    for s in signals:
        if s.kind != "approval" or s.ref is None:
            continue
        if s.ref in bound:
            got = bound[s.ref]
        elif s.ref in base and (spent is None or s.ts.replace(microsecond=0) > spent):
            got = base[s.ref]
        else:
            continue
        if newest is None or s.ts >= newest:
            state, newest = got, s.ts
    return state


# How the filing and spent lookups walk history: along first parents, each merge diffed against its first parent (as
# scan.name_status diffs a commit), renames as a delete plus an add. git's default history simplification would follow
# a merge's side branch whenever the merge is TREESAME to it for the path, so a merge that brings a pending file back
# would show the side branch's old filing and hide the application before it, letting a spent decision count again.
_ALONG_FIRST_PARENTS = ("--first-parent", "-m", "--no-renames")


def filed_by(vault: Path, item: Pending) -> str | None:
    """The sha of the commit that filed ``item`` as it is now: the newest commit that added its
    ``governance-pending/<slug>.md``, provided ``HEAD`` holds that file with this exact :func:`item_hash` (so no
    removal came after it). None when the file isn't committed as it is, or git can't say."""
    from scout.changes.scan import _FULL_SHA_RE, _git

    path = f"{PENDING_DIR}/{item.slug}.md"
    text = _git(vault, "show", f"HEAD:{path}")
    try:
        head = _parse_text(text, item.slug) if text is not None else None
    except Exception:  # noqa: BLE001 - malformed YAML at HEAD: not this file
        head = None
    if head is None or head.hash != item.hash:
        return None
    found = _git(vault, "log", *_ALONG_FIRST_PARENTS, "-n", "1", "--diff-filter=A", "--format=%H", "--", path)
    sha = (found or "").strip()
    return sha if _FULL_SHA_RE.match(sha) else None


# What spent_at returns when git can't say: every decision is used up, so nothing is applied on an unread history.
_ALL_SPENT = datetime.max.replace(tzinfo=UTC)


def spent_at(vault: Path, item: Pending, *, before: str | None = None) -> datetime | None:
    """For decisions not bound to a filing (:func:`decision`): when a decision about ``item`` was last acted on, the
    commit time of the newest commit (along first parents) that took this exact file (same slug, same
    :func:`item_hash`) out of ``governance-pending/``, whether ``apply`` moved it to ``applied/``, deleted a
    ``governance: false`` one, or ``discard`` deleted it. None when none did. Outside a git repository, None; when the
    repository can't be read, every decision counts as spent. A bound decision needs none of this: commit order says
    which filing it was for.

    ``before`` (a commit sha) reads only the history before that commit instead of all of ``HEAD``'s."""
    from scout.changes.scan import _git  # the scanner's git runner: no locks, a timeout, never raises

    path = f"{PENDING_DIR}/{item.slug}.md"
    revs = [f"{before}^"] if before else []
    log = _git(vault, "log", *_ALONG_FIRST_PARENTS, "--diff-filter=D", "--format=%H %cI", *revs, "--", path)
    if log is None:
        # No repository at all: nothing was ever applied (and `apply` refuses without git). A repository git can't
        # read: fail closed.
        return None if _git(vault, "rev-parse", "--git-dir") is None else _ALL_SPENT
    for line in log.splitlines():  # newest first
        sha, _, when = line.partition(" ")
        text = _git(vault, "show", f"{sha}^:{path}")
        try:
            gone = _parse_text(text, item.slug) if text is not None else None
            ts = datetime.fromisoformat(when)
        except Exception:  # noqa: BLE001 - malformed YAML or date: that removal says nothing about this file
            continue
        if gone is not None and gone.hash == item.hash:
            return ts
    return None


def state_of(vault: Path, signals: Sequence[Signal], item: Pending) -> str:
    """:func:`decision` for a pending file as it is now, its spent decisions left out."""
    return decision(signals, item.id, item.hash, filed=filed_by(vault, item), spent=spent_at(vault, item))


_MOVED_RE = re.compile(rf"^{PENDING_DIR}/([a-z0-9][a-z0-9-]{{0,60}})\.md$")


def approved_application(vault: Path, signals: Sequence[Signal], apply_sha: str) -> bool:
    """Whether commit ``apply_sha`` applied governance items the owner approved, and nothing else: it moves at least
    one ``governance-pending/<slug>.md`` to ``governance-pending/applied/<slug>.md`` (what makes a commit governance,
    scout.changes.scan), for every such move the owner's decision on the applied file as the commit holds it (its
    :func:`item_hash`) was ``approved`` as of the commit, and the commit holds exactly those items' diffs
    (:func:`_holds_only`). The scanner trusts the move alone, so without this a run could make any edit immune to its
    test and its undo by moving an unapproved pending file itself, or by committing an unapproved edit alongside an
    approved one (a whole-file commit sweeps up whatever else was left uncommitted).

    As of the commit means :func:`decision` with the filing (the newest first-parent commit before ``apply_sha`` that
    added the pending file) and with unbound rows counted only up to the commit's time, in whole seconds, and only after
    an earlier application of the same file (:func:`spent_at` before ``apply_sha``). A bound row counts whatever its
    time: commit order ties it to its filing. The applied file must parse as a governance item (a ``governance: false``
    item is deleted on apply, never moved). False whenever git can't say."""
    from scout.changes.scan import name_status

    entries = name_status(vault, apply_sha) or []
    slugs = []
    for status, sides in entries:
        m = _MOVED_RE.match(sides[0]) if status[:1] == "R" else None
        if m and sides[1] == f"{PENDING_DIR}/applied/{m.group(1)}.md":
            slugs.append(m.group(1))
    applied_at = _commit_time(vault, apply_sha) if slugs else None
    if applied_at is None:
        return False
    items = []
    for slug in slugs:
        item = _approved_move(vault, signals, apply_sha, slug, applied_at)
        if item is None:
            return False
        items.append(item)
    return _holds_only(vault, apply_sha, entries, items)


def _holds_only(
    vault: Path, apply_sha: str, entries: Sequence[tuple[str, Sequence[str]]], items: Sequence[Pending]
) -> bool:
    """Whether commit ``apply_sha`` changed only what applying ``items`` changes: every path it touched is one an
    item's diff patches or an item's own pending/applied file, and replaying the items' diffs (in slug order, as
    ``git apply`` would) on the parent's versions of the patched paths gives exactly the commit's versions, content
    and executable bit. Fails closed: a symlink or any mode but a plain file, or anything git can't read or replay."""
    allowed: set[str] = set()
    for item in items:
        allowed.update(item.paths, (item.path, f"{PENDING_DIR}/applied/{item.slug}.md"))
    if not all(side in allowed for _, sides in entries for side in sides):
        return False
    patched = sorted({p for item in items for p in item.paths})
    before = _files_at(vault, f"{apply_sha}^", patched)
    after = _files_at(vault, apply_sha, patched)
    if before is None or after is None:
        return False
    return _replayed(before, patched, [item.diff for item in items]) == after


_Files = dict[str, tuple[bool, bytes]]  # path -> (executable, content); a path that isn't there is left out


def _files_at(vault: Path, rev: str, paths: Sequence[str]) -> _Files | None:
    """``paths`` as commit ``rev`` holds them, or None when git can't read them or one is not a plain file."""
    from scout.changes.scan import _git_bytes

    raw = _git_bytes(vault, "--literal-pathspecs", "ls-tree", "-r", "-z", rev, "--", *paths)
    if raw is None:
        return None
    out: _Files = {}
    wanted = set(paths)
    for entry in os.fsdecode(raw).split("\0"):
        meta, _, path = entry.partition("\t")
        if path not in wanted:
            continue  # the trailing empty field, or a file under a folder an item names
        mode, _, oid = meta.split(" ")
        if mode not in ("100644", "100755"):
            return None
        data = _git_bytes(vault, "cat-file", "blob", oid)
        if data is None:
            return None
        out[path] = (mode == "100755", data)
    return out


def _replayed(before: _Files, paths: Sequence[str], diffs: Sequence[str]) -> _Files | None:
    """``paths`` after applying ``diffs`` in order to ``before``, with ``git apply`` in a fresh private folder (never
    the vault), or None when a diff doesn't apply or leaves something other than a plain file."""
    from scout.changes.actions import _temp_root  # actions imports this module lazily too
    from scout.changes.scan import _git_bytes

    folder = tempfile.mkdtemp(prefix="scout-replay-", dir=_temp_root())
    try:
        for rel, (executable, data) in before.items():
            target = os.path.join(folder, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "xb") as f:  # a fresh name in a fresh private folder
                f.write(data)
            os.chmod(target, 0o755 if executable else 0o644)
        for diff in diffs:
            done = _git_bytes(
                Path(folder), "apply", "--whitespace=nowarn", stdin=diff.encode("utf-8"), outside_repo=True
            )
            if done is None:
                return None
        out: _Files = {}
        for rel in paths:
            target = os.path.join(folder, rel)
            if os.path.islink(target):
                return None
            if os.path.isfile(target):
                with open(target, "rb") as f:
                    out[rel] = (bool(os.stat(target).st_mode & stat.S_IXUSR), f.read())
        return out
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _commit_time(vault: Path, sha: str) -> datetime | None:
    from scout.changes.scan import _git

    raw = _git(vault, "log", "-1", "--format=%cI", sha, "--")
    try:
        return datetime.fromisoformat(raw.strip()) if raw else None
    except ValueError:
        return None


def _approved_move(
    vault: Path, signals: Sequence[Signal], apply_sha: str, slug: str, applied_at: datetime
) -> Pending | None:
    """The applied item, when the owner's decision on it as of ``apply_sha`` was ``approved``; else None."""
    from scout.changes.scan import _FULL_SHA_RE, _git

    text = _git(vault, "show", f"{apply_sha}:{PENDING_DIR}/applied/{slug}.md")
    try:
        item = _parse_text(text, slug) if text is not None else None
    except Exception:  # noqa: BLE001 - malformed YAML: not an item the owner could have approved
        item = None
    if item is None or not item.governance:
        return None
    path = f"{PENDING_DIR}/{slug}.md"
    found = _git(
        vault, "log", *_ALONG_FIRST_PARENTS, "-n", "1", "--diff-filter=A", "--format=%H", f"{apply_sha}^", "--", path
    )
    filing = (found or "").strip()
    unbound = {decision_ref(verb, item.id, item.hash) for verb in ("approve", "reject")}
    rows = [s for s in signals if s.ref not in unbound or s.ts.replace(microsecond=0) <= applied_at]
    filed = filing if _FULL_SHA_RE.match(filing) else None
    spent = spent_at(vault, item, before=apply_sha)
    return item if decision(rows, item.id, item.hash, filed=filed, spent=spent) == "approved" else None


def undo_requested(signals: Sequence[Signal], change_id: str) -> bool:
    """Whether the owner asked to undo ``change_id`` (an ``approval`` row with ref ``undo:<change_id>``)."""
    ref = f"undo:{change_id}"
    return any(s.kind == "approval" and s.ref == ref for s in signals)
