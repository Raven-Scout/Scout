"""The A8b client's edge answers and the planning helpers' remaining branches (Shared Areas S1b).

No network: :meth:`StopsClient._send` is replaced with canned ``(status, body)``
answers, so each test says exactly what Agnes answered.
"""

from __future__ import annotations

from typing import Any

import pytest

from scout.kb import share_stops as ss
from scout.kb.share import Target

GLOB = "knowledge-base/projects/widget/**"
PRIYA = "person:priya@example.com"
SAM = "person:sam@example.com"
STOP = {"stop_id": "stop0001", "glob": GLOB, "principal": None, "created_at": "2026-11-16T09:30:00Z"}


def client(*answers: tuple[int, Any]) -> ss.StopsClient:
    c = ss.StopsClient("http://agnes.invalid", "x.y.z")
    queue = list(answers)

    def send(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        return queue.pop(0)

    c._send = send  # type: ignore[method-assign]
    return c


def view(**raw: Any) -> ss.SharesView:
    return ss.SharesView.parse(raw)


# ----- the client ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ((409, {"detail": {"code": "repository_busy", "message": "busy"}}), "busy"),
        ((500, {"detail": {"code": "boom", "message": "it broke"}}), "error"),
        ((502, None), "error"),
        ((403, None), "auth"),
        ((404, {"detail": {"code": "knowledge_repository_not_found"}}), "no_repository"),
        ((405, None), "old_agnes"),
        ((200, ["not", "an", "object"]), "error"),
    ],
)
def test_each_answer_to_the_listing_has_a_reason(answer: tuple[int, Any], reason: str) -> None:
    with pytest.raises(ss.StopsUnavailable) as e:
        client(answer).shares()
    assert e.value.reason == reason


def test_a_stop_or_clear_answered_without_its_stop_is_an_error() -> None:
    with pytest.raises(ss.StopsUnavailable, match="without the stop it made"):
        client((201, {"created": True})).stop(GLOB, None)
    with pytest.raises(ss.StopsUnavailable, match="without the stop it cleared"):
        client((200, {})).clear("stop0001")


def test_a_clear_refused_for_the_credential_is_a_refusal_and_other_failures_pass_through() -> None:
    with pytest.raises(ss.StopRefused) as refused:
        client((404, {"detail": {"code": "knowledge_repository_not_found"}})).clear("stop0001")
    assert refused.value.code == "credential_refused"
    with pytest.raises(ss.StopsUnavailable) as e:
        client((401, None)).clear("stop0001")
    assert e.value.reason == "auth"


def test_a_stop_sends_the_person_and_never_a_write_back(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []
    c = ss.StopsClient("http://agnes.invalid", "x.y.z")

    def send(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        sent.append((method, path, body))
        return 201, {"stop": {**STOP, "principal": PRIYA}}

    monkeypatch.setattr(c, "_send", send)
    assert c.stop(GLOB, PRIYA).who() == "priya@example.com"
    assert sent == [("POST", ss.STOP_PATH, {"glob": GLOB, "write_back": False, "person": PRIYA})]


def test_a_body_that_isnt_json_is_read_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    class Resp:
        status_code = 502
        content = b"<html>bad gateway</html>"

        def json(self) -> Any:
            raise ValueError("not json")

    monkeypatch.setattr(requests, "request", lambda *a, **k: Resp())
    assert ss.StopsClient("http://agnes.invalid", "x.y.z")._send("GET", "/x") == (502, None)


# ----- parsing ---------------------------------------------------------------------------------


def test_parsing_skips_junk_rows() -> None:
    v = view(
        shares=[
            "junk",
            {"no": "glob"},
            {"glob": GLOB, "principals": ["junk", {"principal": PRIYA}], "stops": [{"stop_id": 1}, STOP]},
        ],
        stops_not_listed="junk",
        cleared_stops=[STOP, None],
    )
    (share,) = v.shares
    assert [p.principal for p in share.principals] == [PRIYA]
    assert [s.stop_id for s in share.stops] == ["stop0001"]
    assert v.stops_not_listed == () and len(v.cleared_stops) == 1
    assert ss.Stop.parse({"stop_id": "s", "glob": GLOB, "principal": "group:g1"}).who() == "group:g1"  # type: ignore[union-attr]
    assert ss.when("not a time") == "not a time" and ss.when("") == "?"


def test_a_share_admits_a_group_member_but_not_a_group_by_membership() -> None:
    v = view(
        shares=[
            {
                "glob": GLOB,
                "principals": [{"principal": "group:g1", "kind": "group", "members": [{"email": "sam@example.com"}]}],
            }
        ]
    )
    share = v.shares[0]
    assert share.admits(SAM) and not share.admits(PRIYA)
    assert share.group_of("group:g2") is None


# ----- planning --------------------------------------------------------------------------------


def test_a_person_already_stopped_isnt_stopped_again_and_a_broader_share_is_noted() -> None:
    v = view(
        shares=[
            {
                "glob": GLOB,
                "principals": [{"principal": PRIYA}, {"principal": SAM}],
                "stops": [{**STOP, "principal": PRIYA}],
            }
        ]
    )
    wanted, notes = ss.unshare_stops(v, Target(rel="knowledge-base/projects/widget", folder=True), [PRIYA, SAM])
    assert [r.principal for r in wanted] == [SAM]
    _, notes = ss.unshare_stops(v, Target(rel="knowledge-base/projects/widget/design", folder=True), [SAM])
    assert notes and "Agnes stops only a whole share" in notes[0]
    _, notes = ss.unshare_stops(v, Target(rel="knowledge-base/projects/widget/design", folder=True), ["person:x@y.z"])
    assert notes == []  # the broader share doesn't admit them: nothing to say


def test_a_clear_plan_reports_kept_stops_and_the_pending_pull() -> None:
    v = view(
        shares=[{"glob": GLOB, "principals": [{"principal": PRIYA}, {"principal": SAM}], "stops": [STOP]}],
        pending_server_commits=2,
    )
    plan = ss.share_clears(v, Target(rel="knowledge-base/projects/widget", folder=True), [PRIYA])
    assert [s.stop_id for s in plan.clears] == ["stop0001"]
    assert [r.principal for r in plan.keep_stopped] == [SAM]
    data = plan.to_json()
    assert data["available"] and data["keeps_stopped"][0]["principal"] == SAM
    assert any("Agnes has 2 commit(s)" in n for n in plan.notes)
    assert any("keeps it stopped for the people you didn't name" in line for line in plan.lines())


def test_apply_clears_says_when_a_stop_was_already_cleared_or_agnes_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.kb import live_run

    monkeypatch.setattr(live_run, "commit_source_now", lambda v: None)
    pat = "eyJhbGciOiJIUzI1NiJ9.eyJ0eXAiOiJwYXQifQ.c2ln"  # {"typ": "pat"}: a made-up token
    stops = [ss.Stop(f"stop000{n}", GLOB, PRIYA, "cli", "not_requested", "") for n in (1, 2)]
    plan = ss.ClearPlan(clears=stops)
    c = client((404, {"detail": {"code": "share_stop_not_found"}}), (503, None))
    c._token = pat
    lines = ss.apply_clears(__import__("pathlib").Path("/nonexistent-vault"), {}, c, plan)
    assert "was already cleared" in lines[0]
    assert "Couldn't clear the stop" in lines[1] and "still in force" in lines[1]


def test_make_stops_with_nothing_made_and_no_failure_is_nothing() -> None:
    v = view(shares=[{"glob": GLOB, "principals": [{"principal": PRIYA}]}])
    outcome = ss.make_stops(client(), v, None, [], ["a note"], dry_run=False)
    assert outcome.status == "nothing" and outcome.notes == ["a note"]


def test_still_served_skips_a_share_stopped_for_everyone_and_kb_shares_mentions_the_pending_pull() -> None:
    v = view(
        shares=[
            {"glob": GLOB, "principals": [{"principal": PRIYA}], "stops": [STOP]},
            {"glob": "knowledge-base/topics/**", "kind": "note", "principals": [{"principal": SAM}]},
        ],
        pending_server_commits=1,
    )
    assert ss.still_served(v, {}) == []
    lines = ss.stop_lines(v, None, [GLOB])
    assert any("Agnes has 1 commit(s) on top of your last push" in line for line in lines)
    assert ss.stop_lines(None, ss.UNLINKED_NOTE, []) == []
