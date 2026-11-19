"""The dreaming fact-check pass, computed instead of left to prose.

The pass (``phases/core/kb-management.md``, *Fact-Check Pass*) re-verifies what
recent runs wrote into the KB against a freshly read primary source. Left to the
phase text ("``scoutctl kb delta --since 3d --diff`` finds it"), it ran in 3 of 50
dreaming sessions (KB usage audit, 2026-11-19).

So the runner computes it, as it does for *Past-Due Tasks Step Down*
(:mod:`scout.action_items.past_due`): :func:`fact_check_targets` lists the KB notes
changed in the last :data:`WINDOW_DAYS` days that nobody has re-verified since,
most consequential first, and ``scoutctl pre-session data`` writes them into
``.scout-cache/session-context.json`` (``fact_check_targets``). Before a deep-work
run commits, :func:`check_fact_check` (``scoutctl kb check-fact-check``) holds it to
them: at least :data:`REQUIRED` of them (all, when fewer) carry ``last_verified:``
of today and a ``verified_against:``. A run that could not reach a live source says
so on its session-log row (``fact-check skipped: <reason>``), which the check accepts.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scout import paths

WINDOW_DAYS = 3
MAX_TARGETS = 10
REQUIRED = 2
SKIP_MARK = "fact-check skipped:"
KB = "knowledge-base"
# Records and tooling, not claims: the session log, archive copies, ontology tooling, the review queue.
_NOT_CLAIMS = re.compile(
    r"^knowledge-base/(?:session-log(?:/|\.md$)|ontology/|review-queue(?:/|\.md$)|connector-health\.md$)|/archive/"
)


@dataclass(frozen=True)
class Target:
    path: str
    changed: str  # YYYY-MM-DD, the newest change in the window
    visibility: str

    def to_json(self) -> dict[str, str]:
        return {"path": self.path, "changed": self.changed, "visibility": self.visibility}


def _rank(rel: str) -> int:
    """Most consequential first: a project's front page (status that gates action), then its other
    notes, then people and entities (who owns what), then topics, then the rest (sources last)."""
    parts = rel.split("/")
    if len(parts) == 4 and parts[1] == "projects" and parts[3] == f"{parts[2]}.md":
        return 0
    if len(parts) > 2 and parts[1] == "projects":
        return 1
    if len(parts) > 2 and parts[1] in ("people", "ontology", "personal"):
        return 2
    if len(parts) > 2 and parts[1] == "topics":
        return 3
    return 4 if parts[1:2] != ["sources"] else 5


def verified_on(text: str) -> tuple[dt.date | None, bool]:
    """``(last_verified, has verified_against)`` from a note's frontmatter."""
    from scout.kb.structure import split_page

    fm, _ = split_page(text)
    raw = fm.get("last_verified")
    if isinstance(raw, dt.datetime):
        day: dt.date | None = raw.date()
    elif isinstance(raw, dt.date):
        day = raw
    else:
        try:
            day = dt.date.fromisoformat(str(raw).strip()[:10]) if raw else None
        except ValueError:
            day = None
    against = fm.get("verified_against")
    return day, bool(against)


def _read(vault: Path, rel: str) -> str | None:
    try:
        return (vault / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def fact_check_targets(vault: Path, day: dt.date, reader: str) -> list[Target]:
    """The KB notes changed in the last :data:`WINDOW_DAYS` days and not re-verified since, as the
    reader sees them: most consequential first, then newest change first, at most :data:`MAX_TARGETS`."""
    from scout.kb.privacy import FULL_READERS, reads_any
    from scout.kb.read import visibility_map
    from scout.kb.structure import newest_changes

    if not reads_any(reader):
        return []  # nothing is opened for a reader that sees nothing
    changed = newest_changes(vault, KB, day, since=day - dt.timedelta(days=WINDOW_DAYS))
    candidates: list[tuple[str, dt.date]] = []
    for rel, when in changed.items():
        if not rel.endswith(".md") or _NOT_CLAIMS.search(rel):
            continue
        text = _read(vault, rel)
        if text is None:
            continue  # gone since: nothing left to verify
        last, _ = verified_on(text)
        if last is not None and last >= when:
            continue  # already re-verified after its newest change
        candidates.append((rel, when))
    if not candidates:
        return []
    labels = visibility_map(vault, reader, [f"{KB}/**"], who="fact-check")
    full = reader in FULL_READERS  # a label that couldn't be read is private (visibility_map fails closed)
    rows = [
        Target(rel, when.isoformat(), labels.get(rel, "private")) for rel, when in candidates if full or rel in labels
    ]
    rows.sort(key=lambda t: (_rank(t.path), -dt.date.fromisoformat(t.changed).toordinal(), t.path))
    return rows[:MAX_TARGETS]


# ----- the pre-commit check --------------------------------------------------------------


@dataclass
class FactCheck:
    targets: list[Target]
    verified: list[str] = field(default_factory=list)
    required: int = 0
    skipped: str | None = None  # the reason on today's session-log row, if the run gave one

    @property
    def passed(self) -> bool:
        return self.skipped is not None or len(self.verified) >= self.required


def _rows(raw: object) -> list[Target]:
    from scout.action_items.prefetched import PrefetchCheckError

    if raw is None:
        raise PrefetchCheckError("the runner could not compute fact_check_targets")
    if not isinstance(raw, list):
        raise PrefetchCheckError("fact_check_targets is not a list")
    out = []
    for row in raw:
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise PrefetchCheckError("a fact_check_targets row has no path")
        out.append(Target(str(row["path"]), str(row.get("changed") or ""), str(row.get("visibility") or "private")))
    return out


def skip_reason(vault: Path, today: str) -> str | None:
    """The reason on today's session-log row, when it says ``fact-check skipped: <reason>``."""
    shard = vault / KB / "session-log" / f"{today[:7]}.md"
    text = _read(vault, shard.relative_to(vault).as_posix())
    if not text:
        return None
    for line in text.splitlines():
        if today in line and SKIP_MARK in line.casefold():
            reason = line.casefold().split(SKIP_MARK, 1)[1].split("|", 1)[0].strip()
            return reason or "no reason given"
    return None


def judge(targets: Sequence[Target], notes: Mapping[str, str | None], today: dt.date) -> FactCheck:
    """Which targets now carry today's ``last_verified`` and a ``verified_against``."""
    verified = []
    for t in targets:
        text = notes.get(t.path)
        if text is None:
            continue
        last, against = verified_on(text)
        if last is not None and last >= today and against:
            verified.append(t.path)
    return FactCheck(list(targets), verified, min(REQUIRED, len(targets)))


def check_fact_check(*, data_dir: Path | None = None, today: str, context: Path | None = None) -> FactCheck:
    """Compare the vault's notes with the pre-fetched ``fact_check_targets``."""
    from scout.action_items.prefetched import context_path, load_context

    data: dict[str, Any] = load_context(context_path(data_dir, context), today=today)
    targets = _rows(data.get("fact_check_targets"))
    vault = paths.data_dir(data_dir)
    result = judge(targets, {t.path: _read(vault, t.path) for t in targets}, dt.date.fromisoformat(today))
    if not result.passed:
        result.skipped = skip_reason(vault, today)
    return result
