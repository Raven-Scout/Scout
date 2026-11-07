"""End to end: a scheduled run can't get the owner's commit rules by hiding that it's a run (#595).

A real runner (``run-*.sh`` with the real ``scout-lock.sh``) starts a "session"
(``claude-with-retry.sh``, stubbed) in the privacy fixture vault, which has the
real pre-commit hook installed. The session moves a note out of a local-only
path, which a scheduled run may not do (#562), and commits it with
``env -u SCOUT_MODE -u SCOUT_READER git commit`` after one more step:

- none, or blanking the variables instead of unsetting them;
- deleting the run marker by a glob;
- killing the runner holding the session lock (the session lives on);
- double-forking the commit, so git is re-parented and has no run in its ancestry.

The hook refuses every one, and the routes that break the lock leave a line in
``failures.log``. Once the run has ended, the owner's identical commit goes through.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scout.kb.hook import install_hook
from scout.kb.ontology import clear_frontmatter_cache
from tests.unit.privacy_vault_helpers import build_privacy_vault, git, git_env

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
TEMPLATES = REPO_ROOT / "templates"
SLOT = "morning-briefing"
POSITIONS = "trading/positions.md"  # local-only through the fixture overlay's local_only_add
MOVED = "notes/positions.md"
MARKER = ".scout-logs/.scout-session.lock.run"


def _scoutctl_has_kb() -> bool:
    exe = Path(sys.executable).parent / "scoutctl"
    return exe.exists() and subprocess.run([str(exe), "kb", "--help"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _scoutctl_has_kb(), reason="needs the engine's scoutctl for the real hook")


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _render(tmpl: Path, out: Path, scout_dir: Path) -> Path:
    text = tmpl.read_text(encoding="utf-8")
    for placeholder, value in {
        "{{SCOUT_DIR}}": str(scout_dir),
        "{{CLAUDE_BIN}}": "/usr/bin/true",  # never reached — the retry wrapper is stubbed
        "{{SCOUTCTL_BIN}}": str(scout_dir / "bin" / "scoutctl"),
        "{{INSTANCE_NAME_LOWER}}": "scout",
        "{{INSTANCE_NAME}}": "Scout",
        "{{MAX_BUDGET}}": "25",
        "{{USER_NAME}}": "Alex",
        "{{USER_SLACK_ID}}": "U0123456789",
    }.items():
        text = text.replace(placeholder, value)
    assert "{{" not in text
    return _script(out, text)


COMMIT = 'env -u SCOUT_MODE -u SCOUT_READER git commit -q -m "move positions" >"$out/commit.out" 2>&1'
ROUTES = {
    "unset": COMMIT,
    "blank": 'SCOUT_MODE= SCOUT_READER=owner git commit -q -m "move positions" >"$out/commit.out" 2>&1',
    "rm-marker": f"rm -f .scout-logs/.*.run\n{COMMIT}",
    "kill-holder": f'kill -9 "$(cat .scout-logs/.scout-sess*n.lock)"\nsleep 0.5\n{COMMIT}',
    # The outer subshell exits at once, so git's parent is re-parented to init/launchd.
    "double-fork": f'( ( {COMMIT}; echo $? >"$out/commit.rc" ) & )\n'
    'for _ in $(seq 1 600); do [ -s "$out/commit.rc" ] && break; sleep 0.1; done',
}

# The "session". Its result lands in the vault's .scout-cache/ (gitignored),
# with the marker it saw when it started.
SESSION = """#!/bin/bash
cd "$SCOUT_DATA_DIR" || exit 1
out=.scout-cache/e2e
mkdir -p "$out" notes
cp .scout-logs/.scout-session.lock.run "$out/marker" 2>/dev/null || true
git mv {positions} {moved}
{route}
rc=$?
[ -s "$out/commit.rc" ] || echo "$rc" >"$out/commit.rc"
git reset -q --hard
exit 0
"""


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    v = build_privacy_vault(tmp_path)
    with (v / ".git" / "info" / "exclude").open("a", encoding="utf-8") as fh:
        fh.write(".scout-logs/\n.scout-cache/\nbin/\nscripts/\nrun-*.sh\n")
    install_hook(v)  # the scoutctl next to this interpreter
    _script(v / "bin" / "scoutctl", "#!/bin/bash\nexit 0\n")  # the runner's own scoutctl calls
    _render(TEMPLATES / "scripts" / "run-outcome.sh.tmpl", v / "scripts" / "run-outcome.sh", v)
    _render(TEMPLATES / "scripts" / "scout-lock.sh.tmpl", v / "scripts" / "scout-lock.sh", v)
    return v


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in git_env().items() if not k.startswith("SCOUT_")}


def _run(vault: Path, runner: str, route: str) -> subprocess.CompletedProcess[str]:
    session = SESSION.format(positions=POSITIONS, moved=MOVED, route=ROUTES[route])
    _script(vault / "scripts" / "claude-with-retry.sh", session)
    script = _render(TEMPLATES / runner, vault / runner.removesuffix(".tmpl"), vault)
    env = {**_clean_env(), "SCOUT_FORCE_MODE": SLOT, "SCOUT_LOCK_POLL_SECS": "1", "SCOUT_LOCK_WAIT_SECS": "30"}
    return subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=180)


def _result(vault: Path, name: str) -> str:
    return (vault / ".scout-cache" / "e2e" / name).read_text(encoding="utf-8")


def _failures(vault: Path) -> str:
    path = vault / ".scout-logs" / "failures.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _assert_refused(vault: Path, head: str, mode: str = SLOT) -> None:
    out = _result(vault, "commit.out")
    assert _result(vault, "commit.rc").strip() != "0", out
    assert "[privacy-widening]" in out and "local-only" in out
    assert f"kb-lint: this commit comes from a scheduled run ({mode})" in out
    assert git(vault, "rev-parse", "HEAD") == head


def _assert_owner_commits(vault: Path) -> None:
    """The run has ended: the owner's identical commit is the owner's again."""
    (vault / MOVED).parent.mkdir(parents=True, exist_ok=True)
    git(vault, "mv", POSITIONS, MOVED)
    owner = subprocess.run(
        ["git", "commit", "-q", "-m", "move positions"], cwd=vault, env=_clean_env(), capture_output=True, text=True
    )
    assert owner.returncode == 0, owner.stderr
    assert "comes from a scheduled run" not in owner.stderr
    assert (vault / MOVED).exists()


@pytest.mark.parametrize("runner", ["run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl"])
@pytest.mark.parametrize("route", ["unset", "blank"])
def test_a_runs_commit_with_its_env_taken_away_is_still_refused(vault: Path, runner: str, route: str) -> None:
    head = git(vault, "rev-parse", "HEAD")
    proc = _run(vault, runner, route)
    assert proc.returncode == 0, proc.stderr
    assert _result(vault, "marker").split()[1] == SLOT, "the runner holds a run marker during the session"
    _assert_refused(vault, head)
    assert not (vault / MARKER).exists(), "the runner removes its marker when it releases the lock"
    assert _failures(vault) == "", "an untouched run leaves no tamper line"
    _assert_owner_commits(vault)


def test_deleting_the_marker_changes_nothing_and_is_logged_at_release(vault: Path) -> None:
    head = git(vault, "rev-parse", "HEAD")
    proc = _run(vault, "run-scout.sh.tmpl", "rm-marker")
    assert proc.returncode == 0, proc.stderr
    _assert_refused(vault, head, mode="scheduled")  # the marker only named the mode
    assert "run marker" in _failures(vault) and SLOT in _failures(vault)
    _assert_owner_commits(vault)


def test_killing_the_runner_changes_nothing_and_the_next_run_logs_it(vault: Path) -> None:
    head = git(vault, "rev-parse", "HEAD")
    _run(vault, "run-scout.sh.tmpl", "kill-holder")  # killed: no exit code to check
    _assert_refused(vault, head)
    # The next run finds the dead holder's lock and says so.
    proc = _run(vault, "run-scout.sh.tmpl", "blank")
    assert proc.returncode == 0, proc.stderr
    assert "died without releasing the session lock" in _failures(vault)
    _assert_owner_commits(vault)


def test_a_double_forked_commit_during_a_run_is_still_refused(vault: Path) -> None:
    head = git(vault, "rev-parse", "HEAD")
    proc = _run(vault, "run-scout.sh.tmpl", "double-fork")
    assert proc.returncode == 0, proc.stderr
    _assert_refused(vault, head)
    _assert_owner_commits(vault)
