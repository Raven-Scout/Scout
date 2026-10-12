"""The scripted-connector rule engine (spec 2026-10-12 §5.4)."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from tests.eval.harness.stubs.rules import Script, ScriptError

TODAY = date(2026, 10, 14)


def _script(tmp_path: Path, text: str, files: dict[str, str] | None = None) -> Script:
    for rel, body in (files or {}).items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    path = tmp_path / "connectors.yaml"
    path.write_text(text)
    return Script.load(path, today=TODAY)


def _never(_rule: str) -> int:
    return 0


def test_first_matching_rule_wins_by_regex_on_each_field(tmp_path: Path) -> None:
    s = _script(
        tmp_path,
        """
slack:
  slack_search_public_and_private:
    - match: {query: "to:@alex"}
      respond: {messages: [dm]}
    - respond: {messages: []}
""",
    )
    hit = s.reply("slack", "slack_search_public_and_private", {"query": "is:dm to:@alex"}, prior_hits=_never)
    assert (hit.kind, hit.payload, hit.rule) == ("ok", {"messages": ["dm"]}, "slack:slack_search_public_and_private:0")
    miss = s.reply("slack", "slack_search_public_and_private", {"query": "in:#general"}, prior_hits=_never)
    assert (miss.payload, miss.rule) == ({"messages": []}, "slack:slack_search_public_and_private:1")


def test_non_string_args_are_matched_as_json(tmp_path: Path) -> None:
    s = _script(
        tmp_path,
        "linear:\n  list_issues:\n"
        "    - match: {filter: '\"assignee\": \"me\"', limit: '^5$'}\n"
        "      respond: {issues: [1]}\n",
    )
    args = {"filter": {"assignee": "me"}, "limit": 5}
    assert s.reply("linear", "list_issues", args, prior_hits=_never).payload == {"issues": [1]}
    assert s.reply("linear", "list_issues", {"limit": 5}, prior_hits=_never).kind == "unstubbed"


def test_persona_wide_star_rule_answers_every_tool(tmp_path: Path) -> None:
    s = _script(tmp_path, "linear:\n  '*':\n    - error: auth_expired\n")
    r = s.reply("linear", "list_teams", {}, prior_hits=_never)
    assert (r.kind, r.rule) == ("error", "linear:*:0")
    assert "expired" in r.payload


def test_tool_rules_are_tried_before_the_star_rule(tmp_path: Path) -> None:
    s = _script(tmp_path, "linear:\n  list_teams:\n    - respond: {teams: []}\n  '*':\n    - error: unavailable\n")
    assert s.reply("linear", "list_teams", {}, prior_hits=_never).kind == "ok"
    assert s.reply("linear", "list_issues", {}, prior_hits=_never).kind == "error"


def test_times_limits_a_rule_then_falls_through(tmp_path: Path) -> None:
    s = _script(
        tmp_path, "slack:\n  slack_read_thread:\n    - times: 1\n      error: unavailable\n    - respond: {ok: true}\n"
    )
    hits: Counter[str] = Counter()

    def call() -> str:
        r = s.reply("slack", "slack_read_thread", {}, prior_hits=lambda rule: hits[rule])
        hits[r.rule or ""] += 1
        return r.kind

    assert [call(), call(), call()] == ["error", "ok", "ok"]


def test_file_response_is_read_relative_to_the_script_and_date_rendered(tmp_path: Path) -> None:
    s = _script(
        tmp_path,
        "calendar:\n  gcal_list_events:\n    - respond: fixtures/events.json\n",
        files={"fixtures/events.json": json.dumps({"events": [{"start": "{{d+1}}T09:00:00Z"}]})},
    )
    r = s.reply("calendar", "gcal_list_events", {}, prior_hits=_never)
    assert r.payload == {"events": [{"start": "2026-10-15T09:00:00Z"}]}


def test_capture_is_recorded_and_returns_ok(tmp_path: Path) -> None:
    s = _script(tmp_path, "slack:\n  slack_send_message:\n    - capture: wrap\n")
    r = s.reply("slack", "slack_send_message", {"text": "hi"}, prior_hits=_never)
    assert (r.kind, r.capture) == ("ok", "wrap")


def test_unknown_tool_and_unknown_persona_are_unstubbed(tmp_path: Path) -> None:
    s = _script(tmp_path, "slack:\n  slack_search:\n    - respond: {}\n")
    assert s.reply("slack", "nope", {}, prior_hits=_never).kind == "unstubbed"
    assert s.reply("email", "gmail_search_messages", {}, prior_hits=_never).kind == "unstubbed"


def test_a_rule_without_an_outcome_is_rejected_at_load(tmp_path: Path) -> None:
    with pytest.raises(ScriptError, match="slack:x:0"):
        _script(tmp_path, "slack:\n  x:\n    - match: {a: b}\n")


def test_a_missing_response_file_is_rejected_at_load(tmp_path: Path) -> None:
    with pytest.raises(ScriptError, match="missing.json"):
        _script(tmp_path, "slack:\n  x:\n    - respond: missing.json\n")


def test_an_unknown_rule_key_is_rejected_at_load(tmp_path: Path) -> None:
    with pytest.raises(ScriptError, match=r"slack:x:0.*macth"):
        _script(tmp_path, "slack:\n  x:\n    - macth: {a: b}\n      respond: {}\n")


@pytest.mark.parametrize("bad", ["'1'", "0", "-2", "1.5"])
def test_times_must_be_a_positive_integer(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ScriptError, match="times"):
        _script(tmp_path, f"slack:\n  x:\n    - times: {bad}\n      respond: {{}}\n")


def test_a_response_file_that_is_not_json_is_a_script_error(tmp_path: Path) -> None:
    with pytest.raises(ScriptError, match="bad.json"):
        _script(tmp_path, "slack:\n  x:\n    - respond: bad.json\n", files={"bad.json": "{nope"})
