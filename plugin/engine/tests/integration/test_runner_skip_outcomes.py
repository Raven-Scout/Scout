"""A deliberate runner skip records a run outcome (#401).

A runner that finds the session lock held by a live session, or whose budget
check says to skip, logs one line and exits 0. Before #401 it exited before
``scripts/run-outcome.sh record``, so the slot had fired with no ledger row and
``scoutctl health`` reported ``slot.fired_silent:<slot>`` for a run that was
skipped on purpose. These tests render the real runner and ``run-outcome.sh``
templates into a temporary vault and check that each skip leaves a
``skipped_lock`` / ``skipped_budget`` row that health reads as covering the fire.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
TEMPLATES = REPO_ROOT / "templates"
RUNNERS = ("run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl")
RUN_TIMEOUT_S = 30
SLOT = "morning-briefing"

SCHEDULE = f"""\
schema_version: 1
slots:
  {SLOT}:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 0
"""


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


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    # health notify is a no-op stand-in; the fake notifiers keep the
    # engine-unreachable floor from ever reaching a real desktop.
    _script(v / "bin" / "scoutctl", "#!/bin/bash\nexit 0\n")
    for name in ("osascript", "notify-send"):
        _script(tmp_path / "fakebin" / name, "#!/bin/bash\ncat > /dev/null\n")
    _render(TEMPLATES / "scripts" / "run-outcome.sh.tmpl", v / "scripts" / "run-outcome.sh", v)
    _render(TEMPLATES / "scripts" / "scout-lock.sh.tmpl", v / "scripts" / "scout-lock.sh", v)
    # The session body must never run on a skip path.
    _script(v / "scripts" / "claude-with-retry.sh", f'#!/bin/bash\ntouch "{v}/session-ran"\n')
    return v


def _run(vault: Path, runner: str, **env: str) -> subprocess.CompletedProcess[str]:
    script = _render(TEMPLATES / runner, vault / runner.removesuffix(".tmpl"), vault)
    fake_bin = vault.parent / "fakebin"
    return subprocess.run(
        [str(script)],
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", **env},
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
    )


def _ledger(vault: Path) -> list[dict]:
    path = vault / ".scout-logs" / "run-outcomes.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def live_holder(vault: Path):
    """A live process holding the session lock, as another session would."""
    holder = subprocess.Popen(["sleep", "60"])
    (vault / ".scout-logs" / ".scout-session.lock").write_text(f"{holder.pid}\n", encoding="utf-8")
    yield holder
    holder.kill()
    holder.wait()


def _over_budget(vault: Path) -> None:
    _script(vault / "scripts" / "budget-check.sh", "#!/bin/bash\necho 'over budget'\nexit 1\n")


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_lock_skip_records_a_skipped_lock_row(vault: Path, runner: str, live_holder: subprocess.Popen) -> None:
    before = int(time.time())

    # No wait: a wait that runs out is the same skip (test_scout_lock.py).
    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT, SCOUT_LOCK_WAIT_SECS="0")

    assert result.returncode == 0, result.stderr
    assert not (vault / "session-ran").exists()
    (row,) = _ledger(vault)
    assert row["outcome"] == "skipped_lock"
    assert row["slot"] == SLOT
    assert row["exit_code"] == 0
    assert before <= row["started_at"] <= row["finished_at"]
    # The holder's lock is untouched.
    assert (vault / ".scout-logs" / ".scout-session.lock").read_text(encoding="utf-8").strip() == str(live_holder.pid)


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_budget_skip_records_a_skipped_budget_row(vault: Path, runner: str) -> None:
    _over_budget(vault)

    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT)

    assert result.returncode == 0, result.stderr
    assert not (vault / "session-ran").exists()
    (row,) = _ledger(vault)
    assert row["outcome"] == "skipped_budget"
    assert row["slot"] == SLOT
    assert row["exit_code"] == 0
    # The budget skip releases the lock it took.
    assert not (vault / ".scout-logs" / ".scout-session.lock").exists()


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_manual_skip_is_recorded_under_the_runner_default_mode(vault: Path, runner: str) -> None:
    _over_budget(vault)
    env = {k: v for k, v in os.environ.items() if k != "SCOUT_FORCE_MODE"}

    script = _render(TEMPLATES / runner, vault / runner.removesuffix(".tmpl"), vault)
    subprocess.run(
        [str(script)],
        env={**env, "PATH": f"{vault.parent / 'fakebin'}{os.pathsep}{env['PATH']}"},
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
        check=True,
    )

    (row,) = _ledger(vault)
    expected = {
        "run-scout.sh.tmpl": "manual",
        "run-dreaming.sh.tmpl": "dreaming-manual",
        "run-research.sh.tmpl": "research-manual",
    }[runner]
    assert (row["slot"], row["mode"], row["outcome"]) == (expected, expected, "skipped_budget")


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_run_that_was_not_skipped_records_no_skip(vault: Path, runner: str) -> None:
    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT)

    assert result.returncode == 0, result.stderr
    assert (vault / "session-ran").exists()
    (row,) = _ledger(vault)
    assert row["outcome"] == "ok"
    assert row["runner"] == {"run-scout.sh.tmpl": "scout", "run-dreaming.sh.tmpl": "dreaming"}.get(runner, "research")


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_failed_gate_probe_records_skipped_gates_and_never_launches(vault: Path, runner: str) -> None:
    """Bare-mode guard (spec 2026-11-13 §3.5): claude without Scout's hooks never runs against the vault."""
    _script(
        vault / "bin" / "scoutctl",
        '#!/bin/bash\nif [ "$1" = gates ]; then echo "[scout:gates] not loaded" >&2; exit 3; fi\nexit 0\n',
    )

    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT)

    assert result.returncode == 0, result.stderr
    assert not (vault / "session-ran").exists()
    (row,) = _ledger(vault)
    assert (row["slot"], row["outcome"], row["exit_code"]) == (SLOT, "skipped_gates", 0)
    assert not (vault / ".scout-logs" / ".scout-session.lock").exists()


@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.parametrize(("code", "klass"), [(4, "gates_unsafe_endpoint"), (5, "gates_unsafe_launcher")])
def test_a_probe_that_cant_run_safely_records_its_cause(vault: Path, runner: str, code: int, klass: str) -> None:
    """Exit 4 (a setting it can't keep local) and 5 (a launcher it can't see through) are gates skips with a cause."""
    gate = f'if [ "$1" = gates ]; then echo "[scout:gates] can\'t probe safely" >&2; exit {code}; fi'
    _script(vault / "bin" / "scoutctl", f"#!/bin/bash\n{gate}\nexit 0\n")

    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT)

    assert result.returncode == 0, result.stderr
    assert not (vault / "session-ran").exists()
    (row,) = _ledger(vault)
    assert (row["outcome"], row["failure_class"], row["exit_code"]) == ("skipped_gates", klass, 0)


@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.parametrize("broken", ["missing", "not-executable"])
def test_a_missing_scoutctl_skips_the_run_instead_of_running_ungated(vault: Path, runner: str, broken: str) -> None:
    """The hooks run the plugin's scoutctl, not this one, so a broken one here proves nothing (review of #635)."""
    stub = vault / "bin" / "scoutctl"
    if broken == "missing":
        stub.unlink()
    else:
        stub.chmod(0o644)

    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT)

    assert result.returncode == 0, result.stderr
    assert not (vault / "session-ran").exists()
    (row,) = _ledger(vault)
    assert (row["slot"], row["outcome"]) == (SLOT, "skipped_gates")


@pytest.mark.parametrize("runner", RUNNERS)
def test_the_gate_probe_gets_the_runners_claude_and_vault(vault: Path, runner: str) -> None:
    _script(vault / "bin" / "scoutctl", f'#!/bin/bash\necho "$@" >> "{vault}/scoutctl-calls"\nexit 0\n')

    _run(vault, runner, SCOUT_FORCE_MODE=SLOT)

    calls = (vault / "scoutctl-calls").read_text(encoding="utf-8").splitlines()
    (probe,) = [c for c in calls if c.startswith("gates probe")]
    assert probe == f"gates probe --cached --vault {vault} --claude-bin /usr/bin/true"
    assert (vault / "session-ran").exists()


@pytest.mark.parametrize("runner", RUNNERS)
def test_the_gate_probe_runs_after_the_lock_and_budget_and_before_claude(runner: str) -> None:
    text = (TEMPLATES / runner).read_text(encoding="utf-8")
    probe = text.index("gates probe --cached")
    assert text.index("scout_lock_acquire") < probe
    assert text.index("scout_record_skip skipped_budget") < probe
    assert probe < text.index('"$SCOUT_DIR/scripts/claude-with-retry.sh"')


# ----- the health reading of a skip --------------------------------------------


def _health_vault(vault: Path, monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    state = vault / ".scout-state"
    state.mkdir(exist_ok=True)
    (state / "schedule.yaml").write_text(SCHEDULE, encoding="utf-8")
    old = (now - timedelta(days=60)).timestamp()
    os.utime(state / "schedule.yaml", (old, old))
    monkeypatch.setattr(health, "vault_birth", lambda _v: now - timedelta(days=60))
    monkeypatch.setattr(health, "tick_job_installed", lambda home=None: True, raising=False)
    tick = {
        "id": "x",
        "ts": health.iso_z(now - timedelta(minutes=5)),
        "kind": "schedule.tick.completed",
        "source": "t",
        "payload": {},
    }
    (vault / ".scout-logs" / f"schedule-events-{now.date().isoformat()}.jsonl").write_text(json.dumps(tick) + "\n")
    # An earlier, ordinary run of the slot: the ledger predates the fire.
    earlier = int((now - timedelta(days=2)).timestamp())
    row = {
        "slot": SLOT,
        "mode": SLOT,
        "started_at": earlier,
        "finished_at": earlier + 60,
        "exit_code": 0,
        "failure_class": "ok",
        "log": None,
    }
    path = vault / ".scout-logs" / "run-outcomes.jsonl"
    path.write_text(json.dumps(row) + "\n" + (path.read_text(encoding="utf-8") if path.exists() else ""))


def _fire(vault: Path, at: datetime) -> None:
    (vault / ".scout-state" / "last-fire.json").write_text(
        json.dumps({"schema_version": 1, "last_fire": {SLOT: health.iso_z(at)}}), encoding="utf-8"
    )


def test_health_does_not_report_a_budget_skipped_fire_as_silent(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fired = datetime.now(UTC).replace(microsecond=0)
    _over_budget(vault)
    assert _run(vault, "run-scout.sh.tmpl", SCOUT_FORCE_MODE=SLOT).returncode == 0
    now = fired + timedelta(hours=5)
    _health_vault(vault, monkeypatch, now)
    _fire(vault, fired)

    ids = {f.id for f in health.evaluate(vault, now=now).findings}

    assert f"slot.fired_silent:{SLOT}" not in ids
    (status,) = health.evaluate(vault, now=now).slots
    assert status.last_outcome is not None and status.last_outcome.outcome == "skipped_budget"


def test_health_still_reports_a_fire_that_recorded_nothing(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The negative control: no skip row, no run row — still fired_silent."""
    fired = datetime.now(UTC).replace(microsecond=0)
    now = fired + timedelta(hours=5)
    _health_vault(vault, monkeypatch, now)
    _fire(vault, fired)

    ids = {f.id for f in health.evaluate(vault, now=now).findings}

    assert f"slot.fired_silent:{SLOT}" in ids


# -- the write sandbox (spec 2026-11-15, Phase 1c) ------------------------------------------------------------

_SANDBOX_STUB = """#!/bin/bash
if [ "$1" = sandbox ]; then
    {action}
fi
exit 0
"""


def _sandbox_scoutctl(vault: Path, action: str) -> None:
    _script(vault / "bin" / "scoutctl", _SANDBOX_STUB.format(action=action))


def _capture_wrapper_env(vault: Path) -> Path:
    seen = vault / "wrapper-env.txt"
    _script(
        vault / "scripts" / "claude-with-retry.sh",
        f'#!/bin/bash\nprintf "%s|%s\\n" "${{SCOUT_SANDBOX_STATUS-}}" "${{SCOUT_SANDBOX_PROFILE:0:11}}" > "{seen}"\n'
        f'touch "{vault}/session-ran"\n',
    )
    return seen


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_sandbox_that_cant_be_set_up_skips_the_run(vault: Path, runner: str) -> None:
    _sandbox_scoutctl(vault, 'echo "[scout:sandbox] failed: self-test: nope" >&2; exit 3')

    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT, SCOUT_RUN_SANDBOX="")

    assert result.returncode == 0, result.stderr
    assert not (vault / "session-ran").exists()
    (row,) = _ledger(vault)
    assert (row["outcome"], row["sandbox"], row["exit_code"]) == ("skipped_sandbox", "failed", 0)


@pytest.mark.parametrize("runner", RUNNERS)
def test_an_engine_that_prints_no_profile_never_runs_the_session_unsandboxed(vault: Path, runner: str) -> None:
    _sandbox_scoutctl(vault, "exit 0")  # "enforced" with nothing to enforce

    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT, SCOUT_RUN_SANDBOX="")

    assert result.returncode == 0, result.stderr
    assert not (vault / "session-ran").exists()
    (row,) = _ledger(vault)
    assert row["outcome"] == "skipped_sandbox"


@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.parametrize(("code", "status"), [(6, "unavailable"), (7, "off"), (8, "conflict")])
def test_no_sandbox_here_runs_as_before_and_says_so(vault: Path, runner: str, code: int, status: str) -> None:
    _sandbox_scoutctl(vault, f"exit {code}")
    seen = _capture_wrapper_env(vault)

    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT, SCOUT_RUN_SANDBOX="")

    assert result.returncode == 0, result.stderr
    assert seen.read_text().strip() == f"{status}|"
    (row,) = _ledger(vault)
    assert (row["outcome"], row["sandbox"]) == ("ok", status)


@pytest.mark.parametrize("runner", RUNNERS)
def test_an_enforced_sandbox_hands_the_profile_to_the_wrapper(vault: Path, runner: str) -> None:
    _sandbox_scoutctl(vault, "printf '(version 1)\\n(allow default)\\n'; exit 0")
    seen = _capture_wrapper_env(vault)

    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT, SCOUT_RUN_SANDBOX="")

    assert result.returncode == 0, result.stderr
    assert seen.read_text().strip() == "enforced|(version 1)"
    (row,) = _ledger(vault)
    assert (row["outcome"], row["sandbox"]) == ("ok", "enforced")


@pytest.mark.parametrize("runner", RUNNERS)
def test_the_env_switch_turns_it_off_without_asking_the_engine(vault: Path, runner: str) -> None:
    calls = vault / "scoutctl.calls"
    _script(vault / "bin" / "scoutctl", f'#!/bin/bash\necho "$*" >> "{calls}"\nexit 0\n')
    seen = _capture_wrapper_env(vault)

    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT, SCOUT_RUN_SANDBOX="OFF")

    assert result.returncode == 0, result.stderr
    assert seen.read_text().strip() == "off|"
    assert "sandbox profile" not in calls.read_text()
    (row,) = _ledger(vault)
    assert (row["sandbox"], row["sandbox_source"]) == ("off", "env")


@pytest.mark.parametrize("runner", RUNNERS)
def test_the_owners_switch_is_recorded_as_the_source(vault: Path, runner: str) -> None:
    _sandbox_scoutctl(vault, "exit 7")
    _capture_wrapper_env(vault)

    _run(vault, runner, SCOUT_FORCE_MODE=SLOT, SCOUT_RUN_SANDBOX="")

    (row,) = _ledger(vault)
    assert (row["sandbox"], row["sandbox_source"]) == ("off", "switch")


# -- the post-run integrity check (spec 2026-11-15 §13) -----------------------------------------------------------

_INTEGRITY_STUB = """#!/bin/bash
case "$1 $2" in
    "sandbox profile") printf '(version 1)\\n(allow default)\\n' ;;
    "sandbox snapshot") echo '{{"version":1,"start":true}}' ;;
    "sandbox verify") cat > "{stdin}"; echo "integrity check: stub"; exit {verify_rc} ;;
esac
exit 0
"""


@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.parametrize(
    ("verify_rc", "status"), [(0, "clean"), (10, "restored"), (11, "unrestored"), (12, "error"), (1, "error")]
)
def test_the_runner_checks_integrity_after_claude_and_records_it(
    vault: Path, runner: str, verify_rc: int, status: str
) -> None:
    stdin = vault / "verify-stdin.txt"
    _script(vault / "bin" / "scoutctl", _INTEGRITY_STUB.format(stdin=stdin, verify_rc=verify_rc))
    _capture_wrapper_env(vault)

    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT, SCOUT_RUN_SANDBOX="")

    assert result.returncode == 0, result.stderr  # never changes the run's exit code
    assert stdin.read_text() == '{"version":1,"start":true}'  # the start state, by value
    (row,) = _ledger(vault)
    assert (row["outcome"], row["integrity"]) == ("ok", status)


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_scoutctl_that_takes_no_start_snapshot_records_an_error(vault: Path, runner: str) -> None:
    _sandbox_scoutctl(vault, "exit 6")  # prints nothing for any sandbox subcommand
    _capture_wrapper_env(vault)

    _run(vault, runner, SCOUT_FORCE_MODE=SLOT, SCOUT_RUN_SANDBOX="")

    (row,) = _ledger(vault)
    assert row["integrity"] == "error"  # health turns yellow, so a snapshot that always fails gets noticed


@pytest.mark.parametrize("runner", RUNNERS)
def test_the_profile_is_built_for_the_claude_the_runner_starts(vault: Path, runner: str) -> None:
    seen = vault / "profile-claude-bin.txt"
    _sandbox_scoutctl(vault, f'[ "$2" = profile ] && printf "%s" "${{SCOUT_CLAUDE_BIN-}}" > "{seen}"; exit 6')
    _capture_wrapper_env(vault)

    _run(vault, runner, SCOUT_FORCE_MODE=SLOT, SCOUT_RUN_SANDBOX="")

    assert seen.read_text() == "/usr/bin/true"  # the runner's CLAUDE_BIN, not whatever `which claude` finds


@pytest.mark.parametrize("runner", RUNNERS)
def test_the_integrity_check_brackets_claude_and_comes_before_every_post_session_step(runner: str) -> None:
    text = (TEMPLATES / runner).read_text(encoding="utf-8")
    snapshot = text.index("sandbox snapshot --vault")
    claude = text.index('"$SCOUT_DIR/scripts/claude-with-retry.sh"')
    verify = text.index("sandbox verify --vault")
    outcome = text.index('"$OUTCOME" record')
    assert snapshot < claude < verify < outcome
    for later in ("POST_BACKFILL=", "connector-health-report", "budget record-run", "delegate sync"):
        if later in text:
            assert verify < text.index(later), later
