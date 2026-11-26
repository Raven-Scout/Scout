"""CI's serial perf step must not spend its time deleting the parallel step's temp tree.

Both pytest steps of ``plugin-test`` run on one runner. Left to pytest's default
temp folder, the perf session ends by removing the suite's ``pytest-N`` folder
(3.6 GB, ~760k entries): 55-140 s per leg, more than the perf tests take. A
session given ``--basetemp`` removes nothing but its own folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ENGINE = Path(__file__).resolve().parents[2]
WORKFLOW = ENGINE.parents[1] / ".github" / "workflows" / "plugin-test.yml"


def _pytest_runs() -> list[str]:
    if not WORKFLOW.exists():
        pytest.skip("plugin-test.yml only exists in a Raven-Scout/Scout checkout")
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["test"]["steps"]
    return [str(s["run"]) for s in steps if "bin/pytest" in str(s.get("run", ""))]


def test_the_perf_step_has_a_temp_folder_of_its_own() -> None:
    parallel, perf = _pytest_runs()
    assert '-m "not perf"' in parallel and "-m perf " in perf
    assert '--basetemp="$RUNNER_TEMP/' in perf
    # The suite itself stays on pytest's default: xdist workers share its numbered folder.
    assert "--basetemp" not in parallel
