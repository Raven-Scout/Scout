"""What the runner reads back from a finished sample for the slice 5 checks."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from tests.eval.harness.build import build_vault
from tests.eval.harness.runner import read_outputs
from tests.eval.harness.scenario import load_scenario

MONDAY = date(2026, 10, 12)


def test_read_outputs_gathers_notes_wrap_run_notes_review_and_calls(tmp_path: Path) -> None:
    vault = build_vault(tmp_path / "vault", today=MONDAY)
    ctx = vault / "action-items" / "context"
    ctx.mkdir(parents=True)
    (ctx / "NEW.md").write_text("---\ntag: NEW\n---\n")
    (vault / "action-items" / "action-items-2026-10-12.md").write_text(
        "## 🟡 To Do\n- [ ] [#NEW] **x** — y\n\n"
        "## 🪵 Run notes & connector availability\n- 09:00 Linear ✗ auth expired\n"
    )
    review = vault / "knowledge-base" / "review-queue.md"
    review.write_text(review.read_text() + "\n- dropoff: RUNB\n")
    calls = tmp_path / "calls.jsonl"
    rows = [
        {"persona": "linear", "tool": "list_issues", "kind": "error", "capture": None, "args": {}},
        {"persona": "slack", "tool": "slack_send_message", "kind": "ok", "capture": "wrap",
         "args": {"channel_id": "U0123456789", "message": "Run done. Linear unreachable."}},
        {"persona": "slack", "tool": "slack_search_public", "kind": "ok", "capture": None, "args": {}},
    ]  # fmt: skip
    calls.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = read_outputs(vault, calls, MONDAY)
    assert out.notes == {"NEW": "---\ntag: NEW\n---\n"}
    assert out.wrap == "Run done. Linear unreachable."
    assert out.run_notes.strip() == "- 09:00 Linear ✗ auth expired"
    assert out.review_added.strip() == "- dropoff: RUNB"
    assert out.calls == {"linear": 1, "slack": 2}


def test_read_outputs_with_nothing_written(tmp_path: Path) -> None:
    vault = build_vault(tmp_path / "vault", today=MONDAY)
    out = read_outputs(vault, tmp_path / "none.jsonl", MONDAY)
    assert (out.notes, out.wrap, out.run_notes, out.review_added, out.calls) == ({}, None, None, "", {})


def test_scenario_rejects_an_unknown_check_before_any_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tests.eval.harness.scenario as scenario

    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "scenario.yaml").write_text(yaml.safe_dump({"mode": "m", "checks": ["placement", "plcement"]}))
    monkeypatch.setattr(scenario, "SCENARIOS_DIR", tmp_path)
    with pytest.raises(ValueError, match="plcement"):
        scenario.load_scenario("bad")


def test_busy_weekday_runs_the_slice_5_daily_checks() -> None:
    busy = load_scenario("busy-weekday")
    assert {"context_note", "waiting_format", "no_collapse", "body_budget"} <= set(busy.checks)
    assert busy.expected_waiting == ("BILL", "ref:example-org/widgets#22")


def test_connector_down_scenario_is_consistent() -> None:
    from scout.action_items.parser import parse_lines

    sc = load_scenario("connector-down")
    assert sc.mode == "morning-consolidation"
    assert set(sc.personas_down) == {"linear", "email"} and set(sc.personas_down) < set(sc.personas)
    assert set(sc.checks) == {"outage_reported", "no_false_outage", "dropoff_caught", "no_drop"}
    files = sc.path / "vault" / "action-items"
    yesterday = parse_lines((files / "action-items-{{d-1}}.md").read_text().splitlines())
    today = parse_lines((files / "action-items-{{today}}.md").read_text().splitlines())
    open_yesterday = {i.short_prefix for i in yesterday if i.status != "done"}
    in_today = {i.short_prefix for i in today}
    assert set(sc.dropped_tags) <= open_yesterday, "dropped tags must be open yesterday"
    assert not set(sc.dropped_tags) & in_today, "dropped tags must be missing from the pre-seeded today file"
    assert open_yesterday - set(sc.dropped_tags) <= in_today, "everything else is carried"
    assert "Mechanical carry-forward" not in (files / "action-items-{{today}}.md").read_text()
