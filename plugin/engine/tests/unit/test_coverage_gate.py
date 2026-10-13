"""The coverage floor must fail CI whenever the total is below it.

pytest-cov prints ``FAIL Required test coverage of 98.0% not reached`` from the
raw total, but decides the exit code with coverage's ``should_fail_under``,
which first rounds the total to ``[tool.coverage.report] precision`` digits.
Left at coverage's default of 0, a 97.82% total rounds to 98 and exits 0 while
the log says FAIL, and a 97.44% total rounds to 97 and exits 1 — so the
required ``plugin-test`` gate let some sub-floor merges through and blocked
others. These tests pin the config and the CI steps so the floor is enforced
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


def _test_job() -> dict[str, Any]:
    if not WORKFLOW.exists():
        pytest.skip("plugin-test.yml only exists in a Raven-Scout/Scout checkout")
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job: dict[str, Any] = workflow["jobs"]["test"]
    return job


def _masks(step: dict[str, Any]) -> list[str]:
    run = str(step.get("run", ""))
    return [m for m in ("|| true", "|| :", "|| exit 0", "set +e") if m in run]


def test_exactly_one_matrix_leg_measures_coverage_with_the_tui_installed() -> None:
    legs = _test_job()["strategy"]["matrix"]["include"]
    covered = [leg for leg in legs if leg.get("coverage")]
    assert len(covered) == 1, "expected exactly one coverage leg"
    # Without [full], every scout/tui test importorskips textual and the total
    # drops ~3 points under the floor.
    assert "full" in covered[0]["extras"].split(",")


def test_the_coverage_floor_step_checks_the_combined_total_and_fails_the_job() -> None:
    job = _test_job()
    assert not job.get("continue-on-error"), "the test job must fail when the floor does"
    steps = job["steps"]
    floor = [s for s in steps if "coverage report" in str(s.get("run", ""))]
    assert len(floor) == 1, "expected exactly one `coverage report` floor step"
    assert floor[0]["if"] == "matrix.coverage"
    assert not floor[0].get("continue-on-error")
    assert not _masks(floor[0]), f"floor step masks its exit code with {_masks(floor[0])}"
    # Every pytest step collecting coverage runs before the floor, and appends
    # after the first, so the floor sees the whole suite's data.
    cov_steps = [s for s in steps if "--cov" in str(s.get("run", ""))]
    assert len(cov_steps) == 2
    assert all(steps.index(s) < steps.index(floor[0]) for s in cov_steps)
    assert "--cov-append" not in cov_steps[0]["run"] and "--cov-append" in cov_steps[1]["run"]
    for s in cov_steps:
        assert not s.get("continue-on-error")
        assert not _masks(s), f"coverage step masks pytest's exit code with {_masks(s)}"


def test_coverage_gate_needs_the_test_job() -> None:
    if not WORKFLOW.exists():
        pytest.skip("plugin-test.yml only exists in a Raven-Scout/Scout checkout")
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert "test" in workflow["jobs"]["plugin-test"]["needs"]
