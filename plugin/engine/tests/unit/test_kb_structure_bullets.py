"""`kb structure` holds "Where it stands" to the standard: at most `max_bullets` entries of at most
`max_bullet_chars` each. Before, the check passed a page however many or long its bullets were."""

from __future__ import annotations

import pytest

from scout.errors import KBError
from scout.kb.lint import lint_staged
from scout.kb.lint_config import load_lint_config
from scout.kb.structure import check_bullets, load_structure_config, structure_rows

P = "knowledge-base/projects"
FRONT = f"{P}/widgets/widgets.md"
SECTIONS = ["Where it stands", "What's next", "Key decisions", "Open questions", "People", "Go deeper", "History"]
SHORT = "- The rollout reached the second region; detail in [[widgets-rollout]]."
LONG = "- " + "The rollout reached the second region and the pilot customers signed off. " * 5  # ~375 chars


def page(where: list[str], *, private: bool = False, as_of: str = "2026-10-01") -> str:
    parts = []
    for s in SECTIONS:
        body = "\n".join(where) if s == "Where it stands" else "None right now."
        parts.append(f"## {s}\n{body}\n\n")
    vis = "visibility: private\n" if private else ""
    return (
        f'---\nname: widgets\ntype: project\npriority: "🟡 P1"\nas_of: {as_of}\n{vis}---\n# Widgets\n\n{"".join(parts)}'
    )


def row(kb_repo) -> dict:
    return {r["project"]: r for r in structure_rows(kb_repo.root)}["widgets"]


def test_the_defaults_are_the_standards_six_bullets_of_300_chars() -> None:
    cfg = load_structure_config()
    assert (cfg.max_bullets, cfg.max_bullet_chars, cfg.bullet_sections) == (6, 300, ("Where it stands",))


def test_six_short_bullets_conform(kb_repo) -> None:
    kb_repo.write(FRONT, page([SHORT] * 6))
    r = row(kb_repo)
    assert (
        r["conforms"] and r["bullets"] == {"Where it stands": 6} and r["bullets_over"] == 0 and r["long_bullets"] == 0
    )


def test_too_many_bullets_fail_the_count(kb_repo) -> None:
    kb_repo.write(FRONT, page([SHORT] * 9))
    r = row(kb_repo)
    assert not r["conforms"]
    assert r["problems"] == ["9 bullets in Where it stands (limit 6)"]
    assert r["bullets_over"] == 9 and r["rebuild_target"]


def test_five_of_six_bullets_over_the_length_fail(kb_repo) -> None:
    kb_repo.write(FRONT, page([LONG] * 5 + [SHORT]))
    r = row(kb_repo)
    assert not r["conforms"] and r["long_bullets"] == 5
    assert r["problems"] == ["5 bullet(s) over 300 chars"]


def test_a_paragraph_counts_as_an_entry(kb_repo) -> None:
    kb_repo.write(FRONT, page([SHORT] * 6 + ["", "A closing paragraph that is a seventh entry."]))
    assert row(kb_repo)["problems"] == ["7 bullets in Where it stands (limit 6)"]


def test_a_page_failing_only_on_bullets_waits_behind_a_page_a_reader_cant_follow(kb_repo) -> None:
    kb_repo.write(FRONT, page([SHORT] * 9))
    kb_repo.write(
        f"{P}/gadgets/gadgets.md",
        page([SHORT]).replace("name: widgets", "name: gadgets").replace("as_of: 2026-10-01\n", ""),
    )
    assert [r["project"] for r in structure_rows(kb_repo.root)][:2] == ["gadgets", "widgets"]


def test_a_private_page_shows_counts_but_no_section_name(kb_repo) -> None:
    kb_repo.write(FRONT, page([LONG] * 8, private=True))
    r = row(kb_repo)
    assert r["problems"] == ["8 bullets (limit 6)", "8 bullet(s) over 300 chars"]
    assert r["bullets"] is None


def test_the_limits_are_settings(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_structure:\n  max_bullets: 10\n  max_bullet_chars: 1000\n")
    kb_repo.write(FRONT, page([LONG] * 9))
    assert row(kb_repo)["conforms"]


@pytest.mark.parametrize("value", [0, -1])
def test_a_bullet_count_that_isnt_positive_is_an_error(kb_repo, value: int) -> None:
    kb_repo.write("scout-config.yaml", f"kb_structure:\n  max_bullets: {value}\n")
    with pytest.raises(KBError, match="max_bullets"):
        load_structure_config(kb_repo.root)


def test_check_bullets_counts_per_section() -> None:
    cfg = load_structure_config()
    text = page([SHORT, LONG, SHORT])
    result = check_bullets(text, cfg)
    assert result.counts == {"Where it stands": 3} and result.long == 1 and result.over(2) == {"Where it stands": 3}


# ----- kb lint: a commit may not grow the count past the limit -----------------------------


def bullets(kb_repo) -> list:
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    return [f for f in result.findings if f.check == "page-bullet"]


def test_lint_flags_a_commit_that_takes_the_section_past_six(kb_repo) -> None:
    kb_repo.stage(FRONT, page([SHORT] * 6))
    kb_repo.commit()
    kb_repo.stage(FRONT, page([SHORT] * 7))
    [f] = bullets(kb_repo)
    assert f.blocking and '"Where it stands" now has 7 entries (limit 6)' in f.message


def test_lint_flags_a_new_front_page_with_too_many_bullets(kb_repo) -> None:
    kb_repo.stage(FRONT, page([SHORT] * 8))
    assert len(bullets(kb_repo)) == 1


def test_lint_lets_a_commit_shrink_an_over_long_section(kb_repo) -> None:
    kb_repo.stage(FRONT, page([SHORT] * 12))
    kb_repo.commit()
    kb_repo.stage(FRONT, page([SHORT] * 9))
    assert bullets(kb_repo) == []


def test_lint_names_no_section_on_a_private_page(kb_repo) -> None:
    kb_repo.stage(FRONT, page([SHORT] * 8, private=True))
    [f] = bullets(kb_repo)
    assert "Where it stands" not in f.message and "a section now has 8 entries" in f.message
