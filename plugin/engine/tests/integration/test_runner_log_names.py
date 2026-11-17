"""Each run gets its own log, even when two runs start in the same minute.

Runner logs were named to the minute (``<stem>-YYYY-MM-DD_HH-MM.log``), so a
run that started in the same minute as the live one, typically skipped by the
session lock, appended its skip line to the live run's log, and the app then
read the live run as skipped. A runner now claims the minute-named log
atomically (noclobber, never truncating) and, when another run already holds it,
logs to ``<stem>-YYYY-MM-DD_HH-MM-SS-PID.log`` instead.

These tests render the real runner templates into a temporary vault, freeze the
runner's clock with a ``date`` shim so both runs land in one minute, and drive
the lock-skip path. No scheduler is touched.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
TEMPLATES = REPO_ROOT / "templates"
RUNNERS = {"run-scout.sh.tmpl": "scout", "run-dreaming.sh.tmpl": "dreaming", "run-research.sh.tmpl": "research"}
RUN_TIMEOUT_S = 30
SLOT = "morning-briefing"
STAMP = "2026-11-17_09-03-25"
MINUTE = "2026-11-17_09-03"


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _render(tmpl: Path, out: Path, scout_dir: Path) -> Path:
    text = tmpl.read_text(encoding="utf-8")
    for placeholder, value in {
        "{{SCOUT_DIR}}": str(scout_dir),
        "{{CLAUDE_BIN}}": "/usr/bin/true",  # never reached: the lock is held
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


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    _script(v / "bin" / "scoutctl", "#!/bin/bash\nexit 0\n")
    fake_bin = tmp_path / "fakebin"
    for name in ("osascript", "notify-send"):
        _script(fake_bin / name, "#!/bin/bash\ncat > /dev/null\n")
    # Freeze only the log-name stamp; every other `date` call is the real one.
    _script(
        fake_bin / "date",
        f'#!/bin/bash\nif [ "$*" = "+%Y-%m-%d_%H-%M-%S" ]; then echo "{STAMP}"; exit 0; fi\nexec /bin/date "$@"\n',
    )
    _render(TEMPLATES / "scripts" / "run-outcome.sh.tmpl", v / "scripts" / "run-outcome.sh", v)
    _render(TEMPLATES / "scripts" / "scout-lock.sh.tmpl", v / "scripts" / "scout-lock.sh", v)
    _script(v / "scripts" / "claude-with-retry.sh", f'#!/bin/bash\ntouch "{v}/session-ran"\n')
    return v


@pytest.fixture
def live_holder(vault: Path):
    """A live process holding the session lock, as the running session would."""
    holder = subprocess.Popen(["sleep", "60"])
    (vault / ".scout-logs" / ".scout-session.lock").write_text(f"{holder.pid}\n", encoding="utf-8")
    yield holder
    holder.kill()
    holder.wait()


def _run(vault: Path, runner: str) -> subprocess.CompletedProcess[str]:
    script = _render(TEMPLATES / runner, vault / runner.removesuffix(".tmpl"), vault)
    fake_bin = vault.parent / "fakebin"
    return subprocess.run(
        [str(script)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "SCOUT_FORCE_MODE": SLOT,
            "SCOUT_LOCK_WAIT_SECS": "0",
        },
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
    )


def _logs(vault: Path, stem: str) -> list[str]:
    return sorted(p.name for p in (vault / ".scout-logs").glob(f"{stem}-*.log"))


def _ledger_logs(vault: Path) -> list[str]:
    path = vault / ".scout-logs" / "run-outcomes.jsonl"
    return [json.loads(line)["log"] for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.usefixtures("live_holder")
@pytest.mark.parametrize("runner", RUNNERS)
def test_a_lock_skip_in_the_live_runs_minute_leaves_the_live_log_alone(vault: Path, runner: str) -> None:
    stem = RUNNERS[runner]
    live_log = vault / ".scout-logs" / f"{stem}-{MINUTE}.log"
    live_log.write_text("=== live run output ===\n", encoding="utf-8")

    result = _run(vault, runner)

    assert result.returncode == 0, result.stderr
    assert not (vault / "session-ran").exists()
    # The live run's log is neither truncated nor appended to.
    assert live_log.read_text(encoding="utf-8") == "=== live run output ===\n"
    (skip_log,) = [name for name in _logs(vault, stem) if name != live_log.name]
    assert re.fullmatch(rf"{stem}-{STAMP}-\d+\.log", skip_log)
    assert "session running" in (vault / ".scout-logs" / skip_log).read_text(encoding="utf-8")
    # The ledger points the skip at its own log, so the app matches it to the right run.
    assert _ledger_logs(vault) == [skip_log]


@pytest.mark.usefixtures("live_holder")
@pytest.mark.parametrize("runner", RUNNERS)
def test_two_runs_in_one_minute_get_two_logs(vault: Path, runner: str) -> None:
    stem = RUNNERS[runner]

    first = _run(vault, runner)
    second = _run(vault, runner)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    logs = _logs(vault, stem)
    assert len(logs) == 2
    claimed, fallback = f"{stem}-{MINUTE}.log", next(name for name in logs if name != f"{stem}-{MINUTE}.log")
    assert claimed in logs
    assert re.fullmatch(rf"{stem}-{STAMP}-\d+\.log", fallback)
    for name in logs:
        assert (vault / ".scout-logs" / name).read_text(encoding="utf-8").count("session running") == 1, name
    assert _ledger_logs(vault) == [claimed, fallback]
