"""`.github/scripts/rerun_unacquired.py`: which CI runs get another attempt.

The fixtures are real runs from 2026-11-18, captured from the GitHub API (jobs,
annotations, and the workflow's runs on the branch as of when the attempt
completed). The script runs on main from `.github/workflows/rerun-unacquired.yml`,
where nothing else can test it.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / ".github" / "scripts" / "rerun_unacquired.py"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ci" / "rerun-unacquired"

_spec = importlib.util.spec_from_file_location("rerun_unacquired", SCRIPT)
assert _spec and _spec.loader
rerun = importlib.util.module_from_spec(_spec)
sys.modules["rerun_unacquired"] = rerun
_spec.loader.exec_module(rerun)


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def decide(fx: dict[str, Any]):
    return rerun.decide(fx["run"], fx["jobs"], fx["annotations"], fx["branch_runs"])


def _job(fx: dict[str, Any], prefix: str) -> dict[str, Any]:
    return next(j for j in fx["jobs"] if j["name"].startswith(prefix))


# ----- the runs GitHub left without a runner get another attempt ---------------


def test_a_pr_run_whose_macos_leg_never_got_a_runner_is_rerun() -> None:
    d = decide(load("plugin-test-pr-unacquired"))
    assert d.rerun, d.reason
    assert d.unacquired == ["test (macos-latest, 3.12, dev, 6)"]
    assert "attempt 3" in d.reason  # this was attempt 2


def test_a_main_push_whose_scouttests_never_got_a_runner_is_rerun() -> None:
    d = decide(load("app-ci-main-unacquired"))
    assert d.rerun, d.reason
    assert d.unacquired == ["ScoutTests"]


# ----- everything else is left alone --------------------------------------------


def test_a_green_run_is_left_alone() -> None:
    assert not decide(load("app-ci-pr-success")).rerun


def test_a_run_whose_jobs_ran_and_failed_is_left_alone() -> None:
    d = decide(load("plugin-test-pr-real-failure"))
    assert not d.rerun
    assert "no job was left without a runner" in d.reason


def test_a_job_cancelled_by_our_own_concurrency_group_is_not_rerun() -> None:
    fx = load("app-ci-pr-concurrency-cancelled")
    assert all("higher priority waiting request" in a["message"] for notes in fx["annotations"].values() for a in notes)
    fx["branch_runs"] = [r for r in fx["branch_runs"] if r["id"] <= fx["run"]["id"]]  # judge the cancel alone
    d = decide(fx)
    assert not d.rerun
    assert "no job was left without a runner" in d.reason


def test_a_real_failure_beside_an_unacquired_job_keeps_the_whole_run_from_rerunning() -> None:
    # --failed would rerun the failing ubuntu leg too.
    fx = load("plugin-test-pr-unacquired")
    ubuntu = _job(fx, "test (ubuntu-latest, 3.11")
    ubuntu["conclusion"] = "failure"
    d = decide(fx)
    assert not d.rerun
    assert "test (ubuntu-latest, 3.11, dev, 8) (failure, 13 steps)" in d.reason


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"steps": [{"name": "Set up job", "conclusion": "success"}]}, id="it-had-started"),
        pytest.param({"runner_name": "GitHub Actions 1000018557"}, id="it-had-a-runner"),
    ],
)
def test_a_job_that_got_a_runner_and_was_cancelled_is_not_unacquired(change: dict[str, Any]) -> None:
    fx = load("plugin-test-pr-unacquired")
    _job(fx, "test (macos-latest").update(change)
    d = decide(fx)
    assert not d.rerun
    assert "no job was left without a runner" in d.reason


def test_a_cancelled_job_without_githubs_note_is_not_rerun() -> None:
    # A person cancelling a queued job leaves 0 steps and no runner too.
    fx = load("plugin-test-pr-unacquired")
    fx["annotations"] = {k: [a for a in v if "not acquired" not in a["message"]] for k, v in fx["annotations"].items()}
    assert not decide(fx).rerun


def test_a_superseded_run_is_not_rerun() -> None:
    fx = load("app-ci-main-unacquired")
    fx["branch_runs"].insert(0, {"id": fx["run"]["id"] + 1, "created_at": "2026-11-18T15:30:00Z"})
    d = decide(fx)
    assert not d.rerun
    assert d.reason.startswith("superseded: push on main")


def test_the_third_attempt_is_the_last() -> None:
    fx = load("plugin-test-pr-unacquired")
    fx["run"]["run_attempt"] = 3
    d = decide(fx)
    assert not d.rerun
    assert "cap 3" in d.reason


def test_another_workflows_run_is_never_touched() -> None:
    fx = load("app-ci-main-unacquired")
    fx["run"]["name"] = "contract"
    assert not decide(fx).rerun


# ----- main(): what it asks gh, what it prints, when it reruns ------------------


class FakeGh:
    """Answers the `gh api` calls from a fixture and records `gh run rerun`."""

    def __init__(self, fx: dict[str, Any]) -> None:
        self.fx = copy.deepcopy(fx)
        self.reruns: list[list[str]] = []

    def __call__(self, cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        assert cmd[0] == "gh"
        if cmd[1:3] == ["run", "rerun"]:
            self.reruns.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        path = next(a for a in cmd if a.startswith("repos/"))
        if path.endswith("/jobs?per_page=100"):
            out: Any = [{"jobs": self.fx["jobs"]}]  # --paginate --slurp: a list of pages
        elif "/check-runs/" in path:
            out = self.fx["annotations"].get(path.split("/")[-2], [])
        elif path.endswith("/runs"):
            out = {"workflow_runs": self.fx["branch_runs"]}
        else:
            out = self.fx["run"]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")


@pytest.mark.parametrize(
    ("name", "reruns"),
    [("plugin-test-pr-unacquired", True), ("plugin-test-pr-real-failure", False), ("app-ci-pr-success", False)],
)
def test_main_reruns_only_what_decide_says_and_logs_why(
    name: str, reruns: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fx = load(name)
    gh = FakeGh(fx)
    monkeypatch.setattr(rerun.subprocess, "run", gh)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    run_id, attempt = fx["run"]["id"], fx["run"]["run_attempt"]
    assert rerun.main(["x", "Raven-Scout/Scout", str(run_id), str(attempt)]) == 0

    line = capsys.readouterr().out.strip()
    assert f"actions/runs/{run_id}/attempts/{attempt}" in line
    assert line.split(": ", 1)[1].startswith("RERUN" if reruns else "left alone")
    assert summary.read_text().strip() == line
    assert gh.reruns == ([["gh", "run", "rerun", str(run_id), "--failed", "-R", "Raven-Scout/Scout"]] if reruns else [])


def test_main_wants_three_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert rerun.main(["x", "Raven-Scout/Scout"]) == 2
    assert "usage:" in capsys.readouterr().err


def test_the_workflow_runs_main_and_asks_only_for_what_it_needs() -> None:
    import yaml

    wf = yaml.safe_load((REPO / ".github" / "workflows" / "rerun-unacquired.yml").read_text())
    on = wf[True]  # YAML 1.1 reads the bare key `on` as True
    assert on == {"workflow_run": {"workflows": ["app-ci", "plugin-test"], "types": ["completed"]}}
    assert sorted(on["workflow_run"]["workflows"]) == sorted(rerun.WORKFLOWS)
    assert wf["permissions"] == {"actions": "write", "checks": "read"}
    steps = wf["jobs"]["rerun"]["steps"]
    assert [s.get("uses") for s in steps if "uses" in s] == ["actions/checkout@v4"]
    assert "secrets." not in json.dumps(wf)
    assert ".github/scripts/rerun_unacquired.py" in steps[-1]["run"]
