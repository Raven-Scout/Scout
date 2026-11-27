"""Layout v2's callers at their edges (spec 2026-11-26 §4.3, §6, §7): deferral surfaces, strictest labels,
space registry errors and the push lines."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import config as scout_config
from scout.cli import app
from scout.errors import ConfigError
from scout.kb import privacy
from scout.kb.privacy import Label
from tests.unit.layout_v1_vault import build_v1_vault

SPACES = "spaces:\n  - key: work\n    default: true\n  - key: personal\n    visibility: private\n"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


def _run(*args: str) -> tuple[int, str]:
    res = CliRunner().invoke(app, list(args))
    return res.exit_code, res.output


# ---- bootstrap surfaces ---------------------------------------------------------------------------------


def test_bootstrap_upgrade_reports_a_deferred_move(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = build_v1_vault(tmp_path / "v", spaces=SPACES, dirty=False)
    (vault / "SKILL.md.proposed-merge").write_text("<<<<<<<\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    code, out = _run("bootstrap", "upgrade", "--no-jobs", "--json")
    assert code == 0, out
    payload = json.loads(out)
    assert payload["action"] == "deferred" and payload["migration"]["state"] == "deferred"
    assert "the layout move is deferred" in payload["error"]
    assert (vault / "SKILL.md").is_file() and not (vault / "scout/brain").exists()


def test_migrate_legacy_refuses_a_v1_legacy_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = tmp_path / "legacy"
    (vault / ".scout-state").mkdir(parents=True)
    (vault / "SKILL.md").write_text("# SKILL\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    code, out = _run("bootstrap", "migrate-legacy", "--user-name", "Alex", "--user-email", "alex@example.com")
    assert code == 2 and "legacy vault in the old layout" in out


def test_the_plan_names_unknown_root_entries_and_other_work_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout import layout_migrate

    vault = build_v1_vault(tmp_path / "v", spaces=SPACES, dirty=False)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    real = layout_migrate.plan

    def with_extras(v: Path) -> layout_migrate.Plan:
        p = real(v)
        p.unknown_root = ["notes-of-mine"]
        p.worktrees = ["/elsewhere/wt"]
        return p

    monkeypatch.setattr(layout_migrate, "plan", with_extras)
    code, out = _run("vault", "migrate", "--plan")
    assert code == 0
    assert "unknown root entries): notes-of-mine" in out and "old layout: /elsewhere/wt" in out


def test_stage_migrations_raises_on_a_deferral(tmp_path: Path) -> None:
    from scout.scripts import bootstrap

    vault = build_v1_vault(tmp_path / "v", spaces=SPACES, dirty=False)
    (vault / "SKILL.md.proposed-merge").write_text("x\n")
    cfg = bootstrap.config_from_vault(
        vault, plugin_root=Path(__file__).resolve().parents[3], plugin_version="0.19.0", skip_jobs=True
    )
    with pytest.raises(bootstrap.LayoutDeferred) as e:
        bootstrap._stage_migrations(cfg)
    assert "sidecar" in e.value.reason and e.value.outcome.state == "deferred"  # type: ignore[attr-defined]


def test_the_readme_stage_leaves_a_link_an_unreadable_or_a_users_readme(tmp_path: Path) -> None:
    from scout import layout
    from scout.scripts import bootstrap

    vault = build_v1_vault(tmp_path / "v", spaces=SPACES, git=False)
    cfg = bootstrap.config_from_vault(
        vault, plugin_root=Path(__file__).resolve().parents[3], plugin_version="0.19.0", skip_jobs=True
    )
    readme = vault / "README.md"
    readme.write_text("# Mine\n")
    bootstrap._stage_readme(cfg)
    assert readme.read_text() == "# Mine\n"
    readme.write_bytes(b"\xff\xfe")
    bootstrap._stage_readme(cfg)
    assert readme.read_bytes() == b"\xff\xfe"
    readme.unlink()
    (tmp_path / "elsewhere.md").write_text("x")
    readme.symlink_to(tmp_path / "elsewhere.md")
    bootstrap._stage_readme(cfg)
    assert (tmp_path / "elsewhere.md").read_text() == "x"
    readme.unlink()
    bootstrap._stage_readme(cfg)
    assert readme.read_text().startswith(layout.README_MARKER)


# ---- self-update push lines ---------------------------------------------------------------------------


def test_self_update_pushes_once_for_an_applied_move_and_once_per_deferral_reason() -> None:
    from scout import update_outcomes as uo
    from scout.scripts import self_update_run as su
    from tests.unit.self_update_fakes import NOW

    def row(result: str, detail: str) -> uo.UpdateOutcome:
        return uo.UpdateOutcome(
            at=NOW,
            from_version="0.18.0",
            target="0.19.0",
            managed_by="install.sh",
            result=result,
            step="vault",
            detail=detail,
        )

    assert su._migration_detail(None) == ""
    applied = su._migration_detail({"state": "applied", "summary": {"moved": 7}, "report": "reports/x.md"})
    assert applied == f"{su.LAYOUT_APPLIED_MARK}7 files moved; report reports/x.md"
    pushes = su.info_pushes(row("applied", applied), [])
    layout_push = next(p for p in pushes if p.finding_id == "update.layout")
    assert "7 files moved" in layout_push.body and "reports/x.md" in layout_push.body
    waiting = row("deferred", f"{su.LAYOUT_DEFERRED_MARK}a brain sidecar is pending")
    (push,) = su.info_pushes(waiting, [])
    assert push.finding_id == "update.layout_deferred" and "a brain sidecar is pending" in push.body
    assert su.info_pushes(waiting, [waiting]) == []  # the same reason again: no second push
    assert len(su.info_pushes(row("deferred", f"{su.LAYOUT_DEFERRED_MARK}other"), [waiting])) == 1


# ---- privacy and the space registry ----------------------------------------------------------------------


def _label(**kw: object) -> Label:
    base: dict[str, object] = {"path": "x.md", "visibility": "normal", "visibility_source": "default"}
    base.update(kw)
    return Label(**base)  # type: ignore[arg-type]


def test_strictest_narrows_scopes_ceilings_and_delegates() -> None:
    with pytest.raises(ValueError):
        privacy.strictest([], path="x.md")
    a = _label(federation_scope="org", ceiling="org", delegate_scope=("d1", "d2"))
    b = _label(federation_scope=("team-a", "team-b"), ceiling="people", delegate_scope=("d2",))
    c = _label(federation_scope=("team-b",), ceiling="org", visibility="private", visibility_source="frontmatter")
    out = privacy.strictest([a, b, c], path="people/alex.md")
    assert out.path == "people/alex.md" and out.visibility == "private"
    assert out.federation_scope == ("team-b",)
    assert out.ceiling == "people"
    assert out.delegate_scope == ("d2",)
    assert privacy._narrower_scope(("x",), "org") == ("x",)
    assert privacy._narrower_scope(("x",), ("y",)) == "none"
    assert privacy._narrower_scope("none", "org") == "none"


def test_an_invalid_space_registry_fails_closed() -> None:
    bad = privacy._spaces_or_invalid([{"default": True}])
    assert bad is privacy._INVALID
    policy = privacy.with_spaces(privacy.policy_from_overlay(None), bad)
    assert "spaces-invalid" in policy.warnings
    assert privacy.classify("knowledge-base/club/topics/x.md", None, policy).visibility == "private"
    assert privacy.classify("knowledge-base/people/alex.md", None, policy).visibility == "normal"


def test_space_registry_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must be a mapping"):
        scout_config.parse_spaces(["work"])
    with pytest.raises(ConfigError, match="true or false"):
        scout_config.parse_spaces([{"key": "work", "default": "yes"}])
    (tmp_path / "scout-config.yaml").write_text("spaces:\n  - work\n")
    assert scout_config.load_spaces(tmp_path) == scout_config.DEFAULT_SPACES


def test_a_layout_deferral_whose_count_changed_is_the_same_reason() -> None:
    from scout import update_outcomes as uo
    from scout.scripts import self_update_run as su
    from tests.unit.self_update_fakes import NOW

    def r(detail: str) -> uo.UpdateOutcome:
        return uo.UpdateOutcome(
            at=NOW,
            from_version="0.18.0",
            target="0.19.0",
            managed_by="install.sh",
            result="deferred",
            step="vault",
            detail=f"{su.LAYOUT_DEFERRED_MARK}{detail}",
        )

    assert su.info_pushes(r("3 files are unmerged"), [r("2 files are unmerged")]) == []


def test_retitle_finds_the_commit_helper_on_either_layout(tmp_path: Path) -> None:
    from scout.action_items import retitle

    v1 = tmp_path / "v1"
    v1.mkdir()
    (v1 / "SKILL.md").write_text("# s\n")
    v2 = tmp_path / "v2"
    (v2 / "scout" / "brain").mkdir(parents=True)
    assert retitle.commit_helper(v1) == Path("scripts/git-safe-commit.sh")
    assert retitle.commit_helper(v2) == Path("scout/scripts/git-safe-commit.sh")
