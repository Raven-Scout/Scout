"""Every runner pins git's global config to the account's own ~/.gitconfig, or records why it didn't.

git reads its global config from ~/.gitconfig and ~/.config/git/config wherever HOME points, so a session command
that pointed HOME at a folder it wrote a `.gitconfig` into (`core.hooksPath=/dev/null`) skipped the commit hook. The
run gate denies a HOME change (#800); the pin makes git deaf to one too. It is the file git reads anyway, so nothing
else changes, and it is skipped where it would change something: GIT_CONFIG_GLOBAL already set (the eval harness's
/dev/null), an XDG git config git would also read, or a HOME that isn't the account's. Only the block is run here,
never a runner.
"""

from __future__ import annotations

import os
import pwd
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = REPO_ROOT / "templates"
RUNNERS = ("run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl", "run-respond.sh.tmpl")
START = "# git reads its global config from ~/.gitconfig"
END = 'echo "=== git config: $SCOUT_GIT_CONFIG_PIN ===" >> "$LOG_FILE"\n'
CLAUDE_LAUNCH = '"$SCOUT_DIR/scripts/claude-with-retry.sh"'
ACCOUNT_HOME = pwd.getpwuid(os.getuid()).pw_dir


def _block(name: str) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    start = text.index(START)
    return text[start : text.index(END, start) + len(END)]


def test_every_runner_has_the_same_block_before_claude_starts() -> None:
    blocks = {name: _block(name) for name in RUNNERS}
    assert len(set(blocks.values())) == 1
    for name in RUNNERS:
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert text.count(START) == 1
        assert text.index(START) < text.index(CLAUDE_LAUNCH), name


def _run(tmp_path: Path, env: dict[str, str], path: str = "/usr/bin:/bin") -> tuple[str, str, str]:
    log = tmp_path / "run.log"
    shown = 'printf "%s|%s" "$SCOUT_GIT_CONFIG_PIN" "${GIT_CONFIG_GLOBAL-unset}"\n'
    script = "set -euo pipefail\n" + _block(RUNNERS[0]) + shown
    out = subprocess.run(
        ["/bin/bash", "-c", script],
        env={"PATH": path, "LOG_FILE": str(log), **env},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    pin, value = out.stdout.split("|")
    return pin, value, log.read_text(encoding="utf-8")


def test_a_set_git_config_global_is_kept(tmp_path: Path) -> None:
    assert _run(tmp_path, {"HOME": ACCOUNT_HOME, "GIT_CONFIG_GLOBAL": "/dev/null"})[:2] == ("preset", "/dev/null")
    assert _run(tmp_path, {"HOME": ACCOUNT_HOME, "GIT_CONFIG_GLOBAL": ""})[:2] == ("preset", "")


def test_a_home_that_isnt_the_accounts_is_left_alone(tmp_path: Path) -> None:
    pin, value, log = _run(tmp_path, {"HOME": str(tmp_path)})
    assert (pin, value) == ("home", "unset")
    assert log == "=== git config: home ===\n"


def test_an_xdg_git_config_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / "git").mkdir()
    (tmp_path / "git" / "config").write_text("[user]\n\tname = Alex\n")
    assert _run(tmp_path, {"HOME": ACCOUNT_HOME, "XDG_CONFIG_HOME": str(tmp_path)})[:2] == ("xdg", "unset")


def test_an_account_it_cant_name_is_left_alone(tmp_path: Path) -> None:
    assert _run(tmp_path, {"HOME": ACCOUNT_HOME}, path=str(tmp_path))[:2] == ("unknown", "unset")


def test_the_accounts_own_file_is_pinned(tmp_path: Path) -> None:
    if Path(ACCOUNT_HOME, ".config", "git", "config").exists():
        pytest.skip("this account has an XDG git config, so the runner leaves git as it is")
    empty = tmp_path / "xdg"
    empty.mkdir()
    pin, value, log = _run(tmp_path, {"HOME": ACCOUNT_HOME, "XDG_CONFIG_HOME": str(empty)})
    assert (pin, value) == ("pinned", f"{ACCOUNT_HOME}/.gitconfig")
    assert log == "=== git config: pinned ===\n"


def test_git_reads_the_pinned_file_whatever_home_says(tmp_path: Path) -> None:
    """What the pin is for: with it, a HOME holding another `.gitconfig` changes nothing git reads."""
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    for folder, hooks in ((mine, "hooks"), (theirs, "/dev/null")):
        folder.mkdir()
        (folder / ".gitconfig").write_text(f"[core]\n\thooksPath = {hooks}\n")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(theirs), "GIT_CONFIG_NOSYSTEM": "1"}
    read = ["git", "config", "--global", "core.hooksPath"]
    assert subprocess.run(read, env=env, capture_output=True, text=True).stdout == "/dev/null\n"
    pinned = {**env, "GIT_CONFIG_GLOBAL": str(mine / ".gitconfig")}
    assert subprocess.run(read, env=pinned, capture_output=True, text=True).stdout == "hooks\n"


def _run_at_home(home: Path, env: dict[str, str] | None = None) -> tuple[str, str]:
    """The block with ``home`` as the account's home (only the password-database lookup is replaced), so a
    `.gitconfig` can be written for it."""
    block = _block(RUNNERS[0])
    lookup = 'eval "_scout_account_home=~$_scout_user"'
    assert block.count(lookup) == 1
    shown = 'printf "%s|%s" "$SCOUT_GIT_CONFIG_PIN" "${GIT_CONFIG_GLOBAL-unset}"\n'
    script = "set -euo pipefail\n" + block.replace(lookup, f"_scout_account_home={home}") + shown
    out = subprocess.run(
        ["/bin/bash", "-c", script],
        env={"PATH": "/usr/bin:/bin", "LOG_FILE": str(home / "run.log"), "HOME": str(home), **(env or {})},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    pin, value = out.stdout.split("|")
    return pin, value


def test_a_gitconfig_without_includes_is_pinned(tmp_path: Path) -> None:
    (tmp_path / ".gitconfig").write_text("[user]\n\tname = Alex\n# [include] in a comment is no include\n")
    assert _run_at_home(tmp_path) == ("pinned", f"{tmp_path}/.gitconfig")


@pytest.mark.parametrize(
    "config",
    [
        "[include]\n\tpath = ~/.gitconfig.local\n",
        '[includeIf "gitdir:~/work/"]\n\tpath = "~/work/.gitconfig"\n',
        "[INCLUDE]\n  PATH=~/x\n",
        "[include]\n\tpath = /etc/shared.gitconfig\n",  # which may include a `~` path of its own
    ],
)
def test_an_include_is_left_alone(tmp_path: Path, config: str) -> None:
    """git expands `~` in an include's path with HOME, pinned or not, in any file it includes: one pin can't keep
    them on the account's files."""
    (tmp_path / ".gitconfig").write_text("[user]\n\tname = Alex\n" + config)
    assert _run_at_home(tmp_path) == ("include", "unset")


def test_the_run_outcome_row_keeps_the_pin() -> None:
    text = (TEMPLATES / "scripts" / "run-outcome.sh.tmpl").read_text(encoding="utf-8")
    assert 'os.environ.get("SCOUT_GIT_CONFIG_PIN", "")' in text
    assert '("pinned", "preset", "xdg", "include", "home", "unknown")' in text
