"""Tests for scout.kb.hook — the vault pre-commit hook."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
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
        "for private links in shared files. Run scoutctl bootstrap doctor.",
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


def _hold_lock(vault: Path, pid: int, marker: str | None = None) -> None:
    """The session lock naming ``pid``, with a run marker beside it (#595)."""
    logs = vault / ".scout-logs"
    logs.mkdir(exist_ok=True)
    (logs / ".scout-session.lock").write_text(f"{pid}\n")
    if marker is not None:
        (logs / ".scout-session.lock.run").write_text(marker)


@pytest.fixture
def live(tmp_path):
    """Start a live process in the vault named like ``run-scout.sh`` or ``scripts/claude-with-retry.sh``."""
    procs: list[subprocess.Popen[bytes]] = []

    def start(vault: Path, rel: str, *args: str) -> int:
        script = vault / rel
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/bin/bash\nsleep 60\n")
        script.chmod(0o755)
        procs.append(subprocess.Popen([str(script), *args]))
        time.sleep(0.2)  # let bash exec, so ps shows the script's argv
        return procs[-1].pid

    yield start
    for proc in procs:
        proc.kill()
        proc.wait()


@pytest.mark.parametrize("which", SCENARIOS)
@pytest.mark.parametrize("owner_env", [{}, {"SCOUT_READER": "owner"}, {"SCOUT_MODE": ""}])
@pytest.mark.parametrize("marked_by", ["runner", "runner-no-marker", "wrapper", "claude"])
def test_a_commit_during_a_live_run_is_refused_when_the_check_cant_run_whatever_its_env(
    tmp_path, live, which: str, owner_env: dict[str, str], marked_by: str
) -> None:
    """Without scoutctl the hook can't tell whose commit it is, so any live run refuses it (fail closed)."""
    vault = _git_vault(tmp_path)
    if marked_by == "wrapper":
        live(vault, "scripts/claude-with-retry.sh")  # the runner was killed; its wrapper lives on
    elif marked_by == "claude":
        live(tmp_path, "claude", "--max-budget-usd", "25", "--name", "scout-x", "-p", f"Work in {vault}.")
    else:
        pid = live(vault, "run-scout.sh")
        _hold_lock(vault, pid, None if marked_by == "runner-no-marker" else f"{pid} morning-briefing\n")
    scoutctl, _reason = _scenario(tmp_path, which)
    proc = _run_in_vault(vault, scoutctl, **owner_env)
    assert proc.returncode == 1
    lines = [ln for ln in proc.stderr.splitlines() if ln.startswith("kb-lint:")]
    assert lines == [
        UNAVAILABLE,
        "kb-lint: a scheduled run is in progress, so this commit is refused until the check can run.",
    ]
    assert not (vault / ".scout-logs" / "kb-reads.jsonl").exists()


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def test_the_hook_script_cant_match_its_own_awk() -> None:
    """Linux ``ps`` prints awk's whole argv on one row, with ``-v root=<vault>`` beside the program text."""
    assert "--max-budget-usd" not in render_hook("/x/scoutctl")


@pytest.mark.parametrize("shell", [s for s in ("/bin/sh", "/bin/dash", "/usr/bin/dash") if Path(s).exists()])
def test_a_missing_lock_says_nothing_to_the_owner(tmp_path, shell: str) -> None:
    vault = _git_vault(tmp_path)
    script = tmp_path / "pre-commit"
    script.write_text(render_hook(str(tmp_path / "nope")))
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")}
    proc = subprocess.run([shell, str(script)], capture_output=True, text=True, env=env, cwd=vault)
    assert proc.returncode == 0
    assert "scout-session.lock" not in proc.stderr and "cannot open" not in proc.stderr


@pytest.mark.parametrize("rel", ["less-on-the-wrapper", "vim-on-the-wrapper"])
def test_a_process_only_naming_the_wrapper_leaves_the_owner_commit_alone(tmp_path, live, rel: str) -> None:
    vault = _git_vault(tmp_path)
    wrapper = vault / "scripts" / "claude-with-retry.sh"
    live(tmp_path, rel, str(wrapper))  # argv[1] names it, but a shell isn't running it
    proc = _run_in_vault(vault, str(tmp_path / "nope"))
    assert proc.returncode == 0 and "WARNING" in proc.stderr


@pytest.mark.parametrize("holder", ["dead", "not-a-runner", "another-vaults-wrapper"])
def test_no_live_run_leaves_the_owner_commit_alone(tmp_path, live, holder: str) -> None:
    vault = _git_vault(tmp_path)
    if holder == "dead":
        pid = _dead_pid()
        _hold_lock(vault, pid, f"{pid} morning-briefing\n")
    elif holder == "not-a-runner":
        _hold_lock(vault, os.getpid(), f"{os.getpid()} morning-briefing\n")  # e.g. scoutctl bootstrap, or a reused PID
    else:
        other = tmp_path / "other"
        other.mkdir()
        live(other, "scripts/claude-with-retry.sh")
    proc = _run_in_vault(vault, str(tmp_path / "nope"))
    assert proc.returncode == 0 and "WARNING" in proc.stderr


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


@pytest.mark.skipif(not _scoutctl_has_kb(), reason="installed scoutctl predates `kb` (passes after merge)")
def test_the_real_hook_names_a_misspelt_path_modes_key(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_lint:\n  pathmodes:\n    - {glob: 'knowledge-base/**', mode: block}\n")
    install_hook(kb_repo.root)
    kb_repo.write("knowledge-base/topics/note.md", "A note.\n")
    kb_repo.git("add", "-A")
    proc = subprocess.run(["git", "commit", "-q", "-m", "note"], cwd=kb_repo.root, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "kb-lint: config: kb_lint has unknown key(s) ['pathmodes']" in proc.stderr


@pytest.mark.skipif(not _scoutctl_has_kb(), reason="installed scoutctl predates `kb` (passes after merge)")
def test_the_real_hook_applies_path_modes_and_shows_a_config_mistake(kb_repo) -> None:
    kb_repo.write(
        "scout-config.yaml",
        "kb_lint:\n  mode: report\n  path_modes:\n"
        "    - {glob: 'knowledge-base/projects/**', mode: block}\n"
        "    - {glob: 'knowledge-base/topics/**', mode: Block}\n",
    )
    install_hook(kb_repo.root)
    kb_repo.write("knowledge-base/topics/big.md", "x" * 20000 + "\n")
    kb_repo.git("add", "-A")
    commit = ["git", "commit", "-q", "-m", "big"]
    proc = subprocess.run(commit, cwd=kb_repo.root, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "kb-lint: config: path_modes rule 2" in proc.stderr and "[size] (report)" in proc.stderr
    kb_repo.write("knowledge-base/projects/big.md", "x" * 20000 + "\n")
    kb_repo.git("add", "-A")
    proc = subprocess.run(commit, cwd=kb_repo.root, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "Commit blocked." in proc.stderr


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
