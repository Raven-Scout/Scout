"""The user's own Linear activity, pre-fetched for briefings (scout.linear_activity).

The fetch reads an optional key from ``~/.scout-secrets/linear-api-key``, asks
Linear for the issues the user touched since the last briefing or
consolidation, and keeps only what the user did. It never raises: no key or any
failure leaves nothing pre-fetched, and ``check-linear-delta`` falls back.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scout import linear_activity as la

NOW = datetime(2026, 4, 15, 7, 0, tzinfo=UTC)
SINCE = datetime(2026, 4, 14, 7, 0, tzinfo=UTC)
ME, PRIYA = "user-alex", "user-priya"


def _at(hours_after_since: float) -> str:
    return (SINCE + timedelta(hours=hours_after_since)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _issue(identifier: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "identifier": identifier,
        "title": f"Title of {identifier}",
        "url": f"https://linear.app/example-org/issue/{identifier}",
        "createdAt": "2026-03-01T09:00:00.000Z",
        "state": {"name": "In Progress"},
        "creator": {"id": PRIYA},
        "history": {"nodes": []},
        "comments": {"nodes": []},
    }
    base.update(fields)
    return base


def _move(at: str, actor: str, frm: str, to: str) -> dict[str, Any]:
    return {"createdAt": at, "actor": {"id": actor}, "fromState": {"name": frm}, "toState": {"name": to}}


def _response(*issues: dict[str, Any], viewer: str = ME) -> dict[str, Any]:
    return {"data": {"viewer": {"id": viewer, "name": "Alex Example"}, "issues": {"nodes": list(issues)}}}


@pytest.fixture
def secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "secrets"
    d.mkdir()
    monkeypatch.setenv("SCOUT_SECRETS_DIR", str(d))
    return d


def _key(secrets: Path, value: str = "lin_api_test", mode: int = 0o600) -> Path:
    path = secrets / la.KEY_FILENAME
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


# ----- the key --------------------------------------------------------------------


def test_no_key_file_is_no_key(secrets: Path) -> None:
    assert la.read_key() is None


def test_the_key_is_read_stripped(secrets: Path) -> None:
    _key(secrets)
    assert la.read_key() == "lin_api_test"


def test_the_default_secrets_dir_is_under_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SCOUT_SECRETS_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert la.secrets_dir() == tmp_path / ".scout-secrets"


def test_a_key_others_can_read_is_refused(secrets: Path) -> None:
    _key(secrets, mode=0o644)
    with pytest.raises(la.LinearFetchError, match="insecure permissions"):
        la.read_key()


def test_an_empty_key_is_refused(secrets: Path) -> None:
    _key(secrets, value="")
    with pytest.raises(la.LinearFetchError, match="empty"):
        la.read_key()


# ----- the window -----------------------------------------------------------------


def _ledger(vault: Path, *rows: dict[str, Any]) -> None:
    logs = vault / ".scout-logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "run-outcomes.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _row(mode: str, started: datetime, exit_code: int = 0) -> dict[str, Any]:
    return {
        "slot": mode,
        "mode": mode,
        "started_at": int(started.timestamp()),
        "finished_at": int(started.timestamp()) + 600,
        "exit_code": exit_code,
    }


def test_without_a_ledger_the_window_is_a_day(tmp_path: Path) -> None:
    assert la.since_last_run(tmp_path, NOW) == NOW - la.DEFAULT_LOOKBACK


def test_the_window_starts_at_the_last_good_briefing_or_consolidation(tmp_path: Path) -> None:
    good = NOW - timedelta(hours=10)
    _ledger(
        tmp_path,
        _row("morning-briefing", NOW - timedelta(hours=30)),
        _row("evening-consolidation", good),
        _row("morning-briefing", NOW - timedelta(hours=5), exit_code=1),  # failed: its scan may not have run
        _row("dreaming", NOW - timedelta(hours=2)),  # dreaming doesn't scan Linear
    )
    assert la.since_last_run(tmp_path, NOW) == good


def test_the_window_is_never_longer_than_a_week(tmp_path: Path) -> None:
    _ledger(tmp_path, _row("morning-briefing", NOW - timedelta(days=30)))
    assert la.since_last_run(tmp_path, NOW) == NOW - la.MAX_LOOKBACK


# ----- what the user did ----------------------------------------------------------


def test_only_the_users_own_actions_after_the_window_count() -> None:
    created = _issue("PROJ-1", creator={"id": ME}, createdAt=_at(1), state={"name": "Todo"})
    moved = _issue(
        "PROJ-2",
        history={"nodes": [_move(_at(2), ME, "In Progress", "In Review"), _move(_at(3), PRIYA, "In Review", "Done")]},
    )
    commented = _issue("OPS-3", comments={"nodes": [{"createdAt": _at(4)}, {"createdAt": _at(5)}]})
    edited = _issue("OPS-4", history={"nodes": [{"createdAt": _at(6), "actor": {"id": ME}}]})
    others = _issue(
        "OPS-5", history={"nodes": [{"createdAt": _at(1), "actor": {"id": PRIYA}, "toState": {"name": "Done"}}]}
    )
    old = _issue(
        "OPS-6",
        creator={"id": ME},
        createdAt=_at(-48),
        comments={"nodes": [{"createdAt": _at(-1)}]},
        history={"nodes": [{"createdAt": _at(-2), "actor": {"id": ME}, "toState": {"name": "Done"}}]},
    )

    rows = la.own_activity(_response(created, moved, commented, edited, others, old), SINCE)

    by_id = {r["identifier"]: r for r in rows}
    assert set(by_id) == {"PROJ-1", "PROJ-2", "OPS-3", "OPS-4"}
    assert by_id["PROJ-1"]["actions"] == ["created"]
    assert by_id["PROJ-2"]["actions"] == ["moved In Progress → In Review"]
    assert by_id["OPS-3"]["actions"] == ["commented (2)"]
    assert by_id["OPS-4"]["actions"] == ["edited"]
    assert [r["identifier"] for r in rows] == ["OPS-4", "OPS-3", "PROJ-2", "PROJ-1"]  # newest action first
    assert by_id["PROJ-2"] == {
        "identifier": "PROJ-2",
        "title": "Title of PROJ-2",
        "url": "https://linear.app/example-org/issue/PROJ-2",
        "state": "In Progress",
        "actions": ["moved In Progress → In Review"],
        "last_at": "2026-04-14T09:00:00Z",
    }


def test_creating_an_issue_is_not_also_an_edit() -> None:
    """Linear writes a history row with the issue's creation; it isn't a separate edit."""
    created = _issue(
        "PROJ-1",
        creator={"id": ME},
        createdAt=_at(1),
        history={"nodes": [{"createdAt": _at(1), "actor": {"id": ME}}]},
    )
    assert la.own_activity(_response(created), SINCE)[0]["actions"] == ["created"]


def test_several_actions_on_one_issue_are_listed_oldest_first() -> None:
    issue = _issue(
        "PROJ-7",
        creator={"id": ME},
        createdAt=_at(1),
        history={"nodes": [_move(_at(2), ME, "Todo", "In Progress")]},
        comments={"nodes": [{"createdAt": _at(3)}]},
    )
    assert la.own_activity(_response(issue), SINCE)[0]["actions"] == [
        "created",
        "moved Todo → In Progress",
        "commented",
    ]


def test_unreadable_timestamps_are_skipped_and_naive_ones_read_as_utc() -> None:
    issue = _issue(
        "PROJ-8",
        history={"nodes": [{"createdAt": "not a date", "actor": {"id": ME}, "toState": {"name": "Done"}}]},
        comments={"nodes": [{"createdAt": None}, {"createdAt": "2026-04-14T09:00:00"}]},
    )
    row = la.own_activity(_response(issue), SINCE)[0]
    assert row["actions"] == ["commented"]
    assert row["last_at"] == "2026-04-14T09:00:00Z"


def test_a_ledger_it_cannot_read_falls_back_to_a_day(
    tmp_path: Path, secrets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: Any) -> datetime:
        raise OSError("ledger unreadable")

    monkeypatch.setattr(la, "since_last_run", broken)
    got = la.fetch_own_activity(tmp_path, now=NOW, post=lambda *_: pytest.fail("no key"))
    assert got.since == "2026-04-14T07:00:00Z" and got.status == la.NO_KEY


def test_a_graphql_error_is_a_fetch_error() -> None:
    with pytest.raises(la.LinearFetchError, match="Cannot query field"):
        la.own_activity({"errors": [{"message": "Cannot query field 'x'"}]}, SINCE)


def test_a_response_without_a_viewer_is_a_fetch_error() -> None:
    with pytest.raises(la.LinearFetchError, match="no viewer"):
        la.own_activity({"data": {"issues": {"nodes": []}}}, SINCE)


# ----- fetch_own_activity ---------------------------------------------------------


def test_without_a_key_nothing_is_fetched(tmp_path: Path, secrets: Path) -> None:
    def never(*_: Any) -> dict[str, Any]:
        raise AssertionError("no key: Linear must not be called")

    got = la.fetch_own_activity(tmp_path, now=NOW, post=never)
    assert got == la.OwnActivity(la.NO_KEY, "2026-04-14T07:00:00Z", None)


def test_with_a_key_it_asks_for_the_window_and_keeps_the_users_actions(tmp_path: Path, secrets: Path) -> None:
    _key(secrets)
    seen: list[tuple[str, dict[str, Any], str]] = []

    def post(url: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        seen.append((url, payload, key))
        return _response(_issue("PROJ-1", creator={"id": ME}, createdAt=_at(1)))

    got = la.fetch_own_activity(tmp_path, now=NOW, post=post)

    assert got.status == la.FETCHED and got.since == "2026-04-14T07:00:00Z" and got.error == ""
    assert [r["identifier"] for r in got.issues or []] == ["PROJ-1"]
    url, payload, key = seen[0]
    assert url == la.API_URL and key == "lin_api_test"
    assert payload["variables"] == {"since": "2026-04-14T07:00:00Z", "first": la.ISSUE_LIMIT}
    assert "viewer" in payload["query"] and "isMe" in payload["query"]


def test_the_endpoint_can_be_moved(tmp_path: Path, secrets: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _key(secrets)
    monkeypatch.setenv("SCOUT_LINEAR_API_URL", "https://linear.example.test/graphql")
    seen: list[str] = []
    la.fetch_own_activity(tmp_path, now=NOW, post=lambda url, *_: seen.append(url) or _response())
    assert seen == ["https://linear.example.test/graphql"]


def test_a_file_url_reads_a_recorded_response(tmp_path: Path, secrets: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """How the eval harness serves a scenario's Linear data without a network."""
    _key(secrets)
    recorded = tmp_path / "linear-graphql.json"
    recorded.write_text(json.dumps(_response(_issue("PROJ-1", creator={"id": ME}, createdAt=_at(1)))), encoding="utf-8")
    monkeypatch.setenv("SCOUT_LINEAR_API_URL", f"file://{recorded}")
    got = la.fetch_own_activity(tmp_path, now=NOW)
    assert got.status == la.FETCHED
    assert [r["identifier"] for r in got.issues or []] == ["PROJ-1"]


@pytest.mark.parametrize(
    ("post_result", "error"),
    [
        (la.LinearFetchError("Linear API returned HTTP 401"), "Linear API returned HTTP 401"),
        (ConnectionError("no route to host"), "ConnectionError: no route to host"),
        ({"errors": [{"message": "Rate limited"}]}, "Linear API error: Rate limited"),
        (["not", "an", "object"], "not a JSON object"),
    ],
)
def test_any_failure_is_reported_never_raised(tmp_path: Path, secrets: Path, post_result: Any, error: str) -> None:
    _key(secrets)

    def post(*_: Any) -> Any:
        if isinstance(post_result, Exception):
            raise post_result
        return post_result

    got = la.fetch_own_activity(tmp_path, now=NOW, post=post)
    assert got.status == la.FAILED and got.issues is None
    assert error in got.error
    assert "lin_api_test" not in got.error


def test_an_unsafe_key_is_a_failure_not_a_call(tmp_path: Path, secrets: Path) -> None:
    _key(secrets, mode=0o644)
    got = la.fetch_own_activity(tmp_path, now=NOW, post=lambda *_: pytest.fail("must not call Linear"))
    assert got.status == la.FAILED and "insecure permissions" in got.error


def test_the_real_post_sends_the_key_and_reads_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    seen: dict[str, Any] = {}

    class Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return _response()

    def fake_post(url: str, **kwargs: Any) -> Resp:
        seen.update(url=url, **kwargs)
        return Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    assert la._post(la.API_URL, {"query": "q"}, "lin_api_test") == _response()
    assert seen["headers"]["Authorization"] == "lin_api_test"
    assert seen["json"] == {"query": "q"} and seen["timeout"] == la.TIMEOUT_S


def test_the_real_post_turns_an_http_error_into_a_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    class Resp:
        status_code = 401

    monkeypatch.setattr(requests, "post", lambda *_a, **_k: Resp())
    with pytest.raises(la.LinearFetchError, match="HTTP 401"):
        la._post(la.API_URL, {}, "lin_api_test")
