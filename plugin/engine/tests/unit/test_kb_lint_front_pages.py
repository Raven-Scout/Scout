"""Blocking regrowth on project front pages: the `{folder}` token in `path_modes`
globs, the size ratchet and `page-bullet` on a front page, and what the block tells
the run to do."""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest

from scout.kb.lint import lint_staged, run_hook
from scout.kb.lint_config import REPORT_UNTIL_NAMED, glob_match, load_lint_config

FRONT = "knowledge-base/projects/widgets/widgets.md"
LOOSE = "knowledge-base/projects/widgets/notes.md"
NOTE = "knowledge-base/projects/widgets/knowledge/widgets-pricing.md"
SECTIONS = ["Where it stands", "What's next", "Key decisions", "Open questions", "People", "Go deeper", "History"]

# The block the PR body gives the maintainer to apply after release; keep the two in step.
FRONT_PAGE_LOCK = """\
kb_lint:
  path_modes:
    - glob: "knowledge-base/projects/*/{folder}.md"
      mode: block
      exclude: ["knowledge-base/projects/archived/**"]
      checks: [size, page-narration, page-sections, page-bullet]
    - {glob: "knowledge-base/**", mode: block, exclude_logs: true, checks: [mega-line, diary]}
"""


def _config(tmp_path: Path, text: str) -> None:
    (tmp_path / "scout-config.yaml").write_text(text, encoding="utf-8")


def _rule(glob: str, **extra: str) -> str:
    fields = "".join(f", {k}: {v}" for k, v in extra.items())
    return f"kb_lint:\n  path_modes:\n    - {{glob: '{glob}', mode: block{fields}}}\n"


def page(where: list[str] | None = None, extra: str = "", fill: int = 0) -> str:
    """A front page meeting the standard; ``where`` replaces "Where it stands"'s body lines."""
    parts = []
    for s in SECTIONS:
        body = "\n".join(where) if s == "Where it stands" and where is not None else "None right now."
        parts.append(f"## {s}\n{body}\n\n")
    padding = "".join(f"- a plain history line, number {i}\n" for i in range(fill))
    return f"---\nname: widgets\ntype: project\nas_of: 2026-10-01\n---\n# Widgets\n\n{''.join(parts)}{padding}{extra}"


# ----- the `{folder}` token ----------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "front"),
    [
        (FRONT, True),
        (LOOSE, False),
        (NOTE, False),
        ("knowledge-base/projects/widgets/knowledge/widgets.md", False),
        ("knowledge-base/projects/widgets/archive/widgets.md", False),
        ("knowledge-base/projects/widgets/gadgets/gadgets.md", False),  # a sub-project is two folders down
        ("knowledge-base/projects/widgets.md", False),
        ("knowledge-base/topics/widgets/widgets.md", False),
    ],
)
def test_folder_matches_a_file_named_like_its_folder(tmp_path: Path, rel: str, front: bool) -> None:
    _config(tmp_path, _rule("knowledge-base/projects/*/{folder}.md"))
    cfg = load_lint_config(tmp_path)
    assert cfg.warnings == ()
    assert (cfg.mode_for(rel, "size") == "block") is front


def test_folder_takes_literal_text_around_it(tmp_path: Path) -> None:
    _config(tmp_path, _rule("knowledge-base/**/index-{folder}.v2.md"))
    cfg = load_lint_config(tmp_path)
    assert cfg.mode_for("knowledge-base/a/b/index-b.v2.md", "size") == "block"
    assert cfg.mode_for("knowledge-base/a/b/index-a.v2.md", "size") == "report"
    assert cfg.mode_for("knowledge-base/a/b/b.md", "size") == "report"


def test_folder_works_in_a_rules_exclude(tmp_path: Path) -> None:
    _config(tmp_path, _rule("knowledge-base/projects/*/*.md", exclude="['knowledge-base/projects/*/{folder}.md']"))
    cfg = load_lint_config(tmp_path)
    assert cfg.mode_for(LOOSE, "size") == "block"
    assert cfg.mode_for(FRONT, "size") == "report"


def test_folder_is_not_part_of_the_shared_glob_language() -> None:
    """Privacy, sync and the Agnes indexer read globs through glob_match: `{` stays a literal there."""
    assert glob_match("projects/*/{folder}.md", "projects/w/{folder}.md")
    assert not glob_match("projects/*/{folder}.md", "projects/w/w.md")


@pytest.mark.parametrize(
    "glob",
    [
        "knowledge-base/{folder}/x.md",  # not in the last segment
        "knowledge-base/projects/*/{folder}-{folder}.md",  # twice
        "knowledge-base/projects/*/{folder}*.md",  # beside a wildcard
        "knowledge-base/projects/*/?{folder}.md",
        "{folder}.md",  # no folder to name
    ],
)
def test_folder_where_it_cant_be_read_drops_the_rule_with_a_warning(tmp_path: Path, glob: str) -> None:
    _config(tmp_path, "kb_lint:\n  mode: report\n" + _rule(glob).removeprefix("kb_lint:\n"))
    cfg = load_lint_config(tmp_path)
    assert cfg.path_modes == ()
    assert any("{folder}" in w for w in cfg.warnings), cfg.warnings


def test_a_bad_folder_exclude_drops_the_rule(tmp_path: Path) -> None:
    _config(tmp_path, _rule("knowledge-base/**", exclude="['{folder}/x.md']"))
    cfg = load_lint_config(tmp_path)
    assert cfg.path_modes == ()
    assert any("{folder}" in w for w in cfg.warnings)


def test_a_folder_rule_is_checked_against_the_linted_files(tmp_path: Path) -> None:
    _config(tmp_path, _rule("knowledge-base/projects/*/{folder}.md"))
    cfg = load_lint_config(tmp_path)
    assert cfg.rules_matching_nothing([FRONT]) == []
    assert len(cfg.rules_matching_nothing([LOOSE, NOTE])) == 1


def test_a_folder_glob_on_a_long_path_returns_at_once(tmp_path: Path) -> None:
    _config(tmp_path, _rule("**a" * 10 + "**/{folder}.md"))
    cfg = load_lint_config(tmp_path)
    deep = "knowledge-base/" + "a" * 60 + "/" + "b" * 60
    started = time.monotonic()
    assert cfg.mode_for(f"{deep}/{'b' * 60}.md", "size") == "block"
    assert cfg.mode_for(f"{deep}/{'b' * 59}.md", "size") == "report"
    assert time.monotonic() - started < 1


# ----- the maintainer's block --------------------------------------------------------


def test_the_front_page_lock_reads_cleanly_and_targets_front_pages_only(tmp_path: Path) -> None:
    _config(tmp_path, FRONT_PAGE_LOCK)
    cfg = load_lint_config(tmp_path)
    assert cfg.warnings == ()
    for check in ("size", "page-narration", "page-sections", "page-bullet"):
        assert cfg.mode_for(FRONT, check) == "block"
        assert cfg.mode_for(LOOSE, check) == "report"
        assert cfg.mode_for(NOTE, check) == "report"
        assert cfg.mode_for("knowledge-base/projects/archived/archived.md", check) == "report"
    # The long-line and diary rule still covers front pages, through the second rule.
    assert cfg.mode_for(FRONT, "mega-line") == "block"


# ----- the size ratchet on a front page ------------------------------------------------


def _hook(kb_repo, env: dict[str, str] | None = None) -> tuple[int, str]:
    out = io.StringIO()
    return run_hook(kb_repo.root, env=env or {}, out=out), out.getvalue()


def _locked(kb_repo, committed: str) -> None:
    kb_repo.write("scout-config.yaml", FRONT_PAGE_LOCK)
    kb_repo.stage(FRONT, committed)
    kb_repo.commit()


def test_an_over_budget_front_page_may_not_grow(kb_repo) -> None:
    _locked(kb_repo, page(fill=500))  # ~17 KB
    kb_repo.stage(FRONT, page(fill=501))
    code, out = _hook(kb_repo)
    assert code == 1
    assert "[size]" in out and "knowledge/" in out and "first" in out


def test_an_over_budget_front_page_may_shrink(kb_repo) -> None:
    _locked(kb_repo, page(fill=520))
    kb_repo.stage(FRONT, page(fill=510))
    assert _hook(kb_repo)[0] == 0


def test_a_commit_may_not_push_a_front_page_over_budget(kb_repo) -> None:
    _locked(kb_repo, page(fill=400))  # ~13.5 KB
    kb_repo.stage(FRONT, page(fill=480))
    code, out = _hook(kb_repo)
    assert code == 1 and "[size]" in out


def test_a_loose_file_or_knowledge_note_over_budget_only_reports(kb_repo) -> None:
    _locked(kb_repo, page())
    kb_repo.stage(LOOSE, "x\n" * 9000)
    kb_repo.stage(NOTE, "a cited line [[widgets]]\n" * 600)
    code, out = _hook(kb_repo)
    assert code == 0 and out.count("[size] (report)") == 2


def test_the_override_after_two_failed_attempts_is_unchanged(kb_repo) -> None:
    _locked(kb_repo, page(fill=500))
    kb_repo.stage(FRONT, page(fill=501))
    code, out = _hook(kb_repo)
    assert code == 1 and "only after two failed attempts" in out and "SCOUT_LINT_OVERRIDE" in out
    assert _hook(kb_repo, {"SCOUT_LINT_OVERRIDE": "moving the history out next"})[0] == 0
    assert (kb_repo.root / ".scout-logs" / "lint-overrides.log").is_file()


# ----- page-bullet ------------------------------------------------------------------


LONG = "- " + "The rollout reached the second region and the pilot customers signed off. " * 5  # ~375 chars
SHORT = "- The rollout reached the second region; detail in [[widgets-rollout]]."


def bullets(kb_repo) -> list:
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    return [f for f in result.findings if f.check == "page-bullet"]


def test_page_bullet_is_a_page_check_that_reports_until_named() -> None:
    assert "page-bullet" in REPORT_UNTIL_NAMED


def test_an_added_long_bullet_in_where_it_stands_is_flagged(kb_repo) -> None:
    kb_repo.stage(FRONT, page([SHORT]))
    kb_repo.commit()
    text = page([SHORT, LONG])
    kb_repo.stage(FRONT, text)
    [f] = bullets(kb_repo)
    assert f.blocking and f.path == FRONT
    assert f.line == text.split("\n").index(LONG) + 1
    assert "knowledge/" in f.message and "first" in f.message
    assert "rollout" not in f.message  # its length, never its text


def test_a_wrapped_bullet_counts_its_continuation_lines(kb_repo) -> None:
    kb_repo.stage(FRONT, page([SHORT]))
    kb_repo.commit()
    wrap = "  and the pilot customers signed off on it." * 2
    wrapped = [SHORT, "- The rollout reached the second region,", wrap, wrap, wrap, wrap]
    kb_repo.stage(FRONT, page(wrapped))
    [f] = bullets(kb_repo)
    assert f.line == page(wrapped).split("\n").index("- The rollout reached the second region,") + 1


def test_a_timestamped_paragraph_in_where_it_stands_is_flagged(kb_repo) -> None:
    kb_repo.stage(FRONT, page([SHORT]))
    kb_repo.commit()
    para = "On 2026-11-02 the consolidation run noted that " + "the pilot widened again and pricing moved. " * 7
    kb_repo.stage(FRONT, page([SHORT, "", para]))
    assert len(bullets(kb_repo)) == 1


def test_long_bullets_outside_where_it_stands_are_not_this_checks_business(kb_repo) -> None:
    kb_repo.stage(FRONT, page([SHORT]))
    kb_repo.commit()
    kb_repo.stage(FRONT, page([SHORT], extra=LONG + "\n"))  # under History
    kb_repo.stage(NOTE, "## Where it stands\n" + LONG + "\n")
    assert bullets(kb_repo) == []


def test_an_existing_long_bullet_is_debt_an_edit_elsewhere_doesnt_raise(kb_repo) -> None:
    kb_repo.stage(FRONT, page([LONG]))
    kb_repo.commit()
    kb_repo.stage(FRONT, page([LONG, SHORT]))
    assert bullets(kb_repo) == []


def test_an_edit_may_shorten_a_long_bullet_but_not_lengthen_it(kb_repo) -> None:
    kb_repo.stage(FRONT, page([LONG]))
    kb_repo.commit()
    kb_repo.stage(FRONT, page([LONG[:-20]]))
    assert bullets(kb_repo) == []
    kb_repo.stage(FRONT, page([LONG + " And then more happened."]))
    assert len(bullets(kb_repo)) == 1


def test_splitting_one_long_bullet_into_two_long_ones_is_flagged(kb_repo) -> None:
    kb_repo.stage(FRONT, page([LONG]))
    kb_repo.commit()
    kb_repo.stage(FRONT, page([LONG[:-10], LONG[:-20]]))
    assert len(bullets(kb_repo)) == 1


def test_a_new_front_page_with_a_long_bullet_is_flagged(kb_repo) -> None:
    kb_repo.stage(FRONT, page([LONG]))
    assert len(bullets(kb_repo)) == 1


def test_the_limit_and_sections_are_settings(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_structure:\n  max_bullet_chars: 1000\n")
    kb_repo.stage(FRONT, page([LONG]))
    assert bullets(kb_repo) == []
    kb_repo.write("scout-config.yaml", "kb_structure:\n  max_bullet_chars: 100\n  bullet_sections: [History]\n")
    kb_repo.stage(FRONT, page([LONG], extra=SHORT + "\n"))
    assert bullets(kb_repo) == []
    kb_repo.stage(FRONT, page([SHORT], extra=LONG + "\n"))
    assert len(bullets(kb_repo)) == 1


def test_a_private_front_page_gets_only_line_numbers_and_counts(kb_repo) -> None:
    text = page([LONG]).replace("type: project\n", "type: project\nvisibility: private\n")
    kb_repo.stage(FRONT, text)
    [f] = bullets(kb_repo)
    assert "Where it stands" not in f.message and f.line


def test_page_bullet_reports_by_default_and_blocks_under_the_lock(kb_repo) -> None:
    kb_repo.stage(FRONT, page([SHORT]))
    kb_repo.commit()
    kb_repo.stage(FRONT, page([SHORT, LONG]))
    code, out = _hook(kb_repo)
    assert code == 0 and "[page-bullet]" in out
    kb_repo.write("scout-config.yaml", FRONT_PAGE_LOCK)
    code, out = _hook(kb_repo)
    assert code == 1 and "[page-bullet]" in out and "Commit blocked" in out
