"""`scoutctl action-items check-linear-delta`.

With a Linear key the runner pre-fetches the user's own Linear activity into
`.scout-cache/session-context.json`, and every pre-fetched issue must be named
under `### Your Linear Activity` in today's 📋 Scout Digest. Without one, the
heading must exist with at least one bullet, built from the Linear connector.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scout.action_items.cli import app
from scout.action_items.linear_delta import check_linear_delta, report_lines
from scout.action_items.prefetched import PrefetchCheckError

runner = CliRunner()
TODAY = "2026-04-15"
SINCE = "2026-04-14T07:00:00Z"
PROJ_1212 = {
    "identifier": "PROJ-1212",
    "title": "Ship the catalog search",
    "url": "https://linear.app/example-org/issue/PROJ-1212",
    "state": "In Review",
    "actions": ["moved In Progress → In Review"],
    "last_at": "2026-04-15T05:00:00Z",
}
OPS_315 = {**PROJ_1212, "identifier": "OPS-315", "title": "Gateway capacity plan", "actions": ["commented"]}

HEAD = f"# Action Items — {TODAY}\n\n## 🔴 Urgent\n\n- [ ] [#QUOTA] **Answer Priya** — reply\n\n"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "Scout"
    (data_dir / "action-items").mkdir(parents=True)
    (data_dir / ".scout-cache").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(data_dir))
    return data_dir


def _context(vault: Path, activity: list[dict] | None, status: str = "fetched", **extra: Any) -> Path:
    path = vault / ".scout-cache" / "session-context.json"
    data = {
        "generated_at": f"{TODAY}T07:00:00",
        "session_type": "morning-briefing",
        "linear_own_activity": activity,
        "linear_own_activity_since": SINCE,
        "linear_own_activity_status": status,
        **extra,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _daily(vault: Path, digest: str | None) -> Path:
    path = vault / "action-items" / f"action-items-{TODAY}.md"
    body = HEAD
    if digest is not None:
        body += f"## 📋 Scout Digest — {TODAY} (07:40)\n\n**Scout ran 1 session today.**\n\n{digest}\n"
    body += "## 🪵 Run notes & connector availability\n\n- **2026-04-15 07:40 UTC** (morning-briefing) — ok\n"
    path.write_text(body, encoding="utf-8")
    return path


REPORT = (
    "### Your Linear Activity\n\n- PROJ-1212 Ship the catalog search: you moved it to In Review\n"
    "- OPS-315: you commented\n"
)


# ----- with a pre-fetch ------------------------------------------------------------


def test_every_prefetched_issue_named_under_the_heading_passes(vault: Path) -> None:
    _context(vault, [PROJ_1212, OPS_315])
    _daily(vault, REPORT)
    result = check_linear_delta(today=TODAY)
    assert result.passed and result.unreported == ()
    assert [i.identifier for i in result.prefetched or ()] == ["PROJ-1212", "OPS-315"]


def test_an_issue_left_out_is_listed(vault: Path) -> None:
    _context(vault, [PROJ_1212, OPS_315])
    _daily(vault, "### Your Linear Activity\n\n- PROJ-1212: moved to In Review\n")
    result = check_linear_delta(today=TODAY)
    assert not result.passed
    assert [i.identifier for i in result.unreported] == ["OPS-315"]


def test_naming_it_elsewhere_in_the_file_does_not_count(vault: Path) -> None:
    """An action item or a run note naming the issue is not the report the rule asks for."""
    _context(vault, [PROJ_1212])
    path = _daily(vault, "### Files to Review\n\n- PROJ-1212 is mentioned here, under another heading\n")
    path.write_text(path.read_text() + "- PROJ-1212 also in the run notes\n", encoding="utf-8")
    assert [i.identifier for i in check_linear_delta(today=TODAY).unreported] == ["PROJ-1212"]


def test_a_near_miss_identifier_does_not_count(vault: Path) -> None:
    _context(vault, [PROJ_1212])
    _daily(vault, "### Your Linear Activity\n\n- PROJ-12120 and XPROJ-1212 are other issues\n")
    assert not check_linear_delta(today=TODAY).passed


def test_matching_ignores_case(vault: Path) -> None:
    _context(vault, [PROJ_1212])
    _daily(vault, "### Your Linear Activity\n\n- proj-1212 moved to In Review\n")
    assert check_linear_delta(today=TODAY).passed


def test_a_heading_inside_a_code_fence_does_not_count(vault: Path) -> None:
    _context(vault, [PROJ_1212])
    _daily(vault, "```\n### Your Linear Activity\n- PROJ-1212\n```\n")
    assert not check_linear_delta(today=TODAY).passed


def test_nothing_prefetched_needs_no_report_and_no_daily_file(vault: Path) -> None:
    _context(vault, [])
    result = check_linear_delta(today=TODAY)
    assert result.passed and result.prefetched == ()


# ----- without a pre-fetch (no key, a failed fetch, an older engine) -------------


@pytest.mark.parametrize("status", ["no_key", "failed: HTTP 401", "skipped"])
def test_without_a_prefetch_a_bulleted_heading_passes(vault: Path, status: str) -> None:
    _context(vault, None, status=status)
    _daily(vault, "### Your Linear Activity\n\n- None since 2026-04-14 07:00 UTC\n")
    result = check_linear_delta(today=TODAY)
    assert result.prefetched is None and result.reported and result.passed
    assert result.status == status


@pytest.mark.parametrize(
    "digest",
    [None, "### Files to Review\n\n- a file\n", "### Your Linear Activity\n\nNothing to say, and no bullet.\n"],
)
def test_without_a_prefetch_a_missing_or_empty_heading_fails(vault: Path, digest: str | None) -> None:
    _context(vault, None, status="no_key")
    _daily(vault, digest)
    assert not check_linear_delta(today=TODAY).passed


def test_a_context_from_an_older_engine_falls_back(vault: Path) -> None:
    path = vault / ".scout-cache" / "session-context.json"
    path.write_text(json.dumps({"generated_at": f"{TODAY}T07:00:00", "pr_review_requested": []}), encoding="utf-8")
    _daily(vault, REPORT)
    result = check_linear_delta(today=TODAY)
    assert result.prefetched is None and result.status == "" and result.passed


# ----- cannot check ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (None, "not found"),
        ("{not json", "not valid JSON"),
        ("[]", "not a JSON object"),
        ('{"session_type": "x"}', "no generated_at"),
        ('{"generated_at": "2026-04-14T07:00:00"}', "is from 2026-04-14"),
        (json.dumps({"generated_at": f"{TODAY}T07:00:00", "linear_own_activity": [{"title": "x"}]}), "no identifier"),
    ],
)
def test_it_refuses_what_it_cannot_check(vault: Path, content: str | None, reason: str) -> None:
    if content is not None:
        (vault / ".scout-cache" / "session-context.json").write_text(content, encoding="utf-8")
    _daily(vault, REPORT)
    with pytest.raises(PrefetchCheckError, match=reason):
        check_linear_delta(today=TODAY)


def test_it_refuses_without_todays_file(vault: Path) -> None:
    _context(vault, [PROJ_1212])
    with pytest.raises(PrefetchCheckError, match="no action-items file"):
        check_linear_delta(today=TODAY)


def test_report_lines_stop_at_the_next_heading() -> None:
    lines = ["## 📋 Scout Digest — x", "### Your Linear Activity", "- A", "### KB Growth Today", "- B", "## 🪵 Run"]
    assert report_lines(lines) == ["- A"]
    assert report_lines(["### Your Linear Activity", "- A"]) is None  # not inside the digest


# ----- CLI -----------------------------------------------------------------------------


def _cli(*args: str) -> Any:
    return runner.invoke(app, ["check-linear-delta", "--date", TODAY, *args])


def test_cli_exit_0_when_reported(vault: Path) -> None:
    _context(vault, [PROJ_1212, OPS_315])
    _daily(vault, REPORT)
    result = _cli()
    assert result.exit_code == 0
    assert "all 2 pre-fetched Linear issues are under ### Your Linear Activity" in result.stdout


def test_cli_exit_0_when_nothing_was_prefetched(vault: Path) -> None:
    _context(vault, [])
    result = _cli()
    assert result.exit_code == 0 and f"no own Linear activity since {SINCE}" in result.stdout


def test_cli_exit_1_lists_each_unreported_issue(vault: Path) -> None:
    _context(vault, [PROJ_1212, OPS_315])
    _daily(vault, None)
    result = _cli()
    assert result.exit_code == 1
    assert "2 of 2 issues you acted on in Linear since" in result.stdout
    assert "PROJ-1212 — Ship the catalog search — moved In Progress → In Review (now In Review)" in result.stdout
    assert "OPS-315 — Gateway capacity plan — commented" in result.stdout


def test_cli_exit_1_without_a_prefetch_says_how_to_build_the_report(vault: Path) -> None:
    _context(vault, None, status="no_key")
    _daily(vault, None)
    result = _cli()
    assert result.exit_code == 1
    assert "not pre-fetched (no_key)" in result.stdout
    assert f"`- None since {SINCE}`" in result.stdout and "Linear unavailable" in result.stdout


def test_cli_exit_0_without_a_prefetch_when_reported(vault: Path) -> None:
    _context(vault, None, status="no_key")
    _daily(vault, REPORT)
    result = _cli()
    assert result.exit_code == 0 and "not pre-fetched: no_key" in result.stdout


def test_cli_exit_2_when_it_cannot_check(vault: Path) -> None:
    result = _cli()
    assert result.exit_code == 2 and "check-linear-delta: cannot check" in result.stdout


def test_cli_json(vault: Path) -> None:
    _context(vault, [PROJ_1212, OPS_315])
    _daily(vault, "### Your Linear Activity\n\n- PROJ-1212\n")
    result = _cli("--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "status": "fetched",
        "since": SINCE,
        "prefetched": 2,
        "reported": True,
        "unreported": [{"identifier": "OPS-315", "title": "Gateway capacity plan", "actions": ["commented"]}],
        "truncated": False,
        "passed": False,
    }


# ----- the digest is shared by the day's runs -----------------------------------------


EARLIER = ["- PROJ-1212 Ship the catalog search: you moved it to In Progress", "- OPS-315: you commented"]


def test_a_line_an_earlier_run_wrote_does_not_count(vault: Path) -> None:
    """The afternoon run's pre-fetch has a new action on an issue the morning already named."""
    _context(vault, [PROJ_1212], linear_report_before=["", *EARLIER])
    _daily(vault, "### Your Linear Activity\n\n" + "\n".join(EARLIER) + "\n")
    result = check_linear_delta(today=TODAY)
    assert [i.identifier for i in result.unreported] == ["PROJ-1212"] and not result.passed


def test_updating_an_earlier_line_counts(vault: Path) -> None:
    _context(vault, [PROJ_1212], linear_report_before=EARLIER)
    _daily(vault, "### Your Linear Activity\n\n- PROJ-1212: you moved it to In Review\n" + EARLIER[1] + "\n")
    assert check_linear_delta(today=TODAY).passed


def test_without_a_prefetch_an_earlier_runs_bullet_does_not_count(vault: Path) -> None:
    _context(vault, None, "no_key", linear_report_before=["- None since 2026-04-14T07:00:00Z"])
    _daily(vault, "### Your Linear Activity\n\n- None since 2026-04-14T07:00:00Z\n")
    assert not check_linear_delta(today=TODAY).passed


def test_a_malformed_snapshot_reads_as_empty(vault: Path) -> None:
    _context(vault, [PROJ_1212], linear_report_before="not a list")
    _daily(vault, REPORT)
    assert check_linear_delta(today=TODAY).passed


def test_cli_notes_a_truncated_prefetch(vault: Path) -> None:
    _context(vault, [PROJ_1212], linear_own_activity_truncated=True)
    _daily(vault, REPORT)
    result = _cli()
    assert result.exit_code == 0 and "hit a limit" in result.stdout
    assert json.loads(_cli("--json").stdout)["truncated"] is True


def test_cli_json_error(vault: Path) -> None:
    result = _cli("--json")
    assert result.exit_code == 2 and "not found" in json.loads(result.stdout)["error"]


def test_cli_rejects_a_bad_date(vault: Path) -> None:
    result = runner.invoke(app, ["check-linear-delta", "--date", "15/04/2026"])
    assert result.exit_code == 2 and "invalid date" in result.stdout


def test_cli_defaults_to_the_configured_today(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as dt

    monkeypatch.setattr("scout.config.today", lambda _data=None: dt.date.fromisoformat(TODAY))
    _context(vault, [PROJ_1212])
    _daily(vault, REPORT)
    assert runner.invoke(app, ["check-linear-delta"]).exit_code == 0


def test_cli_context_override(vault: Path, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere.json"
    other.write_text(_context(vault, [PROJ_1212]).read_text(), encoding="utf-8")
    (vault / ".scout-cache" / "session-context.json").unlink()
    _daily(vault, REPORT)
    assert _cli("--context", str(other)).exit_code == 0
