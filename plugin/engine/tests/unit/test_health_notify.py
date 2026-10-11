"""Notifier state machine: red / reminder / recovered, plus `scoutctl health notify`."""

from __future__ import annotations

import fcntl
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import health, health_notify
from scout.cli import app

runner = CliRunner()
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _finding(fid: str, severity: str = "red") -> health.Finding:
    return health.Finding(
        id=fid,
        severity=severity,
        since=NOW - timedelta(hours=3),
        summary=f"{fid} is broken",
        fix=f"fix {fid}",
        source="ledger",
    )


def _report(*findings: health.Finding, now: datetime = NOW) -> health.RunHealth:
    return health.build_report(list(findings), now=now, last_ok_at=None)


def _vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def _run(report, state, now):
    pushes, new_state = health_notify.plan(report, state, now=now)
    return [p.kind for p in pushes], pushes, new_state


# state after a single red "a" was first pushed at NOW
_AFTER_RED_A = health_notify.plan(_report(_finding("a")), {}, now=NOW)[1]
_AFTER_TWO_REDS = health_notify.plan(_report(_finding("a"), _finding("b")), {}, now=NOW)[1]


@pytest.mark.parametrize(
    ("name", "report", "state", "delta", "expected_kinds"),
    [
        ("new red", _report(_finding("a")), {}, timedelta(0), ["new"]),
        ("same red 23h later", _report(_finding("a")), _AFTER_RED_A, timedelta(hours=23), []),
        ("same red 25h later", _report(_finding("a")), _AFTER_RED_A, timedelta(hours=25), ["reminder"]),
        ("red to green", _report(), _AFTER_RED_A, timedelta(hours=1), ["recovered"]),
        ("red to yellow", _report(_finding("y", "yellow")), _AFTER_RED_A, timedelta(hours=1), ["recovered"]),
        ("yellow only", _report(_finding("y", "yellow")), {}, timedelta(0), []),
        ("two reds", _report(_finding("a"), _finding("b")), {}, timedelta(0), ["new", "new"]),
        ("one red gone, other stays", _report(_finding("b")), _AFTER_TWO_REDS, timedelta(hours=1), []),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_state_machine(name, report, state, delta, expected_kinds):
    now = NOW + delta
    kinds, pushes, new = _run(report, state, now)
    assert kinds == expected_kinds, name
    if name == "new red":
        assert new["findings"]["a"]["pushes"] == 1
        assert new["findings"]["a"]["last_pushed"] == health.iso_z(NOW)
        assert pushes[0].title == "Scout: a is broken"
        assert pushes[0].body == "a is broken\nFix: fix a"
    if name == "same red 25h later":
        assert "1 day" in pushes[0].body
        assert pushes[0].body.startswith("Still failing after 1 day: a is broken\nFix: fix a")
        assert new["findings"]["a"]["pushes"] == 2
    if name == "red to green":
        assert new["findings"] == {}
        assert new["last_overall"] == "green"
        assert pushes[0].severity == "green"
        assert pushes[0].finding_id == "recovered"
        assert pushes[0].body == "Scout runs are healthy again."
    if name == "red to yellow":
        assert pushes[0].severity == "yellow"
        assert pushes[0].body == "Scout runs are healthy again; remaining warnings: 1"
        assert new["last_overall"] == "yellow"
        assert set(new["findings"]) == {"y"}
        assert new["findings"]["y"]["last_pushed"] is None
    if name == "yellow only":
        assert new["findings"]["y"]["last_pushed"] is None
        assert new["findings"]["y"]["first_seen"] == health.iso_z(now)
        assert new["last_overall"] == "yellow"
    if name == "two reds":
        assert {p.finding_id for p in pushes} == {"a", "b"}
    if name == "one red gone, other stays":
        assert set(new["findings"]) == {"b"}
        assert new["last_overall"] == "red"


def test_reminder_elapsed_wording():
    state = _AFTER_RED_A
    # 3 days later
    kinds, pushes, _ = _run(_report(_finding("a")), state, NOW + timedelta(days=3, hours=1))
    assert kinds == ["reminder"]
    assert "Still failing after 3 days" in pushes[0].body
    # first_seen long ago but last_pushed just past 24h: hours wording when under a day
    state = json.loads(json.dumps(state))
    state["findings"]["a"]["first_seen"] = health.iso_z(NOW - timedelta(hours=1))
    state["findings"]["a"]["last_pushed"] = health.iso_z(NOW - timedelta(hours=25))
    _, pushes, _ = _run(_report(_finding("a")), state, NOW)
    assert "after 1 hour:" in pushes[0].body


def test_title_truncates_summary():
    long = health.Finding("a", "red", None, "x" * 100, "f", "ledger")
    pushes, _ = health_notify.plan(_report(long), {}, now=NOW)
    assert pushes[0].title == "Scout: " + "x" * 60


def test_unparseable_stamps_are_treated_as_new_pushes():
    state = {"findings": {"a": {"first_seen": "garbage", "last_pushed": "garbage", "pushes": "x"}}}
    pushes, new = health_notify.plan(_report(_finding("a")), state, now=NOW)
    assert [p.kind for p in pushes] == ["new"]
    assert new["findings"]["a"]["first_seen"] == health.iso_z(NOW)


def test_red_entry_never_pushed_pushes_new():
    state = {"findings": {"a": {"first_seen": health.iso_z(NOW), "last_pushed": None, "pushes": 0}}}
    pushes, new = health_notify.plan(_report(_finding("a")), state, now=NOW)
    assert [p.kind for p in pushes] == ["new"]
    assert new["findings"]["a"]["first_seen"] == health.iso_z(NOW)


def test_malformed_findings_in_state_ignored():
    pushes, _ = health_notify.plan(_report(_finding("a")), {"findings": "nope"}, now=NOW)
    assert [p.kind for p in pushes] == ["new"]
    pushes, _ = health_notify.plan(_report(_finding("a")), {"findings": {"a": "nope"}}, now=NOW)
    assert [p.kind for p in pushes] == ["new"]


# ----- state file ---------------------------------------------------------


def test_load_state_missing_and_non_dict(tmp_path):
    assert health_notify.load_state(tmp_path) == {}
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / health_notify.STATE_FILE).write_text("[1]")
    assert health_notify.load_state(tmp_path) == {}


def test_save_state_roundtrip_and_oserror_swallowed(tmp_path):
    health_notify.save_state(tmp_path, {"last_overall": "red"})
    assert health_notify.load_state(tmp_path) == {"last_overall": "red"}
    blocker = tmp_path / "file"
    blocker.write_text("x")
    health_notify.save_state(blocker, {"a": 1})  # parent is a file: OSError, swallowed


def _fake_channel(name: str, ok: bool = True):
    def channel(push):
        return {"channel": name, "ok": ok, "detail": "sent"}

    channel.__name__ = f"{name}_channel"
    return channel


def test_corrupt_state_is_treated_as_empty(tmp_path):
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / health_notify.STATE_FILE).write_text("{")
    out = health_notify.notify(tmp_path, now=NOW, channels=[_fake_channel("fake")], report=_report(_finding("a")))
    assert [p.kind for p, _ in out] == ["new"]


def test_notify_records_channel_results(tmp_path):
    chans = [_fake_channel("one"), _fake_channel("two", ok=False)]
    out = health_notify.notify(tmp_path, now=NOW, channels=chans, report=_report(_finding("a")))
    expected = [
        {"channel": "one", "ok": True, "detail": "sent"},
        {"channel": "two", "ok": False, "detail": "sent"},
    ]
    assert out[0][1] == expected
    state = health_notify.load_state(tmp_path)
    assert state["findings"]["a"]["last_results"] == expected
    # second call within 24h: nothing more is sent
    assert (
        health_notify.notify(tmp_path, now=NOW + timedelta(hours=1), channels=chans, report=_report(_finding("a")))
        == []
    )


def test_notify_records_recovered_results(tmp_path):
    chans = [_fake_channel("one")]
    health_notify.notify(tmp_path, now=NOW, channels=chans, report=_report(_finding("a")))
    out = health_notify.notify(tmp_path, now=NOW + timedelta(hours=1), channels=chans, report=_report())
    assert [p.kind for p, _ in out] == ["recovered"]
    state = health_notify.load_state(tmp_path)
    assert state["last_recovered"] == [{"channel": "one", "ok": True, "detail": "sent"}]
    assert state["last_overall"] == "green"


def test_notify_evaluates_when_no_report(tmp_path, monkeypatch):
    calls = []

    def fake_evaluate(vault, *, now=None):
        calls.append(vault)
        return _report(_finding("a"))

    monkeypatch.setattr(health, "evaluate", fake_evaluate)
    out = health_notify.notify(tmp_path, now=NOW, channels=[_fake_channel("fake")])
    assert calls == [tmp_path]
    assert [p.kind for p, _ in out] == ["new"]


def test_notify_never_raises(tmp_path, monkeypatch):
    def boom(vault, *, now=None):
        raise RuntimeError("evaluate exploded")

    monkeypatch.setattr(health, "evaluate", boom)
    assert health_notify.notify(tmp_path, now=NOW, channels=[_fake_channel("fake")]) == []


def test_notify_uses_default_now(tmp_path):
    out = health_notify.notify(tmp_path, channels=[_fake_channel("fake")], report=_report(_finding("a")))
    assert [p.kind for p, _ in out] == ["new"]


# ----- advisory lock ------------------------------------------------------


def test_held_lock_returns_empty_quickly_without_channel_calls(tmp_path):
    (tmp_path / ".scout-state").mkdir()
    lock_path = tmp_path / ".scout-state" / "health-notify.lock"
    calls = []

    def channel(push):
        calls.append(push)
        return {"channel": "x", "ok": True, "detail": "sent"}

    with lock_path.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        start = time.monotonic()
        out = health_notify.notify(
            tmp_path,
            now=NOW,
            channels=[channel],
            report=_report(_finding("a")),
            lock_timeout=0.3,
            sleep=lambda s: time.sleep(0.05),
        )
        assert time.monotonic() - start < 2
    assert out == []
    assert calls == []
    assert lock_path.exists()  # never deleted
    assert not (tmp_path / health_notify.STATE_FILE).exists()


def test_lock_is_released_after_notify(tmp_path):
    health_notify.notify(tmp_path, now=NOW, channels=[_fake_channel("f")], report=_report())
    lock_path = tmp_path / ".scout-state" / "health-notify.lock"
    assert lock_path.exists()
    with lock_path.open("w") as again:
        fcntl.flock(again, fcntl.LOCK_EX | fcntl.LOCK_NB)  # would raise if still held


# ----- CLI ----------------------------------------------------------------


def _red_vault(tmp_path, monkeypatch) -> Path:
    v = _vault(tmp_path, monkeypatch)
    now = int(time.time())
    rows = [
        {
            "slot": "morning-briefing",
            "mode": "scout",
            "started_at": now - 600 * (i + 1),
            "finished_at": now - 600 * (i + 1) + 60,
            "duration_s": 60,
            "exit_code": 1,
            "failure_class": "unknown",
            "log": f"scout-{i}.log",
        }
        for i in range(2)
    ]
    (v / ".scout-logs" / "run-outcomes.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return v


def test_cli_notify_quiet_red_vault_exits_zero(tmp_path, monkeypatch):
    v = _red_vault(tmp_path, monkeypatch)
    sent = []

    def fake_fan_out(push, *, vault, now, channels=None):
        sent.append(push)
        return [{"channel": "fake", "ok": True, "detail": "sent"}]

    monkeypatch.setattr(health_notify, "fan_out", fake_fan_out)
    result = runner.invoke(app, ["health", "notify", "--quiet"])
    assert result.exit_code == 0, result.output
    assert sent
    assert "run health:" not in result.stdout
    assert health_notify.load_state(v)["last_overall"] == "red"


def test_cli_notify_prints_one_line_per_push(tmp_path, monkeypatch):
    _red_vault(tmp_path, monkeypatch)
    monkeypatch.setattr(
        health_notify,
        "fan_out",
        lambda push, *, vault, now, channels=None: [
            {"channel": "desktop", "ok": True, "detail": "sent"},
            {"channel": "telegram", "ok": False, "detail": "x"},
        ],
    )
    result = runner.invoke(app, ["health", "notify"])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith("pushed ")]
    assert len(lines) >= 1
    assert lines[0].startswith("pushed new ")
    assert lines[0].endswith(": desktop")


def test_cli_notify_no_push_prints_nothing_with_quiet(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["health", "notify", "--quiet"])
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_cli_notify_no_push_says_so_without_quiet(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    result = runner.invoke(app, ["health", "notify"])
    assert result.exit_code == 0
    assert "nothing to push" in result.stdout


def test_cli_notify_failing_fan_out_still_exits_zero(tmp_path, monkeypatch):
    _red_vault(tmp_path, monkeypatch)

    def broken(push, *, vault, now, channels=None):
        raise RuntimeError("every channel down")

    monkeypatch.setattr(health_notify, "fan_out", broken)
    result = runner.invoke(app, ["health", "notify", "--quiet"])
    assert result.exit_code == 0, result.output


def _failing_channel(push):
    return {"channel": "bad", "ok": False, "detail": "error"}


@pytest.mark.parametrize("channels", [[_failing_channel], []], ids=["all-fail", "no-channels"])
def test_undelivered_new_push_is_retried(tmp_path, channels):
    rep = _report(_finding("a"))
    first = health_notify.notify(tmp_path, now=NOW, channels=channels, report=rep)
    assert [p.kind for p, _ in first] == ["new"]
    entry = health_notify.load_state(tmp_path)["findings"]["a"]
    assert entry["last_pushed"] is None
    assert entry["pushes"] == 0
    again = health_notify.notify(tmp_path, now=NOW + timedelta(hours=1), channels=[_fake_channel("ok")], report=rep)
    assert [p.kind for p, _ in again] == ["new"]
    assert health_notify.load_state(tmp_path)["findings"]["a"]["pushes"] == 1


def test_undelivered_reminder_keeps_prior_stamp(tmp_path):
    rep = _report(_finding("a"))
    health_notify.notify(tmp_path, now=NOW, channels=[_fake_channel("ok")], report=rep)
    later = NOW + timedelta(hours=25)
    out = health_notify.notify(tmp_path, now=later, channels=[_failing_channel], report=rep)
    assert [p.kind for p, _ in out] == ["reminder"]
    entry = health_notify.load_state(tmp_path)["findings"]["a"]
    assert entry["last_pushed"] == health.iso_z(NOW)
    assert entry["pushes"] == 1
    retry = health_notify.notify(tmp_path, now=later + timedelta(hours=1), channels=[_fake_channel("ok")], report=rep)
    assert [p.kind for p, _ in retry] == ["reminder"]


def test_duplicate_telegram_result_counts_as_delivered(tmp_path):
    def dup(push):
        return {"channel": "telegram", "ok": True, "detail": "duplicate"}

    health_notify.notify(tmp_path, now=NOW, channels=[dup], report=_report(_finding("a")))
    entry = health_notify.load_state(tmp_path)["findings"]["a"]
    assert entry["last_pushed"] == health.iso_z(NOW)
    assert entry["pushes"] == 1


def test_undelivered_with_malformed_prior_state(tmp_path):
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / health_notify.STATE_FILE).write_text(json.dumps({"findings": "bad"}))
    health_notify.notify(tmp_path, now=NOW, channels=[_failing_channel], report=_report(_finding("a")))
    assert health_notify.load_state(tmp_path)["findings"]["a"]["pushes"] == 0


def test_cli_notify_unresolvable_vault_exits_zero(monkeypatch):
    from scout import paths

    def boom(explicit=None):
        raise OSError("no home")

    monkeypatch.setattr(paths, "data_dir", boom)
    result = runner.invoke(app, ["health", "notify"])
    assert result.exit_code == 0, result.output
    assert "cannot resolve the vault" in result.output


def test_last_recovered_is_carried_forward():
    state = {"last_overall": "green", "last_recovered": [{"channel": "x", "ok": True, "detail": "sent"}]}
    _, new = health_notify.plan(_report(), state, now=NOW)
    assert new["last_recovered"] == state["last_recovered"]


def test_cli_notify_does_not_run_callback_evaluation(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    calls = []
    real = health.evaluate

    def counting(vault, *, now=None):
        calls.append(1)
        return real(vault, now=now)

    monkeypatch.setattr(health, "evaluate", counting)
    result = runner.invoke(app, ["health", "notify"])
    assert result.exit_code == 0
    assert "run health:" not in result.stdout
    assert len(calls) == 1  # notify's own evaluation only, not the group callback's
