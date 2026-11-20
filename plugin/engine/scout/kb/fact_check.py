"""The dreaming fact-check pass, computed instead of left to prose.

The pass (``phases/core/kb-management.md``, *Fact-Check Pass*) re-verifies what
recent runs wrote into the KB against a freshly read primary source. Left to the
phase text ("``scoutctl kb delta --since 3d --diff`` finds it"), most dreaming runs
skipped it.

So the runner computes it, as it does for *Past-Due Tasks Step Down*
(:mod:`scout.action_items.past_due`): :func:`fact_check_targets` lists the KB notes
changed in the last :data:`WINDOW_DAYS` days that nobody has re-verified since,
most consequential first, and ``scoutctl pre-session data`` writes them into
``.scout-cache/session-context.json`` (``fact_check_targets``). Before a deep-work
run commits, :func:`check_fact_check` (``scoutctl kb check-fact-check``) holds it to
them: at least :data:`REQUIRED` of them (all, when fewer) carry ``last_verified:``
of today and a ``verified_against:``. A shared note is never a target: the shared-note
check (#569) refuses a run's added line there, so it couldn't be stamped. A target moved
or archived since, or shared since, counts as done. A run that could not reach a live
source says so on the call itself (``--skipped "<reason>"``), which the check accepts and
echoes for the run's session-log row.
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
# A reason shorter than this says nothing (`--skipped x`).
MIN_REASON = 8
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


def _shared(label: Any) -> bool:
    """A note someone else reads: shared by path, or with an effective ``federation_scope``."""
    return bool(label.shared_file) or label.federation_scope != "none"


def fact_check_targets(vault: Path, day: dt.date, reader: str) -> list[Target]:
    """The KB notes changed in the last :data:`WINDOW_DAYS` days and not re-verified since, as the
    reader sees them and not shared: most consequential first, then newest change first, at most
    :data:`MAX_TARGETS`. Only the candidates are labelled."""
    from scout.kb.privacy import label_file, load_policy, reads_any, visible_to
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
        if last is not None and when <= last <= day:
            continue  # already re-verified after its newest change (a date in the future proves nothing)
        candidates.append((rel, when))
    if not candidates:
        return []
    policy = load_policy(vault)
    rows = []
    for rel, when in candidates:
        label = label_file(vault, rel, policy)
        if visible_to(label, reader, policy) and not _shared(label):
            rows.append(Target(rel, when.isoformat(), label.visibility))
    rows.sort(key=lambda t: (_rank(t.path), -dt.date.fromisoformat(t.changed).toordinal(), t.path))
    return rows[:MAX_TARGETS]


# ----- the pre-commit check --------------------------------------------------------------


@dataclass
class FactCheck:
    targets: list[Target]
    verified: list[str] = field(default_factory=list)
    required: int = 0
    skipped: str | None = None  # the reason the run gave (`--skipped`), when it could reach no live source
    settled: list[str] = field(default_factory=list)  # moved, archived or shared since: nothing to stamp

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


def judge(
    targets: Sequence[Target], notes: Mapping[str, str | None], today: dt.date, settled: Sequence[str] = ()
) -> FactCheck:
    """Which targets now carry today's ``last_verified`` and a ``verified_against``. A target
    whose note is gone (moved or archived) or in ``settled`` (shared since) can't be stamped and
    counts as done; the rest must reach ``min(REQUIRED, len(rest))``."""
    gone = {t.path for t in targets if notes.get(t.path) is None} | set(settled)
    live = [t for t in targets if t.path not in gone]
    verified = []
    for t in live:
        last, against = verified_on(notes[t.path] or "")
        if last == today and against:  # a future date is refused: it would hide the note from every later check
            verified.append(t.path)
    return FactCheck(list(targets), verified, min(REQUIRED, len(live)), settled=sorted(gone))


def check_fact_check(
    *, data_dir: Path | None = None, today: str, context: Path | None = None, skipped: str | None = None
) -> FactCheck:
    """Compare the vault's notes with the pre-fetched ``fact_check_targets``. ``skipped`` is the
    run's reason for checking nothing; it must say something (``MIN_REASON`` characters)."""
    from scout.action_items.prefetched import PrefetchCheckError, context_path, load_context
    from scout.kb.privacy import label_file, load_policy

    if skipped is not None and len(skipped.strip()) < MIN_REASON:
        raise PrefetchCheckError(f"--skipped needs the reason no live source could be reached, not {skipped!r}")
    data: dict[str, Any] = load_context(context_path(data_dir, context), today=today)
    targets = _rows(data.get("fact_check_targets"))
    vault = paths.data_dir(data_dir)
    notes = {t.path: _read(vault, t.path) for t in targets}
    policy = load_policy(vault)
    shared = [t.path for t in targets if notes[t.path] is not None and _shared(label_file(vault, t.path, policy))]
    result = judge(targets, notes, dt.date.fromisoformat(today), shared)
    if not result.passed and skipped is not None:
        result.skipped = skipped.strip()
    return result


# ----- the skip's trace -----------------------------------------------------------------------

SKIP_EVENT = "kb.fact_check_skipped"
# A doctor note when the pass was skipped this often in the last week: one dreaming run a night.
SKIP_NOTE_AT = 3
SKIP_WINDOW_DAYS = 7


def record_skip(vault: Path, day: str, targets: int, mode: str | None) -> None:
    """One event row per accepted ``--skipped``; never the reason's text, which can name a note."""
    from scout.events import append_event

    append_event(
        paths.logs_dir(vault),
        kind=SKIP_EVENT,
        source="cli:kb check-fact-check",
        payload={"date": day, "targets": targets, "mode": mode},
    )


def recent_skips(vault: Path, now: dt.datetime) -> int:
    """Accepted skips in the last :data:`SKIP_WINDOW_DAYS` days, from the event logs."""
    import json

    from scout.events import EVENT_LOG_PREFIX

    logs = paths.logs_dir(vault)
    count = 0
    for back in range(SKIP_WINDOW_DAYS):
        day = (now - dt.timedelta(days=back)).date().isoformat()
        try:
            lines = (logs / f"{EVENT_LOG_PREFIX}{day}.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                count += json.loads(line).get("kind") == SKIP_EVENT
            except (ValueError, AttributeError):
                continue
    return count
