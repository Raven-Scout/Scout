"""Desktop notification sender. Every test injects fakes; nothing real runs."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from scout import notify_desktop


class FakeRun:
    def __init__(self, returncode=0, exc=None):
        self.calls = []
        self.returncode = returncode
        self.exc = exc

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.exc:
            raise self.exc
        return SimpleNamespace(returncode=self.returncode)


def _which(*present):
    return lambda name: f"/bin/{name}" if name in present else None


def test_osascript_gets_title_and_body_as_argv():
    run = FakeRun()
    ok = notify_desktop.send("T", "B", run=run, which=_which("osascript", "notify-send"))
    assert ok is True
    argv, kwargs = run.calls[0]
    assert argv == ["osascript", "-", "T", "B"]
    assert "on run argv" in kwargs["input"]
    assert 'sound name "Basso"' in kwargs["input"]
    assert kwargs["timeout"] == 10


def test_osascript_nonzero_is_false():
    assert notify_desktop.send("T", "B", run=FakeRun(returncode=1), which=_which("osascript")) is False


def test_notify_send_with_bus_address_when_socket_exists(tmp_path):
    sock = tmp_path / "bus"
    sock.write_text("")
    run = FakeRun()
    ok = notify_desktop.send("T", "B", run=run, which=_which("notify-send"), env={"PATH": "/x"}, bus_path=sock)
    assert ok is True
    argv, kwargs = run.calls[0]
    assert argv == ["notify-send", "--urgency=critical", "T", "B"]
    assert kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={sock}"
    assert kwargs["env"]["PATH"] == "/x"


def test_notify_send_keeps_existing_bus_address(tmp_path):
    sock = tmp_path / "bus"
    sock.write_text("")
    run = FakeRun()
    notify_desktop.send(
        "T", "B", run=run, which=_which("notify-send"), env={"DBUS_SESSION_BUS_ADDRESS": "mine"}, bus_path=sock
    )
    assert run.calls[0][1]["env"]["DBUS_SESSION_BUS_ADDRESS"] == "mine"


def test_notify_send_without_socket_leaves_env_alone(tmp_path):
    run = FakeRun()
    notify_desktop.send("T", "B", run=run, which=_which("notify-send"), env={}, bus_path=tmp_path / "none")
    assert "DBUS_SESSION_BUS_ADDRESS" not in run.calls[0][1]["env"]


def test_default_bus_path_and_env_are_used(monkeypatch):
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    run = FakeRun()
    assert notify_desktop.send("T", "B", run=run, which=_which("notify-send")) is True
    assert isinstance(run.calls[0][1]["env"], dict)


def test_neither_binary_is_false():
    run = FakeRun()
    assert notify_desktop.send("T", "B", run=run, which=_which()) is False
    assert run.calls == []


def test_timeout_and_oserror_are_false():
    for exc in (subprocess.TimeoutExpired("x", 10), OSError("boom"), subprocess.SubprocessError("x")):
        assert notify_desktop.send("T", "B", run=FakeRun(exc=exc), which=_which("osascript")) is False


def test_defaults_resolve_late(monkeypatch):
    calls = []
    monkeypatch.setattr(
        notify_desktop.subprocess, "run", lambda argv, **kw: calls.append(argv) or SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(notify_desktop.shutil, "which", lambda name: "/bin/osascript" if name == "osascript" else None)
    assert notify_desktop.send("T", "B") is True
    assert calls == [["osascript", "-", "T", "B"]]
