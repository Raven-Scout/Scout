"""Tests for `scoutctl kb structure --next`: what the nightly KB pass can't pick, and what it resumes."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from scout import cli
from scout.kb.structure import load_structure_config, missing_sections, next_target, structure_rows

runner = CliRunner()

SECTIONS = ["Where it stands", "What's next", "Key decisions", "Open questions", "People", "Go deeper", "History"]
P = "knowledge-base/projects"
PRIVATE_NOTE = "---\nvisibility: private\n---\nnot movable\n"


def page(
    name: str, *, priority: str = "🟢", as_of: str | None = "2026-10-01", frontmatter: str = "", extra: str = ""
) -> str:
    fm = f'name: {name}\ntype: project\npriority: "{priority} P2"\n'
    fm += f"as_of: {as_of}\n" if as_of else ""
    body = "".join(f"## {s}\nNone right now.\n\n" for s in SECTIONS)
    return f"---\n{fm}{frontmatter}---\n# {name.title()}\n\n{body}{extra}"


def rows_by_name(root: Path) -> dict[str, dict]:
    return {r["project"]: r for r in structure_rows(root)}


def picked(root: Path) -> str | None:
    target = next_target(structure_rows(root))
    return target["project"] if target else None


def test_next_skips_pages_the_pass_must_not_copy(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", f"privacy:\n  shared_files_add: ['{P}/gadgets/gadgets.md']\n")
    kb_repo.write(
        f"{P}/widgets/widgets.md", page("widgets", priority="🔴", as_of=None, frontmatter="visibility: private\n")
    )
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", priority="🔴", as_of=None))
    kb_repo.write(f"{P}/sprockets/sprockets.md", page("sprockets", as_of=None))
    rows = rows_by_name(kb_repo.root)
    assert not rows["widgets"]["rebuild_target"] and not rows["widgets"]["conforms"]
    assert not rows["gadgets"]["rebuild_target"] and rows["sprockets"]["rebuild_target"]
    result = runner.invoke(cli.app, ["kb", "structure", "--next", "--repo", str(kb_repo.root)])
    assert result.output == "sprockets\n"
    table = runner.invoke(cli.app, ["kb", "structure", "--repo", str(kb_repo.root)]).output
    assert sum("[--next skips]" in line for line in table.splitlines()) == 2


def test_next_skips_a_local_only_page(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", f"privacy:\n  local_only_add: ['{P}/widgets/**']\n")
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🔴", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    assert picked(kb_repo.root) == "gadgets"


def test_next_is_empty_when_only_unfixable_pages_are_left(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None, frontmatter="visibility: private\n"))
    result = runner.invoke(cli.app, ["kb", "structure", "--next", "--repo", str(kb_repo.root)])
    assert result.output == "\n"


def test_a_loose_private_file_alone_does_not_pin_the_target(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🔴"))
    kb_repo.write(f"{P}/widgets/secret.md", PRIVATE_NOTE)
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    rows = rows_by_name(kb_repo.root)
    assert not rows["widgets"]["conforms"] and not rows["widgets"]["rebuild_target"]
    assert picked(kb_repo.root) == "gadgets"


def test_a_loose_private_file_with_other_work_left_stays_a_target(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    kb_repo.write(f"{P}/widgets/secret.md", PRIVATE_NOTE)
    kb_repo.write(f"{P}/widgets/notes.md", "movable\n")
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    kb_repo.write(f"{P}/gadgets/secret.md", PRIVATE_NOTE)
    rows = rows_by_name(kb_repo.root)
    assert rows["widgets"]["rebuild_target"] and rows["gadgets"]["rebuild_target"]


def test_next_resumes_a_half_rebuilt_page_first(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🔴", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", extra="x" * 17000))
    kb_repo.write(f"{P}/gadgets/archive/gadgets-2026-10-30-pre-restructure.md", "the old page\n")
    assert rows_by_name(kb_repo.root)["gadgets"]["resume"]
    assert picked(kb_repo.root) == "gadgets"


def test_a_finished_rebuild_that_lost_its_as_of_does_not_jump_the_queue(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🔴", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    kb_repo.write(f"{P}/gadgets/archive/gadgets-2026-10-30-pre-restructure.md", "the old page\n")
    assert not rows_by_name(kb_repo.root)["gadgets"]["resume"]
    assert picked(kb_repo.root) == "widgets"


def test_the_defaults_match_the_seeded_standard() -> None:
    template = Path(__file__).parents[3] / "templates" / "docs" / "kb-page-standard.md"
    text = template.read_text(encoding="utf-8")
    cfg = load_structure_config()
    assert "**Target: under 8 KB. Hard budget: 15 KB.**" in text
    assert (cfg.page_target_bytes, cfg.page_budget_bytes) == (8 * 1024, 15 * 1024)
    assert missing_sections(text, cfg.required_sections) == []
    for name in cfg.allowed_dirs:
        assert f"  {name}/ " in text
