"""Unit tests for Track S: set-recall items (hindsight prep, masked links, computed sets), scoring and the runner."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.eval.harness.arms import KNOWN_ARMS
from tests.eval.harness.cli import main as eval_main
from tests.eval.harness.metrics import (
    ToolCall,
    graph_context_reads,
    graph_verb_breakdown,
    traversal_verb_calls,
)
from tests.eval.harness.track_s import (
    MaskEdit,
    SetItem,
    apply_mask,
    build_computed_items,
    build_link_recovery_items,
    build_meeting_prep_items,
    build_track_s_prompt,
    export_tree,
    load_set_items,
    norm_name,
    parse_listed,
    score_set_answer,
    select_mask,
    summarize_track_s,
    write_set_items,
)

pytestmark = pytest.mark.usefixtures("eval_runner_git_off_xcrun")

PROJECTS = {
    "gateway": ("Active", "", "# Gateway\nThe shared API gateway.\n"),
    "widgets": (
        "Active",
        '  - type: depends_on\n    target: "[[gateway]]"\n',
        "# Widgets\nDepends on [[gateway]]. Feeds [[billing]]. Owned by [[priya-shah|Priya]].\n",
    ),
    "billing": (
        "Active",
        '  - type: depends_on\n    target: "[[widgets]]"\n',
        "# Billing\nWaits on the widgets launch.\n",
    ),
    "legacy": ("Archived", '  - type: depends_on\n    target: "[[gateway]]"\n', "# Legacy\nRetired.\n"),
}
PEOPLE = {
    "priya-shah": ("Priya Shah", ["widgets", "billing"], "# Priya\nWorks with Alex Chen on billing.\n"),
    "alex-chen": ("Alex Chen", ["widgets", "billing"], "# Alex\nEngineer.\n"),
    "sam-lee": ("Sam Lee", ["gateway", "widgets"], "# Sam\nRuns the gateway.\n"),
}


def _git(vault: Path, *args: str, date: str | None = None) -> str:
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    return subprocess.run(
        ["git", "-C", str(vault), *args], capture_output=True, text=True, check=True, env=env
    ).stdout.strip()


def _write(vault: Path, rel: str, text: str) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _make_vault(tmp_path: Path) -> tuple[Path, str, str]:
    """An anonymized vault with two dated commits: the graph on Oct 1, a meeting session on Oct 8."""
    vault = tmp_path / "vault-repo"
    vault.mkdir()
    _write(vault, "scout-config.yaml", "schema_version: 1\n")
    _write(vault, "SKILL.md", "# Skill\n")
    for slug, (status, rels, body) in PROJECTS.items():
        rel_block = f"relationships:\n{rels}" if rels else ""
        _write(
            vault,
            f"knowledge-base/projects/{slug}/{slug}.md",
            f"---\nname: {slug}\ntype: project\nstatus: {status}\n{rel_block}---\n{body}",
        )
    for slug, (name, projects, body) in PEOPLE.items():
        rels = "".join(f'  - type: works_on\n    target: "[[{p}]]"\n' for p in projects)
        _write(
            vault,
            f"knowledge-base/people/{slug}.md",
            f"---\nname: {name}\ntype: person\nrelationships:\n{rels}---\n{body}",
        )
    _write(
        vault,
        "knowledge-base/meetings/widgets-sync/widgets-sync.md",
        "---\nname: Widgets Sync\ntype: meeting\n---\n# Widgets Sync\nWeekly sync on [[widgets]].\n",
    )
    _write(vault, "knowledge-base/session-log/2026-10-01.md", "# Log\nTouched [[widgets]] and [[gateway]].\n")
    _git(vault, "init", "-q")
    _git(vault, "config", "user.email", "eval@example.com")
    _git(vault, "config", "user.name", "Eval")
    _git(vault, "add", ".")
    _git(vault, "commit", "-q", "-m", "graph", date="2026-10-01T12:00:00+00:00")
    first = _git(vault, "rev-parse", "--short=12", "HEAD")
    _write(
        vault,
        "knowledge-base/meetings/widgets-sync/2026-10-08.md",
        "# Widgets Sync — 2026-10-08\n**Attendees:** Priya Shah, Alex Chen\n\n## Prep\n- agenda\n\n"
        "## Notes\n- talked\n\n## Synthesis\n| Decision | Also updated in |\n|---|---|\n"
        "| Slip billing a week | [[billing]], [[widgets\\|Widgets]] |\n\nGateway risk: [[gateway]]. "
        "Follow-up with [[alex-chen]].\n",
    )
    _git(vault, "add", ".")
    _git(vault, "commit", "-q", "-m", "meeting", date="2026-10-08T15:00:00+00:00")
    head = _git(vault, "rev-parse", "--short=12", "HEAD")
    return vault, first, head


# ----- builders ---------------------------------------------------------------------


def test_meeting_prep_items_use_the_vault_before_the_meeting(tmp_path: Path) -> None:
    vault, first, _head = _make_vault(tmp_path)
    items = build_meeting_prep_items(vault)
    assert [it.id for it in items] == ["s-prep-widgets-sync-2026-10-08"]
    it = items[0]
    assert it.as_of_sha == first
    assert it.exclude_paths == ("knowledge-base/meetings/widgets-sync/2026-10-08.md",)
    assert set(it.gold) == {
        "knowledge-base/projects/billing/billing.md",
        "knowledge-base/projects/widgets/widgets.md",
        "knowledge-base/projects/gateway/gateway.md",
        "knowledge-base/people/alex-chen.md",
    }
    # The home file already links widgets, so prep reads it anyway.
    assert it.trivial == ("knowledge-base/projects/widgets/widgets.md",)
    assert "Priya Shah, Alex Chen" in it.question and "Widgets Sync" in it.question
    assert not it.validate()


def test_masking_is_deterministic_and_replays_on_a_checkout(tmp_path: Path) -> None:
    vault, _first, head = _make_vault(tmp_path)
    files = {
        "knowledge-base/projects/widgets/widgets.md": (
            vault / "knowledge-base/projects/widgets/widgets.md"
        ).read_text(),
        "knowledge-base/people/priya-shah.md": (vault / "knowledge-base/people/priya-shah.md").read_text(),
        "knowledge-base/session-log/2026-10-01.md": (vault / "knowledge-base/session-log/2026-10-01.md").read_text(),
    }
    edits = select_mask(files, fraction=1.0, max_edits=50, seed=7)
    assert edits == select_mask(files, fraction=1.0, max_edits=50, seed=7)
    pairs = {e.pair() for e in edits}
    assert "knowledge-base/projects/widgets/widgets.md -> gateway" in pairs
    assert "knowledge-base/projects/widgets/widgets.md -> priya-shah" in pairs
    # Priya's note names billing in plain text, so its typed edge is recoverable and maskable.
    assert any(e.kind == "typed_edge" and e.path.endswith("priya-shah.md") and e.target == "billing" for e in edits)
    assert not any("session-log" in e.path for e in edits)

    root = export_tree(vault, head, tmp_path / "checkout")
    apply_mask(root, edits)
    widgets = (root / "knowledge-base/projects/widgets/widgets.md").read_text()
    # The body link is masked; the typed edge to the same target stays, as one masked link per pair.
    assert "Depends on gateway." in widgets and 'target: "[[gateway]]"' in widgets
    assert "Owned by Priya." in widgets
    priya = (root / "knowledge-base/people/priya-shah.md").read_text()
    assert "[[billing]]" not in priya and "[[widgets]]" in priya
    with pytest.raises(ValueError, match="no longer applies"):
        apply_mask(root, edits)


def test_link_recovery_items_group_by_scope(tmp_path: Path) -> None:
    vault, _first, head = _make_vault(tmp_path)
    items = build_link_recovery_items(vault, fraction=1.0, max_edits=50, seed=3, min_gold=1)
    assert items and all(it.family == "link_recovery" and it.as_of_sha == head for it in items)
    assert len({it.mask_id for it in items}) == 1
    by_id = {it.id: it for it in items}
    assert "s-links-projects-widgets" in by_id
    assert all(g.startswith("knowledge-base/projects/widgets/") for g in by_id["s-links-projects-widgets"].gold)
    # Every item replays the full mask, so the vault each arm sees is the same.
    assert all(it.mask_edits == items[0].mask_edits for it in items)


def test_computed_items_enumerate_gold_from_typed_edges(tmp_path: Path) -> None:
    vault, _first, _head = _make_vault(tmp_path)
    items = {it.id: it for it in build_computed_items(vault)}
    blast = items["s-blast-radius-gateway"]
    # The archived project is out of scope; billing is two hops away.
    assert blast.gold == ("widgets", "billing")
    assert "knowledge-base/projects/billing/billing.md" in blast.oracle_paths
    assert "billing.md" in blast.aliases["billing"]
    assert items["s-shared-stakeholder-widgets"].gold == ("billing", "gateway")
    assert items["s-touched-both-billing-widgets"].gold == ("Alex Chen", "Priya Shah")


# ----- storage, parsing and scoring -------------------------------------------------


def test_set_items_round_trip_and_refuse_the_public_repo(tmp_path: Path) -> None:
    from tests.eval.harness.build import PLUGIN_ROOT

    edit = MaskEdit("knowledge-base/a.md", "body_link", "b", "[[b]]", "b")
    item = SetItem(
        id="s-x",
        family="link_recovery",
        question="q",
        as_of_sha="abc",
        gold=(edit.pair(),),
        gold_kind="pair",
        mask_id="m",
        mask_edits=(edit,),
        aliases={"b": ("B",)},
    )
    dest = write_set_items(tmp_path / "private" / "s.jsonl", [item])
    assert (dest.stat().st_mode & 0o777) == 0o600
    assert load_set_items(dest) == [item]
    with pytest.raises(PermissionError):
        write_set_items(PLUGIN_ROOT / "leak.jsonl", [item])
    with pytest.raises(ValueError, match="trivial"):
        write_set_items(tmp_path / "bad.jsonl", [SetItem("s-y", "meeting_prep", "q", "abc", ("a",), "path", ("b",))])


def test_parse_and_score_set_answers() -> None:
    answer = (
        "Some notes first.\n- not an item\n\nItems:\n"
        "- `knowledge-base/projects/billing/billing.md` — slipping a week\n"
        "- knowledge-base/projects/gateway/gateway.md\n"
        "- knowledge-base/people/ghost.md\n"
    )
    assert parse_listed(answer) == [
        "knowledge-base/projects/billing/billing.md",
        "knowledge-base/projects/gateway/gateway.md",
        "knowledge-base/people/ghost.md",
    ]
    item = SetItem(
        id="s-p",
        family="meeting_prep",
        question="q",
        as_of_sha="abc",
        gold=(
            "knowledge-base/projects/billing/billing.md",
            "knowledge-base/projects/gateway/gateway.md",
            "knowledge-base/projects/widgets/widgets.md",
        ),
        gold_kind="path",
        trivial=("knowledge-base/projects/widgets/widgets.md",),
    )
    visible = {
        "knowledge-base/projects/billing/billing.md": "normal",
        "knowledge-base/projects/gateway/gateway.md": "normal",
    }
    s = score_set_answer(item, answer, visible=visible)
    assert s["recall"] == pytest.approx(2 / 3, abs=1e-3)
    assert s["nontrivial_recall"] == 1.0
    assert s["precision"] == pytest.approx(2 / 3, abs=1e-3)
    assert s["missed"] == ["knowledge-base/projects/widgets/widgets.md"]
    assert s["extra"] == ["knowledge-base/people/ghost.md"]
    assert s["leaked_paths"] == ["knowledge-base/people/ghost.md"]

    ent = SetItem(
        "s-e", "touched_both", "q", "abc", ("Priya Shah",), "entity", aliases={"Priya Shah": ("priya-shah.md",)}
    )
    assert score_set_answer(ent, "Items:\n- [[priya-shah]]\n")["recall"] == 1.0
    pair = SetItem("s-l", "link_recovery", "q", "abc", ("knowledge-base/a.md -> Sam Lee",), "pair")
    assert score_set_answer(pair, "Items:\n- knowledge-base/a.md -> [[sam-lee]]\n")["recall"] == 1.0
    assert norm_name("knowledge-base/people/priya_shah.md") == "priya shah"


def test_prompt_carries_the_arm_overlay_and_oracle_hint() -> None:
    item = SetItem(
        "s-p",
        "meeting_prep",
        "Prep it.",
        "abc",
        ("knowledge-base/x.md",),
        "path",
        oracle_paths=("knowledge-base/x.md",),
    )
    p_bf = build_track_s_prompt(item, KNOWN_ARMS["bf"])
    assert "Before any other knowledge-base read" in p_bf and "Items:" in p_bf
    p_o = build_track_s_prompt(item, KNOWN_ARMS["o"])
    assert "- `knowledge-base/x.md`" in p_o
    assert "knowledge-base/x.md" not in build_track_s_prompt(item, KNOWN_ARMS["a"])


def test_graph_verb_and_context_metrics() -> None:
    calls = [
        ToolCall(1, "Bash", {"command": 'scripts/scoutctl kb neighbors "Priya Shah" --depth 2'}, False, 0.1),
        ToolCall(2, "Bash", {"command": 'scripts/scoutctl kb search "gateway" --ranked'}, False, 0.2),
        ToolCall(3, "Bash", {"command": "scripts/scoutctl kb backlinks knowledge-base/a.md"}, False, 0.3),
        ToolCall(4, "Read", {"file_path": "/wt/.scout-cache/session-context.json"}, False, 0.4),
        ToolCall(5, "Bash", {"command": "scripts/scoutctl kb link-gaps"}, True, 0.5, gate="kb-read-gate"),
    ]
    assert {k: v for k, v in graph_verb_breakdown(calls).items() if v} == {
        "neighbors": 1,
        "search-ranked": 1,
        "backlinks": 1,
    }
    assert traversal_verb_calls(calls) == 2
    assert graph_context_reads(calls) == 1


def test_summary_applies_the_decision_rule() -> None:
    def row(arm: str, i: int, recall: float, tokens: int) -> dict[str, object]:
        return {
            "id": f"s-{i}",
            "family": "meeting_prep",
            "project": f"p{i}",
            "rep": 1,
            "arm": arm,
            "aborted": None,
            "recall": recall,
            "nontrivial_recall": recall,
            "precision": 0.5,
            "total_tokens": tokens,
            "cost_usd": 0.1,
            "graph_verb_breakdown": {"neighbors": 1} if arm == "bf" else {},
            "traversal_verb_calls": 1 if arm == "bf" else 0,
            "graph_context_reads": 0,
            "leaked_paths": [],
        }

    arms = [KNOWN_ARMS["a"], KNOWN_ARMS["bf"]]
    rows = {
        "a": [row("a", i, 0.4, 1000) for i in range(6)],
        "bf": [row("bf", i, 0.7, 1050) for i in range(6)],
    }
    report = summarize_track_s(rows, arms)
    (cmp,) = report["comparisons"]
    assert cmp["ref"] == "a" and cmp["cand"] == "bf"
    assert cmp["recall_diff"]["mean_diff"] == pytest.approx(0.3)
    assert cmp["wins"] is True
    assert report["arms"]["bf"]["traversal_uptake"] == 1.0
    rows["bf"] = [row("bf", i, 0.7, 2000) for i in range(6)]
    assert summarize_track_s(rows, arms)["comparisons"][0]["wins"] is False


# ----- runner and CLI end to end ----------------------------------------------------

FAKE_TRACK_S_CLAUDE = """#!/usr/bin/env python3
import json, os, re, sys

prompt = sys.stdin.read()
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5",
                  "apiKeySource": "none", "mcp_servers": []}), flush=True)
task = re.search(r"Task \\(([^)]+)\\)", prompt).group(1)
if task.startswith("s-prep"):
    assert not os.path.exists("knowledge-base/meetings/widgets-sync/2026-10-08.md"), "meeting note leaked"
    listed = ["knowledge-base/projects/billing/billing.md", "knowledge-base/projects/gateway/gateway.md"]
elif task.startswith("s-links"):
    text = open("knowledge-base/projects/widgets/widgets.md", encoding="utf-8").read()
    assert "Depends on [[gateway]]" not in text, "mask was not applied"
    listed = [
        "knowledge-base/projects/widgets/widgets.md -> gateway",
        "knowledge-base/people/priya-shah.md -> billing",
        "knowledge-base/people/sam-lee.md -> gateway",
    ]
else:
    listed = ["widgets", "billing"]
ans = "Done.\\n\\nItems:\\n" + "".join(f"- {x}\\n" for x in listed)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                  "duration_ms": 100, "total_cost_usd": 0.01, "result": ans,
                  "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 0}}), flush=True)
"""


def test_track_s_cli_builds_and_runs_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, _first, _head = _make_vault(tmp_path)
    items_file = tmp_path / "private-eval" / "track-s.jsonl"
    rc = eval_main(
        [
            "track-s-build",
            "--vault",
            str(vault),
            "--out",
            str(items_file),
            "--family",
            "meeting-prep",
            "--family",
            "mask",
            "--fraction",
            "1.0",
        ]
    )
    assert rc == 0
    built = json.loads(capsys.readouterr().out)
    assert built["by_family"]["meeting_prep"] == 1
    assert built["by_family"]["link_recovery"] >= 1

    fake = tmp_path / "fake-track-s-claude"
    fake.write_text(FAKE_TRACK_S_CLAUDE, encoding="utf-8")
    fake.chmod(0o755)
    rc = eval_main(
        [
            "track-s-run",
            "--items",
            str(items_file),
            "--vault",
            str(vault),
            "--arms",
            "a,o",
            "--family",
            "meeting_prep",
            "--family",
            "link_recovery",
            "--limit",
            "2",
            "--results",
            str(tmp_path / "private-eval" / "runs"),
            "--yes",
            "--claude",
            str(fake),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "Track S summary" in out
    (report_path,) = (tmp_path / "private-eval" / "runs").glob("track-s-*/track-s-report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert (report_path.stat().st_mode & 0o777) == 0o600
    prep = next(r for r in report["items_by_arm"]["a"] if r["family"] == "meeting_prep")
    assert prep["recall"] == 0.5 and prep["nontrivial_recall"] == pytest.approx(2 / 3, abs=1e-3)
    links = next(r for r in report["items_by_arm"]["a"] if r["family"] == "link_recovery")
    assert links["recall"] == 1.0 and links["aborted"] is None
    assert not list((Path(report["run_dir"]) / "_worktrees").glob("wt-*"))
