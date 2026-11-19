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
# How far back the first watermark scan reads (no mark yet, or its commit is gone from the repository).
FALLBACK_WINDOW = timedelta(days=14)

BRAIN_FILES = frozenset({"SKILL.md", "DREAMING.md", "RESEARCH.md"})
_PHASE_LANES = ("briefing", "consolidation", "dreaming", "research", "work")
_UPDATE_PREFIXES = ("scout-update:", "upgrade", "chore(scout-update)")
_PREFIX_RE = re.compile(r"^(?:(?:briefing|consolidation|dreaming|research|work|trading) \[[^\]]*\]|scout):\s*")
_PATTERN_RE = re.compile(r"Pattern\s+#(\d+)", re.IGNORECASE)
_WISHLIST_PATH_RE = re.compile(r"docs/wishlist/([A-Za-z0-9._-]+?)(?:\.md)?(?=\s|$|[,;:)\]])")
_STATUS_DONE_RE = re.compile(r"^\+\s*status:\s*done\b", re.MULTILINE)
_REVERTS_BODY_RE = re.compile(r"^This reverts commit ([0-9a-f]{40})", re.MULTILINE)
_FULL_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_RS, _US = "\x1e", "\x1f"


def parse_trailers(message: str) -> dict[str, str]:
    """Reads the ``Scout-Change-*`` trailers (keys without the prefix, lowercased) from a commit message."""
    out: dict[str, str] = {}
    for line in message.splitlines():
        m = re.match(r"^Scout-Change-(Why|Summary|Targets|Governance|Reverts):\s*(.*?)\s*$", line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


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
    trailers: dict[str, str] | None = None,
) -> str | None:
    if lane_of(subject) == "update":
        return "upgrade"
    if subject.startswith("Revert ") or "reverts" in (trailers or {}):
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


def _git(vault: Path, *args: str) -> str | None:
    # Never take the index lock: a read must not make a concurrent run's `git add` fail.
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        proc = subprocess.run(
            ["git", "-C", str(vault), "-c", "core.quotepath=false", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=GIT_TIMEOUT_S,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):  # SubprocessError: the timeout
        return None
    return proc.stdout if proc.returncode == 0 else None


def _parse_log(raw: str) -> list[tuple[str, datetime, str, str, list[tuple[str, str]]]]:
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
        for line in rest.splitlines():
            cols = line.split("\t")
            if len(cols) >= 2 and cols[0]:
                paths.append((cols[0], cols[-1]))
        commits.append((sha, ts, subject, body, paths))
    return commits


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


def _change_from_commit(
    vault: Path,
    sha: str,
    ts: datetime,
    subject: str,
    body: str,
    paths: list[tuple[str, str]],
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
    kind = classify(paths, subject, diff_of, trailers)
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
        governance=trailers.get("governance", "").lower() == "yes",
        author_lane=lane,
        backfilled=backfilled,
        reverts=_reverts_of(trailers, body),
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
    raw = _git(
        vault,
        "log",
        "--no-walk=unsorted",
        "--ignore-missing",
        "--no-merges",
        "-M",
        "--name-status",
        _LOG_FORMAT,
        *valid,
        "--",
    )
    if raw is None:
        return None
    for sha, ts, subject, body, paths in _parse_log(raw):
        if sha in out:
            out[sha] = _change_from_commit(vault, sha, ts, subject, body, paths, backfilled=False)
    return out


def _record(vault: Path, revs: list[str], *, backfilled: bool) -> list[Change] | None:
    """Append a row per new change in ``git log <revs>``. None when git couldn't read the log."""
    raw = _git(vault, "log", "--no-merges", "-M", "--name-status", _LOG_FORMAT, *revs, "--")
    if raw is None:
        return None
    known = {c.id for c in read_changes(vault)[0]}
    new: list[Change] = []
    for sha, ts, subject, body, paths in reversed(_parse_log(raw)):
        if f"chg-{sha[:12]}" in known:
            continue
        change = _change_from_commit(vault, sha, ts, subject, body, paths, backfilled=backfilled)
        if change is None:
            continue
        append_change(vault, change)
        known.add(change.id)
        new.append(change)
    return new
