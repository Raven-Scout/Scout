"""templates/scripts/git-safe-commit.sh — commit explicit paths without sweeping in a peer's work.

Scheduled sessions overlap and share one working tree, so a session that runs
``git add -A && git commit`` commits whatever a concurrent session has
half-written, plus anything a peer left staged. The dreaming brain's commit
steps call ``scripts/git-safe-commit.sh "msg" <paths>`` instead: it stages and
commits only the named paths, serialises the stage→commit step behind a
cross-session mutex (``flock`` where it exists, an atomic ``mkdir`` where it
doesn't — macOS ships no ``flock``), and clears a git lock only when it is
provably stale.

The first tests install a vault through bootstrap; the rest render the template
into a scratch git repo and run it with the real shell — ``/bin/bash`` when it
exists, which on macOS is bash 3.2 (``set -u`` and empty arrays don't mix there).
"""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from scout.scripts.bootstrap import BootstrapConfig, install, upgrade
from scout.scripts.phase_assembly import render_template

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = PLUGIN_ROOT / "templates" / "scripts" / "git-safe-commit.sh.tmpl"
REL = "scripts/git-safe-commit.sh"
BASH = "/bin/bash" if Path("/bin/bash").exists() else (shutil.which("bash") or "bash")
RUN_TIMEOUT_S = 60

# Every external command the helper runs, besides the optional flock.
TOOLS = ("git", "mkdir", "rmdir", "rm", "cat", "sleep", "stat", "date", "pgrep", "ps")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Scout Bot",
    "GIT_AUTHOR_EMAIL": "alex@example.com",
    "GIT_COMMITTER_NAME": "Scout Bot",
    "GIT_COMMITTER_EMAIL": "alex@example.com",
    # The developer's own git config (signing, hooks) must not leak in.
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        env={**os.environ, **GIT_ENV},
        check=True,
        capture_output=True,
        text=True,
    ).stdout


# ---------- shipped by bootstrap ----------


@pytest.fixture
def plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "plugin"
    root.mkdir()
    for name in ("templates", "phases", "engine"):
        (root / name).symlink_to(PLUGIN_ROOT / name)
    stub = _script(root / ".venv" / "bin" / "scoutctl", "#!/bin/sh\nexit 0\n")
    monkeypatch.setattr("scout.scripts.bootstrap.resolve_scoutctl_bin", lambda: stub)
    return root


def _config(vault: Path, plugin: Path, *, version: str = "0.4.0") -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=plugin,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version=version,
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


@pytest.fixture
def installed(tmp_path: Path, plugin: Path) -> Path:
    v = tmp_path / "Scout"
    install(_config(v, plugin))
    return v


def test_a_fresh_install_ships_the_helper_executable_and_rendered_for_this_vault(installed: Path) -> None:
    script = installed / REL

    text = script.read_text(encoding="utf-8")
    assert os.access(script, os.X_OK)
    assert "{{" not in text
    assert f'VAULT="${{SCOUT_DATA_DIR:-{installed}}}"' in text


def test_an_upgrade_gives_an_existing_vault_the_helper(installed: Path, plugin: Path) -> None:
    """A vault installed before the helper shipped picks it up on its next upgrade."""
    (installed / REL).unlink()

    upgrade(_config(installed, plugin, version="0.4.1"))

    assert os.access(installed / REL, os.X_OK)


def test_an_upgrade_parks_the_vaults_own_copy_instead_of_losing_it(installed: Path, plugin: Path) -> None:
    """A vault that wrote its own helper before the plugin shipped one has no
    record of a plugin render for it: the plugin's version goes live and the
    vault's copy is parked under .scout-state/drift/, reported as replaced."""
    (installed / ".scout-state" / "last-rendered" / REL).unlink()
    own = "#!/usr/bin/env bash\n# the vault's own commit helper\n"
    (installed / REL).write_text(own, encoding="utf-8")

    result = upgrade(_config(installed, plugin, version="0.4.1"))

    assert (installed / REL).read_text(encoding="utf-8") != own
    assert os.access(installed / REL, os.X_OK)
    (edit,) = [e for e in result.vault_edits if e.path == REL]
    assert edit.outcome == "replaced"
    (parked,) = edit.parked
    assert Path(parked).parts[:2] == (".scout-state", "drift")
    assert (installed / parked).read_text(encoding="utf-8") == own


def test_the_dreaming_brain_commits_through_the_shipped_helper(installed: Path) -> None:
    brain = (installed / "DREAMING.md").read_text(encoding="utf-8")

    assert f"{installed}/{REL} " in brain
    assert "add -A" not in brain


# ---------- behaviour ----------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A vault with one commit: two tracked notes and the stock .gitignore line."""
    v = tmp_path / "vault"
    v.mkdir()
    _git(v, "init", "-q")
    (v / ".gitignore").write_text(".scout-cache/\n", encoding="utf-8")
    (v / "a.md").write_text("a\n", encoding="utf-8")
    (v / "other.md").write_text("other\n", encoding="utf-8")
    _git(v, "add", "-A")
    _git(v, "commit", "-q", "-m", "init")
    return v


@pytest.fixture
def helper(repo: Path) -> Path:
    text = render_template(TEMPLATE.read_text(encoding="utf-8"), {"SCOUT_DIR": str(repo)})
    assert "{{" not in text
    # Outside the vault, so the helper itself never shows up in `git status`.
    return _script(repo.parent / "bin" / "git-safe-commit.sh", text)


@pytest.fixture
def stubs(tmp_path: Path) -> Path:
    """Front of PATH for stand-ins (pgrep), so no test depends on what else runs on the host."""
    d = tmp_path / "stubs"
    d.mkdir()
    return d


def _no_flock_bin(tmp_path: Path) -> Path:
    d = tmp_path / "no-flock-bin"
    d.mkdir(exist_ok=True)
    for tool in TOOLS:
        found = shutil.which(tool)
        assert found, f"{tool} is not on PATH"
        if not (d / tool).exists():
            (d / tool).symlink_to(found)
    return d


@pytest.fixture(params=["host-path", "no-flock"])
def path_env(request: pytest.FixtureRequest, tmp_path: Path, stubs: Path) -> str:
    """The host PATH (flock where the host has it), and a PATH with no flock."""
    if request.param == "host-path":
        return f"{stubs}:{os.environ['PATH']}"
    return f"{stubs}:{_no_flock_bin(tmp_path)}"


def _run(helper: Path, path_env: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, str(helper), *args],
        cwd=helper.parent,  # the working directory must not matter
        env={**os.environ, **GIT_ENV, "PATH": path_env, **env},
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
    )


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def _committed(repo: Path) -> set[str]:
    return set(_git(repo, "show", "--name-only", "--format=", "HEAD").split())


def _staged(repo: Path) -> set[str]:
    return set(_git(repo, "diff", "--cached", "--name-only").split())


def _pgrep(stubs: Path, *, live_git: bool) -> None:
    _script(stubs / "pgrep", f"#!/bin/sh\nexit {0 if live_git else 1}\n")


def _age(path: Path, seconds: int) -> None:
    t = path.stat().st_mtime - seconds
    os.utime(path, (t, t))


def _dead_pid() -> int:
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


@pytest.fixture
def held_mutex(repo: Path) -> Iterator[None]:
    """Hold both mutexes, as a live peer session would: the flock and the mkdir dir."""
    cache = repo / ".scout-cache"
    cache.mkdir()
    (cache / "commit.lock.d").mkdir()
    (cache / "commit.lock.d" / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    with open(cache / "commit.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def test_only_the_named_paths_are_committed(repo: Path, helper: Path, path_env: str) -> None:
    """A peer's staged file and a peer's half-written file both stay out of the commit."""
    (repo / "other.md").write_text("a peer's staged edit\n", encoding="utf-8")
    _git(repo, "add", "other.md")
    (repo / "half-written.md").write_text("a peer's draft\n", encoding="utf-8")
    (repo / "a.md").write_text("mine\n", encoding="utf-8")
    (repo / "notes").mkdir()
    (repo / "notes" / "new.md").write_text("mine too\n", encoding="utf-8")

    result = _run(helper, path_env, "dreaming [22:00]: KB deep work", "a.md", "notes/")

    assert result.returncode == 0, result.stderr
    assert _committed(repo) == {"a.md", "notes/new.md"}
    assert _git(repo, "log", "-1", "--format=%s").strip() == "dreaming [22:00]: KB deep work"
    assert _staged(repo) == {"other.md"}
    assert "?? half-written.md" in _git(repo, "status", "--porcelain")


def test_the_mutex_is_released_and_never_shows_in_git_status(repo: Path, helper: Path, path_env: str) -> None:
    (repo / "a.md").write_text("mine\n", encoding="utf-8")

    result = _run(helper, path_env, "msg", "a.md")

    assert result.returncode == 0, result.stderr
    assert not (repo / ".scout-cache" / "commit.lock.d").exists()
    assert _git(repo, "status", "--porcelain") == ""


@pytest.mark.parametrize("blanket", ["-A", "--all", ".", "./", ":/"])
def test_a_blanket_stage_is_refused(repo: Path, helper: Path, path_env: str, blanket: str) -> None:
    (repo / "a.md").write_text("mine\n", encoding="utf-8")
    before = _head(repo)

    result = _run(helper, path_env, "msg", "a.md", blanket)

    assert result.returncode == 2
    assert "refusing" in result.stderr
    assert _head(repo) == before
    assert _staged(repo) == set()


@pytest.mark.parametrize("args", [[], ["msg only"], ["", "a.md"]])
def test_usage_errors_exit_2(repo: Path, helper: Path, path_env: str, args: list[str]) -> None:
    result = _run(helper, path_env, *args)

    assert result.returncode == 2
    assert "usage" in result.stderr or "empty commit message" in result.stderr


def test_nothing_to_commit_exits_4(repo: Path, helper: Path, path_env: str) -> None:
    before = _head(repo)

    result = _run(helper, path_env, "msg", "a.md")

    assert result.returncode == 4
    assert "nothing" in result.stderr
    assert _head(repo) == before


def test_a_peers_staged_file_does_not_count_as_something_to_commit(repo: Path, helper: Path, path_env: str) -> None:
    (repo / "other.md").write_text("a peer's staged edit\n", encoding="utf-8")
    _git(repo, "add", "other.md")
    before = _head(repo)

    result = _run(helper, path_env, "msg", "a.md")

    assert result.returncode == 4
    assert _head(repo) == before
    assert _staged(repo) == {"other.md"}


def test_a_held_mutex_blocks_a_second_session(repo: Path, helper: Path, path_env: str, held_mutex: None) -> None:
    (repo / "a.md").write_text("mine\n", encoding="utf-8")
    before = _head(repo)

    result = _run(helper, path_env, "msg", "a.md", LOCK_WAIT_SECS="1")

    assert result.returncode == 3
    assert "could not acquire" in result.stderr
    assert _head(repo) == before
    # The holder's mutex is still the holder's.
    assert (repo / ".scout-cache" / "commit.lock.d").is_dir()


def test_a_mutex_whose_holder_died_is_cleared(repo: Path, helper: Path, path_env: str) -> None:
    lock = repo / ".scout-cache" / "commit.lock.d"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(f"{_dead_pid()}\n", encoding="utf-8")
    (repo / "a.md").write_text("mine\n", encoding="utf-8")

    result = _run(helper, f"{_no_flock_bin(repo.parent)}", "msg", "a.md", LOCK_WAIT_SECS="5")

    assert result.returncode == 0, result.stderr
    assert "cleared stale commit mutex" in result.stderr
    assert _committed(repo) == {"a.md"}
    assert not lock.exists()


def test_an_old_mutex_with_no_holder_and_no_live_git_is_cleared(repo: Path, helper: Path, stubs: Path) -> None:
    """A mutex left by a session killed before it recorded its pid (or by an
    older helper that never did): cleared by age, when no git process is live."""
    _pgrep(stubs, live_git=False)
    lock = repo / ".scout-cache" / "commit.lock.d"
    lock.mkdir(parents=True)
    _age(lock, 600)
    (repo / "a.md").write_text("mine\n", encoding="utf-8")

    result = _run(helper, f"{stubs}:{_no_flock_bin(repo.parent)}", "msg", "a.md", LOCK_WAIT_SECS="5")

    assert result.returncode == 0, result.stderr
    assert "cleared stale commit mutex" in result.stderr
    assert not lock.exists()


def test_a_stale_git_index_lock_is_cleared(repo: Path, helper: Path, path_env: str, stubs: Path) -> None:
    _pgrep(stubs, live_git=False)
    index_lock = repo / ".git" / "index.lock"
    index_lock.touch()
    _age(index_lock, 600)
    (repo / "a.md").write_text("mine\n", encoding="utf-8")

    result = _run(helper, path_env, "msg", "a.md")

    assert result.returncode == 0, result.stderr
    assert "cleared stale" in result.stderr
    assert _committed(repo) == {"a.md"}
    assert not index_lock.exists()


def test_a_fresh_git_index_lock_is_left_alone(repo: Path, helper: Path, path_env: str, stubs: Path) -> None:
    """A young lock may belong to a peer mid-commit: never removed, the commit fails (5)."""
    _pgrep(stubs, live_git=False)
    index_lock = repo / ".git" / "index.lock"
    index_lock.touch()
    (repo / "a.md").write_text("mine\n", encoding="utf-8")

    result = _run(helper, path_env, "msg", "a.md")

    assert result.returncode == 5
    assert index_lock.exists()


def test_an_old_git_lock_is_left_alone_while_a_git_process_is_live(
    repo: Path, helper: Path, path_env: str, stubs: Path
) -> None:
    _pgrep(stubs, live_git=True)
    index_lock = repo / ".git" / "index.lock"
    index_lock.touch()
    _age(index_lock, 600)
    (repo / "a.md").write_text("mine\n", encoding="utf-8")

    result = _run(helper, path_env, "msg", "a.md")

    assert result.returncode == 5
    assert index_lock.exists()


def test_a_gitignored_path_is_skipped_with_a_clear_message(repo: Path, helper: Path, path_env: str) -> None:
    (repo / ".scout-cache").mkdir()
    (repo / ".scout-cache" / "scratch.md").write_text("cache\n", encoding="utf-8")
    (repo / "a.md").write_text("mine\n", encoding="utf-8")

    result = _run(helper, path_env, "msg", "a.md", ".scout-cache/")

    assert result.returncode == 0, result.stderr
    assert "skipping '.scout-cache/'" in result.stderr
    assert "gitignored" in result.stderr
    assert _committed(repo) == {"a.md"}


def test_only_gitignored_paths_is_nothing_to_commit(repo: Path, helper: Path, path_env: str) -> None:
    (repo / ".scout-cache").mkdir()
    (repo / ".scout-cache" / "scratch.md").write_text("cache\n", encoding="utf-8")

    result = _run(helper, path_env, "msg", ".scout-cache/scratch.md")

    assert result.returncode == 4
    assert "gitignored" in result.stderr


def test_a_path_that_does_not_exist_is_skipped(repo: Path, helper: Path, path_env: str) -> None:
    """A step's fixed path list may name a file this vault doesn't have yet."""
    (repo / "a.md").write_text("mine\n", encoding="utf-8")

    result = _run(helper, path_env, "msg", "a.md", "dreaming-proposals/", "archive.md")

    assert result.returncode == 0, result.stderr
    assert "skipping 'dreaming-proposals/'" in result.stderr
    assert _committed(repo) == {"a.md"}


def test_a_deleted_tracked_file_is_committed_as_a_deletion(repo: Path, helper: Path, path_env: str) -> None:
    (repo / "a.md").unlink()

    result = _run(helper, path_env, "msg", "a.md")

    assert result.returncode == 0, result.stderr
    assert "a.md" not in _git(repo, "ls-files").split()
    assert _committed(repo) == {"a.md"}


def test_scout_data_dir_overrides_the_installed_vault_path(tmp_path: Path, helper: Path, path_env: str) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    _git(other, "init", "-q")
    (other / "b.md").write_text("b\n", encoding="utf-8")

    result = _run(helper, path_env, "msg", "b.md", SCOUT_DATA_DIR=str(other))

    assert result.returncode == 0, result.stderr
    assert _committed(other) == {"b.md"}
