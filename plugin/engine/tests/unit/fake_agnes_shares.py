"""A fake of Agnes's A8b owner endpoints on 127.0.0.1, for the Shared Areas S1b tests.

It follows keboola/agnes-the-ai-analyst#7450 as merged
(``app/api/knowledge_repository_shares.py`` and ``owner_shares.py``):

- ``GET /api/v1/me/knowledge-repository/shares`` lists ``shares`` (each with its
  open ``stops``), ``stops_not_listed``, ``cleared_stops`` and
  ``pending_server_commits``;
- ``POST .../shares/stop`` takes ``{glob, person?, write_back}``, answers
  ``404 share_not_found`` for a glob it doesn't list or a person the share
  doesn't admit (a group member counts), and ``422`` for an extra key;
- ``POST .../shares/clear-stop`` takes ``{stop_id}`` and only a bearer whose JWT
  ``typ`` is ``pat``: anything else gets ``404 knowledge_repository_not_found``.

``mode="old"`` answers every path a bare FastAPI ``404 {"detail": "Not Found"}``
(an Agnes before A8b); ``mode="sharing_off"`` answers the switch-off 404. Every
request is recorded (method, path, body, whether it carried a bearer), never the
token itself. Tokens here are made-up JWTs (:func:`fake_jwt`), not secrets.
"""

from __future__ import annotations

import base64
import itertools
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

BASE = "/api/v1/me/knowledge-repository"


def fake_jwt(typ: str | None) -> str:
    def part(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    claims: dict[str, Any] = {"sub": "u-alex", "email": "alex@example.com"}
    if typ is not None:
        claims["typ"] = typ
    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(claims)}.c2lnbmF0dXJl"


def _typ(header: str) -> str | None:
    if header[:7].lower() != "bearer ":
        return None
    parts = header[7:].split(".")
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (IndexError, ValueError):
        return None
    return claims.get("typ") if isinstance(claims, dict) else None


@dataclass
class FakeAgnes:
    """State and recorded requests. ``shares``: ``{glob: [principal entries]}`` as Agnes lists them."""

    mode: str = "a8b"  # a8b | old | sharing_off
    shares: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    stops: list[dict[str, Any]] = field(default_factory=list)  # open and cleared
    pending_server_commits: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)
    clear_refuses: bool = False  # answer clear-stop 404 knowledge_repository_not_found whatever the token
    on_stop: Callable[[dict[str, Any]], None] | None = None
    url: str = ""
    _ids: Iterator[int] = field(default_factory=lambda: itertools.count(1))

    # ----- set-up helpers -----

    def share(self, glob: str, *people: str, groups: dict[str, list[str]] | None = None) -> None:
        entries: list[dict[str, Any]] = [
            {"principal": f"person:{p}", "kind": "person", "email": p, "name": p.split("@")[0], "known": True}
            for p in people
        ]
        for gid, members in (groups or {}).items():
            entries.append(
                {
                    "principal": f"group:{gid}",
                    "kind": "group",
                    "name": f"Group {gid}",
                    "known": True,
                    "members": [{"name": m.split("@")[0], "email": m} for m in members],
                }
            )
        self.shares[glob] = entries

    def add_stop(self, glob: str, principal: str | None, *, cleared: bool = False) -> dict[str, Any]:
        row = {
            "stop_id": f"stop{next(self._ids):04d}",
            "glob": glob,
            "principal": principal,
            "surface": "cli",
            "write_back": "not_requested",
            "written_sha": None,
            "created_at": "2026-11-16T09:30:00.123456Z",
            "cleared_at": "2026-11-17T10:00:00Z" if cleared else None,
        }
        self.stops.append(row)
        return row

    def open_stops(self) -> list[dict[str, Any]]:
        return [s for s in self.stops if s["cleared_at"] is None]

    def calls(self, method: str | None = None, suffix: str = "") -> list[dict[str, Any]]:
        return [r for r in self.requests if (method is None or r["method"] == method) and r["path"].endswith(suffix)]

    # ----- the API -----

    def _listing(self) -> dict[str, Any]:
        open_ = self.open_stops()
        shares = [
            {
                "glob": glob,
                "kind": "folder",
                "notes": 2,
                "private_excluded": 1,
                "principals": entries,
                "stops": [s for s in open_ if s["glob"] == glob],
            }
            for glob, entries in self.shares.items()
        ]
        return {
            "federation_enabled": True,
            "head": "0" * 40,
            "mirrors_config": True,
            "pending_server_commits": self.pending_server_commits,
            "shares": shares,
            "stops_not_listed": [s for s in open_ if s["glob"] not in self.shares],
            "cleared_stops": [s for s in self.stops if s["cleared_at"] is not None],
        }

    def _admits(self, glob: str, principal: str) -> bool:
        for entry in self.shares.get(glob, []):
            if entry["principal"] == principal:
                return True
            if principal.startswith("person:") and any(
                m["email"] == principal[len("person:") :] for m in entry.get("members") or []
            ):
                return True
        return False

    def handle(self, method: str, path: str, auth: str, body: Any) -> tuple[int, Any]:
        self.requests.append({"method": method, "path": path, "body": body, "bearer": auth[:7].lower() == "bearer "})
        if self.mode == "old" or not path.startswith(BASE):
            return 404, {"detail": "Not Found"}
        if self.mode == "sharing_off":
            return 404, {"detail": {"code": "knowledge_repository_sharing_disabled", "message": "off"}}
        if method == "GET" and path == f"{BASE}/shares":
            return 200, self._listing()
        if method == "POST" and path == f"{BASE}/shares/stop":
            if not isinstance(body, dict) or set(body) - {"glob", "person", "write_back"}:
                return 422, {"detail": {"code": "share_widen_in_vault_only", "message": "widen"}}
            glob, person = body.get("glob"), body.get("person")
            if glob not in self.shares or (person is not None and not self._admits(glob, person)):
                return 404, {"detail": {"code": "share_not_found", "message": "Nothing you share matches that"}}
            if self.on_stop is not None:
                self.on_stop(body)
            same = next((s for s in self.open_stops() if s["glob"] == glob and s["principal"] == person), None)
            row = same or self.add_stop(glob, person)
            return 201, {"stop": row, "created": same is None}
        if method == "POST" and path == f"{BASE}/shares/clear-stop":
            if self.clear_refuses or _typ(auth) != "pat":
                return 404, {"detail": {"code": "knowledge_repository_not_found", "message": "no repository"}}
            stop_id = body.get("stop_id") if isinstance(body, dict) else None
            row = next((s for s in self.open_stops() if s["stop_id"] == stop_id), None)
            if row is None:
                return 404, {"detail": {"code": "share_stop_not_found", "message": "no open stop with that id"}}
            row["cleared_at"] = "2026-11-18T08:00:00Z"
            return 200, {"stop": row}
        return 404, {"detail": "Not Found"}


@contextmanager
def serve(agnes: FakeAgnes) -> Iterator[FakeAgnes]:
    """Run ``agnes`` on 127.0.0.1 (a free port) for the ``with`` block; ``agnes.url`` is its base URL."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

        def _do(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.loads(raw) if raw else None
            status, out = agnes.handle(self.command, self.path, self.headers.get("Authorization") or "", body)
            data = json.dumps(out).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = _do
        do_POST = _do

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    agnes.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield agnes
    finally:
        server.shutdown()
        server.server_close()
