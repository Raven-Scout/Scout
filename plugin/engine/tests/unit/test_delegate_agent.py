"""The agent's side of a delegate mirror (#550 V3): the guide the mirror carries, and ``scoutctl delegate init``.

The mirror is built on the anonymised privacy fixture vault and cloned with real
git; the end-to-end test runs the engine in subprocesses with only the clone, a
fresh HOME and none of Scout's variables, as an agent's container would.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import delegate_agent as da
from scout import delegate_mirror as dm
from scout import repo_sync as rs
from scout._spawn import python_module_argv
from scout.cli import app, delegate_write_refusal

from .privacy_vault_helpers import git
from .test_delegate_mirror import PRIYA, _bare, _commit, _link, _show, vault  # noqa: F401 - the fixture

runner = CliRunner()
LAPTOP = "LAPTOP-ONLY-RUN-INSTRUCTIONS"


def _clone(vault: Path, tmp_path: Path, delegate: str = "grok-personal") -> Path:  # noqa: F811
    """Link ``delegate`` to a local bare mirror and clone it, as the agent would."""
    bare = _bare(tmp_path, delegate)
    _link(vault, delegate, bare)
    clone = tmp_path / f"{delegate}-clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{bare}", str(clone)], check=True)
    return clone.resolve()


# ----- the guide the mirror carries -------------------------------------------------------------------


def test_the_mirror_carries_scouts_guide_in_place_of_the_vaults_laptop_instructions(
    vault: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    _commit(vault, "CLAUDE.md", f"# For the laptop\n\n{LAPTOP}: write the daily file.\n", "laptop guide")
    _commit(vault, "AGENTS.md", f"{LAPTOP}\n", "laptop agents")
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    guide = dm.delegate_guide("grok-personal").decode()
    for rel in dm.GUIDE_PATHS:
        assert _show(bare, rel) == guide
    assert LAPTOP not in guide and "delegate:grok-personal" in guide and "scoutctl delegate init" in guide
    assert all(c in guide for c in ("Never write", "git pull --ff-only", dm.ENGINE_INSTALL))


def test_check_tree_refuses_a_guide_the_build_didnt_write(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    head = rs._head(vault)
    blobs = rs.BlobReader(vault)
    try:
        the_plan = dm.plan(vault, head, "grok-personal", blobs)[0]
    finally:
        blobs.close()
    real = dm.delegate_guide
    monkeypatch.setattr(dm, "delegate_guide", lambda d: real(d) + b"\nAlso push your notes to main.\n")
    commit = git(vault, "commit-tree", dm.write_tree(vault, the_plan), "-m", "x").strip()
    monkeypatch.setattr(dm, "delegate_guide", real)
    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_tree(vault, commit, "grok-personal", head)
    assert e.value.code == "delegate_mirror_unsafe"


# ----- delegate init ---------------------------------------------------------------------------------


def test_init_checks_the_clone_and_writes_the_environment_inside_git(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    clone = _clone(vault, tmp_path)
    result = da.init(clone, env={})
    assert (result.delegate, result.clone, result.notes) == ("grok-personal", str(clone), [])
    assert result.env == {"SCOUT_DATA_DIR": str(clone), "SCOUT_READER": "delegate:grok-personal"}
    assert result.files > 0 and result.env_file == str(clone / ".git" / da.ENV_FILE)
    assert result.env_file is not None and Path(result.env_file).read_text(encoding="utf-8") == (
        f"SCOUT_DATA_DIR={clone}\nSCOUT_READER=delegate:grok-personal\n"
    )
    assert (
        subprocess.run(["git", "-C", str(clone), "status", "--porcelain"], capture_output=True, text=True).stdout == ""
    )
    sub = clone / "knowledge-base" / "people"
    assert da.init(None, env={"SCOUT_DATA_DIR": str(sub)}).clone == str(clone)  # found from a folder below
    assert da.init(None, env={"SCOUT_READER": "delegate:grok-personal", "SCOUT_DATA_DIR": str(clone)}).files
    assert result.exports().splitlines() == [
        f"export SCOUT_DATA_DIR={clone}",
        "export SCOUT_READER=delegate:grok-personal",
        "unset SCOUT_MODE",
    ]


def test_init_refuses_what_isnt_this_delegates_clone(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    clone = _clone(vault, tmp_path)
    for where, env, code in (
        (tmp_path / "nowhere", {}, "not_a_mirror"),
        (vault, {}, "owners_vault"),
        (clone, {"SCOUT_READER": "delegate:duet"}, "reader_mismatch"),
    ):
        where.mkdir(exist_ok=True)
        if code == "owners_vault":
            (vault / dm.MANIFEST_PATH).parent.mkdir(parents=True, exist_ok=True)
            (vault / dm.MANIFEST_PATH).write_text("{}", encoding="utf-8")
        with pytest.raises(rs.RepoSyncError) as e:
            da.init(where, env=env)
        assert e.value.code == code, where
    config = clone / "scout-config.yaml"
    config.write_text(config.read_text(encoding="utf-8").replace("grok-personal", "someone-else"), encoding="utf-8")
    with pytest.raises(rs.RepoSyncError) as e:
        da.init(clone, env={})
    assert e.value.code == "delegate_not_listed"
    (clone / dm.MANIFEST_PATH).write_text("not json", encoding="utf-8")
    with pytest.raises(rs.RepoSyncError) as e:
        da.init(clone, env={})
    assert e.value.code == "not_a_mirror"


def test_init_says_what_needs_doing(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    clone = _clone(vault, tmp_path)
    manifest = json.loads((clone / dm.MANIFEST_PATH).read_text(encoding="utf-8"))
    (clone / dm.MANIFEST_PATH).write_text(json.dumps({**manifest, "engine_version": "99.0.0"}), encoding="utf-8")
    (clone / "AGENTS.md").unlink()
    notes = da.init(clone, env={"SCOUT_MODE": "briefing"}).notes
    assert any("SCOUT_MODE" in n for n in notes)
    assert any("older than the 99.0.0" in n and "uv tool upgrade" in n for n in notes)
    assert any("AGENTS.md" in n and "missing" in n for n in notes)
    copy = tmp_path / "no-git"
    shutil.copytree(clone, copy, ignore=shutil.ignore_patterns(".git"))
    result = da.init(copy, env={})
    assert result.env_file is None and any("not a git checkout" in n for n in result.notes)
    assert da._version("0.18.16rc1") == (0, 18, 16) and da._version("1.2.3-rc.1") == (1, 2, 3)


def test_init_notes_a_clone_that_shows_nothing(
    vault: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(vault, tmp_path)
    monkeypatch.setattr("scout.kb.read.all_labels", lambda *_a, **_k: {})
    assert any("sees no files" in n for n in da.init(clone, env={}).notes)


def test_the_cli_and_the_delegate_guard(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    clone = _clone(vault, tmp_path)
    env = {"SCOUT_DATA_DIR": str(clone), "SCOUT_READER": ""}
    out = runner.invoke(app, ["delegate", "init"], env=env)
    assert out.exit_code == 0 and "grok-personal's mirror" in out.output and "eval" in out.output
    out = runner.invoke(app, ["delegate", "init", "--json"], env=env)
    assert out.exit_code == 0 and json.loads(out.stdout)["delegate"] == "grok-personal"
    out = runner.invoke(app, ["delegate", "init", "--print-env"], env=env)
    assert out.exit_code == 0 and out.stdout.startswith("export SCOUT_DATA_DIR=")
    out = runner.invoke(app, ["delegate", "init", "--print-env", "--clone", str(tmp_path)], env=env)
    assert out.exit_code == 2 and "isn't in a delegate mirror clone" in out.output and "export" not in out.output
    out = runner.invoke(app, ["delegate", "init", "--clone", str(tmp_path)], env=env)
    assert out.exit_code == 2
    reader = {"SCOUT_READER": "delegate:grok-personal"}
    assert delegate_write_refusal(["delegate", "init", "--print-env"], reader) is None
    assert delegate_write_refusal(["delegate", "sync", "--all"], reader) is not None
    (clone / "AGENTS.md").unlink()
    out = runner.invoke(app, ["delegate", "init"], env=env)
    assert out.exit_code == 0 and "warning:" in out.output
    out = runner.invoke(app, ["delegate", "init", "--print-env"], env=env)
    assert "scoutctl delegate init:" in out.output  # notes go to stderr, which the runner mixes in


def test_an_agent_with_only_the_clone_reads_through_scoutctl(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    """V3's check: nothing but the mirror clone and the engine, and the delegate reads; a write refuses."""
    clone = _clone(vault, tmp_path)
    home = tmp_path / "agent-home"
    home.mkdir()
    bare_env = {k: v for k, v in os.environ.items() if not k.startswith("SCOUT_")}
    bare_env |= {"HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}

    def scoutctl(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            python_module_argv("scout", *args), cwd=clone, env=env, capture_output=True, text=True, timeout=120
        )

    exported = scoutctl("delegate", "init", "--print-env", env=bare_env)
    assert exported.returncode == 0, exported.stderr
    agent_env = dict(bare_env)
    for line in exported.stdout.splitlines():
        if line.startswith("export "):
            key, _, value = line[len("export ") :].partition("=")
            agent_env[key] = value.strip("'")
    assert agent_env["SCOUT_READER"] == "delegate:grok-personal" and agent_env["SCOUT_DATA_DIR"] == str(clone)
    listed = scoutctl("kb", "files", "--json", env=agent_env)
    assert listed.returncode == 0, listed.stderr
    assert PRIYA in listed.stdout and "people/sam" not in listed.stdout
    read = scoutctl("kb", "read", PRIYA, env=agent_env)
    assert read.returncode == 0 and "[[sam" not in read.stdout, read.stderr
    write = scoutctl("action-items", "mark-done", "x", env=agent_env)
    assert write.returncode == 3 and "delegate checkout" in write.stderr
    assert sys.executable  # the same interpreter the agent's `uv tool` install would provide
