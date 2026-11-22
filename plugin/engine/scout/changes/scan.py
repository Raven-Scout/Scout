"""Git scanner: turns self-change commits in a Scout vault repo into ``Change`` rows, idempotently by commit SHA.

Only the commit subject-derived summary, vault-relative paths, the sha and the why are recorded; never diff
content. A non-repo or any git failure (a timeout included) means "no new changes", never an exception."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from scout.changes.models import CHANGE_ID_RE, SUMMARY_MAX, WHY_TYPES, Change, Why
from scout.changes.store import append_change, read_changes, read_scan_mark, write_scan_mark

# One git call may take this long; past it the scan records nothing and the watermark stays put.
GIT_TIMEOUT_S = 30.0
# `derive` reads this many commits per git call, so a big ledger stays under ARG_MAX and the timeout.
DERIVE_CHUNK = 500
# How far back the first watermark scan reads (no mark yet, or its commit is gone from the repository).
FALLBACK_WINDOW = timedelta(days=14)

BRAIN_FILES = frozenset({"SKILL.md", "DREAMING.md", "RESEARCH.md", "RESPOND.md"})
_PHASE_LANES = ("briefing", "consolidation", "dreaming", "research", "work")
_UPDATE_PREFIXES = ("scout-update:", "upgrade", "chore(scout-update)")
_PREFIX_RE = re.compile(r"^(?:(?:briefing|consolidation|dreaming|research|work|trading) \[[^\]]*\]|scout):\s*")
_PATTERN_RE = re.compile(r"Pattern\s+#(\d+)", re.IGNORECASE)
_WISHLIST_PATH_RE = re.compile(r"docs/wishlist/([A-Za-z0-9._-]+?)(?:\.md)?(?=\s|$|[,;:)\]])")
_STATUS_DONE_RE = re.compile(r"^\+\s*status:\s*done\b", re.MULTILINE)
_REVERTS_BODY_RE = re.compile(r"^This reverts commit ([0-9a-f]{40})", re.MULTILINE)
_FULL_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
# A governance change is the commit that moves an approved pending file into applied/ (scout.changes.actions); the
# folder names are spelled out here because governance imports nothing from the scanner and vice versa.
_PENDING_MD_RE = re.compile(r"^governance-pending/([a-z0-9][a-z0-9-]{0,60})\.md$")
_RS, _US = "\x1e", "\x1f"


def parse_trailers(message: str) -> dict[str, str]:
    """Reads the ``Scout-Change-*`` trailers (keys without the prefix, lowercased) from a commit message's last
    paragraph, the trailer block, as git reads trailers: a line quoted in the prose above never counts. There a
    trailer may be indented, since a run copies its commit from a code block nested in a list, indentation and all."""
    out: dict[str, str] = {}
    paragraphs = [p for p in re.split(r"\n[ \t]*\n", message.strip()) if p.strip()]
    for line in (paragraphs[-1] if paragraphs else "").splitlines():
        m = re.match(r"^\s*Scout-Change-(Why|Summary|Targets|Governance|Reverts):\s*(.*?)\s*$", line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


def is_self_path(status: str, path: str) -> bool:
    """A path a self-change is made of, and so what undoing one restores: a brain file, anything under ``scripts/``
    or ``docs/wishlist/``, or either side of a rename inside ``knowledge-base/``. Never session logs, the mistake
    audit or action items: a later run's own bookkeeping there is not part of the change."""
    return (
        path in BRAIN_FILES
        or path.startswith(("scripts/", "docs/wishlist/"))
        or (status[:1] == "R" and path.startswith("knowledge-base/"))
    )


def own_paths(entries: Sequence[tuple[str, Sequence[str]]]) -> list[str]:
    """Sorted, once each: every side of each ``(status, paths)`` entry that has a side :func:`is_self_path` accepts
    (so both sides of a rename into or out of ``scripts/``)."""
    out: set[str] = set()
    for status, sides in entries:
        if any(is_self_path(status, p) for p in sides):
            out.update(sides)
    return sorted(out)


def lane_of(subject: str) -> str | None:
    if subject.startswith("trading ["):
        return None
    for lane in _PHASE_LANES:
        if subject.startswith(f"{lane} ["):
            return lane
    if subject.startswith(_UPDATE_PREFIXES):
        return "update"
    return "interactive"


def classify(
    paths: list[tuple[str, str]],
    subject: str,
    diff_of: Callable[[str], str],
    *,
    rollback: bool = False,
) -> str | None:
    """The commit's kind. ``rollback`` says git showed it undid the change it names (:func:`_rolls_back`); a
    ``Scout-Change-Reverts:`` trailer or a ``Revert`` subject alone never makes one, so the commit is classified by
    what it touched, tested and undoable like any other."""
    if lane_of(subject) == "update":
        return "upgrade"
    if rollback:
        return "rollback"
    if any(p in BRAIN_FILES and s[:1] in ("A", "M") for s, p in paths):
        return "brain-edit"
    if any(p.startswith("scripts/") and s[:1] in ("A", "M") for s, p in paths):
        return "driver"
    if any(s[:1] == "R" and p.startswith("knowledge-base/") for s, p in paths):
        return "kb-structure"
    for s, p in paths:
        if p.startswith("docs/wishlist/") and s[:1] in ("A", "M") and _STATUS_DONE_RE.search(diff_of(p)):
            return "wishlist-done"
    return None


def infer_why(subject: str, body: str, files: Sequence[str] = ()) -> Why:
    m = _PATTERN_RE.search(subject) or _PATTERN_RE.search(body)
    if m:
        return Why("pattern", f"pattern-{m.group(1)}", inferred=True)
    if "wishlist —" in subject or "wishlist —" in body:
        for text in (*files, subject, body):
            slug = _WISHLIST_PATH_RE.search(text)
            if slug:
                return Why("wishlist", slug.group(1), inferred=True)
    return Why("unknown", "", inferred=True)


def _why_from_trailer(value: str) -> Why:
    for t in sorted(WHY_TYPES - {"unknown"}, key=len, reverse=True):
        if value == t or value.startswith((f"{t}-", f"{t}:")):
            return Why(t, value, inferred=False)
    return Why("unknown", value, inferred=False)


def _summary(subject: str, trailers: dict[str, str]) -> str:
    text = trailers.get("summary") or _PREFIX_RE.sub("", subject, count=1)
    return text.strip()[:SUMMARY_MAX]


def _git_raw(vault: Path, args: Sequence[str], stdin: bytes | None = None) -> bytes | None:
    """The one git runner: stdout bytes, or None on a non-zero exit, a timeout or no git. ``stdin`` is fed to git
    (``git apply`` reading a patch); without it git reads nothing."""
    # Never take the index lock: a read must not make a concurrent run's `git add` fail.
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        proc = subprocess.run(
            ["git", "-C", str(vault), "-c", "core.quotepath=false", *args],
            input=stdin if stdin is not None else b"",
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_S,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):  # SubprocessError: the timeout
        return None
    return proc.stdout if proc.returncode == 0 else None


def _git(vault: Path, *args: str) -> str | None:
    raw = _git_raw(vault, args)
    if raw is None:
        return None
    # What text mode gave before the bytes runner existed: UTF-8, bad bytes replaced, universal newlines.
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _git_bytes(vault: Path, *args: str, stdin: bytes | None = None) -> bytes | None:
    """:func:`_git` without decoding: file content (``git show <rev>:<path>``) byte for byte."""
    return _git_raw(vault, args, stdin)


_Commit = tuple[str, datetime, str, str, list[tuple[str, str]], list[tuple[str, str]]]


def name_status(vault: Path, sha: str) -> list[tuple[str, tuple[str, ...]]] | None:
    """What commit ``sha`` changed against its first parent (a root commit: everything it added), renames detected:
    ``(status, (path,))``, or ``(status, (old, new))`` for a rename. None when ``sha`` is not a full hex sha, git
    can't read it, or the output doesn't parse. Paths round-trip any bytes (``os.fsdecode``)."""
    if not isinstance(sha, str) or not _FULL_SHA_RE.match(sha):
        return None
    raw = _git_bytes(vault, "diff-tree", "-r", "-z", "-M", "--name-status", "--no-commit-id", "--root", sha, "--")
    if raw is None:
        return None
    if not raw:
        return []  # a commit that changed nothing
    fields = os.fsdecode(raw.removesuffix(b"\0")).split("\0")
    out: list[tuple[str, tuple[str, ...]]] = []
    i = 0
    while i < len(fields):
        status = fields[i]
        width = 2 if status[:1] in ("R", "C") else 1
        sides = tuple(fields[i + 1 : i + 1 + width])
        if not status or len(sides) != width or not all(sides):
            return None
        out.append((status, sides))
        i += 1 + width
    return out


def _parse_log(raw: str) -> list[_Commit]:
    """``(sha, ts, subject, body, paths, renames)`` per commit: ``paths`` is ``(status, path)`` with a rename's new
    side, ``renames`` is ``(old, new)`` for each rename."""
    commits = []
    for rec in raw.split(_RS):
        if not rec.strip():
            continue
        parts = rec.split(_US, 4)
        if len(parts) < 5:
            continue
        sha, date, subject, body, rest = parts
        try:
            ts = datetime.fromisoformat(date)
        except ValueError:
            continue
        paths = []
        renames = []
        for line in rest.splitlines():
            cols = line.split("\t")
            if len(cols) >= 2 and cols[0]:
                paths.append((cols[0], cols[-1]))
                if cols[0][:1] == "R" and len(cols) == 3:
                    renames.append((cols[1], cols[2]))
        commits.append((sha, ts, subject, body, paths, renames))
    return commits


def _applies_governance(renames: Sequence[tuple[str, str]]) -> bool:
    """The commit moves ``governance-pending/<slug>.md`` to ``governance-pending/applied/<slug>.md``: what
    ``scoutctl changes apply`` does to a governance item the owner approved. A trailer alone never makes a change
    governance (a run could claim it to make any edit immune to rollback)."""
    for old, new in renames:
        m = _PENDING_MD_RE.match(old)
        if m and new == f"governance-pending/applied/{m.group(1)}.md":
            return True
    return False


def scan(vault: Path, *, since: datetime | None, backfilled: bool = False) -> list[Change]:
    """Record the changes in commits since ``since`` (all history when None). Leaves the watermark alone: a
    narrow explicit window must not mark the commits before it as read."""
    revs = [f"--since={since.isoformat()}"] if since is not None else []
    return _record(vault, revs, backfilled=backfilled) or []


def scan_new(vault: Path, *, now: datetime) -> list[Change]:
    """Record the changes in commits made since the last ``scan_new``: those reachable from HEAD but not from the
    watermark commit, or the last :data:`FALLBACK_WINDOW` when there is no usable mark. The mark moves to the HEAD
    read only once the log was read, so a long gap between runs (gates, budget, a lock) loses nothing and a failed
    read retries the same range. Idempotent by sha either way."""
    head = (_git(vault, "rev-parse", "--verify", "--quiet", "HEAD^{commit}") or "").strip()
    if not head:
        return []
    mark = read_scan_mark(vault)
    if mark is not None and _git(vault, "cat-file", "-e", f"{mark}^{{commit}}") is not None:
        revs = [f"{mark}..{head}"]
    else:
        revs = [f"--since={(now - FALLBACK_WINDOW).isoformat()}", head]
    new = _record(vault, revs, backfilled=False)
    if new is None:
        return []
    write_scan_mark(vault, head, now)
    return new


def _reverts_of(trailers: dict[str, str], body: str) -> str | None:
    value = trailers.get("reverts", "")
    if CHANGE_ID_RE.match(value):
        return value
    m = _REVERTS_BODY_RE.search(body)
    return f"chg-{m.group(1)[:12]}" if m else None


# How deep :func:`_rolls_back` follows a chain of rollbacks of rollbacks; past it the commit is an ordinary edit.
_ROLLBACK_DEPTH = 8


def _rolls_back(vault: Path, sha: str, trailers: dict[str, str], body: str, *, depth: int = 0) -> str | None:
    """The change id commit ``sha`` undid, or None. The candidate comes from its ``Scout-Change-Reverts:`` trailer or
    a ``git revert`` body; it counts only when git resolves it to one commit, ``sha`` really put that commit's own
    lines back and touched no other self-change path (scout.changes.actions.restores), and that commit is not itself a
    rollback: undoing a rollback puts the change it undid back, which is an ordinary edit, tested and undoable. A
    chain deeper than :data:`_ROLLBACK_DEPTH`, or a message git can't read, is never a rollback."""
    from scout.changes.actions import restores  # actions imports this module

    cid = _reverts_of(trailers, body)
    if cid is None:
        return None
    m = _REVERTS_BODY_RE.search(body)
    full = m.group(1) if m and m.group(1).startswith(cid[4:]) else None
    if full is None:
        found = (
            _git(vault, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{cid[4:]}^{{commit}}") or ""
        ).strip()
        full = found if _FULL_SHA_RE.match(found) and found.startswith(cid[4:]) else None
    if full is None or depth >= _ROLLBACK_DEPTH or not restores(vault, full, sha):
        return None
    target_body = _git(vault, "log", "-1", "--format=%b", full, "--")
    if target_body is None:
        return None
    target_trailers = parse_trailers(target_body)
    if "reverts" in target_trailers or _REVERTS_BODY_RE.search(target_body):
        if _rolls_back(vault, full, target_trailers, target_body, depth=depth + 1) is not None:
            return None
    return cid


def _change_from_commit(
    vault: Path,
    sha: str,
    ts: datetime,
    subject: str,
    body: str,
    paths: list[tuple[str, str]],
    renames: Sequence[tuple[str, str]],
    *,
    backfilled: bool,
) -> Change | None:
    """The row the scanner writes for one commit, or None when it is not a self-change."""
    lane = lane_of(subject)
    if lane is None:
        return None

    def diff_of(path: str) -> str:
        return _git(vault, "show", "--format=", sha, "--", path) or ""

    trailers = parse_trailers(body)
    reverts = (
        _rolls_back(vault, sha, trailers, body) if "reverts" in trailers or _REVERTS_BODY_RE.search(body) else None
    )
    kind = classify(paths, subject, diff_of, rollback=reverts is not None)
    if kind is None:
        return None
    files = tuple(p for _, p in paths)
    why = _why_from_trailer(trailers["why"]) if "why" in trailers else infer_why(subject, body, files)
    if "targets" in trailers:
        targets = tuple(t.strip() for t in trailers["targets"].split(",") if t.strip())
    else:
        targets = (why.ref,) if why.type == "pattern" else ()
    return Change(
        id=f"chg-{sha[:12]}",
        ts=ts,
        kind=kind,
        summary=_summary(subject, trailers),
        why=why,
        commit=sha,
        files=files,
        targets=targets,
        governance=_applies_governance(renames),
        author_lane=lane,
        backfilled=backfilled,
        reverts=reverts if kind == "rollback" else None,
    )


_LOG_FORMAT = f"--format={_RS}%H{_US}%aI{_US}%s{_US}%b{_US}"


def derive(vault: Path, shas: Sequence[str]) -> dict[str, Change | None] | None:
    """What the scanner would write now for each commit: a row, or None when the commit is not a self-change or is
    missing. None for the whole call when git can't be read. Anything that is not a full hex sha maps to None
    without reaching git, so a forged ledger value is never an option or a revision expression."""
    out: dict[str, Change | None] = {}
    valid = [s for s in dict.fromkeys(shas) if isinstance(s, str) and _FULL_SHA_RE.match(s)]
    out.update({s: None for s in dict.fromkeys(shas) if isinstance(s, str)})
    if not valid:
        return out
    for i in range(0, len(valid), DERIVE_CHUNK):
        raw = _git(
            vault,
            "log",
            "--no-walk=unsorted",
            "--ignore-missing",
            "--no-merges",
            "-M",
            "--name-status",
            _LOG_FORMAT,
            *valid[i : i + DERIVE_CHUNK],
            "--",
        )
        if raw is None:
            return None
        for sha, ts, subject, body, paths, renames in _parse_log(raw):  # git lists only the shas it was given
            out[sha] = _change_from_commit(vault, sha, ts, subject, body, paths, renames, backfilled=False)
    return out


def _record(vault: Path, revs: list[str], *, backfilled: bool) -> list[Change] | None:
    """Append a row per new change in ``git log <revs>``. None when git couldn't read the log."""
    raw = _git(vault, "log", "--no-merges", "-M", "--name-status", _LOG_FORMAT, *revs, "--")
    if raw is None:
        return None
    known = {c.id for c in read_changes(vault)[0]}
    new: list[Change] = []
    for sha, ts, subject, body, paths, renames in reversed(_parse_log(raw)):
        if f"chg-{sha[:12]}" in known:
            continue
        change = _change_from_commit(vault, sha, ts, subject, body, paths, renames, backfilled=backfilled)
        if change is None:
            continue
        append_change(vault, change)
        known.add(change.id)
        new.append(change)
    return new
