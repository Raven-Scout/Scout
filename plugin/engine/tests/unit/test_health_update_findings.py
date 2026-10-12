from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health
from scout import update_outcomes as uo

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    return v


def _row(ago: timedelta, result: str, **kw) -> uo.UpdateOutcome:
    base = dict(
        at=NOW - ago,
        from_version="0.16.0",
        target="0.17.0",
        managed_by="install.sh",
        result=result,
        step="verify" if result.startswith("applied") else "check",
    )
    base.update(kw)
    return uo.UpdateOutcome(**base)


def _found(vault: Path, rows: list[uo.UpdateOutcome]) -> dict[str, health.Finding]:
    return {f.id: f for f in health.update_findings(vault, rows, now=NOW)}


def test_no_rows_no_findings(tmp_path):
    assert _found(_vault(tmp_path), []) == {}


def test_evaluate_without_update_ledger_has_no_update_findings(tmp_path):
    report = health.evaluate(_vault(tmp_path), now=NOW)
    assert not [f for f in report.findings if f.id.startswith("update.")]


def test_evaluate_reads_the_update_ledger(tmp_path):
    v = _vault(tmp_path)
    uo.append(v, _row(timedelta(hours=1), "failed", step="venv"))
    report = health.evaluate(v, now=NOW)
    assert report.severity == "red"
    assert [f.id for f in report.findings] == ["update.failed"]


def test_failed_is_red_and_names_the_step_and_log(tmp_path):
    v = _vault(tmp_path)
    rows = [
        _row(timedelta(days=3), "applied", target="0.16.0", from_version="0.15.0"),
        _row(timedelta(days=2), "failed", step="vault", log="self-update-2026-10-10.log"),
        _row(timedelta(days=1), "failed", step="verify", log="self-update-2026-10-11.log"),
    ]
    f = _found(v, rows)["update.failed"]
    assert f.severity == "red"
    assert "0.17.0" in f.summary and "verify" in f.summary
    assert "scoutctl bootstrap upgrade" in f.fix and "self-update-2026-10-11.log" in f.fix
    assert f.since == NOW - timedelta(days=2)
    assert f.source == "update ledger"


def test_failed_before_vault_says_old_engine_still_runs(tmp_path):
    f = _found(_vault(tmp_path), [_row(timedelta(hours=1), "failed", step="fetch")])["update.failed"]
    assert "still runs" in f.summary


def test_a_later_success_clears_failed(tmp_path):
    rows = [_row(timedelta(days=1), "failed", step="venv"), _row(timedelta(hours=1), "applied")]
    assert "update.failed" not in _found(_vault(tmp_path), rows)


def test_held_is_yellow(tmp_path):
    f = _found(_vault(tmp_path), [_row(timedelta(hours=1), "held", detail="install doctor is red")])["update.held"]
    assert f.severity == "yellow" and "install doctor is red" in f.summary


@pytest.mark.parametrize(("hours", "expected"), [(71, False), (73, True)])
def test_deferred_turns_yellow_after_72h(tmp_path, hours, expected):
    rows = [
        _row(timedelta(hours=hours), "deferred", detail="vault lock busy"),
        _row(timedelta(hours=1), "deferred", detail="vault lock busy"),
    ]
    found = _found(_vault(tmp_path), rows)
    assert ("update.held" in found) is expected
    if expected:
        assert found["update.held"].since == NOW - timedelta(hours=hours)


def test_deferred_run_is_broken_by_another_result(tmp_path):
    rows = [
        _row(timedelta(hours=100), "deferred"),
        _row(timedelta(hours=50), "up_to_date", target="0.16.0"),
        _row(timedelta(hours=1), "deferred"),
    ]
    assert "update.held" not in _found(_vault(tmp_path), rows)


def test_sidecars_yellow_only_when_listed_and_still_pending(tmp_path):
    v = _vault(tmp_path)
    rows = [_row(timedelta(hours=1), "applied_with_sidecars", sidecars=("SKILL.md.proposed-merge",))]
    assert "update.sidecars" not in _found(v, rows)  # resolved since
    (v / "SKILL.md.proposed-merge").write_text("x")
    f = _found(v, rows)["update.sidecars"]
    assert f.severity == "yellow" and "SKILL.md.proposed-merge" in f.summary
    assert "scoutctl bootstrap resolve" in f.fix
    # pending, but the newest row does not list it: the doctor's own warning covers it
    assert "update.sidecars" not in _found(v, [_row(timedelta(hours=1), "applied")])


@pytest.mark.parametrize(("days", "expected"), [(13, False), (15, True)])
def test_eligible_engine_behind_a_published_release(tmp_path, days, expected):
    row = _row(timedelta(hours=1), "deferred", release_published_at=NOW - timedelta(days=days))
    found = _found(_vault(tmp_path), [row])
    assert ("update.behind" in found) is expected
    if expected:
        assert found["update.behind"].severity == "note"


def test_waiting_release_is_not_behind(tmp_path):
    row = _row(timedelta(hours=1), "waiting_release", release_published_at=None)
    assert "update.behind" not in _found(_vault(tmp_path), [row])


@pytest.mark.parametrize("managed_by", ["dev", "unknown"])
def test_dev_or_unknown_behind_at_all_is_a_note(tmp_path, managed_by):
    row = _row(timedelta(hours=1), "not_eligible", managed_by=managed_by, detail=f"managed_by={managed_by}")
    f = _found(_vault(tmp_path), [row])["update.behind"]
    assert f.severity == "note" and managed_by in f.summary and "0.17.0" in f.summary


def test_not_eligible_without_target_or_not_behind(tmp_path):
    v = _vault(tmp_path)
    assert _found(v, [_row(timedelta(hours=1), "not_eligible", target=None, detail="disabled")]) == {}
    same = _row(timedelta(hours=1), "not_eligible", managed_by="dev", target="0.16.0")
    assert _found(v, [same]) == {}


def test_unparseable_target_is_never_behind(tmp_path):
    row = _row(timedelta(hours=1), "not_eligible", managed_by="dev", target="next")
    assert _found(_vault(tmp_path), [row]) == {}


def test_note_never_changes_severity(tmp_path):
    v = _vault(tmp_path)
    uo.append(v, _row(timedelta(hours=1), "not_eligible", managed_by="dev"))
    report = health.evaluate(v, now=NOW)
    assert report.severity == "green" and [f.id for f in report.findings] == ["update.behind"]
