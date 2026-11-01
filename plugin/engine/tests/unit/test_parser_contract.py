"""Cross-language parser contract — Python side.

Asserts scout-plugin's parser reproduces parser-corpus.json exactly. The same
corpus will be asserted by scout-app's Swift ParserContractTests; the two corpus
copies are kept byte-identical by a checksum guard (added in M3.4) so they
cannot drift. See scout-app issue #10.

Parser API note (discovered during M3.2):
  scout-plugin has TWO parsers, and the four contract fields are split across
  them — neither produces all four:

    * scout.action_items.parser.parse_file -> list[ActionItem]
        owns `short_prefix` (the canonical stable-ID surface form; also used by
        backfill/diff/mark-done --by-id). Its `title` strips bold markers and
        is NOT the contract `subject`.

    * scout.action_items.render.parse(md) -> (title, preamble, [Section])
        with Section.tasks: list[Task(done, subject, body, raw)], plus the
        module helper render._plain_subject(subject). This is the rendering /
        --subject-matching path: `subject` retains markdown, `body` is the
        token-aware split remainder, and `_plain_subject` mirrors
        mark_done.py / snooze.py `_strip_markdown_tokens` (the exact form the
        `--subject` substring matcher compares against).

  The contract corpus encodes the UNIFIED intended contract, which matches
  scout-app's reference parser (Scout/ActionItems/ActionItemsParser.swift):
  extract the [#XXXX] prefix FIRST, then split subject/body on what remains.

  Historical note: render.parse() originally did not extract the [#XXXX]
  prefix, leaving it glued to `subject`/`_plain_subject` (tracked as #114,
  strict-xfail'd here until fixed). Fixed by stripping the leading prefix
  before the subject/body split; the full corpus now passes with no xfails.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from scout.action_items import render
from scout.action_items.parser import parse_file
from scout.action_items.plan_marks import read_plan_marks

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "parser-corpus.json"


def _load() -> list[dict]:
    return json.loads(CORPUS.read_text(encoding="utf-8"))["entries"]


_ENTRIES = _load()


def _write(tmp_path: Path, entry: dict) -> Path:
    text = "# T\n\n## 🔴 Urgent\n\n" + "\n".join([entry["line"], *entry.get("sub_lines", [])]) + "\n"
    f = tmp_path / "action-items-2026-06-04.md"
    f.write_text(text, encoding="utf-8")
    return f


def _top_task(f: Path) -> render.Task:
    """The entry's own task. A nested task in `sub_lines` comes after it."""
    _, _, sections = render.parse(f)
    tasks = [t for s in sections for t in s.tasks]
    assert tasks, "expected a render Task"
    return tasks[0]


@pytest.mark.parametrize("entry", _ENTRIES, ids=lambda e: e["name"])
def test_short_prefix(entry: dict, tmp_path: Path) -> None:
    """short_prefix comes from parser.parse_file (the stable-ID parser)."""
    f = _write(tmp_path, entry)
    items = parse_file(f)
    assert len(items) == 1, f"{entry['name']}: expected one item, got {len(items)}"
    assert (items[0].short_prefix or None) == entry["expected"]["short_prefix"], f"{entry['name']}: short_prefix"


@pytest.mark.parametrize("entry", _ENTRIES, ids=lambda e: e["name"])
def test_body(entry: dict, tmp_path: Path) -> None:
    """body comes from render.parse (token-aware subject/body split)."""
    task = _top_task(_write(tmp_path, entry))
    assert task.body == entry["expected"]["body"], f"{entry['name']}: body"


@pytest.mark.parametrize("entry", _ENTRIES, ids=lambda e: e["name"])
def test_subject(entry: dict, tmp_path: Path) -> None:
    """subject (markdown-retaining title) comes from render.parse."""
    task = _top_task(_write(tmp_path, entry))
    assert task.subject == entry["expected"]["subject"], f"{entry['name']}: subject"


@pytest.mark.parametrize("entry", _ENTRIES, ids=lambda e: e["name"])
def test_plain_subject(entry: dict, tmp_path: Path) -> None:
    """plain_subject = render._plain_subject(subject); the --subject match form."""
    task = _top_task(_write(tmp_path, entry))
    assert render._plain_subject(task.subject) == entry["expected"]["plain_subject"], f"{entry['name']}: plain_subject"


_FIELD_ENTRIES = [e for e in _ENTRIES if "fields" in e["expected"]]


@pytest.mark.parametrize("entry", _FIELD_ENTRIES, ids=lambda e: e["name"])
def test_fields(entry: dict, tmp_path: Path) -> None:
    """Board fields come from parser.parse_file, bound by the owner rule."""
    [item] = parse_file(_write(tmp_path, entry))
    fields = asdict(item.fields)
    fields["depends"] = list(fields["depends"])
    assert fields == entry["expected"]["fields"], f"{entry['name']}: fields"


_COMMENT_ENTRIES = [e for e in _ENTRIES if "comment_authors" in e["expected"]]


@pytest.mark.parametrize("entry", _COMMENT_ENTRIES, ids=lambda e: e["name"])
def test_comment_authors(entry: dict, tmp_path: Path) -> None:
    """Field and plan marker lines never become comments; real comments still do."""
    task = _top_task(_write(tmp_path, entry))
    assert [c.author for c in task.comments] == entry["expected"]["comment_authors"], f"{entry['name']}: comments"


_PLAN_ENTRIES = [e for e in _ENTRIES if "plan" in e["expected"]]


@pytest.mark.parametrize("entry", _PLAN_ENTRIES, ids=lambda e: e["name"])
def test_plan(entry: dict, tmp_path: Path) -> None:
    """The plan comes from plan_marks.read_plan_marks; the task line is line 5 of the file."""
    marks = read_plan_marks(_write(tmp_path, entry), task_line_number=5)
    assert marks.to_json_dict() == entry["expected"]["plan"], f"{entry['name']}: plan"
