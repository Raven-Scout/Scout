"""The Telegram bot token never escapes through an error.

The token sits in the request path (``/bot<token>/sendMessage``), and
``requests`` puts that path or the whole URL into ``str()`` of its exceptions:
``ConnectionError`` says ``Max retries exceeded with url: /bot<token>/...``,
``HTTPError`` says ``... for url: https://api.telegram.org/bot<token>/...``.
Every test here makes ``send()`` fail one way and then looks for the token in
everything a failure can reach: the raised exception (message, args, cause,
context, the response/request it carries), stdout/stderr of both entry points,
the trigger result and the trigger fire log.

Hermetic: the only real socket is a connection attempt to a closed port on
127.0.0.1. Nothing here contacts Telegram. The token is fake.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
import requests
from typer.testing import CliRunner

from scout import cli
from scout.redact import redact_secrets
from scout.scripts import notify_telegram
from scout.triggers.actions import notify
from scout.triggers.config import Action, ActionKind, Trigger
from scout.triggers.dispatcher import dispatch, log_fire
from scout.triggers.sources.base import ConnectorEvent

FAKE_TOKEN = "123456:FAKE-TOKEN"
FAKE_CHAT_ID = "987654321"
SECRET_PART = "FAKE-TOKEN"
API_URL = notify_telegram.TELEGRAM_API


@pytest.fixture
def secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "secrets"
    d.mkdir()
    for name, value in (("telegram-bot-token", FAKE_TOKEN), ("telegram-chat-id", FAKE_CHAT_ID)):
        (d / name).write_text(value + "\n", encoding="utf-8")
        (d / name).chmod(0o600)
    monkeypatch.setattr(notify_telegram, "secrets_dir", lambda: d)
    return d


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_error_response(status: int, reason: str) -> requests.Response:
    """A real Response whose raise_for_status() raises the real HTTPError."""
    resp = requests.Response()
    resp.status_code = status
    resp.reason = reason
    resp.url = f"{API_URL}/bot{FAKE_TOKEN}/sendMessage"
    resp._content = b'{"ok": false}'
    resp.request = requests.Request("POST", resp.url).prepare()
    return resp


def _timeout(*_a: object, **_kw: object) -> requests.Response:
    raise requests.Timeout(
        f"HTTPSConnectionPool(host='api.telegram.org', port=443): Read timed out. (url: /bot{FAKE_TOKEN}/sendMessage)",
        request=requests.Request("POST", f"{API_URL}/bot{FAKE_TOKEN}/sendMessage").prepare(),
    )


def _generic(*_a: object, **_kw: object) -> requests.Response:
    raise RuntimeError(f"unexpected failure posting to {API_URL}/bot{FAKE_TOKEN}/sendMessage")


def _connection_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real requests.post, to a closed port on localhost: the real ConnectionError."""
    local = f"http://127.0.0.1:{_closed_port()}"
    monkeypatch.setattr(notify_telegram, "TELEGRAM_API", local)

    def post_to_localhost_only(url: str, **kw: object) -> requests.Response:
        assert url.startswith(local + "/")  # never Telegram
        return requests.post(url, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(notify_telegram.requests, "post", post_to_localhost_only)


def _post_returns(status: int, reason: str) -> Callable[[pytest.MonkeyPatch], None]:
    def arm(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(notify_telegram.requests, "post", lambda *a, **kw: _http_error_response(status, reason))

    return arm


def _post_raises(fn: Callable[..., requests.Response]) -> Callable[[pytest.MonkeyPatch], None]:
    def arm(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(notify_telegram.requests, "post", fn)

    return arm


FAILURES: dict[str, Callable[[pytest.MonkeyPatch], None]] = {
    "connection_refused": _connection_refused,
    "http_401": _post_returns(401, "Unauthorized"),
    "http_404": _post_returns(404, "Not Found"),
    "http_500": _post_returns(500, "Internal Server Error"),
    "timeout": _post_raises(_timeout),
    "generic": _post_raises(_generic),
}


@pytest.fixture(params=sorted(FAILURES))
def failing(request: pytest.FixtureRequest, secrets_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """send() is armed to fail one way; the sent log is off (no vault needed)."""
    monkeypatch.setattr(notify_telegram, "_sent_log_path", lambda: None)
    FAILURES[request.param](monkeypatch)
    yield request.param


def _exception_texts(exc: BaseException) -> list[str]:
    """Every string a logger, traceback or str()/repr() could print from exc."""
    texts: list[str] = []
    seen: set[int] = set()
    todo: list[BaseException | None] = [exc]
    while todo:
        e = todo.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        texts += [str(e), repr(e), repr(e.args)]
        for attr in ("response", "request"):
            obj = getattr(e, attr, None)
            if obj is not None:
                texts += [str(getattr(obj, "url", "")), repr(obj)]
        todo += [e.__cause__, e.__context__]
    return texts


def _assert_clean(*texts: str) -> None:
    for text in texts:
        assert SECRET_PART not in text, "the bot token leaked"


# ----- the helper ------------------------------------------------------------


def test_redact_secrets_masks_the_token_in_a_url_path() -> None:
    out = redact_secrets(f"Max retries exceeded with url: /bot{FAKE_TOKEN}/sendMessage (Caused by ...)")
    assert SECRET_PART not in out
    assert "/bot<redacted>/sendMessage" in out


def test_redact_secrets_masks_a_percent_encoded_colon() -> None:
    assert SECRET_PART not in redact_secrets("/bot123456%3AFAKE-TOKEN/sendMessage")


def test_redact_secrets_masks_explicit_secrets_anywhere() -> None:
    assert redact_secrets(f"token={FAKE_TOKEN};", FAKE_TOKEN) == "token=<redacted>;"


def test_redact_secrets_leaves_ordinary_text_alone() -> None:
    text = "HTTP 401 Unauthorized at 2026-10-19T12:30:00Z (id 01JABCDEF), robot 12:30"
    assert redact_secrets(text) == text


# ----- send(): the raised exception -----------------------------------------


def test_send_failure_carries_no_token(failing: str) -> None:
    with pytest.raises(Exception) as info:
        notify_telegram.send("info", "hello")
    _assert_clean(*_exception_texts(info.value))


def test_send_failures_keep_their_requests_type(failing: str) -> None:
    """Callers map requests.HTTPError → status line, RequestException → exit 2."""
    with pytest.raises(Exception) as info:
        notify_telegram.send("info", "hello")
    if failing.startswith("http_"):
        assert isinstance(info.value, requests.HTTPError)
        assert info.value.response is not None
        assert info.value.response.status_code == int(failing[5:])
    elif failing == "connection_refused":
        assert isinstance(info.value, requests.ConnectionError)
    elif failing == "timeout":
        assert isinstance(info.value, requests.Timeout)


def test_send_failure_message_still_says_what_went_wrong(failing: str) -> None:
    with pytest.raises(Exception) as info:
        notify_telegram.send("info", "hello")
    if failing == "connection_refused":
        assert "/bot<redacted>/sendMessage" in str(info.value)
    if failing == "http_401":
        assert "401" in str(info.value)


# ----- the entry points ------------------------------------------------------


def test_cli_notify_telegram_prints_no_token(failing: str) -> None:
    result = CliRunner().invoke(cli.app, ["notify", "telegram", "--body", "hello"])
    assert result.exit_code != 0
    _assert_clean(result.output, result.stdout, result.stderr)
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        _assert_clean(*_exception_texts(result.exception))


def test_main_prints_no_token(failing: str, capsys: pytest.CaptureFixture[str]) -> None:
    try:
        notify_telegram.main(["--body", "hello"])
    except Exception as e:  # noqa: BLE001 - a raise is a finding only if it leaks
        _assert_clean(*_exception_texts(e))
    out = capsys.readouterr()
    _assert_clean(out.out, out.err)


# ----- the trigger path: result, raised error, fire log ----------------------


def _trigger() -> Trigger:
    return Trigger(
        id="slack_mention_alex",
        source="slack",
        match={"type": "mention"},
        daily_fire_cap=10,
        action=Action(kind=ActionKind.NOTIFY, params={}),
    )


def _event() -> ConnectorEvent:
    return ConnectorEvent(
        source="slack",
        source_event_id="1782910800.000200",
        ts="2026-07-01T13:00:00.000Z",
        raw_payload={"text": "ping"},
        normalized_match_fields={"type": "mention", "author": "priya", "channel": "C0123456789", "text": "ping"},
    )


def test_trigger_notify_action_records_no_token(failing: str, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError) as info:
        notify.run(_trigger(), _event(), vault=tmp_path)
    _assert_clean(*_exception_texts(info.value))


def test_trigger_fire_log_holds_no_token(failing: str, tmp_path: Path) -> None:
    outcome = dispatch(_trigger(), _event(), vault=tmp_path)
    assert outcome.status == "error"
    log_dir = tmp_path / ".scout-logs"
    log_fire(log_dir, outcome)
    _assert_clean(json.dumps(outcome.detail), *(p.read_text() for p in log_dir.iterdir()))


def test_trigger_paths_redact_a_leaky_sender(tmp_path: Path) -> None:
    """Defense in depth: a sender that bypasses send()'s scrub still can't leak."""

    def leaky(**_kw: object) -> None:
        raise requests.ConnectionError(f"Max retries exceeded with url: /bot{FAKE_TOKEN}/sendMessage")

    with pytest.raises(RuntimeError) as info:
        notify.run(_trigger(), _event(), vault=tmp_path, send_telegram=leaky)
    _assert_clean(str(info.value))
    outcome = dispatch(_trigger(), _event(), vault=tmp_path, send_telegram=leaky)
    assert outcome.status == "error"
    _assert_clean(json.dumps(outcome.detail))


def test_successful_send_is_unchanged(secrets_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify_telegram, "_sent_log_path", lambda: None)
    with patch("scout.scripts.notify_telegram.requests.post") as post:
        post.return_value.raise_for_status.return_value = None
        ev = notify_telegram.send("info", "hello")
    assert ev.kind == "notification.sent"
    assert post.call_args.args[0] == f"{API_URL}/bot{FAKE_TOKEN}/sendMessage"
