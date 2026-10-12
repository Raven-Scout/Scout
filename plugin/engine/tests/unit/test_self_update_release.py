from __future__ import annotations

import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest

from scout.scripts import self_update as su

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def _rel(**kw):
    return {"draft": False, "prerelease": False, "published_at": "2026-10-10T12:00:00Z", **kw}


def test_published_and_soaked():
    status = su.release_status("0.17.0", now=NOW, soak_hours=24, fetch=lambda v: _rel())
    assert status == su.ReleaseStatus(True, datetime(2026, 10, 10, 12, tzinfo=UTC), "")


def test_missing():
    assert su.release_status("0.17.0", now=NOW, soak_hours=24, fetch=lambda v: None).reason == "missing"


@pytest.mark.parametrize("release", [_rel(draft=True), _rel(published_at=None), _rel(published_at="garbage")])
def test_draft_or_unpublished(release):
    status = su.release_status("0.17.0", now=NOW, soak_hours=24, fetch=lambda v: release)
    assert not status.published and status.reason == "draft"


def test_prerelease():
    status = su.release_status("0.17.0", now=NOW, soak_hours=24, fetch=lambda v: _rel(prerelease=True))
    assert not status.published and status.reason == "prerelease" and status.published_at is not None


@pytest.mark.parametrize(("hours", "ok"), [(23.9, False), (24.0, True)])
def test_soak_boundary(hours, ok):
    published_at = (NOW - timedelta(hours=hours)).isoformat()
    status = su.release_status("0.17.0", now=NOW, soak_hours=24, fetch=lambda v: _rel(published_at=published_at))
    assert status.published is ok
    if not ok:
        assert status.reason.startswith("soaking until")


def test_naive_published_at_is_utc():
    status = su.release_status("0.17.0", now=NOW, soak_hours=0, fetch=lambda v: _rel(published_at="2026-10-10T12:00:00"))
    assert status.published_at == datetime(2026, 10, 10, 12, tzinfo=UTC)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_release_parses(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        return _Resp(json.dumps(_rel()).encode())

    monkeypatch.setattr(su.urllib.request, "urlopen", fake_urlopen)
    assert su.fetch_release("0.17.0")["draft"] is False
    assert seen["url"] == "https://api.github.com/repos/Raven-Scout/Scout/releases/tags/v0.17.0"


def test_fetch_release_404_is_none(monkeypatch):
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(su.urllib.request, "urlopen", fake_urlopen)
    assert su.fetch_release("0.17.0") is None


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.HTTPError("u", 403, "rate limited", {}, None),
        urllib.error.URLError("offline"),
        TimeoutError(),
    ],
)
def test_fetch_release_errors_raise(monkeypatch, exc):
    def fake_urlopen(req, timeout):
        raise exc

    monkeypatch.setattr(su.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError):
        su.fetch_release("0.17.0")


@pytest.mark.parametrize("body", [b"not json", b"[]"])
def test_fetch_release_bad_payload_raises(monkeypatch, body):
    monkeypatch.setattr(su.urllib.request, "urlopen", lambda req, timeout: _Resp(body))
    with pytest.raises(RuntimeError):
        su.fetch_release("0.17.0")
