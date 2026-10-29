"""busy-weekday's deadline seeds against `action-items check-tier-ceiling`.

PROJ-1211 (a Linear issue due in 12 days) is the task runs put in 🔴 in 6/29
enriched runs; PROJ-1210 is due in 40. Both scenarios that score placement
must keep a 12-day seed whose expected tier matches the gate's ceiling, and
the gate must flag the miss and pass the expected placement. Tier 0: no
model runs.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from scout.action_items.tier_ceiling import _RANK, ceiling, check_tier_ceiling
from tests.eval.harness.dates import render_dates
from tests.eval.harness.scenario import load_scenario

TODAY = dt.date(2026, 10, 12)  # a Monday
SCENARIOS = ("busy-weekday", "busy-briefing")


def _due_dates(name: str) -> dict[str, dt.date]:
    """Linear identifier → due date, from the scenario's issue fixture."""
    raw = (load_scenario(name).files / "fixtures" / "linear-issues.json").read_text(encoding="utf-8")
    issues = json.loads(render_dates(raw, TODAY))["issues"]
    return {i["identifier"]: dt.date.fromisoformat(i["dueDate"]) for i in issues if i.get("dueDate")}


@pytest.mark.parametrize("name", SCENARIOS)
def test_a_twelve_day_seed_is_scored_at_its_ceiling(name: str) -> None:
    scenario = load_scenario(name)
    due = _due_dates(name)
    assert (due["PROJ-1211"] - TODAY).days == 12
    assert scenario.expected_placement["ref:PROJ-1211"] == {"🟡"}
    for ref, allowed in scenario.expected_placement.items():
        ident = ref.removeprefix("ref:")
        if ident in due and due[ident] >= TODAY:
            cap = ceiling((due[ident] - TODAY).days)
            assert max(_RANK[t] for t in allowed) <= _RANK[cap], f"{name}: {ref} is expected above {cap}"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "vault"
    (data / "action-items").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(data))
    return data


def _carried() -> str:
    """busy-weekday's yesterday file, rendered and carried to today."""
    seed = load_scenario("busy-weekday").files / "vault" / "action-items" / "action-items-{{d-1}}.md"
    text = render_dates(seed.read_text(encoding="utf-8"), TODAY)
    return text.replace(f"# Action Items — {TODAY - dt.timedelta(days=1)}", f"# Action Items — {TODAY}")


def _today(vault: Path, **placed: str) -> None:
    """Today's file: the carried seeds plus PROJ-1210/1211, each under the section given."""
    due = _due_dates("busy-weekday")
    lines = {
        "PROJ-1211": f"- [ ] [#QMIG] **Write the catalog quota migration** — due {due['PROJ-1211']}.\n"
        "  - Refs: PROJ-1211\n",
        "PROJ-1210": f"- [ ] [#DASH] **Plan the catalog analytics dashboard** — due {due['PROJ-1210']}.\n"
        "  - Refs: PROJ-1210\n",
    }
    text = _carried()
    for ident, section in placed.items():
        heading = next(h for h in text.splitlines() if h.startswith(f"## {section}"))
        text = text.replace(heading + "\n", heading + "\n\n" + lines[ident.replace("_", "-")], 1)
    (vault / "action-items" / f"action-items-{TODAY}.md").write_text(text, encoding="utf-8")


def test_the_carried_seeds_pass_as_they_stand(vault: Path) -> None:
    _today(vault)
    assert check_tier_ceiling(today=TODAY.isoformat()).passed


def test_the_twelve_day_task_in_red_is_flagged(vault: Path) -> None:
    _today(vault, PROJ_1211="🔴", PROJ_1210="🟢")
    result = check_tier_ceiling(today=TODAY.isoformat())
    assert [(f.tag, f.problem.split(" (")[0]) for f in result.findings] == [("QMIG", "in 🔴, ceiling 🟡")]


def test_the_expected_placement_passes(vault: Path) -> None:
    _today(vault, PROJ_1211="🟡", PROJ_1210="🟢")
    result = check_tier_ceiling(today=TODAY.isoformat())
    assert result.passed
    assert {p.tag: p.ceiling for p in result.checked if p.tag in {"QMIG", "DASH"}} == {"QMIG": "🟡", "DASH": "🟢"}
