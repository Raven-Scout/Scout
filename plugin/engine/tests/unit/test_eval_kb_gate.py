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

from scout import layout
from scout.kb.structure import Marker
from tests._gate_probe import HOOKED_CLAUDE_PRELUDE
from tests.eval.harness.build import EVAL_ROOT, build_vault
from tests.eval.harness.checks.kb_writing import (
    KB_CHECKS,
    KbFact,
    blocks,
    front_page_growth,
    front_page_no_narration,
    kb_facts_cited,
    source_keys,
)
from tests.eval.harness.dates import render_dates
from tests.eval.harness.kb_gate import (
    DENIED_INPUT_CHARS,
    EXIT_VOID,
    commits_since_fixture,
    exit_code,
    gate_report,
    pages_changed,
    score_kb,
    spec_for,
    structure_row,
    void_run,
)
from tests.eval.harness.metrics import SampleMetrics, ToolCall
from tests.eval.harness.scenario import load_scenario

TODAY = date(2026, 10, 14)
SCENARIO = EVAL_ROOT / "scenarios" / "kb-front-page"
GOLDEN = EVAL_ROOT / "golden" / "kb-front-page"
PROJECT = f"{layout.space_dir('work', 'projects')}/report-builder"


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


def _mark_v2(vault: Path) -> None:
    """A layout v2 vault (ADR 0011) is one with ``scout/brain/``; the scenario's project folder is a v2 path."""
    brain = vault / layout.V2["brain_dir"]
    brain.mkdir(parents=True, exist_ok=True)
    (brain / ".keep").write_text("", encoding="utf-8")


def _snapshot(tmp_path: Path, *cases: str) -> Path:
    """A small vault: the scenario's project folder as the fixture commit, then each golden case
    laid over it, committed the way a run commits."""
    vault = tmp_path / "vault"
    _lay(SCENARIO / "vault" / PROJECT, vault / PROJECT)
    _mark_v2(vault)
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
    assert _verdicts(vault, scenario) == {**dict.fromkeys(KB_CHECKS, True), "kb_facts_cited": False}


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


def test_a_front_page_that_keeps_every_detail_fails_on_growth_and_bullet_count(tmp_path: Path, scenario) -> None:
    vault = _snapshot(tmp_path, "good", "grown")
    results = {c.name: c for c in score_kb(vault, spec_for(scenario))}
    assert {n: c.passed for n, c in results.items()} == {
        **dict.fromkeys(KB_CHECKS, True), "front_page_growth": False, "front_page_bullets": False,
        "kb_structure": False,  # `kb structure` holds "Where it stands" to 6 bullets too (#734)
    }  # fmt: skip
    assert results["front_page_bullets"].details == ("'Where it stands' has 7 bullets (the standard's cap is 6)",)


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
        (n for n in KB_CHECKS if n != "kb_facts_cited"), True
    )


def test_a_loose_file_beside_the_front_page_fails_structure(tmp_path: Path, scenario) -> None:
    vault = _snapshot(tmp_path, "good", "broken")
    results = {c.name: c for c in score_kb(vault, spec_for(scenario))}
    assert results["kb_structure"].passed is False and "1 loose" in results["kb_structure"].details[0]
    assert results["kb_facts_cited"].passed is True


def test_facts_filed_only_outside_knowledge_do_not_count(tmp_path: Path, scenario) -> None:
    vault = _snapshot(tmp_path, "broken")  # the updates file alone, cited, but beside the front page
    assert _verdicts(vault, scenario)["kb_facts_cited"] is False


def _only_failing(vault: Path, scenario) -> dict[str, tuple[str, ...]]:
    return {c.name: c.details for c in score_kb(vault, spec_for(scenario)) if c.passed is not True}


def test_the_standards_placeholder_line_is_not_a_new_paragraph(tmp_path: Path, scenario) -> None:
    """Review finding 1: "_None right now._" in the emptied Open questions section passes."""
    assert _only_failing(_snapshot(tmp_path, "good", "placeholder"), scenario) == {}


def test_a_lazily_wrapped_bullet_is_one_entry(tmp_path: Path, scenario) -> None:
    """Review finding 5: a bullet's unindented next line continues it, as CommonMark reads it."""
    assert _only_failing(_snapshot(tmp_path, "good", "lazy-wrap"), scenario) == {}


def test_a_bullet_over_the_page_bullet_cap_fails(tmp_path: Path, scenario) -> None:
    """Review finding 2: one "Where it stands" bullet rewritten in place, past #597's 300 chars,
    within the byte budget, so only the bullet check catches it."""
    failing = _only_failing(_snapshot(tmp_path, "good", "long-bullet"), scenario)
    # `kb structure` fails an entry over 300 chars too (#734).
    assert sorted(failing) == ["front_page_bullets", "kb_structure"]
    assert re.fullmatch(
        r"line \d+: a new 3\d\d-char entry \(limit 300\): - The nightly PDF.*…", failing["front_page_bullets"][0]
    )


def test_a_dated_update_written_as_a_bullet_fails(tmp_path: Path, scenario) -> None:
    """Review finding 3: the update paragraph's way around the paragraph rule."""
    failing = _only_failing(_snapshot(tmp_path, "good", "bullet-diary"), scenario)
    assert list(failing) == ["front_page_no_narration"]
    [detail] = failing["front_page_no_narration"]
    assert detail.startswith("new entry reads as a run update (checked sources, dated entry, this run, update entry): ")


def test_a_citation_to_the_meetings_own_note_counts(tmp_path: Path, scenario) -> None:
    """Review finding 6: meeting-prep files a meeting as knowledge-base/meetings/<slug>/<date>.md."""
    assert _only_failing(_snapshot(tmp_path, "good", "meeting-cite"), scenario) == {}


def test_a_citation_to_a_note_nobody_wrote_does_not_count(tmp_path: Path, scenario) -> None:
    failing = _only_failing(_snapshot(tmp_path, "good", "dangling-cite"), scenario)
    assert list(failing) == ["kb_facts_cited"]
    assert failing["kb_facts_cited"] == (
        "row-cap-200k: in knowledge-base/work/projects/report-builder/knowledge/report-builder-decisions.md without a "
        "citation (a URL, or a link to an existing note under sources/ or meetings/)",
    )


def test_facts_written_as_their_negation_have_not_landed(tmp_path: Path, scenario) -> None:
    """Review finding 7: "is not merged yet" and "isn't done" cite the right links but say the opposite."""
    failing = _only_failing(_snapshot(tmp_path, "good", "negated"), scenario)
    assert list(failing) == ["kb_facts_cited"]
    assert {d.split(":", 1)[0] for d in failing["kb_facts_cited"]} == {"pr-57-merged", "proj-1240-done"}


def test_a_ticket_closed_on_the_front_page_only_passes(tmp_path: Path, scenario) -> None:
    """The shape real runs write: PROJ-1240's close is a rewritten, cited "Where it stands" bullet,
    and no knowledge/ note repeats it, because the decision behind it is already a decisions row."""
    results = {c.name: c for c in score_kb(_snapshot(tmp_path, "good", "status-front"), spec_for(scenario))}
    assert {n: c.passed for n, c in results.items()} == dict.fromkeys(KB_CHECKS, True)
    assert (
        "proj-1240-done: front page (status; decision in "
        f"{PROJECT}/knowledge/report-builder-decisions.md)" in results["kb_facts_cited"].details
    )


def test_the_scenarios_status_decision_matches_the_fixtures_decisions_row(scenario) -> None:
    [fact] = [f for f in scenario.expected_kb_facts if f.id == "proj-1240-done"]
    assert fact.status_decision
    note = (SCENARIO / "vault" / PROJECT / "knowledge" / "report-builder-decisions.md").read_text(encoding="utf-8")
    rows = [b for _k, b in blocks(note) if all(p.search(b) for p in fact.status_decision)]
    assert len(rows) == 1 and "Exports run on demand, not on a nightly schedule" in rows[0]
    assert all(not f.status_decision for f in scenario.expected_kb_facts if f.id != "proj-1240-done")


def test_the_fixture_vault_runs_the_front_page_lock_with_its_hook(tmp_path: Path, scenario) -> None:
    """The gate tests the config a live vault runs: #597's FRONT_PAGE_LOCK, enforced by the
    kb-lint pre-commit hook. The block must stay the one test_kb_lint_front_pages pins."""
    import yaml

    from tests.unit.test_kb_lint_front_pages import FRONT_PAGE_LOCK

    added = (SCENARIO / "scout-config.add.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(added) == yaml.safe_load(FRONT_PAGE_LOCK)
    vault = build_vault(tmp_path / "v", today=TODAY, scenario_dir=scenario.files)
    config = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8"))
    assert config["kb_lint"] == yaml.safe_load(FRONT_PAGE_LOCK)["kb_lint"] and config["timezone"] == "UTC"
    from scout.kb.hook import MARKER, hooks_dir

    assert MARKER in (hooks_dir(vault) / "pre-commit").read_text(encoding="utf-8")
    # A run's commit that adds narration to the front page is refused, as on a live vault.
    front = vault / PROJECT / "report-builder.md"
    front.write_text(front.read_text(encoding="utf-8") + "\nLast verified today against gh.\n", encoding="utf-8")
    _git(vault, "add", "-A", when=f"{TODAY}T07:00:00")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    done = subprocess.run(
        ["git", "-c", "user.name=Alex Example", "-c", "user.email=alex@example.com", "commit", "-q", "-m", "run"],
        cwd=vault, env=env, capture_output=True, text=True,
    )  # fmt: skip
    assert done.returncode != 0 and "page-narration" in done.stdout + done.stderr


def test_a_scenario_config_addition_may_not_override_the_installed_config(tmp_path: Path) -> None:
    sc = tmp_path / "scenario"
    sc.mkdir()
    (sc / "scout-config.add.yaml").write_text("timezone: Europe/Prague\n", encoding="utf-8")
    with pytest.raises(ValueError, match="already sets timezone"):
        build_vault(tmp_path / "v", today=TODAY, scenario_dir=sc)


def test_an_uncommitted_edit_counts(tmp_path: Path, scenario) -> None:
    vault = _snapshot(tmp_path)
    _lay(GOLDEN / "good", vault)  # written, never committed
    assert _verdicts(vault, scenario)["kb_facts_cited"] is True


def test_a_vault_without_a_fixture_commit_is_unevaluated_not_passed(tmp_path: Path, scenario) -> None:
    vault = tmp_path / "vault"
    _lay(SCENARIO / "vault" / PROJECT, vault / PROJECT)
    _mark_v2(vault)
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


EXISTING = source_keys(
    {"knowledge-base/sources/2026-10/2026-10-13-sync.md", "knowledge-base/meetings/report-sync/2026-10-13.md",
     "knowledge-base/projects/report-builder/knowledge/report-builder-decisions.md"}
)  # fmt: skip


@pytest.mark.parametrize(
    ("cite", "ok"),
    [
        ("([PR](https://github.com/example-org/api/pull/57))", True),
        ("([[sources/2026-10/2026-10-13-sync]])", True),
        ("([[knowledge-base/sources/2026-10/2026-10-13-sync.md|the sync]])", True),
        ("([[2026-10-13-sync]])", True),
        ("([[meetings/report-sync/2026-10-13]])", True),
        ("([[sources/2026-10/2026-10-13-nobody-wrote-this]])", False),  # dangling
        ("([[report-builder-decisions]])", False),  # exists, but isn't a source
        ("(per the sync)", False),
    ],
)
def test_what_counts_as_a_citation(cite: str, ok: bool) -> None:
    fact = KbFact("x", (re.compile("200,000"),))
    after = {"k.md": f"# T\n\nThe cap is 200,000 rows {cite}.\n"}
    assert kb_facts_cited({}, after, [fact], EXISTING).passed is ok


def test_a_negated_fact_has_not_landed() -> None:
    fact = KbFact(
        "pr", (re.compile(r"#57\b"), re.compile(r"(?i)\bmerged\b")), (re.compile(r"(?i)\bnot (?:yet )?merged"),)
    )
    url = "([PR](https://github.com/example-org/api/pull/57))"
    assert kb_facts_cited({}, {"k.md": f"PR #57 is not merged yet {url}.\n"}, [fact]).passed is False
    assert kb_facts_cited({}, {"k.md": f"PR #57 merged on Tuesday {url}.\n"}, [fact]).passed is True


TICKET = "([PROJ-1240](https://linear.app/example-org/issue/PROJ-1240))"


def _status_fact() -> KbFact:
    return KbFact(
        "status",
        (re.compile("PROJ-1240"), re.compile(r"(?i)\bretired\b")),
        (re.compile(r"(?i)n't\s+retired"),),
        status_decision=(re.compile(r"(?i)on[- ]demand"), re.compile(r"(?i)\bexports?\b")),
    )


DECISIONS = {"k/decisions.md": "# D\n\n| Date | Decision |\n|---|---|\n| 2026-10-01 | Exports run on demand |\n"}
FRONT = (
    "# P\n\n## Where it stands\n- The nightly PDF job is being retired {cite}.\n\n## History\n- Started as a script.\n"
)


def _status(knowledge: dict[str, str], front_after: str, fact: KbFact | None = None):
    before = FRONT.format(cite=TICKET)
    facts = [fact or _status_fact()]
    return kb_facts_cited(knowledge, knowledge, facts, front_before=before, front_after=front_after)


def test_a_status_fact_on_a_cited_where_it_stands_bullet_passes() -> None:
    after = FRONT.format(cite=TICKET).replace("is being retired", "is retired")
    result = _status(DECISIONS, after)
    assert result.passed is True
    assert result.details == ("status: front page (status; decision in k/decisions.md)",)


def test_a_status_fact_passes_with_the_decision_written_by_the_run() -> None:
    after = FRONT.format(cite=TICKET).replace("is being retired", "is retired")
    before = FRONT.format(cite=TICKET)
    result = kb_facts_cited({}, DECISIONS, [_status_fact()], front_before=before, front_after=after)
    assert result.passed is True


def test_a_status_fact_without_its_decision_in_knowledge_fails() -> None:
    after = FRONT.format(cite=TICKET).replace("is being retired", "is retired")
    result = _status({"k/decisions.md": "# D\n\n| 2026-10-01 | CSV only |\n"}, after)
    assert result.passed is False
    assert result.details == (
        "status: on the front page's 'Where it stands', but no knowledge/ note records the decision behind it",
    )


def test_a_status_fact_on_the_front_page_without_a_citation_fails() -> None:
    after = FRONT.format(cite=TICKET).replace(f"is being retired {TICKET}", "is retired (PROJ-1240)")
    result = _status(DECISIONS, after)
    assert result.passed is False
    assert result.details == (
        "status: on the front page's 'Where it stands' without a citation "
        "(a URL, or a link to an existing note under sources/ or meetings/)",
    )


def test_a_status_fact_outside_where_it_stands_fails() -> None:
    after = FRONT.format(cite=TICKET).replace("- Started as a script.", f"- The nightly PDF job is retired {TICKET}.")
    result = _status(DECISIONS, after)
    assert result.passed is False
    assert result.details == ("status: in no new or changed block of a knowledge/ note",)


def test_a_negated_status_on_the_front_page_fails() -> None:
    after = FRONT.format(cite=TICKET).replace("is being retired", "isn't retired yet")
    assert _status(DECISIONS, after).passed is False


def test_a_fact_without_status_decision_on_the_front_page_only_fails() -> None:
    import dataclasses

    after = FRONT.format(cite=TICKET).replace("is being retired", "is retired")
    result = _status(DECISIONS, after, dataclasses.replace(_status_fact(), status_decision=()))
    assert result.passed is False
    assert result.details == ("status: in no new or changed block of a knowledge/ note",)


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


def test_blocks_follow_lazy_continuation_but_a_blank_line_ends_an_item() -> None:
    text = "- an item\nwrapped lazily\n\nA paragraph.\n"
    assert blocks(text) == [("item", "- an item\nwrapped lazily"), ("prose", "A paragraph.")]


def test_bullets_already_long_in_the_fixture_are_not_new_debt() -> None:
    from tests.eval.harness.checks.kb_writing import front_page_bullets

    long = "- " + "x" * 320
    before = f"# P\n\n## Where it stands\n{long}\n"
    assert front_page_bullets(before, before, 300, ["Where it stands"]).passed is True
    assert front_page_bullets(before, before + long + "y\n", 300, ["Where it stands"]).passed is False
    six = "".join(f"- b{i}\n" for i in range(6))
    page = "# P\n\n## Where it stands\n"
    assert front_page_bullets(page + six, page + six, 300, ["Where it stands"]).passed is True
    assert front_page_bullets(page + six, page + six + "- b6\n", 300, ["Where it stands"]).passed is False


def test_entry_markers_skip_the_sections_dated_by_design() -> None:
    dated = (Marker("dated entry", re.compile(r"^- \d{4}-\d{2}-\d{2}")),)
    before = "# P\n\n## Where it stands\n- a\n\n## What's next\n- 2026-10-20: pilot\n"
    ahead = before.replace("- 2026-10-20: pilot", "- 2026-10-21: pilot")
    assert front_page_no_narration(before, ahead, (), dated).passed is True
    diary = before.replace("- a\n", "- a\n- 2026-10-13: checked everything\n")
    assert front_page_no_narration(before, diary, (), dated).passed is False


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
        ("kb_project: p\nexpected_kb_facts: [{id: x, all: [a], status_decision: ['(']}]", "bad pattern"),
        ("kb_project: p\nexpected_kb_facts: [{id: x, all: [a], status_decision: a}]", "`status_decision` must be"),
    ],
)
def test_bad_kb_settings_fail_before_a_session_is_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_scenario(_probe(tmp_path, monkeypatch, extra))


# ----- void runs: the session finished but did nothing ----------------------------------------


def _metrics(*calls: ToolCall, denials: tuple[str, ...] | None = None) -> SampleMetrics:
    return SampleMetrics(
        calls=calls, tool_call_count=len(calls), error_count=sum(c.is_error for c in calls), turns=5,
        input_tokens=1, output_tokens=1, cache_read_tokens=0, cost_usd=0.3, duration_s=18.9,
        denials=tuple(c.name for c in calls if c.denied) if denials is None else denials, model="m",
        mcp_servers=(), result_subtype="success", skipped_lines=0,
    )  # fmt: skip


def _call(name: str, inp: dict, *, error: bool = False, denied: bool = False, gate: str | None = None) -> ToolCall:
    return ToolCall(
        seq=1, name=name, input=inp, is_error=error or denied or bool(gate), position=0.5, gate=gate, denied=denied
    )


DENIED_BASH = _call("Bash", {"command": "ls .scout-state ../.scout-state 2>/dev/null; " + "x" * 400}, denied=True)


def test_a_session_that_did_nothing_after_a_denial_is_void_with_its_denials() -> None:
    void = void_run(_metrics(_call("Read", {"file_path": "SKILL.md"}), DENIED_BASH), 0, False)
    assert void is not None and void["result"] == "success"
    assert (void["duration_s"], void["turns"], void["tool_calls"]) == (18.9, 5, 2)
    [denied] = void["denials"]
    assert denied["tool"] == "Bash" and denied["by"] == "permissions"
    assert denied["input"].startswith("ls .scout-state ../.scout-state")
    assert len(denied["input"]) == DENIED_INPUT_CHARS


@pytest.mark.parametrize(
    "calls",
    [
        (),  # it ended at once
        (_call("Read", {"file_path": "SKILL.md"}),),  # it read and stopped
        (_call("Bash", {"command": "false"}, error=True),),  # a call failed on its own: no refusal
    ],
)
def test_a_session_that_did_nothing_with_no_denial_is_not_void(calls) -> None:
    """A brain or phase change that makes runs skip KB writing must fail the gate, not ask for a rerun
    every time (at one model session each)."""
    assert void_run(_metrics(*calls), 0, False) is None


def test_a_scout_gate_hook_deny_makes_a_do_nothing_run_void_and_is_listed() -> None:
    hooked = _call("Bash", {"command": "cat knowledge-base/people/x.md"}, gate="kb-read-gate")
    void = void_run(_metrics(hooked, DENIED_BASH), 0, False)
    assert void is not None
    assert [(d["tool"], d["by"]) for d in void["denials"]] == [("Bash", "kb-read-gate"), ("Bash", "permissions")]
    assert void["denials"][0]["input"] == "cat knowledge-base/people/x.md"
    only_hook = void_run(_metrics(hooked), 0, False)
    assert only_hook is not None and [d["by"] for d in only_hook["denials"]] == ["kb-read-gate"]


def test_a_denied_read_is_listed_by_its_path() -> None:
    void = void_run(_metrics(_call("Read", {"file_path": "/elsewhere/x.md"}, denied=True)), 0, False)
    assert void is not None
    assert void["denials"] == [{"tool": "Read", "input": "/elsewhere/x.md", "by": "permissions"}]
    mcp = void_run(_metrics(_call("mcp__x__send", {"to": "a"}, denied=True)), 0, False)
    assert mcp is not None
    assert mcp["denials"] == [{"tool": "mcp__x__send", "input": '{"to": "a"}', "by": "permissions"}]


def test_an_older_transcript_names_the_denied_tool_without_its_input() -> None:
    void = void_run(_metrics(_call("Bash", {"command": "ls"}, error=True), denials=("Bash",)), 0, False)
    assert void is not None and void["denials"] == [{"tool": "Bash", "input": "", "by": "permissions"}]


@pytest.mark.parametrize(
    ("calls", "commits", "changed"),
    [
        ((DENIED_BASH,), 1, False),  # it committed: it worked
        ((DENIED_BASH,), 0, True),  # it changed the project's pages
        ((DENIED_BASH, _call("Write", {"file_path": "a.md"})), 0, False),  # a write went through
        ((DENIED_BASH, _call("Edit", {"file_path": "a.md"})), 0, False),
        ((DENIED_BASH,), None, False),  # git couldn't say: the checks decide
        ((DENIED_BASH,), 0, None),
    ],
)
def test_a_session_that_did_something_or_cannot_be_measured_is_not_void(calls, commits, changed) -> None:
    assert void_run(_metrics(*calls), commits, changed) is None


def test_a_failed_write_still_counts_as_nothing_written() -> None:
    assert void_run(_metrics(_call("Write", {"file_path": "a.md"}, error=True), DENIED_BASH), 0, False) is not None


def test_no_transcript_is_never_void() -> None:
    assert void_run(None, 0, False) is None


def test_commits_and_page_changes_are_measured_against_the_fixture(tmp_path: Path, scenario) -> None:
    vault = _snapshot(tmp_path)
    assert commits_since_fixture(vault) == 0 and pages_changed(vault, scenario.kb_project) is False
    front = vault / PROJECT / "report-builder.md"
    front.write_text(front.read_text(encoding="utf-8") + "\n- one more\n", encoding="utf-8")
    assert pages_changed(vault, scenario.kb_project) is True  # uncommitted, like the checks
    _git(vault, "commit", "-q", "-am", "run", when=f"{TODAY}T07:10:00")
    assert commits_since_fixture(vault) == 1


def test_an_unreadable_vault_measures_nothing(tmp_path: Path, scenario) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    assert commits_since_fixture(bare) is None and pages_changed(bare, scenario.kb_project) is None


def test_void_outranks_fail_and_aborted_outranks_void(tmp_path: Path, scenario) -> None:
    vault, spec = _snapshot(tmp_path), spec_for(scenario)
    checks = score_kb(vault, spec)
    assert any(c.passed is False for c in checks)  # the untouched fixture leaves the facts unfiled
    void = void_run(_metrics(DENIED_BASH), 0, False)
    report = gate_report(vault, spec, checks, scenario=scenario.name, void=void)
    assert report["verdict"] == "void" and report["void"] == void and not report["passed"]
    assert report["checks"] and exit_code(report) == EXIT_VOID == 3
    aborted = gate_report(vault, spec, checks, scenario=scenario.name, void=void, sample={"aborted": {"r": 1}})
    assert aborted["verdict"] == "aborted" and exit_code(aborted) == 2
    assert gate_report(vault, spec, checks, scenario=scenario.name)["void"] is None


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


FAKE_CLAUDE = (
    """#!/usr/bin/env python3
import json, os, sys
"""
    + HOOKED_CLAUDE_PRELUDE
    + """
servers = [s for s in os.environ["EVAL_FAKE_SERVERS"].split(",") if s]
action = os.environ.get("EVAL_FAKE_ACTION", "")
denials = []
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5", "apiKeySource": "none",
                  "mcp_servers": [{"name": s, "status": "connected"} for s in servers]}), flush=True)
if action == "deny":  # the v0.18.21 void run's shape: one compound Bash refused, then the session stops
    command = "cat .scout-cache/run-health.json | head -30; ls .scout-state ../.scout-state 2>/dev/null"
    use = {"type": "tool_use", "id": "toolu_d1", "name": "Bash", "input": {"command": command}}
    print(json.dumps({"type": "assistant", "message": {"id": "m1", "content": [use]}}))
    print(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_d1",
                      "is_error": True, "content": "Permission to use Bash has been denied."}]}}))
    denials = [{"tool_name": "Bash", "tool_use_id": "toolu_d1", "tool_input": {"command": command}}]
if action == "commit":  # a session that did work: one commit past the fixture
    import subprocess
    subprocess.run(["git", "-c", "user.name=Alex Example", "-c", "user.email=alex@example.com", "commit",
                    "-q", "--no-verify", "--allow-empty", "-m", "run"], check=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                  "duration_ms": 1000, "total_cost_usd": 0.01, "result": "Nothing to do.",
                  "permission_denials": denials, "usage": {"input_tokens": 1, "output_tokens": 1}}))
"""
)


def _fake_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str, *extra_args: str) -> int:
    from tests.eval.harness.cli import main

    fake = tmp_path / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    servers = "claude_ai_Gmail,claude_ai_Google_Calendar,claude_ai_Linear,claude_ai_Slack"
    monkeypatch.setenv("EVAL_FAKE_SERVERS", servers)
    monkeypatch.setenv("EVAL_FAKE_ACTION", action)
    return main(["kb-gate", "--yes", "--claude", str(fake), "--cli-version", "2.1.278 (Claude Code)",
                 "--results", str(tmp_path / "results"), *extra_args])  # fmt: skip


def test_kb_gate_runs_one_session_and_writes_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stand-in session that writes nothing and had no call refused: the front page is untouched, so
    the gate fails on the facts alone (never void: nothing stopped it), and says so in the report it
    leaves in the result dir."""
    extra = tmp_path / "out" / "gate.json"
    code = _fake_gate(tmp_path, monkeypatch, "", "--report", str(extra))
    captured = capsys.readouterr()
    assert "Will start 1 session (kb-front-page ×1, morning-consolidation) on sonnet" in captured.err
    report = json.loads(captured.out)
    assert code == 1 and report["verdict"] == "fail" and report["void"] is None
    assert "VOID" not in captured.err
    assert {c["name"]: c["passed"] for c in report["checks"]} == {
        **dict.fromkeys(KB_CHECKS, True),
        "kb_facts_cited": False,
    }
    [result_dir] = (tmp_path / "results").iterdir()
    assert result_dir.name.startswith("kb-front-page-kb-gate-")
    assert json.loads((result_dir / "kb-gate-report.json").read_text(encoding="utf-8")) == report
    assert json.loads(extra.read_text(encoding="utf-8")) == report
    assert report["sample"]["result_dir"] == str(result_dir) and report["sample"]["aborted"] is None
    assert report["sample"]["resolved_model"] == "claude-sonnet-5"
    # Review finding 4: the report names the code it tested.
    checkout = report["sample"]["checkout"]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=EVAL_ROOT, capture_output=True, text=True).stdout.strip()
    assert checkout["commit"] == head and checkout["branch"] and isinstance(checkout["dirty"], bool)
    shutil.rmtree(tmp_path / "results")


def test_kb_gate_lists_the_denied_call_of_a_void_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """v0.18.21's run 1: one Bash refused, the session ended "success" having done nothing. Reported
    as void with the refused command, so the release flow can tell it from a KB-writing fail."""
    code = _fake_gate(tmp_path, monkeypatch, "deny")
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert code == 3 and report["verdict"] == "void"
    [denied] = report["void"]["denials"]
    assert denied["tool"] == "Bash" and denied["by"] == "permissions" and "../.scout-state" in denied["input"]
    assert (report["void"]["turns"], report["void"]["tool_calls"]) == (1, 1)
    assert "VOID run, not a verdict on the release" in captured.err
    assert "Bash (permissions): cat .scout-cache/run-health.json" in captured.err


def test_kb_gate_fails_a_run_that_did_work_but_cited_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A commit past the fixture means the session worked: unfiled facts are a fail, not void."""
    code = _fake_gate(tmp_path, monkeypatch, "commit")
    report = json.loads(capsys.readouterr().out)
    assert code == 1 and report["verdict"] == "fail" and report["void"] is None
