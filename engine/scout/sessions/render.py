"""Render the LLM-facing digest, ``cc-sessions.md`` (spec §4.9).

State buckets first (what needs the user, what is working, what waits, what
rotted), then the per-project activity list the consolidation narrative uses.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from scout.sessions.derive import fmt_ago
from scout.sessions.model import AgentSession, Index, parse_iso

DIGEST_FILENAME = "cc-sessions.md"

_BUCKETS: tuple[tuple[str, str], ...] = (
    ("needs_you", "Needs you"),
    ("running", "Running now"),
    ("waiting", "Waiting on others"),
    ("stale", "Stale"),
)


def _recency(s: AgentSession) -> float:
    dt = parse_iso(s.last_activity_at)
    return dt.timestamp() if dt else 0.0


def _ago(s: AgentSession, now: datetime) -> str:
    dt = parse_iso(s.last_activity_at)
    return fmt_ago(now - dt) if dt else "unknown"


def _bucket_line(s: AgentSession, project_name: str, now: datetime) -> str:
    parts = [f"**{s.title or '(untitled)'}**", project_name, "; ".join(s.state_reasons) or s.state]
    if s.pr is not None and s.pr.url:
        parts.append(s.pr.url)
    parts.append(f"last active {_ago(s, now)}")
    return "- " + " — ".join(parts)


def _local(s: AgentSession, tz: ZoneInfo) -> str:
    dt = parse_iso(s.last_activity_at)
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z") if dt else "unknown"


def render_digest(
    index: Index, *, now: datetime, tz: ZoneInfo, hours: int, instance_name: str, max_per_bucket: int
) -> str:
    names = {p.key: p.name for p in index.projects}
    visible = [s for s in index.sessions if not s.is_scout_run and not s.is_archived]
    counts = {state: sum(1 for s in visible if s.state == state) for state, _ in _BUCKETS}

    out: list[str] = [
        "# Claude Code Sessions — state digest",
        (
            f"Generated {now.astimezone(tz).strftime('%Y-%m-%d %H:%M %Z')} · {len(visible)} sessions"
            f" · {counts['running']} running · {counts['needs_you']} need you · {counts['waiting']} waiting"
            f" · {counts['stale']} stale · {instance_name}'s own runs and archived sessions excluded"
        ),
        "",
    ]
    for state, heading in _BUCKETS:
        members = sorted((s for s in visible if s.state == state), key=_recency, reverse=True)
        out.append(f"## {heading} ({len(members)})")
        if not members:
            out.append("_none_")
        for s in members[:max_per_bucket]:
            out.append(_bucket_line(s, names.get(s.project_key, s.project_key), now))
        if len(members) > max_per_bucket:
            out.append(f"_…and {len(members) - max_per_bucket} more_")
        out.append("")

    cutoff = now.timestamp() - hours * 3600
    active = [s for s in visible if s.transcript is not None and _recency(s) >= cutoff]
    out.append(f"## Activity — last {hours}h, by project")
    out.append("")
    by_project: dict[str, list[AgentSession]] = {}
    for s in active:
        by_project.setdefault(s.project_key, []).append(s)
    for key in sorted(by_project, key=lambda k: names.get(k, k).lower()):
        out.append(f"### {names.get(key, key)}")
        out.append("")
        for s in sorted(by_project[key], key=_recency, reverse=True):
            assert s.transcript is not None
            files = "\n".join(f"- {p}" for p in s.transcript.files_touched) or "- (none detected)"
            out.extend(
                [
                    f"#### {s.title or '(untitled)'}",
                    f"**Last active:** {_local(s, tz)} | **State:** {s.state} | **ID:** `{s.id}`",
                    "",
                    "**First message/context:**",
                    f"> {s.transcript.first_prompt}",
                    "",
                    "**Files touched:**",
                    files,
                    "",
                ]
            )
    if not active:
        out.append(f"*No non-{instance_name} Claude Code sessions with transcripts in the last {hours} hours.*")
        out.append("")
    out.append(f"**Total:** {len(active)} session(s) active in the last {hours}h.")
    return "\n".join(out) + "\n"


__all__ = ["DIGEST_FILENAME", "render_digest"]
