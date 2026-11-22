from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.lab import meter

FAKE = Path(__file__).parent.parent / "fixtures" / "lab" / "fake-claude.sh"
runner = CliRunner()
ALLOWED_ENV = {"PATH", "HOME", "LANG", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}
# The shell the fake runs under may add its own bookkeeping variables.
SHELL_ENV = {"PWD", "SHLVL", "_", "OLDPWD"}


@pytest.fixture
def d() -> Iterator[Path]:
    p = Path(tempfile.mkdtemp(dir="/tmp"))
    p.chmod(0o700)
    yield p
    shutil.rmtree(p, ignore_errors=True)


def _server(d: Path, cap: float = 1.0) -> meter.MeterServer:
    return meter.MeterServer(d / "m.sock", d / "ledger.jsonl", cap, claude_argv=(str(FAKE), str(d / "calls.log")))


def _env(d: Path) -> dict[str, str]:
    return {meter.ENV_METER: str(d / "m.sock")}


def _ledger(d: Path) -> list[dict[str, object]]:
    return [json.loads(x) for x in (d / "ledger.jsonl").read_text().splitlines()]


def test_call_returns_text_and_records_cost(d: Path) -> None:
    s = _server(d)
    s.start()
    try:
        r = meter.meter_call("hi", env=_env(d))
    finally:
        s.stop()
    assert r["ok"] is True and r["text"] == "echo:hi"
    assert r["cost_usd"] == 0.4 and r["spent_usd"] == 0.4
    rows = _ledger(d)
    assert len(rows) == 1 and rows[0]["ok"] is True and rows[0]["cost_usd"] == 0.4 and "at" in rows[0]
    assert not (d / "m.sock").exists()


def test_socket_mode_0600(d: Path) -> None:
    s = _server(d)
    s.start()
    try:
        assert (d / "m.sock").stat().st_mode & 0o777 == 0o600
    finally:
        s.stop()


def test_third_call_refused_at_cap_one_dollar(d: Path) -> None:
    s = _server(d)
    s.start()
    try:
        r1 = meter.meter_call("a", env=_env(d))
        r2 = meter.meter_call("b", env=_env(d))
        r3 = meter.meter_call("c", env=_env(d))
    finally:
        s.stop()
    assert r1["ok"] and r2["ok"]
    assert r3["ok"] is False and r3["refused"] == "cap" and r3["cap_usd"] == 1.0
    assert r3["spent_usd"] == pytest.approx(0.8)
    assert len(_ledger(d)) == 2


def test_spent_survives_restart(d: Path) -> None:
    (d / "ledger.jsonl").write_text(json.dumps({"at": "x", "cost_usd": 1.2, "ok": True}) + "\n")
    s = _server(d)
    s.start()
    try:
        r = meter.meter_call("a", env=_env(d))
    finally:
        s.stop()
    assert r["ok"] is False and r["refused"] == "cap"
    assert len(_ledger(d)) == 1
    assert not (d / "calls.log").exists()


def test_claude_argv_cwd_and_env_are_locked_down(d: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_X", "leak")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    s = _server(d, cap=10)
    s.start()
    try:
        meter.meter_call("hello", model="claude-x_1.2:3", env=_env(d))
        meter.meter_call("hello2", env=_env(d))
    finally:
        s.stop()
    blocks = (d / "calls.log").read_text().split("END\n")[:-1]
    assert len(blocks) == 2
    lines = blocks[0].splitlines()
    argv = lines[0].removeprefix("ARGV:")
    assert argv == (
        "-p --output-format json --restricted --tools  --strict-mcp-config --no-session-persistence"
        " --disable-slash-commands --model claude-x_1.2:3"
    )
    assert "--model" not in blocks[1].splitlines()[0]
    cwd = Path(lines[1].removeprefix("CWD:"))
    assert not cwd.exists()
    env_lines = dict(x.removeprefix("ENV:").split("=", 1) for x in lines if x.startswith("ENV:") and "=" in x)
    assert os.path.realpath(env_lines["HOME"]) == str(cwd)
    assert env_lines["HOME"] != os.environ.get("HOME")
    assert not Path(env_lines["HOME"]).exists()
    keys = {x.removeprefix("ENV:").split("=", 1)[0] for x in lines if x.startswith("ENV:")}
    assert "SCOUT_X" not in keys
    assert keys <= ALLOWED_ENV | SHELL_ENV
    assert "CLAUDE_CODE_OAUTH_TOKEN" in keys


def test_cwd_is_empty_dir(d: Path) -> None:
    wrapper = d / "w.sh"
    wrapper.write_text('#!/bin/sh\nls -A > "$1.ls"\nshift\nexec "$@"\n')
    wrapper.chmod(0o755)
    s = meter.MeterServer(
        d / "m.sock", d / "ledger.jsonl", 10, claude_argv=(str(wrapper), str(d / "o"), str(FAKE), str(d / "c.log"))
    )
    s.start()
    try:
        meter.meter_call("x", env=_env(d))
    finally:
        s.stop()
    assert (d / "o.ls").read_text() == ""


@pytest.mark.parametrize("model", ["a b", "x;y", "a" * 65, "--help\n", "$(id)"])
def test_bad_model_rejected_without_running(d: Path, model: str) -> None:
    s = _server(d)
    s.start()
    try:
        r = meter.meter_call("x", model=model, env=_env(d))
    finally:
        s.stop()
    assert r["ok"] is False and "model" in str(r["error"])
    assert not (d / "calls.log").exists()
    assert not (d / "ledger.jsonl").exists()


def test_protocol_rejections(d: Path) -> None:
    s = _server(d)
    s.start()

    def raw(payload: bytes) -> dict[str, object]:
        c = socket.socket(socket.AF_UNIX)
        c.connect(str(d / "m.sock"))
        c.sendall(payload)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = c.recv(4096)
            if not chunk:
                break
            buf += chunk
        c.close()
        out: dict[str, object] = json.loads(buf)
        return out

    try:
        assert raw(b"{not json\n")["ok"] is False
        assert raw(b'{"v":2,"prompt":"x"}\n')["ok"] is False
        assert raw(b'{"v":1,"prompt":5}\n')["ok"] is False
        big = json.dumps({"v": 1, "prompt": "x" * 200_001, "model": None}).encode() + b"\n"
        assert raw(big)["ok"] is False
    finally:
        s.stop()
    assert not (d / "calls.log").exists()


@pytest.mark.parametrize("prompt", ["FAILCALL", "BADJSON", "NOCOST", "NEGCOST", "SLEEPCALL", "BADUTF8"])
def test_ambiguous_call_charges_remaining_budget(d: Path, prompt: str) -> None:
    s = meter.MeterServer(
        d / "m.sock", d / "ledger.jsonl", 1.0, claude_argv=(str(FAKE), str(d / "calls.log")), call_timeout_s=0.5
    )
    s.start()
    try:
        ok1 = meter.meter_call("fine", env=_env(d))  # 0.4 spent
        r1 = meter.meter_call(prompt, env=_env(d))
        r2 = meter.meter_call("after", env=_env(d))
    finally:
        s.stop()
    assert ok1["ok"] is True
    assert "refused" not in r1
    rows = _ledger(d)
    assert len(rows) == 2
    assert rows[1]["cost_usd"] == pytest.approx(0.6)
    assert r2["ok"] is False and r2["refused"] == "cap"
    assert len(rows) == 2


def test_negative_ledger_rows_ignored(d: Path) -> None:
    (d / "ledger.jsonl").write_text(json.dumps({"at": "x", "cost_usd": -50, "ok": True}) + "\n")
    s = _server(d)
    s.start()
    try:
        r = meter.meter_call("a", env=_env(d))
    finally:
        s.stop()
    assert r["ok"] is True and r["spent_usd"] == 0.4


def test_unreachable_meter_raises(d: Path) -> None:
    with pytest.raises(meter.MeterUnavailable):
        meter.meter_call("x", env=_env(d))
    with pytest.raises(meter.MeterUnavailable):
        meter.meter_call("x", env={})


def test_cli_exit_codes(d: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pf = d / "p.txt"
    pf.write_text("hello")
    call = ["lab", "experiment", "call", "--prompt-file", str(pf)]
    monkeypatch.setenv(meter.ENV_METER, str(d / "m.sock"))
    assert runner.invoke(cli.app, call).exit_code == 4  # nothing listening
    s = _server(d, cap=0.5)
    s.start()
    try:
        r = runner.invoke(cli.app, call)
        assert r.exit_code == 0 and r.stdout.strip() == "echo:hello"
        assert runner.invoke(cli.app, call).exit_code == 3  # 0.4 spent + another 0.4 would pass the 0.5 cap
    finally:
        s.stop()
    pf.write_text("FAILCALL")
    s = _server(d, cap=50)
    s.start()
    try:
        assert runner.invoke(cli.app, call).exit_code == 4
    finally:
        s.stop()
    monkeypatch.delenv(meter.ENV_METER)
    assert runner.invoke(cli.app, call).exit_code == 4
    assert os.environ.get(meter.ENV_METER) is None
