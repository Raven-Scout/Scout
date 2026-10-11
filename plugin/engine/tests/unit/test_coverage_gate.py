"""The coverage floor must fail CI whenever the total is below it.

pytest-cov prints ``FAIL Required test coverage of 98.0% not reached`` from the
raw total, but decides the exit code with coverage's ``should_fail_under``,
which first rounds the total to ``[tool.coverage.report] precision`` digits.
Left at coverage's default of 0, a 97.82% total rounds to 98 and exits 0 while
the log says FAIL, and a 97.44% total rounds to 97 and exits 1 — so the
required ``plugin-test`` gate let some sub-floor merges through and blocked
others. These tests pin the config and the CI job so the floor is enforced
deterministically.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from coverage.results import should_fail_under

ENGINE = Path(__file__).resolve().parents[2]
WORKFLOW = ENGINE.parents[1] / ".github" / "workflows" / "plugin-test.yml"


def _report_config() -> dict[str, Any]:
    pyproject = tomllib.loads((ENGINE / "pyproject.toml").read_text(encoding="utf-8"))
    report: dict[str, Any] = pyproject["tool"]["coverage"]["report"]
    return report


def test_total_just_under_the_floor_fails() -> None:
    report = _report_config()
    floor = float(report["fail_under"])
    precision = int(report.get("precision", 0))

    # 0.01pp under the floor — e.g. 97.99% against 98 — must be a failure,
    # not rounded up onto the floor.
    assert should_fail_under(floor - 0.01, floor, precision)
    assert not should_fail_under(floor, floor, precision)


def test_main_regression_total_fails() -> None:
    # The total main actually shipped with (ebc8d5e, 128bbba) while green.
    report = _report_config()
    assert should_fail_under(97.82, float(report["fail_under"]), int(report.get("precision", 0)))


def _coverage_job() -> dict[str, Any]:
    if not WORKFLOW.exists():
        pytest.skip("plugin-test.yml only exists in a Raven-Scout/Scout checkout")
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job: dict[str, Any] = workflow["jobs"]["coverage"]
    return job


def test_coverage_job_does_not_mask_the_exit_code() -> None:
    job = _coverage_job()
    assert not job.get("continue-on-error"), "the coverage job must fail when pytest does"
    steps = job["steps"]
    pytest_steps = [s for s in steps if "--cov" in str(s.get("run", ""))]
    assert len(pytest_steps) == 1, "expected exactly one coverage pytest step"
    step = pytest_steps[0]
    assert not step.get("continue-on-error")
    run = step["run"]
    for mask in ("|| true", "|| :", "|| exit 0", "set +e"):
        assert mask not in run, f"coverage step masks pytest's exit code with {mask!r}"


def test_coverage_gate_needs_the_coverage_job() -> None:
    if not WORKFLOW.exists():
        pytest.skip("plugin-test.yml only exists in a Raven-Scout/Scout checkout")
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert "coverage" in workflow["jobs"]["plugin-test"]["needs"]
