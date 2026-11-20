"""The dreaming fact-check pass as a driver output (`fact_check_targets`) and a pre-commit check
(`scoutctl kb check-fact-check`), modelled on `action-items check-demotion` (scout/kb/fact_check.py)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.kb.fact_check import (
    MAX_TARGETS,
    REQUIRED,
    Target,
    check_fact_check,
    fact_check_targets,
    judge,
)
from scout.scripts import pre_session_data

runner = CliRunner()
DAY = dt.date(2026, 10, 10)
P = "knowledge-base/projects"
FRONT = f"{P}/widgets/widgets.md"
TOPIC = "knowledge-base/topics/infra/infra-costs.md"
SOURCE = "knowledge-base/sources/2026-10/2026-10-09-sync.md"


def commit_at(kb_repo, day: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_COMMITTER_DATE", f"{day}T12:00:00+00:00")
    monkeypatch.setenv("GIT_AUTHOR_DATE", f"{day}T12:00:00+00:00")
    kb_repo.git("add", "-A")
    kb_repo.commit(f"c {day}")


def note(body: str = "A claim.\n", *, verified: str | None = None, private: bool = False) -> str:
    fm = "visibility: private\n" if private else ""
    if verified:
        fm += f"last_verified: {verified}\nverified_against: ['tracker PROJ-1234']\n"
    return f"---\n{fm}---\n{body}" if fm else body


def paths(targets: list[Target]) -> list[str]:
    return [t.path for t in targets]


# ----- the targets -----------------------------------------------------------------------


def test_recent_changes_not_reverified_are_targets_most_consequential_first(kb_repo, monkeypatch) -> None:
    kb_repo.write(SOURCE, note())
    kb_repo.write(TOPIC, note())
    kb_repo.write(FRONT, note())
    kb_repo.write("knowledge-base/people/priya.md", note())
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    targets = fact_check_targets(kb_repo.root, DAY, "owner")
    assert paths(targets) == [FRONT, "knowledge-base/people/priya.md", TOPIC, SOURCE]
    assert targets[0].changed == "2026-10-09" and targets[0].visibility == "normal"


def test_older_changes_and_already_reverified_notes_are_not_targets(kb_repo, monkeypatch) -> None:
    kb_repo.write(TOPIC, note())
    commit_at(kb_repo, "2026-10-01", monkeypatch)  # outside the 3-day window
    kb_repo.write(FRONT, note(verified="2026-10-09"))  # re-verified on the day it changed
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    assert fact_check_targets(kb_repo.root, DAY, "owner") == []


def test_records_and_archive_copies_are_not_claims(kb_repo, monkeypatch) -> None:
    kb_repo.write("knowledge-base/session-log/2026-10.md", "| row |\n")
    kb_repo.write(f"{P}/widgets/archive/widgets-2026-10-01-pre-restructure.md", "old\n")
    kb_repo.write("knowledge-base/ontology/schema.yaml", "x: 1\n")
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    assert fact_check_targets(kb_repo.root, DAY, "owner") == []


def test_a_private_target_is_labelled(kb_repo, monkeypatch) -> None:
    kb_repo.write(FRONT, note(private=True))
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    [t] = fact_check_targets(kb_repo.root, DAY, "owner")
    assert t.visibility == "private"


def test_an_agent_reader_gets_no_targets(kb_repo, monkeypatch) -> None:
    kb_repo.write(FRONT, note())
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    assert fact_check_targets(kb_repo.root, DAY, "agent:helper") == []


def test_targets_are_capped(kb_repo, monkeypatch) -> None:
    for i in range(MAX_TARGETS + 3):
        kb_repo.write(f"knowledge-base/topics/infra/note-{i:02}.md", note())
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    assert len(fact_check_targets(kb_repo.root, DAY, "owner")) == MAX_TARGETS


def test_the_runner_writes_them_into_the_session_context(kb_repo, monkeypatch) -> None:
    kb_repo.write(FRONT, note())
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    rows = pre_session_data.get_fact_check_targets(kb_repo.root, DAY, "owner")
    assert rows == [{"path": FRONT, "changed": "2026-10-09", "visibility": "normal"}]
    assert "fact_check_targets" in pre_session_data.SessionContext.__dataclass_fields__


def test_the_runner_writes_none_when_it_fails(kb_repo, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("x")

    monkeypatch.setattr("scout.kb.fact_check.fact_check_targets", boom)
    assert pre_session_data.get_fact_check_targets(kb_repo.root, DAY, "owner") is None


# ----- the check -------------------------------------------------------------------------


def targets(*rels: str) -> list[Target]:
    return [Target(r, "2026-10-09", "normal") for r in rels]


def test_judge_needs_two_reverified_today() -> None:
    today = DAY
    ok = note(verified="2026-10-10")
    stale = note(verified="2026-10-09")
    assert REQUIRED == 2
    assert judge(targets("a", "b", "c"), {"a": ok, "b": ok, "c": stale}, today).passed
    assert not judge(targets("a", "b", "c"), {"a": ok, "b": stale, "c": stale}, today).passed


def test_judge_needs_all_when_fewer_than_two() -> None:
    assert judge(targets("a"), {"a": note(verified="2026-10-10")}, DAY).passed
    assert not judge(targets("a"), {"a": note()}, DAY).passed
    assert judge([], {}, DAY).passed


def test_a_bumped_date_without_sources_does_not_count() -> None:
    bare = "---\nlast_verified: 2026-10-10\n---\nA claim.\n"
    assert not judge(targets("a"), {"a": bare}, DAY).passed


def _context(kb_repo, rows: list[dict] | None, day: str = "2026-10-10") -> Path:
    path = kb_repo.root / ".scout-cache" / "session-context.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated_at": f"{day}T02:00:00", "fact_check_targets": rows}))
    return path


ROWS = [{"path": FRONT, "changed": "2026-10-09", "visibility": "normal"},
        {"path": TOPIC, "changed": "2026-10-09", "visibility": "private"}]  # fmt: skip


def _cli(kb_repo, *extra: str):
    return runner.invoke(
        cli.app, ["kb", "check-fact-check", "--date", "2026-10-10", "--data-dir", str(kb_repo.root), *extra]
    )


def test_cli_exit_1_lists_the_targets_and_marks_private_ones(kb_repo) -> None:
    kb_repo.write(FRONT, note())
    kb_repo.write(TOPIC, note(private=True))
    _context(kb_repo, ROWS)
    result = _cli(kb_repo)
    assert result.exit_code == 1
    assert f"· {FRONT} (changed 2026-10-09)" in result.stdout
    assert f"· [private] {TOPIC}" in result.stdout
    assert '--skipped "<why>"' in result.stdout


def test_cli_passes_once_the_run_reverified_them(kb_repo) -> None:
    kb_repo.write(FRONT, note(verified="2026-10-10"))
    kb_repo.write(TOPIC, note(verified="2026-10-10"))
    _context(kb_repo, ROWS)
    result = _cli(kb_repo)
    assert result.exit_code == 0 and "2 of 2" in result.stdout
    data = json.loads(_cli(kb_repo, "--json").stdout)
    assert data["passed"] and data["required"] == 2 and sorted(data["verified"]) == sorted([FRONT, TOPIC])


def test_cli_accepts_a_run_that_says_why_it_skipped(kb_repo) -> None:
    kb_repo.write(FRONT, note())
    kb_repo.write(TOPIC, note())
    _context(kb_repo, ROWS)
    result = _cli(kb_repo, "--skipped", "the tracker was unreachable all run")
    assert result.exit_code == 0
    assert "`fact-check skipped: the tracker was unreachable all run`" in result.stdout


@pytest.mark.parametrize("reason", ["", "  ", "x", "n/a"])
def test_cli_refuses_a_skip_without_a_reason(kb_repo, reason: str) -> None:
    _context(kb_repo, ROWS)
    assert _cli(kb_repo, "--skipped", reason).exit_code == 2


def test_an_earlier_runs_session_log_row_no_longer_counts(kb_repo) -> None:
    kb_repo.write(FRONT, note())
    kb_repo.write(TOPIC, note())
    kb_repo.write(
        "knowledge-base/session-log/2026-10.md",
        "| 2026-10-10 | consolidation 14:00 | fact-check skipped: tracker unreachable | abc123 |\n",
    )
    _context(kb_repo, ROWS)
    assert _cli(kb_repo).exit_code == 1


def test_a_target_moved_or_archived_since_counts_as_done(kb_repo) -> None:
    kb_repo.write(FRONT, note(verified="2026-10-10"))  # TOPIC was moved: no file at its path
    _context(kb_repo, ROWS)
    result = _cli(kb_repo)
    assert result.exit_code == 0
    data = json.loads(_cli(kb_repo, "--json").stdout)
    assert data["required"] == 1 and data["settled"] == [TOPIC]


def test_a_target_shared_since_counts_as_done(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", f"privacy:\n  shared_files_add: ['{TOPIC}']\n")
    kb_repo.write(FRONT, note(verified="2026-10-10"))
    kb_repo.write(TOPIC, note())
    _context(kb_repo, ROWS)
    assert _cli(kb_repo).exit_code == 0


def test_a_shared_note_is_never_a_target(kb_repo, monkeypatch) -> None:
    kb_repo.write("scout-config.yaml", f"privacy:\n  shared_files_add: ['{TOPIC}']\n")
    kb_repo.write(TOPIC, note())
    kb_repo.write(FRONT, note())
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    assert paths(fact_check_targets(kb_repo.root, DAY, "owner")) == [FRONT]


def test_only_a_dreaming_run_computes_targets(kb_repo, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(pre_session_data, "get_fact_check_targets", lambda *a: calls.append("x") or [])
    assert pre_session_data.fact_check_for("morning-briefing", kb_repo.root, DAY, "owner") == []
    assert pre_session_data.fact_check_for("dreaming-nightly", kb_repo.root, DAY, "owner") == []
    assert calls == ["x"]


def test_cli_with_nothing_to_check_passes(kb_repo) -> None:
    _context(kb_repo, [])
    result = _cli(kb_repo)
    assert result.exit_code == 0 and "no KB note" in result.stdout


@pytest.mark.parametrize(
    ("rows", "day", "needle"),
    [
        (ROWS, "2026-10-09", "not 2026-10-10"),
        (None, "2026-10-10", "could not compute"),
    ],
)
def test_cli_exit_2_when_it_cannot_check(kb_repo, rows, day: str, needle: str) -> None:
    _context(kb_repo, rows, day=day)
    result = _cli(kb_repo)
    assert result.exit_code == 2 and needle in result.stdout


def test_cli_exit_2_without_a_context_file(kb_repo) -> None:
    result = _cli(kb_repo)
    assert result.exit_code == 2 and "pre-session data" in result.stdout


def test_cli_rejects_a_bad_date(kb_repo) -> None:
    result = runner.invoke(cli.app, ["kb", "check-fact-check", "--date", "soon", "--data-dir", str(kb_repo.root)])
    assert result.exit_code == 2


def test_check_fact_check_reads_the_notes_as_they_stand(kb_repo) -> None:
    kb_repo.write(FRONT, note(verified="2026-10-10"))
    _context(kb_repo, ROWS[:1])
    result = check_fact_check(data_dir=kb_repo.root, today="2026-10-10")
    assert result.passed and result.verified == [FRONT] and result.required == 1


def test_a_future_last_verified_is_refused() -> None:
    future = note(verified="2026-10-11")
    assert not judge(targets("a"), {"a": future}, DAY).passed


def test_a_future_last_verified_does_not_hide_a_note_from_the_targets(kb_repo, monkeypatch) -> None:
    kb_repo.write(FRONT, note(verified="2030-01-01"))
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    assert paths(fact_check_targets(kb_repo.root, DAY, "owner")) == [FRONT]


def test_an_accepted_skip_is_recorded_and_doctor_notes_repeats(kb_repo, monkeypatch) -> None:
    import datetime as _dt

    from scout.kb.fact_check import SKIP_EVENT, recent_skips
    from scout.scripts.bootstrap_doctor import _check_fact_check_skips

    kb_repo.write(FRONT, note())
    kb_repo.write(TOPIC, note())
    _context(kb_repo, ROWS)
    now = _dt.datetime.now(_dt.UTC)
    assert _check_fact_check_skips(vault=kb_repo.root, now=now) == []
    for _ in range(3):
        assert _cli(kb_repo, "--skipped", "the tracker was unreachable all run").exit_code == 0
    assert recent_skips(kb_repo.root, now) == 3
    logged = "".join(p.read_text() for p in (kb_repo.root / ".scout-logs").glob("schedule-events-*.jsonl"))
    assert SKIP_EVENT in logged and "unreachable" not in logged  # never the reason's text
    [doctor_note] = _check_fact_check_skips(vault=kb_repo.root, now=now)
    assert "skipped 3 time(s)" in doctor_note


def test_a_refused_skip_is_not_recorded(kb_repo) -> None:
    from scout.kb.fact_check import recent_skips

    _context(kb_repo, ROWS)
    assert _cli(kb_repo, "--skipped", "x").exit_code == 2
    import datetime as _dt

    assert recent_skips(kb_repo.root, _dt.datetime.now(_dt.UTC)) == 0


# ----- edges -------------------------------------------------------------------------------


def test_rank_and_verified_on_edges() -> None:
    from scout.kb.fact_check import _rank, verified_on

    assert _rank(f"{P}/widgets/knowledge/costs.md") == 1
    assert verified_on("---\nlast_verified: 2026-10-09 08:00:00\n---\nx\n") == (dt.date(2026, 10, 9), False)
    assert verified_on("---\nlast_verified: soon\n---\nx\n") == (None, False)


@pytest.mark.parametrize("raw", [{"path": FRONT}, [{"changed": "2026-10-09"}], ["x"]])
def test_a_malformed_context_is_refused(raw: object) -> None:
    from scout.action_items.prefetched import PrefetchCheckError
    from scout.kb.fact_check import _rows

    with pytest.raises(PrefetchCheckError):
        _rows(raw)


def test_a_note_gone_since_its_commit_is_not_a_target(kb_repo, monkeypatch) -> None:
    from scout.kb import fact_check

    kb_repo.write(FRONT, note())
    commit_at(kb_repo, "2026-10-09", monkeypatch)
    monkeypatch.setattr(fact_check, "_read", lambda vault, rel: None)
    assert fact_check_targets(kb_repo.root, DAY, "owner") == []


def test_skip_count_ignores_unreadable_event_lines(kb_repo) -> None:
    import datetime as _dt

    from scout.events import EVENT_LOG_PREFIX
    from scout.kb.fact_check import SKIP_EVENT, recent_skips

    now = _dt.datetime.now(_dt.UTC)
    logs = kb_repo.root / ".scout-logs"
    logs.mkdir(exist_ok=True)
    good = json.dumps({"kind": SKIP_EVENT})
    (logs / f"{EVENT_LOG_PREFIX}{now.date().isoformat()}.jsonl").write_text(f"not json\n[1]\n{good}\n")
    assert recent_skips(kb_repo.root, now) == 1
