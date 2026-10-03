"""Golden test for the cc-sessions.md digest."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scout.sessions.model import AgentSession, Index, LastTurn, PRInfo, Project, TranscriptInfo
from scout.sessions.render import DIGEST_FILENAME, render_digest

GOLDEN = Path(__file__).parent.parent / "fixtures" / "sessions" / "digest-golden.md"
NOW = datetime(2026, 9, 8, 16, 0, tzinfo=UTC)  # 12:00 EDT


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _s(
    id_: str,
    title: str | None,
    state: str,
    reasons: list[str],
    *,
    hours_ago: float,
    project: str = "/Users/alex/code/example-repo",
    group: str | None = "Example Repo",
    pr: PRInfo | None = None,
    prompt: str | None = None,
    files: list[str] | None = None,
    scout: bool = False,
    archived: bool = False,
) -> AgentSession:
    tr = None
    if prompt is not None:
        tr = TranscriptInfo(
            path="p",
            first_prompt=prompt,
            files_touched=files or [],
            tool_calls=1,
            last_turn=LastTurn(at=_iso(NOW), kind="end_turn"),
            mtime_ns=1,
        )
    return AgentSession(
        id=id_,
        cli_session_id="u",
        title=title,
        title_source="auto",
        project_key=project,
        group_name=group,
        cwd=project,
        origin_cwd=project,
        worktree=None,
        created_at=None,
        last_activity_at=_iso(NOW - timedelta(hours=hours_ago)),
        model="claude-opus-5",
        effort="high",
        turns=2,
        is_archived=archived,
        is_open=state == "running",
        is_scout_run=scout,
        parent_session_id=None,
        spawned_task_id=None,
        scheduled_task_id=None,
        prs=[pr] if pr else [],
        pr=pr,
        transcript=tr,
        state=state,
        state_reasons=reasons,
    )


def _pr(number: int, **over: object) -> PRInfo:
    base = dict(
        number=number,
        repo="example-org/example-repo",
        url=f"https://github.com/example-org/example-repo/pull/{number}",
        state="OPEN",
        is_draft=False,
        review_decision="",
        review_requested=True,
        checks="passing",
        merge_state="CLEAN",
        fetched_at=_iso(NOW),
        stale=False,
        updated_at=_iso(NOW - timedelta(days=5)),
    )
    base.update(over)
    return PRInfo(**base)  # type: ignore[arg-type]


def _index() -> Index:
    sessions = [
        _s(
            "a",
            "Fix the parser",
            "needs_you",
            ["changes requested on PR #98"],
            hours_ago=1,
            pr=_pr(98, review_decision="CHANGES_REQUESTED", review_requested=False),
            prompt="please fix the parser",
            files=["~/code/example-repo/a.py"],
        ),
        _s("b", "Ship the cache", "running", ["active 30s ago"], hours_ago=0, prompt="ship it"),
        _s("c", "Docs pass", "waiting", ["PR #102 awaiting review 5d"], hours_ago=30, pr=_pr(102)),
        _s(
            "d",
            "Old spike",
            "stale",
            ["dirty worktree, idle 6d"],
            hours_ago=6 * 24,
            project="/Users/alex/code/other",
            group=None,
        ),
        _s(
            "e",
            "scout-morning-briefing-20260908-0800",
            "parked",
            ["last active 4h ago"],
            hours_ago=4,
            project="/Users/alex/Scout",
            group=None,
            scout=True,
            prompt="You are Scout",
        ),
        _s("f", "Archived thing", "done", ["archived"], hours_ago=2, archived=True),
    ]
    projects = [
        Project(key="/Users/alex/code/example-repo", name="Example Repo", group_id="cg-1", counts={}),
        Project(key="/Users/alex/code/other", name="other", group_id=None, counts={}),
        Project(key="/Users/alex/Scout", name="Scout", group_id=None, counts={}),
    ]
    return Index(
        generated_at=_iso(NOW), source_counts={}, source_errors=[], display={}, projects=projects, sessions=sessions
    )


def test_digest_matches_golden() -> None:
    out = render_digest(
        _index(), now=NOW, tz=ZoneInfo("America/New_York"), hours=24, instance_name="Scout", max_per_bucket=15
    )
    assert DIGEST_FILENAME == "cc-sessions.md"
    assert out == GOLDEN.read_text(encoding="utf-8"), (
        "run with UPDATE_GOLDEN=1 to regenerate after an intentional change"
    )


def test_digest_caps_buckets_and_reports_overflow() -> None:
    idx = _index()
    idx.sessions = [_s(f"n{i}", f"Needs {i}", "needs_you", ["CI failing"], hours_ago=i) for i in range(4)]
    out = render_digest(idx, now=NOW, tz=ZoneInfo("UTC"), hours=24, instance_name="Scout", max_per_bucket=2)
    assert "## Needs you (4)" in out and "Needs 0" in out and "Needs 1" in out and "Needs 2" not in out
    assert "_…and 2 more_" in out


def test_untitled_session_renders_its_first_prompt() -> None:
    """Spec §4.1: no title → the first prompt's first line (trimmed, max 80 chars + …); (untitled) only
    when there is no transcript either. Same text in the bucket line and the activity heading."""
    idx = _index()
    idx.sessions = [
        _s(
            "u1",
            None,
            "needs_you",
            ["ended on a question"],
            hours_ago=1,
            prompt=(
                "  Why does the parser drop blank rows when the file ends without a newline at the very end  "
                "\nsecond line of the prompt"
            ),
        ),
        _s("u2", None, "waiting", ["checks pending"], hours_ago=2, prompt="ship it"),
        _s("u3", None, "stale", ["idle 5d"], hours_ago=5 * 24),
    ]
    out = render_digest(idx, now=NOW, tz=ZoneInfo("UTC"), hours=24, instance_name="Scout", max_per_bucket=15)
    long_title = "Why does the parser drop blank rows when the file ends without a newline at the…"  # 80 + …
    assert f"- **{long_title}** — Example Repo — ended on a question — last active 1h ago" in out
    assert f"#### {long_title}\n" in out
    assert "- **ship it** — Example Repo — checks pending — last active 2h ago" in out and "#### ship it\n" in out
    assert "- **(untitled)** — Example Repo — idle 5d — last active 5d ago" in out
    assert "second line of the prompt" not in out.split("**First message/context:**")[0]
