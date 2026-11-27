"""Unit tests for Phase 2 three-arm eval axis, verb-uptake metrics, Track R graders, and Gate G1."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
import yaml

from scout.kb.raw_read import raw_kb_read
from tests.eval.harness.arms import ARM_B_BRAIN_OVERLAY, KNOWN_ARMS, apply_arm, parse_arms
from tests.eval.harness.checks.track_r import (
    abstention_check,
    calibrate_judge,
    calls_to_first_gold_hit,
    citation_faithfulness,
    dolphin_removal_check,
    evaluate_gate_g1,
    exact_answer_match,
    mcnemar_exact_p,
    paired_cluster_bootstrap,
    precision_at_k,
    recall_at_k,
    snapshot_fallback_check,
    strip_backend_revealing_text,
)
from tests.eval.harness.claude_shim import guard_init
from tests.eval.harness.cli import main as eval_main
from tests.eval.harness.mcp_config import write_mcp_config
from tests.eval.harness.metrics import ToolCall, graph_verb_calls, is_graph_verb_call, verb_uptake_rate
from tests.eval.harness.scenario import load_scenario
from tests.eval.harness.track_r_golden import (
    GoldenItem,
    load_golden_set,
    refuse_public_repo_destination,
    summarize_golden_set,
    write_golden_set,
)
from tests.unit.test_eval_runner import FAKE_CLAUDE
from tests.unit.test_phase_privacy_rule import _READ_KB_PATH, _WALK_KB, _code_spans

pytestmark = pytest.mark.usefixtures("eval_runner_git_off_xcrun")
MONDAY = date(2026, 10, 12)
SERVERS = "claude_ai_Google_Calendar,claude_ai_Linear,claude_ai_Slack"


# ----- Arms & Phase Privacy / MCP Guard -------------------------------------------


def test_parse_arms_and_validation() -> None:
    specs = parse_arms("a, b, c")
    assert [s.id for s in specs] == ["a", "b", "c"]
    specs_named = parse_arms("files,relate-verbs,relate-precompute")
    assert specs_named == specs

    with pytest.raises(ValueError, match="at least two"):
        parse_arms("a")
    with pytest.raises(ValueError, match="unknown arm"):
        parse_arms("a, sqlite")
    with pytest.raises(ValueError, match="duplicate arm"):
        parse_arms("a, files")


def test_arm_b_brain_overlay_obeys_privacy_phase_rule() -> None:
    spans = _code_spans(ARM_B_BRAIN_OVERLAY)
    assert [s for s in spans if raw_kb_read(s)] == []
    prose_violations = [
        line
        for line in ARM_B_BRAIN_OVERLAY.splitlines()
        if (_WALK_KB.search(line) or _READ_KB_PATH.search(line)) and "scoutctl kb" not in line
    ]
    assert prose_violations == []


def test_apply_arm_writes_config_and_overlays_without_mcp_leak(tmp_path: Path) -> None:
    for arm_key in ("a", "b", "c"):
        v = tmp_path / f"vault-{arm_key}"
        v.mkdir()
        (v / "scout-config.yaml").write_text("schema_version: 1\n", encoding="utf-8")
        (v / "SKILL.md").write_text("# Base Skill\n", encoding="utf-8")
        spec = KNOWN_ARMS[arm_key]
        apply_arm(v, spec)

        cfg = yaml.safe_load((v / "scout-config.yaml").read_text(encoding="utf-8"))
        assert cfg["kb"]["backend"]["kind"] == spec.backend_kind
        assert cfg["kb"]["precompute_graph_context"] is spec.precompute_graph_context
        skill_text = (v / "SKILL.md").read_text(encoding="utf-8")
        assert ("kb neighbors" in skill_text) is (arm_key == "b")

        # Embedded library adds zero MCP servers -> never trips guard_init mcp_leak
        s = load_scenario("busy-weekday")
        allowed = write_mcp_config(
            tmp_path / f"mcp-{arm_key}.json",
            personas=s.personas,
            script=s.script,
            calls=tmp_path / f"calls-{arm_key}.jsonl",
            today=MONDAY,
            python="python3",
        )
        init_event = {
            "type": "system",
            "subtype": "init",
            "apiKeySource": "none",
            "mcp_servers": [{"name": srv, "status": "connected"} for srv in sorted(allowed)],
        }
        assert guard_init(init_event, allowed) is None

        # Whereas mounting an unstubbed `relate` MCP server is immediately caught as mcp_leak
        leaked_event = {
            **init_event,
            "mcp_servers": [*init_event["mcp_servers"], {"name": "relate", "status": "connected"}],
        }
        assert guard_init(leaked_event, allowed) == {"reason": "mcp_leak", "servers": ["relate"]}


def test_graph_verb_uptake_metrics() -> None:
    c_neigh = ToolCall(1, "Bash", {"command": 'scripts/scoutctl kb neighbors "widgets" --depth 2'}, False, 0.1)
    c_rank = ToolCall(2, "Bash", {"command": 'scripts/scoutctl kb search "canary" --ranked'}, False, 0.5)
    c_denied = ToolCall(3, "Bash", {"command": "scripts/scoutctl kb backlinks a.md"}, True, 0.7, gate="kb-read-gate")
    c_plain = ToolCall(4, "Bash", {"command": "scripts/scoutctl kb read a.md"}, False, 0.9)
    c_read = ToolCall(5, "Read", {"file_path": "/v/SKILL.md"}, False, 1.0)

    assert is_graph_verb_call(c_neigh) is True
    assert is_graph_verb_call(c_rank) is True
    assert is_graph_verb_call(c_denied) is False
    assert is_graph_verb_call(c_plain) is False
    assert is_graph_verb_call(c_read) is False
    assert graph_verb_calls([c_neigh, c_rank, c_denied, c_plain, c_read]) == 2
    assert verb_uptake_rate([[c_plain], [c_neigh], [c_rank, c_plain]]) == pytest.approx(2 / 3)
    assert verb_uptake_rate([]) == 0.0


def test_compare_arms_cli_runs_three_arms_interleaved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = tmp_path / "fake-claude"
    fake.write_text(FAKE_CLAUDE, encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("EVAL_FAKE_SERVERS", SERVERS)
    monkeypatch.setenv("EVAL_FAKE_ENRICH", "1")

    rc = eval_main(
        [
            "compare",
            "busy-weekday",
            "--arms",
            "a,b,c",
            "--reps",
            "1",
            "--yes",
            "--today",
            "2026-10-12",
            "--claude",
            str(fake),
            "--cli-version",
            "9.9.9",
            "--results",
            str(tmp_path / "results"),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Will start 3 sessions" in out
    assert "graph verb uptake (a=0%, b=0%, c=0%)" in out
    assert "arm b (relate-verbs) vs arm a (files)" in out
    assert "arm c (relate-precompute) vs arm a (files)" in out
    assert "arm c (relate-precompute) vs arm b (relate-verbs)" in out

    # Bad --arms exits 2
    assert (
        eval_main(["compare", "busy-weekday", "--arms", "a", "--yes", "--claude", str(fake), "--cli-version", "9.9.9"])
        == 2
    )


# ----- Pure Track R Graders, Judge Calibration & Gate G1 -------------------------


def test_track_r_retrieval_and_answer_graders() -> None:
    gold = ["knowledge-base/projects/widgets/widgets.md", "knowledge-base/people/priya.md"]
    retrieved = [
        "knowledge-base/people/alex.md",
        "knowledge-base/projects/widgets/widgets.md",
        "knowledge-base/projects/widgets/widgets.md",  # duplicate ignored in top-k
        "knowledge-base/people/priya.md",
    ]
    assert recall_at_k(retrieved, gold, k=2) == 0.5
    assert recall_at_k(retrieved, gold, k=3) == 1.0
    assert recall_at_k([], [], k=5) == 1.0
    assert precision_at_k(retrieved, gold, k=3) == pytest.approx(2 / 3)
    assert precision_at_k([], gold, k=3) == 0.0
    assert calls_to_first_gold_hit([["a.md"], ["knowledge-base/people/priya.md"]], gold) == 2
    assert calls_to_first_gold_hit([["a.md"]], gold) is None
    assert calls_to_first_gold_hit([["a.md"]], []) is None

    assert exact_answer_match("Status is Active owned by Priya", ["active", "priya"], ["archived"]).passed is True
    bad_exact = exact_answer_match("Status is Archived", ["active"], ["archived"])
    assert bad_exact.passed is False and len(bad_exact.details) == 2

    notes = {"knowledge-base/projects/widgets/widgets.md": "Release slipped to May 12 due to gateway migration."}
    assert citation_faithfulness(["knowledge-base/projects/widgets/widgets.md"], notes, ["May 12"]).passed is True
    assert citation_faithfulness([], notes, ["May 12"]).passed is False
    assert citation_faithfulness(["missing.md"], notes, ["May 12"]).passed is False
    assert citation_faithfulness(["knowledge-base/projects/widgets/widgets.md"], notes, ["June 30"]).passed is False

    assert abstention_check("This is unverified; route to review-queue.", should_abstain=True).passed is True
    assert abstention_check("Definitely launched yesterday.", should_abstain=True).passed is False
    assert abstention_check("Launched on April 10.", should_abstain=False).passed is True
    assert abstention_check("Cannot confirm from the KB.", should_abstain=False).passed is False

    assert (
        snapshot_fallback_check(
            {"backend": "files", "degraded_from": "relate", "fallback_reason": "ConfigError"}, expect_degraded=True
        ).passed
        is True
    )
    assert snapshot_fallback_check({"backend": "relate"}, expect_degraded=True).passed is False
    assert snapshot_fallback_check({"backend": "relate"}, expect_degraded=False).passed is True

    assert dolphin_removal_check(passed_with_gold=True, passed_without_gold=False).passed is True
    assert dolphin_removal_check(passed_with_gold=True, passed_without_gold=True).passed is False
    assert dolphin_removal_check(passed_with_gold=False, passed_without_gold=False).passed is False


def test_backend_blinding_and_judge_calibration() -> None:
    raw = (
        "Ran `scripts/scoutctl kb neighbors widgets --depth 2` on RelateBackend (.scout-cache/relate/full-123.rl) "
        "with MATCH (n:Note) and read knowledge-base/projects/widgets/widgets.md."
    )
    scrubbed = strip_backend_revealing_text(raw)
    for leaked in ("scoutctl", "RelateBackend", ".rl", "MATCH", "knowledge-base/projects/widgets/widgets.md"):
        assert leaked not in scrubbed

    # 19/20 rejected = 95% >= 90% -> passes
    good = calibrate_judge([False] * 19 + [True])
    assert good["passed"] is True and good["rejection_rate"] == 0.95
    # 16/20 rejected = 80% < 90% -> fails
    weak = calibrate_judge([False] * 16 + [True] * 4)
    assert weak["passed"] is False
    assert calibrate_judge([])["passed"] is False


def test_mcnemar_cluster_bootstrap_and_gate_g1() -> None:
    mc = mcnemar_exact_p([False] * 12 + [True] * 10, [True] * 12 + [True] * 10)
    assert mc["cand_only"] == 12 and mc["ref_only"] == 0 and mc["p_value"] < 0.01
    assert mcnemar_exact_p([True, False], [True, False])["p_value"] == 1.0

    records = []
    # 20 multi_hop items across 10 project clusters where cand beats ref strongly
    for i in range(20):
        records.append(
            {"id": f"mh-{i}", "project": f"p-{i % 10}", "category": "multi_hop", "ref_pass": 0, "cand_pass": 1}
        )
    for cat in ("status", "temporal", "citation", "abstention"):
        for i in range(10):
            records.append({"id": f"{cat}-{i}", "project": f"p-{i}", "category": cat, "ref_pass": 1, "cand_pass": 1})

    g1_pass = evaluate_gate_g1(
        records,
        baseline_tokens_median=10_000,
        candidate_tokens_median=10_500,
        baseline_calls_median=8,
        candidate_calls_median=8,
        n_boot=400,
    )
    assert g1_pass["passed"] is True, g1_pass["reasons"]
    assert g1_pass["strata"]["multi_hop"]["ci_low"] > 0.0

    # Fail when multi_hop has zero improvement and cost exceeds 1.10x
    flat = [{**r, "cand_pass": r["ref_pass"]} for r in records]
    g1_fail = evaluate_gate_g1(
        flat,
        baseline_tokens_median=10_000,
        candidate_tokens_median=13_000,
        baseline_calls_median=8,
        candidate_calls_median=10,
        n_boot=200,
    )
    assert g1_fail["passed"] is False
    assert any("multi_hop" in r for r in g1_fail["reasons"])
    assert any("median tokens" in r for r in g1_fail["reasons"])
    assert paired_cluster_bootstrap([])["mean_diff"] == 0.0


# ----- Local-Only Golden Set Guard & Vault Miner ---------------------------------


def test_golden_set_public_repo_guard_and_seeding(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.eval.harness.build import PLUGIN_ROOT

    # Refuses writing inside the plugin repo checkout
    with pytest.raises(PermissionError, match="plugin repository"):
        refuse_public_repo_destination(PLUGIN_ROOT / "leaked-golden.jsonl")

    # Refuses writing inside any git repo with Raven-Scout/Scout remote
    clone = tmp_path / "public-clone"
    clone.mkdir()
    subprocess.run(["git", "-C", str(clone), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(clone), "remote", "add", "origin", "https://github.com/Raven-Scout/Scout.git"], check=True
    )
    with pytest.raises(PermissionError, match="public repo remote"):
        write_golden_set(clone / "golden.jsonl", [])

    # Build anonymized fixture vault with all 4 candidate sources
    v = tmp_path / "fixture-vault"
    (v / "knowledge-base/projects/widgets").mkdir(parents=True)
    (v / "knowledge-base/people").mkdir(parents=True)
    (v / "knowledge-base/sources/2026-04").mkdir(parents=True)
    (v / "action-items").mkdir(parents=True)
    (v / "scout-config.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (v / "knowledge-base/projects/widgets/widgets.md").write_text(
        "---\nname: widgets\ntype: project\nstatus: Active\nrelationships:\n"
        '  - type: owned_by\n    target: "[[Priya Shah]]"\n---\n# Widgets\n',
        encoding="utf-8",
    )
    (v / "knowledge-base/people/priya-shah.md").write_text(
        "---\nname: Priya Shah\ntype: person\nrole: Director\naliases: [PS]\nrelationships:\n"
        '  - type: manages\n    target: "[[Alex Chen]]"\n---\n# Priya\n',
        encoding="utf-8",
    )
    (v / "knowledge-base/people/alex-chen.md").write_text(
        "---\nname: Alex Chen\ntype: person\nrole: Engineer\n---\n# Alex\n",
        encoding="utf-8",
    )
    (v / "knowledge-base/sources/2026-04/2026-04-10-kickoff.md").write_text(
        "---\ndate: 2026-04-10\n---\n# Kickoff\nLinked to [[widgets]].\n",
        encoding="utf-8",
    )
    (v / "knowledge-base/review-queue.md").write_text("- [x] Clarify Q3 quota ownership\n", encoding="utf-8")
    (v / "knowledge-base/scout-mistake-audit.md").write_text(
        "## Pattern 1: Unverified launch date\nDetails.\n", encoding="utf-8"
    )
    (v / "action-items/action-items-2026-04-14.md").write_text(
        "- [x] [#WIDG] Ship widgets beta checklist\n", encoding="utf-8"
    )

    out_file = tmp_path / "local-eval" / "candidates.jsonl"
    rc = eval_main(["track-r-seed", "--vault", str(v), "--out", str(out_file), "--max-items", "50"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"] >= 6
    loaded = load_golden_set(out_file)
    assert len(loaded) == report["total"]
    summary = summarize_golden_set(loaded)
    for cat in ("status", "multi_hop", "citation", "disambiguation", "single_hop", "abstention", "temporal"):
        assert summary["by_category"][cat] >= 1, summary
    # Mistake-audit items carry exclude_paths so audit file never leaks into eval KB
    audit_items = [it for it in loaded if it.source_kind == "mistake_audit"]
    assert audit_items and audit_items[0].exclude_paths == ("knowledge-base/scout-mistake-audit.md",)
    assert (out_file.stat().st_mode & 0o777) == 0o600
    assert (out_file.parent.stat().st_mode & 0o777) == 0o700

    # Validation catches mistake-audit in gold_paths
    bad = GoldenItem(
        id="bad-1",
        category="status",
        question="q",
        gold_answer="a",
        gold_paths=("knowledge-base/scout-mistake-audit.md",),
        as_of_sha="abc",
    )
    assert any("exclude_paths" in e for e in bad.validate())


# ----- Track R End-to-End Runner & CLI -------------------------------------------

FAKE_TRACK_R_CLAUDE = """#!/usr/bin/env python3
import json, os, subprocess, sys

prompt = sys.stdin.read()
mcp_cfg = json.loads(open(os.environ["SCOUT_EVAL_MCP_CONFIG"], encoding="utf-8").read())
assert mcp_cfg == {"mcpServers": {}}, mcp_cfg

print(json.dumps({
    "type": "system",
    "subtype": "init",
    "model": "claude-sonnet-5",
    "apiKeySource": "none",
    "mcp_servers": [],
}), flush=True)

calls = []
if "Graph-Backed KB Lookup" in prompt:
    proc = subprocess.run(
        ["scripts/scoutctl", "kb", "neighbors", "widgets", "--depth", "2"],
        capture_output=True, text=True, check=False,
    )
    calls.append((
        "t-graph",
        "Bash",
        {"command": 'scripts/scoutctl kb neighbors "widgets" --depth 2'},
        proc.stdout or "knowledge-base/projects/widgets/widgets.md\\nknowledge-base/people/alex-chen.md",
    ))
else:
    w_path = os.path.join(os.getcwd(), "knowledge-base/projects/widgets/widgets.md")
    txt = open(w_path, encoding="utf-8").read() if os.path.isfile(w_path) else "missing"
    calls.append(("t-read", "Read", {"file_path": w_path}, txt))

for tid, name, inp, out_txt in calls:
    print(json.dumps({"type": "assistant", "message": {"id": f"m-{tid}", "content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp},
    ]}}), flush=True)
    print(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": out_txt},
    ]}}), flush=True)

has_widgets = os.path.isfile("knowledge-base/projects/widgets/widgets.md")
has_audit = os.path.isfile("knowledge-base/scout-mistake-audit.md")

if "tr-abs" in prompt:
    assert not has_audit, "mistake-audit file should have been removed by exclude_paths"
    ans = "Answer: Unverified — cannot confirm from the KB; route to review-queue.\\nCitations: none"
elif not has_widgets:
    # DolphinBench gold-removal pass: gold notes were deleted, so abstain
    ans = "Answer: Cannot confirm from the KB.\\nCitations: none"
elif "tr-cite" in prompt:
    ans = (
        "Answer: Release moved to May 12.\\n"
        "Citations: knowledge-base/projects/widgets/knowledge/widgets-rollout.md"
    )
else:
    ans = (
        "Answer: Alex Chen is connected two hops from widgets.\\n"
        "Citations: knowledge-base/projects/widgets/widgets.md, knowledge-base/people/alex-chen.md"
    )

print(json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 2,
    "duration_ms": 250,
    "total_cost_usd": 0.01,
    "result": ans,
    "usage": {"input_tokens": 120, "output_tokens": 40, "cache_read_input_tokens": 0},
}), flush=True)
"""


def test_track_r_run_cli_end_to_end_and_dolphin_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.eval.harness.build import PLUGIN_ROOT
    from tests.eval.harness.track_r_runner import parse_track_r_arms

    assert [a.id for a in parse_track_r_arms("a")] == ["a"]
    with pytest.raises(ValueError, match="at least one"):
        parse_track_r_arms(" , ")

    # Build a committed anonymized git vault fixture
    vault = tmp_path / "vault-repo"
    (vault / "knowledge-base/projects/widgets/knowledge").mkdir(parents=True)
    (vault / "knowledge-base/people").mkdir(parents=True)
    (vault / "scout-config.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (vault / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    (vault / "knowledge-base/projects/widgets/widgets.md").write_text(
        "---\nname: widgets\ntype: project\nstatus: Active\nrelationships:\n"
        '  - type: owned_by\n    target: "[[Priya Shah]]"\n---\n# Widgets\nSee [[widgets-rollout]].\n',
        encoding="utf-8",
    )
    (vault / "knowledge-base/projects/widgets/knowledge/widgets-rollout.md").write_text(
        "---\nname: Widgets Rollout\ntype: topic\n---\n# Widgets Rollout\nRelease moved to May 12.\n",
        encoding="utf-8",
    )
    (vault / "knowledge-base/people/priya-shah.md").write_text(
        "---\nname: Priya Shah\ntype: person\nrelationships:\n"
        '  - type: manages\n    target: "[[Alex Chen]]"\n---\n# Priya\n',
        encoding="utf-8",
    )
    (vault / "knowledge-base/people/alex-chen.md").write_text(
        "---\nname: Alex Chen\ntype: person\nrole: Engineer\n---\n# Alex\n",
        encoding="utf-8",
    )
    (vault / "knowledge-base/scout-mistake-audit.md").write_text(
        "## Pattern 1: Unconfirmed date\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(vault), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "eval@example.com"], check=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "Eval"], check=True)
    subprocess.run(["git", "-C", str(vault), "add", "."], check=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-q", "-m", "fixture"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "--short=12", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    golden_file = tmp_path / "private-eval" / "pilot.jsonl"
    items = [
        GoldenItem(
            id="tr-mh-1",
            category="multi_hop",
            question="Starting from widgets, follow owned by then manages.",
            gold_answer="Alex Chen",
            gold_paths=(
                "knowledge-base/projects/widgets/widgets.md",
                "knowledge-base/people/alex-chen.md",
            ),
            as_of_sha=sha,
            project="widgets",
            expected_tokens=("Alex Chen",),
            audited=True,
        ),
        GoldenItem(
            id="tr-cite-1",
            category="citation",
            question="When did the widgets rollout move, and cite the note?",
            gold_answer="May 12",
            gold_paths=("knowledge-base/projects/widgets/knowledge/widgets-rollout.md",),
            as_of_sha=sha,
            project="widgets",
            expected_tokens=("May 12",),
            audited=True,
        ),
        GoldenItem(
            id="tr-abs-1",
            category="abstention",
            question="Is the unconfirmed gateway rumor verified?",
            gold_answer="",
            gold_paths=(),
            as_of_sha=sha,
            project="widgets",
            source_kind="mistake_audit",
            exclude_paths=("knowledge-base/scout-mistake-audit.md",),
            audited=True,
        ),
    ]
    write_golden_set(golden_file, items)

    fake_claude = tmp_path / "fake-track-r-claude"
    fake_claude.write_text(FAKE_TRACK_R_CLAUDE, encoding="utf-8")
    fake_claude.chmod(0o755)

    # Public repo destination is refused before any session starts
    rc_bad = eval_main(
        [
            "track-r-run",
            "--golden",
            str(golden_file),
            "--vault",
            str(vault),
            "--arms",
            "a",
            "--results",
            str(PLUGIN_ROOT / ".leaked-results"),
            "--yes",
            "--claude",
            str(fake_claude),
            "--cli-version",
            "9.9.9",
        ]
    )
    assert rc_bad == 2
    assert "Refusing to write Track R" in capsys.readouterr().err

    results_dir = tmp_path / "private-eval" / "runs"
    report_copy = tmp_path / "private-eval" / "latest-report.json"
    rc = eval_main(
        [
            "track-r-run",
            "--golden",
            str(golden_file),
            "--vault",
            str(vault),
            "--arms",
            "a",
            "--only-audited",
            "--dolphin-check",
            "--results",
            str(results_dir),
            "--report",
            str(report_copy),
            "--yes",
            "--claude",
            str(fake_claude),
            "--cli-version",
            "9.9.9",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Track R report (3 items, model=sonnet)" in out
    assert "acc=100.0% (3/3)" in out

    saved = json.loads(report_copy.read_text(encoding="utf-8"))
    assert saved["arms"]["a"]["accuracy"] == 1.0
    assert saved["dolphin"]["checked"] == 2
    assert saved["dolphin"]["valid"] == 2
    assert (report_copy.stat().st_mode & 0o777) == 0o600
    # Detached worktrees were cleaned up
    run_path = Path(saved["run_dir"])
    assert not list((run_path / "_worktrees").glob("wt-*"))
