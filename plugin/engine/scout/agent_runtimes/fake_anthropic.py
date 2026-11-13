"""A scripted fake of Anthropic's Messages API on 127.0.0.1, for the gate probe (spec 2026-11-13 §3.5, §11.2).

Pointing ``claude`` at it with ``ANTHROPIC_BASE_URL`` makes a session run a fixed
script with no model and no spend: the first request that offers the scripted
tool gets that tool call back, every later request (or one that doesn't offer the
tool, such as a title or classifier request) gets a one-word text answer, and every
``tool_result`` the client sends for a scripted call is recorded, so the probe can
see whether Scout's hook denied it.

Stdlib only: the probe runs before every scheduled run on the owner's Mac.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, NamedTuple


class ScriptedTurn(NamedTuple):
    tool_name: str
    tool_input: dict[str, Any]


class FakeAnthropic:
    """The server. ``start()`` returns the base URL to put in ``ANTHROPIC_BASE_URL``."""

    def __init__(self, script: Iterable[ScriptedTurn]) -> None:
        self._script = list(script)
        self._issued_ids: list[str] = []
        self._results: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.base_url = ""

    # -- lifecycle -----------------------------------------------------------------

    def start(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
                return

            def do_GET(self) -> None:  # noqa: N802 - stdlib name
                self._send_json({})

            def do_HEAD(self) -> None:  # noqa: N802
                self.send_response(200)
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = {}
                path = self.path.split("?", 1)[0]
                if path.endswith("/v1/messages/count_tokens"):
                    self._send_json({"input_tokens": 1})
                    return
                if not path.endswith("/v1/messages") or not isinstance(body, dict):
                    self._send_json({})
                    return
                content, stop = fake._reply(body)
                if body.get("stream"):
                    self._send_sse(body, content, stop)
                else:
                    self._send_json(fake._message(body, content, stop))

            def _send_json(self, payload: Mapping[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_sse(self, body: Mapping[str, Any], content: list[dict[str, Any]], stop: str) -> None:
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-cache")
                self.end_headers()
                for event in fake._events(body, content, stop):
                    chunk = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
                    self.wfile.write(chunk)
                self.wfile.flush()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="fake-anthropic", daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        return self.base_url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # -- what the probe reads ------------------------------------------------------

    @property
    def issued(self) -> int:
        """How many scripted tool calls were handed out."""
        with self._lock:
            return len(self._issued_ids)

    @property
    def tool_results(self) -> list[dict[str, Any]]:
        """The client's ``tool_result`` block for each scripted call it answered, in script order."""
        with self._lock:
            return [self._results[i] for i in self._issued_ids if i in self._results]

    @staticmethod
    def result_text(block: Mapping[str, Any]) -> str:
        content = block.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
        return ""

    # -- the script ----------------------------------------------------------------

    def _reply(self, body: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
        offered = {t.get("name") for t in body.get("tools") or [] if isinstance(t, dict)}
        with self._lock:
            self._record_results(body.get("messages") or [])
            n = len(self._issued_ids)
            pending = n > 0 and self._issued_ids[-1] not in self._results
            if n < len(self._script) and not pending and self._script[n].tool_name in offered:
                turn = self._script[n]
                tool_id = f"toolu_probe_{uuid.uuid4().hex[:16]}"
                self._issued_ids.append(tool_id)
                return [{"type": "tool_use", "id": tool_id, "name": turn.tool_name, "input": turn.tool_input}], "tool_use"
        return [{"type": "text", "text": "done"}], "end_turn"

    def _record_results(self, messages: Iterable[Any]) -> None:
        issued = set(self._issued_ids)
        for msg in messages:
            if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
                continue
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id")
                    if tid in issued and tid not in self._results:
                        self._results[tid] = block

    @staticmethod
    def _message(body: Mapping[str, Any], content: list[dict[str, Any]], stop: str) -> dict[str, Any]:
        return {
            "id": f"msg_probe_{uuid.uuid4().hex[:16]}",
            "type": "message",
            "role": "assistant",
            "model": body.get("model") or "claude-probe",
            "content": content,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    def _events(self, body: Mapping[str, Any], content: list[dict[str, Any]], stop: str) -> list[dict[str, Any]]:
        start = self._message(body, [], stop)
        start["stop_reason"] = None
        events: list[dict[str, Any]] = [{"type": "message_start", "message": start}]
        for i, block in enumerate(content):
            if block["type"] == "tool_use":
                events.append({"type": "content_block_start", "index": i, "content_block": {**block, "input": {}}})
                delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
            else:
                events.append({"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
                delta = {"type": "text_delta", "text": block["text"]}
            events.append({"type": "content_block_delta", "index": i, "delta": delta})
            events.append({"type": "content_block_stop", "index": i})
        events.append({"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 1}})
        events.append({"type": "message_stop"})
        return events
