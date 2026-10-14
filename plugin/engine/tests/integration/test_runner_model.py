"""Integration test: each runner asks the engine for the run's model and passes it to claude.

Renders run-scout / run-dreaming / run-research with:

  - a stub ``scripts/claude-with-retry.sh`` that records its argv and the
    ``SCOUT_MODEL`` it inherited, and
  - a stub ``scoutctl`` that answers ``model resolve`` (and records its argv).

The runner must pass ``--model <resolved>`` as two arguments (the eval shim
strips exactly that pair) and export ``SCOUT_MODEL`` so the run-outcome ledger
can record it. When the engine cannot answer, the run still goes ahead on opus
and says so in its log.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
TEMPLATES = REPO_ROOT / "templates"

RUNNERS = [
    ("run-scout.sh.tmpl", "midday-consolidation"),
    ("run-dreaming.sh.tmpl", "dreaming-nightly"),
    ("run-research.sh.tmpl", "research"),
]


def _render(tmpl: Path, scout_dir: Path, scoutctl: str) -> Path:
    text = tmpl.read_text(encoding="utf-8")
    for placeholder, value in {
        "{{SCOUT_DIR}}": str(scout_dir),
        "{{CLAUDE_BIN}}": "/usr/bin/true",
        "{{SCOUTCTL_BIN}}": scoutctl,
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
    lock_lib = scout_dir / "scripts" / "scout-lock.sh"
    lock_lib.parent.mkdir(parents=True, exist_ok=True)
    lock_lib.write_text(
        (TEMPLATES / "scripts" / "scout-lock.sh.tmpl")
        .read_text(encoding="utf-8")
        .replace("{{INSTANCE_NAME}}", "Scout"),
        encoding="utf-8",
    )
    return out


def _stub_claude(scout_dir: Path) -> tuple[Path, Path]:
    argv_file = scout_dir / "claude.argv"
    env_file = scout_dir / "claude.model-env"
    stub = scout_dir / "scripts" / "claude-with-retry.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/bin/bash\n"
        f'printf \'%s\\n\' "$@" > "{argv_file}"\n'
        f'printf \'%s\\n\' "${{SCOUT_MODEL-<unset>}}" > "{env_file}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return argv_file, env_file


def _stub_scoutctl(tmp_path: Path, *, answer: str | None) -> tuple[Path, Path]:
    """A scoutctl that answers `model resolve` with ``answer`` (None → fails) and ignores the rest."""
    calls = tmp_path / "scoutctl.calls"
    script = tmp_path / "bin" / "scoutctl"
    script.parent.mkdir(parents=True, exist_ok=True)
    resolve = f'echo "{answer}"; exit 0' if answer is not None else 'echo "error: bad schedule" >&2; exit 1'
    script.write_text(
        f'#!/bin/bash\necho "$*" >> "{calls}"\nif [ "$1" = model ] && [ "$2" = resolve ]; then {resolve}; fi\nexit 0\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, calls


def _run(script: Path, slot_key: str, **extra_env: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCOUT_")}
    env["SCOUT_FORCE_MODE"] = slot_key
    env.update(extra_env)
    return subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=60)


def _log(scout_dir: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((scout_dir / ".scout-logs").glob("*.log")))


def _model_arg(argv_file: Path) -> list[str]:
    argv = argv_file.read_text(encoding="utf-8").splitlines()
    i = argv.index("--model")
    return argv[i : i + 2]


@pytest.mark.parametrize(("name", "slot_key"), RUNNERS)
def test_runner_passes_the_resolved_model(tmp_path: Path, name: str, slot_key: str) -> None:
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    scoutctl, calls = _stub_scoutctl(tmp_path, answer="sonnet")
    script = _render(TEMPLATES / name, scout_dir, str(scoutctl))
    argv_file, env_file = _stub_claude(scout_dir)

    result = _run(script, slot_key)

    assert result.returncode == 0, result.stderr + _log(scout_dir)
    assert _model_arg(argv_file) == ["--model", "sonnet"]
    assert env_file.read_text(encoding="utf-8").strip() == "sonnet", "SCOUT_MODEL must reach the session"
    assert f"model resolve --slot {slot_key}" in calls.read_text(encoding="utf-8")
    assert "=== model: sonnet ===" in _log(scout_dir)


@pytest.mark.parametrize(("name", "slot_key"), RUNNERS)
def test_runner_falls_back_to_opus_when_resolve_fails(tmp_path: Path, name: str, slot_key: str) -> None:
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    scoutctl, _calls = _stub_scoutctl(tmp_path, answer=None)
    script = _render(TEMPLATES / name, scout_dir, str(scoutctl))
    argv_file, _env = _stub_claude(scout_dir)

    result = _run(script, slot_key)

    assert result.returncode == 0, result.stderr + _log(scout_dir)
    assert _model_arg(argv_file) == ["--model", "opus"]
    log = _log(scout_dir)
    assert "model resolve failed" in log
    assert "error: bad schedule" in log  # the engine's reason lands in the run log


@pytest.mark.parametrize(("name", "slot_key"), RUNNERS)
def test_runner_without_an_engine_runs_on_opus(tmp_path: Path, name: str, slot_key: str) -> None:
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATES / name, scout_dir, "/nonexistent/scoutctl")
    argv_file, _env = _stub_claude(scout_dir)

    result = _run(script, slot_key)

    assert result.returncode == 0, result.stderr + _log(scout_dir)
    assert _model_arg(argv_file) == ["--model", "opus"]


@pytest.mark.parametrize(("name", "slot_key"), RUNNERS)
def test_runner_template_hard_codes_no_model(name: str, slot_key: str) -> None:
    assert "--model opus" not in (TEMPLATES / name).read_text(encoding="utf-8")
