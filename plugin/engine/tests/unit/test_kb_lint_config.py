"""Tests for scout.kb.lint_config — budgets, scopes and mode resolution."""

from __future__ import annotations

from pathlib import Path

from scout.kb.lint_config import glob_match, load_lint_config


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
    # A path-only lookup skips check-scoped rules.
    assert cfg.mode_for("knowledge-base/x.md") == "block"


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
