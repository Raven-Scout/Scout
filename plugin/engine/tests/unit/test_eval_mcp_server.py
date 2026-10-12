"""The stdio MCP stub server (spec 2026-10-12 §5.4), driven over real pipes."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[2]


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[subprocess.Popen[str], Path]]:
    (tmp_path / "catalog.yaml").write_text(
        "tools:\n"
        "  - name: slack_read_thread\n"
        "    description: Read a thread.\n"
        "    inputSchema: {type: object, properties: {channel: {type: string}}}\n"
    )
    (tmp_path / "connectors.yaml").write_text(
        "slack:\n  slack_read_thread:\n    - times: 1\n      error: auth_expired\n    - respond: {messages: [hi]}\n"
    )
    calls = tmp_path / "calls.jsonl"
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.eval.harness.stubs.mcp_server", "--persona", "slack",
         "--script", str(tmp_path / "connectors.yaml"), "--catalog", str(tmp_path / "catalog.yaml"),
         "--calls", str(calls), "--today", "2026-10-14"],
        cwd=ENGINE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    yield proc, calls
    proc.kill()
    proc.wait()


def _rpc(proc: subprocess.Popen[str], msg: dict | str) -> dict | None:
    assert proc.stdin and proc.stdout
    proc.stdin.write((msg if isinstance(msg, str) else json.dumps(msg)) + "\n")
    proc.stdin.flush()
    if isinstance(msg, dict) and "id" not in msg:
        return None
    return json.loads(proc.stdout.readline())


def test_initialize_echoes_protocol_and_advertises_tools(server) -> None:
    proc, _ = server
    params = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}
    r = _rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})
    assert r["id"] == 1
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert r["result"]["capabilities"] == {"tools": {}}


def test_tools_list_serves_the_catalog(server) -> None:
    proc, _ = server
    r = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert [t["name"] for t in r["result"]["tools"]] == ["slack_read_thread"]
    assert r["result"]["tools"][0]["inputSchema"]["type"] == "object"


def test_tools_call_follows_the_script_and_logs_every_call(server) -> None:
    proc, calls = server
    _rpc(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    call = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "slack_read_thread", "arguments": {"channel": "C1"}},
    }
    first = _rpc(proc, {**call, "id": 3})["result"]
    second = _rpc(proc, {**call, "id": 4})["result"]
    assert first["isError"] is True and "expired" in first["content"][0]["text"]
    assert second["isError"] is False and json.loads(second["content"][0]["text"]) == {"messages": ["hi"]}
    rows = [json.loads(line) for line in calls.read_text().splitlines()]
    assert [(r["persona"], r["tool"], r["kind"], r["rule"]) for r in rows] == [
        ("slack", "slack_read_thread", "error", "slack:slack_read_thread:0"),
        ("slack", "slack_read_thread", "ok", "slack:slack_read_thread:1"),
    ]
    assert rows[0]["args"] == {"channel": "C1"}


def test_unstubbed_tool_answers_an_error_and_is_logged(server) -> None:
    proc, calls = server
    r = _rpc(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
    assert r["result"]["isError"] is True
    assert json.loads(calls.read_text())["kind"] == "unstubbed"


def test_ping_unknown_method_and_garbage(server) -> None:
    proc, _ = server
    assert _rpc(proc, {"jsonrpc": "2.0", "id": 6, "method": "ping"})["result"] == {}
    assert _rpc(proc, {"jsonrpc": "2.0", "id": 7, "method": "resources/list"})["error"]["code"] == -32601
    assert _rpc(proc, "{not json")["error"]["code"] == -32700
    assert _rpc(proc, {"jsonrpc": "2.0", "id": 8, "method": "ping"})["id"] == 8


def test_a_request_that_breaks_the_handler_gets_an_error_and_the_server_keeps_serving(server) -> None:
    proc, _ = server
    r = _rpc(proc, {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": [1]})
    assert r["error"]["code"] == -32603
    assert _rpc(proc, {"jsonrpc": "2.0", "id": 10, "method": "ping"})["result"] == {}
