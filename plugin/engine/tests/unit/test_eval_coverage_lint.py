"""Stub coverage lint: every tool a phase names is served by the fixture (spec 2026-10-12 §5.5).

A tool missing from a catalog is never in ``tools/list``, so the model can't
see it and silently adapts. Nothing at run time reports that, so this lint
is the only guard.
"""

from __future__ import annotations

from pathlib import Path

from tests.eval.harness.coverage_lint import CATALOG_DIR, PHASES_DIR, lint, referenced_tools


def _phases(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "phases"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def test_live_phases_are_fully_served() -> None:
    assert (PHASES_DIR / "connectors" / "slack.md").is_file()
    refs = referenced_tools(PHASES_DIR)
    assert refs.get("slack") and refs.get("gh"), "lint found no references: is PHASES_DIR right?"
    problems = lint(PHASES_DIR, CATALOG_DIR, CATALOG_DIR.parent / "unserved.txt")
    assert problems == [], "\n".join(problems)


def test_backticked_names_call_forms_and_full_mcp_names_are_collected(tmp_path: Path) -> None:
    d = _phases(
        tmp_path,
        {
            "connectors/slack.md": "Use `slack_read_thread`, not `worked_on_by`; "
            "read `slack_read_channel(id, limit=20)`; probe mcp__claude_ai_Slack__slack_read_user_profile.",
            "connectors/linear.md": "Call `list_issues`, `create_issue` then `save_comment`.",
        },
    )
    refs = referenced_tools(d)
    assert refs["slack"] == {"slack_read_thread", "slack_read_channel", "slack_read_user_profile"}
    assert refs["linear"] == {"list_issues", "create_issue", "save_comment"}


def test_tools_named_outside_the_connector_phases_are_collected(tmp_path: Path) -> None:
    d = _phases(
        tmp_path,
        {"core/kb.md": "Find it with `slack_search_channels`, then `get_project` on the tracker."},
    )
    refs = referenced_tools(d)
    assert refs["slack"] == {"slack_search_channels"}
    assert refs["any"] == {"get_project"}


def test_gh_commands_in_code_are_collected_with_flags_and_any_top_level_command(tmp_path: Path) -> None:
    d = _phases(
        tmp_path,
        {
            "connectors/github.md": '```bash\ngh pr list --author @me\ngh api "/repos/x"\ngh -R o/r pr merge 3\n'
            "gh status\n```\nRun `gh browse` if needed. The gh context to prose is ignored.",
        },
    )
    assert referenced_tools(d)["gh"] == {"pr list", "api", "pr merge", "status", "browse"}


def test_separate_inline_gh_mentions_do_not_join_into_a_command(tmp_path: Path) -> None:
    d = _phases(tmp_path, {"core/git.md": "Use the `gh` CLI.\n\nAll calls go through `gh` too."})
    assert referenced_tools(d).get("gh", set()) == set()


def test_an_unserved_tool_is_reported_unless_listed(tmp_path: Path) -> None:
    d = _phases(
        tmp_path,
        {"connectors/slack.md": "Then call `slack_fly_to_moon`.", "connectors/github.md": "```\ngh run list\n```"},
    )
    unserved = tmp_path / "unserved.txt"
    unserved.write_text("")
    assert lint(d, CATALOG_DIR, unserved) == ["gh: run list", "slack: slack_fly_to_moon"]
    unserved.write_text("slack: slack_fly_to_moon  # not needed by any scenario yet\n")
    assert lint(d, CATALOG_DIR, unserved) == ["gh: run list"]


def test_a_generic_name_served_by_any_persona_passes(tmp_path: Path) -> None:
    d = _phases(tmp_path, {"modes/x.md": "Use `list_issues`."})
    assert lint(d, CATALOG_DIR, tmp_path / "none.txt") == []
