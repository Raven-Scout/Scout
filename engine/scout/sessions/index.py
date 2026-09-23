"""Build and write the session index (spec §3, §4.1–§4.4, §4.8, §4.12).

Every loader is independent; a source that is missing is silent, a source
that is malformed contributes a ``source_error`` and the run continues. The
index and both caches are written atomically.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout import paths
from scout.sessions import cli_home, desktop, github
from scout.sessions.derive import choose_pr, derive_state, git_toplevel, is_scout_run, resolve_project_key
from scout.sessions.model import (
    STATES,
    AgentSession,
    Index,
    PRInfo,
    Project,
    SourceError,
    WorktreeInfo,
    dt_to_iso,
    ms_to_iso,
    now_utc,
    ns_to_iso,
    parse_iso,
)
from scout.sessions.settings import AgentSessionsSettings, load_settings
from scout.sessions.transcript import (
    TRANSCRIPT_CACHE_FILENAME,
    load_transcript_cache,
    transcript_info,
    write_transcript_cache,
)

INDEX_FILENAME = "sessions-index.json"
LEGACY_CACHE_FILENAME = "cc-sessions.cache.json"  # pre-plan-1 cache; deleted on first run
_CUSTOM_TITLE_HEAD_LINES = 5


@dataclass
class BuildOptions:
    data_dir: Path
    settings: AgentSessionsSettings
    claude_home: Path
    support_dir: Path
    now: datetime
    use_gh: bool = True
    gh_runner: github.Runner = github.default_runner
    gh_available: Callable[[], bool] = github.gh_available
    toplevel: Callable[[str], str | None] = git_toplevel
    pid_alive: Callable[[int], bool] = cli_home.pid_alive


def default_options(
    data_dir: Path | None = None,
    *,
    settings: AgentSessionsSettings | None = None,
    now: datetime | None = None,
    use_gh: bool = True,
) -> BuildOptions:
    d = data_dir or paths.data_dir()
    s = settings or load_settings(d)
    home = Path(s.claude_home).expanduser() if s.claude_home else cli_home.default_claude_home()
    support = Path(s.desktop_support_dir).expanduser() if s.desktop_support_dir else desktop.default_support_dir()
    return BuildOptions(
        data_dir=d, settings=s, claude_home=home, support_dir=support, now=now or now_utc(), use_gh=use_gh and s.use_gh
    )


def index_path(data_dir: Path | None = None) -> Path:
    return paths.cache_dir(data_dir) / INDEX_FILENAME


# ----- session construction -----------------------------------------------------------


def _worktree(rec: desktop.DesktopRecord, lease: desktop.WorktreeLease | None) -> WorktreeInfo | None:
    path = rec.worktree_path or (lease.path if lease else None)
    branch = rec.branch or (lease.branch if lease else None)
    if path is None and branch is None:
        return None
    return WorktreeInfo(
        path=path,
        name=rec.worktree_name or (Path(path).name if path else None),
        branch=branch,
        source_branch=rec.source_branch or (lease.source_branch if lease else None),
        dirty=rec.kept_dirty_worktree,
    )


def _session_from_record(
    rec: desktop.DesktopRecord, *, groups: desktop.Groups, leases: dict[str, desktop.WorktreeLease]
) -> AgentSession:
    group_id = groups.assignments.get(rec.session_id)
    group_name = groups.names.get(group_id) if group_id else None
    return AgentSession(
        id=rec.session_id,
        cli_session_id=rec.cli_session_id,
        title=rec.title,
        title_source=rec.title_source,
        project_key=rec.origin_cwd,  # refined in build_index
        group_name=group_name,
        cwd=rec.cwd,
        origin_cwd=rec.origin_cwd or rec.cwd,
        worktree=_worktree(rec, leases.get(rec.session_id)),
        created_at=ms_to_iso(rec.created_at_ms),
        last_activity_at=ms_to_iso(rec.last_activity_at_ms),
        model=rec.model,
        effort=rec.effort,
        turns=rec.completed_turns,
        is_archived=rec.is_archived or group_name == "Archived",
        is_open=False,
        is_scout_run=False,
        parent_session_id=rec.parent_session_id,
        spawned_task_id=rec.spawned_task_id,
        scheduled_task_id=rec.scheduled_task_id,
        prs=[],
        pr=None,
        transcript=None,
    )


def _custom_title(path: Path) -> str | None:
    """Scout's ``claude -p`` runs write a ``custom-title`` row first; read only the head."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= _CUSTOM_TITLE_HEAD_LINES:
                    break
                if '"custom-title"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                title = obj.get("customTitle") if isinstance(obj, dict) else None
                if isinstance(title, str) and title:
                    return title
    except OSError:
        return None
    return None


def _encode_dirname(path: str) -> str:
    """Claude Code's project-dir encoding: every ``/`` and ``.`` becomes ``-``."""
    return re.sub(r"[/.]", "-", path)


def _cli_only_session(uuid: str, path: Path, st: os.stat_result, known_dirs: dict[str, str]) -> AgentSession:
    # Exact match against directories we know (the vault, every desktop cwd) beats
    # the lossy decode — hyphens and dots in real folder names round-trip only this way.
    cwd = known_dirs.get(path.parent.name) or cli_home.project_path_from_dirname(path.parent.name)
    title = _custom_title(path)
    return AgentSession(
        id=f"cli:{uuid}",
        cli_session_id=uuid,
        title=title,
        title_source="custom" if title else None,
        project_key=cwd,
        group_name=None,
        cwd=cwd,
        origin_cwd=cwd,
        worktree=None,
        created_at=None,
        last_activity_at=ns_to_iso(st.st_mtime_ns),
        model=None,
        effort=None,
        turns=None,
        is_archived=False,
        is_open=False,
        is_scout_run=False,
        parent_session_id=None,
        spawned_task_id=None,
        scheduled_task_id=None,
        prs=[],
        pr=None,
        transcript=None,
    )


def _projects(sessions: list[AgentSession], groups: desktop.Groups) -> list[Project]:
    name_to_id = {name: gid for gid, name in groups.names.items()}
    buckets: dict[str, list[AgentSession]] = {}
    for s in sessions:
        buckets.setdefault(s.project_key, []).append(s)
    out: list[Project] = []
    for key, members in buckets.items():
        named = Counter(m.group_name for m in members if m.group_name and m.group_name != "Archived")
        name = named.most_common(1)[0][0] if named else (Path(key).name or key)
        counts = {state: 0 for state in STATES}
        for m in members:
            counts[m.state] = counts.get(m.state, 0) + 1
        out.append(Project(key=key, name=name, group_id=name_to_id.get(name), counts=counts))
    out.sort(key=lambda p: p.name.lower())
    return out


def _recency(s: AgentSession) -> float:
    dt = parse_iso(s.last_activity_at)
    return dt.timestamp() if dt else 0.0


# ----- build ---------------------------------------------------------------------------


def build_index(opts: BuildOptions) -> Index:
    s = opts.settings
    errors: list[SourceError] = []
    records, e1 = desktop.load_desktop_records(opts.support_dir)
    groups, e2 = desktop.load_groups(opts.support_dir)
    leases, e3 = desktop.load_worktree_leases(opts.support_dir)
    live, e4 = cli_home.load_live_processes(opts.claude_home, is_alive=opts.pid_alive)
    errors.extend([*e1, *e2, *e3, *e4])
    tpaths = cli_home.transcript_paths(opts.claude_home)
    cache_dir = paths.cache_dir(opts.data_dir)
    tcache = load_transcript_cache(cache_dir / TRANSCRIPT_CACHE_FILENAME)
    window = timedelta(days=s.transcript_window_days)

    # 1. Desktop records → sessions; forks sharing a cliSessionId dedupe to the most recent.
    by_cli: dict[str, desktop.DesktopRecord] = {}
    without_cli: list[desktop.DesktopRecord] = []
    for rec in records:
        if rec.cli_session_id is None:
            without_cli.append(rec)
            continue
        prev = by_cli.get(rec.cli_session_id)
        if prev is None or (rec.last_activity_at_ms or 0) > (prev.last_activity_at_ms or 0):
            by_cli[rec.cli_session_id] = rec
    sessions: list[AgentSession] = []
    refs_by_session: dict[str, list[desktop.PRRef]] = {}
    no_transcript: set[str] = set()
    for rec in [*by_cli.values(), *without_cli]:
        sess = _session_from_record(rec, groups=groups, leases=leases)
        sessions.append(sess)
        refs_by_session[sess.id] = list(rec.prs)
        if rec.transcript_unavailable:
            no_transcript.add(sess.id)

    # 2. CLI-only sessions (transcript, no desktop record) within the transcript window.
    known_dirs = {_encode_dirname(str(opts.data_dir)): str(opts.data_dir)}
    for rec in records:
        for p in (rec.cwd, rec.origin_cwd):
            if p:
                known_dirs.setdefault(_encode_dirname(p), p)
    cli_only = 0
    for uuid, path in tpaths.items():
        if uuid in by_cli:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        if opts.now - datetime.fromtimestamp(st.st_mtime_ns / 1e9, tz=UTC) > window:
            continue
        sessions.append(_cli_only_session(uuid, path, st, known_dirs))
        cli_only += 1

    # 3. Transcript facts, liveness, last activity.
    for sess in sessions:
        uuid = sess.cli_session_id
        path = tpaths.get(uuid) if uuid else None
        if path is not None:
            try:
                st = path.stat()
            except OSError:
                st = None
            if st is not None:
                mtime_iso = ns_to_iso(st.st_mtime_ns)
                if sess.last_activity_at is None or mtime_iso > sess.last_activity_at:
                    sess.last_activity_at = mtime_iso
                last = parse_iso(sess.last_activity_at)
                if sess.id not in no_transcript and last is not None and opts.now - last <= window:
                    try:
                        sess.transcript = transcript_info(path, cache=tcache)
                    except OSError as exc:
                        errors.append(SourceError(source="transcript", message=f"{path.name}: {exc}"))
        if uuid is not None and uuid in live:
            sess.is_open = True

    # 4. PR state.
    all_refs = [ref for refs in refs_by_session.values() for ref in refs]
    pr_cache = github.load_pr_cache(cache_dir / github.PR_CACHE_FILENAME)
    fetched = 0
    resolved: dict[str, PRInfo] = {}
    if all_refs:
        if opts.use_gh and opts.gh_available():
            resolved, e5, fetched = github.refresh_pr_states(
                all_refs,
                cache=pr_cache,
                now=opts.now,
                ttl=timedelta(minutes=s.pr_refresh_minutes),
                cap=s.pr_fetch_cap,
                runner=opts.gh_runner,
            )
            errors.extend(e5)
        else:
            if opts.use_gh:
                errors.append(SourceError(source="gh", message="gh not found on PATH — PR states unknown"))
            # cap=0: serve cached / legacy-terminal / unknown without calling anything.
            resolved, _, fetched = github.refresh_pr_states(
                all_refs, cache=pr_cache, now=opts.now, ttl=timedelta(days=36500), cap=0, runner=lambda argv: None
            )
    for sess in sessions:
        sess.prs = [
            resolved[f"{r.repo}#{r.number}"]
            for r in refs_by_session.get(sess.id, [])
            if f"{r.repo}#{r.number}" in resolved
        ]
        sess.pr = choose_pr(sess.prs)

    # 5. Project key, Scout-run flag, state.
    stale_after = timedelta(days=s.stale_after_days)
    running_window = timedelta(seconds=s.running_window_seconds)
    for sess in sessions:
        sess.project_key = resolve_project_key(sess.origin_cwd, toplevel=opts.toplevel)
        sess.is_scout_run = is_scout_run(
            origin_cwd=sess.origin_cwd, title=sess.title, scheduled_task_id=sess.scheduled_task_id, vault=opts.data_dir
        )
        sess.state, sess.state_reasons = derive_state(
            sess, now=opts.now, stale_after=stale_after, running_window=running_window
        )

    sessions.sort(key=lambda x: (STATES.index(x.state), -_recency(x)))
    write_transcript_cache(cache_dir / TRANSCRIPT_CACHE_FILENAME, tcache)
    github.write_pr_cache(cache_dir / github.PR_CACHE_FILENAME, pr_cache)

    return Index(
        generated_at=dt_to_iso(opts.now),
        source_counts={
            "desktop": len(records),
            "cli_only": cli_only,
            "open": sum(1 for x in sessions if x.is_open),
            "running": sum(1 for x in sessions if x.state == "running"),
            "prs_refreshed": fetched,
        },
        source_errors=errors,
        display={"done_visible_hours": s.done_visible_hours, "stale_after_days": s.stale_after_days},
        projects=_projects(sessions, groups),
        sessions=sessions,
    )


# ----- write / run -----------------------------------------------------------------------


def write_index(index: Index, path: Path) -> None:
    """Atomic replace. Raises OSError when the target cannot be written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sessions-index.", suffix=".json.tmp", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(index.to_dict(), f, indent=1)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def run(
    *,
    data_dir: Path | None = None,
    use_gh: bool = True,
    now: datetime | None = None,
    opts: BuildOptions | None = None,
    render: bool = False,
    hours: int = 24,
    instance_name: str = "Scout",
    tz_name: str | None = None,
) -> tuple[Index, Path]:
    """Build the index and write it (plus both caches); optionally render the digest."""
    o = opts or default_options(data_dir, now=now, use_gh=use_gh)
    index = build_index(o)
    legacy = paths.cache_dir(o.data_dir) / LEGACY_CACHE_FILENAME
    if legacy.exists():
        try:
            legacy.unlink()
        except OSError:
            pass
    path = index_path(o.data_dir)
    write_index(index, path)
    if render:
        from scout import config as scout_config
        from scout.sessions.render import DIGEST_FILENAME, render_digest

        tz = scout_config.timezone_or_default(tz_name) if tz_name else scout_config.resolve_timezone(o.data_dir)
        digest = render_digest(
            index,
            now=o.now,
            tz=tz,
            hours=hours,
            instance_name=instance_name,
            max_per_bucket=o.settings.render_max_per_bucket,
        )
        (paths.cache_dir(o.data_dir) / DIGEST_FILENAME).write_text(digest, encoding="utf-8")
    return index, path


# ----- CLI entry points ----------------------------------------------------------------


def main(
    *,
    json_out: bool = False,
    render: bool = False,
    use_gh: bool = True,
    hours: int = 24,
    instance_name: str = "Scout",
    tz_name: str | None = None,
    strict: bool = False,
) -> int:
    """`scoutctl session index`. Exit 0 on success (even partial), 1 when the index
    cannot be written or `--strict` sees a source error."""
    import sys

    try:
        index, path = run(use_gh=use_gh, render=render, hours=hours, instance_name=instance_name, tz_name=tz_name)
    except OSError as exc:
        print(f"session index: could not write the index: {exc}", file=sys.stderr)
        return 1
    if json_out:
        print(json.dumps(index.to_dict(), indent=1))
    else:
        counts = index.source_counts
        print(
            f"session index: {path} — {len(index.sessions)} sessions"
            f" ({counts.get('running', 0)} running, {counts.get('open', 0)} open,"
            f" {counts.get('prs_refreshed', 0)} PRs refreshed, {len(index.source_errors)} source errors)"
        )
    if strict and index.source_errors:
        for err in index.source_errors:
            print(f"session index: [{err.source}] {err.message}", file=sys.stderr)
        return 1
    return 0


def _load_index_dict(data_dir: Path | None = None) -> dict:
    path = index_path(data_dir)
    if not path.exists():
        run(use_gh=False)
    return json.loads(path.read_text(encoding="utf-8"))


def list_main(
    *,
    states: list[str],
    project: str | None,
    include_archived: bool,
    include_scout_runs: bool,
    json_out: bool,
) -> int:
    """`scoutctl session list` — a human table (or JSON rows) from the written index."""
    import sys

    try:
        payload = _load_index_dict()
    except (OSError, json.JSONDecodeError) as exc:
        print(f"session list: could not read the index: {exc}", file=sys.stderr)
        return 1
    names = {p["key"]: p["name"] for p in payload.get("projects", [])}
    rows = []
    for s in payload.get("sessions", []):
        if s["is_archived"] and not include_archived:
            continue
        if s["is_scout_run"] and not include_scout_runs:
            continue
        if states and s["state"] not in states:
            continue
        pname = names.get(s["project_key"], s["project_key"])
        if project and project.lower() not in (
            pname.lower(),
            s["project_key"].lower(),
            Path(s["project_key"]).name.lower(),
        ):
            continue
        rows.append({**s, "project_name": pname})
    if json_out:
        print(json.dumps(rows, indent=1))
        return 0
    if not rows:
        print("no sessions match")
        return 0
    print("STATE      PROJECT               TITLE                                     PR      LAST ACTIVE")
    for s in rows:
        pr = f"#{s['pr']['number']}" if s.get("pr") else ""
        title = (s.get("title") or (s.get("transcript") or {}).get("first_prompt") or "(untitled)")[:41]
        print(f"{s['state']:<10} {s['project_name'][:21]:<21} {title:<41} {pr:<7} {s.get('last_activity_at') or ''}")
    return 0


__all__ = [
    "INDEX_FILENAME",
    "BuildOptions",
    "build_index",
    "default_options",
    "index_path",
    "list_main",
    "main",
    "run",
    "write_index",
]
