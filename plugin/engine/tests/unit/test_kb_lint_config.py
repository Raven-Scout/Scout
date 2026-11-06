"""Tests for scout.kb.lint_config — budgets, scopes and mode resolution."""

from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from scout.config import user_value
from scout.kb import lint_config
from scout.kb.lint_config import glob_match, in_privacy_scope, load_lint_config


def test_glob_single_star_does_not_cross_slash() -> None:
    assert glob_match("knowledge-base/projects/*/*.md", "knowledge-base/projects/widget/widget.md")
    assert not glob_match("knowledge-base/projects/*/*.md", "knowledge-base/projects/widget/ref/x.md")


def test_glob_double_star_matches_any_depth() -> None:
    assert glob_match("knowledge-base/topics/**", "knowledge-base/topics/widget/retrieval.md")
    assert glob_match("**/extracted/**", "knowledge-base/personal/pet-records/extracted/doc1.md")


def test_defaults_budgets(tmp_path: Path) -> None:
    cfg = load_lint_config(tmp_path)
    assert cfg.mode == "report"
    assert cfg.budget_for("action-items/action-items-2026-09-28.md") == 61440
    assert cfg.budget_for("knowledge-base/sources/2026-09/2026-09-07-acme-daily.md") == 8192
    assert cfg.budget_for("knowledge-base/topics/widget/retrieval.md") == 10240
    assert cfg.budget_for("knowledge-base/projects/acme-pilot/acme-pilot.md") == 15360
    assert cfg.budget_for("knowledge-base/session-log/2026-09.md") == 98304
    assert cfg.budget_for("knowledge-base/people/sam.md") == 10240
    # The digest is a section of the daily file again (R2): no digests/ budget.
    assert cfg.budget_for("action-items/digests/2026-09-28.md") == 15360


def test_legacy_session_log_has_no_budget_and_stays_a_log(tmp_path: Path) -> None:
    """An un-migrated vault's multi-MB session-log.md must not block every run."""
    cfg = load_lint_config(tmp_path)
    assert cfg.budget_for("knowledge-base/session-log.md") is None
    assert cfg.is_log("knowledge-base/session-log.md")


def test_kg_audit_snapshots_are_logs_without_a_budget(tmp_path: Path) -> None:
    cfg = load_lint_config(tmp_path)
    assert cfg.is_log("knowledge-base/kg-audits/2026-09-28.md")
    assert cfg.budget_for("knowledge-base/kg-audits/2026-09-28.md") is None


def test_scope_and_exclusions(tmp_path: Path) -> None:
    cfg = load_lint_config(tmp_path)
    assert cfg.in_scope("knowledge-base/projects/widget/widget.md")
    assert cfg.in_scope("action-items/action-items-2026-09-28.md")
    assert not cfg.in_scope("SKILL.md")
    assert not cfg.in_scope("docs/superpowers/plans/x.md")
    assert not cfg.in_scope("knowledge-base/projects/acme-pilot/sprint-brief-lanes.csv")
    assert not cfg.in_scope("action-items/archive/action-items-2026-08-01.md")
    assert not cfg.in_scope("knowledge-base/personal/pet-records/extracted/doc1.md")


def test_logs_topics_and_line_limits(tmp_path: Path) -> None:
    cfg = load_lint_config(tmp_path)
    assert cfg.is_log("knowledge-base/session-log.md")
    assert cfg.is_log("knowledge-base/scout-mistake-audit/pattern-217-referent.md")
    assert not cfg.is_log("action-items/digests/2026-09-28.md")
    assert not cfg.is_log("knowledge-base/projects/acme-pilot/acme-pilot.md")
    assert not cfg.is_log("action-items/action-items-2026-09-28.md")
    assert cfg.is_topic("knowledge-base/topics/widget/retrieval.md")
    assert cfg.line_limit("action-items/action-items-2026-09-28.md") == 500
    assert cfg.line_limit("knowledge-base/projects/widget/widget.md") == 1500


def test_vault_overrides_mode_and_budgets(tmp_path: Path) -> None:
    (tmp_path / "scout-config.yaml").write_text(
        "kb_lint:\n  mode: block\nkb_budgets:\n  'knowledge-base/topics/**': 100\n",
        encoding="utf-8",
    )
    cfg = load_lint_config(tmp_path)
    assert cfg.mode == "block"
    assert cfg.budget_for("knowledge-base/topics/widget/retrieval.md") == 100
    # defaults still present after the override
    assert cfg.budget_for("knowledge-base/sources/2026-09/x.md") == 8192


def test_invalid_mode_falls_back_to_report(tmp_path: Path) -> None:
    (tmp_path / "scout-config.yaml").write_text("kb_lint:\n  mode: strict\n", encoding="utf-8")
    assert load_lint_config(tmp_path).mode == "report"


def test_default_report_excludes(tmp_path: Path) -> None:
    cfg = load_lint_config(tmp_path)
    assert cfg.report_excluded("action-items/action-items-2026-09-28.md")
    assert cfg.report_excluded("knowledge-base/session-log.md")
    assert not cfg.report_excluded("knowledge-base/session-log/2026-09.md")
    assert not cfg.report_excluded("knowledge-base/projects/acme-pilot/acme-pilot.md")


def test_null_budget_means_unlimited(tmp_path: Path) -> None:
    (tmp_path / "scout-config.yaml").write_text("kb_budgets:\n  'knowledge-base/big/**': null\n", encoding="utf-8")
    assert load_lint_config(tmp_path).budget_for("knowledge-base/big/x.md") is None


def _path_modes(tmp_path: Path, block: str) -> None:
    (tmp_path / "scout-config.yaml").write_text("kb_lint:\n" + block, encoding="utf-8")


def test_global_mode_alone_applies_to_every_path(tmp_path: Path) -> None:
    _path_modes(tmp_path, "  mode: block\n")
    cfg = load_lint_config(tmp_path)
    assert cfg.path_modes == ()
    assert cfg.mode_for("knowledge-base/projects/widget/widget.md", "size") == "block"
    assert cfg.mode_for("action-items/action-items-2026-09-28.md", "mega-line") == "block"
    assert cfg.mode_label == "block"


def test_overlapping_globs_first_match_wins(tmp_path: Path) -> None:
    _path_modes(
        tmp_path,
        "  mode: block\n"
        "  path_modes:\n"
        "    - {glob: 'knowledge-base/projects/widget/**', mode: report}\n"
        "    - {glob: 'knowledge-base/**', mode: block}\n"
        "    - {glob: 'knowledge-base/projects/**', mode: report}\n"
        "    - {glob: 'action-items/**', mode: report}\n",
    )
    cfg = load_lint_config(tmp_path)
    # The narrow rule listed first wins over the broad one after it ...
    assert cfg.mode_for("knowledge-base/projects/widget/widget.md", "size") == "report"
    # ... and a narrower rule listed AFTER a broad match never applies (first match, not most specific).
    assert cfg.mode_for("knowledge-base/projects/acme-pilot/acme-pilot.md", "size") == "block"
    assert cfg.mode_for("action-items/action-items-2026-09-28.md", "mega-line") == "report"
    # No rule matches: the global mode.
    assert cfg.mode_for("drafts/note.md", "size") == "block"
    assert cfg.mode_label == "block, per path"


def test_excluded_path_falls_through_to_the_next_rule(tmp_path: Path) -> None:
    _path_modes(
        tmp_path,
        "  mode: report\n"
        "  path_modes:\n"
        "    - glob: 'knowledge-base/**'\n"
        "      mode: block\n"
        "      exclude: ['knowledge-base/projects/*/archive/**', 'knowledge-base/inbox/**']\n"
        "    - {glob: 'knowledge-base/inbox/**', mode: block, checks: [size]}\n",
    )
    cfg = load_lint_config(tmp_path)
    assert cfg.mode_for("knowledge-base/topics/widget/retrieval.md", "size") == "block"
    # Excluded, and no later rule matches: the global mode.
    assert cfg.mode_for("knowledge-base/projects/widget/archive/old.md", "size") == "report"
    # Excluded from rule 1, caught by rule 2 for the check it names only.
    assert cfg.mode_for("knowledge-base/inbox/a.md", "size") == "block"
    assert cfg.mode_for("knowledge-base/inbox/a.md", "mega-line") == "report"


def test_exclude_logs_leaves_log_files_to_later_rules(tmp_path: Path) -> None:
    _path_modes(
        tmp_path,
        "  mode: report\n  path_modes:\n    - {glob: 'knowledge-base/**', mode: block, exclude_logs: true}\n",
    )
    cfg = load_lint_config(tmp_path)
    assert cfg.mode_for("knowledge-base/projects/widget/widget.md", "mega-line") == "block"
    for log in (
        "knowledge-base/session-log/2026-09.md",
        "knowledge-base/session-log.md",
        "knowledge-base/projects/widget/decision-log.md",
        "knowledge-base/kg-audits/2026-09-28.md",
    ):
        assert cfg.mode_for(log, "mega-line") == "report", log
    assert cfg.mode_for("action-items/action-items-2026-09-28.md", "size") == "report"


def test_a_rule_scoped_to_checks_covers_only_those(tmp_path: Path) -> None:
    _path_modes(
        tmp_path,
        "  mode: block\n  path_modes:\n    - {glob: '**', mode: report, checks: [future-check]}\n",
    )
    cfg = load_lint_config(tmp_path)
    assert cfg.mode_for("knowledge-base/x.md", "future-check") == "report"
    assert cfg.mode_for("knowledge-base/x.md", "size") == "block"


def test_bad_path_mode_entries(tmp_path: Path) -> None:
    _path_modes(
        tmp_path,
        "  mode: report\n"
        "  path_modes:\n"
        "    - {glob: 'knowledge-base/a/**', mode: strict}\n"  # a typo reports, never blocks
        "    - {mode: block}\n"  # no glob: skipped
        "    - not-a-rule\n"
        "    - {glob: 'knowledge-base/**', mode: block, exclude: 'knowledge-base/b/**'}\n",
    )
    cfg = load_lint_config(tmp_path)
    assert len(cfg.path_modes) == 2
    assert cfg.mode_for("knowledge-base/a/x.md", "size") == "report"
    assert cfg.mode_for("knowledge-base/b/x.md", "size") == "report"  # a scalar exclude works
    assert cfg.mode_for("knowledge-base/c/x.md", "size") == "block"


def test_path_modes_that_is_not_a_list_is_ignored(tmp_path: Path) -> None:
    _path_modes(tmp_path, "  mode: block\n  path_modes: {knowledge-base/**: report}\n")
    cfg = load_lint_config(tmp_path)
    assert cfg.path_modes == ()
    assert cfg.mode_for("knowledge-base/x.md", "size") == "block"


# ----- a config mistake says so -----------------------------------------------


def test_a_good_config_warns_about_nothing(tmp_path: Path) -> None:
    assert load_lint_config(tmp_path).warnings == ()
    _path_modes(
        tmp_path,
        "  mode: block\n  path_modes:\n"
        "    - {glob: 'knowledge-base/a/**', mode: report}\n"
        "    - glob: 'knowledge-base/**'\n"
        "      mode: block\n"
        "      exclude: ['knowledge-base/b/**']\n"
        "      exclude_logs: true\n"
        "      checks: [mega-line, diary]\n",
    )
    assert load_lint_config(tmp_path).warnings == ()


def test_an_empty_path_modes_warns_about_nothing(tmp_path: Path) -> None:
    _path_modes(tmp_path, "  path_modes:\n")
    assert load_lint_config(tmp_path).warnings == ()


@pytest.mark.parametrize("mode", ["Block", "true", "[block]", "strict"])
def test_a_bad_global_mode_warns_and_reports(tmp_path: Path, mode: str) -> None:
    _path_modes(tmp_path, f"  mode: {mode}\n")
    cfg = load_lint_config(tmp_path)
    assert cfg.mode == "report"
    assert len(cfg.warnings) == 1 and "not report or block" in cfg.warnings[0]


@pytest.mark.parametrize("field", ["mode: Block", "mode: true", "mode: [block]", "other: 1"])
def test_a_bad_rule_mode_warns_and_reports(tmp_path: Path, field: str) -> None:
    _path_modes(tmp_path, f"  path_modes:\n    - {{glob: 'knowledge-base/**', {field}}}\n")
    cfg = load_lint_config(tmp_path)
    assert cfg.mode_for("knowledge-base/x.md", "size") == "report"
    assert any("not report or block" in w for w in cfg.warnings)


@pytest.mark.parametrize(
    ("rule", "says"),
    [
        ("{globs: 'knowledge-base/**', mode: block}", "unknown key"),
        ("{glob: [a, b], mode: block}", "not a path glob"),
        ("{glob: '', mode: block}", "not a path glob"),
        ("{glob: 5, mode: block}", "not a path glob"),
        ("not-a-rule", "not a rule"),
        # Each of these would otherwise widen the rule, so it goes rather than guesses.
        ("{glob: 'knowledge-base/**', mode: block, checks: 5}", "not a list of check ids"),
        ("{glob: 'knowledge-base/**', mode: block, checks: {a: b}}", "not a list of check ids"),
        ("{glob: 'knowledge-base/**', mode: block, checks: []}", "not a list of check ids"),
        ("{glob: 'knowledge-base/**', mode: block, checks: [[mega-line]]}", "not a list of check ids"),
        ("{glob: 'knowledge-base/**', mode: block, exclude: 5}", "not a list of globs"),
        ("{glob: 'knowledge-base/**', mode: block, exclude: [5]}", "not a list of globs"),
        ("{glob: 'knowledge-base/**', mode: block, exclude_logs: 'false'}", "not true or false"),
        ("{glob: 'knowledge-base/**', mode: block, exclude_logs: 1}", "not true or false"),
    ],
)
def test_a_rule_that_cant_be_read_is_dropped_with_a_warning(tmp_path: Path, rule: str, says: str) -> None:
    _path_modes(tmp_path, f"  mode: report\n  path_modes:\n    - {rule}\n")
    cfg = load_lint_config(tmp_path)
    assert cfg.path_modes == ()
    assert cfg.mode_for("knowledge-base/x.md", "size") == "report"
    assert any(says in w for w in cfg.warnings), cfg.warnings


def test_an_unknown_key_warns_and_keeps_a_good_rule(tmp_path: Path) -> None:
    _path_modes(tmp_path, "  path_modes:\n    - {glob: 'knowledge-base/**', mode: block, excludes: [a]}\n")
    cfg = load_lint_config(tmp_path)
    assert cfg.mode_for("knowledge-base/x.md", "size") == "block"
    assert len(cfg.warnings) == 1 and "'excludes'" in cfg.warnings[0]


def test_an_unknown_check_warns_and_never_matches(tmp_path: Path) -> None:
    _path_modes(tmp_path, "  path_modes:\n    - {glob: 'knowledge-base/**', mode: block, checks: [megaline]}\n")
    cfg = load_lint_config(tmp_path)
    assert cfg.mode_for("knowledge-base/x.md", "mega-line") == "report"
    assert len(cfg.warnings) == 1 and "unknown check 'megaline'" in cfg.warnings[0]


@pytest.mark.parametrize("check", ["private-link", "citation"])
def test_a_check_no_mode_changes_warns(tmp_path: Path, check: str) -> None:
    _path_modes(tmp_path, f"  path_modes:\n    - {{glob: 'knowledge-base/**', mode: report, checks: [{check}]}}\n")
    assert "no mode changes" in load_lint_config(tmp_path).warnings[0]


@pytest.mark.parametrize("glob", ["./knowledge-base/**", "/knowledge-base/**"])
def test_a_rooted_glob_warns(tmp_path: Path, glob: str) -> None:
    _path_modes(tmp_path, f"  path_modes:\n    - {{glob: '{glob}', mode: block}}\n")
    assert "vault-relative" in load_lint_config(tmp_path).warnings[0]


def test_path_modes_as_a_mapping_warns(tmp_path: Path) -> None:
    _path_modes(tmp_path, "  path_modes: {knowledge-base/**: block}\n")
    assert "not a list of rules" in load_lint_config(tmp_path).warnings[0]


def test_a_rule_matching_no_linted_file_is_named(tmp_path: Path) -> None:
    _path_modes(
        tmp_path,
        "  path_modes:\n"
        "    - {glob: 'Knowledge-Base/**', mode: block}\n"
        "    - {glob: 'drafts/**', mode: block}\n"
        "    - {glob: 'knowledge-base/**', mode: block}\n",
    )
    problems = load_lint_config(tmp_path).rules_matching_nothing(["knowledge-base/a.md", "drafts/b.md"])
    assert [p.split("'")[1] for p in problems] == ["Knowledge-Base/**", "drafts/**"]


# ----- a misspelt or misshapen kb_lint ------------------------------------------


@pytest.mark.parametrize(
    ("config", "said"),
    [
        ("kb_lint:\n  path_mode: []\n", "unknown key(s) ['path_mode']"),
        ("kb_lint:\n  pathmodes: []\n", "unknown key(s) ['pathmodes']"),
        ("kb-lint:\n  mode: block\n", "top-level key 'kb-lint'"),
        ("KB_Lint:\n  mode: block\n", "top-level key 'KB_Lint'"),
        ("kb_lint: nonsense\n", "kb_lint is a str, not a mapping"),
        ("kb_lint: [a]\n", "kb_lint is a list, not a mapping"),
        ("kb_lint: []\n", "kb_lint is a list, not a mapping"),
    ],
)
def test_a_misspelt_or_misshapen_kb_lint_is_named(tmp_path: Path, config: str, said: str) -> None:
    (tmp_path / "scout-config.yaml").write_text(config, encoding="utf-8")
    cfg = load_lint_config(tmp_path)
    assert [w for w in cfg.warnings if said in w]
    assert cfg.mode == "report" and cfg.budgets  # the defaults still apply


def test_an_empty_kb_lint_section_warns_about_nothing(tmp_path: Path) -> None:
    (tmp_path / "scout-config.yaml").write_text("kb_lint:\n", encoding="utf-8")
    assert load_lint_config(tmp_path).warnings == ()


def test_user_value_reads_the_vaults_own_file(tmp_path: Path) -> None:
    config = tmp_path / "scout-config.yaml"
    assert user_value("kb_lint", tmp_path) is None
    config.write_text("kb_lint: nonsense\n", encoding="utf-8")
    assert user_value("kb_lint", tmp_path) == "nonsense"
    assert user_value("absent", tmp_path) is None
    config.write_text("kb_lint: [one\n", encoding="utf-8")
    assert user_value("kb_lint", tmp_path) is None


# ----- a long glob is a glob like any other (#565) -------------------------------

LONG_GLOB = "**a" * 10 + "**b"  # 22 `*`: kb-lint used to drop any glob over 6
LONG_PATH = "knowledge-base/" + "a" * 60 + "/notb.md"


def test_a_long_path_modes_glob_applies_and_returns_at_once(tmp_path: Path) -> None:
    _path_modes(tmp_path, f"  path_modes:\n    - {{glob: '{LONG_GLOB}.md', mode: block}}\n")
    cfg = load_lint_config(tmp_path)
    assert cfg.warnings == ()
    started = time.monotonic()
    assert cfg.mode_for(LONG_PATH, "mega-line") == "block"
    assert cfg.mode_for("knowledge-base/" + "a" * 60 + "/keep.md", "mega-line") == "report"
    assert time.monotonic() - started < 1


def test_a_long_exclude_in_a_rule_sends_the_path_on(tmp_path: Path) -> None:
    rule = f"{{glob: 'knowledge-base/**', mode: block, exclude: ['{LONG_GLOB}.md']}}"
    _path_modes(tmp_path, f"  path_modes:\n    - {rule}\n")
    cfg = load_lint_config(tmp_path)
    assert cfg.mode_for(LONG_PATH, "mega-line") == "report"
    assert cfg.mode_for("knowledge-base/keep.md", "mega-line") == "block"


def test_a_long_glob_means_the_same_in_kb_lint_as_in_privacy(tmp_path: Path) -> None:
    """Privacy and sync call glob_match; kb-lint's lists now do too (test_kb_privacy has the labels)."""
    glob, rel = "**/x/**/y/**/z/**", "knowledge-base/x/y/z/note.md"
    assert glob_match(glob, rel)
    assert in_privacy_scope("w/x/y/z/note.txt", (glob,))
    _path_modes(tmp_path, f"  path_modes:\n    - {{glob: '{glob}', mode: block}}\n")
    assert load_lint_config(tmp_path).mode_for(rel, "mega-line") == "block"


def test_a_long_budget_glob_applies(tmp_path: Path) -> None:
    (tmp_path / "scout-config.yaml").write_text(f"kb_budgets:\n  '{LONG_GLOB}.md': 100\n", encoding="utf-8")
    cfg = load_lint_config(tmp_path)
    assert cfg.warnings == ()
    assert cfg.budget_for(LONG_PATH) == 100


@pytest.mark.parametrize(
    ("key", "applies"),
    [
        ("exclude_globs", lambda cfg, rel: not cfg.in_scope(rel)),
        ("report_exclude_globs", lambda cfg, rel: cfg.report_excluded(rel)),
        ("log_globs", lambda cfg, rel: cfg.is_log(rel)),
        ("topic_globs", lambda cfg, rel: cfg.is_topic(rel)),
        ("strict_line_globs", lambda cfg, rel: cfg.line_limit(rel) == cfg.max_line_chars_strict),
    ],
)
def test_a_long_glob_in_each_lint_list_applies(tmp_path: Path, key: str, applies) -> None:
    _path_modes(tmp_path, f"  {key}: ['{LONG_GLOB}.md']\n")
    cfg = load_lint_config(tmp_path)
    assert cfg.warnings == ()
    assert applies(cfg, LONG_PATH)
    assert not applies(cfg, "knowledge-base/" + "a" * 60 + "/keep.md")


# ----- the check ids ------------------------------------------------------------


def _finding_ids(module: str) -> set[str]:
    """The literal check id of every ``Finding(path, "id", ...)`` in a scout.kb module."""
    tree = ast.parse((Path(lint_config.__file__).parent / f"{module}.py").read_text(encoding="utf-8"))
    return {
        node.args[1].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "Finding"
        and isinstance(node.args[1], ast.Constant)
    }


def test_every_finding_uses_a_known_check_id() -> None:
    from scout.kb.lint_privacy import LABEL_WARNINGS

    ids = _finding_ids("lint") | _finding_ids("lint_privacy") | set(LABEL_WARNINGS)
    assert {"size", "mega-line", "diary", "citation", "privacy-widening"} <= ids
    assert ids <= lint_config.KNOWN_CHECKS, ids - lint_config.KNOWN_CHECKS
