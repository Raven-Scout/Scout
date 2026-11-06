"""The KB-writing release gate: its fixture, its scorer and its JSON report (tests/eval/harness/kb_gate.py).

Tier 0: no test here starts a model. The scorer runs on canned before/after snapshots
(tests/eval/golden/kb-front-page/), and the one end-to-end test uses a stand-in claude.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from scout.kb.structure import Marker
from tests.eval.harness.build import EVAL_ROOT, build_vault
from tests.eval.harness.checks.kb_writing import (
    KB_CHECKS,
    KbFact,
    blocks,
    front_page_growth,
    front_page_no_narration,
    kb_facts_cited,
)
from tests.eval.harness.dates import render_dates
from tests.eval.harness.kb_gate import exit_code, gate_report, score_kb, spec_for, structure_row
from tests.eval.harness.scenario import load_scenario

TODAY = date(2026, 10, 14)
SCENARIO = EVAL_ROOT / "scenarios" / "kb-front-page"
GOLDEN = EVAL_ROOT / "golden" / "kb-front-page"
PROJECT = "knowledge-base/projects/report-builder"


@pytest.fixture(scope="module")
def scenario():
    return load_scenario("kb-front-page")


def _git(vault: Path, *args: str, when: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    stamp = f"{when}+00:00"
    env.update(GIT_AUTHOR_NAME="Alex Example", GIT_AUTHOR_EMAIL="alex@example.com", GIT_AUTHOR_DATE=stamp,
               GIT_COMMITTER_NAME="Alex Example", GIT_COMMITTER_EMAIL="alex@example.com",
               GIT_COMMITTER_DATE=stamp, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")  # fmt: skip
    subprocess.run(["git", *args], cwd=vault, env=env, check=True, capture_output=True)


def _lay(src: Path, dest: Path) -> None:
    for path in sorted(p for p in src.rglob("*") if p.is_file() and p.suffix == ".md" and p.name != "README.md"):
        target = dest / render_dates(path.relative_to(src).as_posix(), TODAY)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_dates(path.read_text(encoding="utf-8"), TODAY), encoding="utf-8")


def _snapshot(tmp_path: Path, *cases: str) -> Path:
    """A small vault: the scenario's project folder as the fixture commit, then each golden case
    laid over it, committed the way a run commits."""
    vault = tmp_path / "vault"
    _lay(SCENARIO / "vault" / PROJECT, vault / PROJECT)
    _git(vault, "init", "-q", "-b", "main", when=f"{TODAY}T06:00:00")
    _git(vault, "add", "-A", when=f"{TODAY}T06:00:00")
    _git(vault, "commit", "-q", "-m", "fixture", when=f"{TODAY}T06:00:00")
    for case in cases:
        _lay(GOLDEN / case, vault)
    if cases:
        _git(vault, "add", "-A", when=f"{TODAY}T07:10:00")
        _git(vault, "commit", "-q", "-m", "run", when=f"{TODAY}T07:10:00")
    return vault


def _verdicts(vault: Path, scenario) -> dict[str, bool | None]:
    return {c.name: c.passed for c in score_kb(vault, spec_for(scenario))}


# ----- the fixture ---------------------------------------------------------------------------


def test_the_scenario_loads_with_the_kb_checks(scenario) -> None:
    assert scenario.mode.endswith("consolidation")
    assert scenario.checks == KB_CHECKS
    assert scenario.kb_project == "report-builder"
    assert scenario.max_front_page_growth == 512
    assert [f.id for f in scenario.expected_kb_facts] == ["pr-57-merged", "proj-1240-done", "row-cap-200k"]


def test_the_built_fixture_folder_meets_the_page_standard(tmp_path: Path, scenario) -> None:
    """The real fixture build (bootstrap install + overlays): `kb structure` shows the folder ✓, and
    an untouched vault passes every check but the facts, which no run has filed yet."""
    vault = build_vault(tmp_path / "v", today=TODAY, scenario_dir=scenario.files)
    row = structure_row(vault, "report-builder")
    assert row is not None and row["conforms"], row
    assert not row["over_target"]
    assert _verdicts(vault, scenario) == {
        "kb_structure": True,
        "front_page_growth": True,
        "kb_facts_cited": False,
        "front_page_no_narration": True,
    }


def test_every_signal_is_in_a_connector_fixture(scenario) -> None:
    """Each expected fact is in what the scripted connectors serve, and not yet in the KB."""
    served = "\n".join(p.read_text(encoding="utf-8") for p in (SCENARIO / "fixtures").glob("*.json"))
    kb = "\n".join(p.read_text(encoding="utf-8") for p in (SCENARIO / "vault" / PROJECT).rglob("*.md"))
    assert '"state": "MERGED"' in served and "PROJ-1240" in served and "200,000" in served
    assert "200,000" not in kb and "merged" not in kb.lower()
    connectors = (SCENARIO / "connectors.yaml").read_text(encoding="utf-8")
    for persona in scenario.personas:
        assert re.search(rf"(?m)^{persona}:", connectors), persona


@pytest.mark.parametrize(
    ("persona", "tool", "args", "needle"),
    [
        ("gh", "*", {"argv": "pr view 57 --repo example-org/api --json state,mergedAt"}, '"state": "MERGED"'),
        ("gh", "*", {"argv": "pr list --repo example-org/api --state merged --json number"}, '"number": 57'),
        ("gh", "*", {"argv": "search prs --author @me --merged --json number,url"}, '"number": 57'),
        ("gh", "*", {"argv": "pr list --author @me --json number"}, "[]"),  # nothing open
        ("linear", "get_issue", {"id": "PROJ-1240"}, '"name": "Done"'),
        ("linear", "list_comments", {"issueId": "PROJ-1240"}, "Turned off the nightly PDF job"),
        ("calendar", "gcal_list_events", {"timeMin": "x"}, "Report builder weekly sync"),
        ("email", "get_thread", {"threadId": "thr-sync-notes"}, "from 50,000 to 200,000 rows"),
    ],
)
def test_the_scripted_connectors_serve_the_signals(persona: str, tool: str, args: dict, needle: str) -> None:
    from tests.eval.harness.stubs.gh import _stdout
    from tests.eval.harness.stubs.rules import Script

    script = Script.load(SCENARIO / "connectors.yaml", today=TODAY)
    reply = script.reply(persona, tool, args, prior_hits=lambda _rule: 0)
    assert reply.kind == "ok", reply
    text = _stdout(reply.payload["stdout"], SCENARIO, TODAY) if persona == "gh" else json.dumps(reply.payload)
    assert needle in text and "{{" not in text


# ----- the scorer on canned snapshots ---------------------------------------------------------


def test_a_good_run_passes_every_check(tmp_path: Path, scenario) -> None:
    vault = _snapshot(tmp_path, "good")
    assert _verdicts(vault, scenario) == dict.fromkeys(KB_CHECKS, True)


def test_a_narrated_update_paragraph_fails(tmp_path: Path, scenario) -> None:
    vault = _snapshot(tmp_path, "good", "narrated")
    results = {c.name: c for c in score_kb(vault, spec_for(scenario))}
    assert results["front_page_no_narration"].passed is False
    details = "\n".join(results["front_page_no_narration"].details)
    assert "narration marker: Last verified" in details and "narration marker: session name" in details
    assert "section 'Where it stands' gained 1 paragraph" in details
    assert results["kb_structure"].passed is False  # kb structure flags the markers too
    assert results["kb_facts_cited"].passed is True  # the knowledge notes are good's


def test_a_front_page_that_keeps_every_detail_fails_on_growth_only(tmp_path: Path, scenario) -> None:
    vault = _snapshot(tmp_path, "good", "grown")
    verdicts = _verdicts(vault, scenario)
    assert verdicts == {**dict.fromkeys(KB_CHECKS, True), "front_page_growth": False}


def test_the_growth_limit_is_the_scenarios(tmp_path: Path, scenario) -> None:
    import dataclasses

    vault = _snapshot(tmp_path, "good", "grown")
    roomy = dataclasses.replace(scenario, max_front_page_growth=100_000)
    assert _verdicts(vault, roomy)["front_page_growth"] is True
    tight = dataclasses.replace(scenario, max_front_page_growth=0)
    assert _verdicts(_snapshot(tmp_path / "g", "good"), tight)["front_page_growth"] is True  # good shrinks it
    assert _verdicts(_snapshot(tmp_path / "n", "good", "narrated"), tight)["front_page_growth"] is False


def test_facts_without_a_citation_fail(tmp_path: Path, scenario) -> None:
    vault = _snapshot(tmp_path, "good", "uncited")
    results = {c.name: c for c in score_kb(vault, spec_for(scenario))}
    assert results["kb_facts_cited"].passed is False
    assert all("without a citation" in d for d in results["kb_facts_cited"].details), results["kb_facts_cited"]
    assert {n: results[n].passed for n in KB_CHECKS if n != "kb_facts_cited"} == dict.fromkeys(
        ("kb_structure", "front_page_growth", "front_page_no_narration"), True
    )


def test_a_loose_file_beside_the_front_page_fails_structure(tmp_path: Path, scenario) -> None:
    vault = _snapshot(tmp_path, "good", "broken")
    results = {c.name: c for c in score_kb(vault, spec_for(scenario))}
    assert results["kb_structure"].passed is False and "1 loose" in results["kb_structure"].details[0]
    assert results["kb_facts_cited"].passed is True


def test_facts_filed_only_outside_knowledge_do_not_count(tmp_path: Path, scenario) -> None:
    vault = _snapshot(tmp_path, "broken")  # the updates file alone, cited, but beside the front page
    assert _verdicts(vault, scenario)["kb_facts_cited"] is False


def test_an_uncommitted_edit_counts(tmp_path: Path, scenario) -> None:
    vault = _snapshot(tmp_path)
    _lay(GOLDEN / "good", vault)  # written, never committed
    assert _verdicts(vault, scenario)["kb_facts_cited"] is True


def test_a_vault_without_a_fixture_commit_is_unevaluated_not_passed(tmp_path: Path, scenario) -> None:
    vault = tmp_path / "vault"
    _lay(SCENARIO / "vault" / PROJECT, vault / PROJECT)
    _git(vault, "init", "-q", "-b", "main", when=f"{TODAY}T06:00:00")
    checks = score_kb(vault, spec_for(scenario))
    assert {c.passed for c in checks} == {None}
    report = gate_report(vault, spec_for(scenario), checks, scenario="kb-front-page")
    assert report["verdict"] == "unevaluated" and exit_code(report) == 2


# ----- the pure checks ------------------------------------------------------------------------


def test_blocks_split_paragraphs_items_rows_and_headings() -> None:
    text = (
        "---\nas_of: 2026-10-14\n---\n# Title\n\nOne paragraph\nthat wraps.\n\n"
        "- an item\n  that continues\n- another item\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    )
    assert blocks(text) == [
        ("heading", "# Title"),
        ("prose", "One paragraph\nthat wraps."),
        ("item", "- an item\n  that continues"),
        ("item", "- another item"),
        ("row", "| a | b |"),
        ("row", "| 1 | 2 |"),
    ]


def test_a_fact_already_in_the_fixture_is_not_credited() -> None:
    fact = KbFact("x", (re.compile("200,000"),))
    note = "# T\n\nThe cap is 200,000 rows ([PR](https://github.com/example-org/api/pull/9)).\n"
    assert kb_facts_cited({"k.md": note}, {"k.md": note}, [fact]).passed is False
    moved = {"other.md": note}
    assert kb_facts_cited({"k.md": note}, moved, [fact]).passed is False  # moved, not new
    rewritten = {"k.md": note.replace("The cap", "Since Tuesday the cap")}
    assert kb_facts_cited({"k.md": note}, rewritten, [fact]).passed is True


@pytest.mark.parametrize(
    ("cite", "ok"),
    [
        ("([PR](https://github.com/example-org/api/pull/57))", True),
        ("([[sources/2026-10/2026-10-13-sync]])", True),
        ("([[2026-10-13-sync]])", True),
        ("([[report-builder-decisions]])", False),
        ("(per the sync)", False),
    ],
)
def test_what_counts_as_a_citation(cite: str, ok: bool) -> None:
    fact = KbFact("x", (re.compile("200,000"),))
    after = {"k.md": f"# T\n\nThe cap is 200,000 rows {cite}.\n"}
    assert kb_facts_cited({}, after, [fact]).passed is ok


def test_rewriting_a_bullet_or_row_in_place_is_not_narration() -> None:
    before = "# P\n\n## Where it stands\n_As of 2026-10-13._\n- PR in review.\n\n## Key decisions\n| a | b |\n"
    after = "# P\n\n## Where it stands\n_As of 2026-10-14._\n- PR merged.\n- Pilot moved.\n\n## Key decisions\n"
    after += "| c | d |\n"
    assert front_page_no_narration(before, after, ()).passed is True


def test_a_new_heading_or_paragraph_on_the_front_page_is_reported() -> None:
    before = "# P\n\n## Where it stands\n- PR in review.\n"
    after = before + "\nThis run found the PR merged.\n\n### Update 2026-10-14\n- merged\n"
    result = front_page_no_narration(before, after, (Marker("this run", re.compile(r"(?i)this run")),))
    assert result.passed is False
    assert set(result.details) == {
        "narration marker: this run",
        "new heading: ### Update 2026-10-14",
        "section 'Where it stands' gained 1 paragraph",
    }


def test_growth_is_counted_in_bytes_not_characters() -> None:
    assert front_page_growth("a", "a🟢", 3).passed is False  # the emoji is 4 bytes
    assert front_page_growth("a", "a🟢", 4).passed is True
    assert front_page_growth("a", None, 4).passed is False
    assert front_page_growth(None, "a", 4).passed is None


# ----- the scenario's settings ----------------------------------------------------------------


def _probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: str) -> str:
    import tests.eval.harness.scenario as scenario_mod

    root = tmp_path / "scenarios"
    (root / "probe").mkdir(parents=True)
    base = "mode: morning-consolidation\npersonas: [slack]\nchecks: [kb_structure]\n"
    (root / "probe" / "scenario.yaml").write_text(base + extra + "\n", encoding="utf-8")
    monkeypatch.setattr(scenario_mod, "SCENARIOS_DIR", root)
    return "probe"


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("", "need kb_project"),
        ("kb_project: p\nmax_front_page_growth: -1", "max_front_page_growth"),
        ("kb_project: p\nmax_front_page_growth: lots", "max_front_page_growth"),
        ("kb_project: p\nexpected_kb_facts: [{id: x, all: ['(']}]", "bad pattern"),
        ("kb_project: p\nexpected_kb_facts: [{id: x}]", "an `all` list"),
    ],
)
def test_bad_kb_settings_fail_before_a_session_is_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_scenario(_probe(tmp_path, monkeypatch, extra))


# ----- the commands ---------------------------------------------------------------------------


def test_kb_score_prints_a_json_report_and_exits_by_verdict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.eval.harness.cli import main

    good = _snapshot(tmp_path / "g", "good")
    out = tmp_path / "report.json"
    assert main(["kb-score", str(good), "--report", str(out)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == json.loads(out.read_text(encoding="utf-8"))
    assert report["gate"] == "kb-writing" and report["verdict"] == "pass" and report["passed"] is True
    assert [c["name"] for c in report["checks"]] == list(KB_CHECKS)
    front = report["front_page"]
    assert front["path"] == f"{PROJECT}/report-builder.md"
    assert front["growth"] == front["bytes_after"] - front["bytes_before"] < 0  # good shrinks the page
    assert report["sample"] is None

    narrated = _snapshot(tmp_path / "n", "good", "narrated")
    assert main(["kb-score", str(narrated)]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "fail"
    assert main(["kb-score", str(narrated), "--max-growth", "-1"]) == 2
    assert main(["kb-score", str(tmp_path / "nowhere")]) == 2


FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os
servers = [s for s in os.environ["EVAL_FAKE_SERVERS"].split(",") if s]
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5", "apiKeySource": "none",
                  "mcp_servers": [{"name": s, "status": "connected"} for s in servers]}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                  "duration_ms": 1000, "total_cost_usd": 0.01, "result": "Nothing to do.",
                  "usage": {"input_tokens": 1, "output_tokens": 1}}))
"""


def test_kb_gate_runs_one_session_and_writes_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stand-in session that writes nothing: the front page is untouched, so the gate fails on
    the facts alone, and says so in the report it leaves in the result dir."""
    from tests.eval.harness.cli import main

    fake = tmp_path / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    servers = "claude_ai_Gmail,claude_ai_Google_Calendar,claude_ai_Linear,claude_ai_Slack"
    monkeypatch.setenv("EVAL_FAKE_SERVERS", servers)
    extra = tmp_path / "out" / "gate.json"
    code = main(["kb-gate", "--yes", "--claude", str(fake), "--cli-version", "2.1.278 (Claude Code)",
                 "--results", str(tmp_path / "results"), "--report", str(extra)])  # fmt: skip
    captured = capsys.readouterr()
    assert "Will start 1 session (kb-front-page ×1, morning-consolidation) on sonnet" in captured.err
    report = json.loads(captured.out)
    assert code == 1 and report["verdict"] == "fail"
    assert {c["name"]: c["passed"] for c in report["checks"]} == {
        "kb_structure": True,
        "front_page_growth": True,
        "kb_facts_cited": False,
        "front_page_no_narration": True,
    }
    [result_dir] = (tmp_path / "results").iterdir()
    assert result_dir.name.startswith("kb-front-page-kb-gate-")
    assert json.loads((result_dir / "kb-gate-report.json").read_text(encoding="utf-8")) == report
    assert json.loads(extra.read_text(encoding="utf-8")) == report
    assert report["sample"]["result_dir"] == str(result_dir) and report["sample"]["aborted"] is None
    assert report["sample"]["resolved_model"] == "claude-sonnet-5"
    shutil.rmtree(tmp_path / "results")
