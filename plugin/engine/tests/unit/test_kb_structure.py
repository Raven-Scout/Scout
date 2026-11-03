"""Tests for `scoutctl kb structure` (scout/kb/structure.py): project folders vs the KB page standard."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.errors import KBError
from scout.kb import structure
from scout.kb.structure import (
    load_structure_config,
    missing_sections,
    narration_found,
    newest_changes,
    split_page,
    structure_rows,
)

runner = CliRunner()

SECTIONS = ["Where it stands", "What's next", "Key decisions", "Open questions", "People", "Go deeper", "History"]
P = "knowledge-base/projects"


def page(
    name: str,
    *,
    as_of: str | None = "2026-10-01",
    priority: str = "🟢",
    sections: list[str] | None = None,
    extra: str = "",
    frontmatter: str = "",
) -> str:
    fm = f'name: {name}\ntype: project\npriority: "{priority} P2"\n'
    if as_of:
        fm += f"as_of: {as_of}\n"
    body = "".join(f"## {s}\nNone right now.\n\n" for s in (SECTIONS if sections is None else sections))
    return f"---\n{fm}{frontmatter}---\n# {name.title()}\n\nA made-up project.\n\n{body}{extra}"


def commit_at(kb_repo, day: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_COMMITTER_DATE", f"{day}T12:00:00+00:00")
    monkeypatch.setenv("GIT_AUTHOR_DATE", f"{day}T12:00:00+00:00")
    kb_repo.git("add", "-A")
    kb_repo.commit(f"c {day}")


def rows_by_name(root: Path) -> dict[str, dict]:
    return {r["project"]: r for r in structure_rows(root)}


# ----- pure page checks ----------------------------------------------------------


def test_split_page_reads_frontmatter_and_body() -> None:
    fm, body = split_page(page("widgets"))
    assert fm["name"] == "widgets" and str(fm["as_of"]) == "2026-10-01"
    assert body.startswith("# Widgets")


def test_split_page_without_or_with_bad_frontmatter() -> None:
    assert split_page("# Plain\n") == ({}, "# Plain\n")
    fm, body = split_page("---\n: [unclosed\n---\n## History\n")
    assert fm == {} and body == "## History\n"


def test_missing_sections_keeps_the_standards_order() -> None:
    body = "## History\n## People\n### Where it stands\n"
    assert missing_sections(body, SECTIONS) == SECTIONS[:4] + ["Go deeper"]


def test_default_narration_markers() -> None:
    markers = load_structure_config().narration_markers
    assert narration_found("Plain prose about widgets.", markers) == []
    body = (
        "> Last verified Oct 3 against the tracker\n"
        "Finding 12 says so (§4). The evening-consolidation run at 08:0x saw it.\n"
    )
    assert narration_found(body, markers) == ["Finding N", "Last verified", "masked time", "session name", "§N"]
    # A plain time or a word like "finding" is not narration.
    assert narration_found("The sync runs at 08:00; finding out is next.", markers) == []


# ----- the folder checks ------------------------------------------------------------


def test_a_standard_folder_conforms(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-decisions.md", "# Decisions\n")
    kb_repo.write(f"{P}/widgets/documents/widgets-vision.md", "# Vision\n")
    kb_repo.write(f"{P}/widgets/archive/widgets-2026-09-01-pre-restructure.md", "old\n")
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["conforms"] and row["problems"] == []
    assert row["path"] == f"{P}/widgets/widgets.md" and row["priority"] == "🟢"


def test_loose_files_and_odd_dirs(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    kb_repo.write(f"{P}/widgets/notes.md", "loose\n")
    kb_repo.write(f"{P}/widgets/diagram.png", "not markdown\n")
    kb_repo.stage(f"{P}/widgets/scratch/a.md", "odd\n")
    kb_repo.stage(f"{P}/widgets/widgets-api/widgets-api.md", "a sub-project\n")
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["loose"] == [f"{P}/widgets/notes.md"]
    assert row["odd_dirs"] == [f"{P}/widgets/scratch"]
    assert row["problems"] == ["1 loose", "odd dirs: scratch"]


def test_size_budget_and_target(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", extra="x" * 9000))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", extra="x" * 17000))
    rows = rows_by_name(kb_repo.root)
    assert rows["widgets"]["conforms"] and rows["widgets"]["over_target"] and not rows["widgets"]["over_budget"]
    assert rows["gadgets"]["over_budget"] and rows["gadgets"]["problems"] == ["16 KB"]


def test_missing_sections_narration_and_as_of(kb_repo) -> None:
    kb_repo.write(
        f"{P}/widgets/widgets.md",
        page("widgets", as_of=None, sections=SECTIONS[:5], extra="Finding 3 holds.\n"),
    )
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["missing_sections"] == ["Go deeper", "History"] and row["missing_count"] == 2
    assert row["narration"] == ["Finding N"] and row["narration_count"] == 1
    assert row["as_of"] is None
    assert row["problems"] == ["2 sections missing", "narration: Finding N", "no as_of"]


def test_a_bad_as_of_counts_as_missing(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of="soon"))
    assert rows_by_name(kb_repo.root)["widgets"]["problems"] == ["no as_of"]


def test_an_impossible_yaml_date_reads_as_no_as_of(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of="2026-13-45"))
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["problems"] == ["no as_of"]
    assert row["private"]  # the frontmatter doesn't parse, so the page fails closed


def test_a_priority_that_is_not_a_status_emoji_reads_as_none(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="high"))
    assert rows_by_name(kb_repo.root)["widgets"]["priority"] is None


def test_skipped_and_frontless_folders_are_left_out(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    kb_repo.write(f"{P}/archived/old-thing/old-thing.md", page("old-thing"))
    kb_repo.write(f"{P}/archived/archived.md", page("archived", sections=[]))
    kb_repo.write(f"{P}/drafts/notes.md", "no front page\n")
    assert list(rows_by_name(kb_repo.root)) == ["widgets"]


def test_no_projects_folder_is_an_empty_table(kb_repo) -> None:
    assert structure_rows(kb_repo.root) == []


# ----- staleness: one git pass ---------------------------------------------------------


def test_stale_when_the_folder_changed_after_as_of(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of="2026-10-01"))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-pricing.md", "v1\n")
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    assert not rows_by_name(kb_repo.root)["widgets"]["stale"]

    kb_repo.write(f"{P}/widgets/knowledge/widgets-pricing.md", "v2\n")
    commit_at(kb_repo, "2026-10-04", monkeypatch)  # 3 days: the threshold, not past it
    assert not rows_by_name(kb_repo.root)["widgets"]["stale"]

    kb_repo.write(f"{P}/widgets/knowledge/widgets-pricing.md", "v3\n")
    commit_at(kb_repo, "2026-10-05", monkeypatch)
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["stale"] and row["folder_last_change"] == "2026-10-05"
    assert row["problems"] == ["stale (as_of 2026-10-01, folder changed 2026-10-05)"]


def test_front_page_and_archive_changes_do_not_count(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of="2026-10-01"))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-pricing.md", "v1\n")
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of="2026-10-01", extra="more\n"))
    kb_repo.write(f"{P}/widgets/archive/widgets-old.md", "old\n")
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets"))  # another folder entirely
    commit_at(kb_repo, "2026-10-20", monkeypatch)
    row = rows_by_name(kb_repo.root)["widgets"]
    assert not row["stale"] and row["folder_last_change"] == "2026-10-01"


def test_loose_file_history_counts(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of="2026-10-01"))
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    kb_repo.write(f"{P}/widgets/notes.md", "loose\n")
    commit_at(kb_repo, "2026-10-10", monkeypatch)
    assert rows_by_name(kb_repo.root)["widgets"]["stale"]


def test_newest_changes_maps_each_path_to_its_newest_day(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    kb_repo.write(f"{P}/widgets/knowledge/a b.md", "1\n")
    kb_repo.write(f"{P}/widgets/knowledge/é.md", "1\n")
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    kb_repo.write(f"{P}/widgets/knowledge/a b.md", "2\n")
    commit_at(kb_repo, "2026-10-03", monkeypatch)
    history = newest_changes(kb_repo.root, P)
    assert str(history[f"{P}/widgets/knowledge/a b.md"]) == "2026-10-03"
    assert str(history[f"{P}/widgets/knowledge/é.md"]) == "2026-10-01"


def test_a_vault_inside_a_larger_repo_still_gets_history(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    kb_repo.write(f"nested/{P}/widgets/knowledge/widgets-notes.md", "n\n")
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    history = newest_changes(kb_repo.root / "nested", P)
    assert str(history[f"{P}/widgets/knowledge/widgets-notes.md"]) == "2026-10-01"


def test_newest_changes_without_git_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("git")

    monkeypatch.setattr(structure.subprocess, "run", no_git)
    assert newest_changes(tmp_path, P) == {}


def test_newest_changes_outside_a_repo_is_empty(tmp_path: Path) -> None:
    assert newest_changes(tmp_path, P) == {}


def test_a_path_git_would_quote_still_counts_as_a_change(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of="2026-10-01"))
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    kb_repo.write(f'{P}/widgets/knowledge/with"quote and\ttab.md', "n\n")
    commit_at(kb_repo, "2026-10-20", monkeypatch)
    assert rows_by_name(kb_repo.root)["widgets"]["stale"]


def test_an_amend_does_not_make_a_page_stale(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of="2026-10-01"))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-notes.md", "n\n")
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-20T12:00:00+00:00")
    kb_repo.git("commit", "-q", "--amend", "--no-edit", "--no-verify")
    row = rows_by_name(kb_repo.root)["widgets"]
    assert not row["stale"] and row["folder_last_change"] == "2026-10-01"


def test_a_later_commit_with_an_older_author_date_does_not_hide_a_change(
    kb_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = f"{P}/widgets/knowledge/widgets-notes.md"
    kb_repo.write(note, "1\n")
    commit_at(kb_repo, "2026-10-20", monkeypatch)
    kb_repo.write(note, "2\n")
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    assert str(newest_changes(kb_repo.root, P)[note]) == "2026-10-20"


def test_a_dotted_projects_dir_reads_the_same_folder(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    kb_repo.write("scout-config.yaml", f"kb_structure:\n  projects_dir: ./{P}/\n")
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of="2026-10-01"))
    kb_repo.write(f"{P}/widgets/knowledge/widgets-notes.md", "n\n")
    commit_at(kb_repo, "2026-10-20", monkeypatch)
    assert load_structure_config(kb_repo.root).projects_dir == P
    assert rows_by_name(kb_repo.root)["widgets"]["stale"]


def test_a_projects_dir_outside_the_vault_is_an_error(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_structure:\n  projects_dir: ../elsewhere\n")
    result = runner.invoke(cli.app, ["kb", "structure", "--repo", str(kb_repo.root)])
    assert isinstance(result.exception, KBError) and "projects_dir" in str(result.exception)


def test_a_null_projects_dir_is_an_error_not_an_empty_table(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_structure:\n  projects_dir: null\n")
    result = runner.invoke(cli.app, ["kb", "structure", "--next", "--repo", str(kb_repo.root)])
    assert isinstance(result.exception, KBError) and "projects_dir" in str(result.exception)  # cli.main: exit 20


def test_history_is_read_in_one_git_call(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("widgets", "gadgets", "sprockets"):
        kb_repo.write(f"{P}/{name}/{name}.md", page(name))
        kb_repo.write(f"{P}/{name}/knowledge/{name}-notes.md", "n\n")
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        if cmd and cmd[0] == "git":
            calls.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(structure.subprocess, "run", spy)
    assert len(structure_rows(kb_repo.root)) == 3
    assert sorted(c[1] for c in calls) == ["log", "ls-files"]


# ----- order and --next ---------------------------------------------------------------


def test_rebuild_order_and_next(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", priority="🟢", as_of=None))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", priority="🔴", as_of=None))
    kb_repo.write(f"{P}/sprockets/sprockets.md", page("sprockets", priority="🔴", as_of=None, extra="x" * 3000))
    kb_repo.write(f"{P}/gizmos/gizmos.md", page("gizmos", priority="🔴"))  # conforms: last
    kb_repo.write(f"{P}/doohickeys/doohickeys.md", page("doohickeys", priority="", as_of=None))
    order = [r["project"] for r in structure_rows(kb_repo.root)]
    assert order == ["sprockets", "gadgets", "widgets", "doohickeys", "gizmos"]

    result = runner.invoke(cli.app, ["kb", "structure", "--next", "--repo", str(kb_repo.root)])
    assert result.exit_code == 0 and result.output == "sprockets\n"
    result = runner.invoke(cli.app, ["kb", "structure", "--next", "--json", "--repo", str(kb_repo.root)])
    assert json.loads(result.output) == {"project": "sprockets", "path": f"{P}/sprockets/sprockets.md"}


def test_next_is_empty_when_everything_conforms(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    result = runner.invoke(cli.app, ["kb", "structure", "--next", "--repo", str(kb_repo.root)])
    assert result.exit_code == 0 and result.output == "\n"
    result = runner.invoke(cli.app, ["kb", "structure", "--next", "--json", "--repo", str(kb_repo.root)])
    assert json.loads(result.output) is None


def test_cli_table_and_json(kb_repo) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    kb_repo.write(f"{P}/gadgets/gadgets.md", page("gadgets", as_of=None))
    result = runner.invoke(cli.app, ["kb", "structure", "--repo", str(kb_repo.root)])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert lines[0] == "1/2 project folders meet the KB page standard"
    assert lines[2].startswith("✗ 🟢 gadgets") and lines[2].endswith("no as_of")
    assert lines[3].startswith("✓ 🟢 widgets") and lines[3].endswith("ok")
    data = json.loads(runner.invoke(cli.app, ["kb", "structure", "--json", "--repo", str(kb_repo.root)]).output)
    assert [r["project"] for r in data] == ["gadgets", "widgets"]
    assert set(data[0]) >= {"size", "loose", "odd_dirs", "missing_sections", "narration", "stale", "conforms"}


# ----- configuration and exclusions ---------------------------------------------------------


def test_vault_config_overrides_the_defaults(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    kb_repo.write(
        "scout-config.yaml",
        "kb_structure:\n"
        "  page_budget_bytes: 2000\n"
        "  stale_days: 0\n"
        "  allowed_dirs: [knowledge, notes]\n"
        "  required_sections: [Summary]\n"
        "  narration_markers:\n"
        "    - {label: ticket, pattern: 'TICKET-\\d+'}\n",
    )
    kb_repo.write(
        f"{P}/widgets/widgets.md", page("widgets", sections=["Summary"], as_of="2026-10-01", extra="TICKET-7\n")
    )
    kb_repo.write(f"{P}/widgets/notes/a.md", "fine here\n")
    kb_repo.write(f"{P}/widgets/research/b.md", "not allowed now\n")
    commit_at(kb_repo, "2026-10-02", monkeypatch)
    cfg = load_structure_config(kb_repo.root)
    assert cfg.page_budget_bytes == 2000 and cfg.required_sections == ("Summary",)
    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["odd_dirs"] == [f"{P}/widgets/research"]
    assert row["missing_sections"] == [] and row["narration"] == ["ticket"] and row["stale"]


def test_a_bad_marker_pattern_is_an_error(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_structure:\n  narration_markers:\n    - {label: bad, pattern: '('}\n")
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    result = runner.invoke(cli.app, ["kb", "structure", "--repo", str(kb_repo.root)])
    assert result.exit_code != 0
    assert "bad pattern" in str(result.exception) or "bad pattern" in result.output


def test_lint_exclude_globs_and_graph_exclude_are_respected(kb_repo) -> None:
    kb_repo.write(
        "scout-config.yaml",
        "kb_lint:\n  exclude_globs: ['knowledge-base/projects/widgets/scratch.md', '**/extracted/**']\n",
    )
    kb_repo.write(
        "knowledge-base/ontology/schema.yaml",
        "entity_types: {}\nrelationship_types: {}\n"
        "graph:\n  exclude: ['projects/old-*/**', 'projects/**/archive/**']\n",
    )
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets"))
    kb_repo.write(f"{P}/widgets/scratch.md", "excluded loose file\n")
    kb_repo.write(f"{P}/widgets/extracted/x.md", "excluded dir\n")
    kb_repo.write(f"{P}/old-widgets/old-widgets.md", page("old-widgets", as_of=None))
    rows = rows_by_name(kb_repo.root)
    assert list(rows) == ["widgets"] and rows["widgets"]["conforms"]


# ----- privacy ------------------------------------------------------------------------------


def test_a_private_front_page_shows_only_paths_and_counts(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "Finding 9: the codename is Bluebird"
    kb_repo.write(
        f"{P}/widgets/widgets.md",
        page(
            "widgets",
            priority="🔴",
            as_of="2026-10-01",
            sections=SECTIONS[:3],
            extra=secret + "\n",
            frontmatter="visibility: private\n",
        ),
    )
    kb_repo.write(f"{P}/widgets/knowledge/widgets-notes.md", "n\n")
    kb_repo.write(f"{P}/widgets/notes.md", "loose\n")
    commit_at(kb_repo, "2026-10-01", monkeypatch)
    kb_repo.write(f"{P}/widgets/knowledge/widgets-notes.md", "n2\n")
    commit_at(kb_repo, "2026-10-09", monkeypatch)

    row = rows_by_name(kb_repo.root)["widgets"]
    assert row["private"] and row["stale"] and not row["conforms"]
    assert row["priority"] is None and row["as_of"] is None and row["folder_last_change"] is None
    assert row["missing_sections"] is None and row["missing_count"] == 4
    assert row["narration"] is None and row["narration_count"] == 1
    assert row["loose"] == [f"{P}/widgets/notes.md"]
    assert row["problems"] == ["1 loose", "4 sections missing", "narration: 1 marker(s)", "stale"]

    for args in (["kb", "structure"], ["kb", "structure", "--json"], ["kb", "structure", "--next"]):
        out = runner.invoke(cli.app, [*args, "--repo", str(kb_repo.root)]).output
        for leak in ("Bluebird", "Finding", "🔴", "2026-10", "Open questions", "People"):
            assert leak not in out, (args, leak)
    text = runner.invoke(cli.app, ["kb", "structure", "--repo", str(kb_repo.root)]).output
    assert "widgets [private]" in text


def test_an_agent_reader_gets_nothing(kb_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    kb_repo.write(f"{P}/widgets/widgets.md", page("widgets", as_of=None))
    opened: list[str] = []
    monkeypatch.setattr(structure, "structure_rows", lambda vault: opened.append("x") or [])
    args = ["kb", "structure", "--json", "--reader", "agent:person:alex@example.com", "--repo", str(kb_repo.root)]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0 and json.loads(result.output) == [] and opened == []
