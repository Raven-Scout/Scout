"""`scoutctl kb edit`: the sanctioned rule-based edit a run uses in place of a python heredoc (follow-up to #824).

The output is counts and status only: no test below may find a note's text, a pattern or a heading in it."""

from __future__ import annotations

import json
import os
import time

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.kb import edit as kb_edit
from scout.kb.edit import OpFailed, apply_ops, check_ops, line_counts, load_ops
from scout.kb.read import UsageError

runner = CliRunner()
NOTE = "knowledge-base/projects/alpha/alpha.md"
CONTEXT = "action-items/context/PROJ-1234.md"
SECRET = "knowledge-base/personal/renewal.md"
BODY = (
    "---\n"
    "title: Alpha\n"
    "last_verified: 2026-11-01\n"
    "tags:\n"
    "- infra\n"
    "- costs\n"
    "status: active\n"
    "---\n"
    "# Alpha\n"
    "\n"
    "## Where it stands\n"
    "\n"
    "- Sam owns the rollout\n"
    "- budget is TBD\n"
    "\n"
    "## Log\n"
    "\n"
    "- 2026-11-01 kickoff\n"
    "\n"
    "```\n"
    "## Log\n"
    "```\n"
)


def kb(vault, *args: str, stdin: str | None = None, reader: str | None = None):
    env = {}
    if reader:  # a scheduled reader's vault is SCOUT_DATA_DIR, which must be a Scout vault
        (vault / "scout-config.yaml").write_text("user: {}\n", encoding="utf-8")
        env = {"SCOUT_READER": reader, "SCOUT_DATA_DIR": str(vault)}
    return runner.invoke(app, ["kb", "edit", *args, "--repo", str(vault)], input=stdin, env=env)


def run_ops(vault, path: str, ops, *extra: str, reader: str | None = None):
    return kb(vault, path, "--ops", "-", *extra, stdin=json.dumps(ops), reader=reader)


@pytest.fixture
def vault(kb_repo):
    kb_repo.write(NOTE, BODY)
    kb_repo.write(CONTEXT, "---\ntag: PROJ-1234\nupdated: 2026-11-01\n---\nStatus: waiting\n")
    kb_repo.write(SECRET, "---\nvisibility: private\n---\nrenew the permit\n")
    return kb_repo.root


def text(vault, rel: str = NOTE) -> str:
    return (vault / rel).read_text(encoding="utf-8")


# ----- replace and regex -----------------------------------------------------------------


def test_a_literal_replace_writes_and_prints_only_counts(vault) -> None:
    result = run_ops(vault, NOTE, [{"op": "replace", "old": "budget is TBD", "new": "budget is 40k"}])
    assert result.exit_code == 0, result.output
    assert "budget is 40k" in text(vault) and "budget is TBD" not in text(vault)
    assert result.stdout.splitlines() == [f"{NOTE}: 1 op applied, +1 -1 lines, written", "  1 replace: 1 match"]
    assert "budget" not in result.stdout


@pytest.mark.parametrize(("want", "code"), [(1, 1), (2, 0), ("all", 0), (3, 1)])
def test_count_is_the_number_of_matches_expected(vault, want, code: int) -> None:
    result = run_ops(vault, NOTE, [{"op": "replace", "old": "## Log", "new": "## History", "count": want}])
    assert result.exit_code == code
    if code:
        assert "nothing written" in result.stdout and text(vault) == BODY
    else:
        assert text(vault).count("## History") == 2


def test_no_match_fails_and_writes_nothing(vault) -> None:
    result = run_ops(vault, NOTE, [{"op": "replace", "old": "nowhere in the note", "new": "x"}])
    assert result.exit_code == 1 and text(vault) == BODY
    assert result.stdout.strip() == f"{NOTE}: nothing written: op 1 (replace): expected 1 match, found 0"


def test_all_needs_at_least_one_match(vault) -> None:
    result = run_ops(vault, NOTE, [{"op": "replace", "old": "zzz", "new": "y", "count": "all"}])
    assert result.exit_code == 1 and "expected 1 or more matches, found 0" in result.stdout


def test_regex_with_groups_and_flags(vault) -> None:
    ops = [{"op": "regex", "pattern": r"^- (\d{4})-11-01 KICKOFF$", "new": r"- \1-11-02 kickoff", "flags": "mi"}]
    result = run_ops(vault, NOTE, ops)
    assert result.exit_code == 0, result.output
    assert "- 2026-11-02 kickoff\n" in text(vault)
    assert "kickoff" not in result.stdout


def test_a_regex_template_that_names_a_missing_group_fails(vault) -> None:
    result = run_ops(vault, NOTE, [{"op": "regex", "pattern": "kickoff", "new": r"\2"}])
    assert result.exit_code == 1 and "template" in result.stdout and text(vault) == BODY


# ----- insert and append -----------------------------------------------------------------


def test_insert_at_the_end_of_a_section_keeps_its_blank_line(vault) -> None:
    result = run_ops(vault, NOTE, [{"op": "insert", "heading": "## Where it stands", "text": "- launch on 12-01"}])
    assert result.exit_code == 0, result.output
    assert "- budget is TBD\n- launch on 12-01\n\n## Log\n" in text(vault)
    assert "1 insert: under 1 heading" in result.stdout


def test_insert_at_the_start_of_a_section(vault) -> None:
    result = run_ops(
        vault, NOTE, [{"op": "insert", "heading": "## Where it stands", "text": "- first\n", "at": "start"}]
    )
    assert result.exit_code == 0
    assert "## Where it stands\n\n- first\n- Sam owns the rollout\n" in text(vault)


def test_a_heading_inside_a_code_fence_is_not_a_heading(vault) -> None:
    result = run_ops(vault, NOTE, [{"op": "insert", "heading": "## Log", "text": "- 2026-11-25 shipped"}])
    assert result.exit_code == 0, result.output  # one match, not two: the section runs past its code block
    assert text(vault).endswith("- 2026-11-01 kickoff\n\n```\n## Log\n```\n- 2026-11-25 shipped\n")


def test_the_last_section_runs_to_the_end_of_the_note(kb_repo) -> None:
    kb_repo.write(NOTE, "# A\n\n## Log\n- one\n\n### Detail\n- d\n")
    result = run_ops(kb_repo.root, NOTE, [{"op": "insert", "heading": "## Log", "text": "- two"}])
    assert result.exit_code == 0
    assert text(kb_repo.root) == "# A\n\n## Log\n- one\n\n### Detail\n- d\n- two\n"


def test_a_missing_newline_at_the_end_is_added_before_the_insert(kb_repo) -> None:
    kb_repo.write(NOTE, "## Log\n- one")
    assert run_ops(kb_repo.root, NOTE, [{"op": "insert", "heading": "## Log", "text": "- two"}]).exit_code == 0
    assert text(kb_repo.root) == "## Log\n- one\n- two\n"


def test_an_empty_section_gets_the_text_right_under_its_heading(kb_repo) -> None:
    kb_repo.write(NOTE, "## Log\n## Next\n")
    assert run_ops(kb_repo.root, NOTE, [{"op": "insert", "heading": "## Log", "text": "- one"}]).exit_code == 0
    assert text(kb_repo.root) == "## Log\n- one\n## Next\n"


def test_no_heading_fails_unless_create(vault) -> None:
    op = {"op": "insert", "heading": "## Decisions", "text": "- go"}
    assert run_ops(vault, NOTE, [op]).exit_code == 1 and text(vault) == BODY
    result = run_ops(vault, NOTE, [op | {"create": True}])
    assert result.exit_code == 0 and text(vault).endswith("```\n\n## Decisions\n\n- go\n")
    assert "heading created" in result.stdout


def test_create_on_an_empty_note(kb_repo) -> None:
    kb_repo.write(NOTE, "")
    op = {"op": "insert", "heading": "## Log", "text": "- one", "create": True}
    assert run_ops(kb_repo.root, NOTE, [op]).exit_code == 0
    assert text(kb_repo.root) == "## Log\n\n- one\n"


def test_two_matching_headings_fail(kb_repo) -> None:
    kb_repo.write(NOTE, "## Log\n- a\n## Log\n- b\n")
    result = run_ops(kb_repo.root, NOTE, [{"op": "insert", "heading": "## Log", "text": "- c"}])
    assert result.exit_code == 1 and "2 headings match" in result.stdout


def test_a_heading_in_the_frontmatter_is_not_one(kb_repo) -> None:
    kb_repo.write(NOTE, "---\ntitle: x\n---\n## Log\n")
    assert run_ops(kb_repo.root, NOTE, [{"op": "insert", "heading": "## Log", "text": "- a"}]).exit_code == 0


def test_append(kb_repo) -> None:
    kb_repo.write(NOTE, "# A\nlast")
    result = run_ops(kb_repo.root, NOTE, [{"op": "append", "text": "tail line"}])
    assert result.exit_code == 0 and text(kb_repo.root) == "# A\nlast\ntail line\n"


# ----- frontmatter -----------------------------------------------------------------------


def test_set_a_date_field_unquoted(vault) -> None:
    result = run_ops(vault, NOTE, [{"op": "frontmatter", "field": "last_verified", "value": "2026-11-25"}])
    assert result.exit_code == 0, result.output
    assert "last_verified: 2026-11-25\n" in text(vault) and "1 frontmatter: field set" in result.stdout


def test_set_a_block_list_replaces_all_its_lines(vault) -> None:
    result = run_ops(vault, NOTE, [{"op": "frontmatter", "field": "tags", "value": ["infra", "budget"]}])
    assert result.exit_code == 0, result.output
    assert "tags:\n- infra\n- budget\nstatus: active\n" in text(vault) and "- costs" not in text(vault)


def test_add_and_remove_a_field(vault) -> None:
    assert run_ops(vault, NOTE, [{"op": "frontmatter", "field": "owner", "value": "Priya"}]).exit_code == 0
    assert "status: active\nowner: Priya\n---\n" in text(vault)
    result = run_ops(vault, NOTE, [{"op": "frontmatter", "field": "owner", "remove": True}])
    assert result.exit_code == 0 and "owner" not in text(vault) and "field removed" in result.stdout
    again = run_ops(vault, NOTE, [{"op": "frontmatter", "field": "owner", "remove": True}])
    assert again.exit_code == 0 and "field absent" in again.stdout and "unchanged" in again.stdout


@pytest.mark.parametrize("value", [3, 2.5, True, None, "has: a colon", "line one\nline two", "2026-11-25 10:00"])
def test_values_read_back_as_given(kb_repo, value) -> None:
    kb_repo.write(NOTE, "---\ntitle: x\nn: 1\n---\nbody\n")
    result = run_ops(kb_repo.root, NOTE, [{"op": "frontmatter", "field": "n", "value": value}])
    assert result.exit_code == 0, result.output
    assert text(kb_repo.root).startswith("---\ntitle: x\n") and text(kb_repo.root).endswith("---\nbody\n")


def test_no_frontmatter_fails(kb_repo) -> None:
    kb_repo.write(NOTE, "# A\n")
    result = run_ops(kb_repo.root, NOTE, [{"op": "frontmatter", "field": "status", "value": "done"}])
    assert result.exit_code == 1 and "no frontmatter" in result.stdout


@pytest.mark.parametrize(
    "fm",
    [
        "status: a\nstatus: b\n",  # set on two lines
        "status: [unclosed\n",  # doesn't parse
        "- a\n- b\n",  # not a mapping
    ],
)
def test_an_odd_frontmatter_fails_closed(kb_repo, fm: str) -> None:
    kb_repo.write(NOTE, f"---\n{fm}---\nbody\n")
    result = run_ops(kb_repo.root, NOTE, [{"op": "frontmatter", "field": "status", "value": "done"}])
    assert result.exit_code == 1 and text(kb_repo.root) == f"---\n{fm}---\nbody\n"


def test_a_field_whose_lines_cant_be_told_apart_fails_closed(kb_repo) -> None:
    # `notes:`'s value runs on an unindented line the edit can't know is its own: the other fields must read back.
    original = "---\nnotes: >\n  folded\nother: 1\n---\n"
    kb_repo.write(NOTE, original)
    result = run_ops(kb_repo.root, NOTE, [{"op": "frontmatter", "field": "notes", "value": "x"}])
    assert result.exit_code == 0 and "other: 1" in text(kb_repo.root)
    with pytest.raises(OpFailed):  # `y"` is the value's own line, but unindented: the edited block can't parse
        apply_ops('---\na: "x\ny"\nb: 1\n---\n', [{"op": "frontmatter", "field": "a", "value": "z"}])
    edited = apply_ops("---\na: 1\n---\n", [{"op": "frontmatter", "field": "a", "value": "2026-13-45"}])
    assert edited.text == "---\na: '2026-13-45'\n---\n"  # not a date: quoted, so it reads back as text


def test_a_field_whose_next_line_is_another_key_fails_closed() -> None:
    # `-b: 2` is a key of its own, but starts like a list item: the edit would take it as `a`'s, and the
    # other fields wouldn't read back.
    with pytest.raises(OpFailed, match="read back"):
        apply_ops("---\na: 1\n-b: 2\n---\n", [{"op": "frontmatter", "field": "a", "value": 3}])


def test_a_frontmatter_block_of_comments_only() -> None:
    edited = apply_ops("---\n# c\n---\nbody\n", [{"op": "frontmatter", "field": "a", "value": 1}])
    assert edited.text == "---\n# c\na: 1\n---\nbody\n"


def test_a_pure_add_after_a_last_line_without_newline() -> None:
    assert apply_ops("a", [{"op": "patch", "diff": "@@ -1,0 +2 @@\n+b\n"}]).text == "a\nb\n"


def test_an_empty_frontmatter_block(kb_repo) -> None:
    kb_repo.write(NOTE, "---\n---\nbody\n")
    assert run_ops(kb_repo.root, NOTE, [{"op": "frontmatter", "field": "status", "value": "done"}]).exit_code == 0
    assert text(kb_repo.root) == "---\nstatus: done\n---\nbody\n"


# ----- patch -----------------------------------------------------------------------------

PATCH = """\
--- a/alpha.md
+++ b/alpha.md
@@ -13,2 +13,3 @@
 - Sam owns the rollout
-- budget is TBD
+- budget is 40k
+- launch on 12-01
"""


def test_a_unified_diff_applies(vault) -> None:
    result = run_ops(vault, NOTE, [{"op": "patch", "diff": PATCH}])
    assert result.exit_code == 0, result.output
    assert "- Sam owns the rollout\n- budget is 40k\n- launch on 12-01\n\n## Log" in text(vault)
    assert "1 patch: 1 hunk" in result.stdout and "+2 -1 lines" in result.stdout


def test_a_hunk_off_by_some_lines_applies_where_its_old_lines_are(vault) -> None:
    moved = PATCH.replace("@@ -13,2 +13,3 @@", "@@ -3,2 +3,3 @@")
    assert run_ops(vault, NOTE, [{"op": "patch", "diff": moved}]).exit_code == 0
    assert "budget is 40k" in text(vault)


def test_a_hunk_that_matches_nowhere_or_twice_fails(kb_repo) -> None:
    kb_repo.write(NOTE, "a\nx\nb\nx\n")
    twice = "@@ -9,1 +9,1 @@\n-x\n+y\n"
    result = run_ops(kb_repo.root, NOTE, [{"op": "patch", "diff": twice}])
    assert result.exit_code == 1 and "in 2 places" in result.stdout
    nowhere = "@@ -1,1 +1,1 @@\n-zzz\n+y\n"
    assert "anywhere" in run_ops(kb_repo.root, NOTE, [{"op": "patch", "diff": nowhere}]).stdout


def test_several_hunks_and_a_pure_add(kb_repo) -> None:
    kb_repo.write(NOTE, "one\ntwo\nthree\nfour\nfive\n")
    diff = "@@ -1,1 +1,1 @@\n-one\n+ONE\n@@ -3,0 +4,2 @@\n+3a\n+3b\n@@ -5 +7 @@\n-five\n+FIVE\n"
    result = run_ops(kb_repo.root, NOTE, [{"op": "patch", "diff": diff}])
    assert result.exit_code == 0, result.output
    assert text(kb_repo.root) == "ONE\ntwo\nthree\n3a\n3b\nfour\nFIVE\n"


def test_no_newline_at_end_of_file_markers(kb_repo) -> None:
    kb_repo.write(NOTE, "a\nb")
    diff = "@@ -2 +2,2 @@\n-b\n\\ No newline at end of file\n+b\n+c\n\\ No newline at end of file\n"
    assert run_ops(kb_repo.root, NOTE, [{"op": "patch", "diff": diff}]).exit_code == 0
    assert text(kb_repo.root) == "a\nb\nc"
    kb_repo.write(NOTE, "a\nb")
    keep = "@@ -2 +2 @@\n-b\n+B\n"
    assert run_ops(kb_repo.root, NOTE, [{"op": "patch", "diff": keep}]).exit_code == 0
    assert text(kb_repo.root) == "a\nB"


def test_an_empty_context_line_without_its_space(kb_repo) -> None:
    kb_repo.write(NOTE, "a\n\nb\n")
    assert run_ops(kb_repo.root, NOTE, [{"op": "patch", "diff": "@@ -1,3 +1,3 @@\n a\n\n-b\n+c\n"}]).exit_code == 0
    assert text(kb_repo.root) == "a\n\nc\n"


@pytest.mark.parametrize(
    ("diff", "why"),
    [
        ("+++ b/a\n@@ -1 +1 @@\n-a\n+b\n+++ b/c\n@@ -1 +1 @@\n-a\n+b\n", "more than one file"),
        ("@@ -1,2 +1,1 @@\n-a\n+b\n", "line counts"),
        ("no hunks here\n", "no hunks"),
        ("@@ -1 +1 @@\n-a\n+b\nstray\n", "isn't part of a hunk"),
        ("@@ -1,2 +1,2 @@\n-a\n?b\n+b\n", "isn't a hunk line"),
        ("@@ -5,0 +6,1 @@\n+x\n", "past the end"),
    ],
)
def test_a_bad_diff_fails(diff: str, why: str) -> None:
    with pytest.raises(OpFailed, match=why):
        apply_ops("a\n", [{"op": "patch", "diff": diff}])


# ----- first-only, and an anchor line ----------------------------------------------------


def test_first_replaces_only_the_first_match(vault) -> None:
    result = run_ops(vault, NOTE, [{"op": "replace", "old": "## Log", "new": "## History", "first": True}])
    assert result.exit_code == 0, result.output
    assert text(vault).count("## History") == 1 and text(vault).count("## Log") == 1
    assert "1 replace: 2 matches, first replaced" in result.stdout


def test_first_with_a_count_still_checks_the_note(vault) -> None:
    op = {"op": "replace", "old": "## Log", "new": "## History", "first": True, "count": 1}
    assert run_ops(vault, NOTE, [op]).exit_code == 1 and text(vault) == BODY
    assert run_ops(vault, NOTE, [op | {"old": "absent"}]).exit_code == 1


def test_regex_first(kb_repo) -> None:
    kb_repo.write(NOTE, "a1 a2 a3\n")
    result = run_ops(kb_repo.root, NOTE, [{"op": "regex", "pattern": r"a(\d)", "new": r"b\1", "first": True}])
    assert result.exit_code == 0 and text(kb_repo.root) == "b1 a2 a3\n"


def test_insert_after_and_before_an_anchor_line(vault) -> None:
    ops = [
        {"op": "insert", "after": "Sam owns", "text": "- Priya reviews"},
        {"op": "insert", "before": "# Alpha", "text": "<!-- top -->"},
    ]
    result = run_ops(vault, NOTE, ops)
    assert result.exit_code == 0, result.output
    assert "- Sam owns the rollout\n- Priya reviews\n- budget" in text(vault)
    assert "---\n<!-- top -->\n# Alpha\n" in text(vault)
    assert "1 insert: after 1 line" in result.stdout and "2 insert: before 1 line" in result.stdout
    assert "Sam" not in result.stdout


def test_a_multi_line_anchor(kb_repo) -> None:
    kb_repo.write(NOTE, "a\nb\nc\nb\n")
    assert run_ops(kb_repo.root, NOTE, [{"op": "insert", "after": "a\nb", "text": "x"}]).exit_code == 0
    assert text(kb_repo.root) == "a\nb\nx\nc\nb\n"
    kb_repo.write(NOTE, "a\nb\nc\n")
    assert run_ops(kb_repo.root, NOTE, [{"op": "insert", "before": "b\nc\n", "text": "x"}]).exit_code == 0
    assert text(kb_repo.root) == "a\nx\nb\nc\n"


def test_an_anchor_after_a_last_line_without_newline(kb_repo) -> None:
    kb_repo.write(NOTE, "a\nend")
    assert run_ops(kb_repo.root, NOTE, [{"op": "insert", "after": "end", "text": "x"}]).exit_code == 0
    assert text(kb_repo.root) == "a\nend\nx\n"


@pytest.mark.parametrize("anchor", ["Sam", "kickoff\n\n", "nowhere"])
def test_an_anchor_found_twice_or_not_at_all_fails(kb_repo, anchor: str) -> None:
    kb_repo.write(NOTE, "Sam and Sam\n- kickoff\n\nx\n- kickoff\n\n")
    result = run_ops(kb_repo.root, NOTE, [{"op": "insert", "after": anchor, "text": "x"}])
    assert result.exit_code == 1 and "expected the anchor once" in result.stdout


# ----- several notes in one call ---------------------------------------------------------


def test_several_notes_in_one_call(vault) -> None:
    doc = {
        NOTE: [{"op": "replace", "old": "budget is TBD", "new": "budget is 40k"}],
        CONTEXT: [{"op": "frontmatter", "field": "updated", "value": "2026-11-25"}],
    }
    result = kb(vault, "--ops", "-", stdin=json.dumps(doc))
    assert result.exit_code == 0, result.output
    assert "budget is 40k" in text(vault) and "updated: 2026-11-25" in text(vault, CONTEXT)
    assert f"{NOTE}: 1 op applied, +1 -1 lines, written" in result.stdout
    assert f"{CONTEXT}: 1 op applied, +1 -1 lines, written" in result.stdout


def test_one_note_failing_writes_neither(vault) -> None:
    doc = {
        NOTE: [{"op": "replace", "old": "budget is TBD", "new": "x"}],
        CONTEXT: [{"op": "replace", "old": "absent", "new": "y"}],
    }
    result = kb(vault, "--ops", "-", "--json", stdin=json.dumps(doc))
    assert result.exit_code == 1 and text(vault) == BODY
    rows = json.loads(result.stdout)["files"]
    assert rows[0]["written"] is False and "failed" in rows[1]
    plain = kb(vault, "--ops", "-", stdin=json.dumps(doc)).stdout
    assert "not written: another note's edit didn't apply" in plain


def test_one_note_refused_writes_neither(vault) -> None:
    os.link(vault / CONTEXT, vault / "elsewhere.md")
    doc = {NOTE: [{"op": "append", "text": "x"}], CONTEXT: [{"op": "append", "text": "y"}]}
    result = kb(vault, "--ops", "-", stdin=json.dumps(doc))
    assert result.exit_code == 3 and text(vault) == BODY


def test_one_note_changed_meanwhile_writes_neither(vault, monkeypatch) -> None:
    real = kb_edit.apply_ops
    calls = []

    def racing(src, ops):
        calls.append(1)
        if len(calls) == 2:
            (vault / CONTEXT).write_text(src + "another run\n", encoding="utf-8")
        return real(src, ops)

    monkeypatch.setattr(kb_edit, "apply_ops", racing)
    doc = {NOTE: [{"op": "append", "text": "x"}], CONTEXT: [{"op": "append", "text": "y"}]}
    result = kb(vault, "--ops", "-", stdin=json.dumps(doc))
    assert result.exit_code == 1 and text(vault) == BODY and "changed meanwhile" in result.stdout


def test_the_same_note_twice_is_a_usage_error(vault) -> None:
    doc = {NOTE: [{"op": "append", "text": "x"}], "./" + NOTE: [{"op": "append", "text": "y"}]}
    assert kb(vault, "--ops", "-", stdin=json.dumps(doc)).exit_code == 2 and text(vault) == BODY


@pytest.mark.parametrize(
    ("path", "doc", "why"),
    [
        (NOTE, {NOTE: [{"op": "append", "text": "x"}]}, "with a <path>"),
        (None, [{"op": "append", "text": "x"}], "without a <path>"),
        (None, {"op": "append", "text": "x"}, "without a <path>"),
        (None, {}, "without a <path>"),
        (None, {f"knowledge-base/n{i}.md": [{"op": "append", "text": "x"}] for i in range(11)}, "notes per call"),
        (
            None,
            {"a.md": [{"op": "append", "text": "x"}] * 60, "b.md": [{"op": "append", "text": "x"}] * 60},
            "operations",
        ),
    ],
)
def test_the_document_matches_the_path_argument(path, doc, why: str) -> None:
    with pytest.raises(UsageError, match=why):
        load_ops(json.dumps(doc), path)


# ----- several ops, and all-or-nothing ---------------------------------------------------


def test_ops_apply_in_order_to_what_the_one_before_left(vault) -> None:
    ops = [
        {"op": "replace", "old": "budget is TBD", "new": "budget is 40k"},
        {"op": "replace", "old": "budget is 40k", "new": "budget is 45k"},
        {"op": "frontmatter", "field": "last_verified", "value": "2026-11-25"},
        {"op": "insert", "heading": "## Log", "text": "- 2026-11-25 budget set"},
    ]
    result = run_ops(vault, NOTE, ops)
    assert result.exit_code == 0, result.output
    assert "budget is 45k" in text(vault) and "last_verified: 2026-11-25" in text(vault)
    assert result.stdout.splitlines()[0] == f"{NOTE}: 4 ops applied, +3 -2 lines, written"


def test_a_failing_op_writes_none_of_them(vault) -> None:
    ops = [{"op": "replace", "old": "budget is TBD", "new": "x"}, {"op": "replace", "old": "absent", "new": "y"}]
    result = run_ops(vault, NOTE, ops, "--json")
    assert result.exit_code == 1 and text(vault) == BODY
    data = json.loads(result.stdout)
    assert data["written"] is False and data["files"][0]["failed"] == {
        "index": 2,
        "op": "replace",
        "reason": "expected 1 match, found 0",
    }


def test_check_reports_but_writes_nothing(vault) -> None:
    result = run_ops(vault, NOTE, [{"op": "replace", "old": "budget is TBD", "new": "x"}], "--check", "--json")
    assert result.exit_code == 0 and text(vault) == BODY
    data = json.loads(result.stdout)
    row = data["files"][0]
    assert (data["written"], data["check"]) == (False, True)
    assert (row["written"], row["changed"], row["added"], row["removed"]) == (False, True, 1, 1)
    assert row["label"]["path"] == NOTE and row["ops"] == [
        {"index": 1, "op": "replace", "status": "1 match", "matches": 1}
    ]
    assert "would write" in run_ops(vault, NOTE, [{"op": "replace", "old": "TBD", "new": "x"}], "--check").stdout


def test_a_note_that_changes_meanwhile_is_not_overwritten(vault, monkeypatch) -> None:
    real = kb_edit.apply_ops

    def racing(src, ops):
        (vault / NOTE).write_text(src + "another run's line\n", encoding="utf-8")
        return real(src, ops)

    monkeypatch.setattr(kb_edit, "apply_ops", racing)
    result = run_ops(vault, NOTE, [{"op": "replace", "old": "budget is TBD", "new": "x"}])
    assert result.exit_code == 1 and "changed meanwhile" in result.stdout
    assert text(vault).endswith("another run's line\n") and "budget is TBD" in text(vault)


def test_one_op_object(vault) -> None:
    result = kb(vault, NOTE, "--ops", "-", stdin=json.dumps({"op": "replace", "old": "budget is TBD", "new": "x"}))
    assert result.exit_code == 0 and "budget is TBD" not in text(vault)


def test_ops_come_only_from_stdin(vault, tmp_path) -> None:
    """A file would be read whole and its JSON errors echoed: a way to probe any file's text (#826's review)."""
    secret = tmp_path / "secret.txt"
    secret.write_text("the private plan\n", encoding="utf-8")
    result = kb(vault, NOTE, "--ops", str(secret))
    assert result.exit_code == 2 and "stdin" in result.output and "private plan" not in result.output
    assert text(vault) == BODY


def test_stdin_is_read_only_up_to_the_limit(vault, monkeypatch) -> None:
    monkeypatch.setattr(kb_edit, "MAX_OPS_BYTES", 64)
    result = kb(vault, NOTE, "--ops", "-", stdin=json.dumps([{"op": "append", "text": "x" * 200}]))
    assert result.exit_code == 2 and "bytes" in result.output and text(vault) == BODY


def test_an_action_item_context_note(vault) -> None:
    result = run_ops(vault, CONTEXT, [{"op": "replace", "old": "waiting", "new": "unblocked"}])
    assert result.exit_code == 0 and "unblocked" in text(vault, CONTEXT)


# ----- the rules every KB write keeps ----------------------------------------------------


@pytest.mark.parametrize("reader", ["agent:person:priya@example.com", "delegate:helper", "elsewhere"])
def test_a_reader_that_isnt_owner_or_autonomous_edits_nothing(vault, reader: str) -> None:
    result = run_ops(vault, NOTE, [{"op": "replace", "old": "budget is TBD", "new": "x"}], reader=reader)
    assert result.exit_code == 3 and text(vault) == BODY and "budget" not in result.stdout


def test_a_scheduled_run_edits_a_private_note_without_printing_it(vault) -> None:
    result = run_ops(vault, SECRET, [{"op": "replace", "old": "permit", "new": "licence"}], reader="autonomous")
    assert result.exit_code == 0 and "licence" in text(vault, SECRET) and "permit" not in result.stdout


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "action-items/2026-11-25.md",
        "knowledge-base/ontology/schema.yaml",
        "drafts/knowledge-base/d.md",  # under some other folder's knowledge-base/: not the KB (#826's review)
        "notes/knowledge-base-old.md",
        ".claude/agents/knowledge-base.md",
    ],
)
def test_only_kb_notes(vault, path: str) -> None:
    (vault / path).parent.mkdir(parents=True, exist_ok=True)
    (vault / path).write_text("x\n", encoding="utf-8")
    assert run_ops(vault, path, [{"op": "replace", "old": "x", "new": "y"}]).exit_code == 2
    assert (vault / path).read_text(encoding="utf-8") == "x\n"


def test_a_missing_note_or_one_outside_the_vault(vault, tmp_path) -> None:
    assert run_ops(vault, "knowledge-base/none.md", [{"op": "append", "text": "x"}]).exit_code == 2
    assert run_ops(vault, str(tmp_path / "x.md"), [{"op": "append", "text": "x"}]).exit_code == 2


def test_a_note_that_isnt_utf8(vault) -> None:
    (vault / NOTE).write_bytes(b"caf\xe9\n")
    assert run_ops(vault, NOTE, [{"op": "append", "text": "x"}]).exit_code == 2


def test_a_hard_linked_note_is_refused_by_the_write_gate(vault) -> None:
    os.link(vault / NOTE, vault / "elsewhere.md")
    result = run_ops(vault, NOTE, [{"op": "replace", "old": "budget is TBD", "new": "x"}])
    assert result.exit_code == 3 and "write gate" in result.stdout and text(vault) == BODY


def test_making_a_note_private_is_allowed(vault) -> None:
    result = run_ops(vault, NOTE, [{"op": "frontmatter", "field": "visibility", "value": "private"}])
    assert result.exit_code == 0 and "visibility: private" in text(vault)


@pytest.mark.parametrize(
    ("path", "op"),
    [
        (SECRET, {"op": "frontmatter", "field": "visibility", "value": "normal"}),
        (SECRET, {"op": "frontmatter", "field": "visibility", "remove": True}),
        (NOTE, {"op": "frontmatter", "field": "federation_scope", "value": "org"}),
        (NOTE, {"op": "frontmatter", "field": "source", "value": "shared:person:priya@example.com"}),
    ],
)
def test_an_edit_that_would_widen_or_relabel_a_note_is_refused(vault, path: str, op) -> None:
    before = text(vault, path)
    result = run_ops(vault, path, [op])
    assert result.exit_code == 3 and "privacy label" in result.stdout and text(vault, path) == before


# ----- the ops document ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        ("not json", "not JSON"),
        ("[]", "JSON list"),
        ('"x"', "JSON list"),
        ('[{"op": "delete"}]', "one of"),
        ('[{"op": "replace", "old": "a"}]', "missing new"),
        ('[{"op": "replace", "old": "a", "new": "b", "x": 1}]', "1 unknown key$"),
        ('[{"op": "replace", "old": "a", "new": "b", "old": "c"}]', "same key twice"),
        ('[{"op": "replace", "old": 1, "new": "b"}]', "must be a string"),
        ('[{"op": "replace", "old": "", "new": "b"}]', "must not be empty"),
        ('[{"op": "replace", "old": "a", "new": "b", "count": 0}]', "count"),
        ('[{"op": "replace", "old": "a", "new": "b", "count": true}]', "count"),
        ('[{"op": "regex", "pattern": "(", "new": "b"}]', "doesn't compile"),
        ('[{"op": "regex", "pattern": "a", "new": "b", "flags": "q"}]', "flags"),
        ('[{"op": "insert", "heading": "Status", "text": "b"}]', "markdown heading"),
        ('[{"op": "insert", "heading": "## S", "text": "b", "at": "middle"}]', "at is"),
        ('[{"op": "insert", "heading": "## S", "text": "b", "create": "yes"}]', "create"),
        ('[{"op": "insert", "text": "b"}]', "one of heading, after or before"),
        ('[{"op": "insert", "heading": "## S", "after": "x", "text": "b"}]', "one of heading"),
        ('[{"op": "insert", "after": 1, "text": "b"}]', "after must be a string"),
        ('[{"op": "insert", "after": "x", "text": "b", "at": "end"}]', "go with heading"),
        ('[{"op": "insert", "before": " ", "text": "b"}]', "before must not be empty"),
        ('[{"op": "insert", "heading": 2, "text": "b"}]', "heading must be a string"),
        ('[{"op": "replace", "old": "a", "new": "b", "first": 1}]', "first must be true or false"),
        ('[{"op": "append", "text": "  "}]', "must not be empty"),
        ('[{"op": "frontmatter", "field": "a b", "value": 1}]', "plain top-level key"),
        ('[{"op": "frontmatter", "field": "a"}]', "give value"),
        ('[{"op": "frontmatter", "field": "a", "value": 1, "remove": true}]', "give value"),
        ('[{"op": "frontmatter", "field": "a", "remove": "yes"}]', "remove must"),
        ('[{"op": "frontmatter", "field": "a", "value": {"b": 1}}]', "value must"),
        ('[{"op": "patch", "diff": " "}]', "must not be empty"),
    ],
)
def test_a_bad_ops_document_is_a_usage_error(raw: str, why: str) -> None:
    with pytest.raises(UsageError, match=why):
        load_ops(raw, NOTE)


def test_an_unknown_key_isnt_named() -> None:
    with pytest.raises(UsageError) as e:
        load_ops('[{"op": "append", "text": "a", "the_private_plan": 1, "b": 2}]', NOTE)
    assert "private" not in str(e.value) and "2 unknown keys" in str(e.value)


def test_a_note_named_twice_in_the_map_is_refused_not_dropped() -> None:
    one = '[{"op": "append", "text": "a"}]'
    raw = f'{{"{NOTE}": {one}, "{NOTE}": {one}}}'
    with pytest.raises(UsageError, match="same key twice"):
        load_ops(raw)


def test_a_regex_that_backtracks_without_end_fails_in_time(kb_repo, monkeypatch) -> None:
    monkeypatch.setattr(kb_edit, "SEARCH_SECONDS", 0.2)
    kb_repo.write(NOTE, "a" * 40 + "b\n")
    result = run_ops(kb_repo.root, NOTE, [{"op": "regex", "pattern": "(a+)+$", "new": "x"}])
    assert result.exit_code == 1 and "ran over 0.2 s" in result.stdout
    assert text(kb_repo.root) == "a" * 40 + "b\n"


def test_the_regex_time_limit_leaves_no_alarm_behind() -> None:
    import signal

    before = signal.getsignal(signal.SIGALRM)
    apply_ops("abc\n", [{"op": "regex", "pattern": "b", "new": "x"}])
    assert signal.getsignal(signal.SIGALRM) is before and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_a_callers_timer_is_put_back_with_the_time_it_had_left() -> None:
    """A real 300 s timer: far longer than the op, so a loaded CI runner can't make it fire."""
    import signal

    fired: list[int] = []
    old = signal.signal(signal.SIGALRM, lambda *_: fired.append(1))
    try:
        signal.setitimer(signal.ITIMER_REAL, 300, 5)
        apply_ops("abc\n", [{"op": "regex", "pattern": "b", "new": "x"}])
        left, interval = signal.getitimer(signal.ITIMER_REAL)
        assert 200 < left <= 300 and interval == 5 and not fired
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def test_a_callers_timer_that_ran_out_during_the_op_fires_right_after(monkeypatch) -> None:
    """The timer calls are recorded, not run: the caller's timer had 0.01 s left, the op took at least 0.05 s."""
    import signal

    calls: list[tuple[float, ...]] = []

    def setitimer(which: int, delay: float, interval: float = 0.0) -> tuple[float, float]:
        calls.append((delay, interval))
        return (0.01, 0.0) if len(calls) == 1 else (0.0, 0.0)

    monkeypatch.setattr(signal, "setitimer", setitimer)
    assert kb_edit._limited(1, "regex", "slow", lambda: time.sleep(0.05) or 7) == 7
    # taken off, ours armed, ours cleared, the caller's put back to fire at once (1 µs) with its interval
    assert calls == [(0, 0.0), (kb_edit.SEARCH_SECONDS, 0.0), (0, 0.0), (1e-6, 0.0)]


def test_a_patch_hunk_search_over_repeated_lines_fails_in_time(monkeypatch) -> None:
    monkeypatch.setattr(kb_edit, "SEARCH_SECONDS", 0.05)
    note = "x\n" * 200_000
    diff = "@@ -1,41 +1,41 @@\n" + " x\n" * 40 + "-y\n+z\n"
    with pytest.raises(OpFailed, match="hunk 1's place ran over 0.05 s"):
        apply_ops(note, [{"op": "patch", "diff": diff}])


def test_ops_limits() -> None:
    with pytest.raises(UsageError, match="at most"):
        check_ops([{"op": "append", "text": "x"}] * (kb_edit.MAX_OPS + 1))
    with pytest.raises(UsageError, match="bytes"):
        load_ops(" " * (kb_edit.MAX_OPS_BYTES + 1), NOTE)


def test_a_bad_ops_document_exits_2_and_touches_nothing(vault) -> None:
    result = kb(vault, NOTE, "--ops", "-", stdin="[{")
    assert result.exit_code == 2 and text(vault) == BODY


def test_line_counts() -> None:
    assert line_counts("a\nb\n", "a\nc\nd\n") == (2, 1)
    assert line_counts("a\n", "a\n") == (0, 0)


# ----- the read gate lets the form the phases teach through --------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "scripts/scoutctl kb edit knowledge-base/people.md --ops - <<'EOF'\n"
        '[{"op":"replace","old":"cat knowledge-base/x.md","new":"y","count":2}]\nEOF',
        "cd /tmp && /x/scripts/scoutctl kb edit knowledge-base/people.md --ops - <<'EOF'\n"
        '[{"op":"insert","heading":"## Log","text":"- grep -r found it"}]\nEOF',
        'scripts/scoutctl kb edit knowledge-base/people.md --check --json --ops - <<< \'[{"op":"append","text":"x"}]\'',
    ],
)
def test_the_read_gate_allows_kb_edit_in_a_run(command: str, tmp_path, monkeypatch) -> None:
    from tests.unit.test_hooks_kb_read_gate import _decision

    v = tmp_path / "vault"
    (v / "knowledge-base").mkdir(parents=True)
    (v / "scout-config.yaml").write_text("user: {}\n")
    (v / "knowledge-base" / "people.md").write_text("# People\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    assert _decision(v.resolve(), "Bash", {"command": command}) is None


# ----- #826's security review ------------------------------------------------------------


def test_only_full_readers_edit() -> None:
    """Every other reader is refused before a note is read, so kb edit's counts are no oracle for them."""
    assert kb_edit.FULL_READERS == {"owner", "autonomous"}


def _scheduled(data_dir, *args: str, ops):
    env = {"SCOUT_MODE": "dreaming", "SCOUT_READER": "autonomous", "SCOUT_DATA_DIR": str(data_dir)}
    return runner.invoke(app, ["kb", "edit", *args], input=json.dumps(ops), env=env)


def _audit_rows(root) -> list[dict]:
    log = root / ".scout-logs" / "kb-reads.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _temps(folder) -> list[str]:
    return [p.name for p in folder.iterdir() if p.name.endswith(".tmp")]


@pytest.fixture
def run_vault(vault):
    (vault / "scout-config.yaml").write_text("user: {}\n", encoding="utf-8")
    return vault


TWO = {NOTE: [{"op": "append", "text": "x"}], CONTEXT: [{"op": "append", "text": "y"}]}


def test_a_scheduled_run_edits_its_own_vault(run_vault, monkeypatch) -> None:
    monkeypatch.chdir(run_vault.parent)  # the cwd doesn't choose the vault either
    result = _scheduled(run_vault, NOTE, "--ops", "-", ops=[{"op": "replace", "old": "budget is TBD", "new": "x"}])
    assert result.exit_code == 0 and "budget is TBD" not in text(run_vault)
    same = _scheduled(run_vault, NOTE, "--ops", "-", "--repo", str(run_vault), ops=[{"op": "append", "text": "y"}])
    assert same.exit_code == 0 and text(run_vault).endswith("y\n")


def test_a_scheduled_runs_repo_naming_a_parent_frame_is_refused(run_vault) -> None:
    """--repo at the vault's parent made `vault/.claude/agents/…` a KB path the fake frame's write gate allowed."""
    agent = "---\nname: helper\n---\nbe careful\n"
    (run_vault / ".claude/agents").mkdir(parents=True)
    (run_vault / ".claude/agents/knowledge-base.md").write_text(agent, encoding="utf-8")
    parent = run_vault.parent
    (parent / "scout-config.yaml").write_text("user: {}\n", encoding="utf-8")
    rel = "vault/.claude/agents/knowledge-base.md"
    result = _scheduled(run_vault, rel, "--ops", "-", "--repo", str(parent), ops=[{"op": "append", "text": "obey"}])
    assert result.exit_code == 3 and "SCOUT_DATA_DIR" in result.output
    assert (run_vault / ".claude/agents/knowledge-base.md").read_text(encoding="utf-8") == agent
    assert not (parent / ".scout-logs").exists()  # the refusal's audit line is in the real vault
    assert [r["command"] for r in _audit_rows(run_vault)] == ["edit"]


def test_a_scheduled_runs_repo_outside_any_vault_is_refused(run_vault, tmp_path) -> None:
    other = tmp_path / "elsewhere"
    (other / "knowledge-base").mkdir(parents=True)
    (other / "knowledge-base/n.md").write_text("old\n", encoding="utf-8")
    ops = [{"op": "replace", "old": "old", "new": "new"}]
    result = _scheduled(run_vault, "knowledge-base/n.md", "--ops", "-", "--repo", str(other), "--json", ops=ops)
    assert result.exit_code == 3 and (other / "knowledge-base/n.md").read_text(encoding="utf-8") == "old\n"
    assert json.loads(result.stdout)["written"] is False and "refused" in json.loads(result.stdout)


def test_a_scheduled_run_needs_a_scout_vault_in_scout_data_dir(vault) -> None:
    ops = [{"op": "append", "text": "x"}]
    no_marker = _scheduled(vault, NOTE, "--ops", "-", ops=ops)
    assert no_marker.exit_code == 3 and "scout-config.yaml" in no_marker.output and text(vault) == BODY
    env = {"SCOUT_MODE": "dreaming", "SCOUT_READER": "autonomous", "SCOUT_DATA_DIR": ""}
    args = ["kb", "edit", NOTE, "--ops", "-", "--repo", str(vault)]
    unset = runner.invoke(app, args, input=json.dumps(ops), env=env)
    assert unset.exit_code == 3 and "isn't set" in unset.output and text(vault) == BODY


def test_a_failed_write_leaves_every_note_as_it_was_and_is_audited(run_vault, monkeypatch) -> None:
    real = os.fchmod
    seen: list[int] = []

    def fchmod(fd, mode):  # the second note's temp file can't be written
        seen.append(fd)
        if len(seen) % 2 == 0:
            raise PermissionError(13, "Permission denied")
        return real(fd, mode)

    monkeypatch.setattr(kb_edit.os, "fchmod", fchmod)
    data = json.loads(_scheduled(run_vault, "--ops", "-", "--json", ops=TWO).stdout)
    assert data["written"] is False and data["error"] == "Permission denied"
    assert text(run_vault) == BODY and text(run_vault, CONTEXT).endswith("Status: waiting\n")
    assert not _temps(run_vault / "knowledge-base/projects/alpha") and not _temps(run_vault / "action-items/context")
    plain = _scheduled(run_vault, "--ops", "-", ops=TWO)
    assert plain.exit_code == 1 and "the write failed (Permission denied): 0 notes written" in plain.stdout
    assert [r["command"] for r in _audit_rows(run_vault)] == ["edit", "edit"]


def test_a_rename_failing_part_way_says_which_notes_were_written(run_vault, monkeypatch) -> None:
    real = os.replace
    calls: list[int] = []

    def second_fails(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        return real(*args, **kwargs)

    monkeypatch.setattr(kb_edit.os, "replace", second_fails)
    result = _scheduled(run_vault, "--ops", "-", "--json", ops=TWO)
    data = json.loads(result.stdout)
    assert result.exit_code == 4  # not 1: a run must not retry an append it already made
    assert [f["written"] for f in data["files"]] == [True, False] and data["written"] is True
    assert text(run_vault).endswith("x\n") and text(run_vault, CONTEXT).endswith("Status: waiting\n")
    assert not _temps(run_vault / "action-items/context")


def test_a_folder_swapped_for_a_symlink_after_the_plan_is_not_followed(run_vault, tmp_path, monkeypatch) -> None:
    real = kb_edit.apply_ops
    decoy = tmp_path / "decoy"

    def swap(src, ops):
        folder = run_vault / "knowledge-base/projects/alpha"
        decoy.mkdir()
        (decoy / "alpha.md").write_text(src, encoding="utf-8")
        folder.rename(tmp_path / "moved")
        folder.symlink_to(decoy)
        return real(src, ops)

    monkeypatch.setattr(kb_edit, "apply_ops", swap)
    result = _scheduled(run_vault, NOTE, "--ops", "-", ops=[{"op": "append", "text": "x"}])
    assert result.exit_code == 1 and "the write failed" in result.stdout
    assert (decoy / "alpha.md").read_text(encoding="utf-8") == BODY


def test_a_hard_link_made_after_the_plan_is_not_written_through(run_vault, tmp_path, monkeypatch) -> None:
    real = kb_edit.apply_ops

    def link(src, ops):
        os.link(run_vault / NOTE, tmp_path / "outside.md")
        return real(src, ops)

    monkeypatch.setattr(kb_edit, "apply_ops", link)
    result = _scheduled(run_vault, NOTE, "--ops", "-", ops=[{"op": "append", "text": "x"}])
    assert result.exit_code == 1 and "the write failed" in result.stdout
    assert text(run_vault) == BODY and (tmp_path / "outside.md").read_text(encoding="utf-8") == BODY


def test_a_note_changed_between_staging_and_the_renames_is_not_overwritten(run_vault, monkeypatch) -> None:
    real = kb_edit._current
    calls: list[int] = []

    def late(dir_fd, name):
        calls.append(1)
        if len(calls) == 3:  # the second look at the first note: another run wrote it meanwhile
            (run_vault / NOTE).write_text(BODY + "another run\n", encoding="utf-8")
        return real(dir_fd, name)

    monkeypatch.setattr(kb_edit, "_current", late)
    result = _scheduled(run_vault, "--ops", "-", ops=TWO)
    assert result.exit_code == 1 and "changed meanwhile" in result.stdout
    assert text(run_vault) == BODY + "another run\n" and text(run_vault, CONTEXT).endswith("Status: waiting\n")
    assert not _temps(run_vault / "knowledge-base/projects/alpha") and not _temps(run_vault / "action-items/context")
