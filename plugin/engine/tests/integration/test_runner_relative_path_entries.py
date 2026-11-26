"""A manual run never runs a program planted in the vault.

Scheduled runs get a fixed absolute PATH from their plist, but a manual one (the
scout-briefing skill, the app's Run now) inherits the user's PATH with the vault as
cwd. A relative entry there (`.`, `node_modules/.bin`, an empty one) would let a
`python3`, `date` or `mkdir` planted in the vault run in place of the real one: the
runner calls them by name, and so does scout/scripts/scout-lock.sh (`python3 -I` for the
awake clock, on every lock sample and at each run's start).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.integration.test_scout_lock import RUN_TIMEOUT_S, RUNNERS, SLOT, TEMPLATES, _render, _script
from tests.unit.test_relative_path_entries import PLANTED

# Not "./bin": the vault's bin/ holds this harness's stand-in scoutctl, which the runner calls by
# absolute path anyway.
RELATIVE = [".", "node_modules/.bin", ""]
EXTRA_PLANTED = ["date", "ps", "stat", "awk", "touch", "id", "kill", "sleep", "mktemp", "jq", "timeout"]


def _plant(vault: Path) -> Path:
    marker = vault.parent / "planted-ran"
    for folder in (vault, vault / "node_modules" / ".bin"):
        folder.mkdir(parents=True, exist_ok=True)
        for name in (*PLANTED, *EXTRA_PLANTED):
            if name == "scoutctl" and folder == vault:
                continue
            _script(folder / name, f'#!/bin/sh\necho "{folder.name}/{name} $*" >>"{marker}"\necho PLANTED\nexit 0\n')
    return marker


def _hostile_path() -> str:
    absolute = [p for p in os.environ.get("PATH", "").split(":") if p.startswith("/")]
    return ":".join(["", *RELATIVE, *absolute, *RELATIVE, ""])


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    _script(v / "bin" / "scoutctl", "#!/bin/bash\nexit 0\n")
    _render(TEMPLATES / "scripts" / "run-outcome.sh.tmpl", v / "scout" / "scripts" / "run-outcome.sh", v)
    _render(TEMPLATES / "scripts" / "scout-lock.sh.tmpl", v / "scout" / "scripts" / "scout-lock.sh", v)
    _script(v / "scout" / "scripts" / "claude-with-retry.sh", f'#!/bin/bash\ntouch "{v}/session-ran"\n')
    return v


def _assert_nothing_planted_ran(marker: Path, out: subprocess.CompletedProcess[str]) -> None:
    ran = marker.read_text() if marker.exists() else ""
    assert not ran, f"planted programs ran:\n{ran}\nstdout={out.stdout!r}\nstderr={out.stderr!r}"
    assert "PLANTED" not in out.stdout + out.stderr


@pytest.mark.parametrize("runner", sorted(RUNNERS))
def test_a_manual_run_from_the_vault_never_runs_a_planted_program(vault: Path, runner: str) -> None:
    script = _render(TEMPLATES / runner, vault / "scout" / "runners" / runner.removesuffix(".tmpl"), vault)
    marker = _plant(vault)

    out = subprocess.run(
        [str(script)],
        env={**os.environ, "PATH": _hostile_path(), "SCOUT_FORCE_MODE": SLOT, "SCOUT_LOCK_WAIT_SECS": "30"},
        cwd=vault,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
        check=False,
    )

    _assert_nothing_planted_ran(marker, out)
    assert out.returncode == 0, out
    assert (vault / "session-ran").exists(), out


def test_scout_lock_sourced_alone_takes_python3_only_from_an_absolute_entry(vault: Path) -> None:
    """Anything else that sources scout-lock.sh gets the same cleaning, before the awake clock."""
    marker = _plant(vault)

    out = subprocess.run(
        ["/bin/bash", "-c", 'source scout/scripts/scout-lock.sh && _scout_awake_now && printf "%s\\n" "$PATH"'],
        env={**os.environ, "PATH": _hostile_path()},
        cwd=vault,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    _assert_nothing_planted_ran(marker, out)
    assert out.returncode == 0, out
    clock, cleaned = out.stdout.splitlines()
    assert clock.split()[0] in {"awake", "wall"} and clock.split()[1].isdigit(), out
    assert all(p.startswith("/") for p in cleaned.split(":")), cleaned
