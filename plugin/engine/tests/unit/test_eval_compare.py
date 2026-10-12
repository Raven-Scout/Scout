"""Slice 4: samples on disk, checkouts, the base cache, verdicts and `eval.sh compare` (spec 2026-10-12 §8).

No test starts the real claude: samples come from a fake claude through the real runner.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tests.eval.harness.runner import load_sample, run_sample
from tests.eval.harness.scenario import load_scenario
from tests.unit.test_eval_runner import FAKE_CLAUDE

MONDAY = date(2026, 10, 12)
SERVERS = "claude_ai_Google_Calendar,claude_ai_Linear,claude_ai_Slack"


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "fake-claude"
    path.write_text(FAKE_CLAUDE)
    path.chmod(0o755)
    monkeypatch.setenv("EVAL_FAKE_SERVERS", SERVERS)
    monkeypatch.setenv("EVAL_FAKE_ENRICH", "1")
    return path


def test_a_sample_round_trips_through_its_result_dir(tmp_path: Path, fake: Path) -> None:
    ran = run_sample(
        load_scenario("busy-weekday"), today=MONDAY, rep=2, model="sonnet", results_root=tmp_path / "r",
        real_claude=str(fake), arm="base", cli_version="9.9.9 (Claude Code)",
    )  # fmt: skip
    loaded = load_sample(ran.result_dir)
    assert loaded == ran
    assert loaded.meta["arm"] == "base"
    assert loaded.meta["resolved_model"] == "claude-sonnet-5"
    assert loaded.meta["cli_version"] == "9.9.9 (Claude Code)"
    assert loaded.meta["today"] == "2026-10-12"


def test_an_aborted_sample_round_trips_with_its_reason(
    tmp_path: Path, fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVAL_FAKE_SUBTYPE", "error_max_budget_usd")
    ran = run_sample(
        load_scenario("busy-weekday"), today=MONDAY, rep=1, model="sonnet", results_root=tmp_path / "r",
        real_claude=str(fake),
    )  # fmt: skip
    loaded = load_sample(ran.result_dir)
    assert loaded.aborted == {"reason": "error_max_budget_usd"}
    assert loaded == ran
