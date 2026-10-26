"""Tests for scout.kb.hook — the vault pre-commit hook."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scout.kb.hook import MARKER, HookConflict, hooks_dir, install_hook, render_hook


def test_install_writes_executable_marked_hook(kb_repo) -> None:
    path = install_hook(kb_repo.root, scoutctl="/opt/scout/bin/scoutctl")
    assert path == hooks_dir(kb_repo.root) / "pre-commit"
    text = path.read_text()
    assert MARKER in text and "/opt/scout/bin/scoutctl" in text
    assert path.stat().st_mode & stat.S_IXUSR


def test_reinstall_overwrites_own_hook(kb_repo) -> None:
    install_hook(kb_repo.root, scoutctl="/a")
    path = install_hook(kb_repo.root, scoutctl="/b")
    assert "/b" in path.read_text()


def test_refuses_foreign_hook(kb_repo) -> None:
    d = hooks_dir(kb_repo.root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "pre-commit").write_text("#!/bin/sh\necho mine\n")
    with pytest.raises(HookConflict):
        install_hook(kb_repo.root, scoutctl="/a")
    assert "echo mine" in (d / "pre-commit").read_text()


def _fake_scoutctl(tmp_path: Path, rc: int, *, prints_kb_lint: bool = True) -> Path:
    p = tmp_path / "scoutctl"
    line = 'echo "kb-lint: 1 blocking, 0 warning(s) (mode: block)" >&2' if prints_kb_lint else "echo called >&2"
    p.write_text(f"#!/bin/sh\n{line}\nexit {rc}\n")
    p.chmod(0o755)
    return p


def _run_hook_script(tmp_path: Path, scoutctl: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "pre-commit"
    script.write_text(render_hook(scoutctl))
    script.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")}
    # Run outside any git repo, so a "could not run" audit row can't land in the developer's checkout.
    return subprocess.run([str(script)], capture_output=True, text=True, env=env, cwd=tmp_path)


def test_hook_passes_through_block(tmp_path) -> None:
    assert _run_hook_script(tmp_path, str(_fake_scoutctl(tmp_path, 1))).returncode == 1


# ----- when the check can't run (#453, spec §8.3; Jordan, 2026-10-22: option B) -------------

UNAVAILABLE = "kb-lint: privacy check could not run"


def _git_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    return vault


def _run_in_vault(vault: Path, scoutctl: str, **extra: str) -> subprocess.CompletedProcess[str]:
    script = vault.parent / "pre-commit"
    script.write_text(render_hook(scoutctl))
    script.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"), **extra}
    return subprocess.run(["/bin/sh", str(script)], capture_output=True, text=True, env=env, cwd=vault)


def _scenario(tmp_path: Path, which: str) -> tuple[str, str]:
    """(scoutctl path, the reason the warning names)."""
    if which == "missing":
        return str(tmp_path / "nope"), "scoutctl not found"
    if which == "exit-70":
        return str(_fake_scoutctl(tmp_path, 70, prints_kb_lint=False)), "engine error 70"
    return str(_fake_scoutctl(tmp_path, 1, prints_kb_lint=False)), "engine error 1"  # a crash before cli.main


SCENARIOS = ["missing", "exit-70", "exit-1-no-verdict"]


@pytest.mark.parametrize("which", SCENARIOS)
@pytest.mark.parametrize(
    "run_env",
    [
        {"SCOUT_MODE": "morning-briefing"},
        {"SCOUT_READER": "autonomous"},
        {"SCOUT_READER": " Autonomous "},
        {"SCOUT_READER": "agent:person:priya@example.com"},
        {"SCOUT_READER": "garbage"},
        {"SCOUT_READER": ""},
        {"SCOUT_READER": "owner", "SCOUT_MODE": "dreaming"},
    ],
)
def test_a_run_commit_is_refused_when_the_check_cant_run(tmp_path, which: str, run_env: dict[str, str]) -> None:
    vault = _git_vault(tmp_path)
    scoutctl, _reason = _scenario(tmp_path, which)
    proc = _run_in_vault(vault, scoutctl, **run_env)
    assert proc.returncode == 1
    assert [ln for ln in proc.stderr.splitlines() if ln.startswith("kb-lint:")] == [UNAVAILABLE]
    assert not (vault / ".scout-logs" / "kb-reads.jsonl").exists()


@pytest.mark.parametrize("which", SCENARIOS)
@pytest.mark.parametrize("owner_env", [{}, {"SCOUT_READER": "owner"}, {"SCOUT_MODE": ""}])
def test_an_owner_commit_goes_on_with_a_warning_and_an_audit_row(
    tmp_path, which: str, owner_env: dict[str, str]
) -> None:
    import json
    from datetime import UTC, datetime, timedelta

    vault = _git_vault(tmp_path)
    scoutctl, reason = _scenario(tmp_path, which)
    proc = _run_in_vault(vault, scoutctl, **owner_env)
    assert proc.returncode == 0
    lines = [ln for ln in proc.stderr.splitlines() if ln.startswith("kb-lint:")]
    assert lines == [
        UNAVAILABLE,
        f"kb-lint: WARNING: the privacy check could not run ({reason}); this commit was NOT checked "
        "for private links in shared files. Run scoutctl doctor.",
    ]
    log = vault / ".scout-logs" / "kb-reads.jsonl"
    (row,) = [json.loads(ln) for ln in log.read_text().splitlines()]
    ts = datetime.fromisoformat(row.pop("ts"))
    assert ts.utcoffset() == timedelta(0) and abs(datetime.now(UTC) - ts) < timedelta(minutes=5)
    assert row == {
        "reader": "owner",
        "command": "kb lint --staged",
        "surface": None,
        "args_hash": None,
        "paths_returned": 0,
        "paths_denied": 0,
        "private_paths": 0,
        "blocked": False,
        "scout_mode": None,
        "event": "privacy.check_unavailable",
    }
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_the_audit_row_goes_to_the_main_vault_from_a_linked_worktree(tmp_path) -> None:
    vault = _git_vault(tmp_path)
    subprocess.run(
        [
            "git",
            "-C",
            str(vault),
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(vault), "worktree", "add", "-q", str(wt)], check=True)
    proc = _run_in_vault(wt, str(tmp_path / "nope"))
    assert proc.returncode == 0
    assert (vault / ".scout-logs" / "kb-reads.jsonl").exists()
    assert not (wt / ".scout-logs").exists()


def test_a_failed_audit_write_never_fails_the_owner_commit(tmp_path) -> None:
    vault = _git_vault(tmp_path)
    (vault / ".scout-logs").write_text("a file where the directory should be")
    proc = _run_in_vault(vault, str(tmp_path / "nope"))
    assert proc.returncode == 0
    assert "WARNING" in proc.stderr


def test_outside_a_git_repo_the_owner_commit_still_goes_on(tmp_path) -> None:
    proc = _run_hook_script(tmp_path, str(tmp_path / "nope"))
    assert proc.returncode == 0 and UNAVAILABLE in proc.stderr
    assert not (tmp_path / ".scout-logs").exists()


def test_hook_scoutctl_path_with_space_and_dollar(tmp_path) -> None:
    weird = tmp_path / "weird $dir"
    weird.mkdir()
    scoutctl = weird / "scoutctl"
    scoutctl.write_text('#!/bin/sh\necho "kb-lint: 0 blocking, 0 warning(s) (mode: report)" >&2\nexit 0\n')
    scoutctl.chmod(0o755)
    proc = _run_hook_script(tmp_path, str(scoutctl))
    assert proc.returncode == 0
    assert "0 blocking" in proc.stderr


def _scoutctl_has_kb() -> bool:
    exe = Path(sys.executable).parent / "scoutctl"
    return exe.exists() and subprocess.run([str(exe), "kb", "--help"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _scoutctl_has_kb(), reason="installed scoutctl predates `kb` (passes after merge)")
def test_hook_blocks_real_commit(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_lint:\n  mode: block\n")
    install_hook(kb_repo.root)  # default: the scoutctl next to this interpreter
    kb_repo.write("knowledge-base/big.md", "x" * 20000 + "\n")
    kb_repo.git("add", "-A")
    proc = subprocess.run(["git", "commit", "-q", "-m", "big"], cwd=kb_repo.root, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "kb-lint" in proc.stderr


def test_bootstrap_stage_installs_into_git_vault(kb_repo, tmp_path) -> None:
    from scout.scripts.bootstrap import BootstrapConfig, _stage_install_git_hook

    cfg = BootstrapConfig(
        vault=kb_repo.root,
        plugin_root=tmp_path / "plugin",
        instance_name="T",
        instance_name_lower="t",
        user_name="T",
        user_email="t@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )
    _stage_install_git_hook(cfg)
    assert MARKER in (hooks_dir(kb_repo.root) / "pre-commit").read_text()


def _stage_cfg(vault, plugin_root):
    from scout.scripts.bootstrap import BootstrapConfig

    return BootstrapConfig(
        vault=vault,
        plugin_root=plugin_root,
        instance_name="T",
        instance_name_lower="t",
        user_name="T",
        user_email="t@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


def test_bootstrap_stage_prefers_the_engine_launcher(kb_repo, tmp_path) -> None:
    """App-managed engines live outside plugin_root/.venv; the launcher resolves them."""
    from scout.scripts.bootstrap import _stage_install_git_hook

    plugin = tmp_path / "plugin"
    launcher = plugin / "engine" / "bin" / "scoutctl"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    _stage_install_git_hook(_stage_cfg(kb_repo.root, plugin))
    text = (hooks_dir(kb_repo.root) / "pre-commit").read_text()
    assert f"SCOUTCTL={launcher}" in text


def test_bootstrap_stage_falls_back_to_the_venv_scoutctl(kb_repo, tmp_path) -> None:
    from scout.scripts.bootstrap import _stage_install_git_hook

    plugin = tmp_path / "plugin"
    _stage_install_git_hook(_stage_cfg(kb_repo.root, plugin))
    text = (hooks_dir(kb_repo.root) / "pre-commit").read_text()
    assert f"SCOUTCTL={plugin / '.venv' / 'bin' / 'scoutctl'}" in text


def test_bootstrap_stage_skips_non_git_vault(tmp_path) -> None:
    from scout.scripts.bootstrap import BootstrapConfig, _stage_install_git_hook

    vault = tmp_path / "v"
    vault.mkdir()
    cfg = BootstrapConfig(
        vault=vault,
        plugin_root=tmp_path,
        instance_name="T",
        instance_name_lower="t",
        user_name="T",
        user_email="t@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )
    _stage_install_git_hook(cfg)  # must not raise
    assert not (vault / ".git").exists()
