"""The user's own Linear activity since the last run, pre-fetched for briefings.

The Linear connector phase asks every briefing and consolidation to report what
the user did in Linear since the last run (issues created, moved, commented on,
edited). Left to the session, that query was skipped in every eval sample. So
the runner fetches it before the session starts, like the review requests
``gh`` fetches, and ``scoutctl action-items check-linear-delta`` holds the run
to reporting it.

Linear has no CLI with its own login, and the claude.ai connector's login lives
inside Claude, so this reads an optional personal API key from
``~/.scout-secrets/linear-api-key`` (mode 600, as the Telegram and Slack
secrets). Without a key, or when the call fails, nothing is pre-fetched and the
check falls back to asking the session for the report.

Two paged passes find the issues: the ones the user is assigned to, created,
commented on or subscribed to (complete up to ``MINE_PAGES`` pages), then the
workspace's most recently updated ones (``WORKSPACE_PAGES`` pages), which
catches a move or edit on an issue the user has no other tie to. When the
first pass, or an issue's history or comments, runs past its limit the result
says ``truncated``, and the phase asks the session to look past it.

``SCOUT_SECRETS_DIR`` moves the secrets dir and ``SCOUT_LINEAR_API_URL`` the
endpoint. A ``file://`` URL reads a recorded GraphQL response instead of
calling Linear: the eval harness uses it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

API_URL = "https://api.linear.app/graphql"
KEY_FILENAME = "linear-api-key"
TIMEOUT_S = 10.0
ISSUE_LIMIT = 50  # issues per page
MINE_PAGES = 10  # the user's own issues: past this many pages the result is truncated
WORKSPACE_PAGES = 4  # the workspace's most recently updated issues: a best-effort pass
HISTORY_LIMIT = 40
COMMENT_LIMIT = 20
# With no earlier briefing or consolidation on record, look back this far; never further than MAX_LOOKBACK.
DEFAULT_LOOKBACK = timedelta(hours=24)
MAX_LOOKBACK = timedelta(days=7)
_RUN_MODES = ("briefing", "consolidation")

FETCHED, NO_KEY, FAILED = "fetched", "no_key", "failed"

# One page of issues matching $filter; their history and the viewer's comments are filtered to
# the viewer and the window client-side.
QUERY = f"""
query OwnActivity($filter: IssueFilter!, $first: Int!, $after: String) {{
  viewer {{ id name }}
  issues(first: $first, after: $after, orderBy: updatedAt, filter: $filter) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      identifier
      title
      url
      createdAt
      state {{ name }}
      creator {{ id }}
      history(first: {HISTORY_LIMIT}) {{
        pageInfo {{ hasNextPage }}
        nodes {{ createdAt actor {{ id }} fromState {{ name }} toState {{ name }} }}
      }}
      comments(first: {COMMENT_LIMIT}, filter: {{ user: {{ isMe: {{ eq: true }} }} }}) {{
        pageInfo {{ hasNextPage }}
        nodes {{ createdAt }}
      }}
    }}
  }}
}}
"""

_ME = {"isMe": {"eq": True}}


def filters(window: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The two passes' issue filters: the user's own issues, then any updated in the window."""
    updated: dict[str, Any] = {"updatedAt": {"gt": window}}
    mine = {
        **updated,
        "or": [
            {"assignee": _ME},
            {"creator": _ME},
            {"comments": {"some": {"user": _ME}}},
            {"subscribers": {"some": _ME}},
        ],
    }
    return mine, updated


class LinearFetchError(Exception):
    """The key or the response is unusable. Never carries the key."""


@dataclass(frozen=True)
class OwnActivity:
    status: str  # FETCHED | NO_KEY | FAILED
    since: str  # ISO-8601 UTC, the window's start
    issues: list[dict[str, Any]] | None  # None unless FETCHED
    error: str = ""
    truncated: bool = False  # a limit cut the user's own issues, or one's history or comments


def secrets_dir() -> Path:
    override = os.environ.get("SCOUT_SECRETS_DIR")
    return Path(override) if override else Path.home() / ".scout-secrets"


def read_key() -> str | None:
    """The API key, or None when there is no key file. Raises on an unsafe or empty one."""
    path = secrets_dir() / KEY_FILENAME
    if not path.exists():
        return None
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise LinearFetchError(f"{path} has insecure permissions {oct(mode)}; expected 600 (chmod 600 {path})")
    key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise LinearFetchError(f"{path} is empty")
    return key


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: object) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def since_last_run(vault: Path, now: datetime) -> datetime:
    """When the last successful briefing or consolidation started, within MAX_LOOKBACK of ``now``."""
    from scout.health import read_outcomes

    starts = [
        r.started_at
        for r in read_outcomes(vault)
        if r.outcome == "ok" and any(m in r.mode for m in _RUN_MODES) and r.started_at < now
    ]
    since = max(starts) if starts else now - DEFAULT_LOOKBACK
    return max(since, now - MAX_LOOKBACK)


def _error(errors: object) -> str:
    first = errors[0] if isinstance(errors, list) and errors else errors
    return str(first.get("message", first) if isinstance(first, dict) else first)[:300]


def _post(url: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
    if url.startswith("file://"):
        return json.loads(Path(url[len("file://") :]).read_text(encoding="utf-8"))
    import requests

    resp = requests.post(
        url,
        json=payload,
        headers={"Authorization": key, "Content-Type": "application/json"},
        timeout=TIMEOUT_S,
    )
    if resp.status_code != 200:
        # Linear answers a query it rejects (an unknown field, a bad argument) with HTTP 400 and
        # a GraphQL `errors` body: keep its message.
        try:
            errors = resp.json().get("errors")
        except Exception:
            errors = None
        detail = f": {_error(errors)}" if errors else ""
        raise LinearFetchError(f"Linear API returned HTTP {resp.status_code}{detail}")
    return resp.json()


def _actions(issue: dict[str, Any], viewer: str, since: datetime) -> tuple[list[str], datetime | None]:
    """What ``viewer`` did to ``issue`` after ``since``, oldest first, and when they last did it."""
    events: list[tuple[datetime, str]] = []
    created = _parse(issue.get("createdAt"))
    if (issue.get("creator") or {}).get("id") == viewer and created and created > since:
        events.append((created, "created"))
    edited: datetime | None = None
    for h in (issue.get("history") or {}).get("nodes") or []:
        at = _parse(h.get("createdAt"))
        if not at or at <= since or (h.get("actor") or {}).get("id") != viewer:
            continue
        to = (h.get("toState") or {}).get("name")
        if to:
            frm = (h.get("fromState") or {}).get("name") or "?"
            events.append((at, f"moved {frm} → {to}"))
        elif not (created and abs((at - created).total_seconds()) < 5):  # creation's own history row
            edited = max(edited, at) if edited else at
    if edited:
        events.append((edited, "edited"))
    said = [
        at
        for c in (issue.get("comments") or {}).get("nodes") or []
        if (at := _parse(c.get("createdAt"))) and at > since
    ]
    if said:
        events.append((max(said), "commented" if len(said) == 1 else f"commented ({len(said)})"))
    events.sort(key=lambda e: e[0])
    return [what for _, what in events], (events[-1][0] if events else None)


def _body(data: object) -> tuple[str, dict[str, Any]]:
    """The viewer's id and the ``issues`` connection of one GraphQL response."""
    if not isinstance(data, dict):
        raise LinearFetchError("Linear API response is not a JSON object")
    if data.get("errors"):
        raise LinearFetchError(f"Linear API error: {_error(data['errors'])}")
    body = data.get("data") or {}
    viewer = (body.get("viewer") or {}).get("id")
    if not viewer:
        raise LinearFetchError("Linear API response has no viewer")
    return str(viewer), body.get("issues") or {}


def _clipped(issue: dict[str, Any]) -> bool:
    """Whether a limit cut this issue's history or the viewer's comments on it."""
    return any(((issue.get(k) or {}).get("pageInfo") or {}).get("hasNextPage") for k in ("history", "comments"))


def _rows(viewer: str, issues: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    rows: list[tuple[datetime, dict[str, Any]]] = []
    seen: set[str] = set()
    for issue in issues:
        identifier = str(issue.get("identifier") or "")
        if identifier in seen:
            continue
        seen.add(identifier)
        actions, last = _actions(issue, viewer, since)
        if not actions or last is None:
            continue
        rows.append(
            (
                last,
                {
                    "identifier": identifier,
                    "title": str(issue.get("title") or ""),
                    "url": str(issue.get("url") or ""),
                    "state": str((issue.get("state") or {}).get("name") or ""),
                    "actions": actions,
                    "last_at": _iso(last),
                },
            )
        )
    rows.sort(key=lambda r: r[0], reverse=True)
    return [row for _, row in rows]


def own_activity(data: dict[str, Any], since: datetime) -> list[dict[str, Any]]:
    """The issues the viewer acted on after ``since``, from one GraphQL response; newest action first."""
    viewer, issues = _body(data)
    return _rows(viewer, issues.get("nodes") or [], since)


Post = Callable[[str, dict[str, Any], str], dict[str, Any]]


def _pages(
    post: Post, url: str, key: str, flt: dict[str, Any], max_pages: int
) -> tuple[str, list[dict[str, Any]], bool]:
    """The viewer, every issue matching ``flt`` up to ``max_pages`` pages, and whether more were left."""
    viewer, after = "", None
    nodes: list[dict[str, Any]] = []
    for _ in range(max_pages):
        variables: dict[str, Any] = {"filter": flt, "first": ISSUE_LIMIT}
        if after:
            variables["after"] = after
        viewer, issues = _body(post(url, {"query": QUERY, "variables": variables}, key))
        nodes += issues.get("nodes") or []
        page = issues.get("pageInfo") or {}
        after = page.get("endCursor")
        if not page.get("hasNextPage") or not after:
            return viewer, nodes, False
    return viewer, nodes, True


def fetch_own_activity(vault: Path, *, now: datetime | None = None, post: Post = _post) -> OwnActivity:
    """Fetch the user's own Linear activity since the last run. Never raises."""
    now = now or datetime.now(UTC)
    try:
        since = since_last_run(vault, now)
    except Exception:
        since = now - DEFAULT_LOOKBACK
    window = _iso(since)
    try:
        key = read_key()
        if key is None:
            return OwnActivity(NO_KEY, window, None)
        url = os.environ.get("SCOUT_LINEAR_API_URL") or API_URL
        mine_filter, updated_filter = filters(window)
        viewer, mine, cut = _pages(post, url, key, mine_filter, MINE_PAGES)
        _, others, _ = _pages(post, url, key, updated_filter, WORKSPACE_PAGES)
        truncated = cut or any(_clipped(i) for i in mine)
        return OwnActivity(FETCHED, window, _rows(viewer, mine + others, since), truncated=truncated)
    except LinearFetchError as e:
        return OwnActivity(FAILED, window, None, str(e))
    except Exception as e:  # network, JSON, an unexpected shape: never block the session
        return OwnActivity(FAILED, window, None, f"{type(e).__name__}: {e}"[:300])
