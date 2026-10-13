"""The install/upgrade pipeline makes the vault a git repo (#433).

Nothing else ever ran `git init`: the git-setup phase only sets the identity,
so a fresh vault (on Linux and macOS alike) stayed a plain folder, every run
commit failed, and the kb-lint pre-commit hook was never installed.

The hermetic HOME (tests/conftest.py) has no ~/.gitconfig, so these runs see
what a fresh Linux host sees: no global git identity.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from scout.kb.hook import MARKER, hooks_dir
from scout.scripts import bootstrap
from scout.scripts.bootstrap import BootstrapConfig, _stage_git_init, install, upgrade

PLUGIN = Path(__file__).parent.parent.parent.parent


def _config(vault: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=PLUGIN,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="linux",
        plugin_version="0.4.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True, text=True).stdout


def test_install_makes_the_vault_a_repo_with_one_clean_commit(tmp_path):
    vault = tmp_path / "Scout"
    install(_config(vault))

    assert (vault / ".git").is_dir()
    assert _git(vault, "rev-list", "--count", "HEAD").strip() == "1"
    # The commit holds the whole install, scout-config.yaml included; nothing is left over.
    assert _git(vault, "status", "--porcelain") == ""
    assert "scout-config.yaml" in _git(vault, "ls-files")
    assert ".scout-state/install-incomplete" not in _git(vault, "ls-files")


def test_install_sets_the_run_identity_locally(tmp_path):
    vault = tmp_path / "Scout"
    install(_config(vault))

    # The same identity the git-setup phase sets, and only in the vault's own config.
    assert _git(vault, "config", "--local", "user.email").strip() == "test@example.com"
    assert _git(vault, "config", "--local", "user.name").strip() == "TestScout Bot"
    assert _git(vault, "log", "-1", "--format=%an <%ae>").strip() == "TestScout Bot <test@example.com>"


def test_install_installs_the_kb_lint_hook_in_the_same_run(tmp_path):
    vault = tmp_path / "Scout"
    install(_config(vault))

    assert MARKER in (hooks_dir(vault) / "pre-commit").read_text()


def test_upgrade_repairs_a_vault_that_was_never_initialized(tmp_path):
    """A vault installed before #433 has no .git; the next upgrade (the headless one too) fixes it."""
    vault = tmp_path / "Scout"
    install(_config(vault))
    shutil.rmtree(vault / ".git")

    upgrade(_config(vault))

    assert _git(vault, "rev-list", "--count", "HEAD").strip() == "1"
    assert MARKER in (hooks_dir(vault) / "pre-commit").read_text()


def test_an_existing_repo_is_left_alone(kb_repo):
    before = _git(kb_repo.root, "rev-parse", "HEAD")
    kb_repo.write("knowledge-base/new.md", "x\n")

    _stage_git_init(_config(kb_repo.root))

    assert _git(kb_repo.root, "rev-parse", "HEAD") == before
    assert _git(kb_repo.root, "config", "--local", "user.name").strip() == "T"
    assert "knowledge-base/new.md" in _git(kb_repo.root, "status", "--porcelain", "-uall")


def test_commit_signing_in_the_global_config_does_not_block_the_first_commit(tmp_path):
    home = Path.home()
    (home / ".gitconfig").write_text("[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = false\n")
    vault = tmp_path / "Scout"
    install(_config(vault))

    assert _git(vault, "rev-list", "--count", "HEAD").strip() == "1"


def test_a_global_hooks_path_does_not_run_during_the_first_commit(tmp_path):
    hooks = tmp_path / "global-hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    (Path.home() / ".gitconfig").write_text(f"[core]\n\thooksPath = {hooks}\n")
    vault = tmp_path / "Scout"
    install(_config(vault))

    assert _git(vault, "rev-list", "--count", "HEAD").strip() == "1"


def test_no_git_on_the_host_warns_and_never_fails_the_install(tmp_path, monkeypatch, capsys):
    real_which = bootstrap.shutil.which
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name, *a, **k: None if name == "git" else real_which(name))
    vault = tmp_path / "Scout"
    install(_config(vault))

    assert not (vault / ".git").exists()
    assert (vault / "scout-config.yaml").exists()
    assert "git" in capsys.readouterr().err


def test_a_failing_git_warns_and_never_fails_the_install(tmp_path, monkeypatch, capsys):
    fake = tmp_path / "bin" / "git"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\necho 'fatal: boom' >&2\nexit 128\n")
    fake.chmod(0o755)
    real_which = bootstrap.shutil.which
    monkeypatch.setattr(
        bootstrap.shutil, "which", lambda name, *a, **k: str(fake) if name == "git" else real_which(name)
    )
    vault = tmp_path / "Scout"
    install(_config(vault))

    assert (vault / "scout-config.yaml").exists()
    err = capsys.readouterr().err
    assert "could not initialize git" in err and "boom" in err
