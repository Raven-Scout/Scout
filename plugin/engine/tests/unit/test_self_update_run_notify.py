from __future__ import annotations

from datetime import timedelta

from scout import health_notify
from scout import update_outcomes as uo
from scout.scripts import self_update_run as sur
from tests.unit.self_update_fakes import NOW


def _row(result: str, **kw) -> uo.UpdateOutcome:
    return uo.UpdateOutcome(
        at=kw.pop("at", NOW),
        from_version="0.16.0",
        target="0.17.0",
        managed_by="install.sh",
        result=result,
        step=kw.pop("step", "verify"),
        **kw,
    )


def test_first_applied_names_the_setting_and_how_to_turn_it_off():
    (push,) = sur.info_pushes(_row("applied"), [])
    assert push.kind == "info" and push.severity == "green"
    assert "0.17.0" in push.title
    assert "auto_update.enabled" in push.body and "scoutctl config set-auto-update --disabled" in push.body


def test_later_applied_is_silent():
    prior = [_row("applied", at=NOW - timedelta(days=20))]
    assert sur.info_pushes(_row("applied"), prior) == []


def test_applied_with_sidecars_pushes_the_files_once():
    prior = [_row("applied", at=NOW - timedelta(days=20))]
    (push,) = sur.info_pushes(_row("applied_with_sidecars", sidecars=("SKILL.md.proposed-merge",)), prior)
    assert push.kind == "info" and "SKILL.md.proposed-merge" in push.body
    assert "scoutctl bootstrap drift" in push.body


def test_first_ever_apply_with_sidecars_pushes_both():
    pushes = sur.info_pushes(_row("applied_with_sidecars", sidecars=("SKILL.md.proposed-merge",)), [])
    assert [p.finding_id for p in pushes] == ["update.applied", "update.sidecars"]


def test_other_results_push_nothing_themselves():
    for result in ("failed", "held", "deferred", "up_to_date", "waiting_release", "not_eligible"):
        assert sur.info_pushes(_row(result, step="check"), []) == []


def test_default_notify_fans_out_info_then_runs_the_health_notifier(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(health_notify, "fan_out", lambda push, *, vault, now: calls.append(("fan_out", push.finding_id)))
    monkeypatch.setattr(health_notify, "notify", lambda vault: calls.append(("notify", vault)))
    sur.default_notify(tmp_path)(_row("applied"), [])
    assert calls == [("fan_out", "update.applied"), ("notify", tmp_path)]


def test_a_failed_update_reaches_a_channel_as_red(tmp_path):
    """End to end through the real notifier with an injected channel: no osascript, no Telegram."""
    vault = tmp_path / "vault"
    (vault / ".scout-logs").mkdir(parents=True)
    (vault / ".scout-state").mkdir()
    uo.append(vault, _row("failed", step="venv", at=NOW))
    seen = []
    sent = health_notify.notify(
        vault,
        now=NOW + timedelta(minutes=1),
        channels=[lambda push: seen.append(push) or {"channel": "test", "ok": True, "detail": ""}],
    )
    assert sent and seen[0].severity == "red" and "update.failed" in seen[0].finding_ids
