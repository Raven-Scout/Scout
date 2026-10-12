"""Review requests a run must not drop (#404).

The runner pre-fetches the user's open review requests
(``gh search prs --review-requested @me``) into
``.scout-cache/session-context.json`` before the session starts. A request the
session never classifies never becomes an action item, and a team-only request
is the one most often lost. This module lists every pre-fetched request that
today's daily file does not account for, so the action-items phase can resolve
each one before it commits.

A request is accounted for when today's file has it

- in a task's line or sub-lines (the ``- Refs:`` line, usually), as
  ``owner/repo#N`` or as the PR URL, in any section except 🎯 Today's Focus,
  🪵 Run notes, 📥 Inbox, 🧹 Loose ends and 📋 Scout Digest; or
- in a skip line with a reason, normally in the run notes:
  ``- Skipped review request owner/repo#N — <reason>``;

or when the user dismissed its URL (``action-items/dismissed.md``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from scout.action_items.parser import parse_lines

# Sections whose lines say something about a PR without making it a task.
_NOT_PLACEMENT = ("🎯", "🪵", "📥", "🧹", "📋")
_PR_URL = re.compile(r"github\.com/(?P<repo>[^/\s]+/[^/\s]+)/pull/(?P<number>\d+)")
_SKIP_LINE = re.compile(
    r"^\s*[-*]\s+Skipped review request\s+`?(?P<target>[^\s`]+)`?\s+(?:—|–|--?)\s*(?P<reason>.*)$",
    re.IGNORECASE,
)
SKIP_LINE_FORMAT = "- Skipped review request <ref> — <reason>"


class ReviewCheckError(Exception):
    """The check cannot run: the pre-fetched context or today's file is missing or unusable."""


@dataclass(frozen=True)
class ReviewRequest:
    repo: str  # owner/name
    number: int
    title: str
    url: str

    @property
    def ref(self) -> str:
        return f"{self.repo}#{self.number}"

    def mentioned_in(self, text: str) -> bool:
        repo, num = re.escape(self.repo), self.number
        ref = re.compile(rf"(?<![\w./-]){repo}#{num}(?!\d)", re.IGNORECASE)
        url = re.compile(rf"github\.com/{repo}/pull/{num}(?!\d)", re.IGNORECASE)
        return bool(ref.search(text) or url.search(text))


@dataclass(frozen=True)
class ReviewCheck:
    checked: tuple[ReviewRequest, ...]
    unaccounted: tuple[ReviewRequest, ...]


def _entry(raw: object, index: int) -> ReviewRequest:
    if not isinstance(raw, dict):
        raise ReviewCheckError(f"pr_review_requested entry {index} is not an object")
    url = str(raw.get("url") or "")
    from_url = _PR_URL.search(url)
    repository = raw.get("repository")
    repo = repository.get("nameWithOwner") if isinstance(repository, dict) else None
    repo = repo or (from_url.group("repo") if from_url else None)
    number = raw.get("number") or (from_url.group("number") if from_url else None)
    try:
        number = int(number)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = None
    if not repo or not number:
        raise ReviewCheckError(f"pr_review_requested entry {index} names no repository and number")
    return ReviewRequest(
        repo=repo,
        number=number,
        title=str(raw.get("title") or ""),
        url=url or f"https://github.com/{repo}/pull/{number}",
    )


def load_review_requests(context: Path, *, today: str) -> list[ReviewRequest]:
    """The review requests in ``context``, which must have been written ``today`` (YYYY-MM-DD)."""
    try:
        data = json.loads(context.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ReviewCheckError(f"{context} not found; run `scoutctl pre-session data` first") from e
    except (OSError, json.JSONDecodeError) as e:
        raise ReviewCheckError(f"{context} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ReviewCheckError(f"{context} is not a JSON object")
    generated = str(data.get("generated_at") or "")
    if not generated:
        raise ReviewCheckError(f"{context} has no generated_at")
    if generated[:10] != today:
        raise ReviewCheckError(
            f"{context} is from {generated[:10]}, not {today}; run `scoutctl pre-session data` first"
        )
    raw = data.get("pr_review_requested", [])
    if not isinstance(raw, list):
        raise ReviewCheckError(f"{context}: pr_review_requested is not a list")
    return [_entry(entry, i) for i, entry in enumerate(raw, start=1)]


def _unfenced(lines: Iterable[str]) -> list[str]:
    out, fenced = [], False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced:
            out.append(line)
    return out


def _skipped(request: ReviewRequest, lines: Sequence[str]) -> bool:
    for line in lines:
        m = _SKIP_LINE.match(line)
        if m and re.search(r"[A-Za-z]", m.group("reason")):
            target = m.group("target").rstrip(".,;:")
            if target.lower() == request.ref.lower() or request.mentioned_in(target):
                return True
    return False


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def unaccounted(
    requests: Sequence[ReviewRequest], lines: Sequence[str], *, dismissed: Iterable[str] = ()
) -> list[ReviewRequest]:
    """The ``requests`` today's ``lines`` neither place, skip with a reason, nor dismiss."""
    # A skip line is never a placement, whatever section it sits in.
    placed = [
        "\n".join(text for text in [item.raw_line, *item.details] if not _SKIP_LINE.match(text))
        for item in parse_lines(list(lines))
        if item.section and not item.section.startswith(_NOT_PLACEMENT)
    ]
    plain = _unfenced(lines)
    gone = {_norm_url(key) for key in dismissed}
    return [
        r
        for r in requests
        if not any(r.mentioned_in(text) for text in placed) and not _skipped(r, plain) and _norm_url(r.url) not in gone
    ]


def check_review_requests(*, data_dir: Path | None = None, today: str, context: Path | None = None) -> ReviewCheck:
    """Compare the pre-fetched review requests with today's daily file in ``data_dir``."""
    import datetime as _dt

    from scout import paths
    from scout.action_items.dismissed import dismissed_keys
    from scout.scripts.pre_session_data import OUTPUT_FILENAME

    context = context or paths.cache_dir(data_dir) / OUTPUT_FILENAME
    requests = load_review_requests(context, today=today)
    if not requests:
        return ReviewCheck((), ())
    daily = paths.action_items_daily_path(data_dir, date=_dt.date.fromisoformat(today))
    if not daily.is_file():
        raise ReviewCheckError(f"no action-items file for {today} ({daily}); write it first")
    lines = daily.read_text(encoding="utf-8").splitlines()
    missing = unaccounted(requests, lines, dismissed=dismissed_keys(data_dir))
    return ReviewCheck(tuple(requests), tuple(missing))
