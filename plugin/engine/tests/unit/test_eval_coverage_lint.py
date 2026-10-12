"""Stub coverage lint: every tool a connector phase names is served by the fixture (spec 2026-10-12 §5.5)."""

from __future__ import annotations

from pathlib import Path

from tests.eval.harness.coverage_lint import CATALOG_DIR, PHASES_DIR, lint, referenced_tools


def _phases(tmp_path: Path, files: dict[str, str]) -> Path:
    d = tmp_path / "connectors"
    d.mkdir()
    for name, text in files.items():
        (d / name).write_text(text)
    return d


def test_live_phases_are_fully_served() -> None:
    problems = lint(PHASES_DIR, CATALOG_DIR, CATALOG_DIR.parent / "unserved.txt")
    assert problems == [], "\n".join(problems)


def test_backticked_tool_names_and_full_mcp_names_are_collected(tmp_path: Path) -> None:
    d = _phases(
        tmp_path,
        {
            "slack.md": "Use `slack_read_thread`, not `worked_on_by`; "
            "probe mcp__claude_ai_Slack__slack_read_user_profile.",
            "linear.md": "Call `list_issues` then `save_comment`.",
        },
    )
    refs = referenced_tools(d)
    assert refs["slack"] == {"slack_read_thread", "slack_read_user_profile"}
    assert refs["linear"] == {"list_issues", "save_comment"}


def test_gh_subcommands_are_collected(tmp_path: Path) -> None:
    d = _phases(tmp_path, {"github.md": '```bash\ngh pr list --author @me\ngh api "/repos/x"\n```\nThe gh context to'})
    assert referenced_tools(d)["gh"] == {"pr list", "api"}


def test_an_unserved_tool_is_reported_unless_listed(tmp_path: Path) -> None:
    d = _phases(tmp_path, {"slack.md": "Then call `slack_fly_to_moon`.", "github.md": "gh run list"})
    unserved = tmp_path / "unserved.txt"
    unserved.write_text("")
    assert lint(d, CATALOG_DIR, unserved) == ["gh: run list", "slack: slack_fly_to_moon"]
    unserved.write_text("slack: slack_fly_to_moon  # not needed by any scenario yet\n")
    assert lint(d, CATALOG_DIR, unserved) == ["gh: run list"]
