"""Integration test: each runner records the run's cost with `scoutctl budget record-run`.

Renders run-scout / run-dreaming / run-research with a stub
``scripts/claude-with-retry.sh`` (records its argv) and a stub ``scoutctl``
(records its argv; ``record-run`` succeeds or fails on request). The runner must
hand ``record-run`` the same ``--name`` it gave claude, the start time, the cap
and the exit code. When ``record-run`` fails (an engine without it), the old
``write-session-cost.sh`` runner row is written instead.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
TEMPLATES = REPO_ROOT / "templates"

RUNNERS = [
    ("run-scout.sh.tmpl", "midday-consolidation", "midday-consolidation"),
    ("run-dreaming.sh.tmpl", "dreaming-nightly", "dreaming"),
    ("run-research.sh.tmpl", "research", "research"),
]


def _render(tmpl: Path, scout_dir: Path, scoutctl: Path) -> Path:
    text = tmpl.read_text(encoding="utf-8")
    for placeholder, value in {
        "{{SCOUT_DIR}}": str(scout_dir),
        "{{CLAUDE_BIN}}": "/usr/bin/true",
        "{{SCOUTCTL_BIN}}": str(scoutctl),
        "{{INSTANCE_NAME_LOWER}}": "scout",
        "{{INSTANCE_NAME}}": "Scout",
        "{{MAX_BUDGET}}": "25",
        "{{USER_NAME}}": "Alex",
        "{{USER_SLACK_ID}}": "U0123456789",
    }.items():
        text = text.replace(placeholder, value)
    assert "{{" not in text, f"unrendered placeholder left in {tmpl.name}"
    out = scout_dir / tmpl.name.removesuffix(".tmpl")
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    scripts = scout_dir / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "scout-lock.sh").write_text(
        (TEMPLATES / "scripts" / "scout-lock.sh.tmpl")
        .read_text(encoding="utf-8")
        .replace("{{INSTANCE_NAME}}", "Scout"),
        encoding="utf-8",
    )
    return out


def _stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _setup(tmp_path: Path, name: str, *, record_run_ok: bool) -> tuple[Path, Path, Path, Path, Path]:
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    calls = tmp_path / "scoutctl.calls"
    scoutctl = tmp_path / "bin" / "scoutctl"
    record = "exit 0" if record_run_ok else 'echo "No such command" >&2; exit 2'
    _stub(
        scoutctl,
        f'echo "$*" >> "{calls}"\n'
        'if [ "$1" = model ] && [ "$2" = resolve ]; then echo opus; exit 0; fi\n'
        f'if [ "$1" = budget ] && [ "$2" = record-run ]; then {record}; fi\n'
        "exit 0\n",
    )
    script = _render(TEMPLATES / name, scout_dir, scoutctl)
    argv = scout_dir / "claude.argv"
    _stub(scout_dir / "scripts" / "claude-with-retry.sh", f'printf \'%s\\n\' "$@" > "{argv}"\nexit 1\n')
    fallback = scout_dir / "fallback.args"
    _stub(scout_dir / "scripts" / "write-session-cost.sh", f'echo "$*" >> "{fallback}"\n')
    return scout_dir, script, calls, argv, fallback


def _run(script: Path, slot_key: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCOUT_")}
    env["SCOUT_FORCE_MODE"] = slot_key
    return subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=60)


def _record_run_call(calls: Path) -> list[str]:
    (line,) = [c for c in calls.read_text(encoding="utf-8").splitlines() if c.startswith("budget record-run")]
    return line.split()


@pytest.mark.parametrize(("name", "slot_key", "run_type"), RUNNERS)
def test_runner_records_the_run_by_the_name_it_gave_claude(tmp_path: Path, name: str, slot_key: str, run_type: str):
    _scout_dir, script, calls, argv_file, fallback = _setup(tmp_path, name, record_run_ok=True)

    _run(script, slot_key)

    argv = argv_file.read_text(encoding="utf-8").splitlines()
    claude_name = argv[argv.index("--name") + 1]
    assert claude_name.startswith(f"scout-{slot_key}-")
    call = _record_run_call(calls)
    assert call[2] == run_type
    assert call[call.index("--name") + 1] == claude_name
    assert call[call.index("--started") + 1].isdigit()
    assert call[call.index("--cap") + 1] == "25"
    assert call[call.index("--exit-code") + 1] == "1"
    assert not fallback.exists(), "the $0 fallback row must not be written as well"


@pytest.mark.parametrize(("name", "slot_key", "run_type"), RUNNERS)
def test_runner_tells_the_retry_wrapper_how_to_record_a_failed_attempt(
    tmp_path: Path, name: str, slot_key: str, run_type: str
):
    """claude-with-retry.sh records each failed attempt's spend with the run's type and start (#561)."""
    scout_dir, script, calls, _argv, _fallback = _setup(tmp_path, name, record_run_ok=True)
    env_file = scout_dir / "wrapper.env"
    wrapper = scout_dir / "scripts" / "claude-with-retry.sh"
    _stub(wrapper, f'env | grep -E "^(SCOUT_RUN_|SCOUTCTL_BIN=)" > "{env_file}"\n')

    _run(script, slot_key)

    seen = dict(line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines())
    assert seen["SCOUTCTL_BIN"] == str(tmp_path / "bin" / "scoutctl")
    assert seen["SCOUT_RUN_TYPE"] == run_type
    call = _record_run_call(calls)
    assert seen["SCOUT_RUN_STARTED"] == call[call.index("--started") + 1]


@pytest.mark.parametrize(("name", "slot_key", "run_type"), RUNNERS)
def test_an_engine_without_record_run_falls_back_to_the_old_row(
    tmp_path: Path, name: str, slot_key: str, run_type: str
):
    _scout_dir, script, calls, _argv, fallback = _setup(tmp_path, name, record_run_ok=False)

    _run(script, slot_key)

    assert _record_run_call(calls)
    assert fallback.read_text(encoding="utf-8").split() == [run_type, "25", "0", "1", "runner"]
