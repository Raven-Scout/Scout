"""build_vault: the eval fixture vault, built fresh from the checkout (spec 2026-10-12 §5.1)."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from tests.eval.harness.build import FIXTURE_USER, FixtureError, build_vault

TODAY = date(2026, 10, 14)


def _scenario(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "scenario"
    for rel, text in files.items():
        p = root / "vault" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _tree(root: Path) -> dict[str, bytes]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.relative_to(root).parts:
            out[p.relative_to(root).as_posix()] = p.read_bytes().replace(str(root).encode(), b"<VAULT>")
    return out


def test_brain_files_come_from_the_checkout(tmp_path: Path) -> None:
    vault = build_vault(tmp_path / "v", today=TODAY)
    skill = (vault / "SKILL.md").read_text()
    assert "## Run Modes" in skill
    assert FIXTURE_USER in (vault / "run-scout.sh").read_text()


def test_base_overlay_is_rendered_with_dates(tmp_path: Path) -> None:
    vault = build_vault(tmp_path / "v", today=TODAY)
    widgets = (vault / "knowledge-base/projects/widgets/widgets.md").read_text()
    assert "**Last verified:** 2026-10-12" in widgets
    assert "{{" not in widgets


def test_scenario_overlay_renders_file_names_too(tmp_path: Path) -> None:
    sc = _scenario(tmp_path, {"action-items/action-items-{{d-1}}.md": "# {{d-1}}\n"})
    vault = build_vault(tmp_path / "v", today=TODAY, scenario_dir=sc)
    assert (vault / "action-items/action-items-2026-10-13.md").read_text() == "# 2026-10-13\n"


def test_generated_index_is_written_and_rendered(tmp_path: Path) -> None:
    vault = build_vault(tmp_path / "v", today=TODAY)
    index = (vault / "knowledge-base.md").read_text()
    assert len(index) > 60_000 and "{{" not in index


def test_session_cache_script_is_a_noop(tmp_path: Path) -> None:
    vault = build_vault(tmp_path / "v", today=TODAY)
    script = vault / "scripts/cc-session-cache.sh"
    assert script.read_text() == "#!/bin/bash\nexit 0\n"
    assert os.access(script, os.X_OK)


def test_vault_is_a_git_repo_with_one_commit(tmp_path: Path) -> None:
    vault = build_vault(tmp_path / "v", today=TODAY)
    log = subprocess.run(["git", "-C", str(vault), "log", "--format=%an|%s"], capture_output=True, text=True)
    assert log.stdout.strip() == "Alex Example|fixture"
    status = subprocess.run(["git", "-C", str(vault), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout == ""


def test_the_fixture_repo_never_runs_background_maintenance(tmp_path: Path) -> None:
    # A detached `git maintenance run --auto` after a commit creates and removes .git/maintenance.lock
    # under a caller's rmtree (CI: FileNotFoundError 'maintenance.lock'). The vault's own config turns it off.
    vault = build_vault(tmp_path / "v", today=TODAY)
    for key, want in (("maintenance.auto", "false"), ("gc.auto", "0")):
        got = subprocess.run(["git", "-C", str(vault), "config", "--local", key], capture_output=True, text=True)
        assert got.stdout.strip() == want, key


def test_two_builds_on_the_same_day_are_identical(tmp_path: Path) -> None:
    # Same path both times: brain files embed the vault path, and provenance.json hashes them.
    dest = tmp_path / "v"
    first = _tree(build_vault(dest, today=TODAY))
    shutil.rmtree(dest)
    assert _tree(build_vault(dest, today=TODAY)) == first


def test_an_unrendered_macro_fails_loudly(tmp_path: Path) -> None:
    sc = _scenario(tmp_path, {"notes.md": "ok\n{{bogus}}\n"})
    with pytest.raises(FixtureError, match=r"notes\.md.*\{\{bogus\}\}"):
        build_vault(tmp_path / "v", today=TODAY, scenario_dir=sc)


def test_an_unrendered_macro_in_a_path_fails_loudly(tmp_path: Path) -> None:
    sc = _scenario(tmp_path, {"action-items/action-items-{{d-l}}.md": "typo in the macro\n"})
    with pytest.raises(FixtureError, match=r"\{\{d-l\}\}"):
        build_vault(tmp_path / "v", today=TODAY, scenario_dir=sc)


def test_a_macro_in_any_text_file_is_rendered_or_fails(tmp_path: Path) -> None:
    sc = _scenario(tmp_path, {"scripts/note.sh": "echo {{d-1}}\n", "data.csv": "x,{{bogus}}\n"})
    with pytest.raises(FixtureError, match=r"data\.csv"):
        build_vault(tmp_path / "v", today=TODAY, scenario_dir=sc)
    sc2 = _scenario(tmp_path / "ok", {"scripts/note.sh": "echo {{d-1}}\n"})
    vault = build_vault(tmp_path / "v2", today=TODAY, scenario_dir=sc2)
    assert (vault / "scripts/note.sh").read_text() == "echo 2026-10-13\n"


def test_an_inherited_git_dir_cannot_redirect_the_fixture_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outer = tmp_path / "outer"
    subprocess.run(["git", "init", "-q", str(outer)], check=True)
    monkeypatch.setenv("GIT_DIR", str(outer / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(outer))
    vault = build_vault(tmp_path / "v", today=TODAY)
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    assert (vault / ".git").is_dir()
    outer_log = subprocess.run(["git", "-C", str(outer), "log", "--oneline"], capture_output=True, text=True)
    assert outer_log.stdout == ""


def test_eval_sh_resolves_a_relative_destination_against_the_callers_cwd(tmp_path: Path) -> None:
    eval_sh = Path(__file__).resolve().parents[3] / "scripts" / "eval.sh"
    r = subprocess.run(
        [str(eval_sh), "build", "rel-vault", "--today", "2026-10-14"], cwd=tmp_path, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str((tmp_path / "rel-vault").resolve())
    assert (tmp_path / "rel-vault" / "SKILL.md").exists()


def test_cli_build_prints_the_vault_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.eval.harness.cli import main

    assert main(["build", str(tmp_path / "v"), "--today", "2026-10-14"]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "v")
    assert (tmp_path / "v" / "SKILL.md").exists()


def test_cli_build_rejects_an_unknown_scenario(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.eval.harness.cli import main

    assert main(["build", str(tmp_path / "v"), "--scenario", "no-such-scenario"]) == 2
    assert "no-such-scenario" in capsys.readouterr().err


def test_refuses_an_existing_destination(tmp_path: Path) -> None:
    dest = tmp_path / "v"
    dest.mkdir()
    (dest / "keep.txt").write_text("x")
    with pytest.raises(FileExistsError):
        build_vault(dest, today=TODAY)


def test_a_scheduler_tick_can_be_seeded_so_run_health_sees_a_live_scheduler(tmp_path: Path) -> None:
    import json as _json
    from datetime import UTC, datetime

    tick = datetime(2026, 10, 14, 8, 59, tzinfo=UTC)
    vault = build_vault(tmp_path / "v", today=TODAY, scheduler_tick=tick)
    [log] = (vault / ".scout-logs").glob("schedule-events-*.jsonl")
    row = _json.loads(log.read_text().splitlines()[-1])
    assert row["kind"] == "schedule.tick.completed" and row["ts"] == "2026-10-14T08:59:00Z"
    from scout.health import last_tick_completed

    assert last_tick_completed(vault) == tick
