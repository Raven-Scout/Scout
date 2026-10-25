"""Integration test for templates/scripts/post-session-backfill.sh.tmpl.

Renders the template (substituting SCOUT_DIR + SCOUTCTL_BIN), runs it against
a temp git vault, and asserts the backfill prefixes open tasks and commits
exactly once — idempotently. Mirrors the deterministic session-end guarantee
behind scout-app issue #10.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/scout-plugin
TEMPLATE = REPO_ROOT / "templates" / "scripts" / "post-session-backfill.sh.tmpl"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _render(tmpl: Path, scout_dir: Path, scoutctl_bin: str) -> Path:
    text = tmpl.read_text(encoding="utf-8")
    text = text.replace("{{SCOUT_DIR}}", str(scout_dir)).replace("{{SCOUTCTL_BIN}}", scoutctl_bin)
    out = scout_dir / "scripts" / "post-session-backfill.sh"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    return out


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    scout_dir = tmp_path / "Scout"
    (scout_dir / "action-items").mkdir(parents=True)
    daily = scout_dir / "action-items" / "action-items-2026-06-04.md"
    daily.write_text(
        "# Tuesday, June 4\n\n"
        "## 🔴 Urgent\n\n"
        "- [ ] **Unprefixed urgent task** — needs an id\n"
        "- [ ] [#AB12] **Already prefixed** — leave me\n",
        encoding="utf-8",
    )
    _git(scout_dir, "init", "-q")
    _git(scout_dir, "config", "user.email", "test@example.com")
    _git(scout_dir, "config", "user.name", "test")
    _git(scout_dir, "add", "-A")
    _git(scout_dir, "commit", "-q", "-m", "seed")
    return scout_dir


def test_backfill_adds_prefix_and_commits_once(vault: Path) -> None:
    candidate = Path(sys.executable).with_name("scoutctl")
    scoutctl = str(candidate) if candidate.exists() else shutil.which("scoutctl")
    if not scoutctl:
        pytest.skip("scoutctl console script not found (looked next to sys.executable and on PATH)")
    script = _render(TEMPLATE, vault, scoutctl)

    # The wrapper defaults (no arg) to ET-local "today"; the seeded daily file is
    # dated 2026-06-04 so the test passes its explicit path as the $1 override to
    # stay deterministic regardless of the real wall-clock date.
    daily_path = str(vault / "action-items" / "action-items-2026-06-04.md")
    env = {**os.environ, "SCOUT_DATA_DIR": str(vault)}
    before = _git(vault, "rev-list", "--count", "HEAD").strip()

    r1 = subprocess.run([str(script), daily_path], env=env, capture_output=True, text=True)
    assert r1.returncode == 0, r1.stderr

    daily = (vault / "action-items" / "action-items-2026-06-04.md").read_text()

    assert re.search(r"- \[ \] \[#[0-9A-HJKMNP-TV-Z]{4}\] \*\*Unprefixed urgent task\*\*", daily)
    assert "[#AB12]" in daily

    after = _git(vault, "rev-list", "--count", "HEAD").strip()
    assert int(after) == int(before) + 1, "exactly one backfill commit expected"

    r2 = subprocess.run([str(script), daily_path], env=env, capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr
    after2 = _git(vault, "rev-list", "--count", "HEAD").strip()
    assert after2 == after, "second run must be a no-op"


def test_does_not_commit_a_dying_sessions_wreckage(vault: Path) -> None:
    """A session that dies mid-write must not have its truncation committed.

    Regression for the 2026-10-24 data loss: a briefing died on a network error
    after rewriting the daily file down to its run-notes section and before
    writing the item list back. This script then staged all of action-items/
    and committed the 807-line deletion under "chore: backfill prefixes".
    backfill-prefixes itself had changed nothing.
    """
    candidate = Path(sys.executable).with_name("scoutctl")
    scoutctl = str(candidate) if candidate.exists() else shutil.which("scoutctl")
    if not scoutctl:
        pytest.skip("scoutctl console script not found (looked next to sys.executable and on PATH)")
    script = _render(TEMPLATE, vault, scoutctl)
    daily = vault / "action-items" / "action-items-2026-06-04.md"
    daily_path = str(daily)
    env = {**os.environ, "SCOUT_DATA_DIR": str(vault)}

    # Prefix everything first, so the second invocation is a genuine no-op and
    # the only pending change is the one the dead session left behind.
    subprocess.run([str(script), daily_path], env=env, capture_output=True, text=True, check=True)
    assert not _git(vault, "status", "--porcelain", "--", "action-items").strip()

    before = _git(vault, "rev-list", "--count", "HEAD").strip()
    survivor = daily.read_text(encoding="utf-8")
    daily.write_text("## 🪵 Run notes & connector availability\n- the run died here\n", encoding="utf-8")

    r = subprocess.run([str(script), daily_path], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    after = _git(vault, "rev-list", "--count", "HEAD").strip()
    assert after == before, "a truncation the backfill did not make must not be committed"
    assert _git(vault, "status", "--porcelain", "--", "action-items").strip(), (
        "the truncation must stay visible in the working tree, not be buried in a commit"
    )
    assert "leaving pre-existing uncommitted changes alone" in r.stderr
    committed = _git(vault, "show", "HEAD:action-items/action-items-2026-06-04.md")
    assert committed == survivor, "the committed daily file must still hold the item list"
