from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    return v


def _row(at: datetime, *, code: int = 0, klass: str = "ok", slot: str = "morning-briefing", **extra) -> dict:
    started = int((at - timedelta(minutes=5)).timestamp())
    return {
        "slot": slot,
        "mode": "scout",
        "started_at": started,
        "finished_at": int(at.timestamp()),
        "duration_s": 300,
        "exit_code": code,
        "failure_class": klass,
        "log": f"scout-{started}.log",
        **extra,
    }


def _ledger(vault: Path, rows: list[dict | str]) -> None:
    lines = [r if isinstance(r, str) else json.dumps(r) for r in rows]
    (vault / ".scout-logs" / "run-outcomes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ids(report: health.RunHealth) -> dict[str, str]:
    return {f.id: f.severity for f in report.findings}


def test_no_ledger_and_no_logs_is_green(tmp_path):
    report = health.evaluate(_vault(tmp_path), now=NOW)
    assert report.severity == "green" and report.findings == () and report.last_ok_at is None


def test_one_failure_is_yellow_two_are_red(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=5)), _row(NOW - timedelta(hours=2), code=1, klass="network")])
    assert _ids(health.evaluate(v, now=NOW)) == {"runs.failing": "yellow"}
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=5)),
            _row(NOW - timedelta(hours=3), code=1, klass="network"),
            _row(NOW - timedelta(hours=2), code=1, klass="unknown"),
        ],
    )
    report = health.evaluate(v, now=NOW)
    assert _ids(report) == {"runs.failing": "red"}
    assert report.findings[0].since == NOW - timedelta(hours=5)
    assert "last 2 scheduled run(s) failed (unknown)" in report.findings[0].summary


def test_a_success_ends_the_streak(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=3), code=1), _row(NOW - timedelta(hours=1))])
    assert health.evaluate(v, now=NOW).severity == "green"


def test_skips_neither_break_nor_extend_the_streak(tmp_path):
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=4), code=1),
            _row(NOW - timedelta(hours=3), outcome="skipped_lock"),
            _row(NOW - timedelta(hours=2), code=1),
        ],
    )
    assert _ids(health.evaluate(v, now=NOW)) == {"runs.failing": "red"}


def test_the_233_shape_is_one_auth_finding_with_the_count(tmp_path):
    """72 oauth_expired rows: one red finding (Q-C), carrying the count and last success."""
    v = _vault(tmp_path)
    ok_at = NOW - timedelta(days=21)
    rows = [_row(ok_at)] + [
        _row(ok_at + timedelta(hours=7 * (i + 1)), code=1, klass="oauth_expired") for i in range(72)
    ]
    _ledger(v, rows)
    report = health.evaluate(v, now=NOW)
    assert _ids(report) == {"auth.expired": "red"}
    f = report.findings[0]
    assert f.since == ok_at and "72 failed run(s)" in f.summary and health.iso_z(ok_at) in f.summary


def test_auth_is_judged_on_the_newest_non_skip_row(tmp_path):
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=3), code=1, klass="oauth_expired"),
            _row(NOW - timedelta(hours=2), code=1, klass="network"),
        ],
    )
    assert _ids(health.evaluate(v, now=NOW)) == {"runs.failing": "red"}


def _failing(report: health.RunHealth) -> health.Finding:
    (finding,) = [f for f in report.findings if f.id == "runs.failing"]
    return finding


def test_a_usage_limit_with_a_named_reset_says_when_scout_resumes(tmp_path):
    """#407: a session limit lifts on its own; the finding (and the push built
    from it) says when, instead of pointing at the doctor."""
    v = _vault(tmp_path)
    resets = int((NOW + timedelta(hours=3)).timestamp())
    _ledger(v, [_row(NOW - timedelta(hours=1), code=1, klass="rate_limited", resets_at=resets)])

    finding = _failing(health.evaluate(v, now=NOW))

    assert "Claude usage limit" in finding.summary
    assert "resumes after 2026-10-11T15:00:00Z" in finding.summary
    assert finding.fix.startswith("Nothing to fix")


@pytest.mark.parametrize(
    "resets_at",
    [
        pytest.param(int((NOW - timedelta(minutes=10)).timestamp()), id="already-passed"),
        pytest.param("later", id="unreadable"),
        pytest.param(10**20, id="out-of-range"),
        pytest.param(None, id="absent"),
    ],
)
def test_a_usage_limit_without_a_future_reset_reads_as_before(tmp_path, resets_at):
    v = _vault(tmp_path)
    extra = {} if resets_at is None else {"resets_at": resets_at}
    _ledger(v, [_row(NOW - timedelta(hours=1), code=1, klass="rate_limited", **extra)])

    finding = _failing(health.evaluate(v, now=NOW))

    assert finding.summary == "The last 1 scheduled run(s) failed (rate_limited)."
    assert finding.fix.startswith("Run scoutctl bootstrap doctor")


def test_a_reset_on_an_older_row_does_not_speak_for_a_newer_failure(tmp_path):
    v = _vault(tmp_path)
    resets = int((NOW + timedelta(hours=3)).timestamp())
    _ledger(
        v,
        [
            _row(NOW - timedelta(hours=2), code=1, klass="rate_limited", resets_at=resets),
            _row(NOW - timedelta(hours=1), code=1, klass="network"),
        ],
    )

    assert "resumes" not in _failing(health.evaluate(v, now=NOW)).summary


def test_log_fallback_only_without_a_ledger(tmp_path):
    v = _vault(tmp_path)
    (v / ".scout-logs" / "scout-2026-10-11_08-00.log").write_text(
        "...\nFailed to authenticate. API Error: 401\n", encoding="utf-8"
    )
    report = health.evaluate(v, now=NOW)
    assert _ids(report) == {"auth.expired": "red"} and report.findings[0].source == "log"
    _ledger(v, [_row(NOW - timedelta(hours=1))])
    assert health.evaluate(v, now=NOW).severity == "green"


@pytest.mark.parametrize(
    "marker",
    [
        "=== Authentication failure (HTTP 401/403)",
        "Failed to authenticate. API Error: 403",
        "Invalid authentication credentials",
        "Failed to authenticate: OAuth session expired and could not be refreshed",
    ],
)
def test_log_fallback_markers(tmp_path, marker):
    v = _vault(tmp_path)
    (v / ".scout-logs" / "dreaming-2026-10-11_22-00.log").write_text(f"x\n{marker}\n", encoding="utf-8")
    assert "auth.expired" in _ids(health.evaluate(v, now=NOW))


def test_a_session_quoting_an_error_mid_line_is_not_an_auth_failure(tmp_path):
    v = _vault(tmp_path)
    (v / ".scout-logs" / "scout-2026-10-11_08-00.log").write_text(
        "I saw 'Failed to authenticate' in a ticket\n", encoding="utf-8"
    )
    assert health.evaluate(v, now=NOW).severity == "green"


def test_a_bare_401_in_a_run_log_is_not_an_auth_failure(tmp_path):
    """Ported from the doctor's log check: a session that merely *writes about*
    HTTP 401 can't trip the detector (M8)."""
    v = _vault(tmp_path)
    (v / ".scout-logs" / "scout-2026-05-28.log").write_text(
        "The vendor API returned 401 for the old token; noted in the KB.\n", encoding="utf-8"
    )
    assert health.evaluate(v, now=NOW).findings == ()


def test_three_lock_skips_in_a_day_are_yellow(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=h), outcome="skipped_lock") for h in (20, 10, 1)])
    report = health.evaluate(v, now=NOW)
    assert _ids(report) == {"lock.skips": "yellow"}
    assert report.findings[0].fix == (
        "A long-running session is holding the vault lock; check .scout-logs/ for a run that took hours."
    )
    assert "#218" not in report.findings[0].fix


def test_two_lock_skips_are_fine(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=h), outcome="skipped_lock") for h in (30, 10, 1)])
    assert health.evaluate(v, now=NOW).severity == "green"


def test_malformed_ledger_rows_are_skipped(tmp_path):
    v = _vault(tmp_path)
    _ledger(
        v,
        [
            "not json",
            "[1, 2]",
            json.dumps({"slot": "x"}),
            json.dumps({**_row(NOW), "started_at": "soon"}),
            _row(NOW - timedelta(hours=2), code=1),
            _row(NOW - timedelta(hours=1), code=1),
        ],
    )
    rows = health.read_outcomes(v)
    assert len(rows) == 2
    assert _ids(health.evaluate(v, now=NOW)) == {"runs.failing": "red"}


def test_outcome_field_is_normalised_from_exit_code_when_absent_or_unknown(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW, code=0), _row(NOW, code=3), _row(NOW, code=0, outcome="bogus")])
    assert [r.outcome for r in health.read_outcomes(v)] == ["ok", "failed", "ok"]


def test_report_to_dict_shape(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=1), code=1, klass="network")])
    d = health.evaluate(v, now=NOW).to_dict()
    assert d["severity"] == "yellow" and d["evaluated_at"] == "2026-10-11T12:00:00Z" and d["last_ok_at"] is None
    assert d["findings"] == [
        {
            "id": "runs.failing",
            "severity": "yellow",
            "since": None,
            "summary": "The last 1 scheduled run(s) failed (network).",
            "fix": d["findings"][0]["fix"],
            "source": "ledger",
        }
    ]
    assert set(d) == {"severity", "evaluated_at", "last_ok_at", "findings", "slots"}


def test_notes_never_raise_severity():
    note = health.Finding(id="update.behind", severity="note", since=None, summary="s", fix="f", source="x")
    assert health.build_report([note], now=NOW, last_ok_at=None).severity == "green"
    assert health.build_report([note], now=NOW, last_ok_at=None).exit_code == 0


def test_slot_status_to_dict():
    row = health.OutcomeRow(
        slot="morning-briefing",
        mode="scout",
        started_at=NOW - timedelta(minutes=5),
        finished_at=NOW,
        exit_code=1,
        failure_class="network",
        outcome="failed",
        log=None,
        commits_in_window=None,
        newest_commit=None,
    )
    assert health.SlotStatus("morning-briefing", "scout", NOW, row).to_dict() == {
        "key": "morning-briefing",
        "type": "scout",
        "last_fire": "2026-10-11T12:00:00Z",
        "last_outcome": {"outcome": "failed", "finished_at": "2026-10-11T12:00:00Z", "failure_class": "network"},
    }
    assert health.SlotStatus("k", "t", None, None).to_dict() == {
        "key": "k",
        "type": "t",
        "last_fire": None,
        "last_outcome": None,
    }


def test_optional_commit_fields_blank_lines_and_mode_fallback(tmp_path):
    v = _vault(tmp_path)
    mode_only = {k: val for k, val in _row(NOW).items() if k != "slot"}
    _ledger(
        v,
        [
            "",
            _row(NOW, commits_in_window=2, newest_commit="abc1234"),
            _row(NOW, commits_in_window="many"),
            mode_only,
        ],
    )
    rows = health.read_outcomes(v)
    assert [(r.commits_in_window, r.newest_commit) for r in rows] == [(2, "abc1234"), (None, None), (None, None)]
    assert rows[2].slot == "scout"


def test_row_without_slot_or_mode_is_skipped(tmp_path):
    v = _vault(tmp_path)
    bare = {k: val for k, val in _row(NOW).items() if k not in ("slot", "mode")}
    _ledger(v, [bare])
    assert health.read_outcomes(v) == []


def test_missing_vault_and_empty_logs_dir_are_green(tmp_path):
    assert health.evaluate(tmp_path / "nowhere", now=NOW).severity == "green"
    (tmp_path / "empty" / ".scout-logs").mkdir(parents=True)
    assert health.evaluate(tmp_path / "empty", now=NOW).severity == "green"


def test_log_fallback_reads_only_the_tail_and_survives_unreadable_logs(tmp_path):
    v = _vault(tmp_path)
    log = v / ".scout-logs" / "scout-2026-10-11_08-00.log"
    log.write_text("Failed to authenticate. API Error: 401\n" + "x" * 70000 + "\n", encoding="utf-8")
    assert health.evaluate(v, now=NOW).severity == "green"  # marker is outside the last 64 KiB
    assert health._tail_text(v / ".scout-logs" / "gone.log") == ""
    broken = v / ".scout-logs" / "dreaming-broken.log"
    broken.symlink_to(v / "does-not-exist")  # glob finds it; stat() raises
    assert health.evaluate(v, now=NOW).severity == "green"
