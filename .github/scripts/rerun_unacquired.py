#!/usr/bin/env python3
"""Rerun a CI run whose only problem is a job GitHub never gave a runner.

GitHub's free macOS arm64 runners are short of capacity (2026-11). A job that
waits too long for one is cancelled by GitHub itself, with 0 steps, no runner,
and the annotation "The job was not acquired by Runner of type hosted even
after multiple attempts". Its aggregator (app-ci / plugin-test, the required
checks) then fails. Nothing is wrong with the change; the run just needs
another attempt. `.github/workflows/rerun-unacquired.yml` calls this after
every app-ci or plugin-test run that didn't succeed.

It reruns (`gh run rerun <id> --failed`) only when all of these hold:

- at least one job was cancelled with 0 steps, no runner, and that annotation;
- every other job that didn't succeed is an aggregator (app-ci, plugin-test),
  whose failure only reports the cancelled job. A job that ran and failed, or
  one cancelled any other way (a concurrency group, a person, a timeout), means
  the run is left alone, since `--failed` would rerun those too;
- the run is its workflow's newest on that branch and event: a newer push has
  its own run, so the old one is not worth a runner;
- this was attempt 1 or 2. Attempt 3 is the last (MAX_ATTEMPTS).

Usage: rerun_unacquired.py <owner/repo> <run_id> <run_attempt>
Needs `gh` and GH_TOKEN (actions: write to rerun, checks: read for annotations).
Prints every decision. With GITHUB_STEP_SUMMARY set, writes it there too.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

NOT_ACQUIRED = "was not acquired by Runner"
AGGREGATORS = frozenset({"app-ci", "plugin-test"})
WORKFLOWS = frozenset({"app-ci", "plugin-test"})
MAX_ATTEMPTS = 3
OK_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})


@dataclass
class Decision:
    rerun: bool
    reason: str
    unacquired: list[str] = field(default_factory=list)


def is_unacquired(job: dict[str, Any], annotations: list[dict[str, Any]]) -> bool:
    """Cancelled before any runner took it, and GitHub says it could not acquire one."""
    return (
        job.get("conclusion") == "cancelled"
        and not job.get("steps")
        and not job.get("runner_name")
        and any(NOT_ACQUIRED in (a.get("message") or "") for a in annotations)
    )


def decide(
    run: dict[str, Any],
    jobs: list[dict[str, Any]],
    annotations: dict[str, list[dict[str, Any]]],
    branch_runs: list[dict[str, Any]],
) -> Decision:
    """Whether to rerun ``run``'s failed jobs. Pure: every input is API data."""
    if run.get("name") not in WORKFLOWS:
        return Decision(False, f"workflow {run.get('name')!r} is not one this reruns")
    if run.get("status") != "completed" or run.get("conclusion") in OK_CONCLUSIONS:
        return Decision(False, f"run is {run.get('status')}/{run.get('conclusion')}, nothing to rerun")

    unacquired, others = [], []
    for job in jobs:
        if job.get("conclusion") in OK_CONCLUSIONS:
            continue
        if is_unacquired(job, annotations.get(str(job["id"]), [])):
            unacquired.append(job["name"])
        elif job["name"] not in AGGREGATORS:
            state = job.get("conclusion") or job.get("status")
            others.append(f"{job['name']} ({state}, {len(job.get('steps') or [])} steps)")

    if not unacquired:
        return Decision(False, "no job was left without a runner by GitHub")
    if others:
        return Decision(
            False, "other jobs didn't succeed and --failed would rerun them: " + "; ".join(others), unacquired
        )

    newer = [r["id"] for r in branch_runs if r["id"] > run["id"]]
    if newer:
        return Decision(
            False, f"superseded: {run.get('event')} on {run.get('head_branch')} has newer run(s) {newer}", unacquired
        )

    attempt = int(run.get("run_attempt") or 1)
    if attempt >= MAX_ATTEMPTS:
        return Decision(False, f"attempt {attempt} was the last (cap {MAX_ATTEMPTS})", unacquired)

    return Decision(
        True, f"GitHub never gave a runner to: {', '.join(unacquired)}; starting attempt {attempt + 1}", unacquired
    )


def _gh(*args: str) -> Any:
    out = subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def _fetch(repo: str, run_id: int, attempt: int) -> tuple[dict, list, dict, list]:
    base = f"repos/{repo}/actions/runs/{run_id}"
    run = _gh("api", f"{base}/attempts/{attempt}")
    jobs = _gh("api", "--paginate", "--slurp", f"{base}/attempts/{attempt}/jobs?per_page=100")
    jobs = [j for page in jobs for j in page["jobs"]]
    annotations = {
        str(j["id"]): _gh("api", f"repos/{repo}/check-runs/{j['id']}/annotations") or []
        for j in jobs
        if j.get("conclusion") == "cancelled" and not j.get("steps")
    }
    branch_runs = _gh(
        "api",
        "-X", "GET",
        f"repos/{repo}/actions/workflows/{run['workflow_id']}/runs",
        "-f", f"branch={run['head_branch']}",
        "-f", f"event={run['event']}",
        "-f", "per_page=20",
    )["workflow_runs"]  # fmt: skip
    return run, jobs, annotations, branch_runs


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: rerun_unacquired.py <owner/repo> <run_id> <run_attempt>", file=sys.stderr)
        return 2
    repo, run_id, attempt = argv[1], int(argv[2]), int(argv[3])
    run, jobs, annotations, branch_runs = _fetch(repo, run_id, attempt)
    decision = decide(run, jobs, annotations, branch_runs)

    url = f"https://github.com/{repo}/actions/runs/{run_id}/attempts/{attempt}"
    line = f"{run.get('name')} {url} ({run.get('event')} on {run.get('head_branch')}): " + (
        f"RERUN: {decision.reason}" if decision.rerun else f"left alone: {decision.reason}"
    )
    print(line)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    if decision.rerun:
        subprocess.run(["gh", "run", "rerun", str(run_id), "--failed", "-R", repo], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
