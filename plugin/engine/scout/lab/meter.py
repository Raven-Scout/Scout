"""Model-call meter: the only way an experiment script reaches a model.

Experiments run inside a sandbox whose sole network access is a unix socket to this server. The
server lives OUTSIDE the sandbox, makes each ``claude -p`` call on the script's behalf, adds up the
cost in a JSONL ledger and refuses once the per-experiment dollar cap is reached.

Security: the call runs unsandboxed, so it must not be a way out of the sandbox. ``claude`` is
started with no tools (``--tools ""``), ``--restricted``, no MCP servers, no session persistence, in
a fresh empty directory that is both its working directory and its ``HOME`` (so no user CLAUDE.md,
skills, commands or settings are loaded; slash commands are disabled), with an environment of only
``PATH``, ``LANG`` and the Claude auth variables the scheduled runner passes
(``scripts/claude-with-retry.sh``): ``CLAUDE_CODE_OAUTH_TOKEN`` and ``ANTHROPIC_API_KEY``. Auth comes
only from that env token or API key, never from the user's home. The ``model`` value is passed as an
argv value, so it is held to a strict pattern.

Cost accounting fails closed: a timed-out call, or one whose JSON lacks a valid non-negative finite
``total_cost_usd``, is charged the remaining budget so the next call is refused.

Protocol: one JSON line per connection, ``{"v":1,"prompt":str,"model":str|None}``; one JSON line back.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ENV_METER = "SCOUT_LAB_METER"
MAX_PROMPT_CHARS = 200_000
MAX_REQUEST_BYTES = 1 << 20
_READ_TIMEOUT_S = 10.0
_MODEL_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_PASS_ENV = ("PATH", "LANG", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


class MeterUnavailable(Exception):
    """The meter socket is missing, refused the connection or answered nonsense."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MeterServer:
    """Serves ``claude -p`` calls on ``sock_path``, one at a time, against a dollar cap."""

    def __init__(
        self,
        sock_path: Path,
        ledger: Path,
        cap_usd: float,
        claude_argv: Sequence[str] = ("claude",),
        call_timeout_s: float = 600,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.sock_path = Path(sock_path)
        self.ledger = Path(ledger)
        self.cap_usd = float(cap_usd)
        self.claude_argv = tuple(claude_argv)
        self.call_timeout_s = call_timeout_s
        self._now = now
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._lock = threading.Lock()

    # --- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock_path.unlink()
        except FileNotFoundError:
            pass
        sock.bind(str(self.sock_path))
        os.chmod(self.sock_path, 0o600)
        sock.listen(8)
        sock.settimeout(0.2)
        self._sock = sock
        self._stopping.clear()
        self._thread = threading.Thread(target=self._serve, name="lab-meter", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=self.call_timeout_s + 5)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        try:
            self.sock_path.unlink()
        except FileNotFoundError:
            pass

    def _serve(self) -> None:
        sock = self._sock
        assert sock is not None
        while not self._stopping.is_set():
            try:
                conn, _ = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with conn:
                try:
                    reply = self._handle(conn)
                except Exception as exc:  # never let one connection kill the server
                    reply = {"ok": False, "error": f"meter error: {exc.__class__.__name__}"}
                try:
                    conn.sendall((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
                except OSError:
                    pass

    # --- one request -------------------------------------------------------------------

    def _handle(self, conn: socket.socket) -> dict[str, Any]:
        conn.settimeout(_READ_TIMEOUT_S)
        buf = b""
        try:
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_REQUEST_BYTES:
                    return {"ok": False, "error": "request too large"}
        except TimeoutError:
            return {"ok": False, "error": "request timed out"}
        line = buf.split(b"\n", 1)[0]
        try:
            req = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {"ok": False, "error": "malformed request: not JSON"}
        if not isinstance(req, dict) or req.get("v") != 1:
            return {"ok": False, "error": "unsupported protocol version (want v=1)"}
        prompt = req.get("prompt")
        if not isinstance(prompt, str):
            return {"ok": False, "error": "prompt must be a string"}
        if len(prompt) > MAX_PROMPT_CHARS:
            return {"ok": False, "error": f"prompt too long (max {MAX_PROMPT_CHARS} characters)"}
        model = req.get("model")
        if model is not None and not (isinstance(model, str) and _MODEL_RE.fullmatch(model)):
            return {"ok": False, "error": "bad model name (allowed: letters, digits, '.', '_', ':', '-'; max 64)"}
        with self._lock:
            return self._call(prompt, model)

    def _spent(self) -> tuple[float, float]:
        """(total spent, largest single call so far) from the ledger."""
        total = 0.0
        biggest = 0.0
        try:
            text = self.ledger.read_text(encoding="utf-8")
        except FileNotFoundError:
            return 0.0, 0.0
        for line in text.splitlines():
            try:
                cost = json.loads(line).get("cost_usd", 0)
            except (ValueError, AttributeError):
                continue
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
                total += cost
                biggest = max(biggest, cost)
        return total, biggest

    def _record(self, cost: float, ok: bool) -> None:
        row = {"at": self._now().strftime("%Y-%m-%d %H:%M:%S"), "cost_usd": cost, "ok": ok}
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _call(self, prompt: str, model: str | None) -> dict[str, Any]:
        spent, biggest = self._spent()
        # Refuse once the cap is reached, and also when one more call as large as the largest so
        # far would overshoot it (a call's cost is only known afterwards, so this keeps the
        # overshoot to at most one unusually large call).
        if spent >= self.cap_usd or spent + biggest > self.cap_usd:
            return {"ok": False, "refused": "cap", "spent_usd": spent, "cap_usd": self.cap_usd}
        argv = [
            *self.claude_argv,
            "-p",
            "--output-format",
            "json",
            "--restricted",
            "--tools",
            "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--disable-slash-commands",
            *(["--model", model] if model else []),
        ]
        cwd = tempfile.mkdtemp(prefix="scout-lab-meter-")
        env = {k: os.environ[k] for k in _PASS_ENV if k in os.environ}
        env["HOME"] = cwd  # no ~/.claude/CLAUDE.md, skills or settings: the call sees nothing of the user's
        try:
            try:
                proc = subprocess.run(
                    argv,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    env=env,
                    cwd=cwd,
                    timeout=self.call_timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                # The API has likely billed something we cannot see: fail closed.
                charge = max(self.cap_usd - spent, 0.0)
                self._record(charge, False)
                return {
                    "ok": False,
                    "error": f"model call timed out after {self.call_timeout_s:g}s; remaining budget charged",
                    "spent_usd": spent + charge,
                }
            except OSError as exc:
                self._record(0.0, False)  # claude never started: nothing was billed
                return {"ok": False, "error": f"cannot run claude: {exc.__class__.__name__}"}
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
        doc: Any = None
        try:
            doc = json.loads(proc.stdout)
        except ValueError:
            pass
        raw = doc.get("total_cost_usd") if isinstance(doc, dict) else None
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw) and raw >= 0:
            cost = float(raw)
        else:
            cost = max(self.cap_usd - spent, 0.0)  # cost unknown: fail closed, charge the remaining budget
        spent_after = spent + cost
        if proc.returncode != 0:
            self._record(cost, False)
            return {"ok": False, "error": f"claude exited {proc.returncode}", "spent_usd": spent_after}
        if not isinstance(doc, dict) or not isinstance(doc.get("result"), str) or doc.get("is_error") is True:
            self._record(cost, False)
            return {"ok": False, "error": "claude returned no usable result", "spent_usd": spent_after}
        self._record(cost, True)
        return {"ok": True, "text": doc["result"], "cost_usd": cost, "spent_usd": spent_after}


def meter_call(prompt: str, model: str | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Ask the meter at ``env[SCOUT_LAB_METER]`` (default: ``os.environ``) for one model call.

    Returns the server's reply dict. Raises :class:`MeterUnavailable` when no meter can be reached.
    """
    path = (env if env is not None else os.environ).get(ENV_METER)
    if not path:
        raise MeterUnavailable(f"${ENV_METER} is not set: model calls are only available inside a lab experiment")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(1800)
        sock.connect(path)
        sock.sendall((json.dumps({"v": 1, "prompt": prompt, "model": model}) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    except OSError as exc:
        raise MeterUnavailable(f"meter unreachable ({exc.__class__.__name__})") from None
    finally:
        sock.close()
    try:
        reply = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise MeterUnavailable("meter sent an unreadable reply") from None
    if not isinstance(reply, dict):
        raise MeterUnavailable("meter sent an unreadable reply")
    return reply
