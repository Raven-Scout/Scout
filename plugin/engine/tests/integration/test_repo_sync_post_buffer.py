"""A push over git's default http.postBuffer reaches a host that refuses git's probe.

Over 1 MiB, git sends a pack chunked, after a bare ``0000`` probe POST to
git-receive-pack. Agnes refuses the probe as malformed (HTTP 400), so the
push dies. The server here is ``git http-backend`` behind a small handler
that does the same, on 127.0.0.1. The pack is random bytes, so it can't
compress under the buffer.
"""

from __future__ import annotations

import gzip
import os
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scout import repo_sync as rs
from tests.unit.privacy_vault_helpers import git_env

PACK_BYTES = 3 * 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    root: Path

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            out = b""
            while size := int(self.rfile.readline().split(b";")[0], 16):
                out += self.rfile.read(size)
                self.rfile.readline()
            self.rfile.readline()
            return out
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _backend(self, body: bytes) -> None:
        path, _, query = self.path.partition("?")
        if self.headers.get("Content-Encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
        env = {
            **os.environ,
            "GIT_PROJECT_ROOT": str(self.root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "REMOTE_USER": "scout",
            "REQUEST_METHOD": self.command,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
        }
        out = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True, check=True).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        headers = [line.split(":", 1) for line in head.decode().split("\r\n") if ":" in line]
        status = next((int(v.split()[0]) for k, v in headers if k.lower() == "status"), 200)
        self.send_response(status)
        for k, v in headers:
            if k.lower() != "status":
                self.send_header(k, v.strip())
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        self._backend(b"")

    def do_POST(self) -> None:
        body = self._body()
        if self.path.endswith("/git-receive-pack") and body == b"0000":
            # Agnes's push checker reads the probe as an empty, malformed push.
            msg = b'{"detail":{"code":"repository_push_malformed"}}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        self._backend(body)


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    root = tmp_path / "srv"
    bare = root / "repo.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True, env=git_env())
    subprocess.run(["git", "-C", str(bare), "config", "http.receivepack", "true"], check=True, env=git_env())
    handler = type("Handler", (_Handler,), {"root": root})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/repo.git", bare
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    for key, value in git_env().items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "blob.bin").write_bytes(os.urandom(PACK_BYTES))
    subprocess.run(["git", "-C", str(work), "add", "blob.bin"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "random"], check=True)
    rev = rs.git(work, "rev-parse", "HEAD").stdout.strip()
    return work, rev


class _DefaultBufferHost(rs.AgnesHost):
    """Agnes's git env without the http.postBuffer pair, as before the fix."""

    def git_env(self) -> dict[str, str]:
        env = super().git_env()
        pairs = [
            (env.pop(f"GIT_CONFIG_KEY_{i}"), env.pop(f"GIT_CONFIG_VALUE_{i}"))
            for i in range(int(env["GIT_CONFIG_COUNT"]))
        ]
        pairs = [p for p in pairs if p[0] != "http.postBuffer"]
        for i, (k, v) in enumerate(pairs):
            env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = k, v
        env["GIT_CONFIG_COUNT"] = str(len(pairs))
        return env


def test_push_over_default_post_buffer_succeeds(server: tuple[str, Path], repo: tuple[Path, str]) -> None:
    url, bare = server
    work, rev = repo
    rs.push(work, rs.AgnesHost(url, "t"), url, rev, lease=("lt", 1))
    head = subprocess.run(["git", "-C", str(bare), "rev-parse", "main"], capture_output=True, text=True, check=True)
    assert head.stdout.strip() == rev


def test_push_without_post_buffer_is_refused_as_malformed(server: tuple[str, Path], repo: tuple[Path, str]) -> None:
    url, _ = server
    work, rev = repo
    with pytest.raises(rs.RepoSyncError) as e:
        rs.push(work, _DefaultBufferHost(url, "t"), url, rev)
    assert e.value.code == "repository_push_malformed"
    assert "malformed (HTTP 400)" in str(e.value) and "http.postBuffer" in str(e.value)
