"""Run protection: one read-only summary of what keeps a scheduled run from going wrong (app card C9).

``scoutctl protection status --json`` prints :func:`report`. The macOS app's Settings ▸ Engine "Run protection"
card reads only this, so it is a contract: ``tests/fixtures/contract/protection-status.json`` is generated from
``tests/unit/test_run_protection_contract.py`` and byte-copied to ``apps/macos/ScoutTests/Fixtures/``.

It gathers what other commands already decide, and decides nothing new:

- **sandbox**: whether the next scheduled run gets the OS write sandbox (:func:`scout.sandbox.launch.prepare`, judged
  as the scheduler sees it, so ``SCOUT_RUN_SANDBOX`` in the caller's shell is left out, as the doctor does);
- **gate_probe**: the last privacy-check probe the runners ran (the ``.scout-state/gate-probe.json`` cache; reading
  it never starts claude);
- **last_run**: the newest run that wasn't skipped, from the run ledger (:func:`scout.health.read_outcomes`), with
  its sandbox and post-run integrity result;
- **broker**: the commit and Telegram broker status the newest run recorded (``.scout-logs/.run-broker.json``);
- **slots**: the agent runtime, model and run limits each scheduled slot would run with now;
- **problems**: what needs the owner, each with the fix and an ``action`` the app turns into a button.

Paths in the output are relative to the vault, so the fixture doesn't depend on where a test's vault lives.
It never writes anything.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scout import health
from scout.errors import ConfigError

SCHEMA_VERSION = 1
# The broker status file Phase 1d's runners write (scout/sandbox/broker.py STATUS_FILE): {"at", "status", "reason"}.
BROKER_STATUS_FILE = ".run-broker.json"
BROKER_STATUSES = ("on", "unavailable", "off")

# What the app offers for a problem, as a button. A problem with none shows its fix text only.
ACTION_TURN_ON_SANDBOX = "turn_on_sandbox"  # scoutctl sandbox on
ACTION_CHECK_SANDBOX = "check_sandbox"  # re-read this report (the sandbox status self-tests every time)
ACTION_CHECK_GATES = "check_gates"  # scoutctl gates probe --cached --json (free: a local fake endpoint)
ACTION_SHOW_LOG = "show_log"  # reveal the problem's log
ACTIONS = (ACTION_TURN_ON_SANDBOX, ACTION_CHECK_SANDBOX, ACTION_CHECK_GATES, ACTION_SHOW_LOG)

# Ledger findings this card shows (the Health panel shows every finding; this is the protection subset).
_LEDGER_ACTIONS: dict[str, str | None] = {
    "sandbox.failed": ACTION_CHECK_SANDBOX,
    "runs.unsandboxed": ACTION_CHECK_SANDBOX,
    "runs.sandbox_env_off": None,
    "gates.not_loaded": ACTION_CHECK_GATES,
    "gates.probe_unsafe": ACTION_CHECK_GATES,
    "run.integrity_restored": ACTION_SHOW_LOG,
    "run.integrity_unchecked": ACTION_SHOW_LOG,
    "agent_runtime.refused": ACTION_SHOW_LOG,
}


def _rel(vault: Path, path: str | None) -> str | None:
    """``path`` relative to the vault when it's inside it, else its basename (never an absolute path)."""
    if not path:
        return None
    p = Path(path)
    try:
        return p.resolve().relative_to(vault.resolve()).as_posix()
    except ValueError:
        return p.name


def _problem(
    id: str, severity: str, summary: str, fix: str, action: str | None, log: str | None = None
) -> dict[str, Any]:
    return {"id": id, "severity": severity, "summary": summary, "fix": fix, "action": action, "log": log}


# ----- the parts ------------------------------------------------------------------------------------------------


def sandbox_part(
    vault: Path, env: Mapping[str, str], *, prepare: Callable[..., Any] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(the sandbox block, its problems now). ``prepare`` stands in for :func:`launch.prepare` in tests."""
    from scout.sandbox import launch

    # Scheduled runs don't inherit this shell's SCOUT_RUN_SANDBOX (bootstrap_doctor._check_run_sandbox).
    sched_env = {k: v for k, v in env.items() if k != launch.ENV_SWITCH}
    on, source = launch.switch(sched_env)
    block: dict[str, Any] = {
        "status": "unknown",
        "reason": "",
        "switch": "on" if on else "off",
        "switch_source": "default" if source == "default" else "owner",
    }
    if env.get("SCOUT_SANDBOX_STATUS") == "enforced":
        block.update(status="enforced", reason="this session runs inside it")
        return block, []
    try:
        result = (prepare or launch.prepare)(vault, sched_env)
    except Exception as e:  # noqa: BLE001 - a bug here must not hide the rest of the card
        block["reason"] = f"couldn't check: {e}"
        return block, [
            _problem(
                "sandbox.unchecked",
                "yellow",
                "Scout couldn't check whether scheduled runs get write protection.",
                f"Check again. If it keeps failing, `scoutctl sandbox status` says why ({e}).",
                ACTION_CHECK_SANDBOX,
            )
        ]
    # "turned off (<the switch file's absolute path>)" says where; the card only needs who.
    reason = "turned off by the owner (scoutctl sandbox off)" if result.status == "off" else result.reason
    block.update(status=result.status, reason=reason)
    problems: list[dict[str, Any]] = []
    if result.status == "off":
        problems.append(
            _problem(
                "sandbox.off",
                "yellow",
                "You turned write protection off, so scheduled runs could change the files that decide which of "
                "Scout's checks run. Scout's privacy checks still run.",
                "Turn it back on unless you are fixing something that needs it off.",
                ACTION_TURN_ON_SANDBOX,
            )
        )
    elif result.status == "failed":
        problems.append(
            _problem(
                "sandbox.not_ready",
                "red",
                "Write protection is expected on this Mac but couldn't be set up, so scheduled runs are skipped "
                f"until it can ({result.reason}).",
                "Check again. Runs resume on their own once it works. To run without it meanwhile, turn it off.",
                ACTION_CHECK_SANDBOX,
            )
        )
    elif result.status == "unavailable":
        problems.append(
            _problem(
                "sandbox.unavailable",
                "yellow",
                f"Scheduled runs run without write protection: there is none for Scout here yet ({result.reason}).",
                "Nothing to do until Scout ships it for this system. On macOS, /usr/bin/sandbox-exec must exist.",
                ACTION_CHECK_SANDBOX,
            )
        )
    elif result.status == "conflict":
        problems.append(
            _problem(
                "sandbox.conflict",
                "yellow",
                "Scheduled runs run under Claude Code's own sandbox instead of Scout's write protection: "
                "sandbox.enabled is on in your Claude settings, and the two can't run together.",
                "Turn off sandbox.enabled in the Claude settings the runs use if you want Scout's write protection.",
                ACTION_CHECK_SANDBOX,
            )
        )
    return block, problems


def gate_probe_part(vault: Path, *, now: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(the last privacy-check probe, its problem). Reads the runners' cache; never starts claude."""
    from scout.agent_runtimes import gate_probe

    cached = gate_probe.read_cache(vault)
    if cached is None:
        return {
            "status": "not_checked",
            "reason": "",
            "checked_at": None,
            "claude_version": None,
            "unsafe": "",
            "stale": False,
        }, []
    try:
        checked = datetime.fromisoformat(cached.checked_at)
        stale = not (now - checked < gate_probe.MAX_AGE)
        checked_at: str | None = health.iso_z(checked)
    except ValueError:
        stale, checked_at = True, None
    status = "passed" if cached.ok else ("unsafe" if cached.unsafe else "failed")
    block = {
        "status": status,
        "reason": cached.reason,
        "checked_at": checked_at,
        "claude_version": cached.claude_version,
        "unsafe": cached.unsafe,
        "stale": stale,
    }
    if cached.ok:
        return block, []
    if cached.unsafe:
        summary = (
            f"Scheduled runs are paused: Scout couldn't check its privacy checks safely on this Mac ({cached.reason})."
        )
    else:
        summary = (
            f"Scheduled runs are paused: the Claude Code they use didn't load Scout's privacy checks ({cached.reason})."
        )
    return block, [
        _problem(
            "gates.probe_failed",
            "red",
            summary,
            "Check again after updating Scout or Claude Code. Runs resume on their own once the check passes.",
            ACTION_CHECK_GATES,
        )
    ]


def last_run_part(rows: list[health.OutcomeRow]) -> dict[str, Any] | None:
    last = next((r for r in reversed(rows) if r.outcome not in health.SKIP_OUTCOMES), None)
    if last is None:
        return None
    return {
        "slot": last.slot,
        "mode": last.mode,
        "finished_at": health.iso_z(last.finished_at),
        "outcome": last.outcome,
        "sandbox": last.sandbox,
        "sandbox_source": last.sandbox_source,
        "integrity": last.integrity,
        "log": f".scout-logs/{Path(last.log).name}" if last.log else None,
    }


def broker_part(vault: Path, *, last_run_sandbox: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The commit and Telegram broker status the newest run recorded (Phase 1d), and its problem."""
    try:
        data = json.loads((vault / ".scout-logs" / BROKER_STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        data = None
    if not isinstance(data, dict) or data.get("status") not in BROKER_STATUSES:
        return {"status": "not_recorded", "reason": "", "recorded_at": None}, []
    at = data.get("at")
    recorded_at = None
    if isinstance(at, int) and not isinstance(at, bool):
        try:
            recorded_at = health.iso_z(datetime.fromtimestamp(at, UTC))
        except (OverflowError, OSError, ValueError):
            recorded_at = None
    reason = data.get("reason") if isinstance(data.get("reason"), str) else ""
    block = {"status": data["status"], "reason": reason, "recorded_at": recorded_at}
    # The status describes the newest run; a run without the sandbox never had a broker to miss.
    if data["status"] != "unavailable" or last_run_sandbox != "enforced":
        return block, []
    return block, [
        _problem(
            "broker.unavailable",
            "yellow",
            "The last scheduled run ran without its commit and Telegram broker, so it could still write the vault's "
            f"git history directly and read the Telegram token ({reason or 'no reason given'}).",
            "If the reason names an old git-safe-commit.sh, update Scout. Otherwise the run log's `broker:` lines "
            "say why; the next run tries again on its own.",
            None,
        )
    ]


def slots_part(vault: Path, env: Mapping[str, str]) -> list[dict[str, Any]]:
    """Each scheduled slot's agent runtime, model and run limits now (``agent-runtime show --slot`` for each)."""
    from scout.agent_runtimes import limits as _limits
    from scout.agent_runtimes import registry
    from scout.agent_runtimes.resolve import resolve_model_for, resolve_runtime
    from scout.schedule import load_default_schedule, load_schedule

    path = vault / ".scout-state" / "schedule.yaml"
    try:
        schedule = load_schedule(path) if path.exists() else load_default_schedule()
    except ConfigError:
        return []
    out: list[dict[str, Any]] = []
    for key, slot in schedule.items():
        row: dict[str, Any] = {
            "key": key,
            "type": str(slot.type.value),
            "runtime": None,
            "runtime_source": None,
            "model": None,
            "model_source": None,
            "run_timeout_s": None,
            "timeout_source": None,
            "max_tokens_per_day": None,
            "ceiling_source": None,
            "ignored": [],
            "error": None,
        }
        try:
            rt = resolve_runtime(vault, key, environ=env)
            model = resolve_model_for(vault, key, rt.runtime, environ=env)
            lim = _limits.limits_for(vault, registry.get(rt.runtime), key, env=env)
        except (ConfigError, KeyError) as e:
            row["error"] = str(e.args[0] if isinstance(e, KeyError) and e.args else e)
            out.append(row)
            continue
        row.update(
            runtime=rt.runtime,
            runtime_source=rt.source,
            model=model.model,
            model_source=model.source,
            run_timeout_s=lim.run_timeout_s,
            timeout_source=lim.timeout_source,
            max_tokens_per_day=lim.max_tokens_per_day,
            ceiling_source=lim.ceiling_source,
            ignored=list(lim.ignored),
        )
        out.append(row)
    return out


def ledger_problems(vault: Path, rows: list[health.OutcomeRow], *, now: datetime) -> list[dict[str, Any]]:
    """The run-health findings about protection, as problems."""
    found: list[health.Finding] = [
        *health.sandbox_findings(rows, vault=vault),
        *health.gates_findings(rows, vault=vault),
        *health.integrity_findings(rows, vault=vault, now=now),
        *health.runtime_findings(rows, vault=vault),
    ]
    return [
        _problem(f.id, f.severity, f.summary, f.fix, _LEDGER_ACTIONS.get(f.id), _rel(vault, f.log))
        for f in found
        if f.id in _LEDGER_ACTIONS
    ]


# ----- the report -----------------------------------------------------------------------------------------------


def report(
    vault: Path,
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
    prepare: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """The run-protection summary for ``vault``. Read-only; never raises for a missing or broken part."""
    now = now or datetime.now(UTC)
    env = dict(os.environ if env is None else env)
    try:
        rows = health.read_outcomes(vault)
    except Exception:  # noqa: BLE001 - an unreadable ledger reads as no runs
        rows = []
    sandbox, sandbox_now = sandbox_part(vault, env, prepare=prepare)
    gates, gates_now = gate_probe_part(vault, now=now)
    last_run = last_run_part(rows)
    broker, broker_now = broker_part(vault, last_run_sandbox=last_run["sandbox"] if last_run else None)
    from_ledger = ledger_problems(vault, rows, now=now)
    ledger_ids = {p["id"] for p in from_ledger}
    problems = list(from_ledger)
    # What's wrong now replaces the ledger's word on the last run, and the ledger's word on the runs it skipped
    # replaces what's wrong now: one problem per cause.
    if sandbox_now:
        problems = [p for p in problems if p["id"] != "runs.unsandboxed"]
        if "sandbox.failed" not in ledger_ids or sandbox_now[0]["id"] != "sandbox.not_ready":
            problems += sandbox_now
    if gates_now and not ({"gates.not_loaded", "gates.probe_unsafe"} & ledger_ids):
        problems += gates_now
    problems += broker_now
    problems.sort(key=lambda p: -health.SEVERITY_ORDER.get(p["severity"], 0))
    worst = max((health.SEVERITY_ORDER.get(p["severity"], 0) for p in problems), default=0)
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluated_at": health.iso_z(now),
        "severity": {0: "green", 1: "yellow", 2: "red"}[worst],
        "sandbox": sandbox,
        "gate_probe": gates,
        "last_run": last_run,
        "broker": broker,
        "slots": slots_part(vault, env),
        "problems": problems,
    }


def format_text(data: Mapping[str, Any]) -> str:
    """The human form of :func:`report`, for ``scoutctl protection status`` without --json."""
    lines = [f"run protection: {data['severity']}"]
    sb = data["sandbox"]
    lines.append(f"  write sandbox: {sb['status']} ({sb['reason'] or 'no reason'}); switch {sb['switch']}")
    gp = data["gate_probe"]
    lines.append(
        f"  privacy-check probe: {gp['status']}" + (f", checked {gp['checked_at']}" if gp["checked_at"] else "")
    )
    last = data["last_run"]
    if last:
        lines.append(
            f"  last run: {last['slot']} at {last['finished_at']}: sandbox {last['sandbox'] or 'not recorded'}, "
            f"integrity {last['integrity'] or 'not recorded'}"
        )
    lines.append(f"  broker: {data['broker']['status']}")
    for s in data["slots"]:
        if s["error"]:
            lines.append(f"  slot {s['key']}: {s['error']}")
            continue
        minutes = f"{s['run_timeout_s'] // 60} min" if s["run_timeout_s"] else "no time limit"
        lines.append(f"  slot {s['key']}: {s['runtime']} ({s['model']}), {minutes}")
    for p in data["problems"]:
        lines.append(f"  [{p['severity']}] {p['summary']} {p['fix']}")
    return "\n".join(lines)
