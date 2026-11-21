from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.kb import lint_config
from scout.kb.lint import enforced, lint_staged, run_hook
from scout.kb.lint_config import load_lint_config
from scout.lab.lint import lab_findings

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
LAB = "knowledge-base/lab"
FRONT = f"{LAB}/monitors/monitors.md"
TIMELINE = f"{LAB}/monitors/monitors-timeline.md"
E1 = f"{LAB}/monitors/experiments/monitors-E1-seeded.md"
E2 = f"{LAB}/monitors/experiments/monitors-E2-cascade.md"
INDEX = f"{LAB}/lab.md"
INDEX_TEXT = "# Lab\n\n## Active\n- [[monitors]] — Do monitors keep catching adaptive agents?\n"
runner = CliRunner()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURES / "monitors", tmp_path / LAB / "monitors")
    (tmp_path / INDEX).write_text(INDEX_TEXT, encoding="utf-8")
    return tmp_path


def _text(vault: Path, rel: str) -> str:
    return (vault / rel).read_text(encoding="utf-8")


def _lint(vault: Path, rel: str, text: str | None = None) -> list:
    return lab_findings(vault, rel, _text(vault, rel) if text is None else text)


def test_clean_fixture_has_no_findings(vault: Path) -> None:
    for rel in (FRONT, TIMELINE, E1, E2, INDEX):
        assert _lint(vault, rel) == [], rel


def test_files_outside_the_formats_are_ignored(vault: Path) -> None:
    assert lab_findings(vault, f"{LAB}/monitors/experiments/monitors-E1-seeded/notes.md", "anything") == []


def test_duplicate_qid_blocks(vault: Path) -> None:
    text = _text(vault, FRONT).replace("- **Q5** ", "- **Q3** ")
    found = _lint(vault, FRONT, text)
    assert [f.check for f in found] == ["lab-format"]
    assert found[0].blocking and "duplicate ID Q3" in found[0].message and found[0].line


def test_duplicate_eid_blocks(vault: Path) -> None:
    text = _text(vault, FRONT).replace("[[monitors-E1-seeded|E1]]", "[[monitors-E1-seeded|E2]]")
    assert any("duplicate ID E2" in f.message for f in _lint(vault, FRONT, text))


def test_next_q_must_exceed_every_question(vault: Path) -> None:
    text = _text(vault, FRONT).replace("next_q: 8", "next_q: 5")
    [f] = _lint(vault, FRONT, text)
    assert f.blocking and "next_q" in f.message and "Q5" in f.message


def test_next_e_must_exceed_every_experiment(vault: Path) -> None:
    text = _text(vault, FRONT).replace("next_e: 3", "next_e: 2")
    [f] = _lint(vault, FRONT, text)
    assert f.blocking and "next_e" in f.message


def test_next_q_missing_or_not_an_integer_blocks(vault: Path) -> None:
    gone = _text(vault, FRONT).replace("next_q: 8\n", "")
    assert any("next_q" in f.message for f in _lint(vault, FRONT, gone))
    junk = _text(vault, FRONT).replace("next_q: 8", "next_q: lots")
    assert any("next_q" in f.message for f in _lint(vault, FRONT, junk))


def test_thread_status_outside_the_vocabulary_blocks(vault: Path) -> None:
    text = _text(vault, FRONT).replace("status: active", "status: thriving")
    [f] = _lint(vault, FRONT, text)
    assert f.blocking and "thriving" in f.message and f.line == 1


def test_renamed_heading_is_a_parser_problem(vault: Path) -> None:
    text = _text(vault, FRONT).replace("## Evidence", "## Sources")
    found = _lint(vault, FRONT, text)
    assert found and all(f.check == "lab-format" and f.blocking for f in found)
    assert any("Evidence" in f.message for f in found)


def test_understanding_over_2kb_is_a_warning_only(vault: Path) -> None:
    text = _text(vault, FRONT).replace("Cascades look cheap", "x" * 2100 + " Cascades look cheap")
    [f] = _lint(vault, FRONT, text)
    assert (f.check, f.blocking) == ("lab-understanding-size", False)


def test_timeline_unknown_qid_blocks(vault: Path) -> None:
    text = _text(vault, TIMELINE).replace("opened Q5", "opened Q9")
    [f] = _lint(vault, TIMELINE, text)
    assert f.blocking and "Q9" in f.message and f.line == 6


def test_timeline_unknown_eid_blocks(vault: Path) -> None:
    text = _text(vault, TIMELINE).replace("planned E2.", "planned E7.")
    [f] = _lint(vault, TIMELINE, text)
    assert f.blocking and "E7" in f.message


def test_timeline_heading_form_and_tag_vocabulary(vault: Path) -> None:
    bad_form = _text(vault, TIMELINE).replace("### 2026-11-20 13:05 · owner", "### 2026-11-20 · owner")
    [f] = _lint(vault, TIMELINE, bad_form)
    assert f.blocking and f.line == 10
    bad_tag = _text(vault, TIMELINE).replace("· owner", "· musing")
    [g] = _lint(vault, TIMELINE, bad_tag)
    assert g.blocking and "musing" in g.message


def test_timeline_needs_its_h1(vault: Path) -> None:
    text = _text(vault, TIMELINE).replace("# Monitors under adaptive attack — timeline", "# Monitors")
    [f] = _lint(vault, TIMELINE, text)
    assert f.blocking and "timeline" in f.message and f.line == 1


def test_timeline_without_a_front_page_blocks(tmp_path: Path) -> None:
    rel = f"{LAB}/ghost/ghost-timeline.md"
    [f] = lab_findings(tmp_path, rel, "# Ghost — timeline\n\n### 2026-11-20 13:05 · owner\n- hi\n")
    assert f.blocking and "front page" in f.message


def test_experiment_not_before_before_planned_blocks(vault: Path) -> None:
    text = _text(vault, E2).replace("not_before: 2026-11-21T13:00", "not_before: 2026-11-21T00:30")
    [f] = _lint(vault, E2, text)
    assert f.blocking and "not_before" in f.message and f.line


def test_experiment_missing_key_and_unknown_status_block(vault: Path) -> None:
    text = _text(vault, E2).replace("cost_cap_usd: 1.00\n", "")
    [f] = _lint(vault, E2, text)
    assert f.blocking and "cost_cap_usd" in f.message
    bad = _text(vault, E2).replace("status: planned", "status: sleeping")
    assert any("sleeping" in f.message for f in _lint(vault, E2, bad))


def test_experiment_unparseable_time_blocks(vault: Path) -> None:
    text = _text(vault, E2).replace("planned: 2026-11-21T01:11", "planned: soon")
    [f] = _lint(vault, E2, text)
    assert f.blocking and "planned" in f.message


def test_index_missing_thread_blocks(vault: Path) -> None:
    [f] = lab_findings(vault, INDEX, "# Lab\n\n## Active\n")
    assert f.blocking and "monitors" in f.message


def test_index_private_threads_are_counted_not_listed(vault: Path) -> None:
    secret = vault / LAB / "secret"
    secret.mkdir()
    page = _text(vault, FRONT).replace("visibility: normal", "visibility: private")
    (secret / "secret.md").write_text(page, encoding="utf-8")
    assert [f.message for f in _lint(vault, INDEX, INDEX_TEXT)] != []
    assert _lint(vault, INDEX, INDEX_TEXT + "\n_1 private thread(s) not listed._\n") == []
    wrong = _lint(vault, INDEX, INDEX_TEXT + "\n_3 private thread(s) not listed._\n")
    assert len(wrong) == 1 and wrong[0].blocking and "private" in wrong[0].message
    # a private thread must not be linked from lab.md either way: it is simply not required
    assert _lint(vault, INDEX, INDEX_TEXT + "- [[secret]] — hi\n_1 private thread(s) not listed._\n") == []


def test_every_finding_uses_a_known_check_id() -> None:
    assert {"lab-format", "lab-understanding-size"} <= lint_config.KNOWN_CHECKS
    assert "lab-format" in lint_config.ALWAYS_BLOCKING
    assert "lab-understanding-size" in lint_config.WARNING_CHECKS


# ----- through the commit hook ------------------------------------------------------


def _stage_thread(kb_repo, front: str | None = None) -> None:
    root = FIXTURES / "monitors"
    kb_repo.stage(FRONT, front if front is not None else (root / "monitors.md").read_text(encoding="utf-8"))
    kb_repo.stage(TIMELINE, (root / "monitors-timeline.md").read_text(encoding="utf-8"))
    kb_repo.stage(E1, (root / "experiments/monitors-E1-seeded.md").read_text(encoding="utf-8"))
    kb_repo.stage(E2, (root / "experiments/monitors-E2-cascade.md").read_text(encoding="utf-8"))
    kb_repo.stage(INDEX, INDEX_TEXT)


def _broken_front() -> str:
    return (FIXTURES / "monitors" / "monitors.md").read_text(encoding="utf-8").replace("next_q: 8", "next_q: 2")


def test_staged_lab_file_blocks_commit(kb_repo) -> None:
    _stage_thread(kb_repo, _broken_front())
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    [f] = [x for x in result.findings if x.check == "lab-format"]
    assert (f.path, f.blocking) == (FRONT, True)
    assert run_hook(kb_repo.root, env={}, out=io.StringIO()) == 1


def test_a_clean_staged_thread_commits(kb_repo) -> None:
    _stage_thread(kb_repo)
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    assert [f for f in result.findings if f.check.startswith("lab-")] == []


@pytest.mark.parametrize("mode", ["report", "block"])
def test_lab_format_blocks_in_every_mode(kb_repo, mode: str) -> None:
    kb_repo.write("scout-config.yaml", f"kb_lint:\n  mode: {mode}\n")
    _stage_thread(kb_repo, _broken_front())
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 1
    assert "[lab-format]" in out.getvalue() and "Commit blocked" in out.getvalue()


def test_a_path_rule_cannot_turn_lab_format_off(kb_repo) -> None:
    kb_repo.write(
        "scout-config.yaml",
        "kb_lint:\n  path_modes:\n    - {glob: 'knowledge-base/lab/**', mode: report}\n",
    )
    _stage_thread(kb_repo, _broken_front())
    assert run_hook(kb_repo.root, env={}, out=io.StringIO()) == 1


def test_lint_override_applies_to_lab_format(kb_repo) -> None:
    _stage_thread(kb_repo, _broken_front())
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={"SCOUT_LINT_OVERRIDE": "hand-fixing next_q"}, out=out) == 0
    assert "override accepted" in out.getvalue()


def test_the_warning_never_blocks(kb_repo) -> None:
    front = (FIXTURES / "monitors" / "monitors.md").read_text(encoding="utf-8")
    _stage_thread(kb_repo, front.replace("Cascades look cheap", "x" * 2100 + " Cascades look cheap"))
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    [f] = [x for x in result.findings if x.check == "lab-understanding-size"]
    assert not f.blocking and not enforced(f, load_lint_config(kb_repo.root))
    assert run_hook(kb_repo.root, env={}, out=io.StringIO()) == 0


# ----- scoutctl lab lint --------------------------------------------------------------


def _run(vault: Path, *args: str):
    return runner.invoke(cli.app, ["lab", "--vault", str(vault), "lint", *args])


def test_cli_lint_clean_vault_exits_zero(vault: Path) -> None:
    result = _run(vault)
    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""


def test_cli_lint_prints_findings_and_exits_one(vault: Path) -> None:
    front = vault / FRONT
    front.write_text(front.read_text(encoding="utf-8").replace("next_q: 8", "next_q: 2"), encoding="utf-8")
    result = _run(vault)
    assert result.exit_code == 1
    line = result.output.strip().splitlines()[0]
    assert line.startswith(f"{FRONT}:") and ": lab-format: " in line and "next_q" in line


def test_cli_lint_paths_limit_the_files_and_warnings_exit_zero(vault: Path) -> None:
    front = vault / FRONT
    front.write_text(
        front.read_text(encoding="utf-8").replace("Cascades look cheap", "x" * 2100 + " Cascades look cheap"),
        encoding="utf-8",
    )
    (vault / TIMELINE).write_text("garbage\n", encoding="utf-8")
    result = _run(vault, FRONT)
    assert result.exit_code == 0, result.output
    assert "lab-understanding-size" in result.output and "monitors-timeline" not in result.output
    assert _run(vault, str(vault / TIMELINE)).exit_code == 1
