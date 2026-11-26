"""Lab lint on damaged files the main lint tests don't reach: experiment notes that disagree with where they
live, timelines with broken frontmatter or fenced lines, front pages without frontmatter."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scout.lab import lint, paths

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
SLUG = "summary-drift"
LAB = "knowledge-base/lab"
E2 = f"{LAB}/{SLUG}/experiments/{SLUG}-E2-section-hash.md"
TIMELINE = f"{LAB}/{SLUG}/{SLUG}-timeline.md"
FRONT = f"{LAB}/{SLUG}/{SLUG}.md"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURES / SLUG, paths.thread_dir(tmp_path, SLUG))
    return tmp_path


def _messages(vault: Path, rel: str, text: str) -> list[str]:
    return [f.message for f in lint.lab_findings(vault, rel, text)]


def _text(vault: Path, rel: str) -> str:
    return (vault / rel).read_text(encoding="utf-8")


def test_files_outside_the_lab_and_a_vault_without_one(tmp_path: Path) -> None:
    assert lint.lab_findings(tmp_path, "knowledge-base/people/sam.md", "anything") == []
    assert lint.WorkingTree(tmp_path).files() == []


def test_an_experiment_note_that_disagrees_with_its_place(vault: Path) -> None:
    text = _text(vault, E2).replace("thread: summary-drift", "thread: other-thread", 1).replace("id: E2", "id: E7", 1)
    messages = _messages(vault, E2, text)
    assert "frontmatter thread is 'other-thread', but the note lives under 'summary-drift'" in messages
    assert "frontmatter id is 'E7', but the file name says E2" in messages


def test_an_experiment_note_without_frontmatter_reports_only_its_parse(vault: Path) -> None:
    text = _text(vault, E2)
    body = text.split("---\n", 2)[2]
    assert not any("is missing" in m for m in _messages(vault, E2, body))


def test_a_planned_time_without_a_not_before_is_reported_missing_only(vault: Path) -> None:
    text = "".join(line for line in _text(vault, E2).splitlines(True) if not line.startswith("not_before:"))
    messages = [m for m in _messages(vault, E2, text) if "not_before" in m]
    assert messages == ["frontmatter is missing 'not_before'"]  # no ordering check without it


def test_a_timeline_whose_frontmatter_isnt_yaml(vault: Path) -> None:
    text = "---\nvisibility: [unclosed\n---\n" + _text(vault, TIMELINE)
    assert "a timeline's frontmatter may hold only 'visibility: private' (or normal)" in _messages(
        vault, TIMELINE, text
    )


def test_a_fenced_heading_in_a_timeline_isnt_an_entry(vault: Path) -> None:
    text = _text(vault, TIMELINE) + "\n```\n### not an entry\n```\n"
    assert not any("not an entry" in m for m in _messages(vault, TIMELINE, text))


def test_a_front_page_without_frontmatter_has_no_counter_findings(vault: Path) -> None:
    text = _text(vault, FRONT).split("---\n", 2)[2]
    assert not any("next_q" in m or "next_e" in m for m in _messages(vault, FRONT, text))


def test_a_private_marker_that_isnt_yaml_isnt_private() -> None:
    assert lint._says_private("---\nvisibility: [unclosed\n---\nx\n") is False
    assert lint._says_private("no frontmatter\n") is False
    assert lint._says_private("---\nvisibility: Private\n---\nx\n") is True


def test_a_counter_with_nothing_to_count_and_no_key_is_fine(vault: Path) -> None:
    from scout.lab.parser import parse_front_page

    text = _text(vault, FRONT).replace("next_q: 8\n", "", 1)
    thread = parse_front_page(SLUG, text)
    assert lint._counter(FRONT, text, thread, "next_q", [], "Q") == []
