"""Tests for scout.kb.lint — the pre-commit ratchet."""

from __future__ import annotations

import io
import json
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from scout.kb.lint import OVERRIDE_ENV, _frontmatter_aliases, lint_staged, override_count, run_hook
from scout.kb.lint_config import load_lint_config

TOPIC = "knowledge-base/topics/widget/retrieval.md"
PROJECT = "knowledge-base/projects/acme-pilot/acme-pilot.md"


def _small_budgets(kb_repo, mode: str = "block") -> None:
    kb_repo.write(
        "scout-config.yaml",
        f"kb_lint:\n  mode: {mode}\nkb_budgets:\n"
        "  'knowledge-base/projects/**': 100\n  'knowledge-base/topics/**': 100\n",
    )


def _checks(result) -> list[tuple[str, str, bool]]:
    return sorted((f.path, f.check, f.blocking) for f in result.findings)


def test_under_budget_passes(kb_repo) -> None:
    _small_budgets(kb_repo)
    kb_repo.stage(PROJECT, "short ([[x]])\n")
    assert lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).blocking == []


def test_new_file_over_budget_blocks(kb_repo) -> None:
    _small_budgets(kb_repo)
    kb_repo.stage(PROJECT, "x" * 200 + "\n")
    assert ("knowledge-base/projects/acme-pilot/acme-pilot.md", "size", True) in _checks(
        lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    )


def test_over_budget_file_growing_blocks_shrinking_passes(kb_repo) -> None:
    _small_budgets(kb_repo)
    kb_repo.stage(PROJECT, "a\n" * 100)
    kb_repo.commit()
    cfg = load_lint_config(kb_repo.root)
    kb_repo.stage(PROJECT, "a\n" * 101)
    assert [f.check for f in lint_staged(kb_repo.root, cfg).blocking] == ["size"]
    kb_repo.stage(PROJECT, "a\n" * 99)
    assert lint_staged(kb_repo.root, cfg).blocking == []


def test_rename_without_growth_passes(kb_repo) -> None:
    _small_budgets(kb_repo)
    diary = "## §59 · 2026-09-07 (1:0x AM ET, `overnight-research`)\n"
    kb_repo.stage("knowledge-base/projects/old/old.md", diary + "x" * 2000 + "\n" + "a\n" * 100)
    kb_repo.commit()
    kb_repo.git("mv", "knowledge-base/projects/old/old.md", "knowledge-base/projects/old/renamed.md")
    assert lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).blocking == []


def test_added_diary_heading_blocks_existing_one_does_not(kb_repo) -> None:
    diary = "## §59 · 🔴 2026-09-07 (1:0x AM ET, `overnight-research`): finding\n"
    kb_repo.stage(PROJECT, "# Acme\n" + diary)
    kb_repo.commit()
    cfg = load_lint_config(kb_repo.root)
    kb_repo.stage(PROJECT, "# Acme\n" + diary + "status: live\n")
    assert lint_staged(kb_repo.root, cfg).blocking == []
    kb_repo.stage(PROJECT, "# Acme\n" + diary + "status: live\n## ✅ 2026-09-28 (8:0x AM ET, `morning-briefing`)\n")
    blocking = lint_staged(kb_repo.root, cfg).blocking
    assert [(f.check, f.line) for f in blocking] == [("diary", 4)]


def test_diary_heading_allowed_in_log_paths(kb_repo) -> None:
    kb_repo.stage("knowledge-base/session-log/2026-09.md", "## 2026-09-28 (8:0x AM ET, `morning-briefing`)\n")
    assert lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).blocking == []


def test_diary_heading_blocked_in_daily_action_items(kb_repo) -> None:
    kb_repo.stage(
        "action-items/action-items-2026-09-28.md",
        "### ⬇️ Re-tiered Sat Aug 29 (`weekend-briefing`) — 92 rows\n",
    )
    assert [f.check for f in lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).blocking] == ["diary"]


def test_mega_line_limits(kb_repo) -> None:
    cfg = load_lint_config(kb_repo.root)
    kb_repo.stage(PROJECT, "x" * 1501 + "\n")
    assert "mega-line" in [f.check for f in lint_staged(kb_repo.root, cfg).blocking]
    kb_repo.git("reset", "-q")
    kb_repo.stage("action-items/action-items-2026-09-28.md", "- [ ] " + "y" * 600 + "\n")
    assert [f.check for f in lint_staged(kb_repo.root, cfg).blocking] == ["mega-line"]


DAILY = "action-items/action-items-2026-09-28.md"
LEGACY_LONG = "- [ ] 🔴 **Legacy item** — " + "context " * 80  # ~670 chars, over the 500 strict limit


def _block_mode(kb_repo, budgets: str = "") -> None:
    kb_repo.write("scout-config.yaml", "kb_lint:\n  mode: block\n" + budgets)


def test_ticking_checkbox_on_long_legacy_line_passes(kb_repo) -> None:
    _block_mode(kb_repo)
    kb_repo.stage(DAILY, "# Action Items\n" + LEGACY_LONG + "\n")
    kb_repo.commit()
    kb_repo.stage(DAILY, "# Action Items\n" + LEGACY_LONG.replace("- [ ]", "- [x]", 1) + "\n")
    assert lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).blocking == []


def test_inserting_short_id_prefix_on_long_legacy_line_passes(kb_repo) -> None:
    _block_mode(kb_repo)
    kb_repo.stage(DAILY, "# Action Items\n" + LEGACY_LONG + "\n")
    kb_repo.commit()
    kb_repo.stage(DAILY, "# Action Items\n" + LEGACY_LONG.replace("- [ ] ", "- [ ] [#AB12] ", 1) + "\n")
    assert lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).blocking == []


def test_genuinely_new_long_line_still_blocks(kb_repo) -> None:
    _block_mode(kb_repo)
    kb_repo.stage(DAILY, "# Action Items\n" + LEGACY_LONG + "\n")
    kb_repo.commit()
    new_long = "- [ ] 🟡 **New item** — " + "detail " * 90
    kb_repo.stage(DAILY, "# Action Items\n" + LEGACY_LONG + "\n" + new_long + "\n")
    blocking = lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).blocking
    assert [(f.check, f.line) for f in blocking] == [("mega-line", 3)]


def _yesterday_body() -> str:
    return (
        "## 🔴 Urgent\n"
        + LEGACY_LONG
        + "\n### ⬇️ Re-tiered Sat Aug 29 (`weekend-briefing`) — 92 rows\n"
        + "".join(f"- [ ] 🟡 item {i} [[no-such-note-{i}]]\n" for i in range(20))
    )


def test_new_daily_file_only_line_checks_items_not_in_yesterdays_file(kb_repo) -> None:
    _block_mode(kb_repo)
    kb_repo.stage("action-items/action-items-2026-09-26.md", "# Older\n")
    kb_repo.stage("action-items/action-items-2026-09-27.md", "# Action Items — Sep 27\n" + _yesterday_body())
    kb_repo.commit()
    today = "# Action Items — Sep 28\n" + _yesterday_body().replace("- [ ] 🟡 item 3 ", "- [x] 🟡 item 3 ")
    kb_repo.stage(DAILY, today + "- [ ] 🟢 brand new item\n")
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    assert result.blocking == []
    # carried-forward lines are not re-checked for dangling links either
    assert result.warnings == []
    new_long = "- [ ] 🟡 **New item** — " + "detail " * 90
    kb_repo.stage(DAILY, today + new_long + "\n")
    blocking = lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).blocking
    assert [(f.check, f.line) for f in blocking] == [("mega-line", len(today.splitlines()) + 1)]


def test_new_daily_file_size_compares_against_yesterdays_file(kb_repo) -> None:
    _block_mode(kb_repo, "kb_budgets:\n  'action-items/action-items-*.md': 500\n")
    yesterday = "# Action Items — Sep 27\n" + _yesterday_body()
    assert len(yesterday.encode()) > 500
    kb_repo.stage("action-items/action-items-2026-09-27.md", yesterday)
    kb_repo.stage("action-items/action-items-2026-09-30.md", "# a later file is never the baseline\n")
    kb_repo.commit()
    cfg = load_lint_config(kb_repo.root)
    lines = yesterday.splitlines(keepends=True)
    shrunk = "".join(lines[:-2]) + "- [ ] 🟢 new\n"
    kb_repo.stage(DAILY, shrunk)
    assert lint_staged(kb_repo.root, cfg).blocking == []
    kb_repo.stage(DAILY, yesterday + "- [ ] 🟢 new\n")
    assert [f.check for f in lint_staged(kb_repo.root, cfg).blocking] == ["size"]


def test_new_daily_file_with_no_earlier_baseline_is_a_new_file(kb_repo) -> None:
    _block_mode(kb_repo)
    kb_repo.stage(DAILY, LEGACY_LONG + "\n")
    assert [f.check for f in lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).blocking] == ["mega-line"]


def test_out_of_scope_files_ignored(kb_repo) -> None:
    kb_repo.stage("SKILL.md", "## §1 · 2026-09-28 (8:0x AM, `morning-briefing`)\n" + "x" * 5000 + "\n")
    assert lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).findings == []


def test_topic_citation_and_dangling_link_are_warnings(kb_repo) -> None:
    kb_repo.stage("knowledge-base/sources/2026-09/2026-09-07-acme-daily.md", "# Acme daily\n")
    kb_repo.commit()
    kb_repo.stage(
        TOPIC,
        "# Retrieval\n\nWidget chunks PDFs.\n\nIt uses BM25 ([[2026-09-07-acme-daily]]).\n\nSee [[no-such-note]].\n",
    )
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    assert result.blocking == []
    assert sorted((f.check, f.line) for f in result.warnings) == [("citation", 3), ("dangling-link", 7)]


def test_frontmatter_alias_target_is_not_dangling(kb_repo) -> None:
    """Obsidian resolves a wikilink to a frontmatter alias, not just a filename."""
    kb_repo.stage(
        "knowledge-base/people/sam-example.md",
        '---\nname: Sam Example\naliases: [Sam Example, "Example, Sam"]\ntype: person\n---\n\n# Sam\n',
    )
    kb_repo.commit()
    kb_repo.stage(PROJECT, "Owner is [[Sam Example]], also [[sam example#Role|Sam]] and [[Example, Sam]].\n")
    assert lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).warnings == []


def test_frontmatter_block_list_alias_is_not_dangling(kb_repo) -> None:
    """The block-list form is what Obsidian's Properties editor writes."""
    kb_repo.stage(
        "knowledge-base/people/zoe-example.md",
        "---\naliases:\n  - Zoë Example\n  - 'Z. Example'\ntags: [person]\n---\n\n# Zoë\n",
    )
    kb_repo.commit()
    kb_repo.stage(PROJECT, "Reviewed by [[Zoë Example]] and [[Z. Example]].\n")
    assert lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).warnings == []


def test_frontmatter_name_or_title_alone_is_still_dangling(kb_repo) -> None:
    """`name:` / `title:` are not link targets in Obsidian, so a link that only
    matches one is broken for the reader and must keep warning."""
    kb_repo.stage("knowledge-base/people/sam.md", "---\nname: Sam Smith\ntitle: Weekly sync\n---\n\n# Sam\n")
    kb_repo.commit()
    kb_repo.stage(PROJECT, "See [[Sam Smith]] and [[Weekly sync]] and [[Nobody At All]].\n")
    warnings = lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).warnings
    assert [f.check for f in warnings] == ["dangling-link"] * 3


@pytest.mark.parametrize(
    ("frontmatter", "expected"),
    [
        ("aliases: Solo Name\n", ["Solo Name"]),
        ("aliases: Solo Name # trailing comment\n", ["Solo Name"]),
        ("aliases: ['A, B', C]\n", ["A, B", "C"]),
        ('aliases:\n- One\n- "Two"\nname: X\n', ["One", "Two"]),
        ("aliases: []\n", []),
        ("aliases: null\n", []),
        ("aliases: ~\n", []),
        ("aliases:\nname: X\n", []),
        ("meta:\n  aliases: [Nested]\n", []),
    ],
)
def test_frontmatter_aliases_forms(tmp_path, frontmatter, expected) -> None:
    f = tmp_path / "note.md"
    f.write_text(f"---\n{frontmatter}---\n\n# Note\n", encoding="utf-8")
    assert _frontmatter_aliases(f) == expected


def test_frontmatter_aliases_bom_crlf_and_late_frontmatter(tmp_path) -> None:
    bom = tmp_path / "bom.md"
    bom.write_bytes("\ufeff---\r\naliases: [Bom Name]\r\n---\r\n".encode())
    assert _frontmatter_aliases(bom) == ["Bom Name"]
    late = tmp_path / "late.md"
    late.write_text("# Title\n\n---\naliases: [Not Frontmatter]\n---\n", encoding="utf-8")
    assert _frontmatter_aliases(late) == []


def test_escaped_pipe_wikilink_in_table_is_not_dangling(kb_repo) -> None:
    kb_repo.stage("knowledge-base/people/sam.md", "# Sam\n")
    kb_repo.commit()
    kb_repo.stage(PROJECT, "| Owner |\n|---|\n| [[people/sam\\|Sam]] |\n")
    assert lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).warnings == []


def test_lint_staged_no_head(tmp_path) -> None:
    repo = tmp_path / "fresh"
    (repo / "knowledge-base").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "knowledge-base" / "a.md").write_text("x" * 20000 + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    result = lint_staged(repo, load_lint_config(repo))
    assert ("size", True) in [(f.check, f.blocking) for f in result.findings]


def test_lint_staged_in_worktree(kb_repo, tmp_path) -> None:
    wt = tmp_path / "wt"
    kb_repo.git("worktree", "add", "-q", "-b", "wt-branch", str(wt))
    (wt / "scout-config.yaml").write_text("kb_budgets:\n  'knowledge-base/projects/**': 100\n", encoding="utf-8")
    p = wt / PROJECT
    p.parent.mkdir(parents=True)
    p.write_text("x" * 200 + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", PROJECT], cwd=wt, check=True)
    assert [f.check for f in lint_staged(wt, load_lint_config(wt)).blocking] == ["size"]


def test_run_hook_report_mode_logs_and_allows(kb_repo) -> None:
    _small_budgets(kb_repo, mode="report")
    kb_repo.stage(PROJECT, "x" * 200 + "\n")
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 0
    assert "report mode" in out.getvalue()
    rows = (kb_repo.root / ".scout-logs" / "kb-lint.jsonl").read_text().splitlines()
    assert json.loads(rows[0])["check"] == "size"


def test_run_hook_block_mode_blocks(kb_repo) -> None:
    _small_budgets(kb_repo)
    kb_repo.stage(PROJECT, "x" * 200 + "\n")
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 1
    assert "Commit blocked" in out.getvalue()


def test_run_hook_override_from_worktree_logs_to_main_vault(kb_repo, tmp_path) -> None:
    wt = tmp_path / "wt"
    kb_repo.git("worktree", "add", "-q", "-b", "wt-branch", str(wt))
    (wt / "scout-config.yaml").write_text(
        "kb_lint:\n  mode: block\nkb_budgets:\n  'knowledge-base/projects/**': 100\n",
        encoding="utf-8",
    )
    p = wt / PROJECT
    p.parent.mkdir(parents=True)
    p.write_text("x" * 200 + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", PROJECT], cwd=wt, check=True)
    env = {OVERRIDE_ENV: "two fix attempts failed", "SCOUT_MODE": "morning-briefing"}
    assert run_hook(wt, env=env, out=io.StringIO()) == 0
    main_log = kb_repo.root / ".scout-logs" / "lint-overrides.log"
    assert main_log.exists()
    row = json.loads(main_log.read_text().splitlines()[0])
    assert row["reason"] == "two fix attempts failed"
    assert not (wt / ".scout-logs").exists()


def test_run_hook_override_allows_and_logs(kb_repo) -> None:
    _small_budgets(kb_repo)
    kb_repo.stage(PROJECT, "x" * 200 + "\n")
    env = {OVERRIDE_ENV: "two fix attempts failed", "SCOUT_MODE": "morning-briefing"}
    assert run_hook(kb_repo.root, env=env, out=io.StringIO()) == 0
    row = json.loads((kb_repo.root / ".scout-logs" / "lint-overrides.log").read_text().splitlines()[0])
    assert row["reason"] == "two fix attempts failed"
    assert row["mode"] == "morning-briefing"
    assert row["files"] == [PROJECT]


SESSION_SHARD = "knowledge-base/session-log/2026-09.md"
DAILY_NOTE = "action-items/backlog.md"


def _per_path(kb_repo, mode: str = "report") -> None:
    """Block in the KB except its log files; everything else gets ``mode``."""
    kb_repo.write(
        "scout-config.yaml",
        f"kb_lint:\n  mode: {mode}\n"
        "  path_modes:\n    - {glob: 'knowledge-base/**', mode: block, exclude_logs: true}\n"
        "kb_budgets:\n  'knowledge-base/projects/**': 100\n  'action-items/**': 100\n",
    )


def test_per_path_report_paths_never_block(kb_repo) -> None:
    _per_path(kb_repo)
    kb_repo.stage(SESSION_SHARD, "- " + "y" * 600 + "\n")  # mega-line in a log file
    kb_repo.stage(DAILY_NOTE, "x" * 200 + "\n")  # over budget outside the KB
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 0
    text = out.getvalue()
    assert "(mode: report, per path)" in text
    assert "[mega-line] (report)" in text and "[size] (report)" in text
    assert "report mode — not blocking" in text


def test_per_path_block_path_blocks_and_only_its_files_are_overridden(kb_repo) -> None:
    _per_path(kb_repo)
    kb_repo.stage(PROJECT, "x" * 200 + "\n")
    kb_repo.stage(SESSION_SHARD, "- " + "y" * 600 + "\n")
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 1
    text = out.getvalue()
    assert f"{PROJECT} [size] " in text  # enforced: no (report) tag
    assert "[mega-line] (report)" in text
    assert "Commit blocked." in text and "marked (report)" in text
    rows = [json.loads(r) for r in (kb_repo.root / ".scout-logs" / "kb-lint.jsonl").read_text().splitlines()]
    by_path = {r["path"]: (r["lint_mode"], r["enforced"]) for r in rows}
    assert by_path == {PROJECT: ("block", True), SESSION_SHARD: ("report", False)}

    env = {OVERRIDE_ENV: "two fix attempts failed", "SCOUT_MODE": "morning-briefing"}
    assert run_hook(kb_repo.root, env=env, out=io.StringIO()) == 0
    row = json.loads((kb_repo.root / ".scout-logs" / "lint-overrides.log").read_text().splitlines()[0])
    assert row["files"] == [PROJECT]


def test_global_block_with_a_report_rule_lets_that_path_through(kb_repo) -> None:
    kb_repo.write(
        "scout-config.yaml",
        "kb_lint:\n  mode: block\n  path_modes:\n    - {glob: 'action-items/**', mode: report}\n"
        "kb_budgets:\n  'action-items/**': 100\n  'knowledge-base/projects/**': 100\n",
    )
    kb_repo.stage(DAILY_NOTE, "x" * 200 + "\n")
    assert run_hook(kb_repo.root, env={}, out=io.StringIO()) == 0
    kb_repo.stage(PROJECT, "x" * 200 + "\n")
    assert run_hook(kb_repo.root, env={}, out=io.StringIO()) == 1


def test_warnings_never_block_on_a_block_path(kb_repo) -> None:
    _per_path(kb_repo)
    kb_repo.stage(PROJECT, "see [[nowhere]]\n")
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 0
    assert "[dangling-link]" in out.getvalue()


def test_a_new_check_plugs_into_the_registry_and_its_path_mode(kb_repo, monkeypatch) -> None:
    from scout.kb import lint

    def loose_file(f: lint.StagedFile) -> list[lint.Finding]:
        return [lint.Finding(f.rel, "loose-file", True, "loose file")]

    monkeypatch.setattr(lint, "STAGED_CHECKS", (*lint.STAGED_CHECKS, loose_file))
    kb_repo.write(
        "scout-config.yaml",
        "kb_lint:\n  mode: block\n  path_modes:\n    - {glob: '**', mode: report, checks: [loose-file]}\n",
    )
    kb_repo.stage(PROJECT, "fine\n")
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 0
    assert "[loose-file] (report)" in out.getvalue()


def test_run_hook_clean_commit_writes_nothing(kb_repo) -> None:
    kb_repo.stage(PROJECT, "fine\n")
    assert run_hook(kb_repo.root, env={}, out=io.StringIO()) == 0
    assert not (kb_repo.root / ".scout-logs" / "kb-lint.jsonl").exists()


def test_override_count_window(tmp_path) -> None:
    now = datetime(2026, 9, 28, 12, tzinfo=UTC)
    log = tmp_path / "lint-overrides.log"
    log.write_text(
        json.dumps({"ts": (now - timedelta(days=1)).isoformat()})
        + "\n"
        + json.dumps({"ts": (now - timedelta(days=20)).isoformat()})
        + "\n"
        + "not json\n",
        encoding="utf-8",
    )
    assert override_count(tmp_path, days=14, now=now) == 1
    assert override_count(tmp_path / "missing", days=14, now=now) == 0


def test_over_budget_ranks_by_excess(kb_repo) -> None:
    from scout.kb.lint import over_budget

    kb_repo.write("scout-config.yaml", "kb_budgets:\n  'knowledge-base/**': 100\n")
    kb_repo.write("knowledge-base/a.md", "x" * 150)
    kb_repo.write("knowledge-base/b.md", "x" * 900)
    kb_repo.write("knowledge-base/c.md", "x" * 50)
    kb_repo.write("SKILL.md", "x" * 9000)
    rows = over_budget(kb_repo.root, load_lint_config(kb_repo.root))
    assert rows == [("knowledge-base/b.md", 900, 100), ("knowledge-base/a.md", 150, 100)]


def test_over_budget_skips_report_excluded_dated_records(kb_repo) -> None:
    """Past daily files, the legacy session log and the topic board's files are never shrink targets."""
    from scout.kb.lint import over_budget

    kb_repo.write("scout-config.yaml", "kb_budgets:\n  'knowledge-base/**': 100\n  'action-items/**': 100\n")
    kb_repo.write("action-items/action-items-2026-09-01.md", "x" * 5000)
    kb_repo.write("knowledge-base/session-log.md", "x" * 9000)
    for board_file in ("topics.md", "topic-names.md", "dismissed.md"):
        kb_repo.write(f"action-items/{board_file}", "- x\n" * 2000)
    kb_repo.write("knowledge-base/a.md", "x" * 150)
    rows = over_budget(kb_repo.root, load_lint_config(kb_repo.root))
    assert rows == [("knowledge-base/a.md", 150, 100)]


DAILY_REL = "action-items/action-items-2026-10-06.md"
DAILY_HEAD = "# Action Items\n\n## 🔴 Urgent\n\n"


def _ctx_findings(kb_repo) -> list[tuple[str, int | None]]:
    res = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    return [(f.check, f.line) for f in res.findings if f.check == "missing-context-note"]


def test_new_open_item_without_note_warns(kb_repo) -> None:
    kb_repo.stage(DAILY_REL, DAILY_HEAD + "- [ ] [#DETX] **Order the items**\n")
    res = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    found = [f for f in res.findings if f.check == "missing-context-note"]
    assert len(found) == 1 and found[0].blocking is False and "action-items/context/DETX.md" in found[0].message


def test_staged_note_silences_warning(kb_repo) -> None:
    kb_repo.stage("action-items/context/DETX.md", "---\ntag: DETX\n---\n\nctx\n")
    kb_repo.stage(DAILY_REL, DAILY_HEAD + "- [ ] [#DETX] **Order the items**\n")
    assert _ctx_findings(kb_repo) == []


def test_committed_note_silences_warning(kb_repo) -> None:
    kb_repo.stage("action-items/context/DETX.md", "ctx\n")
    kb_repo.commit()
    kb_repo.stage(DAILY_REL, DAILY_HEAD + "- [ ] [#DETX] **Order the items**\n")
    assert _ctx_findings(kb_repo) == []


def test_untagged_and_done_lines_are_ignored(kb_repo) -> None:
    kb_repo.stage(DAILY_REL, DAILY_HEAD + "- [ ] **No tag**\n- [x] [#PLN] **Done**\n")
    assert _ctx_findings(kb_repo) == []


def test_carried_forward_line_is_not_rechecked(kb_repo) -> None:
    kb_repo.stage("action-items/action-items-2026-10-05.md", DAILY_HEAD + "- [ ] [#DETX] **Order the items**\n")
    kb_repo.commit()
    kb_repo.stage(DAILY_REL, DAILY_HEAD + "- [ ] [#DETX] **Order the items**\n")
    assert _ctx_findings(kb_repo) == []


def test_missing_note_never_blocks_in_block_mode(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_lint:\n  mode: block\n")
    kb_repo.stage(DAILY_REL, DAILY_HEAD + "- [ ] [#DETX] **Order the items**\n")
    res = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    assert all(f.check != "missing-context-note" for f in res.blocking)


def test_non_daily_files_are_not_checked(kb_repo) -> None:
    kb_repo.stage("action-items/backlog.md", "- [ ] [#DETX] **Backlog item**\n")
    assert _ctx_findings(kb_repo) == []
