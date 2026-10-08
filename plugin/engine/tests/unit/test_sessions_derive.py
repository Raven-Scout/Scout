"""Table-driven tests for scout.sessions.derive (spec §4.2, §4.3, §4.6 choice, §4.7 rules)."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.sessions.derive import (
    choose_pr,
    derive_state,
    fmt_ago,
    is_scout_run,
    repo_root,
    resolve_project_key,
    strip_worktree,
)
from scout.sessions.model import AgentSession, LastTurn, PRInfo, TranscriptInfo, WorktreeInfo

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
STALE = timedelta(days=3)
RUNNING = timedelta(seconds=120)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(**over: object) -> PRInfo:
    base = dict(
        number=98,
        repo="example-org/example-repo",
        url=None,
        state="OPEN",
        is_draft=False,
        review_decision="",
        review_requested=False,
        checks="passing",
        merge_state="CLEAN",
        fetched_at=_iso(NOW),
        stale=False,
        updated_at=_iso(NOW - timedelta(days=5)),
    )
    base.update(over)
    return PRInfo(**base)  # type: ignore[arg-type]


def _session(*, last_active: datetime = NOW - timedelta(hours=2), **over: object) -> AgentSession:
    base = dict(
        id="local_x",
        cli_session_id="u",
        title="t",
        title_source="auto",
        project_key="/r",
        group_name=None,
        cwd="/r",
        origin_cwd="/r",
        worktree=None,
        created_at=_iso(NOW - timedelta(days=9)),
        last_activity_at=_iso(last_active),
        model="claude-opus-5",
        effort="high",
        turns=3,
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
    base.update(over)
    return AgentSession(**base)  # type: ignore[arg-type]


def _question_transcript() -> TranscriptInfo:
    return TranscriptInfo(
        path="p",
        first_prompt="x",
        files_touched=[],
        tool_calls=1,
        last_turn=LastTurn(at=_iso(NOW), kind="question"),
        mtime_ns=1,
    )


# ----- project + scout-run -----------------------------------------------------


def test_strip_worktree() -> None:
    assert strip_worktree("/Users/alex/code/repo/.claude/worktrees/w1") == "/Users/alex/code/repo"
    assert strip_worktree("/Users/alex/code/repo/.claude/worktrees/w1/sub") == "/Users/alex/code/repo"
    assert strip_worktree("/Users/alex/code/repo") == "/Users/alex/code/repo"


def test_resolve_project_key_prefers_the_repo_root_then_stripping() -> None:
    assert resolve_project_key("/a/b/.claude/worktrees/w", toplevel=lambda p: "/a/b") == "/a/b"
    assert resolve_project_key("/a/b/.claude/worktrees/w", toplevel=lambda p: None) == "/a/b"
    assert resolve_project_key("/plain", toplevel=lambda p: None) == "/plain"


def test_resolve_project_key_of_an_empty_cwd_never_asks_git() -> None:
    def boom(path: str) -> str | None:
        raise AssertionError(f"toplevel called for {path!r}")

    assert resolve_project_key("", toplevel=boom) == ""


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Alex",
            "-c",
            "user.email=alex@example.com",
            "-c",
            "init.defaultBranch=main",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=30,
    )


def test_repo_root_of_an_empty_or_relative_path_is_none() -> None:
    # Path("") is ".", which used to resolve the caller's own repo; a relative path would too.
    assert repo_root("") is None
    assert repo_root("code/example-repo") is None


def _repo(root: Path) -> Path:
    (root / ".git").mkdir(parents=True)
    return root.resolve()


def _linked_worktree(repo: Path, name: str, *, relative: bool = False) -> Path:
    """What `git worktree add` lays out: <wt>/.git names <repo>/.git/worktrees/<name>, whose commondir is ../.."""
    gitdir = repo / ".git" / "worktrees" / name
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    wt = repo / ".claude" / "worktrees" / name
    wt.mkdir(parents=True)
    target = os.path.relpath(gitdir, wt) if relative else str(gitdir)
    (wt / ".git").write_text(f"gitdir: {target}\n", encoding="utf-8")
    return wt


def test_repo_root_finds_the_folder_holding_dot_git(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "example-repo")
    (repo / "src" / "deep").mkdir(parents=True)
    assert repo_root(str(repo)) == str(repo)
    assert repo_root(str(repo / "src" / "deep")) == str(repo)


@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_repo_root_resolves_a_linked_worktree_to_its_main_repo(tmp_path: Path, relative: bool) -> None:
    repo = _repo(tmp_path / "example-repo")
    wt = _linked_worktree(repo, "w1", relative=relative)
    (wt / "sub").mkdir()
    assert repo_root(str(wt)) == str(repo)
    assert repo_root(str(wt / "sub")) == str(repo)


def test_repo_root_of_a_submodule_is_the_submodule_folder(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "example-repo")
    (repo / ".git" / "modules" / "vendored").mkdir(parents=True)  # a submodule's git dir has no commondir
    sub = repo / "vendored"
    sub.mkdir()
    (sub / ".git").write_text("gitdir: ../.git/modules/vendored\n", encoding="utf-8")
    assert repo_root(str(sub)) == str(sub)


def test_repo_root_of_a_worktree_of_a_submodule_is_that_worktree(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "example-repo")
    gitdir = repo / ".git" / "modules" / "vendored" / "worktrees" / "w2"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")  # → .git/modules/vendored, not named .git
    wt = tmp_path / "vendored-w2"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    assert repo_root(str(wt)) == str(wt.resolve())


def test_repo_root_of_a_deleted_folder_uses_its_nearest_existing_ancestor(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "example-repo")
    assert repo_root(str(repo / ".claude" / "worktrees" / "gone")) == str(repo)


def test_repo_root_outside_any_repo_is_none(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    assert repo_root(str(tmp_path / "plain")) is None


@pytest.mark.parametrize("dot_git", ["", "not a gitdir line\n", "gitdir:\n", "gitdir: /nowhere/at/all\n"])
def test_repo_root_skips_a_broken_dot_git_file_and_keeps_walking(tmp_path: Path, dot_git: str) -> None:
    repo = _repo(tmp_path / "example-repo")
    inner = repo / "inner"
    inner.mkdir()
    (inner / ".git").write_text(dot_git, encoding="utf-8")
    assert repo_root(str(inner)) == str(repo)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads a mode-000 file anyway")
def test_repo_root_skips_an_unreadable_dot_git_file_and_keeps_walking(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "example-repo")
    inner = repo / "inner"
    inner.mkdir()
    dot = inner / ".git"
    dot.write_text("gitdir: ../.git/modules/inner\n", encoding="utf-8")
    dot.chmod(0)
    try:
        assert repo_root(str(inner)) == str(repo)
    finally:
        dot.chmod(0o600)


def test_repo_root_follows_a_symlinked_project_folder(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "real" / "example-repo")
    link = tmp_path / "link-to-repo"
    link.symlink_to(repo, target_is_directory=True)
    assert repo_root(str(link)) == str(repo)  # the real path, as git reports it


def test_repo_root_never_raises_on_a_nul_byte(tmp_path: Path) -> None:
    assert repo_root(f"{tmp_path}/bad\x00name") is None


def _git_answer(path: Path) -> str | None:
    """What plan 1 asked git: the common dir's parent when it is <repo>/.git, else --show-toplevel."""

    def rev_parse(*args: str) -> str | None:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", *args], capture_output=True, text=True, check=False, timeout=30
        )
        return (proc.stdout.strip() or None) if proc.returncode == 0 else None

    common = rev_parse("--path-format=absolute", "--git-common-dir")
    if common is not None and common.endswith("/.git"):
        return str(Path(common).parent)
    return rev_parse("--show-toplevel")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs a git binary")
def test_repo_root_agrees_with_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    repo = tmp_path / "example-repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=repo)
    (repo / "sub").mkdir()
    worktree = repo / ".claude" / "worktrees" / "w1"
    _git("worktree", "add", "-q", "-b", "claude/w1", str(worktree), cwd=repo)
    relative = repo / ".claude" / "worktrees" / "w2"
    _git("worktree", "add", "-q", "-b", "claude/w2", str(relative), cwd=repo)
    gitfile = relative / ".git"
    target = gitfile.read_text(encoding="utf-8").split(":", 1)[1].strip()
    gitfile.write_text(f"gitdir: {os.path.relpath(target, relative)}\n", encoding="utf-8")  # as --relative-paths does
    lib = tmp_path / "example-lib"
    lib.mkdir()
    _git("init", "-q", cwd=lib)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=lib)
    _git("-c", "protocol.file.allow=always", "submodule", "--quiet", "add", str(lib), "vendored", cwd=repo)

    for p in (repo, repo / "sub", worktree, relative, repo / "vendored"):
        assert repo_root(str(p)) == _git_answer(p), p
    assert repo_root(str(worktree)) == str(repo.resolve())  # a worktree is its main repo, not itself


def test_is_scout_run_tolerates_a_nul_byte_in_a_recorded_path(tmp_path: Path) -> None:
    vault = tmp_path / "Scout"
    assert not is_scout_run(
        origin_cwd=f"{vault}\x00", title="scout-morning-briefing-20260908-1150", scheduled_task_id=None, vault=vault
    )


def test_is_scout_run_needs_vault_cwd_and_a_run_signature(tmp_path: Path) -> None:
    vault = tmp_path / "Scout"
    assert is_scout_run(
        origin_cwd=str(vault), title="scout-morning-briefing-20260908-1150", scheduled_task_id=None, vault=vault
    )
    assert is_scout_run(origin_cwd=str(vault), title="Scout research", scheduled_task_id="scout-research", vault=vault)
    assert not is_scout_run(origin_cwd=str(vault), title="Tidy the release notes", scheduled_task_id=None, vault=vault)
    assert not is_scout_run(
        origin_cwd="/elsewhere", title="scout-dreaming-20260908-1830", scheduled_task_id=None, vault=vault
    )


# ----- PR choice -------------------------------------------------------------------


def test_choose_pr_prefers_most_recently_updated_open() -> None:
    old_open = _pr(number=1, updated_at=_iso(NOW - timedelta(days=9)))
    new_open = _pr(number=2, updated_at=_iso(NOW - timedelta(days=1)))
    merged = _pr(number=3, state="MERGED", updated_at=_iso(NOW))
    assert choose_pr([old_open, merged, new_open]) is new_open
    assert choose_pr([merged]) is merged
    assert choose_pr([]) is None


# ----- state rules, first match wins -----------------------------------------------


@pytest.mark.parametrize(
    ("session", "expected_state", "expected_reason_fragment"),
    [
        pytest.param(
            _session(is_archived=True, is_open=True, last_active=NOW), "done", "archived", id="1-archived-wins"
        ),
        pytest.param(
            _session(is_open=True, last_active=NOW - timedelta(seconds=40)), "running", "active 40s ago", id="2-running"
        ),
        pytest.param(
            _session(is_open=True, last_active=NOW - timedelta(minutes=12)),
            "parked",
            "open, idle 12m",
            id="2b-open-idle-is-parked",
        ),
        pytest.param(_session(pr=_pr(state="MERGED")), "done", "PR #98 merged", id="3-merged"),
        pytest.param(_session(pr=_pr(state="CLOSED")), "done", "PR #98 closed", id="3-closed"),
        pytest.param(
            _session(pr=_pr(review_decision="CHANGES_REQUESTED")),
            "needs_you",
            "changes requested on PR #98",
            id="4-changes-requested",
        ),
        pytest.param(
            _session(pr=_pr(checks="failing", review_requested=True)), "needs_you", "CI failing", id="4-ci-failing"
        ),
        pytest.param(
            _session(pr=_pr(merge_state="DIRTY", review_requested=True)), "needs_you", "merge conflict", id="4-conflict"
        ),
        pytest.param(_session(transcript=_question_transcript()), "needs_you", "ended on a question", id="4-question"),
        pytest.param(_session(pr=_pr()), "needs_you", "PR #98 ready to merge", id="4-ready-no-review-requested"),
        pytest.param(
            _session(pr=_pr(review_decision="APPROVED", review_requested=True)),
            "needs_you",
            "PR #98 ready to merge",
            id="4-ready-approved",
        ),
        pytest.param(
            _session(pr=_pr(review_requested=True)), "waiting", "PR #98 awaiting review 5d", id="5-awaiting-review"
        ),
        pytest.param(
            _session(pr=_pr(review_decision="REVIEW_REQUIRED", review_requested=True)),
            "waiting",
            "awaiting review",
            id="5-review-required",
        ),
        pytest.param(_session(pr=_pr(checks="pending")), "waiting", "checks pending", id="5-checks-pending"),
        pytest.param(
            _session(pr=_pr(is_draft=True), last_active=NOW - timedelta(hours=1)),
            "parked",
            "draft PR #98",
            id="draft-falls-through",
        ),
        pytest.param(_session(last_active=NOW - timedelta(days=4)), "stale", "idle 4d", id="6-stale"),
        pytest.param(
            _session(
                last_active=NOW - timedelta(days=6),
                worktree=WorktreeInfo(path="/w", name="w", branch="b", source_branch="main", dirty=True),
            ),
            "stale",
            "dirty worktree, idle 6d",
            id="6-dirty-worktree",
        ),
        pytest.param(
            _session(pr=_pr(state="unknown", checks="unknown", merge_state="unknown", review_decision="unknown")),
            "parked",
            "PR #98 open (state unknown)",
            id="7-unknown-pr-parked",
        ),
        pytest.param(_session(), "parked", "last active 2h ago", id="7-parked-closed"),
    ],
)
def test_derive_state_table(session: AgentSession, expected_state: str, expected_reason_fragment: str) -> None:
    state, reasons = derive_state(session, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == expected_state, reasons
    assert any(expected_reason_fragment in r for r in reasons), reasons


def test_needs_you_lists_every_matched_signal() -> None:
    s = _session(pr=_pr(review_decision="CHANGES_REQUESTED", checks="failing"), transcript=_question_transcript())
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "needs_you"
    assert reasons == ["changes requested on PR #98", "CI failing", "ended on a question"]


def test_waiting_beats_stale_for_an_old_pr() -> None:
    s = _session(pr=_pr(review_requested=True), last_active=NOW - timedelta(days=10))
    assert derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)[0] == "waiting"


# §4.7: state_reasons lists every matched signal, not only the deciding rule's.


def test_running_also_lists_needs_you_signals() -> None:
    s = _session(is_open=True, last_active=NOW - timedelta(seconds=40), pr=_pr(review_decision="CHANGES_REQUESTED"))
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "running"
    assert reasons == ["active 40s ago", "changes requested on PR #98"]


def test_needs_you_also_lists_awaiting_review() -> None:
    s = _session(pr=_pr(checks="failing", review_requested=True))
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "needs_you"
    assert reasons == ["CI failing", "PR #98 awaiting review 5d"]


def test_waiting_also_lists_idle_past_the_stale_threshold() -> None:
    s = _session(pr=_pr(review_requested=True), last_active=NOW - timedelta(days=10))
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "waiting"
    assert reasons == ["PR #98 awaiting review 5d", "idle 10d"]


def test_needs_you_lists_waiting_and_stale_signals_too() -> None:
    s = _session(
        pr=_pr(merge_state="DIRTY", checks="pending", review_requested=True),
        last_active=NOW - timedelta(days=6),
        worktree=WorktreeInfo(path="/w", name="w", branch="b", source_branch="main", dirty=True),
    )
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "needs_you"
    assert reasons == ["merge conflict", "PR #98 awaiting review 5d", "checks pending", "dirty worktree, idle 6d"]


def test_a_draft_pr_adds_no_pr_signals_but_a_question_still_counts() -> None:
    s = _session(
        pr=_pr(is_draft=True, checks="failing", review_requested=True),
        transcript=_question_transcript(),
        last_active=NOW - timedelta(days=1),
    )
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "needs_you"
    assert reasons == ["ended on a question"]


def test_question_fresh_needs_you() -> None:
    s = _session(last_active=NOW - timedelta(days=1), transcript=_question_transcript())
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "needs_you"
    assert reasons == ["ended on a question"]


def test_question_stale_becomes_stale_signal() -> None:
    s = _session(last_active=NOW - timedelta(days=7), transcript=_question_transcript())
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "stale"
    assert reasons == ["idle 7d", "ended on a question"]


def test_question_stale_with_changes_requested_still_needs_you() -> None:
    s = _session(
        last_active=NOW - timedelta(days=7),
        pr=_pr(review_decision="CHANGES_REQUESTED"),
        transcript=_question_transcript(),
    )
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "needs_you"
    assert reasons == ["changes requested on PR #98", "idle 7d", "ended on a question"]


def test_question_at_stale_threshold_still_needs_you() -> None:
    s = _session(last_active=NOW - STALE, transcript=_question_transcript())
    state, reasons = derive_state(s, now=NOW, stale_after=STALE, running_window=RUNNING)
    assert state == "needs_you"
    assert reasons == ["ended on a question"]


def test_fmt_ago() -> None:
    assert fmt_ago(timedelta(seconds=40)) == "40s ago"
    assert fmt_ago(timedelta(minutes=12)) == "12m ago"
    assert fmt_ago(timedelta(hours=3, minutes=5)) == "3h ago"
    assert fmt_ago(timedelta(days=4, hours=2)) == "4d ago"
    assert fmt_ago(None) == "unknown"
