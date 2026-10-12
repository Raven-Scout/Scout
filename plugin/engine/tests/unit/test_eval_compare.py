"""Slice 4: samples on disk, checkouts, the base cache, verdicts and `eval.sh compare` (spec 2026-10-12 §8).

No test starts the real claude: samples come from a fake claude through the real runner.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from tests.eval.harness.build import PLUGIN_ROOT, build_vault
from tests.eval.harness.checkout import Checkout, CheckoutError, base_checkout, head_checkout
from tests.eval.harness.runner import load_sample, run_sample
from tests.eval.harness.scenario import load_scenario
from tests.unit.test_eval_runner import FAKE_CLAUDE

MONDAY = date(2026, 10, 12)
SERVERS = "claude_ai_Google_Calendar,claude_ai_Linear,claude_ai_Slack"


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "fake-claude"
    path.write_text(FAKE_CLAUDE)
    path.chmod(0o755)
    monkeypatch.setenv("EVAL_FAKE_SERVERS", SERVERS)
    monkeypatch.setenv("EVAL_FAKE_ENRICH", "1")
    return path


def test_a_sample_round_trips_through_its_result_dir(tmp_path: Path, fake: Path) -> None:
    ran = run_sample(
        load_scenario("busy-weekday"), today=MONDAY, rep=2, model="sonnet", results_root=tmp_path / "r",
        real_claude=str(fake), arm="base", cli_version="9.9.9 (Claude Code)",
    )  # fmt: skip
    loaded = load_sample(ran.result_dir)
    assert loaded == ran
    assert loaded.meta["arm"] == "base"
    assert loaded.meta["resolved_model"] == "claude-sonnet-5"
    assert loaded.meta["cli_version"] == "9.9.9 (Claude Code)"
    assert loaded.meta["today"] == "2026-10-12"


def test_an_aborted_sample_round_trips_with_its_reason(
    tmp_path: Path, fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVAL_FAKE_SUBTYPE", "error_max_budget_usd")
    ran = run_sample(
        load_scenario("busy-weekday"), today=MONDAY, rep=1, model="sonnet", results_root=tmp_path / "r",
        real_claude=str(fake),
    )  # fmt: skip
    loaded = load_sample(ran.result_dir)
    assert loaded.aborted == {"reason": "error_max_budget_usd"}
    assert loaded == ran


def test_head_checkout_is_this_repo_with_no_pythonpath() -> None:
    head = head_checkout()
    assert head.label == "head" and head.root == PLUGIN_ROOT.parent and head.pythonpath is None


def test_an_unknown_ref_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CheckoutError, match="no-such-ref-xyz"):
        base_checkout("no-such-ref-xyz", tmp_path)


def test_base_checkout_is_a_clean_detached_worktree_reused_by_commit(tmp_path: Path) -> None:
    repo = PLUGIN_ROOT.parent
    base = base_checkout("HEAD", tmp_path)
    try:
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        assert (base.label, base.commit) == ("base", sha)
        assert base.root == tmp_path / "checkouts" / sha
        assert base.pythonpath == base.root / "plugin" / "engine"
        assert (base.root / "plugin" / "phases" / "core").is_dir()
        assert base_checkout("HEAD", tmp_path) == base
        (base.root / "plugin" / "phases" / "core" / "stray.md").write_text("x")
        with pytest.raises(CheckoutError, match="not clean"):
            base_checkout("HEAD", tmp_path)
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(base.root)], check=False)


def _plugin_copy_with_marker(tmp_path: Path) -> Checkout:
    """A stand-in base: a copy of plugin/ whose run-modes phase says something head's doesn't."""
    root = tmp_path / "other"
    shutil.copytree(PLUGIN_ROOT, root / "plugin", ignore=shutil.ignore_patterns(".venv", "__pycache__", "tests"))
    phase = root / "plugin" / "phases" / "core" / "00-run-modes.md"
    phase.write_text(phase.read_text() + "\nBASE-ARM-MARKER\n")
    return Checkout(label="base", root=root, commit="0" * 40, pythonpath=root / "plugin" / "engine")


def test_a_vault_built_from_another_checkout_uses_that_checkouts_phases(tmp_path: Path) -> None:
    other = _plugin_copy_with_marker(tmp_path)
    vault = build_vault(tmp_path / "v", today=MONDAY, checkout=other)
    assert "BASE-ARM-MARKER" in (vault / "SKILL.md").read_text()
    head_vault = build_vault(tmp_path / "h", today=MONDAY)
    assert "BASE-ARM-MARKER" not in (head_vault / "SKILL.md").read_text()


def test_the_base_arms_session_sees_the_base_engine(
    tmp_path: Path, fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _plugin_copy_with_marker(tmp_path)
    monkeypatch.setenv("EVAL_FAKE_ENVFILE", str(tmp_path / "pythonpath.txt"))
    ran = run_sample(
        load_scenario("busy-weekday"), today=MONDAY, rep=1, model="sonnet", results_root=tmp_path / "r",
        real_claude=str(fake), arm="base", checkout=other,
    )  # fmt: skip
    assert ran.aborted is None
    assert (tmp_path / "pythonpath.txt").read_text() == str(other.pythonpath)
    assert "BASE-ARM-MARKER" in (ran.result_dir / "vault" / "SKILL.md").read_text()


def test_a_base_install_never_installs_launchd_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --no-jobs, `bootstrap install` replaces the user's real loaded scheduler jobs."""
    import tests.eval.harness.build as build

    seen: list[list[str]] = []

    envs: list[dict[str, str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        envs.append(kwargs["env"])
        (tmp_path / "v").mkdir(exist_ok=True)
        (tmp_path / "v" / "SKILL.md").write_text("x")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(build.subprocess, "run", fake_run)
    other = Checkout(label="base", root=tmp_path, commit=None, pythonpath=tmp_path / "plugin" / "engine")
    build._install_from(other, tmp_path / "v", frozenset({"slack"}), dict.fromkeys(
        ("user_slack_id", "github_username", "github_repos", "claude_bin", "max_budget"), "x"))  # fmt: skip
    [cmd] = seen
    assert "--no-jobs" in cmd and "--skip-claude" in cmd
    assert Path(envs[0]["HOME"]).is_relative_to(tmp_path), "the install must run with a throwaway HOME"


def test_the_install_guard_refuses_a_command_without_no_jobs() -> None:
    import tests.eval.harness.build as build

    with pytest.raises(AssertionError, match="--no-jobs"):
        build._refuse_system_side_effects(["python", "-m", "scout", "bootstrap", "install", "--skip-claude"])


def test_the_in_process_install_refuses_jobs() -> None:
    import tests.eval.harness.build as build

    cfg = build.BootstrapConfig(
        vault=Path("/x"), plugin_root=Path("/p"), instance_name="S", instance_name_lower="s", user_name="A",
        user_email="a@example.com", timezone="UTC", platform="macos", plugin_version="0", enabled_connectors=set(),
        connector_inputs={}, skip_jobs=False, skip_claude=True,
    )  # fmt: skip
    with pytest.raises(AssertionError, match="skip_jobs"):
        build._refuse_jobs(cfg)


@pytest.fixture
def cache_root(tmp_path: Path):
    root = tmp_path / "cache"
    yield root
    for wt in (root / "checkouts").glob("*"):
        subprocess.run(["git", "-C", str(PLUGIN_ROOT.parent), "worktree", "remove", "--force", str(wt)], check=False)


def _compare(fake: Path, cache_root: Path, tmp_path: Path, *extra: str) -> int:
    from tests.eval.harness.cli import main

    return main(
        ["compare", "busy-weekday", "--base", "HEAD", "--yes", "--today", "2026-10-12", "--claude", str(fake),
         "--cli-version", "9.9.9", "--cache", str(cache_root), "--results", str(tmp_path / "results"), *extra]
    )  # fmt: skip


def _cached(cache_root: Path) -> list[Path]:
    return sorted(p for p in (cache_root / "baselines").glob("*/*") if p.is_dir())


def test_compare_runs_both_arms_then_reuses_the_cached_base(
    tmp_path: Path, fake: Path, cache_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _compare(fake, cache_root, tmp_path, "--reps", "1") == 0
    out = capsys.readouterr().out
    assert "Will start 2 sessions (head busy-weekday ×1, base busy-weekday ×1) on sonnet" in out
    assert "placement" in out and "need N≥2" in out
    assert len(_cached(cache_root)) == 1

    assert _compare(fake, cache_root, tmp_path, "--reps", "1") == 0
    out = capsys.readouterr().out
    assert "Will start 1 session (head busy-weekday ×1) on sonnet" in out
    assert "1 cached base sample" in out
    assert len(_cached(cache_root)) == 1

    assert _compare(fake, cache_root, tmp_path, "--reps", "1", "--fresh-base") == 0
    assert "Will start 2 sessions" in capsys.readouterr().out
    assert len(_cached(cache_root)) == 2


def test_compare_refuses_an_unknown_base_and_a_big_plan(
    tmp_path: Path, fake: Path, cache_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.eval.harness.cli import main

    assert main(["compare", "busy-weekday", "--base", "no-such-ref-xyz", "--yes", "--cache", str(cache_root),
                 "--claude", str(fake), "--cli-version", "9"]) == 2  # fmt: skip
    assert "no-such-ref-xyz" in capsys.readouterr().err
    assert _compare(fake, cache_root, tmp_path, "--reps", "6") == 2
    assert "--many" in capsys.readouterr().err
