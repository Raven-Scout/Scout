"""End to end: a scheduled run can't get the owner's commit rules by unsetting SCOUT_MODE (#595).

A real runner (``run-*.sh`` with the real ``scout-lock.sh``) starts a "session"
(``claude-with-retry.sh``, stubbed) in the privacy fixture vault, which has the
real pre-commit hook installed. The session moves a note out of a local-only
path, which a scheduled run may not do (#562), and commits it with
``env -u SCOUT_MODE -u SCOUT_READER git commit`` and with ``SCOUT_MODE=``. The
hook still refuses it: the runner holds the session lock and its run marker for
the whole session. Once the run has ended, the owner's identical commit goes
through.
"""

from __future__ import annotations

import os
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


# The "session": two ways to commit the move with the run's env taken away. Each
# result lands in the vault's .scout-cache/ (gitignored), with the marker it saw.
SESSION = """#!/bin/bash
cd "$SCOUT_DATA_DIR" || exit 1
out=.scout-cache/e2e
mkdir -p "$out" notes
cp .scout-logs/.scout-session.lock.run "$out/marker" 2>/dev/null || true
git mv {positions} {moved}
env -u SCOUT_MODE -u SCOUT_READER git commit -q -m "move positions" >"$out/unset.out" 2>&1
echo $? >"$out/unset.rc"
SCOUT_MODE= SCOUT_READER=owner git commit -q -m "move positions" >"$out/blank.out" 2>&1
echo $? >"$out/blank.rc"
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
    _script(v / "scripts" / "claude-with-retry.sh", SESSION.format(positions=POSITIONS, moved=MOVED))
    return v


def _env() -> dict[str, str]:
    env = {k: v for k, v in git_env().items() if not k.startswith("SCOUT_")}
    return {**env, "SCOUT_FORCE_MODE": SLOT, "SCOUT_LOCK_POLL_SECS": "1", "SCOUT_LOCK_WAIT_SECS": "30"}


def _result(vault: Path, name: str) -> str:
    return (vault / ".scout-cache" / "e2e" / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("runner", ["run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl"])
def test_a_scheduled_runs_commit_with_its_env_taken_away_is_still_refused(vault: Path, runner: str) -> None:
    head = git(vault, "rev-parse", "HEAD")
    script = _render(TEMPLATES / runner, vault / runner.removesuffix(".tmpl"), vault)
    proc = subprocess.run([str(script)], env=_env(), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr

    assert _result(vault, "marker").split()[1] == SLOT, "the runner holds a run marker during the session"
    for how in ("unset", "blank"):
        out = _result(vault, f"{how}.out")
        assert _result(vault, f"{how}.rc").strip() != "0", out
        assert "[privacy-widening]" in out and "local-only" in out
        assert f"kb-lint: a scheduled run ({SLOT}) holds the session lock" in out
    assert git(vault, "rev-parse", "HEAD") == head
    assert not (vault / MARKER).exists(), "the runner removes its marker when it releases the lock"

    # The run has ended: the owner's identical commit is the owner's again.
    (vault / MOVED).parent.mkdir(parents=True, exist_ok=True)
    git(vault, "mv", POSITIONS, MOVED)
    owner = subprocess.run(
        ["git", "commit", "-q", "-m", "move positions"],
        cwd=vault,
        env={k: v for k, v in git_env().items() if not k.startswith("SCOUT_")},
        capture_output=True,
        text=True,
    )
    assert owner.returncode == 0, owner.stderr
    assert "holds the session lock" not in owner.stderr
    assert os.path.exists(vault / MOVED)
