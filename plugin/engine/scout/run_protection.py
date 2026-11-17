"""Run protection: one read-only summary of what keeps a scheduled run from going wrong (app card C9).

``scoutctl protection status --json`` prints :func:`report`. The macOS app's Settings ▸ Engine "Run protection"
card reads only this, so it is a contract: ``tests/fixtures/contract/protection-status.json`` is generated from
``tests/unit/test_run_protection_contract.py`` and byte-copied to ``apps/macos/ScoutTests/Fixtures/``.

It gathers what other commands already decide, and decides nothing new:

- **sandbox**: whether scheduled runs get the OS write sandbox, judged as the scheduler sees it (so
  ``SCOUT_RUN_SANDBOX`` in the caller's shell is left out, as the doctor does). ``off``, ``unavailable`` and
  ``conflict`` come from :func:`scout.sandbox.launch.precheck`, which only reads files. Telling ``enforced`` from
  ``failed`` takes building and self-testing the profile (``sandbox-exec`` and a temp dir), so the plain report takes
  that from the newest run that recorded it, and only ``--check-sandbox`` (an owner's click in the app) self-tests;
- **gate_probe**: the last privacy-check probe the runners ran (the ``.scout-state/gate-probe.json`` cache; reading
  it never starts claude);
- **last_run**: the newest run that wasn't skipped, from the run ledger (:func:`scout.health.read_outcomes`), with
  its sandbox and post-run integrity result;
- **broker**: the commit and Telegram broker status the newest run recorded (``.scout-logs/.run-broker.json``);
- **slots**: the agent runtime, model and run limits each scheduled slot would run with now;
- **problems**: what needs the owner, each with the fix and an ``action`` the app turns into a button.

Paths in the output are relative to the vault, so the fixture doesn't depend on where a test's vault lives.
Without ``--check-sandbox`` it starts no process and writes nothing.

Most of what it reads lives in ``.scout-logs`` and ``.scout-state``, which a scheduled run can write. So every field
taken from those files is shape-checked before it is shown: a value that isn't one of its known words reads as
``null`` or ``"unknown"``, names must fit a fixed pattern, and free text is trimmed, stripped of control characters
and secret-looking tokens, and has home folders replaced by ``~``.
"""

from __future__ import annotations

import json
import os
import re
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
ACTION_CHECK_SANDBOX = "check_sandbox"  # scoutctl protection status --json --check-sandbox (self-tests the profile)
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


# ----- shapes for what a run can write --------------------------------------------------------------------------

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")  # a slot, mode or runtime
_WORD_RE = re.compile(r"[a-z][a-z0-9_.-]{0,40}")  # an outcome or a source
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,80}")
_VERSION_RE = re.compile(r"\d{1,4}(?:\.\d{1,6}){1,3}(?:[-+][0-9A-Za-z.]{1,20})?")
_LOG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.log")
_PATH_PART_RE = re.compile(r"\.?[A-Za-z0-9][A-Za-z0-9._-]{0,120}")
_RUN_SANDBOX = frozenset({"enforced", "unavailable", "off", "conflict", "failed"})
_RUN_SANDBOX_SOURCE = frozenset({"env", "switch"})
_RUN_INTEGRITY = frozenset({"clean", "restored", "unrestored", "error", "unchecked"})

_REDACTED = "[redacted]"
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # key=value / key: value, for the names a secret goes by
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?key|key|token|secret|password|passwd|pwd|auth|authorization|"
            r"cookie|credentials?|session)(\s*[=:]\s*)\S+"
        ),
        rf"\1\2{_REDACTED}",
    ),
    (re.compile(r"(?i)\bbearer\s+\S+"), f"Bearer {_REDACTED}"),
    # well-known token prefixes, whatever surrounds them
    (re.compile(r"(?:sk-|xox[abprs]-|gh[pousr]_|github_pat_|glpat-|AKIA|ASIA)[A-Za-z0-9_\-]*"), _REDACTED),
    # any long opaque run of token characters
    (re.compile(r"[A-Za-z0-9+/_=-]{32,}"), _REDACTED),
)
_HOME_RE = re.compile(r"/(?:Users|home)/[^/\s]+")


def _text(value: object, limit: int = 240) -> str:
    """Free text a run could have written, made safe to show: one line, no secret-looking tokens, ``~`` for home."""
    if not isinstance(value, str):
        return ""
    out = re.sub(r"[\x00-\x1f\x7f\u2028\u2029]+", " ", value)
    out = _HOME_RE.sub("~", out)
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    out = " ".join(out.split())
    return out if len(out) <= limit else out[: limit - 1].rstrip() + "\u2026"


def _match(value: object, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _pick(value: object, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _rel(vault: Path, path: str | None) -> str | None:
    """``path`` relative to the vault when it's inside it, else its basename; None for anything oddly shaped."""
    if not path:
        return None
    p = Path(path)
    try:
        rel = p.resolve().relative_to(vault.resolve()).as_posix()
    except (ValueError, OSError):
        rel = p.name
    parts = rel.split("/")
    if len(parts) > 4 or not all(_PATH_PART_RE.fullmatch(part) and part != ".." for part in parts):
        return None
    return rel


def _problem(
    id: str, severity: str, summary: str, fix: str, action: str | None, log: str | None = None
) -> dict[str, Any]:
    # Summaries quote reasons a run could have written (the ledger's, the probe's, the broker's).
    return {
        "id": id,
        "severity": severity,
        "summary": _text(summary, 600),
        "fix": _text(fix, 600),
        "action": action,
        "log": log,
    }


# ----- the parts ------------------------------------------------------------------------------------------------


def sandbox_part(
    vault: Path,
    env: Mapping[str, str],
    rows: list[health.OutcomeRow],
    *,
    check: bool = False,
    precheck: Callable[..., Any] | None = None,
    prepare: Callable[..., Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(the sandbox block, its problems now).

    Read-only unless ``check``: off / unavailable / conflict come from :func:`launch.precheck` (files only), and
    enforced / failed from the newest run that recorded a sandbox status. ``check`` (an owner's click) builds and
    self-tests the profile with :func:`launch.prepare` instead. ``precheck`` and ``prepare`` stand in for them in tests.
    """
    from scout.sandbox import launch

    # Scheduled runs don't inherit this shell's SCOUT_RUN_SANDBOX (bootstrap_doctor._check_run_sandbox).
    sched_env = {k: v for k, v in env.items() if k != launch.ENV_SWITCH}
    on, source = launch.switch(sched_env)
    block: dict[str, Any] = {
        "status": "unknown",
        "reason": "",
        "switch": "on" if on else "off",
        "switch_source": "default" if source == "default" else "owner",
        "checked_by": None,
    }
    if env.get("SCOUT_SANDBOX_STATUS") == "enforced":
        block.update(status="enforced", reason="this session runs inside it", checked_by="this_run")
        return block, []
    try:
        result = (precheck or launch.precheck)(vault, sched_env)
        checked_by = "settings"
        if result is None and check:
            result, checked_by = (prepare or launch.prepare)(vault, sched_env), "self_test"
    except Exception as e:  # noqa: BLE001 - a bug here must not hide the rest of the card
        block["reason"] = _text(f"couldn't check: {e}")
        return block, [
            _problem(
                "sandbox.unchecked",
                "yellow",
                "Scout couldn't check whether scheduled runs get write protection.",
                f"Check again. If it keeps failing, `scoutctl sandbox status` says why ({e}).",
                ACTION_CHECK_SANDBOX,
            )
        ]
    if result is None:
        # Read-only: what the newest run that recorded a sandbox status found (a skipped run counts: a sandbox that
        # can't be set up skips the run).
        newest = next((r for r in reversed(rows) if _pick(r.sandbox, _RUN_SANDBOX)), None)
        if newest is None or newest.sandbox not in ("enforced", "failed"):
            block.update(
                status="not_checked",
                reason="no scheduled run has used it since it was last turned on; Check now tests it",
            )
            return block, []
        name = _match(newest.slot, _NAME_RE) or "a scheduled run"
        when = health.iso_z(newest.finished_at)
        if newest.sandbox == "enforced":
            result = launch.Prepared("enforced", f"the last run ({name}, {when}) ran inside it")
        else:
            result = launch.Prepared("failed", f"the last run ({name}, {when}) couldn't set it up; its log says why")
        checked_by = "last_run"
    # "turned off (<the switch file's absolute path>)" says where; the card only needs who.
    reason = "turned off by the owner (scoutctl sandbox off)" if result.status == "off" else _text(result.reason)
    block.update(status=result.status, reason=reason, checked_by=checked_by)
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
                f"until it can ({reason}).",
                "Check again. Runs resume on their own once it works. To run without it meanwhile, turn it off.",
                ACTION_CHECK_SANDBOX,
            )
        )
    elif result.status == "unavailable":
        problems.append(
            _problem(
                "sandbox.unavailable",
                "yellow",
                f"Scheduled runs run without write protection: there is none for Scout here yet ({reason}).",
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
    reason = _text(cached.reason)
    block = {
        "status": status,
        "reason": reason,
        "checked_at": checked_at,
        "claude_version": _match(cached.claude_version, _VERSION_RE),
        "unsafe": cached.unsafe,  # read_cache keeps only gate_probe.UNSAFE_EXIT's keys
        "stale": stale,
    }
    if cached.ok:
        return block, []
    if cached.unsafe:
        summary = f"Scheduled runs are paused: Scout couldn't check its privacy checks safely on this Mac ({reason})."
    else:
        summary = f"Scheduled runs are paused: the Claude Code they use didn't load Scout's privacy checks ({reason})."
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
    log = _match(Path(last.log).name, _LOG_RE) if last.log else None
    return {
        "slot": _match(last.slot, _NAME_RE) or "unknown",
        "mode": _match(last.mode, _NAME_RE) or "unknown",
        "finished_at": health.iso_z(last.finished_at),
        "outcome": _match(last.outcome, _WORD_RE) or "unknown",
        "sandbox": _pick(last.sandbox, _RUN_SANDBOX),
        "sandbox_source": _pick(last.sandbox_source, _RUN_SANDBOX_SOURCE),
        "integrity": _pick(last.integrity, _RUN_INTEGRITY),
        "log": f".scout-logs/{log}" if log else None,
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
    reason = _text(data.get("reason"))
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
            "key": _match(key, _NAME_RE) or "unknown",
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
            row["error"] = _text(str(e.args[0] if isinstance(e, KeyError) and e.args else e))
            out.append(row)
            continue
        row.update(
            runtime=_match(rt.runtime, _NAME_RE),
            runtime_source=_match(rt.source, _WORD_RE),
            model=_match(model.model, _MODEL_RE),
            model_source=_match(model.source, _WORD_RE),
            run_timeout_s=lim.run_timeout_s,
            timeout_source=_match(lim.timeout_source, _WORD_RE),
            max_tokens_per_day=lim.max_tokens_per_day,
            ceiling_source=_match(lim.ceiling_source, _WORD_RE),
            ignored=[_text(i, 160) for i in lim.ignored],
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
    check_sandbox: bool = False,
    precheck: Callable[..., Any] | None = None,
    prepare: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """The run-protection summary for ``vault``; never raises for a missing or broken part.

    Read-only (no process, no write) unless ``check_sandbox``, which self-tests the sandbox profile.
    """
    now = now or datetime.now(UTC)
    env = dict(os.environ if env is None else env)
    try:
        rows = health.read_outcomes(vault)
    except Exception:  # noqa: BLE001 - an unreadable ledger reads as no runs
        rows = []
    sandbox, sandbox_now = sandbox_part(vault, env, rows, check=check_sandbox, precheck=precheck, prepare=prepare)
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
