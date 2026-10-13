from __future__ import annotations

from datetime import timedelta

import pytest

from scout import update_outcomes as uo
from scout.scripts import self_update_run as sur
from scout.scripts.self_update import UpdateStatus
from tests.unit.self_update_fakes import NOW, FakeWorld, forbidden, make_deps, pointer


def _prior(deps, *rows):
    for ago, result, kw in rows:
        uo.append(
            deps.vault,
            uo.UpdateOutcome(
                at=NOW - ago,
                from_version="0.16.0",
                target=kw.pop("target", "0.17.0"),
                managed_by="install.sh",
                result=result,
                step=kw.pop("step", "check"),
                **kw,
            ),
        )


# --- §7.2 step 1: the app-managed engine is refused before anything else -----------------


def test_scout_app_is_refused_before_any_config_network_or_subprocess(tmp_path):
    deps, world, notified = make_deps(
        tmp_path,
        ptr=pointer("scout-app"),
        config={"auto_update": {"enabled": True, "channel": "stable"}},
        load_config=forbidden("load_config"),
        check=forbidden("check"),
        fetch_release=forbidden("fetch_release"),
        run=forbidden("run"),
        lock_busy=forbidden("lock_busy"),
        slot_due_within=forbidden("slot_due_within"),
        in_off_peak=forbidden("in_off_peak"),
        doctor_severity=forbidden("doctor_severity"),
        blocking_sidecars=forbidden("blocking_sidecars"),
        claude_bin=forbidden("claude_bin"),
    )
    for scheduled, force in ((True, False), (False, True), (False, False)):
        attempt = sur.run_update(deps, scheduled=scheduled, force=force)
        assert attempt.row is not None
        assert attempt.row.result == "not_eligible" and attempt.row.detail == "managed_by=scout-app"
        assert attempt.row.target is None
    assert world.calls == []


def test_disabled_writes_not_eligible_and_does_nothing_else(tmp_path):
    deps, world, notified = make_deps(
        tmp_path, config={"auto_update": {"enabled": False}}, check=forbidden("check"), run=forbidden("run")
    )
    attempt = sur.run_update(deps, scheduled=True)
    assert attempt.row.result == "not_eligible" and attempt.row.detail == "disabled"
    assert attempt.settled
    assert [r.result for r in uo.read(deps.vault)] == ["not_eligible"]
    assert len(notified) == 1


@pytest.mark.parametrize("enabled", ["true", 1, None])
def test_only_a_literal_true_enables(tmp_path, enabled):
    deps, _, _ = make_deps(tmp_path, config={"auto_update": {"enabled": enabled}}, check=forbidden("check"))
    assert sur.run_update(deps, scheduled=True).row.detail == "disabled"


def test_missing_auto_update_block_is_disabled(tmp_path):
    deps, _, _ = make_deps(tmp_path, config={"auto_update": "on"}, check=forbidden("check"))
    assert sur.run_update(deps, scheduled=True).row.detail == "disabled"


def test_missing_pointer(tmp_path):
    deps, world, _ = make_deps(tmp_path, ptr=None, check=forbidden("check"))
    row = sur.run_update(deps, scheduled=True).row
    assert row.detail == "pointer_mismatch" and row.managed_by == "unknown" and world.calls == []


def test_pointer_for_another_interpreter(tmp_path):
    deps, world, _ = make_deps(tmp_path, ptr=pointer(python="/other/python"), check=forbidden("check"))
    assert sur.run_update(deps, scheduled=True).row.detail == "pointer_mismatch"
    assert world.calls == []


@pytest.mark.parametrize("managed_by", ["dev", "unknown"])
def test_dev_and_unknown_are_checked_read_only(tmp_path, managed_by):
    deps, world, _ = make_deps(
        tmp_path, ptr=pointer(managed_by), fetch_release=forbidden("fetch_release"), run=forbidden("run")
    )
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "not_eligible" and row.detail == f"managed_by={managed_by}"
    assert row.target == "0.17.0" and row.from_version == "0.16.0"


def test_dev_check_failure_still_records_not_eligible(tmp_path):
    def boom():
        raise RuntimeError("offline")

    deps, _, _ = make_deps(tmp_path, ptr=pointer("dev"), check=boom)
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "not_eligible" and row.target is None


def test_other_channel(tmp_path):
    deps, _, _ = make_deps(tmp_path, config={"auto_update": {"enabled": True, "channel": "beta"}})
    assert sur.run_update(deps, scheduled=True).row.detail == "channel=beta"


def test_claude_code_is_eligible(tmp_path):
    deps, world, _ = make_deps(tmp_path, ptr=pointer("claude-code"))
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "applied" and row.managed_by == "claude-code"
    auto = next(c for c in world.calls if "auto" in c)
    assert auto[auto.index("--managed-by") + 1] == "claude-code"


# --- settings ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ({"enabled": True, "channel": "stable", "soak_hours": 48}, (True, "stable", 48.0)),
        ({"enabled": True, "soak_hours": -1}, (True, "stable", 24.0)),
        ({"enabled": True, "soak_hours": True}, (True, "stable", 24.0)),
        ({"enabled": True, "soak_hours": "x", "channel": 3}, (True, "stable", 24.0)),
        ({}, (False, "stable", 24.0)),
    ],
)
def test_auto_update_settings(block, expected):
    assert sur.auto_update_settings({"auto_update": block}) == expected


# --- the scheduled gate (§7.1, §7.5) -------------------------------------------------------


def test_off_peak_runs(tmp_path):
    deps, _, _ = make_deps(tmp_path)
    _prior(deps, (timedelta(hours=2), "up_to_date", {"target": "0.16.0"}))
    assert sur.scheduled_gate(deps, uo.read(deps.vault)) is None


@pytest.mark.parametrize(("hours", "runs"), [(35, False), (37, True)])
def test_outside_off_peak_runs_only_when_the_last_check_is_stale(tmp_path, hours, runs):
    deps, world, _ = make_deps(tmp_path, in_off_peak=lambda: False)
    _prior(
        deps,
        (timedelta(hours=hours), "up_to_date", {"target": "0.16.0"}),
        (timedelta(hours=1), "deferred", {"detail": "lock"}),  # deferrals don't count as a completed check
    )
    attempt = sur.run_update(deps, scheduled=True)
    if runs:
        assert attempt.row.result == "applied"
    else:
        assert attempt.row is None and attempt.reason == "outside the off-peak window" and not attempt.settled
        assert world.calls == []


def test_outside_off_peak_with_no_history_runs(tmp_path):
    deps, _, _ = make_deps(tmp_path, in_off_peak=lambda: False)
    assert sur.scheduled_gate(deps, []) is None


def test_one_applied_version_per_24h(tmp_path):
    deps, world, _ = make_deps(tmp_path)
    _prior(deps, (timedelta(hours=23), "applied", {"target": "0.16.0", "step": "verify"}))
    attempt = sur.run_update(deps, scheduled=True)
    assert attempt.row is None and attempt.settled and world.calls == []
    # an unscheduled run ignores the gate
    assert sur.run_update(deps, scheduled=False).row.result == "applied"


def test_applied_25h_ago_does_not_gate(tmp_path):
    deps, _, _ = make_deps(tmp_path)
    _prior(deps, (timedelta(hours=25), "applied_with_sidecars", {"target": "0.16.0", "step": "verify"}))
    assert sur.scheduled_gate(deps, uo.read(deps.vault)) is None


def test_force_skips_the_gate(tmp_path):
    deps, _, _ = make_deps(tmp_path, in_off_peak=lambda: False)
    _prior(deps, (timedelta(hours=1), "up_to_date", {"target": "0.16.0"}))
    assert sur.run_update(deps, scheduled=True, force=True).row.result == "applied"


def test_lock_busy_defers_without_a_check(tmp_path):
    deps, world, _ = make_deps(tmp_path, lock_busy=lambda: True, check=forbidden("check"))
    attempt = sur.run_update(deps, scheduled=True)
    assert attempt.row.result == "deferred" and "lock" in attempt.row.detail
    assert not attempt.settled and world.calls == []


def test_slot_due_defers(tmp_path):
    seen = []
    deps, world, _ = make_deps(tmp_path, slot_due_within=lambda w: seen.append(w) or True, check=forbidden("check"))
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "deferred" and "45 min" in row.detail and seen == [timedelta(minutes=45)]


# --- backoff (§7.5) ------------------------------------------------------------------------


@pytest.mark.parametrize(("hours", "retries"), [(23, False), (25, True)])
def test_retry_a_failed_target_after_24h(tmp_path, hours, retries):
    deps, world, _ = make_deps(tmp_path)
    _prior(deps, (timedelta(hours=hours), "failed", {"step": "venv"}))
    attempt = sur.run_update(deps, scheduled=True)
    if retries:
        assert attempt.row.result == "applied"
    else:
        assert attempt.row is None and attempt.settled and "less than 24 h" in attempt.reason
        assert "plugin update" not in world.keys()


def test_three_failures_stop_retrying_that_target(tmp_path):
    deps, world, _ = make_deps(tmp_path)
    for days in (5, 4, 3):
        _prior(deps, (timedelta(days=days), "failed", {"step": "vault"}))
    attempt = sur.run_update(deps, scheduled=True)
    assert attempt.row is None and "failed 3 times" in attempt.reason
    assert "plugin update" not in world.keys()
    # --force retries
    assert sur.run_update(deps, scheduled=True, force=True).row.result == "applied"


def test_a_newer_target_resets_the_backoff(tmp_path):
    deps, world, _ = make_deps(tmp_path, available="0.17.1", world=None)
    deps.run = FakeWorld(tmp_path, target="0.17.1")
    for days in (5, 4, 3):
        _prior(deps, (timedelta(days=days), "failed", {"step": "vault"}))
    assert sur.run_update(deps, scheduled=True).row.result == "applied"


# --- check results -------------------------------------------------------------------------


def test_up_to_date(tmp_path):
    deps, world, _ = make_deps(tmp_path, available="0.16.0", fetch_release=forbidden("fetch_release"))
    attempt = sur.run_update(deps, scheduled=True)
    assert attempt.row.result == "up_to_date" and attempt.settled and world.calls == []


def test_manifest_older_than_installed_is_up_to_date(tmp_path):
    deps, _, _ = make_deps(tmp_path, check=lambda: UpdateStatus("0.16.0", "0.15.0", False))
    assert sur.run_update(deps, scheduled=True).row.result == "up_to_date"


@pytest.mark.parametrize(
    ("release", "reason"),
    [
        (None, "missing"),
        ({"draft": True, "prerelease": False, "published_at": None}, "draft"),
        ({"draft": False, "prerelease": True, "published_at": "2026-10-01T00:00:00Z"}, "prerelease"),
    ],
)
def test_unpublished_releases_wait(tmp_path, release, reason):
    deps, world, _ = make_deps(tmp_path, release=release)
    attempt = sur.run_update(deps, scheduled=True)
    assert attempt.row.result == "waiting_release" and attempt.row.detail == reason
    assert attempt.settled and world.calls == []


@pytest.mark.parametrize(("hours", "result"), [(23, "waiting_release"), (25, "applied")])
def test_soak(tmp_path, hours, result):
    from tests.unit.self_update_fakes import published

    deps, _, _ = make_deps(tmp_path, release=published(days_ago=hours / 24))
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == result
    assert row.release_published_at == NOW - timedelta(hours=hours)


def test_soak_hours_come_from_config(tmp_path):
    from tests.unit.self_update_fakes import published

    deps, _, _ = make_deps(
        tmp_path,
        config={"auto_update": {"enabled": True, "soak_hours": 72}},
        release=published(days_ago=2),
    )
    assert sur.run_update(deps, scheduled=True).row.result == "waiting_release"


def _offline():
    raise RuntimeError("could not reach the marketplace")


def test_network_failures_defer_then_fail_on_the_third(tmp_path):
    deps, _, _ = make_deps(tmp_path, check=_offline)
    results = []
    for _ in range(4):
        results.append(sur.run_update(deps, scheduled=False).row.result)
    assert results == ["deferred", "deferred", "failed", "deferred"]
    rows = uo.read(deps.vault)
    assert all(r.step == "check" and r.detail.startswith("network: ") for r in rows)


def test_release_network_failure_defers(tmp_path):
    def unreachable(v):
        raise RuntimeError("could not reach GitHub")

    deps, world, _ = make_deps(tmp_path, fetch_release=unreachable)
    row = sur.run_update(deps, scheduled=True).row
    assert row.result == "deferred" and row.target == "0.17.0" and world.calls == []


def test_release_network_failure_third_time_fails(tmp_path):
    def unreachable(v):
        raise RuntimeError("could not reach GitHub")

    deps, _, _ = make_deps(tmp_path, fetch_release=unreachable)
    results = [sur.run_update(deps, scheduled=False).row.result for _ in range(3)]
    assert results == ["deferred", "deferred", "failed"]


def test_network_count_is_broken_by_another_row(tmp_path):
    deps, _, _ = make_deps(tmp_path, check=_offline)
    _prior(
        deps,
        (timedelta(hours=3), "deferred", {"detail": "network: x"}),
        (timedelta(hours=2), "deferred", {"detail": "a Scout session holds the vault lock"}),
        (timedelta(hours=1), "deferred", {"detail": "network: x"}),
    )
    assert sur.run_update(deps, scheduled=False).row.result == "deferred"
