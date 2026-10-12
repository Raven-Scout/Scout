"""Push channels and fan-out. Every channel gets injected fakes; nothing real is reached."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import requests

from scout import health_notify as hn
from scout.errors import ConfigError
from scout.scripts import notify_telegram

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _push(finding="runs.failing", kind="new", severity="red", ids=None):
    finding_ids = ids if ids is not None else (() if finding == "recovered" else (finding,))
    return hn.Push(
        finding_id=finding, kind=kind, severity=severity, title="Title", body="Body", finding_ids=finding_ids
    )


# ----- desktop ----------------------------------------------------------


def test_desktop_sends_and_records(tmp_path):
    sent = []
    res = hn.desktop_channel(_push(), vault=tmp_path, now=NOW, send=lambda t, b: sent.append((t, b)) or True)
    assert sent == [("Title", "Body")]
    assert res == {"channel": "desktop", "ok": True, "detail": "sent"}


def test_desktop_failure_recorded(tmp_path):
    res = hn.desktop_channel(_push(), vault=tmp_path, now=NOW, send=lambda t, b: False)
    assert res["ok"] is False and res["channel"] == "desktop"


def _marker(vault, content):
    d = vault / ".scout-logs"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".auth-alert-sent").write_text(content)
    return d / ".auth-alert-sent"


def test_desktop_skips_auth_when_wrapper_alerted_recently(tmp_path):
    _marker(tmp_path, str(int(NOW.timestamp()) - 3600))
    sent = []
    res = hn.desktop_channel(_push("auth.expired"), vault=tmp_path, now=NOW, send=lambda t, b: sent.append(1) or True)
    assert res is None and sent == []


def test_desktop_sends_auth_when_marker_is_25h_old(tmp_path):
    _marker(tmp_path, str(int(NOW.timestamp()) - 25 * 3600))
    res = hn.desktop_channel(_push("auth.expired"), vault=tmp_path, now=NOW, send=lambda t, b: True)
    assert res["ok"] is True


def test_desktop_marker_garbage_falls_back_to_mtime(tmp_path):
    p = _marker(tmp_path, "not a number")
    recent = NOW.timestamp() - 600
    os.utime(p, (recent, recent))
    assert hn.desktop_channel(_push("auth.expired"), vault=tmp_path, now=NOW, send=lambda t, b: True) is None
    old = NOW.timestamp() - 48 * 3600
    os.utime(p, (old, old))
    assert hn.desktop_channel(_push("auth.expired"), vault=tmp_path, now=NOW, send=lambda t, b: True)["ok"]


def test_desktop_future_dated_marker_does_not_suppress(tmp_path):
    _marker(tmp_path, str(int(NOW.timestamp()) + 3600))
    assert hn.desktop_channel(_push("auth.expired"), vault=tmp_path, now=NOW, send=lambda t, b: True)["ok"]


def test_desktop_marker_skips_only_a_push_listing_auth_alone(tmp_path):
    _marker(tmp_path, str(int(NOW.timestamp()) - 3600))
    both = _push("auth.expired", ids=("auth.expired", "lane:dreaming"))
    assert hn.desktop_channel(both, vault=tmp_path, now=NOW, send=lambda t, b: True)["ok"]


def test_desktop_auth_without_marker_sends(tmp_path):
    assert hn.desktop_channel(_push("auth.expired"), vault=tmp_path, now=NOW, send=lambda t, b: True)["ok"]


def test_desktop_marker_only_gates_auth(tmp_path):
    _marker(tmp_path, str(int(NOW.timestamp())))
    assert hn.desktop_channel(_push("runs.failing"), vault=tmp_path, now=NOW, send=lambda t, b: True)["ok"]


def test_desktop_default_send_resolves_late(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(hn.notify_desktop, "send", lambda t, b: seen.append((t, b)) or True)
    assert hn.desktop_channel(_push(), vault=tmp_path, now=NOW)["ok"]
    assert seen == [("Title", "Body")]


# ----- telegram ---------------------------------------------------------


def test_telegram_red_new_is_action_required():
    calls = []
    res = hn.telegram_channel(_push(), send=lambda tier, body: calls.append((tier, body)))
    assert calls == [("action_required", "Body")]  # the title is for desktop; Telegram gets the body once
    assert res == {"channel": "telegram", "ok": True, "detail": "sent"}


def test_telegram_red_reminder_is_action_required():
    calls = []
    hn.telegram_channel(_push(kind="reminder"), send=lambda tier, body: calls.append(tier))
    assert calls == ["action_required"]


def test_telegram_recovered_is_info():
    calls = []
    hn.telegram_channel(_push("recovered", "recovered", "green"), send=lambda tier, body: calls.append(tier))
    assert calls == ["info"]


def test_telegram_not_configured_is_none():
    def boom(tier, body):
        raise ConfigError("Missing secret: /x/telegram-bot-token")

    assert hn.telegram_channel(_push(), send=boom) is None


def test_telegram_duplicate_is_a_recorded_noop():
    def dup(tier, body):
        raise notify_telegram.DuplicateSendError("01ORIGINAL", "2026-10-11T12:00:00+00:00")

    res = hn.telegram_channel(_push(), send=dup)
    assert res == {"channel": "telegram", "ok": True, "detail": "duplicate"}


def test_telegram_http_error_never_leaks_url():
    def boom(tier, body):
        raise requests.HTTPError("403 for https://api.telegram.org/bot123:SECRET/sendMessage")

    res = hn.telegram_channel(_push(), send=boom)
    assert res == {"channel": "telegram", "ok": False, "detail": "HTTPError"}
    assert "SECRET" not in str(res)


def test_telegram_default_send_resolves_late(monkeypatch):
    seen = []
    monkeypatch.setattr(notify_telegram, "send", lambda tier, body: seen.append(tier))
    assert hn.telegram_channel(_push())["ok"]
    assert seen == ["action_required"]


# ----- command ----------------------------------------------------------


def _config(vault, text):
    (vault / "scout-config.yaml").write_text(text)


def test_command_channel_gets_env(tmp_path, monkeypatch):
    out = tmp_path / "env.txt"
    monkeypatch.setenv("OUT", str(out))
    _config(tmp_path, "notify:\n  command: 'env > \"$OUT\"'\n")
    res = hn.command_channel(_push(), vault=tmp_path)
    assert res == {"channel": "command", "ok": True, "detail": "exit 0"}
    text = out.read_text()
    assert "SCOUT_NOTIFY_FINDING=runs.failing" in text
    assert "SCOUT_NOTIFY_TITLE=Title" in text
    assert "SCOUT_NOTIFY_BODY=Body" in text
    assert "SCOUT_NOTIFY_SEVERITY=red" in text


def test_command_channel_run_kwargs(tmp_path):
    _config(tmp_path, "notify:\n  command: echo hi\n")
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        return SimpleNamespace(returncode=0)

    hn.command_channel(_push(), vault=tmp_path, run=run)
    argv, kw = calls[0]
    assert argv == ["/bin/sh", "-c", "echo hi"]
    assert kw["timeout"] == 30
    assert kw["stdin"] == subprocess.DEVNULL
    assert kw["capture_output"] is True


def test_command_channel_timeout_is_recorded(tmp_path):
    _config(tmp_path, "notify:\n  command: sleep 99\n")

    def run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 30)

    assert hn.command_channel(_push(), vault=tmp_path, run=run) == {
        "channel": "command",
        "ok": False,
        "detail": "timeout",
    }


def test_command_channel_oserror_is_recorded(tmp_path):
    _config(tmp_path, "notify:\n  command: x\n")

    def run(argv, **kw):
        raise OSError("nope")

    assert hn.command_channel(_push(), vault=tmp_path, run=run)["detail"] == "oserror"


def test_command_channel_failure_is_recorded(tmp_path):
    _config(tmp_path, "notify:\n  command: exit 3\n")
    res = hn.command_channel(_push(), vault=tmp_path)
    assert res["ok"] is False and res["detail"] == "exit 3"


@pytest.mark.parametrize(
    "text",
    [
        "other: 1\n",
        "notify: {}\n",
        "notify:\n  command: '   '\n",
        "notify: [a\n",
        "- a\n",
        "notify: 5\n",
        "notify:\n  command: 7\n",
    ],
)
def test_command_channel_absent_or_malformed_is_skipped(tmp_path, text):
    _config(tmp_path, text)
    ran = []
    assert hn.command_channel(_push(), vault=tmp_path, run=lambda *a, **k: ran.append(1)) is None
    assert ran == []


def test_command_channel_no_config_file_is_skipped(tmp_path):
    assert hn.command_channel(_push(), vault=tmp_path, run=lambda *a, **k: 1 / 0) is None


# ----- fan-out ----------------------------------------------------------


def test_fan_out_calls_every_channel_even_after_one_fails(tmp_path):
    order = []

    def first(push):
        order.append(1)
        raise RuntimeError("secret-token")

    def second(push):
        order.append(2)
        return None

    def third(push):
        order.append(3)
        return {"channel": "x", "ok": True, "detail": "ok"}

    res = hn.fan_out(_push(), vault=tmp_path, now=NOW, channels=[first, second, third])
    assert order == [1, 2, 3]
    assert res == [{"channel": "first", "ok": False, "detail": "error"}, {"channel": "x", "ok": True, "detail": "ok"}]


def test_fan_out_default_channels_in_order(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(hn.notify_desktop, "send", lambda t, b: order.append("desktop") or True)
    monkeypatch.setattr(notify_telegram, "send", lambda tier, body: order.append("telegram"))
    _config(tmp_path, "notify:\n  command: 'true'\n")
    monkeypatch.setattr(
        hn.subprocess, "run", lambda argv, **kw: order.append("command") or SimpleNamespace(returncode=0)
    )
    res = hn.fan_out(_push(), vault=tmp_path, now=NOW)
    assert order == ["desktop", "telegram", "command"]
    assert [r["channel"] for r in res] == ["desktop", "telegram", "command"]
