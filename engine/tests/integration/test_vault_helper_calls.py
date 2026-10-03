"""The rendered templates call a vault's optional helper scripts, and survive without them.

Several fixes a vault carried by hand are calls from a plugin-owned script into
a helper script only that vault has:

  - each runner records the run's outcome (``scripts/run-outcome.sh record``)
    after the session, from the wrapper, so it still happens when the session
    dies before reaching its phases;
  - ``hooks/kb-pre-filter.sh`` caches the git-truth staleness ranking
    (``scripts/vault-freshness.py``) before the session starts;
  - ``scripts/heartbeat.sh`` runs the session-lane liveness watchdog
    (``scripts/session-lane-liveness.py``) once a day.

The plugin now ships these helpers (see test_shipped_helpers_install.py), but
every call stays guarded: a vault without the helper, or with one that fails,
must run exactly as before. Each runner also regenerates the connector-health
surface itself, calling ``scoutctl connector-health-report`` directly, for the
same reason as the outcome record. These tests render each template into a
temporary vault, stand in for the helpers and for ``claude`` / ``scoutctl``, and
run the real shell.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scout.scripts.phase_assembly import render_template

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
RUN_TIMEOUT_S = 60


def _render(template: str, vault: Path, dest: str, **extra: str) -> Path:
    variables = {
        "INSTANCE_NAME": "Scout",
        "INSTANCE_NAME_LOWER": "scout",
        "SCOUT_DIR": str(vault),
        "CLAUDE_BIN": "/usr/bin/true",  # never reached: the retry wrapper is stubbed
        "MAX_BUDGET": "25",
        "USER_NAME": "Alex",
        "USER_SLACK_ID": "U0123456789",
        "TIMEZONE": "America/New_York",
        **extra,
    }
    text = render_template((TEMPLATES / template).read_text(encoding="utf-8"), variables)
    assert "{{" not in text, f"unrendered placeholder left in {template}"
    out = vault / dest
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    return out


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _run(script: Path, vault: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=vault,
        env={**os.environ, "SCOUT_DATA_DIR": str(vault), **env},
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
    )


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    for d in (".scout-logs", ".scout-cache", "scripts", "hooks"):
        (v / d).mkdir(parents=True)
    return v


# ---------- runners: run outcome + connector-health roll-up ----------

RUNNERS = [
    pytest.param("run-scout.sh.tmpl", "morning-briefing", id="run-scout"),
    pytest.param("run-dreaming.sh.tmpl", "dreaming-nightly", id="run-dreaming"),
    pytest.param("run-research.sh.tmpl", "research", id="run-research"),
]

# Records each call with the vault it was pointed at, and says so in the log.
ROLLUP_SCOUTCTL = """#!/bin/bash
echo "$SCOUT_DATA_DIR $*" >> "{calls}"
echo "connector-health: 2 sessions in window, 0 alert(s)"
exit {exit_code}
"""


def _stub_claude(vault: Path, exit_code: int) -> None:
    _script(vault / "scripts" / "claude-with-retry.sh", f"#!/bin/bash\nexit {exit_code}\n")


def _rollup_scoutctl(vault: Path, exit_code: int = 0) -> tuple[Path, Path]:
    calls = vault / "scoutctl.calls"
    stub = _script(vault / "bin" / "scoutctl", ROLLUP_SCOUTCTL.format(calls=calls, exit_code=exit_code))
    return stub, calls


def _runner_log(vault: Path) -> str:
    logs = [p for p in (vault / ".scout-logs").glob("*.log") if p.name != "failures.log"]
    assert len(logs) == 1, f"expected one run log, found {[p.name for p in logs]}"
    return logs[0].read_text(encoding="utf-8")


@pytest.mark.parametrize("exit_code", [0, 3])
@pytest.mark.parametrize(("template", "slot"), RUNNERS)
def test_runner_records_the_outcome_and_rolls_up_connector_health(
    vault: Path, template: str, slot: str, exit_code: int
) -> None:
    scoutctl, calls = _rollup_scoutctl(vault)
    runner = _render(template, vault, template.removesuffix(".tmpl"), SCOUTCTL_BIN=str(scoutctl))
    _stub_claude(vault, exit_code)
    outcome_args = vault / "outcome.args"
    _script(vault / "scripts" / "run-outcome.sh", f'#!/bin/bash\nprintf \'%s\\n\' "$@" > "{outcome_args}"\n')

    result = _run(runner, vault, SCOUT_FORCE_MODE=slot)

    assert result.returncode == 0, result.stderr
    verb, mode, code, started, log_file = outcome_args.read_text(encoding="utf-8").splitlines()
    assert (verb, mode, code) == ("record", slot, str(exit_code))
    assert started.isdigit()
    assert Path(log_file).parent == vault / ".scout-logs"
    assert calls.read_text(encoding="utf-8").splitlines() == [f"{vault} connector-health-report"]
    assert "connector-health: 2 sessions in window" in _runner_log(vault)


@pytest.mark.parametrize(("template", "slot"), RUNNERS)
def test_runner_without_the_helpers_runs_as_before(vault: Path, template: str, slot: str) -> None:
    runner = _render(template, vault, template.removesuffix(".tmpl"), SCOUTCTL_BIN=str(vault / "bin" / "absent"))
    _stub_claude(vault, 0)

    result = _run(runner, vault, SCOUT_FORCE_MODE=slot)

    assert result.returncode == 0, result.stderr
    log = _runner_log(vault)
    assert "run finished" in log
    assert "connector-health roll-up skipped" in log


@pytest.mark.parametrize(("template", "slot"), RUNNERS)
def test_failing_helpers_do_not_fail_the_runner(vault: Path, template: str, slot: str) -> None:
    scoutctl = _script(
        vault / "bin" / "scoutctl", "#!/bin/bash\necho 'connector-health: fatal error: engine broken' >&2\nexit 1\n"
    )
    runner = _render(template, vault, template.removesuffix(".tmpl"), SCOUTCTL_BIN=str(scoutctl))
    _stub_claude(vault, 0)
    _script(vault / "scripts" / "run-outcome.sh", "#!/bin/bash\necho 'run-outcome: ledger not writable' >&2\nexit 1\n")

    result = _run(runner, vault, SCOUT_FORCE_MODE=slot)

    assert result.returncode == 0, result.stderr
    # A broken helper must leave a trace: otherwise the alerting it provides is
    # dead with nothing anywhere to say so.
    log = _runner_log(vault)
    assert "run-outcome: ledger not writable" in log
    assert "connector-health: fatal error: engine broken" in log


# ---------- kb-pre-filter.sh: git-truth staleness ranking ----------


def _stub_scoutctl(vault: Path) -> tuple[Path, Path]:
    calls = vault / "scoutctl.calls"
    stub = _script(vault / "bin" / "scoutctl", f'#!/bin/bash\necho "$*" >> "{calls}"\n')
    return stub, calls


FRESHNESS_STUB = """#!/usr/bin/env python3
import os, sys
with open("freshness.calls", "a") as fh:
    fh.write(os.getcwd() + " " + " ".join(sys.argv[1:]) + "\\n")
if {exit_code}:
    print("vault-freshness: boom", file=sys.stderr)
sys.exit({exit_code})
"""


def _kb_pre_filter(vault: Path) -> tuple[Path, Path]:
    scoutctl, calls = _stub_scoutctl(vault)
    hook = _render("hooks/kb-pre-filter.sh.tmpl", vault, "hooks/kb-pre-filter.sh", SCOUTCTL_BIN=str(scoutctl))
    return hook, calls


def test_kb_pre_filter_caches_the_git_truth_ranking_before_filtering(vault: Path) -> None:
    hook, scoutctl_calls = _kb_pre_filter(vault)
    _script(vault / "scripts" / "vault-freshness.py", FRESHNESS_STUB.format(exit_code=0))

    result = _run(hook, vault, "briefing")

    assert result.returncode == 0, result.stderr
    cwd, *args = (vault / "freshness.calls").read_text(encoding="utf-8").split()
    assert Path(cwd).resolve() == vault.resolve()
    assert args == ["--limit", "15", "--out", ".scout-cache/vault-freshness.md"]
    assert scoutctl_calls.read_text(encoding="utf-8").split() == ["hook", "kb-pre-filter", "--session-type", "briefing"]


@pytest.mark.parametrize("helper", ["absent", "failing"])
def test_kb_pre_filter_still_filters_without_a_working_ranking(vault: Path, helper: str) -> None:
    hook, scoutctl_calls = _kb_pre_filter(vault)
    if helper == "failing":
        _script(vault / "scripts" / "vault-freshness.py", FRESHNESS_STUB.format(exit_code=1))

    result = _run(hook, vault, "dreaming")

    assert result.returncode == 0, result.stderr
    assert scoutctl_calls.read_text(encoding="utf-8").split() == ["hook", "kb-pre-filter", "--session-type", "dreaming"]


def test_kb_pre_filter_reports_a_failing_ranking_and_drops_its_stale_cache(vault: Path) -> None:
    """The runner sends the hook's stderr to the run log, so the crash is visible;
    and a cache left by an earlier run must not be read as the current ranking."""
    hook, _ = _kb_pre_filter(vault)
    _script(vault / "scripts" / "vault-freshness.py", FRESHNESS_STUB.format(exit_code=1))
    stale = vault / ".scout-cache" / "vault-freshness.md"
    stale.write_text("# ranking from an earlier run\n", encoding="utf-8")

    result = _run(hook, vault, "briefing")

    assert result.returncode == 0, result.stderr
    assert "vault-freshness: boom" in result.stderr
    assert not stale.exists()


# ---------- heartbeat.sh: once-a-day session-lane liveness watchdog ----------

LIVENESS_STUB = """#!/usr/bin/env python3
import os, sys
with open(os.path.join(os.environ["SCOUT_DATA_DIR"], "liveness.calls"), "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\\n")
print("lane dreaming: DARK")
sys.exit(2)
"""


def _heartbeat(vault: Path) -> tuple[Path, Path]:
    scoutctl, calls = _stub_scoutctl(vault)
    beat = _render("scripts/heartbeat.sh.tmpl", vault, "scripts/heartbeat.sh", SCOUTCTL_BIN=str(scoutctl))
    return beat, calls


def test_heartbeat_runs_the_lane_watchdog_once_a_day(vault: Path) -> None:
    beat, scoutctl_calls = _heartbeat(vault)
    _script(vault / "scripts" / "session-lane-liveness.py", LIVENESS_STUB)

    first = _run(beat, vault)
    second = _run(beat, vault)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert (vault / "liveness.calls").read_text(encoding="utf-8").splitlines() == ["--quiet --notify"]
    assert "lane dreaming: DARK" in (vault / ".scout-logs" / "lane-liveness.log").read_text(encoding="utf-8")
    assert scoutctl_calls.read_text(encoding="utf-8").splitlines() == ["heartbeat run", "heartbeat run"]


def test_heartbeat_dry_run_skips_the_lane_watchdog(vault: Path) -> None:
    beat, scoutctl_calls = _heartbeat(vault)
    _script(vault / "scripts" / "session-lane-liveness.py", LIVENESS_STUB)

    result = _run(beat, vault, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert not (vault / "liveness.calls").exists()
    assert scoutctl_calls.read_text(encoding="utf-8").splitlines() == ["heartbeat run --dry-run"]


def test_heartbeat_without_the_watchdog_runs_as_before(vault: Path) -> None:
    beat, scoutctl_calls = _heartbeat(vault)

    result = _run(beat, vault)

    assert result.returncode == 0, result.stderr
    assert scoutctl_calls.read_text(encoding="utf-8").splitlines() == ["heartbeat run"]
    assert list((vault / ".scout-cache").glob("lane-liveness-*.done")) == []


def test_heartbeat_survives_an_unwritable_cache_dir(vault: Path) -> None:
    """The watchdog block must never stop the heartbeat reaching its real work."""
    beat, scoutctl_calls = _heartbeat(vault)
    _script(vault / "scripts" / "session-lane-liveness.py", LIVENESS_STUB)
    cache = vault / ".scout-cache"
    cache.chmod(0o555)
    try:
        result = _run(beat, vault)
    finally:
        cache.chmod(0o755)

    assert result.returncode == 0, result.stderr
    assert scoutctl_calls.read_text(encoding="utf-8").splitlines() == ["heartbeat run"]
