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
from scout.lab import writer
from scout.lab.lint import lab_findings

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
LAB = "knowledge-base/lab"
FRONT = f"{LAB}/summary-drift/summary-drift.md"
TIMELINE = f"{LAB}/summary-drift/summary-drift-timeline.md"
E1 = f"{LAB}/summary-drift/experiments/summary-drift-E1-baseline.md"
E2 = f"{LAB}/summary-drift/experiments/summary-drift-E2-section-hash.md"
INDEX = f"{LAB}/lab.md"
INDEX_TEXT = (
    "# Lab\n\n## Active\n- [[summary-drift]] — "
    "What is the cheapest signal that a stored summary no longer matches the page it summarises?\n"
)
runner = CliRunner()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURES / "summary-drift", tmp_path / LAB / "summary-drift")
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
    assert lab_findings(vault, f"{LAB}/summary-drift/experiments/summary-drift-E1-baseline/notes.md", "anything") == []


def test_duplicate_qid_blocks(vault: Path) -> None:
    text = _text(vault, FRONT).replace("- **Q5** ", "- **Q3** ")
    found = _lint(vault, FRONT, text)
    assert [f.check for f in found] == ["lab-format"]
    assert found[0].blocking and "duplicate ID Q3" in found[0].message and found[0].line


def test_duplicate_eid_blocks(vault: Path) -> None:
    text = _text(vault, FRONT).replace("[[summary-drift-E1-baseline|E1]]", "[[summary-drift-E1-baseline|E2]]")
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
    text = _text(vault, FRONT).replace("Hashing only the sections", "x" * 2100 + " Hashing only the sections")
    [f] = _lint(vault, FRONT, text)
    assert (f.check, f.blocking) == ("lab-understanding-size", False)


def test_timeline_unknown_qid_blocks(vault: Path) -> None:
    text = _text(vault, TIMELINE).replace("opened Q5", "opened Q9")
    [f] = _lint(vault, TIMELINE, text)
    assert f.blocking and "Q9" in f.message and f.line == 6


def test_timeline_unknown_eid_blocks(vault: Path) -> None:
    text = _text(vault, TIMELINE).replace("(E2 planned)", "(E7 planned)")
    [f] = _lint(vault, TIMELINE, text)
    assert f.blocking and "E7" in f.message


def test_ids_in_prose_bullets_are_not_references(vault: Path) -> None:
    text = _text(vault, TIMELINE).replace(
        "the length rule disagrees", "Q3 2026 results; E5 embeddings; the length rule disagrees"
    )
    assert _lint(vault, TIMELINE, text) == []


def test_unknown_id_in_the_steers_bullet_blocks(vault: Path) -> None:
    text = _text(vault, TIMELINE).replace("alex on Q3", "alex on Q8")
    [f] = _lint(vault, TIMELINE, text)
    assert f.blocking and "Q8" in f.message


def test_timeline_heading_form_and_tag_vocabulary(vault: Path) -> None:
    bad_form = _text(vault, TIMELINE).replace("### 2026-11-20 13:05 · owner", "### 2026-11-20 · owner")
    [f] = _lint(vault, TIMELINE, bad_form)
    assert f.blocking and f.line == 10
    bad_tag = _text(vault, TIMELINE).replace("· owner", "· musing")
    [g] = _lint(vault, TIMELINE, bad_tag)
    assert g.blocking and "musing" in g.message


def test_timeline_needs_its_h1(vault: Path) -> None:
    text = _text(vault, TIMELINE).replace("# Keeping cached summaries honest — timeline", "# Summary-Drift")
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
    assert f.blocking and "summary-drift" in f.message


def test_index_private_threads_are_counted_not_listed(vault: Path) -> None:
    secret = vault / LAB / "secret"
    secret.mkdir()
    page = _text(vault, FRONT).replace("visibility: normal", "visibility: private")
    (secret / "secret.md").write_text(page, encoding="utf-8")
    assert [f.message for f in _lint(vault, INDEX, INDEX_TEXT)] != []
    assert _lint(vault, INDEX, INDEX_TEXT + "\n_1 private thread not listed._\n") == []
    wrong = _lint(vault, INDEX, INDEX_TEXT + "\n_3 private threads not listed._\n")
    assert len(wrong) == 1 and wrong[0].blocking and "private" in wrong[0].message
    # a private thread is never named in lab.md: linking it, in any spelling, blocks
    for link in ("[[secret]]", "[[secret|a thread]]", "[[lab/secret/secret]]", "[[secret-timeline]]"):
        [f] = _lint(vault, INDEX, INDEX_TEXT + f"- {link} — hi\n_1 private thread not listed._\n")
        assert f.blocking and "links private thread 'secret'" in f.message, link
    assert _lint(vault, INDEX, INDEX_TEXT + "- [[secretive]] — another note\n_1 private thread not listed._\n") == []


def _private_thread(vault: Path) -> None:
    """Make the fixture thread private, with its timeline and experiment notes saying so."""
    (vault / FRONT).write_text(_text(vault, FRONT).replace("visibility: normal", "visibility: private"), "utf-8")
    for rel in (E1, E2):
        (vault / rel).write_text(writer.set_frontmatter(_text(vault, rel), visibility="private"), "utf-8")
    (vault / TIMELINE).write_text("---\nvisibility: private\n---\n" + _text(vault, TIMELINE), "utf-8")


def test_a_private_threads_timeline_with_its_visibility_is_clean(vault: Path) -> None:
    _private_thread(vault)
    for rel in (FRONT, TIMELINE, E1, E2):
        assert _lint(vault, rel) == [], rel


def test_every_file_of_a_private_thread_must_say_private(vault: Path) -> None:
    """I4: the privacy layer labels each file by its own frontmatter; a private thread's file without it would
    be shown to a delegate."""
    _private_thread(vault)
    bare = _text(vault, TIMELINE).split("---\n", 2)[2]  # the timeline without its frontmatter
    [f] = _lint(vault, TIMELINE, bare)
    assert f.blocking and "visibility: private" in f.message
    normal_note = _text(vault, E2).replace("visibility: private", "visibility: normal")
    [f] = _lint(vault, E2, normal_note)
    assert f.blocking and "visibility: private" in f.message
    out = f"{LAB}/summary-drift/experiments/summary-drift-E2-section-hash/results.md"
    [f] = lab_findings(vault, out, "# Results\nnumbers\n")
    assert f.blocking and f.path == out
    assert lab_findings(vault, out, "---\nvisibility: private\n---\n# Results\n") == []
    # The front page names every sibling that doesn't say so (only the front page may be in the commit).
    (vault / TIMELINE).write_text(bare, "utf-8")
    [f] = _lint(vault, FRONT)
    assert f.blocking and TIMELINE in f.message


def test_lint_flags_non_markdown_in_a_private_thread(vault: Path) -> None:
    rel = f"{LAB}/summary-drift/experiments/summary-drift-E1-x/run.py"
    assert lab_findings(vault, rel, "print(1)\n") == []  # a normal thread may hold scripts
    _private_thread(vault)
    [f] = lab_findings(vault, rel, "print(1)\n")
    assert f.blocking and f.path == rel and "a private thread can't hold non-markdown files" in f.message


def test_a_staged_script_in_a_private_thread_blocks_the_commit(kb_repo) -> None:
    _stage_thread(kb_repo, _text_of_fixture().replace("visibility: normal", "visibility: private"))
    rel = f"{LAB}/summary-drift/experiments/summary-drift-E1-x/run.py"
    kb_repo.stage(rel, "print(1)\n")
    found = [f for f in _lab_findings(kb_repo) if "non-markdown" in f.message]
    assert [(f.path, f.blocking) for f in found] == [(rel, True)]


def test_a_timelines_frontmatter_holds_only_its_visibility(vault: Path) -> None:
    body = _text(vault, TIMELINE)
    assert _lint(vault, TIMELINE, "---\nvisibility: normal\n---\n" + body) == []
    for fm in ("visibility: secret", "visibility: private\nowner: alex", "- a list"):
        [f] = _lint(vault, TIMELINE, f"---\n{fm}\n---\n" + body)
        assert f.blocking and "frontmatter" in f.message, fm


def test_every_finding_uses_a_known_check_id() -> None:
    assert {"lab-format", "lab-understanding-size"} <= lint_config.KNOWN_CHECKS
    assert "lab-format" in lint_config.ALWAYS_BLOCKING
    assert "lab-understanding-size" in lint_config.WARNING_CHECKS


# ----- through the commit hook ------------------------------------------------------


def _stage_thread(kb_repo, front: str | None = None) -> None:
    root = FIXTURES / "summary-drift"
    kb_repo.stage(FRONT, front if front is not None else (root / "summary-drift.md").read_text(encoding="utf-8"))
    kb_repo.stage(TIMELINE, (root / "summary-drift-timeline.md").read_text(encoding="utf-8"))
    kb_repo.stage(E1, (root / "experiments/summary-drift-E1-baseline.md").read_text(encoding="utf-8"))
    kb_repo.stage(E2, (root / "experiments/summary-drift-E2-section-hash.md").read_text(encoding="utf-8"))
    kb_repo.stage(INDEX, INDEX_TEXT)


def _broken_front() -> str:
    return (
        (FIXTURES / "summary-drift" / "summary-drift.md").read_text(encoding="utf-8").replace("next_q: 8", "next_q: 2")
    )


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
    front = (FIXTURES / "summary-drift" / "summary-drift.md").read_text(encoding="utf-8")
    _stage_thread(kb_repo, front.replace("Hashing only the sections", "x" * 2100 + " Hashing only the sections"))
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
        front.read_text(encoding="utf-8").replace(
            "Hashing only the sections", "x" * 2100 + " Hashing only the sections"
        ),
        encoding="utf-8",
    )
    (vault / TIMELINE).write_text("garbage\n", encoding="utf-8")
    result = _run(vault, FRONT)
    assert result.exit_code == 0, result.output
    assert "lab-understanding-size" in result.output and "summary-drift-timeline" not in result.output
    assert _run(vault, str(vault / TIMELINE)).exit_code == 1


# ----- the staged lab is judged as a whole, from the index ---------------------------


def _lab_findings(kb_repo) -> list:
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    return [f for f in result.findings if f.check.startswith("lab-")]


def test_thread_staged_without_lab_md_blocks(kb_repo) -> None:
    _stage_thread(kb_repo)
    kb_repo.git("rm", "-q", "--cached", INDEX)
    found = [f for f in _lab_findings(kb_repo) if f.path == INDEX]
    assert len(found) == 1 and found[0].blocking and "missing" in found[0].message


def _text_of_fixture() -> str:
    return (FIXTURES / "summary-drift" / "summary-drift.md").read_text(encoding="utf-8")


def test_new_thread_without_staging_lab_md_blocks(kb_repo) -> None:
    _stage_thread(kb_repo)
    kb_repo.commit()
    kb_repo.stage(f"{LAB}/other/other.md", _text_of_fixture())
    [f] = [f for f in _lab_findings(kb_repo) if f.check == "lab-format"]
    assert f.path == INDEX and "'other'" in f.message


def test_visibility_flip_with_lab_md_unchanged_blocks(kb_repo) -> None:
    _stage_thread(kb_repo)
    kb_repo.commit()
    kb_repo.stage(FRONT, _text_of_fixture().replace("visibility: normal", "visibility: private"))
    found = sorted(f.message for f in _lab_findings(kb_repo) if f.check == "lab-format" and f.path == INDEX)
    assert len(found) == 2 and "links private thread 'summary-drift'" in found[0] and "1 private thread" in found[1]
    # and the thread's other files, which still don't say private, are named on the front page
    assert {f.path for f in _lab_findings(kb_repo) if f.path != INDEX} == {FRONT}


def test_index_is_checked_once_per_commit(kb_repo) -> None:
    _stage_thread(kb_repo)
    kb_repo.git("rm", "-q", "--cached", INDEX)
    assert len([f for f in _lab_findings(kb_repo) if f.path == INDEX]) == 1


def test_untracked_thread_folder_does_not_block_a_lab_md_commit(kb_repo) -> None:
    _stage_thread(kb_repo)
    kb_repo.commit()
    kb_repo.write(f"{LAB}/draft/draft.md", _text_of_fixture())
    kb_repo.stage(INDEX, INDEX_TEXT + "\n")
    assert _lab_findings(kb_repo) == []


def test_partially_staged_front_page_is_judged_by_its_staged_text(kb_repo) -> None:
    _stage_thread(kb_repo)
    kb_repo.commit()
    front = _text_of_fixture()
    kb_repo.stage(FRONT, front)  # the index keeps a good page
    kb_repo.write(FRONT, front.replace("**Q5**", "**Q4**").replace("next_q: 8", "next_q: 1"))
    kb_repo.stage(TIMELINE, (FIXTURES / "summary-drift/summary-drift-timeline.md").read_text(encoding="utf-8") + "\n")
    assert _lab_findings(kb_repo) == []
    # and the other way: a bad staged page with a good worktree copy still blocks its timeline's IDs
    kb_repo.stage(FRONT, front.replace("- **Q5** ", "- **Q6** "))
    kb_repo.write(FRONT, front)
    assert any("Q5" in f.message for f in _lab_findings(kb_repo))
