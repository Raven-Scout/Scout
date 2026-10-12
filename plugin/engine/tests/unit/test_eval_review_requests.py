"""busy-weekday's review-request seeds reach `action-items check-review-requests` (#404).

The runner's pre-session step asks the fake ``gh`` for the review requests with
the engine's own arguments; the check must then flag the team-only request
(``example-org/api#40``) until today's file places it. Tier 0: no model runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scout.action_items.review_requests import check_review_requests
from scout.scripts import pre_session_data as psd
from tests.eval.harness.scenario import load_scenario
from tests.eval.harness.stubs.gh import GH_BIN_DIR

ENGINE = Path(__file__).resolve().parents[2]
TODAY = "2026-10-12"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    busy = load_scenario("busy-weekday")
    monkeypatch.setenv("PATH", f"{GH_BIN_DIR}:/usr/bin:/bin")
    monkeypatch.setenv("SCOUT_EVAL_PYTHON", sys.executable)
    monkeypatch.setenv("SCOUT_EVAL_ENGINE", str(ENGINE))
    monkeypatch.setenv("SCOUT_EVAL_SCRIPT", str(busy.script))
    monkeypatch.setenv("SCOUT_EVAL_CALLS", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("SCOUT_EVAL_TODAY", TODAY)
    data = tmp_path / "vault"
    (data / "action-items").mkdir(parents=True)
    ctx = psd.SessionContext(
        generated_at=f"{TODAY}T07:00:00",
        session_type="morning-consolidation",
        pr_review_requested=psd.get_pr_review_requested(),
    )
    psd.write_context(ctx, data / ".scout-cache" / psd.OUTPUT_FILENAME)
    return data


def _today_file(vault: Path, body: str) -> None:
    (vault / "action-items" / f"action-items-{TODAY}.md").write_text(f"# Action Items — {TODAY}\n\n{body}")


def test_the_prefetch_serves_both_seeds_and_the_check_flags_the_team_only_one(vault: Path) -> None:
    _today_file(vault, "## 🟡 To Do\n\n- [ ] [#RV12] **Review the rate limits**\n  - Refs: example-org/widgets#12\n")
    result = check_review_requests(data_dir=vault, today=TODAY)
    assert [r.ref for r in result.checked] == ["example-org/widgets#12", "example-org/api#40"]
    assert [r.ref for r in result.unaccounted] == ["example-org/api#40"]


def test_placing_it_in_watching_clears_the_check(vault: Path) -> None:
    _today_file(
        vault,
        "## 🟡 To Do\n\n- [ ] [#RV12] **Review the rate limits**\n  - Refs: example-org/widgets#12\n\n"
        "## 🟢 Watching\n\n- [ ] [#APIDOC] **Docs auth-header PR in the team queue** — team request\n"
        "  - Refs: example-org/api#40\n",
    )
    assert check_review_requests(data_dir=vault, today=TODAY).unaccounted == ()
