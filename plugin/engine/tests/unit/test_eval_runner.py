"""Scenarios, the session plan, and one sample end to end with a fake claude (spec 2026-10-12 §7, §9).

No test here starts the real claude: the end-to-end test runs the vault's real
run-scout.sh with a fake CLAUDE target, which is the whole pipeline for free.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tests.eval.harness.plan import PlanRefused, session_plan
from tests.eval.harness.runner import run_sample
from tests.eval.harness.scenario import SCENARIOS_DIR, load_scenario

MONDAY = date(2026, 10, 12)


@pytest.fixture(scope="module")
def busy():
    return load_scenario("busy-weekday")


def test_busy_weekday_loads(busy) -> None:
    assert busy.mode == "morning-consolidation"
    assert busy.weekdays_only is True
    assert busy.personas == ("slack", "linear", "calendar")
    assert busy.checks[:2] == ("placement", "no_drop")
    assert busy.expected_placement["QUOTA"] == {"🔴", "🟡"}
    assert busy.expected_closures == {"LCHK"}


def test_every_expected_tag_is_seeded_yesterday_and_every_ref_exists_in_a_fixture(busy) -> None:
    yesterday = (busy.path / "vault" / "action-items" / "action-items-{{d-1}}.md").read_text()
    fixtures = "\n".join(p.read_text() for p in (busy.path / "fixtures").glob("*.json"))
    for key in busy.expected_placement:
        if key.startswith("ref:"):
            ref = key[4:]
            needle = ref.replace("#", "/pull/") if "#" in ref else ref
            assert needle in fixtures, key
        else:
            assert f"[#{key}]" in yesterday, key
    for tag in busy.expected_closures:
        assert f"[#{tag}]" in yesterday


def test_unknown_scenario_is_an_error() -> None:
    with pytest.raises(FileNotFoundError, match="nope"):
        load_scenario("nope")
    assert (SCENARIOS_DIR / "busy-weekday").is_dir()


def test_plan_describes_what_will_start(busy) -> None:
    plan = session_plan([busy], reps=3, opus=False, many=False)
    assert plan.sessions == 3
    assert plan.describe() == "Will start 3 sessions (busy-weekday ×3) on sonnet via your Claude login."


def test_plan_refuses_more_than_ten_sessions_without_many(busy) -> None:
    with pytest.raises(PlanRefused, match="--many"):
        session_plan([busy], reps=11, opus=False, many=False)
    assert session_plan([busy], reps=11, opus=False, many=True).sessions == 11


def test_plan_uses_opus_only_when_asked(busy) -> None:
    assert session_plan([busy], reps=1, opus=True, many=False).model == "opus"


FAKE_CLAUDE = """#!/usr/bin/env python3
import glob, json, os, sys, time
if os.environ.get("EVAL_FAKE_ENRICH"):  # rewrite today's file the way a session would: drop the carry banner
    path = sorted(glob.glob("action-items/action-items-*.md"))[-1]
    lines = [ln for ln in open(path) if "Mechanical carry-forward" not in ln]
    open(path, "w").writelines(lines)
if os.environ.get("EVAL_FAKE_ENVFILE"):
    open(os.environ["EVAL_FAKE_ENVFILE"], "w").write(os.environ.get("PYTHONPATH", ""))
if os.environ.get("EVAL_FAKE_SLEEP"):
    open(os.environ["EVAL_FAKE_PIDFILE"], "w").write(str(os.getpid()))
    time.sleep(float(os.environ["EVAL_FAKE_SLEEP"]))
servers = [s for s in os.environ["EVAL_FAKE_SERVERS"].split(",") if s]
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5", "apiKeySource": "none",
                  "mcp_servers": [{"name": s, "status": "connected"} for s in servers]}), flush=True)
print(json.dumps({"type": "assistant", "message": {"id": "m1", "content": [
    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": os.getcwd() + "/SKILL.md"}}]}}), flush=True)
result = {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
print(json.dumps({"type": "user", "message": {"content": [result]}}))
subtype = os.environ.get("EVAL_FAKE_SUBTYPE", "success")
print(json.dumps({"type": "result", "subtype": subtype, "is_error": subtype != "success", "num_turns": 1,
                  "duration_ms": 1000, "total_cost_usd": 0.01, "result": "Nothing to do.",
                  "usage": {"input_tokens": 1, "output_tokens": 1}}))
"""


def test_one_sample_end_to_end_with_a_fake_claude(tmp_path: Path, busy, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    monkeypatch.setenv("EVAL_FAKE_SERVERS", "claude_ai_Google_Calendar,claude_ai_Linear,claude_ai_Slack")
    monkeypatch.setenv("EVAL_FAKE_ENRICH", "1")
    result = run_sample(
        busy, today=MONDAY, rep=1, model="sonnet", results_root=tmp_path / "results", real_claude=str(fake)
    )
    assert result.aborted is None
    assert result.metrics.tool_call_count == 1 and result.metrics.model == "claude-sonnet-5"
    # The runner's pre-session step carried yesterday's file into today; the fake session
    # only dropped the carry banner: nothing dropped, but no item was re-placed.
    by_name = {c.name: c for c in result.checks}
    assert by_name["no_drop"].passed is True
    assert by_name["placement"].passed is False
    saved = json.loads((result.result_dir / "checks.json").read_text())
    assert {c["name"] for c in saved} == set(busy.checks)
    assert json.loads((result.result_dir / "metrics.json").read_text())["tool_call_count"] == 1
    assert (result.result_dir / "vault" / "action-items" / "action-items-2026-10-12.md").exists()


def test_a_leaking_session_is_aborted_and_not_scored(tmp_path: Path, busy, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    monkeypatch.setenv(
        "EVAL_FAKE_SERVERS", "claude_ai_Google_Calendar,claude_ai_Linear,claude_ai_Slack,claude.ai Slack"
    )
    result = run_sample(
        busy, today=MONDAY, rep=1, model="sonnet", results_root=tmp_path / "results", real_claude=str(fake)
    )
    assert result.aborted == {"reason": "mcp_leak", "servers": ["claude.ai Slack"]}
    assert result.checks == ()


def test_a_session_that_never_starts_is_aborted_not_scored(tmp_path: Path, busy) -> None:
    fake = tmp_path / "fake-claude"
    fake.write_text("#!/bin/bash\nexit 1\n")
    fake.chmod(0o755)
    result = run_sample(
        busy, today=MONDAY, rep=1, model="sonnet", results_root=tmp_path / "results", real_claude=str(fake)
    )
    assert result.aborted is not None and result.aborted["reason"] == "no_session"
    assert result.checks == ()


def test_cli_run_refuses_a_big_plan_without_many(capsys: pytest.CaptureFixture[str]) -> None:
    from tests.eval.harness.cli import main

    assert main(["run", "busy-weekday", "--reps", "11", "--yes"]) == 2
    assert "--many" in capsys.readouterr().err


def test_cli_run_refuses_a_weekday_scenario_on_a_weekend(capsys: pytest.CaptureFixture[str]) -> None:
    from tests.eval.harness.cli import main

    assert main(["run", "busy-weekday", "--yes", "--today", "2026-10-17"]) == 2
    assert "weekday" in capsys.readouterr().err


def test_cli_run_with_a_fake_claude_prints_the_plan_and_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.eval.harness.cli import main

    fake = tmp_path / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    monkeypatch.setenv("EVAL_FAKE_SERVERS", "claude_ai_Google_Calendar,claude_ai_Linear,claude_ai_Slack")
    monkeypatch.setenv("EVAL_FAKE_ENRICH", "1")
    code = main(
        ["run", "busy-weekday", "--reps", "1", "--yes", "--today", "2026-10-12",
         "--claude", str(fake), "--results", str(tmp_path / "results")]
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    assert "Will start 1 session (busy-weekday ×1) on sonnet" in out
    assert "placement  0/1" in out and "no_drop  1/1" in out
    assert "tool calls  1" in out


def test_allowlist_covers_the_shell_steps_phases_use_but_not_destructive_ones(tmp_path: Path) -> None:
    from tests.eval.harness.runner import allowed_tools

    tools = allowed_tools(tmp_path / "vault", {"claude_ai_Slack"})
    for needed in (
        "Bash(echo *)",
        "Bash(mkdir *)",
        "Bash(scoutctl *)",
        "Bash(python3 *)",
        "Bash(python *)",
        "Bash(git *)",
        "Bash(gh *)",
        "mcp__claude_ai_Slack",
    ):
        assert needed in tools
    assert f"Read(/{tmp_path / 'vault'}/**)" in tools
    assert not any(t.startswith(("Bash(rm", "Bash(mv", "Bash(cp", "Bash(curl")) for t in tools)


def _gone(pid: int, wait_s: float = 10.0) -> bool:
    """True once ``pid`` no longer runs. A killed child whose parent died too can linger
    as a zombie until something reaps it (common on Linux CI), and kill(pid, 0) still
    succeeds on a zombie, so a zombie counts as gone."""
    import os
    import time

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists() and stat.read_text().rsplit(")", 1)[-1].split()[0] == "Z":
            return True
        time.sleep(0.1)
    return False


def _fake(tmp_path: Path) -> Path:
    fake = tmp_path / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    return fake


SERVERS = "claude_ai_Google_Calendar,claude_ai_Linear,claude_ai_Slack"


def test_a_session_that_ends_in_an_error_is_aborted_not_scored(
    tmp_path: Path, busy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVAL_FAKE_SERVERS", SERVERS)
    monkeypatch.setenv("EVAL_FAKE_SUBTYPE", "error_max_budget_usd")
    result = run_sample(
        busy, today=MONDAY, rep=1, model="sonnet", results_root=tmp_path / "r", real_claude=str(_fake(tmp_path))
    )
    assert result.aborted == {"reason": "error_max_budget_usd"}
    assert result.checks == ()


def test_a_session_that_never_rewrote_todays_file_is_not_scored_as_a_pass(
    tmp_path: Path, busy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVAL_FAKE_SERVERS", SERVERS)
    result = run_sample(
        busy, today=MONDAY, rep=1, model="sonnet", results_root=tmp_path / "r", real_claude=str(_fake(tmp_path))
    )
    assert result.aborted is None
    assert {(c.name, c.passed, c.details) for c in result.checks} == {
        (name, None, ("not_enriched",)) for name in busy.checks
    }


def test_items_archived_today_in_the_real_archive_format_are_not_drops(tmp_path: Path) -> None:
    from scout.action_items.stale import ArchivedItem, write_archive
    from tests.eval.harness.runner import archived_today

    write_archive(
        tmp_path,
        MONDAY,
        [ArchivedItem("OLD", date(2026, 8, 1), ["- [ ] [#OLD] **Tidy the runbook** — no news", "  - Refs: OPS-1"])],
    )
    write_archive(
        tmp_path,
        date(2026, 10, 1),
        [ArchivedItem("PREV", date(2026, 7, 1), ["- [ ] [#PREV] **Older thing** — no news"])],
    )
    assert archived_today(tmp_path, MONDAY) == {"OLD"}


def test_a_runner_that_overruns_is_killed_with_its_session_and_not_scored(
    tmp_path: Path, busy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVAL_FAKE_SERVERS", SERVERS)
    monkeypatch.setenv("EVAL_FAKE_SLEEP", "120")
    monkeypatch.setenv("EVAL_FAKE_PIDFILE", str(tmp_path / "fake.pid"))
    result = run_sample(
        busy, today=MONDAY, rep=1, model="sonnet", results_root=tmp_path / "r", real_claude=str(_fake(tmp_path)),
        timeout_s=60, hook_grace_s=-30,  # a 30 s runner budget: slow CI pre-hooks finish first
    )  # fmt: skip
    assert result.aborted is not None and result.aborted["reason"] == "runner_timeout"
    assert result.checks == ()
    pid_file = tmp_path / "fake.pid"
    assert pid_file.exists(), "the session never started inside the runner budget; see runner.log"
    pid = int(pid_file.read_text())
    assert _gone(pid), f"the session process {pid} outlived the runner timeout"


def test_the_gh_stub_answers_per_repo(busy) -> None:
    from tests.eval.harness.stubs.rules import Script

    script = Script.load(busy.script, today=MONDAY)

    def stdout(argv: str) -> str:
        return script.reply("gh", "*", {"argv": argv}, prior_hits=lambda _: 0).payload["stdout"]

    assert stdout("pr list --author @me --state open --repo example-org/api --json number") == "[]"
    assert stdout("pr list --repo example-org/api --author @me --state all") == "[]"
    assert stdout("pr list --repo example-org/api --state all --json number,title") == "fixtures/gh-api-prs.json"
    assert stdout("pr list --author @me --state open --json number,title") == "fixtures/gh-my-prs.json"
    assert stdout("pr list --author @me --repo example-org/widgets --json number") == "fixtures/gh-my-prs.json"
