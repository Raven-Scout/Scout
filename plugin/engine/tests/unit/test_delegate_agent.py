"""The agent's side of a delegate mirror (#550 V3): the guide the mirror carries, and ``scoutctl delegate init``.

The mirror is built on the anonymised privacy fixture vault and cloned with real
git; the end-to-end test runs the engine in subprocesses with only the clone, a
fresh HOME and none of Scout's variables, as an agent's container would.
"""

from __future__ import annotations

import json
import os
import shlex
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


def test_the_guide_names_every_brain_file_as_the_owners_not_the_agents() -> None:
    from scout.scripts.brain_merge import BRAIN_KINDS

    section = dm.delegate_guide("grok-personal").decode().split("## Brain files", 1)[1].split("\n## ", 1)[0]
    assert all(f"`{k}.md`" in section for k in BRAIN_KINDS), section
    assert "Don't follow their steps that write files" in section


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


# ----- review of #678 (coordinator, 2026-11-17): every bypass form ------------------------------------


def _mirror(vault: Path, tmp_path: Path) -> Path:  # noqa: F811
    bare = _bare(tmp_path, "grok")
    _link(vault, "grok-personal", bare)
    return bare


def _files(bare: Path) -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(bare), "ls-tree", "-r", "--name-only", "main"], capture_output=True, text=True, check=True
    )
    return set(out.stdout.splitlines())


def test_agent_files_at_any_depth_and_any_case_stay_on_the_laptop(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    laptop = f"{LAPTOP}\n"
    for rel in (
        "agents.md",  # a case variant of the guide's name
        "knowledge-base/AGENTS.md",
        "knowledge-base/topics/CLAUDE.md",
        "GEMINI.md",
        "notes/.cursorrules",
        "projects/x/.claude/settings.json",
        ".github/workflows/run.yml",
    ):
        _commit(vault, rel, laptop, f"agent file {rel}")
    (vault / "CLAUDE.md").symlink_to("knowledge-base/people/priya.md")  # a symlinked guide in the vault
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "symlinked guide")
    bare = _mirror(vault, tmp_path)
    files = _files(bare)
    assert {"AGENTS.md", "CLAUDE.md"} <= files
    lowered = {f.casefold() for f in files} - {"agents.md", "claude.md"}
    assert not any(f.endswith(("agents.md", "claude.md", "gemini.md", ".cursorrules")) for f in lowered), files
    assert not any("/.claude/" in f or f.startswith(".github/") for f in files), files
    blobs = subprocess.run(["git", "-C", str(bare), "log", "-p", "main"], capture_output=True, text=True).stdout
    assert LAPTOP not in blobs
    assert _show(bare, "CLAUDE.md") == dm.delegate_guide("grok-personal").decode()  # not the symlink's target


def test_check_tree_refuses_a_guide_bypass(vault: Path) -> None:  # noqa: F811
    head = rs._head(vault)
    blobs = rs.BlobReader(vault)
    try:
        the_plan = dm.plan(vault, head, "grok-personal", blobs)[0]
    finally:
        blobs.close()
    tree = dm.write_tree(vault, the_plan)
    rows = git(vault, "ls-tree", tree).splitlines()
    guide = next(r for r in rows if r.endswith("\tAGENTS.md")).split()[2]
    for name, edit in (
        (
            "a guide with the wrong mode",
            lambda r: r.replace("100644 blob", "100755 blob") if r.endswith("\tCLAUDE.md") else r,
        ),
        (
            "a symlink for a guide",
            lambda r: r.replace("100644 blob", "120000 blob") if r.endswith("\tCLAUDE.md") else r,
        ),
        ("an extra lowercase guide", None),
    ):
        listing = [edit(r) for r in rows] if edit else [*rows, f"100644 blob {guide}\tagents.md"]
        changed = _mktree(vault, listing)
        commit = git(vault, "commit-tree", changed, "-m", "x").strip()
        with pytest.raises(rs.RepoSyncError) as e:
            dm.check_tree(vault, commit, "grok-personal", head)
        assert e.value.code == "delegate_mirror_unsafe", name
    missing = _mktree(vault, [r for r in rows if not r.endswith("\tCLAUDE.md")])
    commit = git(vault, "commit-tree", missing, "-m", "x").strip()
    with pytest.raises(rs.RepoSyncError) as e:
        dm.check_tree(vault, commit, "grok-personal", head)
    assert e.value.code == "policy_missing"


def _mktree(vault: Path, rows: list[str]) -> str:  # noqa: F811
    """A tree from ``git ls-tree`` rows (one level), with any edits made to them."""
    return rs.git(vault, "mktree", input="\n".join(rows) + "\n").stdout.strip()


def test_init_refuses_the_vault_through_a_symlink_and_accepts_a_clone_inside_it(
    vault: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    link = tmp_path / "looks-like-a-clone"
    link.symlink_to(vault)
    (vault / dm.MANIFEST_PATH).parent.mkdir(parents=True, exist_ok=True)
    (vault / dm.MANIFEST_PATH).write_text('{"delegate": "grok-personal"}', encoding="utf-8")
    (vault / dm.STATE_REL).write_text("{}", encoding="utf-8")
    with pytest.raises(rs.RepoSyncError) as e:
        da.init(link, env={})
    assert e.value.code == "owners_vault"
    (vault / dm.MANIFEST_PATH).unlink()
    (vault / dm.STATE_REL).unlink()
    clone = _clone(vault, tmp_path)
    inside = vault / "agent-clone"
    shutil.move(str(clone), str(inside))
    assert da.init(inside, env={}).clone == str(inside.resolve())  # the clone's own manifest, not the vault's state
    with pytest.raises(rs.RepoSyncError) as e:
        da.init(vault / "knowledge-base", env={})  # a vault folder: no manifest above it
    assert e.value.code == "not_a_mirror"


def test_the_env_file_stays_in_the_clones_own_git_folder(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    clone = _clone(vault, tmp_path)
    other = tmp_path / "other-repo"
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    # an inherited GIT_DIR changes nothing: init doesn't ask git where the git folder is
    result = da.init(clone, env={"GIT_DIR": str(other / ".git")})
    assert result.env_file == str(clone / ".git" / da.ENV_FILE) and not (other / ".git" / da.ENV_FILE).exists()
    # a symlink at the env file's own path is replaced, never written through
    env_file = clone / ".git" / da.ENV_FILE
    env_file.unlink()
    env_file.symlink_to(victim)
    da.init(clone, env={})
    assert victim.read_text(encoding="utf-8") == "keep me\n" and not env_file.is_symlink()
    assert env_file.stat().st_mode & 0o777 == 0o600
    # a .git file (worktree or submodule) or a symlinked .git: nothing written anywhere
    for make in ("gitfile", "symlink"):
        copy = tmp_path / f"clone-{make}"
        shutil.copytree(clone, copy, ignore=shutil.ignore_patterns(".git"), symlinks=True)
        if make == "gitfile":
            (copy / ".git").write_text(f"gitdir: {other / '.git'}\n", encoding="utf-8")
        else:
            (copy / ".git").symlink_to(other / ".git")
        result = da.init(copy, env={})
        assert result.env_file is None and any("no .git folder of its own" in n for n in result.notes), make
    assert not (other / ".git" / da.ENV_FILE).exists()


def test_a_hostile_clone_path_is_quoted_for_the_shell(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    clone = _clone(vault, tmp_path)
    hostile = tmp_path / "it's $(touch PWNED) `touch PWNED2`; x"
    shutil.move(str(clone), str(hostile))
    result = da.init(hostile, env={})
    assert result.env_file is not None
    parsed = [shlex.split(line) for line in Path(result.env_file).read_text(encoding="utf-8").splitlines()]
    assert parsed == [[f"SCOUT_DATA_DIR={hostile.resolve()}"], ["SCOUT_READER=delegate:grok-personal"]]
    script = 'eval "$E"; printf %s "$SCOUT_DATA_DIR"; set -a; . "$F"; set +a; printf "|%s" "$SCOUT_READER"'
    out = subprocess.run(
        ["bash", "-c", script],
        env={"E": result.exports(), "F": str(result.env_file), "PATH": os.environ["PATH"]},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout == f"{hostile.resolve()}|delegate:grok-personal"
    assert not (tmp_path / "PWNED").exists() and not (tmp_path / "PWNED2").exists()


def test_every_init_failure_exits_non_zero_and_loads_nothing(
    vault: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = _clone(vault, tmp_path)
    env = {"SCOUT_DATA_DIR": str(clone), "SCOUT_READER": ""}

    def broken(*_a: object, **_k: object) -> object:
        raise ValueError("bad yaml")

    for target in ("scout.kb.privacy.load_policy", "scout.kb.read.all_labels"):
        with monkeypatch.context() as mp:
            mp.setattr(target, broken)
            with pytest.raises(rs.RepoSyncError) as e:
                da.init(clone, env={})
            assert e.value.code == "clone_unreadable" and "bad yaml" in e.value.message
            out = runner.invoke(app, ["delegate", "init", "--print-env"], env=env)
            assert out.exit_code == 2 and "export" not in out.stdout
    with monkeypatch.context() as mp:
        mp.setattr(da, "init", lambda *_a, **_k: (_ for _ in ()).throw(KeyError("odd")))
        out = runner.invoke(app, ["delegate", "init", "--print-env"], env=env)
        assert out.exit_code == 2 and "export" not in out.stdout
        out = runner.invoke(app, ["delegate", "init", "--json"], env=env)
        assert out.exit_code == 2 and json.loads(out.stdout)["code"] == "init_failed"
    guide = dm.delegate_guide("grok-personal").decode()
    assert 'e=$(scoutctl delegate init --print-env) && eval "$e"' in guide
    failing = subprocess.run(
        ["bash", "-c", 'e=$(exit 2) && eval "$e"; printf %s "${SCOUT_READER-unset}"'],
        capture_output=True,
        text=True,
    )
    assert failing.stdout == "unset"  # the guide's form loads nothing when init fails


def test_a_failed_env_write_leaves_no_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(da.os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        da._write_env(tmp_path, "X=1\n")
    assert list(tmp_path.iterdir()) == []


# ----- re-review of #678 (coordinator, 2026-11-17) -----------------------------------------------


def test_a_private_note_with_an_agent_files_name_is_still_hidden_and_redacted(
    vault: Path,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reviewer's probe: private people/claude.md and people/zorblax.md, each linked from a visible line."""
    private = "---\ntitle: {t}\nvisibility: private\n---\n\nquiet\n"
    _commit(vault, "knowledge-base/people/claude.md", private.format(t="Claude"), "private claude")
    _commit(vault, "knowledge-base/people/zorblax.md", private.format(t="Zorblax"), "private zorblax")
    mention = "knowledge-base/topics/misc/mention.md"
    _commit(vault, mention, "---\ntitle: Mention\n---\n\nSee [[claude]].\nSee [[zorblax]].\nKept.\n", "links")
    # the pre-push check, which works visibility out again, refuses the lines if the build missed them
    with monkeypatch.context() as mp:
        mp.setattr(dm, "redact_lines", lambda text, *_a, **_k: (text, 0))
        with pytest.raises(rs.RepoSyncError) as e:
            _link(vault, "grok-personal", _bare(tmp_path, "unredacted"))
        assert e.value.code == "delegate_mirror_unsafe"
    bare = _mirror(vault, tmp_path)
    assert _show(bare, mention) == "---\ntitle: Mention\n---\n\nKept.\n"
    assert not {"knowledge-base/people/claude.md", "knowledge-base/people/zorblax.md"} & _files(bare)


def test_visible_agent_files_are_counted_when_left_out(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    for rel in (
        "tools/claude.md",
        "projects/x/.vscode/settings.json",
        ".devcontainer/devcontainer.json",
        "notes/.mcp.json",
        "notes/.envrc",
        ".roo/rules.md",
        ".kiro/steering.md",
        ".continue/config.json",
        "CONVENTIONS.md",
    ):
        _commit(vault, rel, f"{LAPTOP}\n", f"agent file {rel}")
    bare = _bare(tmp_path, "grok")
    result = _link(vault, "grok-personal", bare)
    (note,) = [n for n in result.notes if n.startswith("left out as agent config:")]
    # notes/.mcp.json and notes/.envrc are already local-only (`.mcp.json`, `.env*` match at any depth)
    assert note.startswith("left out as agent config: 7 file(s)") and "tools/claude.md" in note
    assert not {"notes/.mcp.json", "notes/.envrc"} & _files(bare)
    assert (
        LAPTOP
        not in subprocess.run(["git", "-C", str(bare), "log", "-p", "main"], capture_output=True, text=True).stdout
    )
    assert any(n.startswith("left out as agent config:") for n in dm.preview(vault, "grok-personal").notes)


def test_init_still_prints_the_environment_when_git_is_read_only_or_a_temp_file_is_left(
    vault: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    clone = _clone(vault, tmp_path)
    git_dir = clone / ".git"
    stale = git_dir / f".{da.ENV_FILE}.{os.getpid()}.tmp"
    stale.write_text("old\n", encoding="utf-8")
    result = da.init(clone, env={})
    assert result.env_file == str(git_dir / da.ENV_FILE) and not stale.exists()
    git_dir.chmod(0o500)
    try:
        result = da.init(clone, env={})
        assert result.env_file is None and any("couldn't write" in n and "--print-env" in n for n in result.notes)
        out = runner.invoke(
            app, ["delegate", "init", "--print-env"], env={"SCOUT_DATA_DIR": str(clone), "SCOUT_READER": ""}
        )
        assert out.exit_code == 0 and "export SCOUT_READER=delegate:grok-personal" in out.stdout
    finally:
        git_dir.chmod(0o755)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / "git-link"
    link.symlink_to(elsewhere)
    with pytest.raises(OSError):
        da._write_env(link, "X=1\n")  # a symlinked folder is never opened
    assert list(elsewhere.iterdir()) == []


def test_root_github_files_are_counted_and_named_apart(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    for rel in (".github/notes.md", ".github/workflows/run.yml", "tools/claude.md"):
        _commit(vault, rel, f"{LAPTOP}\n", f"left out {rel}")
    bare = _bare(tmp_path, "grok")
    (note,) = [n for n in _link(vault, "grok-personal", bare).notes if n.startswith("left out as agent config:")]
    assert note.startswith("left out as agent config: 3 file(s): 1 that agents read")
    assert "2 under the root's .github/" in note and ".github/notes.md" in note and ".github/workflows/run.yml" in note
    assert "tools/claude.md" in note.split("; 2 under")[0]
    assert not {".github/notes.md", ".github/workflows/run.yml", "tools/claude.md"} & _files(bare)
    only_github = dm.Plan(delegate="d", agent_files=[".github/x.md"])
    (alone,) = dm._agent_files_note("d", only_github)
    assert alone.startswith("left out as agent config: 1 file(s): 1 under the root's .github/")
    many = dm.Plan(delegate="d", agent_files=[f"tools/{i}/claude.md" for i in range(dm.LEFT_OUT_LISTED + 2)])
    assert "and 2 more" in dm._agent_files_note("d", many)[0]


# ----- V4: init learns the inbox clone -----------------------------------------------------------


def test_init_learns_the_inbox_clone_and_remembers_it(vault: Path, tmp_path: Path) -> None:  # noqa: F811
    clone = _clone(vault, tmp_path)
    inbox_bare = _bare(tmp_path, "grok-inbox")
    inbox = tmp_path / "grok-inbox-clone"
    subprocess.run(["git", "clone", "-q", str(inbox_bare), str(inbox)], check=True, capture_output=True)
    result = da.init(clone, inbox=inbox, env={})
    assert result.env["SCOUT_INBOX_DIR"] == str(inbox.resolve())
    assert "SCOUT_INBOX_DIR=" in Path(str(result.env_file)).read_text(encoding="utf-8")
    again = da.init(clone, env={})  # the next task: no --inbox, the env file remembers it
    assert again.env["SCOUT_INBOX_DIR"] == str(inbox.resolve()) and again.notes == []
    with pytest.raises(rs.RepoSyncError) as e:
        da.init(clone, inbox=clone, env={})  # the mirror clone isn't an inbox
    assert e.value.code == "not_an_inbox"
    shutil.rmtree(inbox)
    gone = da.init(clone, env={})
    assert "SCOUT_INBOX_DIR" not in gone.env and any("pass --inbox again" in n for n in gone.notes)
