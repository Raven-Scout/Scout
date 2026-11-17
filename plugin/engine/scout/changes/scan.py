"""Git scanner: turns self-change commits in a Scout vault repo into ``Change`` rows, idempotently by commit SHA.

Only the commit subject-derived summary, vault-relative paths, the sha and the why are recorded; never diff
content. A non-repo or any git failure means "no new changes", never an exception."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from scout.changes.models import SUMMARY_MAX, WHY_TYPES, Change, Why
from scout.changes.store import append_change, read_changes

BRAIN_FILES = frozenset({"SKILL.md", "DREAMING.md", "RESEARCH.md"})
_PHASE_LANES = ("briefing", "consolidation", "dreaming", "research", "work")
_UPDATE_PREFIXES = ("scout-update:", "upgrade", "chore(scout-update)")
_PREFIX_RE = re.compile(r"^(?:(?:briefing|consolidation|dreaming|research|work|trading) \[[^\]]*\]|scout):\s*")
_PATTERN_RE = re.compile(r"Pattern\s+#(\d+)", re.IGNORECASE)
_WISHLIST_PATH_RE = re.compile(r"docs/wishlist/([A-Za-z0-9._-]+?)(?:\.md)?(?=\s|$|[,;:)\]])")
_STATUS_DONE_RE = re.compile(r"^\+\s*status:\s*done\b", re.MULTILINE)
_RS, _US = "\x1e", "\x1f"


def parse_trailers(message: str) -> dict[str, str]:
    """Reads the ``Scout-Change-*`` trailers (keys without the prefix, lowercased) from a commit message."""
    out: dict[str, str] = {}
    for line in message.splitlines():
        m = re.match(r"^Scout-Change-(Why|Summary|Targets|Governance):\s*(.*?)\s*$", line)
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


def classify(paths: list[tuple[str, str]], subject: str, diff_of: Callable[[str], str]) -> str | None:
    if lane_of(subject) == "update":
        return "upgrade"
    if subject.startswith("Revert "):
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
    try:
        proc = subprocess.run(
            ["git", "-C", str(vault), "-c", "core.quotepath=false", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
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
    args = ["log", "--no-merges", "-M", "--name-status", f"--format={_RS}%H{_US}%aI{_US}%s{_US}%b{_US}"]
    if since is not None:
        args.append(f"--since={since.isoformat()}")
    raw = _git(vault, *args)
    if not raw:
        return []
    known = {c.id for c in read_changes(vault)[0]}
    new: list[Change] = []
    for sha, ts, subject, body, paths in reversed(_parse_log(raw)):
        change_id = f"chg-{sha[:12]}"
        lane = lane_of(subject)
        if lane is None or change_id in known:
            continue

        def diff_of(path: str, _sha: str = sha) -> str:
            return _git(vault, "show", "--format=", _sha, "--", path) or ""

        kind = classify(paths, subject, diff_of)
        if kind is None:
            continue
        trailers = parse_trailers(body)
        files = tuple(p for _, p in paths)
        why = _why_from_trailer(trailers["why"]) if "why" in trailers else infer_why(subject, body, files)
        if "targets" in trailers:
            targets = tuple(t.strip() for t in trailers["targets"].split(",") if t.strip())
        else:
            targets = (why.ref,) if why.type == "pattern" else ()
        change = Change(
            id=change_id,
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
        )
        append_change(vault, change)
        known.add(change_id)
        new.append(change)
    return new
