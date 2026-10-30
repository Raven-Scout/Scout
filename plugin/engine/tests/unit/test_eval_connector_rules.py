"""busy-briefing's connector-rule seeds and the checks that score them.

``linear_delta`` scores the Linear phase's own-activity report and
``red_poll`` the Slack phase's 🔴 Project Channel Poll. The seeds reach the
runner's pre-fetch: the recorded Linear API response through the real fetch,
and the 🔴 widgets project's channels through the real KB read. Tier 0: no
model runs.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from scout import linear_activity as la
from scout.scripts.pre_session_data import get_red_project_channels
from tests.eval.harness.build import build_vault
from tests.eval.harness.checks.connector_rules import linear_delta, red_poll
from tests.eval.harness.runner import stage_linear_api
from tests.eval.harness.runner_env import build_env
from tests.eval.harness.scenario import load_scenario

TODAY = date(2026, 10, 12)  # a Monday
DIGEST = "## 📋 Scout Digest — 2026-10-12 (07:40)\n\n"
EXPECTED = ("PROJ-1212", "PROJ-1213", "OPS-315")


@pytest.fixture(scope="module")
def busy():
    return load_scenario("busy-briefing")


# ----- linear_delta -----------------------------------------------------------------


def test_linear_delta_passes_when_the_digest_names_every_issue() -> None:
    daily = f"## 🔴 Urgent\n\n{DIGEST}### Your Linear Activity\n\n- PROJ-1212, PROJ-1213 and OPS-315\n"
    assert linear_delta(daily, None, EXPECTED).passed


def test_linear_delta_passes_when_the_wrap_names_them() -> None:
    """A run built from a phase that predates the digest heading reports in the wrap."""
    assert linear_delta(
        "## 🔴 Urgent\n", "Your Linear: proj-1212 moved, PROJ-1213 created, OPS-315 commented", EXPECTED
    ).passed


def test_linear_delta_names_what_neither_surface_reports() -> None:
    daily = f"## 🟡 To Do\n\n- [ ] [#SRCH] **Merge the search filters** — PROJ-1213\n\n{DIGEST}- PROJ-1212\n"
    result = linear_delta(daily, "OPS-315 commented", EXPECTED)
    assert result.passed is False
    assert result.details == ("missing: PROJ-1213",)  # an action item naming it is not the report


def test_linear_delta_ignores_a_near_miss_id() -> None:
    assert linear_delta(f"{DIGEST}- PROJ-12120\n", None, ["PROJ-1212"]).passed is False


def test_linear_delta_cannot_score_a_run_with_no_outputs() -> None:
    assert linear_delta(None, None, EXPECTED).passed is None


# ----- red_poll ---------------------------------------------------------------------


def _call(tool: str, persona: str = "slack", **args: str) -> dict:
    return {"persona": persona, "tool": tool, "args": args}


def test_red_poll_passes_when_every_channel_was_read() -> None:
    calls = [_call("slack_search_public"), _call("slack_read_channel", channel_id="C0123456789", limit="20")]
    result = red_poll(calls, ["C0123456789"])
    assert result.passed and result.details == ("read: C0123456789",)


def test_red_poll_does_not_count_a_search_or_another_channel() -> None:
    calls = [
        _call("slack_search_public_and_private", query="in:<#C0123456789>"),
        _call("slack_read_channel", channel_id="C0999999999"),
        _call("slack_read_channel", persona="linear", channel_id="C0123456789"),
    ]
    result = red_poll(calls, ["C0123456789"])
    assert result.passed is False and result.details == ("not read: C0123456789",)


# ----- the scenario ---------------------------------------------------------------


def test_busy_briefing_scores_both_rules_on_busy_weekdays_files(busy) -> None:
    assert busy.mode == "morning-briefing"
    assert {"linear_delta", "red_poll"} <= set(busy.checks)
    assert busy.expected_linear_activity == EXPECTED
    assert busy.expected_red_channels == ("C0123456789",)
    assert busy.files == load_scenario("busy-weekday").path
    assert busy.linear_api == busy.files / "fixtures" / "linear-graphql.json"


def test_the_recorded_linear_response_yields_exactly_the_seeded_activity(
    busy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real fetch: Alex's three issues, not PROJ-1207, which Priya moved."""
    url = stage_linear_api(busy, tmp_path, TODAY)
    assert url is not None and url == f"file://{tmp_path / 'linear-graphql.json'}"
    key = tmp_path / "secrets" / "linear-api-key"
    assert key.stat().st_mode & 0o777 == 0o600
    assert "{{" not in (tmp_path / "linear-graphql.json").read_text()

    monkeypatch.setenv("SCOUT_SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.setenv("SCOUT_LINEAR_API_URL", url)
    got = la.fetch_own_activity(tmp_path / "no-vault", now=datetime(2026, 10, 12, 7, 0, tzinfo=UTC))
    assert got.status == la.FETCHED, got.error
    actions = {r["identifier"]: r["actions"] for r in got.issues or []}
    assert sorted(actions) == sorted(EXPECTED)
    assert actions == {
        "OPS-315": ["commented"],
        "PROJ-1213": ["created"],
        "PROJ-1212": ["moved In Progress → In Review"],
    }


def test_the_mcp_persona_serves_the_same_activity(busy) -> None:
    """A run without the pre-fetch can find each seed through the Linear connector."""
    issues = json.loads((busy.files / "fixtures" / "linear-issues.json").read_text())["issues"]
    assert set(EXPECTED) <= {i["identifier"] for i in issues}
    comments = json.loads((busy.files / "fixtures" / "linear-comments-ops-315.json").read_text())
    assert comments["comments"][0]["user"]["name"] == "Alex Example"


def test_the_built_vault_lists_the_red_widgets_channel(busy, tmp_path: Path) -> None:
    vault = build_vault(tmp_path / "v", today=TODAY, scenario_dir=busy.files)
    rows = get_red_project_channels(vault, "autonomous")
    assert [c["id"] for r in rows for c in r["channels"]] == list(busy.expected_red_channels)
    assert rows[0]["channels"][0]["name"] == "#widgets-launch"


def test_a_scenario_without_a_recorded_response_stages_nothing(tmp_path: Path) -> None:
    assert stage_linear_api(load_scenario("privacy-canary"), tmp_path, TODAY) is None
    assert not (tmp_path / "secrets").exists()


def test_the_sample_env_never_points_at_the_real_secrets_dir(tmp_path: Path) -> None:
    env = build_env(
        base={**os.environ, "SCOUT_SECRETS_DIR": "/real/secrets", "SCOUT_LINEAR_API_URL": "https://api.linear.app"},
        mode="morning-briefing",
        result_dir=tmp_path,
        mcp_config=tmp_path / "mcp.json",
        allowed_servers=[],
        allowed_tools=[],
        model="sonnet",
        real_claude="/usr/bin/true",
        script=tmp_path / "connectors.yaml",
        today=TODAY.isoformat(),
        timeout_s=60,
    )
    assert env["SCOUT_SECRETS_DIR"] == str(tmp_path / "secrets")
    assert "SCOUT_LINEAR_API_URL" not in env  # an inherited endpoint never leaks in
    staged = build_env(
        base=os.environ,
        mode="morning-briefing",
        result_dir=tmp_path,
        mcp_config=tmp_path / "mcp.json",
        allowed_servers=[],
        allowed_tools=[],
        model="sonnet",
        real_claude="/usr/bin/true",
        script=tmp_path / "connectors.yaml",
        today=TODAY.isoformat(),
        timeout_s=60,
        linear_api_url="file:///tmp/x.json",
    )
    assert staged["SCOUT_LINEAR_API_URL"] == "file:///tmp/x.json"
