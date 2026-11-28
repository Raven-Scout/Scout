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


# ----- fixes after the first build on a real vault ------------------------------------


def _add_legacy_prep(vault: Path) -> tuple[str, str]:
    """Two older flat prep notes: one with only prep links, one with a Decisions section."""
    _write(
        vault,
        "action-items/meeting-prep/2026-10-10-billing-review.md",
        "# Billing Review\nAttendees: Priya Shah\n\n## Context\n- [[billing]] status\n- [[widgets]] dependency\n",
    )
    _git(vault, "add", ".")
    _git(vault, "commit", "-q", "-m", "prep 1", date="2026-10-10T08:00:00+00:00")
    first_parent = _git(vault, "rev-parse", "--short=12", "HEAD~1")
    _write(
        vault,
        "action-items/meeting-prep/2026-10-17-billing-review.md",
        "# Billing Review\n**Attendees:** Priya Shah, Sam Lee\n\n## Context\n- [[billing]]\n\n"
        "## Decisions\n- Wait on [[gateway]]; ask [[sam-lee]].\n",
    )
    _git(vault, "add", ".")
    _git(vault, "commit", "-q", "-m", "prep 2", date="2026-10-17T08:00:00+00:00")
    second_parent = _git(vault, "rev-parse", "--short=12", "HEAD~1")
    return first_parent, second_parent


def test_prep_notes_are_found_in_every_layout() -> None:
    from tests.eval.harness.track_s import find_prep_notes

    found = dict(
        find_prep_notes(
            [
                "knowledge-base/meetings/widgets-sync/widgets-sync.md",
                "knowledge-base/meetings/widgets-sync/2026-10-08.md",
                "meetings/team-standup/2026-10-09.md",
                "meetings/meetings.md",
                "action-items/meeting-prep/2026-10-10-billing-review.md",
                "action-items/meeting-prep/archive/billing_review_2026-09-01.md",
                "action-items/action-items-2026-10-10.md",
            ]
        )
    )
    assert found == {
        "knowledge-base/meetings/widgets-sync/2026-10-08.md": "widgets-sync",
        "meetings/team-standup/2026-10-09.md": "team-standup",
        "action-items/meeting-prep/2026-10-10-billing-review.md": "billing-review",
        "action-items/meeting-prep/archive/billing_review_2026-09-01.md": "billing_review",
    }
    assert find_prep_notes(["notes/prep/2026-10-01-x.md"], ["notes/prep/*.md"]) == [("notes/prep/2026-10-01-x.md", "x")]


def test_meeting_prep_reads_older_action_items_prep_notes(tmp_path: Path) -> None:
    vault, _first, _head = _make_vault(tmp_path)
    first_parent, second_parent = _add_legacy_prep(vault)
    items = {it.id: it for it in build_meeting_prep_items(vault)}
    # The newer note has a Decisions section: hindsight gold, and the earlier session is trivial.
    newer = items["s-prep-billing-review-2026-10-17"]
    assert newer.source_kind == "hindsight"
    assert newer.as_of_sha == second_parent
    assert newer.gold == ("knowledge-base/projects/gateway/gateway.md", "knowledge-base/people/sam-lee.md")
    assert newer.exclude_paths == ("action-items/meeting-prep/2026-10-17-billing-review.md",)
    assert "Priya Shah, Sam Lee" in newer.question
    # The older note has prep only: its own links are the gold, marked as the weaker kind.
    older = items["s-prep-billing-review-2026-10-10"]
    assert older.source_kind == "prep_links" and older.as_of_sha == first_parent
    assert set(older.gold) == {
        "knowledge-base/projects/billing/billing.md",
        "knowledge-base/projects/widgets/widgets.md",
    }
    assert "s-prep-widgets-sync-2026-10-08" in items
    strict = {it.id for it in build_meeting_prep_items(vault, allow_prep_links=False)}
    assert "s-prep-billing-review-2026-10-10" not in strict and "s-prep-billing-review-2026-10-17" in strict
    only_legacy = build_meeting_prep_items(vault, prep_globs=["action-items/meeting-prep/**/*.md"])
    assert {it.project for it in only_legacy} == {"billing-review"}


def test_people_questions_leave_out_hub_people(tmp_path: Path) -> None:
    from tests.eval.harness.track_s import find_hubs

    people_of = {f"p{i}": {"Owner", f"Person {i}"} for i in range(10)}
    people_of["p0"].add("Person 1")
    hubs, cutoff = find_hubs(people_of, set(people_of), hub_share=0.25)
    assert hubs == {"Owner"} and cutoff == 4
    assert find_hubs(people_of, set(people_of), exclude_people=["person-3"])[0] == {"Owner", "Person 3"}

    vault, _first, _head = _make_vault(tmp_path)
    items = {it.id: it for it in build_computed_items(vault, exclude_people=["Sam Lee"])}
    # Without Sam, widgets shares people with billing only: too small an answer to ask.
    assert "s-shared-stakeholder-widgets" not in items
    both = items["s-touched-both-billing-widgets"]
    assert both.gold == ("Alex Chen", "Priya Shah") and "leaves out Sam Lee" in both.question
    # A question whose answer is too large to tell arms apart is dropped.
    assert not [it for it in build_computed_items(vault, max_gold=1) if it.family == "blast_radius"]


def test_masks_skip_hidden_notes_issue_keys_and_placeholders(tmp_path: Path) -> None:
    vault, _first, _head = _make_vault(tmp_path)
    _write(
        vault,
        "knowledge-base/projects/widgets/knowledge/widgets-notes.md",
        "# Widgets notes\nSee [[PROJ-1234]], [[<person>]], [[example-note]], [[missing-note]], "
        "[[secret-plan]] and [[gateway]].\n",
    )
    _write(
        vault,
        "knowledge-base/projects/widgets/knowledge/secret-plan.md",
        "---\nvisibility: private\n---\n# Secret plan\nSee [[billing]].\n",
    )
    _git(vault, "add", ".")
    _git(vault, "commit", "-q", "-m", "notes", date="2026-10-20T08:00:00+00:00")

    def targets(reader: str) -> set[str]:
        items = build_link_recovery_items(vault, reader=reader, fraction=1.0, max_edits=100, seed=1, min_gold=1)
        return {e.target for e in items[0].mask_edits if e.path.endswith("widgets-notes.md")} if items else set()

    assert targets("autonomous") == {"secret-plan", "gateway"}
    # A reader that can't see the private note gets no mask on it or in it.
    elsewhere = build_link_recovery_items(vault, reader="elsewhere", fraction=1.0, max_edits=100, seed=1, min_gold=1)
    edits = elsewhere[0].mask_edits
    assert not any(e.target == "secret-plan" or e.path.endswith("secret-plan.md") for e in edits)
    assert any(e.target == "gateway" and e.path.endswith("widgets-notes.md") for e in edits)


def test_scoring_forgives_format_and_unverifiable_links(tmp_path: Path) -> None:
    answer = (
        "Here is what I found.\n\n**Items:**\n"
        "1. **`projects/billing/billing.md`** (slipping)\n"
        "• [gateway](knowledge-base/projects/gateway/gateway.md)\n"
        "- widgets: the main project\n"
    )
    assert parse_listed(answer) == [
        "projects/billing/billing.md",
        "knowledge-base/projects/gateway/gateway.md",
        "widgets",
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
    )
    s = score_set_answer(item, answer)
    assert s["recall"] == 1.0 and s["precision"] == 1.0

    root = tmp_path / "wt"
    _write(root, "knowledge-base/people/priya-shah.md", "# Priya\nWorks on billing with Alex Chen and [[widgets]].\n")
    pair = SetItem("s-l", "link_recovery", "q", "abc", ("knowledge-base/people/priya-shah.md -> billing",), "pair")
    links = (
        "## Items\n"
        "- people/priya-shah.md → [[billing]] (named in the text)\n"
        "- knowledge-base/people/priya-shah.md => Alex Chen\n"
        "- knowledge-base/people/priya-shah.md -> widgets\n"
        "- knowledge-base/people/priya-shah.md -> Mars Base\n"
        "- knowledge-base/people/ghost.md -> billing\n"
    )
    s = score_set_answer(pair, links, vault_root=root)
    assert s["recall"] == 1.0
    # Alex Chen is named but unlinked, so it may be a real missing link: left out of precision.
    assert s["unverifiable"] == ["knowledge-base/people/priya-shah.md -> Alex Chen"]
    # Already linked, never mentioned, and a missing note are all wrong: 1 hit of 4 judged.
    assert s["precision"] == 0.25 and s["precision_raw"] == 0.2
    assert s["leaked_paths"] == []
