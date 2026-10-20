"""An unexercised connector is not a dark connector.

A run that never calls a connector logs no row for it at all: no successes and no
errors. That says something about the run (it was short, or didn't need the
connector), not about the connector's health. The ``mode_baseline`` vote asks
*"do prior runs of this mode call this connector successfully?"*, a statement
about **runs**, and must not be promoted into a statement about **connector
health**. The cross-mode liveness guard doesn't cover this either: it measures
``CROSS_MODE_LIVENESS_WINDOW`` in wall-clock hours, so a connector that is
naturally called once a day can never satisfy it.

These tests pin the rule: ``curr_ok == 0 and curr_err == 0`` means *unexercised*,
and only the chronic-skip backstop (>=3 dark runs in a slot type that requires
the connector) may escalate it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout.scripts import connector_health_report as chr_mod

# ----- helpers (mirrors test_scripts_connector_health_required_in_types.py) ----


def _make_call(
    ts_utc: datetime,
    sid: str,
    mode: str,
    connector: str,
    *,
    error: bool = False,
    err: str = "",
) -> dict:
    rec: dict = {
        "ts": ts_utc.isoformat().replace("+00:00", "Z"),
        "session_id": sid,
        "mode": mode,
        "tool": "Bash",
        "connector": connector,
        "error": error,
    }
    if err:
        rec["err"] = err
    return rec


def _seed_session(
    log_dir: Path,
    *,
    sid: str,
    mode: str,
    ts: datetime,
    calls: dict[str, tuple[int, int]],
) -> None:
    records = []
    for connector, (n_ok, n_err) in calls.items():
        for i in range(n_ok):
            records.append(_make_call(ts + timedelta(seconds=i), sid, mode, connector))
        for i in range(n_err):
            records.append(
                _make_call(
                    ts + timedelta(seconds=100 + i),
                    sid,
                    mode,
                    connector,
                    error=True,
                    err=f"boom {connector}",
                )
            )
    out = log_dir / f"connector-calls-{ts.date().isoformat()}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in records]
    if out.exists():
        existing = out.read_text().splitlines()
        existing.extend(lines)
        out.write_text("\n".join(existing) + "\n")
    else:
        out.write_text("\n".join(lines) + "\n")


def _frozen_now() -> datetime:
    return datetime(2026, 4, 28, 21, 0, 0, tzinfo=UTC)


def _alert_keys(event) -> list[str]:
    return [] if event is None else [a["connector_key"] for a in event.payload["alerts"]]


def _seed_chronic_github_gap(log_dir: Path) -> None:
    """One healthy briefing, then three in a row where github is silently absent."""
    base = _frozen_now() - timedelta(days=4)
    _seed_session(
        log_dir,
        sid="brief_d1",
        mode="morning-briefing",
        ts=base,
        calls={"github": (5, 0), "mcp:claude_ai_Slack": (8, 0)},
    )
    for i in range(3):
        _seed_session(
            log_dir,
            sid=f"brief_d{2 + i}",
            mode="morning-briefing",
            ts=base + timedelta(days=1 + i),
            calls={"mcp:claude_ai_Slack": (8, 0)},
        )


# ----- tests -----------------------------------------------------------------


def test_a_run_that_skips_connectors_does_not_mark_them_dark(fake_data_dir, monkeypatch):
    """Two healthy runs establish the mode baseline; the third calls only GitHub.

    Slack/Linear/Granola were never attempted and logged zero errors, so none
    may be reported CRITICAL.
    """
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"
    base = _frozen_now() - timedelta(days=2)

    # Prior runs: this mode normally exercises every connector, healthily.
    for i in range(2):
        _seed_session(
            log_dir,
            sid=f"cons_d{i + 1}",
            mode="afternoon-consolidation",
            ts=base + timedelta(days=i),
            calls={
                "github": (20, 0),
                "mcp:claude_ai_Slack": (24, 0),
                "mcp:claude_ai_Linear": (6, 0),
                "mcp:claude_ai_Granola": (4, 0),
            },
        )

    # Current run: GitHub-only. A short run, not a sick connector.
    _seed_session(
        log_dir,
        sid="cons_curr",
        mode="afternoon-consolidation",
        ts=_frozen_now() - timedelta(hours=1),
        calls={"github": (10, 0)},
    )

    keys = _alert_keys(chr_mod.run(data_dir=fake_data_dir))
    for untouched in ("mcp:claude_ai_Slack", "mcp:claude_ai_Linear", "mcp:claude_ai_Granola"):
        assert untouched not in keys, (
            f"{untouched} was never called this run and has logged no errors — "
            f"it must not be reported CRITICAL. Got: {keys}"
        )


def test_an_attempted_and_failing_connector_still_alerts(fake_data_dir, monkeypatch):
    """The suppression is scoped to *unexercised*, never to *failing*.

    Same shape as above, except the current run actually calls Slack and every
    call errors. That is observed evidence and must still fire.
    """
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"
    base = _frozen_now() - timedelta(days=2)

    for i in range(2):
        _seed_session(
            log_dir,
            sid=f"cons_d{i + 1}",
            mode="afternoon-consolidation",
            ts=base + timedelta(days=i),
            calls={"github": (20, 0), "mcp:claude_ai_Slack": (24, 0)},
        )

    _seed_session(
        log_dir,
        sid="cons_curr",
        mode="afternoon-consolidation",
        ts=_frozen_now() - timedelta(hours=1),
        calls={"github": (10, 0), "mcp:claude_ai_Slack": (0, 5)},
    )

    event = chr_mod.run(data_dir=fake_data_dir)
    assert "mcp:claude_ai_Slack" in _alert_keys(event), "Slack failed 5/5 calls this run — must alert."
    assert event is not None
    reason = next(a["reason"] for a in event.payload["alerts"] if a["connector_key"] == "mcp:claude_ai_Slack")
    assert reason.startswith("5 failed call(s) in `afternoon-consolidation` run"), reason


def test_the_chronic_skip_backstop_still_escalates_a_sustained_gap(fake_data_dir, monkeypatch):
    """A genuinely dead connector that fails silently is still caught.

    If an OAuth session expires the tools stop being offered, so the agent makes
    no calls and logs no errors — indistinguishable from "didn't need it" on any
    single run. The >=3-run gap in a requiring slot type is what separates them.
    """
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    _seed_chronic_github_gap(fake_data_dir / ".scout-logs")

    keys = _alert_keys(chr_mod.run(data_dir=fake_data_dir))
    assert "github" in keys, f"github dark across 3 requiring runs must still escalate. Got: {keys}"


def test_a_failing_connector_escalates_through_the_backstop_when_the_vote_says_no(fake_data_dir, monkeypatch):
    """Errors this run plus a chronic gap alert even when the mode vote is too weak.

    Only 1 of the 3 prior briefings was healthy on github (the vote needs 2), but
    github has been dark for 3 runs in a slot type that requires it and is failing
    now, so the backstop fires and the reason reports the observed failures.
    """
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"
    base = _frozen_now() - timedelta(days=4)

    _seed_session(
        log_dir,
        sid="brief_d1",
        mode="morning-briefing",
        ts=base,
        calls={"github": (5, 0), "mcp:claude_ai_Slack": (8, 0)},
    )
    for i in range(3):
        _seed_session(
            log_dir,
            sid=f"brief_d{2 + i}",
            mode="morning-briefing",
            ts=base + timedelta(days=1 + i),
            calls={"github": (0, 2), "mcp:claude_ai_Slack": (8, 0)},
        )

    event = chr_mod.run(data_dir=fake_data_dir)
    assert "github" in _alert_keys(event)
    assert event is not None
    reason = next(a["reason"] for a in event.payload["alerts"] if a["connector_key"] == "github")
    assert reason.startswith("2 failed call(s) in `morning-briefing` run"), reason
    assert "1/3 prior" in reason, reason


def test_cross_mode_liveness_still_suppresses_a_chronic_unattempted_connector(fake_data_dir, monkeypatch):
    """The backstop doesn't override the cross-mode liveness guard.

    github was healthy in a research run two hours before the current briefing,
    then sat out three briefings in a row. The gap is chronic, but the connector
    is demonstrably alive and nothing failed, so there is no CRITICAL.
    """
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"
    now = _frozen_now()

    _seed_session(
        log_dir,
        sid="research_1",
        mode="research",
        ts=now - timedelta(hours=3),
        calls={"github": (5, 0)},
    )
    for i, hours_ago in enumerate((2.0, 1.5, 1.0)):
        _seed_session(
            log_dir,
            sid=f"brief_{i}",
            mode="morning-briefing",
            ts=now - timedelta(hours=hours_ago),
            calls={"mcp:claude_ai_Slack": (8, 0)},
        )

    assert "github" not in _alert_keys(chr_mod.run(data_dir=fake_data_dir))


def test_an_unattempted_alert_says_it_was_not_called(fake_data_dir, monkeypatch):
    """A zero must say what kind of zero it is.

    When the chronic-skip backstop fires on a connector that logged no errors,
    the reason text must not read "0 successful calls" as though it was tried.
    """
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    _seed_chronic_github_gap(fake_data_dir / ".scout-logs")

    event = chr_mod.run(data_dir=fake_data_dir)
    assert event is not None
    reason = next(a["reason"] for a in event.payload["alerts"] if a["connector_key"] == "github")
    assert "NOT CALLED" in reason, f"Unattempted alert must say so; got: {reason!r}"
    assert "3-run gap" in reason, reason
