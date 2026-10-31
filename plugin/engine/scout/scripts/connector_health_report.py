"""Connector health report — rolls up `.scout-logs/connector-calls-*.jsonl`
into `knowledge-base/connector-health.md` and fires alerts.

Direct port of ``~/Scout/scripts/connector-health-report.sh``. Rules preserved:
  - Mode-aware baseline: alert if connector was healthy on >=2 of last 3 prior
    same-mode runs (or >=1 of 1-2 if fewer same-mode runs exist).
  - Chronic-skip override: >=3 consecutive dark runs in a required mode -> alert.
  - Pattern #48 suppression: total_ok_ever == 0 -> don't alert (unwired).
  - Warning rule: >=3 calls + >50% errors (skipped if connector already CRITICAL).

Connector roster sourced from :mod:`scout.connectors` (Task 1) — replaces the
hardcoded CRITICAL / OPTIONAL / REQUIRED_IN / REMEDIATION dicts in the bash.

Note on timezone: the bash original hardcoded ``-4`` (EDT only); an earlier
port hardcoded ``America/New_York``. Timestamps now render in the CONFIGURED
zone (scout.config.resolve_timezone, #207) with the real ``%Z`` abbreviation.
"""

from __future__ import annotations

import collections
import contextlib
import json
import os
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scout import connector_calls, paths
from scout.config import resolve_timezone
from scout.connectors import Capability, Connector, ConnectorRegistry, load_registry
from scout.events import Event, now_iso
from scout.ids import new_ulid
from scout.schedule import SlotType

# Set for the length of one run(): the zone is read from the config once,
# not once per rendered timestamp (11 config reads per report before #80).
_run_zone: ZoneInfo | None = None


def _zone() -> ZoneInfo:
    """Configured display/day-boundary zone (was hardcoded ET; #207)."""
    return _run_zone if _run_zone is not None else resolve_timezone()


DEFAULT_WINDOW_DAYS = 14
DEFAULT_RETAIN_DAYS = connector_calls.RETAIN_DAYS
# Per-file summary of the ledger under .scout-cache/ (#80); see load_records.
INDEX_FILENAME = "connector-calls-index.json"
INDEX_VERSION = 1
# A file modified this recently isn't summarised: on a coarse-timestamp
# filesystem a same-size rewrite in the same tick would keep its stat key.
_RACY_WINDOW_NS = 2_000_000_000
DEFAULT_MATRIX_DEPTH = 10
WARN_TOTAL_THRESHOLD = 3
WARN_ERR_RATIO = 0.5
HEALTHY_OK_THRESHOLD = 3
DARK_GAP_THRESHOLD = 3
# Pattern #54: if a connector logged a healthy run in ANY mode within this window,
# a 0-call (not 0-error) current run is "alive but unused this mode", not an outage.
CROSS_MODE_LIVENESS_WINDOW = timedelta(hours=4)


# ----- data structures -----------------------------------------------------


@dataclass(frozen=True)
class Alert:
    level: str  # "CRITICAL" | "WARNING"
    name: str  # display name
    reason: str
    connector_key: str
    err_sample: str

    def to_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "name": self.name,
            "reason": self.reason,
            "connector_key": self.connector_key,
            "err_sample": self.err_sample,
        }


# ----- I/O layer -----------------------------------------------------------


def _default_now() -> datetime:
    """Module-level seam so tests can monkeypatch the clock."""
    return datetime.now(UTC)


_StatKey = list[int]


def _stat_key(st: os.stat_result) -> _StatKey:
    return [st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino]


def _read_index(path: Path) -> dict[str, dict[str, Any]]:
    """The per-file index, or empty when it is missing, torn or from another version."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != INDEX_VERSION or not isinstance(data.get("files"), dict):
        return {}
    return data["files"]


def _write_index(path: Path, files: dict[str, dict[str, Any]]) -> None:
    """Replace the index atomically; a failure only costs the next run a re-read."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"version": INDEX_VERSION, "files": files}, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _all_before(entry: dict[str, Any] | None, key: _StatKey, cutoff: datetime) -> bool:
    """True when the index proves every row of this unchanged file predates ``cutoff``."""
    if not isinstance(entry, dict) or entry.get("key") != key:
        return False
    newest = entry.get("max_ts")
    if newest is None:
        return True  # no row with a readable timestamp: the file contributes nothing
    try:
        return bool(datetime.fromisoformat(newest) < cutoff)
    except (TypeError, ValueError):
        return False


def load_records(
    log_dir: Path,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
    index_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Load JSONL records inside the rolling window, skipping interactive runs.

    Each returned record has an extra ``_ts`` key holding a ``datetime``.
    Malformed lines and unreadable files are silently skipped — same as bash.

    With ``index_path`` (#80), a small index remembers each file's newest row
    timestamp under the file's stat key (mtime, ctime, size, inode). A file
    whose key is unchanged and whose newest row is older than the cutoff is
    skipped without being opened: none of its rows could be returned. Any
    change to a file changes its key, so it is read again; a file's name is
    never trusted for its dates. The result is exactly what a full read gives.
    """
    n = now or _default_now()
    cutoff = n - timedelta(days=window_days)
    records: list[dict[str, Any]] = []
    log_dir = Path(log_dir)
    index = _read_index(index_path) if index_path is not None else {}
    seen: dict[str, dict[str, Any]] = {}
    for path in sorted(log_dir.glob(connector_calls.GLOB)):
        try:
            st = path.stat()
        except OSError:
            continue
        # Stat before the read: an append landing mid-read changes the key,
        # so the next run reads the file again.
        key = _stat_key(st)
        if _all_before(index.get(path.name), key, cutoff):
            seen[path.name] = index[path.name]
            continue
        newest: datetime | None = None
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    try:
                        ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
                    except (KeyError, ValueError, AttributeError):
                        continue
                    old = ts < cutoff
                    if newest is None or ts > newest:
                        newest = ts
                    if old:
                        continue
                    if r.get("mode", "interactive") in ("interactive", "unknown"):
                        continue
                    r["_ts"] = ts
                    records.append(r)
        except OSError:
            continue
        if time.time_ns() - st.st_mtime_ns > _RACY_WINDOW_NS:
            seen[path.name] = {"key": key, "max_ts": newest.isoformat() if newest else None}
    if index_path is not None and seen != index:
        _write_index(index_path, seen)
    return records


def cleanup_old_jsonl(
    log_dir: Path,
    *,
    retain_days: int = DEFAULT_RETAIN_DAYS,
    now: datetime | None = None,
) -> None:
    """Delete connector-calls-*.jsonl files older than ``retain_days``.

    Bash filename format: ``connector-calls-YYYY-MM-DD.jsonl``. Date is treated
    as UTC midnight to match bash semantics (``replace(tzinfo=UTC)``). The
    writers prune the same way on each day's first row (#66).
    """
    connector_calls.prune(Path(log_dir), retain_days=retain_days, now=now or _default_now())


# ----- aggregation ---------------------------------------------------------


SessionList = list[tuple[str, list[dict[str, Any]]]]
StatsMap = dict[str, dict[str, dict[str, int]]]


def group_by_session(records: list[dict[str, Any]]) -> SessionList:
    """Group records by session_id, sorted by min(_ts) ascending.

    Bash behavior preserved: a session's ordering key is the FIRST timestamp.
    """
    sessions: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in records:
        sessions[r.get("session_id", "unknown")].append(r)
    return sorted(sessions.items(), key=lambda kv: min(r["_ts"] for r in kv[1]))


def compute_stats(session_list: SessionList, alertable_keys: Iterable[str]) -> StatsMap:
    """stats[connector_key][session_id] = {"ok": N, "err": M}."""
    keys = set(alertable_keys)
    stats: StatsMap = {c: collections.defaultdict(lambda: {"ok": 0, "err": 0}) for c in keys}
    for sid, recs in session_list:
        for r in recs:
            c = r.get("connector")
            if c not in keys:
                continue
            bucket = "err" if r.get("error") else "ok"
            stats[c][sid][bucket] += 1
    return stats


def _current_slot_type(mode: str, *, data_dir: Path | None = None) -> SlotType:
    """Look up ``mode`` (a slot key) in the active schedule and return its SlotType.

    Falls back to the plugin-shipped default schedule if the vault file is
    absent (e.g. on a fresh install or in a test fixture). Returns
    ``SlotType.MANUAL`` for unrecognized slot keys — manual is intentionally
    excluded from any connector's ``required_in_types``, so an unknown mode
    never accidentally fires a chronic-skip alert.

    Lazy imports (``scout.schedule``) keep this side-effect-free at module
    import time and avoid any circular-import surprises.
    """
    from scout.schedule import load_default_schedule, load_schedule

    target = data_dir if data_dir is not None else paths.data_dir()
    vault_schedule = paths.state_dir(target) / "schedule.yaml"
    try:
        sched = load_schedule(vault_schedule) if vault_schedule.exists() else load_default_schedule()
    except Exception:
        # Defensive: a malformed vault schedule should not crash the health
        # report. Fall back to defaults.
        sched = load_default_schedule()
    if mode in sched:
        return sched[mode].type
    return SlotType.MANUAL


def _alertable_registry(registry: ConnectorRegistry) -> dict[str, Connector]:
    """Connectors that can be alerting subjects.

    Outbound-only connectors (e.g. ``notify:telegram``) are excluded — outbound
    failures don't fail a run. Anything with ``inbound`` or ``meta`` capability
    counts.
    """
    out: dict[str, Connector] = {}
    for key, c in registry.items():
        caps = set(c.capabilities)
        if Capability.INBOUND in caps or Capability.META in caps:
            out[key] = c
    return out


# ----- alert rules ---------------------------------------------------------


def _ok_count(stats: StatsMap, c: str, sid: str) -> int:
    return stats[c][sid]["ok"]


def _err_count(stats: StatsMap, c: str, sid: str) -> int:
    return stats[c][sid]["err"]


def dark_runs(stats: StatsMap, c: str, session_list: SessionList) -> int:
    """Consecutive runs (including current) with 0 OK calls."""
    n = 0
    for sid, _ in reversed(session_list):
        if stats[c][sid]["ok"] == 0:
            n += 1
        else:
            break
    return n


def total_ok_ever(
    stats: StatsMap,
    c: str,
    prior_sessions: SessionList,
    current_sid: str,
) -> int:
    return sum(stats[c][sid]["ok"] for sid, _ in prior_sessions) + stats[c][current_sid]["ok"]


def last_healthy_ts(
    stats: StatsMap,
    c: str,
    prior_sessions: SessionList,
    sessions_by_id: dict[str, list[dict[str, Any]]],
    threshold: int = HEALTHY_OK_THRESHOLD,
) -> datetime | None:
    """Most recent prior run with >= threshold OK calls; else None."""
    for sid, _ in reversed(prior_sessions):
        if stats[c][sid]["ok"] >= threshold:
            return sessions_by_id[sid][0]["_ts"]
    return None


def mode_baseline(
    stats: StatsMap,
    c: str,
    prior_sessions: SessionList,
    mode: str,
    sessions_by_id: dict[str, list[dict[str, Any]]],
    n: int = 3,
) -> tuple[int, int]:
    """Return (samples, healthy) for the last n prior runs of ``mode``.

    Healthy = ok_count >= 3.
    """
    same = [sid for sid, _ in prior_sessions if sessions_by_id[sid][0].get("mode") == mode][-n:]
    healthy = sum(1 for sid in same if stats[c][sid]["ok"] >= HEALTHY_OK_THRESHOLD)
    return len(same), healthy


def recent_error_sample(records: list[dict[str, Any]], c: str, limit: int = 140) -> str:
    """Most recent non-empty error snippet for ``c`` (truncated)."""
    for r in reversed(records):
        if r.get("connector") != c or not r.get("error"):
            continue
        snippet = r.get("err") or ""
        if snippet:
            return snippet[:limit]
    return ""


def fmt_ts(ts: datetime | None) -> str:
    if not ts:
        return "never"
    return ts.astimezone(_zone()).strftime("%b %-d %H:%M %Z")


def compute_critical_alerts(
    stats: StatsMap,
    session_list: SessionList,
    sessions_by_id: dict[str, list[dict[str, Any]]],
    alertable: dict[str, Connector],
    records: list[dict[str, Any]],
    *,
    data_dir: Path | None = None,
) -> list[Alert]:
    """Mode-aware critical-connector rule + chronic-skip override + Pattern #48."""
    alerts: list[Alert] = []
    if not session_list:
        return alerts
    current_sid = session_list[-1][0]
    current_mode = sessions_by_id[current_sid][0].get("mode", "unknown")
    prior_sessions = session_list[:-1]

    # Plan 5: connectors are required by SLOT TYPE, not slot key. Resolve the
    # current run's slot type once via the schedule (vault → defaults).
    current_slot_type = _current_slot_type(current_mode, data_dir=data_dir)

    for c, connector in alertable.items():
        curr_ok = _ok_count(stats, c, current_sid)
        if curr_ok != 0:
            continue
        gap = dark_runs(stats, c, session_list)
        samples, healthy = mode_baseline(stats, c, prior_sessions, current_mode, sessions_by_id, n=3)
        if samples >= 3:
            should_alert = healthy >= 2
        elif samples >= 1:
            should_alert = healthy >= 1
        else:
            should_alert = False

        # Chronic-skip override: dark for >=3 runs in a slot type that requires
        # this connector.
        mode_required = connector.required_in_type(current_slot_type)
        if not should_alert and gap >= DARK_GAP_THRESHOLD and mode_required:
            should_alert = True

        if not should_alert:
            continue

        # Pattern #48: never-wired connectors don't get OUTAGE alerts.
        if total_ok_ever(stats, c, prior_sessions, current_sid) == 0:
            continue

        last_ok = last_healthy_ts(stats, c, prior_sessions, sessions_by_id, HEALTHY_OK_THRESHOLD)

        # Pattern #54: cross-mode liveness. If this run made no error calls (the
        # connector simply wasn't exercised in this mode) AND the connector was
        # healthy in ANY mode within the liveness window, it's alive — not an
        # outage. Suppress the false CRITICAL. Error calls this run still alert.
        curr_err = _err_count(stats, c, current_sid)
        current_ts = sessions_by_id[current_sid][0]["_ts"]
        if curr_err == 0 and last_ok is not None and current_ts - last_ok <= CROSS_MODE_LIVENESS_WINDOW:
            continue

        reason = (
            f"0 successful calls in `{current_mode}` run; "
            f"{healthy}/{samples} prior `{current_mode}` runs were healthy; "
            f"dark for {gap} run(s) total; last healthy {fmt_ts(last_ok)}"
        )
        alerts.append(
            Alert(
                level="CRITICAL",
                name=connector.display_name,
                reason=reason,
                connector_key=c,
                err_sample=recent_error_sample(records, c),
            )
        )
    return alerts


def compute_warning_alerts(
    stats: StatsMap,
    current_sid: str,
    alertable: dict[str, Connector],
    records: list[dict[str, Any]],
    critical_keys: set[str],
) -> list[Alert]:
    alerts: list[Alert] = []
    for c, connector in alertable.items():
        if c in critical_keys:
            continue
        ok = _ok_count(stats, c, current_sid)
        err = _err_count(stats, c, current_sid)
        total = ok + err
        if total >= WARN_TOTAL_THRESHOLD and err / total > WARN_ERR_RATIO:
            alerts.append(
                Alert(
                    level="WARNING",
                    name=connector.display_name,
                    reason=f"{err}/{total} calls errored this run ({int(err / total * 100)}%)",
                    connector_key=c,
                    err_sample=recent_error_sample(records, c),
                )
            )
    return alerts


# ----- rendering -----------------------------------------------------------


def _compact_mode(mode: str) -> str:
    return (
        mode.replace("consolidation-", "c-").replace("morning-briefing", "morning").replace("weekend-briefing", "wknd")
    )


def _matrix_cell(stats: StatsMap, c: str, sid: str) -> str:
    s = stats[c][sid]
    total = s["ok"] + s["err"]
    if total == 0:
        return "·"
    if s["ok"] == 0:
        return "❌"
    if total >= WARN_TOTAL_THRESHOLD and s["err"] / total > WARN_ERR_RATIO:
        return f"⚠️ {s['ok']}/{total}"
    return f"✅ {s['ok']}"


def render_health_md(
    *,
    now: datetime,
    session_list: SessionList,
    sessions_by_id: dict[str, list[dict[str, Any]]],
    stats: StatsMap,
    registry: ConnectorRegistry,
    alertable: dict[str, Connector],
    alerts: list[Alert],
    records: list[dict[str, Any]],
    lint_overrides: int | None = None,
    cap_markers: list[dict[str, Any]] | None = None,
) -> str:
    recent = session_list[-DEFAULT_MATRIX_DEPTH:]
    recent_ids = [sid for sid, _ in recent]

    lines: list[str] = [
        "# Connector Health",
        "",
        "Parent: [[knowledge-base]]",
        "",
        f"**Last updated:** {now.astimezone(_zone()).strftime('%Y-%m-%d %H:%M %Z')}",
        f"**Window:** last 14 days, scheduled scout runs only (`{len(recent)}` of `{len(session_list)}` shown).",
        *([f"**KB lint overrides (14d):** {lint_overrides}"] if lint_overrides is not None else []),
        *[
            f"**⚠️ Call log capped:** the {m['_ts'].astimezone(_zone()).strftime('%Y-%m-%d')} log hit its daily "
            f"size cap at {m['_ts'].astimezone(_zone()).strftime('%H:%M %Z')}; calls after that were not logged, "
            "so that day's counts are low."
            for m in cap_markers or []
        ],
        "",
    ]

    if alerts:
        lines += ["## 🔴 Active Alerts", ""]
        for a in alerts:
            icon = "🔴" if a.level == "CRITICAL" else "⚠️"
            connector = registry[a.connector_key] if a.connector_key in registry else None
            detail = (
                connector.remediation.detail
                if connector and connector.remediation.detail
                else "Check the connector status manually and restore it."
            )
            lines.append(f"### {icon} {a.name} — {a.level}")
            lines.append(a.reason)
            lines.append("")
            if a.err_sample:
                lines.append(f"**Last error logged:** `{a.err_sample}`")
                lines.append("")
            lines.append(f"**How to fix:** {detail}")
            lines.append("")
        lines.append("---")
        lines.append("")

    lines += [
        "## Status (last 10 scheduled runs)",
        "",
        "`✅ N` = N successful calls · `⚠️ ok/tot` = errors >50% · `❌` = 0 calls this run · `·` = no attempts",
        "",
    ]

    headers: list[str] = []
    for sid in recent_ids:
        ts = sessions_by_id[sid][0]["_ts"].astimezone(_zone())
        mode = sessions_by_id[sid][0].get("mode", "?")
        headers.append(f"{ts.strftime('%m-%d %H%M')}<br/>{_compact_mode(mode)}")

    lines.append("| Connector | " + " | ".join(headers) + " | 7d OK rate |")
    lines.append("|---|" + "---|" * (len(recent_ids) + 1))

    seven_cutoff = now - timedelta(days=7)
    ok7: collections.Counter[str] = collections.Counter()
    err7: collections.Counter[str] = collections.Counter()
    for r in records:
        c = r.get("connector")
        if c not in alertable or r["_ts"] < seven_cutoff:
            continue
        if r.get("error"):
            err7[c] += 1
        else:
            ok7[c] += 1

    for c, connector in alertable.items():
        cells = [_matrix_cell(stats, c, sid) for sid in recent_ids]
        total7 = ok7[c] + err7[c]
        rate = f"{int(ok7[c] / total7 * 100)}% ({ok7[c]}/{total7})" if total7 else "—"
        lines.append(f"| {connector.display_name} | " + " | ".join(cells) + f" | {rate} |")

    lines += [
        "",
        "## How this works",
        "",
        "- Every tool call in a scheduled scout run is logged by `hooks/connector-log.sh` "
        "(PostToolUse hook) to `.scout-logs/connector-calls-YYYY-MM-DD.jsonl`.",
        "- After each run, `scripts/connector-health-report.sh` rolls up the last 14 days, "
        "rewrites this file in place, and fires alerts on degradation.",
        "- Interactive Claude Code sessions in ~/Scout are not logged (the hook short-circuits "
        "when `SCOUT_MODE` is unset).",
        "",
        "### Alert rules",
        "- **Critical connector (mode-aware):** 0 successful calls in this run when ≥2 of the "
        "3 prior runs OF THE SAME MODE were healthy (≥3 successful calls each). If fewer than 3 "
        "prior same-mode runs exist, ≥1 healthy same-mode run is enough to trigger. If zero "
        "prior same-mode runs exist, no baseline → no alert.",
        "- **Chronic-skip override:** any connector dark across ≥3 consecutive scheduled runs "
        "fires an alert in modes where it's required by `connectors.yaml`.",
        "- **Pattern #48:** if a connector has never logged a successful call across the entire "
        "window, the alert is suppressed (it's unwired, not broken: wire it up rather than treat it as an outage).",
        "- **Pattern #54:** if a connector was healthy in any mode within the last 4h and the "
        "current run had 0 error calls, the CRITICAL is suppressed (alive but unused this mode).",
        "- **Warning:** any connector with ≥3 calls this run and >50% errors.",
        "",
        "Alerts are written to `.scout-logs/connector-alerts.log` and surfaced as a macOS "
        "notification. Raw call records live in `.scout-logs/connector-calls-*.jsonl` for 30 days.",
    ]
    return "\n".join(lines) + "\n"


def render_pending_alerts_md(
    alerts: list[Alert],
    *,
    current_mode: str,
    now: datetime,
    registry: ConnectorRegistry,
) -> str:
    lines = [
        f"🔴 **Connector alerts** (from scout run `{current_mode}` at "
        f"{now.astimezone(_zone()).strftime('%b %-d %H:%M %Z')}):"
    ]
    for a in alerts:
        icon = "🔴" if a.level == "CRITICAL" else "⚠️"
        connector = registry[a.connector_key] if a.connector_key in registry else None
        first_fix = (
            connector.remediation.first_fix
            if connector and connector.remediation.first_fix
            else "Check the connector manually."
        )
        lines.append(f"• {icon} *{a.name}* — {a.reason}")
        lines.append(f"   ↳ *Try first:* {first_fix}")
        if a.err_sample:
            lines.append(f"   ↳ *Last error:* `{a.err_sample}`")
    lines.append("_Full matrix + detailed remediation: `knowledge-base/connector-health.md`._")
    return "\n".join(lines) + "\n"


def fire_macos_notification(alerts: list[Alert]) -> None:
    """Best-effort `osascript display notification` — swallow all exceptions."""
    if not alerts:
        return
    summary = "; ".join(a.name for a in alerts[:3])
    if len(alerts) > 3:
        summary += f" (+{len(alerts) - 3} more)"
    # Pass the title and body as argv to an `on run argv` handler and read the
    # script from stdin. Connector names and reasons come from user-controlled
    # YAML and the JSONL log; interpolating them into the AppleScript *source*
    # (the old `display notification "{body}"`) allowed a name containing
    # quotes/backslashes/newlines to break the script or inject arbitrary
    # AppleScript. As argv they are pure data — never parsed as code. (#51)
    script = (
        'on run argv\n    display notification (item 2 of argv) with title (item 1 of argv) sound name "Basso"\nend run'
    )
    try:
        subprocess.run(
            ["osascript", "-", "Scout: connector degradation", summary],
            input=script,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        pass


# ----- top-level entry -----------------------------------------------------


def _lint_override_count(log_dir: Path, now: datetime) -> int:
    from scout.kb.lint import override_count

    return override_count(log_dir, days=DEFAULT_WINDOW_DAYS, now=now)


def run(
    *,
    data_dir: Path | None = None,
    now: datetime | None = None,
    cleanup: bool = False,
) -> Event | None:
    """Roll up the last 14 days; render the health doc; fire alerts.

    Returns an Event describing the outcome, or None when no scheduled-run
    records exist (matching bash's silent-exit-zero on first run).
    """
    global _run_zone
    _run_zone = resolve_timezone()
    try:
        return _run(data_dir=data_dir, now=now, cleanup=cleanup)
    finally:
        _run_zone = None


def _run(*, data_dir: Path | None, now: datetime | None, cleanup: bool) -> Event | None:
    n = now or _default_now()
    target_data = data_dir if data_dir is not None else paths.data_dir()
    log_dir = paths.logs_dir(target_data)
    out_path = paths.kb_dir(target_data) / "connector-health.md"
    alerts_path = log_dir / "connector-alerts.log"
    pending_path = paths.cache_dir(target_data) / "connector-alerts-pending.md"

    log_dir.mkdir(parents=True, exist_ok=True)

    if cleanup:
        cleanup_old_jsonl(log_dir, retain_days=DEFAULT_RETAIN_DAYS, now=n)

    registry = load_registry(data_dir=target_data)
    alertable = _alertable_registry(registry)

    records = load_records(
        log_dir,
        window_days=DEFAULT_WINDOW_DAYS,
        now=n,
        index_path=paths.cache_dir(target_data) / INDEX_FILENAME,
    )
    # A capped day's marker (#66) is a notice, not a call: keep it out of the
    # sessions and counts, and show it on the page.
    cap_markers = [r for r in records if r.get("connector") == connector_calls.CAP_CONNECTOR]
    if cap_markers:
        records = [r for r in records if r.get("connector") != connector_calls.CAP_CONNECTOR]
    if not records:
        return None

    sessions_by_id: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in records:
        sessions_by_id[r.get("session_id", "unknown")].append(r)

    session_list = group_by_session(records)
    if not session_list:
        return None

    stats = compute_stats(session_list, alertable.keys())

    current_sid = session_list[-1][0]
    current_mode = sessions_by_id[current_sid][0].get("mode", "unknown")

    critical_alerts = compute_critical_alerts(
        stats, session_list, dict(sessions_by_id), alertable, records, data_dir=target_data
    )
    critical_keys = {a.connector_key for a in critical_alerts}
    warning_alerts = compute_warning_alerts(stats, current_sid, alertable, records, critical_keys)
    alerts = critical_alerts + warning_alerts

    # Render connector-health.md.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_health_md(
            now=n,
            session_list=session_list,
            sessions_by_id=dict(sessions_by_id),
            stats=stats,
            registry=registry,
            alertable=alertable,
            alerts=alerts,
            records=records,
            lint_overrides=_lint_override_count(log_dir, n),
            cap_markers=cap_markers,
        ),
        encoding="utf-8",
    )

    pending_path.parent.mkdir(parents=True, exist_ok=True)
    if alerts:
        ts_str = n.astimezone(_zone()).strftime("%Y-%m-%d %H:%M:%S %Z")
        try:
            with alerts_path.open("a", encoding="utf-8") as f:
                for a in alerts:
                    f.write(f"{ts_str} [{a.level}] run={current_mode} {a.name}: {a.reason}\n")
        except OSError:
            pass

        try:
            pending_path.write_text(
                render_pending_alerts_md(alerts, current_mode=current_mode, now=n, registry=registry),
                encoding="utf-8",
            )
        except OSError:
            pass

        fire_macos_notification(alerts)
    else:
        # Clear stale pending file so the next run doesn't re-announce.
        try:
            pending_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="connector_health.report.generated",
        source="script:connector_health_report",
        payload={
            "sessions_in_window": len(session_list),
            "current_sid": current_sid,
            "current_mode": current_mode,
            "alerts": [a.to_dict() for a in alerts],
            **({"log_capped_at": [m["ts"] for m in cap_markers]} if cap_markers else {}),
        },
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Mirrors the bash script's exit semantics.

    - 0 on success or no-records short-circuit.
    - 1 on a fatal error (rare).

    The runners append this output to the run log. When there is nothing to
    roll up but an older connector-health.md exists, that file was not
    rewritten and still shows whatever alerts it last held, so say so.
    """
    try:
        event = run(cleanup=True)
        if event is None:
            surface = paths.kb_dir(paths.data_dir()) / "connector-health.md"
            if surface.exists():
                print(
                    f"connector-health: no scheduled-run records in the last {DEFAULT_WINDOW_DAYS} days; "
                    "connector-health.md was NOT rewritten and may show stale alerts"
                )
            else:
                print("connector-health: no scheduled-run records yet")
            return 0
        n_alerts = len(event.payload.get("alerts", []))
        n_sessions = event.payload.get("sessions_in_window", 0)
        print(f"connector-health: {n_sessions} sessions in window, {n_alerts} alert(s)")
        return 0
    except Exception as e:  # pragma: no cover — defensive
        print(f"connector-health: fatal error: {type(e).__name__}: {e}")
        return 1
