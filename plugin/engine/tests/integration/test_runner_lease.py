"""The runners take the Repository Writer Lease for a vault linked to Agnes (R2).

Renders the real runner, ``scout-lock.sh`` and ``run-outcome.sh`` templates
into a temporary vault, with ``scoutctl`` stubbed: it records each call and
answers ``repo lease acquire`` with the exit code a test asks for. The engine
side of each call is ``tests/unit/test_repo_lease.py``.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health

from .test_runner_skip_outcomes import RUNNERS, SLOT, _fire, _health_vault, _ledger, _run, _script
from .test_runner_skip_outcomes import vault as vault  # noqa: F401  (fixture)

STUB = """#!/bin/bash
echo "$*" >> "{calls}"
case "$1 $2 $3" in
    "repo lease acquire") exit "${{LEASE_RC:-0}}" ;;
    "repo lease keepalive")
        trap 'echo "keepalive stopped" >> "{calls}"; exit 0' TERM
        while :; do sleep 0.2; done ;;
esac
exit 0
"""


def _stub(vault: Path) -> Path:
    calls = vault.parent / "scoutctl-calls"
    _script(vault / "bin" / "scoutctl", STUB.format(calls=calls))
    return calls


def _link(vault: Path) -> None:
    state = vault / ".scout-state"
    state.mkdir(exist_ok=True)
    (state / "repo-sync.json").write_text("{}\n", encoding="utf-8")


def _calls(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


@pytest.mark.parametrize("runner", RUNNERS)
def test_an_unlinked_vault_makes_no_lease_call(vault: Path, runner: str) -> None:  # noqa: F811
    calls = _stub(vault)
    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT)
    assert result.returncode == 0, result.stderr
    assert (vault / "session-ran").exists()
    assert not [c for c in _calls(calls) if c.startswith("repo ")]
    assert _ledger(vault)[0]["outcome"] == "ok"


@pytest.mark.parametrize("runner", RUNNERS)
def test_a_linked_run_holds_the_lease_for_the_session_and_gives_it_back(vault: Path, runner: str) -> None:  # noqa: F811
    calls = _stub(vault)
    _link(vault)
    _script(
        vault / "scripts" / "claude-with-retry.sh",
        f'#!/bin/bash\ngrep -c "repo lease keepalive" "{calls}" > "{vault}/session-ran"\n',
    )

    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT)

    assert result.returncode == 0, result.stderr
    assert (vault / "session-ran").read_text().strip() == "1"
    log = _calls(calls)
    lease = [c for c in log if c.startswith("repo lease") or c == "keepalive stopped"]
    acquire, keepalive, release, stopped = lease
    run_id = acquire.split("--run-id ")[1].split()[0]
    assert acquire.startswith("repo lease acquire") and f"--what {SLOT}" in acquire
    assert keepalive.startswith(f"repo lease keepalive --run-id {run_id} --parent-pid ")
    assert release == f"repo lease release --run-id {run_id}"
    assert stopped == "keepalive stopped"
    assert run_id.startswith(f"{runner.removeprefix('run-').removesuffix('.sh.tmpl')}-{SLOT}-")
    assert _ledger(vault)[0]["outcome"] == "ok"
    assert not (vault / ".scout-logs" / ".scout-session.lock").exists()


@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.parametrize(("rc", "why"), [("3", "lease_held"), ("4", "lease_unavailable"), ("1", "lease_unavailable")])
def test_a_linked_run_without_the_lease_is_skipped_and_recorded(vault: Path, runner: str, rc: str, why: str) -> None:  # noqa: F811
    calls = _stub(vault)
    _link(vault)
    before = int(time.time())

    result = _run(vault, runner, SCOUT_FORCE_MODE=SLOT, LEASE_RC=rc)

    assert result.returncode == 0, result.stderr
    assert not (vault / "session-ran").exists()
    (row,) = _ledger(vault)
    assert (row["outcome"], row["failure_class"], row["exit_code"], row["slot"]) == ("skipped_lease", why, 0, SLOT)
    assert before <= row["started_at"] <= row["finished_at"]
    assert not [c for c in _calls(calls) if "keepalive" in c or "release" in c]
    assert not (vault / ".scout-logs" / ".scout-session.lock").exists()


def test_a_linked_run_with_no_engine_is_skipped_as_lease_unavailable(vault: Path) -> None:  # noqa: F811
    _link(vault)
    (vault / "bin" / "scoutctl").unlink()
    result = _run(vault, "run-scout.sh.tmpl", SCOUT_FORCE_MODE=SLOT)
    assert result.returncode == 0, result.stderr
    assert not (vault / "session-ran").exists()
    (row,) = _ledger(vault)
    assert (row["outcome"], row["failure_class"]) == ("skipped_lease", "lease_unavailable")


def test_the_lease_is_given_back_when_the_run_fails(vault: Path) -> None:  # noqa: F811
    calls = _stub(vault)
    _link(vault)
    _script(vault / "scripts" / "claude-with-retry.sh", "#!/bin/bash\nexit 7\n")
    result = _run(vault, "run-scout.sh.tmpl", SCOUT_FORCE_MODE=SLOT)
    assert result.returncode == 0, result.stderr
    assert any(c.startswith("repo lease release") for c in _calls(calls))
    assert "keepalive stopped" in _calls(calls)
    assert _ledger(vault)[0]["outcome"] == "failed"


# ----- health -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rc", "finding", "severity"), [("3", "lease.held", "note"), ("4", "lease.unavailable", "yellow")]
)
def test_health_shows_a_lease_skip_next_to_the_other_outcomes(
    vault: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    rc: str,
    finding: str,
    severity: str,
) -> None:
    _stub(vault)
    _link(vault)
    fired = datetime.now(UTC).replace(microsecond=0)
    assert _run(vault, "run-scout.sh.tmpl", SCOUT_FORCE_MODE=SLOT, LEASE_RC=rc).returncode == 0
    now = fired + timedelta(hours=1)
    _health_vault(vault, monkeypatch, now)
    _fire(vault, fired)

    report = health.evaluate(vault, now=now)

    found = {f.id: f for f in report.findings}
    assert f"slot.fired_silent:{SLOT}" not in found
    assert found[finding].severity == severity
    (status,) = report.slots
    assert status.last_outcome is not None
    assert (status.last_outcome.outcome, status.last_outcome.failure_class) == (
        "skipped_lease",
        "lease_held" if rc == "3" else "lease_unavailable",
    )
    doc = json.loads(json.dumps(report.to_dict(), default=str))
    slot = next(s for s in doc["slots"] if s["key"] == SLOT)
    assert slot["last_outcome"]["outcome"] == "skipped_lease"
    assert os.path.basename(str(found[finding].log or "")) in ("", status.last_outcome.log)
