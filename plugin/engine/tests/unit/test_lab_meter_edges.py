"""The meter's edges: how it reads a request that never ends, answers a client that left, and survives its own
handler failing; and how the client side reads a meter that answers nonsense. None of it needs a sandbox."""

from __future__ import annotations

import json
import shutil
import socket
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from scout.lab import meter

FAKE = Path(__file__).parent.parent / "fixtures" / "lab" / "fake-claude.sh"


@pytest.fixture
def d() -> Iterator[Path]:
    p = Path(tempfile.mkdtemp(dir="/tmp"))  # a unix socket path must stay short
    p.chmod(0o700)
    yield p
    shutil.rmtree(p, ignore_errors=True)


def _server(d: Path, **kw: object) -> meter.MeterServer:
    return meter.MeterServer(
        d / "m.sock",
        d / "ledger.jsonl",
        1.0,
        claude_argv=(str(FAKE), str(d / "calls.log")),
        **kw,  # type: ignore[arg-type]
    )


def _raw(d: Path, payload: bytes, *, shut: bool = False) -> dict[str, object]:
    c = socket.socket(socket.AF_UNIX)
    try:
        c.connect(str(d / "m.sock"))
        if payload:
            c.sendall(payload)
        if shut:
            c.shutdown(socket.SHUT_WR)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = c.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        c.close()
    out: dict[str, object] = json.loads(buf)
    return out


def test_spent_reads_a_missing_or_damaged_ledger(d: Path) -> None:
    s = _server(d)
    assert s._spent() == (0.0, 0.0)  # no ledger yet
    (d / "ledger.jsonl").write_text('not json\n[1, 2]\n{"cost_usd": true}\n{"cost_usd": 0.25}\n{"cost_usd": "x"}\n')
    assert s._spent() == (0.25, 0.25)


def test_stop_before_start_and_after_the_socket_is_gone(d: Path) -> None:
    s = _server(d)
    s.stop()  # never started: nothing to join, close or remove
    s.start()
    (d / "m.sock").unlink()
    s.stop()
    assert not (d / "m.sock").exists()


def test_a_closed_listening_socket_ends_the_server(d: Path) -> None:
    s = _server(d)
    s.start()
    assert s._sock is not None and s._thread is not None
    s._sock.close()  # accept() now fails with an OSError: the loop returns
    s._thread.join(timeout=10)
    assert not s._thread.is_alive()
    s.stop()


def test_a_request_cut_off_before_its_newline_is_malformed(d: Path) -> None:
    s = _server(d)
    s.start()
    try:
        r = _raw(d, b'{"v":1,"prompt":"x"', shut=True)
    finally:
        s.stop()
    assert r == {"ok": False, "error": "malformed request: not JSON"}


def test_a_request_without_end_is_too_large(d: Path) -> None:
    s = _server(d)
    s.start()
    try:
        r = _raw(d, b"x" * (meter.MAX_REQUEST_BYTES + 1))  # all of it read, so the client's send completes
    finally:
        s.stop()
    assert r == {"ok": False, "error": "request too large"}


def test_a_silent_client_times_out(d: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(meter, "_READ_TIMEOUT_S", 0.2)
    s = _server(d)
    s.start()
    try:
        r = _raw(d, b"")
    finally:
        s.stop()
    assert r == {"ok": False, "error": "request timed out"}


def test_a_handler_that_fails_answers_with_its_class(d: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _server(d)

    def boom(conn: socket.socket) -> dict[str, object]:
        # Read the request first, as the real handler does, so the client's send can't race the close.
        conn.recv(65536)
        raise RuntimeError("bug")

    monkeypatch.setattr(s, "_handle", boom)
    s.start()
    try:
        r = _raw(d, b'{"v":1,"prompt":"x"}\n')
    finally:
        s.stop()
    assert r == {"ok": False, "error": "meter error: RuntimeError"}


def test_a_client_that_left_doesnt_stop_the_server(d: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reply to a client that hung up fails to send; the next client is still served."""
    s = _server(d)
    left = threading.Event()
    real = s._handle

    def slow(conn: socket.socket) -> dict[str, object]:
        if not left.is_set():
            conn.recv(65536)
            left.wait(10)
            return {"ok": True, "text": "x" * (4 << 20)}  # more than a socket buffer: the send fails
        return real(conn)

    monkeypatch.setattr(s, "_handle", slow)
    s.start()
    try:
        c = socket.socket(socket.AF_UNIX)
        c.connect(str(d / "m.sock"))
        c.sendall(b'{"v":1,"prompt":"x"}\n')
        c.close()
        left.set()
        r = _raw(d, b'{"v":1,"prompt":"hi"}\n')
    finally:
        s.stop()
    assert r["ok"] is True and r["text"] == "echo:hi"


def test_claude_that_cant_start_is_charged_nothing(d: Path) -> None:
    s = meter.MeterServer(d / "m.sock", d / "ledger.jsonl", 1.0, claude_argv=(str(d / "no-such-claude"),))
    s.start()
    try:
        r = meter.meter_call("x", env={meter.ENV_METER: str(d / "m.sock")})
    finally:
        s.stop()
    assert r == {"ok": False, "error": "cannot run claude: FileNotFoundError"}
    [row] = [json.loads(x) for x in (d / "ledger.jsonl").read_text().splitlines()]
    assert row["cost_usd"] == 0.0 and row["ok"] is False


def _answering(d: Path, reply: bytes) -> threading.Thread:
    """A stand-in meter that answers one connection with ``reply`` and hangs up."""
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(d / "m.sock"))
    srv.listen(1)

    def serve() -> None:
        conn, _ = srv.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(reply)
        srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return t


@pytest.mark.parametrize("reply", [b"", b"\xff\xfe\n", b"not json", b"[1, 2]\n"])
def test_a_meter_answering_nonsense_is_unavailable(d: Path, reply: bytes) -> None:
    t = _answering(d, reply)
    with pytest.raises(meter.MeterUnavailable, match="unreadable reply"):
        meter.meter_call("x", env={meter.ENV_METER: str(d / "m.sock")})
    t.join(timeout=10)


def test_a_reply_without_a_newline_is_read_to_the_end(d: Path) -> None:
    t = _answering(d, b'{"ok": true, "text": "t"}')
    assert meter.meter_call("x", env={meter.ENV_METER: str(d / "m.sock")}) == {"ok": True, "text": "t"}
    t.join(timeout=10)
