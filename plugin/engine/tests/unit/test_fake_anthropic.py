"""The scripted fake Anthropic endpoint the gate probe points claude at (spec 2026-11-13 §3.5, plan Task 1a.1)."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from scout.agent_runtimes.fake_anthropic import FakeAnthropic, ScriptedTurn

BASH_TOOL = {"name": "Bash", "description": "run a command", "input_schema": {"type": "object"}}
TURN = ScriptedTurn(tool_name="Bash", tool_input={"command": "cat knowledge-base/probe.md"})


@pytest.fixture
def fake() -> Iterator[FakeAnthropic]:
    server = FakeAnthropic([TURN])
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _post(base: str, path: str, body: dict[str, Any]) -> tuple[int, bytes]:
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - loopback test server
        return resp.status, resp.read()


def _sse_events(raw: bytes) -> list[dict[str, Any]]:
    return [json.loads(line[len("data: ") :]) for line in raw.decode().splitlines() if line.startswith("data: ")]


def _first_request(stream: bool) -> dict[str, Any]:
    return {
        "model": "claude-x",
        "max_tokens": 100,
        "stream": stream,
        "tools": [BASH_TOOL],
        "messages": [{"role": "user", "content": "probe"}],
    }


def test_first_request_returns_scripted_tool_use(fake: FakeAnthropic) -> None:
    status, raw = _post(fake.base_url, "/v1/messages", _first_request(stream=False))
    body = json.loads(raw)
    assert status == 200
    assert body["stop_reason"] == "tool_use"
    block = body["content"][0]
    assert block["type"] == "tool_use"
    assert block["name"] == "Bash"
    assert block["input"] == {"command": "cat knowledge-base/probe.md"}
    assert isinstance(block["id"], str) and block["id"]


def test_streaming_request_emits_message_start_and_tool_use_block(fake: FakeAnthropic) -> None:
    _, raw = _post(fake.base_url, "/v1/messages?beta=true", _first_request(stream=True))
    events = _sse_events(raw)
    types = [e["type"] for e in events]
    assert types[0] == "message_start"
    assert types[-1] == "message_stop"
    starts = [e for e in events if e["type"] == "content_block_start"]
    assert starts[0]["content_block"]["type"] == "tool_use"
    assert starts[0]["content_block"]["name"] == "Bash"
    partial = "".join(
        e["delta"]["partial_json"]
        for e in events
        if e["type"] == "content_block_delta" and e["delta"]["type"] == "input_json_delta"
    )
    assert json.loads(partial) == {"command": "cat knowledge-base/probe.md"}
    stop = next(e for e in events if e["type"] == "message_delta")
    assert stop["delta"]["stop_reason"] == "tool_use"


def test_second_request_records_tool_result_and_ends_turn(fake: FakeAnthropic) -> None:
    _, raw = _post(fake.base_url, "/v1/messages", _first_request(stream=False))
    tool_id = json.loads(raw)["content"][0]["id"]
    second = _first_request(stream=False)
    second["messages"] += [
        {"role": "assistant", "content": [json.loads(raw)["content"][0]]},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "is_error": True, "content": "[scout:kb-read-gate] no"}
            ],
        },
    ]
    _, raw2 = _post(fake.base_url, "/v1/messages", second)
    assert json.loads(raw2)["stop_reason"] == "end_turn"
    assert len(fake.tool_results) == 1
    assert fake.tool_results[0]["is_error"] is True
    assert "[scout:kb-read-gate]" in fake.result_text(fake.tool_results[0])


def test_request_without_the_scripted_tool_gets_plain_text(fake: FakeAnthropic) -> None:
    """Side requests (titles, classifiers) never receive the scripted tool call."""
    body = _first_request(stream=False)
    body["tools"] = []
    _, raw = _post(fake.base_url, "/v1/messages", body)
    reply = json.loads(raw)
    assert reply["stop_reason"] == "end_turn"
    assert reply["content"][0]["type"] == "text"
    assert fake.issued == 0


def test_count_tokens_and_unknown_paths_answer(fake: FakeAnthropic) -> None:
    _, raw = _post(fake.base_url, "/v1/messages/count_tokens", {"messages": []})
    assert json.loads(raw)["input_tokens"] >= 0
    status, _ = _post(fake.base_url, "/api/anything", {})
    assert status == 200


def test_result_text_joins_list_content() -> None:
    block = {"type": "tool_result", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    assert FakeAnthropic.result_text(block) == "a\nb"
