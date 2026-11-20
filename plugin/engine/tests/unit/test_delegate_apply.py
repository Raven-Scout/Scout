"""Applying what an agent proposed (#550 V4a2): only what was taken, only what the delegate saw, within limits.

The vault is the anonymised privacy fixture vault with a real mirror for
``grok-personal``. Its daily file has a task the mirror shows, and one that
links a private note, which the mirror redacts. Proposals are taken the way
``inbox pull`` takes them: the signed record first, then the copy.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import delegate_apply as da
from scout import delegate_inbox as di
from scout import delegate_proposals as dp
from scout.cli import app
from scout.repo_sync import RepoSyncError

from .privacy_vault_helpers import git
from .test_delegate_mirror import _bare, _commit, _link, vault  # noqa: F401 - the fixture

D = "grok-personal"
DATE = "2026-11-19"
DAILY = f"action-items/action-items-{DATE}.md"
runner = CliRunner()
TODAY = dt.datetime.now(dt.UTC).date()

TASKS = (
    "# Action items\n\n"
    "- [ ] [#AB12] 🔴 Send the draft\n"
    "  - scout: waiting on the numbers\n"
    "- [ ] [#CD34] Review [[task-renew-permit]]\n"
    "- [ ] [#EF56] Book the room\n"
    "  - scout: ask about [#GH78] first\n"
)


REAL_DEFAULT_COMMIT = da._default_commit  # before the autouse stand-in below replaces it
REAL_TODAY = da._today


def _git_commit(v: Path, message: str, rels: list[str]) -> str:
    """The vault's commit helper, as a test stands it in: commit exactly these paths."""
    git(v, "add", "--", *rels)
    git(v, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", message, "--", *rels)
    return git(v, "rev-parse", "HEAD").strip()


@pytest.fixture(autouse=True)
def _commits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(da, "_default_commit", _git_commit)
    monkeypatch.setattr(da, "_today", lambda v: DATE)  # the fixture's daily file is "today" (V4b1: only today's)


@pytest.fixture
def mirrored(vault: Path, tmp_path: Path) -> Path:  # noqa: F811
    _commit(vault, DAILY, TASKS, "daily")
    _link(vault, D, _bare(tmp_path, "grok"))
    return vault


def _take(v: Path, kind: str, body: dict[str, object], *, delegate: str = D) -> dp.Proposal:
    """Take a proposal as inbox pull does: the signed record, then the copy."""
    p = dp.make(delegate, kind, body)
    state = di.load_state(v)
    state.taken.setdefault(delegate, {})[p.id] = {"mac": di.proposal_mac(delegate, p.id, p.to_bytes()), "at": "x"}
    di.save_state(v, state)
    folder = v.joinpath(*di.INBOX_DIR, delegate)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{p.id}.json").write_bytes(p.to_bytes())
    return p


def _op(v: Path, op: str, tag: str = "AB12", args: dict[str, object] | None = None, **kw: str) -> dp.Proposal:
    return _take(v, "action_item_op", {"op": op, "tag": tag, "date": DATE, "args": args or {}}, **kw)


def _daily(v: Path) -> str:
    return (v / DAILY).read_text(encoding="utf-8")


def test_mark_done_applies_once_and_reverts(mirrored: Path) -> None:
    p = _op(mirrored, "mark-done")
    r = da.apply(mirrored, D)
    assert r.outcomes == {p.id: "applied"}
    assert "- [x] [#AB12]" in _daily(mirrored)
    entry = di.load_state(mirrored).applied[D][p.id]
    assert entry["before"][0].startswith("- [ ] [#AB12]") and entry["after"][0].startswith("- [x] [#AB12]")
    assert "body" not in entry and "Send the draft" not in json.dumps({k: entry[k] for k in ("op", "tag", "date")})
    assert da.apply(mirrored, D).outcomes == {}  # never twice
    assert da.revert(mirrored, p.id) == D
    assert _daily(mirrored) == TASKS
    with pytest.raises(RepoSyncError) as e:
        da.revert(mirrored, p.id)
    assert e.value.code == "not_applied"


def test_a_copy_the_record_didnt_take_is_never_applied(mirrored: Path) -> None:
    planted = dp.make(D, "action_item_op", {"op": "mark-done", "tag": "AB12", "date": DATE, "args": {}})
    folder = mirrored.joinpath(*di.INBOX_DIR, D)
    folder.mkdir(parents=True)
    (folder / f"{planted.id}.json").write_bytes(planted.to_bytes())  # a run wrote it; the pull never took it
    edited = _op(mirrored, "mark-done", "EF56")
    (folder / f"{edited.id}.json").write_bytes(
        edited.to_bytes().replace(b'"EF56"', b'"AB12"')  # taken for EF56, edited afterwards to aim elsewhere
    )
    r = da.apply(mirrored, D)
    assert r.outcomes == {edited.id: "not_taken"} and r.untaken == 1  # the planted one: counted, never recorded
    assert planted.id not in di.load_state(mirrored).applied[D]
    assert _daily(mirrored) == TASKS


@pytest.mark.parametrize(
    "tag",
    [
        "CD34",  # the mirror redacted its line: it links a private note
        "ZZ99",  # no such task
        "GH78",  # only in a comment, never a task line
    ],
)
def test_an_item_the_delegate_didnt_see_is_unknown(mirrored: Path, tag: str) -> None:
    p = _op(mirrored, "mark-done", tag)
    assert da.apply(mirrored, D).outcomes == {p.id: "unknown_item"}
    assert _daily(mirrored) == TASKS


def test_an_item_hidden_since_the_mirror_is_unknown(mirrored: Path) -> None:
    hidden = TASKS.replace("  - scout: waiting on the numbers\n", "  - scout: see [[task-renew-permit]]\n")
    _commit(mirrored, DAILY, hidden, "hide")  # the mirror still shows AB12; the vault no longer would
    p = _op(mirrored, "mark-done")
    assert da.apply(mirrored, D).outcomes == {p.id: "unknown_item"}
    assert "- [ ] [#AB12]" in _daily(mirrored)


def test_add_comment_is_held_and_observations_wait(mirrored: Path) -> None:
    c = _op(mirrored, "add-comment", args={"comment": "Sent it this morning"})
    _take(mirrored, "observation", {"text": "The meeting moved"})
    r = da.apply(mirrored, D)
    assert r.outcomes == {c.id: "held"} and r.waiting == 1
    assert "Sent it this morning" not in _daily(mirrored)


def test_snooze_within_the_horizon_only(mirrored: Path) -> None:
    far = _op(mirrored, "snooze", args={"until": (TODAY + dt.timedelta(days=90)).isoformat()})
    past = _op(mirrored, "snooze", args={"until": (TODAY - dt.timedelta(days=1)).isoformat()})
    ok = _op(mirrored, "snooze", args={"until": (TODAY + dt.timedelta(days=3)).isoformat()})
    r = da.apply(mirrored, D)
    assert r.outcomes == {far.id: "refused", past.id: "refused", ok.id: "applied"}
    assert f"snoozed-until: {(TODAY + dt.timedelta(days=3)).isoformat()}" in _daily(mirrored)


def test_a_burst_is_limited(mirrored: Path) -> None:
    ids = [_op(mirrored, "set-estimate", args={"minutes": 30 + 15 * i}).id for i in range(da.BURST + 1)]
    r = da.apply(mirrored, D)
    assert [r.outcomes[i] for i in ids] == ["applied"] * da.BURST + ["limited"]


def test_already_so_is_a_no_op_and_old_is_stale(mirrored: Path) -> None:
    undo = _op(mirrored, "mark-done", args={"undo": True})  # AB12 is open already
    old = _op(mirrored, "mark-done", "EF56")
    r = da.apply(mirrored, D, now=dt.datetime.now(dt.UTC) + dt.timedelta(days=8))
    assert r.outcomes == {undo.id: "stale", old.id: "stale"}
    fresh = _op(mirrored, "mark-done", args={"undo": True})
    clear = _op(mirrored, "clear-block")
    assert da.apply(mirrored, D).outcomes == {fresh.id: "no_op", clear.id: "no_op"}


def test_no_mirror_is_retried(vault: Path) -> None:  # noqa: F811
    _commit(vault, DAILY, TASKS, "daily")
    p = _op(vault, "mark-done", delegate="duet")  # duet has no mirror on this computer
    assert da.apply(vault, "duet").outcomes == {p.id: "no_mirror"}
    assert da.apply(vault, "duet").outcomes == {p.id: "no_mirror"}  # asked again, not final


def test_a_crash_during_the_op_is_never_applied_twice(mirrored: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _op(mirrored, "mark-done")

    def boom(v: Path, body: object) -> None:
        raise RuntimeError("power cut")

    real = da._run_op
    monkeypatch.setattr(da, "_run_op", boom)
    assert da.apply(mirrored, D).outcomes == {p.id: "interrupted"}  # recorded, and the apply carries on
    monkeypatch.setattr(da, "_run_op", real)
    assert di.load_state(mirrored).applied[D][p.id]["outcome"] == "interrupted"
    assert da.apply(mirrored, D).outcomes == {}  # never tried again: the op may have half-run


def test_revert_refuses_a_task_changed_since(mirrored: Path) -> None:
    p = _op(mirrored, "mark-done")
    da.apply(mirrored, D)
    _commit(mirrored, DAILY, _daily(mirrored).replace("Send the draft", "Send the final draft"), "owner edit")
    with pytest.raises(RepoSyncError) as e:
        da.revert(mirrored, p.id)
    assert e.value.code == "revert_conflict"
    with pytest.raises(RepoSyncError) as e:
        da.revert(mirrored, p.id, env={"SCOUT_MODE": "briefing"})
    assert e.value.code == "headless_refused"
    with pytest.raises(RepoSyncError) as e:
        da.revert(mirrored, "not-a-ulid")
    assert e.value.code == "bad_arguments"


def test_cli_apply_and_revert(mirrored: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(mirrored))
    p = _op(mirrored, "mark-done")
    r = runner.invoke(app, ["inbox", "apply", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert {x["delegate"]: x["counts"] for x in json.loads(r.output)} == {D: {"applied": 1}}
    r = runner.invoke(app, ["inbox", "revert", p.id])
    assert r.exit_code == 0 and "reverted" in r.output
    assert runner.invoke(app, ["inbox", "apply"]).exit_code == 2


def test_a_symlinked_copy_or_folder_is_never_followed(mirrored: Path, tmp_path: Path) -> None:
    p = _op(mirrored, "mark-done")
    copy = mirrored.joinpath(*di.INBOX_DIR, D, f"{p.id}.json")
    real = tmp_path / "elsewhere.json"
    real.write_bytes(copy.read_bytes())
    copy.unlink()
    copy.symlink_to(real)  # the right bytes, but through a link a run could aim anywhere
    assert da.apply(mirrored, D).outcomes == {p.id: "not_taken"}
    folder = mirrored.joinpath(*di.INBOX_DIR, D)
    moved = tmp_path / "moved"
    folder.rename(moved)
    folder.symlink_to(moved)
    with pytest.raises(RepoSyncError) as e:
        da.apply(mirrored, D)
    assert e.value.code == "inbox_folder_unsafe"


def test_a_daily_file_made_private_since_the_mirror_is_unknown(mirrored: Path) -> None:
    _commit(mirrored, DAILY, "---\nvisibility: private\n---\n" + TASKS, "private daily")  # HEAD has it private
    p = _op(mirrored, "mark-done")
    assert da.apply(mirrored, D).outcomes == {p.id: "unknown_item"}


# ----- the remaining paths (coverage floor) ----------------------------------------------------


def test_a_copy_that_is_a_folder_or_too_big_is_not_taken(mirrored: Path) -> None:
    a = _op(mirrored, "mark-done")
    b = _op(mirrored, "mark-done", "EF56")
    folder = mirrored.joinpath(*di.INBOX_DIR, D)
    (folder / f"{a.id}.json").unlink()
    (folder / f"{a.id}.json").mkdir()
    (folder / f"{b.id}.json").write_bytes(b"x" * (dp.MAX_BYTES + 1))
    assert da.apply(mirrored, D).outcomes == {a.id: "not_taken", b.id: "not_taken"}
    assert _daily(mirrored) == TASKS


def test_a_task_line_hidden_since_the_mirror_is_unknown(mirrored: Path) -> None:
    _commit(mirrored, DAILY, TASKS.replace("Send the draft", "Send [[task-renew-permit]]"), "hide the line")
    p = _op(mirrored, "mark-done")
    assert da.apply(mirrored, D).outcomes == {p.id: "unknown_item"}


def test_set_block_applies_and_a_command_refusal_is_refused(mirrored: Path) -> None:
    ok = _op(mirrored, "set-block", args={"day": "2026-11-20", "start": "09:00", "end": "10:30"})
    bad = _op(mirrored, "set-block", "EF56", args={"day": "2026-11-20", "start": "10:30", "end": "09:00"})
    r = da.apply(mirrored, D)
    assert r.outcomes == {ok.id: "applied", bad.id: "refused"}
    assert "block: 2026-11-20 09:00-10:30" in _daily(mirrored)
    assert di.load_state(mirrored).applied[D][bad.id]["why"] == "command"


def test_a_delegate_with_nothing_taken_and_a_failing_apply_all(mirrored: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert da.apply(mirrored, "duet").outcomes == {}  # no copies folder at all
    _op(mirrored, "mark-done")
    _op(mirrored, "mark-done", delegate="duet")
    real = da.apply

    def flaky(v: Path, delegate: str, **kw: object) -> da.ApplyResult:
        if delegate == "duet":
            raise RepoSyncError("busy", "refused: busy")
        return real(v, delegate, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(da, "apply", flaky)
    results = da.apply_all(mirrored)
    assert isinstance(results[0], RepoSyncError) and results[0].detail["delegate"] == "duet"
    assert isinstance(results[1], da.ApplyResult) and results[1].counts() == {"applied": 1}


def test_task_lines_of_a_missing_file_or_tag(tmp_path: Path) -> None:
    assert da._task_lines(tmp_path / "missing.md", "AB12") is None
    (tmp_path / "day.md").write_text(TASKS, encoding="utf-8")
    assert da._task_lines(tmp_path / "day.md", "ZZ99") is None
    assert da._task_lines(tmp_path / "day.md", "AB12") == [
        "- [ ] [#AB12] 🔴 Send the draft",
        "  - scout: waiting on the numbers",
    ]


# ----- the #725 security review ----------------------------------------------------------------


def _head_subject(v: Path) -> str:
    return git(v, "log", "-1", "--format=%s").strip()


def test_each_applied_change_is_committed_at_once_naming_the_delegate(mirrored: Path) -> None:
    p = _op(mirrored, "mark-done")
    da.apply(mirrored, D)
    assert _head_subject(mirrored) == f"inbox: mark-done [#AB12] from delegate-{D} ({p.id})"
    assert git(mirrored, "status", "--porcelain", "--", "action-items").strip() == ""
    assert di.load_state(mirrored).applied[D][p.id]["commit"] == git(mirrored, "rev-parse", "HEAD").strip()
    da.revert(mirrored, p.id)
    assert _head_subject(mirrored) == f"inbox: revert mark-done [#AB12] from delegate-{D} ({p.id})"
    assert git(mirrored, "status", "--porcelain", "--", "action-items").strip() == ""


def test_h1_revert_never_writes_through_a_planted_symlink(mirrored: Path, tmp_path: Path) -> None:
    p = _op(mirrored, "mark-done")
    da.apply(mirrored, D)
    victim = tmp_path / "pre-commit"
    victim.write_text("keep\n", encoding="utf-8")
    daily = mirrored / DAILY
    (daily.parent / f".{daily.name}.revert.tmp").symlink_to(victim)
    da.revert(mirrored, p.id)
    assert victim.read_text(encoding="utf-8") == "keep\n" and _daily(mirrored) == TASKS


def test_h1_a_symlinked_daily_file_is_never_followed(mirrored: Path, tmp_path: Path) -> None:
    p = _op(mirrored, "mark-done")
    da.apply(mirrored, D)
    secret = tmp_path / "secret.md"
    secret.write_text(_daily(mirrored), encoding="utf-8")
    daily = mirrored / DAILY
    daily.unlink()
    daily.symlink_to(secret)
    with pytest.raises(RepoSyncError) as e:
        da.revert(mirrored, p.id)
    assert e.value.code == "revert_conflict"
    q = _op(mirrored, "mark-done", "EF56")
    assert da.apply(mirrored, D).outcomes == {q.id: "unknown_item"}
    assert "[ ] [#EF56]" in secret.read_text(encoding="utf-8")


def test_m1_a_daily_file_that_isnt_utf8_or_any_crash_never_stops_apply(
    mirrored: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (mirrored / DAILY).write_bytes(TASKS.encode() + b"\xff\xfe\n")
    git(mirrored, "add", "-A")
    git(mirrored, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "bytes")
    p = _op(mirrored, "mark-done")
    assert da.apply(mirrored, D).outcomes == {p.id: "unknown_item"}  # not visible as text: never a crash
    a, b = _op(mirrored, "mark-done", "EF56"), _op(mirrored, "clear-block")

    calls: list[int] = []
    real = da._Seen.in_mirror

    def once(self: object, date: str, tag: str) -> bool:
        calls.append(1)
        if len(calls) == 1:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
        return real(self, date, tag)  # type: ignore[arg-type]

    monkeypatch.setattr(da._Seen, "in_mirror", once)
    r = da.apply(mirrored, D)
    assert r.outcomes[a.id] == "refused" and r.outcomes[b.id] in ("no_op", "unknown_item")
    assert di.load_state(mirrored).applied[D][a.id]["why"] == "error"


def test_m1_apply_all_carries_on_past_any_exception(mirrored: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _op(mirrored, "mark-done")
    _op(mirrored, "mark-done", delegate="duet")
    real = da.apply

    def flaky(v: Path, delegate: str, **kw: object) -> da.ApplyResult:
        if delegate == "duet":
            raise KeyError("boom")
        return real(v, delegate, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(da, "apply", flaky)
    results = da.apply_all(mirrored)
    assert isinstance(results[0], RepoSyncError) and results[0].code == "apply_failed"
    assert isinstance(results[1], da.ApplyResult) and results[1].counts() == {"applied": 1}


def test_m2_apply_is_the_owners_and_waits_for_a_live_run(mirrored: Path) -> None:
    from scout.action_items import retitle

    _op(mirrored, "mark-done")
    for env in ({"SCOUT_MODE": "briefing"}, {"SCOUT_READER": "autonomous"}):
        with pytest.raises(RepoSyncError) as e:
            da.apply(mirrored, D, env=env)
        assert e.value.code == "headless_refused"
    with retitle.session_lock(mirrored):  # a run holds the vault's session lock
        for call in (lambda: da.apply(mirrored, D), lambda: da.revert(mirrored, "01M7T0000000000000000000AB")):
            with pytest.raises(RepoSyncError) as e:
                call()
            assert e.value.code == "busy"
    assert _daily(mirrored) == TASKS


def test_m2_a_commit_that_doesnt_land_puts_the_change_back(mirrored: Path) -> None:
    p = _op(mirrored, "mark-done")

    def fails(v: Path, message: str, rels: list[str]) -> str:
        raise RepoSyncError("commit_failed", "refused: the helper failed")

    with pytest.raises(RepoSyncError) as e:
        da.apply(mirrored, D, commit=fails)
    assert e.value.code == "commit_failed" and _daily(mirrored) == TASKS
    assert p.id not in di.load_state(mirrored).applied[D]  # tried again next time
    assert da.apply(mirrored, D).outcomes == {p.id: "applied"}


def test_m2_a_commit_that_lands_elsewhere_is_left_as_it_is(mirrored: Path) -> None:
    p = _op(mirrored, "mark-done")

    def moves_head(v: Path, message: str, rels: list[str]) -> str:
        _commit(v, "README-other.md", "x\n", "someone else")
        raise RuntimeError("the helper crashed")

    with pytest.raises(RepoSyncError) as e:
        da.apply(mirrored, D, commit=moves_head)
    assert e.value.code == "commit_unclear"
    assert di.load_state(mirrored).applied[D][p.id]["outcome"] == "interrupted"


def test_an_uncommitted_edit_waits_and_a_planted_copy_waits(mirrored: Path) -> None:
    p = _op(mirrored, "mark-done")
    (mirrored / DAILY).write_text(TASKS + "- [ ] [#ZZ11] owner's new task\n", encoding="utf-8")
    assert da.apply(mirrored, D).outcomes == {p.id: "dirty"}
    _commit(mirrored, DAILY, (mirrored / DAILY).read_text(encoding="utf-8"), "owner commits")
    assert da.apply(mirrored, D).outcomes == {p.id: "applied"}

    q = _op(mirrored, "mark-done", "EF56")
    copy = mirrored.joinpath(*di.INBOX_DIR, D, f"{q.id}.json")
    good = copy.read_bytes()
    copy.write_bytes(good.replace(b"EF56", b"AB12"))  # a run edits one byte
    assert da.apply(mirrored, D).outcomes == {q.id: "not_taken"}
    copy.write_bytes(good)  # not final: once the copy is right again, it applies
    assert da.apply(mirrored, D).outcomes == {q.id: "applied"}


def test_limited_is_tried_again_on_a_later_day(mirrored: Path) -> None:
    ids = [_op(mirrored, "set-estimate", args={"minutes": 30 + 15 * i}).id for i in range(da.BURST + 1)]
    assert da.apply(mirrored, D).outcomes[ids[-1]] == "limited"
    assert da.apply(mirrored, D).outcomes == {}  # not again the same day
    tomorrow = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
    assert da.apply(mirrored, D, now=tomorrow).outcomes == {ids[-1]: "applied"}


def test_set_block_beyond_the_horizon_is_refused(mirrored: Path) -> None:
    far = (TODAY + dt.timedelta(days=90)).isoformat()
    past = (TODAY - dt.timedelta(days=1)).isoformat()
    a = _op(mirrored, "set-block", args={"day": far, "start": "09:00", "end": "10:00"})
    b = _op(mirrored, "set-block", "EF56", args={"day": past, "start": "09:00", "end": "10:00"})
    assert da.apply(mirrored, D).outcomes == {a.id: "refused", b.id: "refused"}
    assert di.load_state(mirrored).applied[D][a.id]["why"] == "block_horizon"


def test_revert_refuses_an_ambiguous_ulid_or_a_dirty_file(mirrored: Path) -> None:
    p = _op(mirrored, "mark-done")
    da.apply(mirrored, D)
    (mirrored / DAILY).write_text(_daily(mirrored) + "\n- [ ] [#ZZ22] new\n", encoding="utf-8")
    with pytest.raises(RepoSyncError) as e:
        da.revert(mirrored, p.id)
    assert e.value.code == "revert_dirty"
    git(mirrored, "checkout", "--", DAILY)
    state = di.load_state(mirrored)
    state.applied["duet"] = {p.id: dict(state.applied[D][p.id])}
    di.save_state(mirrored, state)
    with pytest.raises(RepoSyncError) as e:
        da.revert(mirrored, p.id)
    assert e.value.code == "ambiguous"


def test_old_applied_entries_are_pruned(mirrored: Path) -> None:
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = di.load_state(mirrored)
    state.applied[D] = {"01M7T0000000000000000000AB": {"outcome": "applied", "at": old, "before": ["x"]}}
    di.save_state(mirrored, state)
    da.apply(mirrored, D)
    assert "01M7T0000000000000000000AB" not in di.load_state(mirrored).applied[D]


def test_the_vault_commit_helper_is_scouts_own(mirrored: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.action_items import retitle

    seen: list[list[str]] = []

    def helper(v: Path, message: str, rels: list[str]) -> str:
        seen.append([message, *rels])
        raise retitle.RetitleError("no helper here")

    monkeypatch.setattr(retitle, "git_safe_commit", helper)
    with pytest.raises(RepoSyncError) as e:
        REAL_DEFAULT_COMMIT(mirrored, "m", ["a"])
    assert e.value.code == "commit_failed" and seen == [["m", "a"]]


def test_plain_refuses_every_odd_shape(tmp_path: Path) -> None:
    v = tmp_path / "v"
    v.mkdir()
    assert not da._plain(v, DAILY)  # no folder
    (v / "action-items").mkdir()
    assert not da._plain(v, DAILY)  # no file
    (v / DAILY).write_text("x\n", encoding="utf-8")
    assert da._plain(v, DAILY)
    (v / "elsewhere.md").hardlink_to(v / DAILY)
    assert not da._plain(v, DAILY)  # a hard link
    (v / "action-items").rename(v / "real")
    (v / "action-items").symlink_to(v / "real")
    assert not da._plain(v, DAILY)  # the folder is a symlink
    (v / "action-items").unlink()
    (v / "action-items").write_text("a file\n", encoding="utf-8")
    assert not da._plain(v, DAILY)  # the folder is a file
    assert da._commit_change(v, "m", {v / "elsewhere.md": None}, _git_commit) is None  # a hard link reads as absent


def test_cli_apply_and_revert_every_output(mirrored: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(mirrored))
    p = _op(mirrored, "mark-done")
    _take(mirrored, "observation", {"text": "The meeting moved"})
    r = runner.invoke(app, ["inbox", "apply", D])
    assert r.exit_code == 0 and f"{D}: applied 1" in r.output and "wait for your review" in r.output
    assert runner.invoke(app, ["inbox", "apply", D]).output.startswith(f"{D}: nothing new")
    r = runner.invoke(app, ["inbox", "revert", p.id, "--json"])
    assert r.exit_code == 0 and json.loads(r.output) == {"ok": True, "delegate": D, "reverted": p.id}
    r = runner.invoke(app, ["inbox", "revert", p.id])
    assert r.exit_code == 2 and "wasn't applied" in r.output

    _op(mirrored, "mark-done", delegate="duet")
    real = da.apply

    def flaky(v: Path, delegate: str, **kw: object) -> da.ApplyResult:
        if delegate == "duet":
            raise RepoSyncError("busy", "refused: busy")
        return real(v, delegate, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(da, "apply", flaky)
    r = runner.invoke(app, ["inbox", "apply", "--all"])
    assert r.exit_code == 2 and "duet: refused: busy" in r.output
    r = runner.invoke(app, ["inbox", "apply", "--all", "--json"])
    assert r.exit_code == 2
    rows = json.loads(r.stdout)
    assert rows[0] == {"ok": False, "delegate": "duet", "code": "busy", "message": "refused: busy"}
    for args in (["inbox", "apply", "duet"], ["inbox", "apply", "duet", "--json"]):
        r = runner.invoke(app, args)
        assert r.exit_code == 2 and "busy" in r.output


# ----- the #725 re-review nits -----------------------------------------------------------------


def test_n1_junk_copies_are_counted_never_recorded(mirrored: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = mirrored.joinpath(*di.INBOX_DIR, D)
    folder.mkdir(parents=True, exist_ok=True)
    junk = [dp.make(D, "observation", {"text": f"j{i}"}) for i in range(200)]
    for p in junk:
        (folder / f"{p.id}.json").write_bytes(p.to_bytes())  # a run planted them: no record took any
    real = di.save_state
    saves: list[int] = []

    def counting(v: Path, state: di.InboxState) -> None:
        saves.append(1)
        real(v, state)

    monkeypatch.setattr(di, "save_state", counting)
    for _ in range(3):
        r = da.apply(mirrored, D)
        assert r.outcomes == {} and r.untaken == 200 and "never taken" in r.notes[-1]
    assert di.load_state(mirrored).applied.get(D, {}) == {}
    assert len(saves) <= 3  # once per apply at most, never once per copy


def test_a_failed_commit_unstages_and_restores_only_what_the_op_left(mirrored: Path) -> None:
    p = _op(mirrored, "mark-done")

    def stages_then_fails(v: Path, message: str, rels: list[str]) -> str:
        git(v, "add", "--", *rels)
        raise RuntimeError("the helper failed after staging")

    with pytest.raises(RepoSyncError) as e:
        da.apply(mirrored, D, commit=stages_then_fails)
    assert e.value.code == "commit_failed" and _daily(mirrored) == TASKS
    assert git(mirrored, "diff", "--cached", "--name-only").strip() == ""  # nothing left for a run to commit

    def edited_meanwhile(v: Path, message: str, rels: list[str]) -> str:
        (v / DAILY).write_text(_daily(v) + "- [ ] [#ZZ33] added in the app meanwhile\n", encoding="utf-8")
        raise RuntimeError("the helper failed")

    with pytest.raises(RepoSyncError) as e:
        da.apply(mirrored, D, commit=edited_meanwhile)
    assert e.value.code == "commit_unclear"
    assert "ZZ33" in _daily(mirrored) and "- [x] [#AB12]" in _daily(mirrored)  # the owner's edit kept, nothing lost
    assert di.load_state(mirrored).applied[D][p.id]["outcome"] == "interrupted"


def test_cli_single_apply_reports_any_failure(mirrored: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(mirrored))

    def broken(*a: object, **k: object) -> object:
        raise OSError("disk went away")

    monkeypatch.setattr(da, "apply", broken)
    r = runner.invoke(app, ["inbox", "apply", D, "--json"])
    assert r.exit_code == 2 and json.loads(r.output)["code"] == "apply_failed"


# ----- V4b1: today's file only, a time budget, events ------------------------------------------


def test_only_todays_daily_file_is_changed(mirrored: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _op(mirrored, "mark-done")
    monkeypatch.setattr(da, "_today", lambda v: "2026-11-20")  # the proposal names another day's file
    assert da.apply(mirrored, D).outcomes == {p.id: "stale"}
    assert di.load_state(mirrored).applied[D][p.id]["why"] == "not_today"
    assert _daily(mirrored) == TASKS


def test_the_real_today_is_the_vaults_configured_day(mirrored: Path) -> None:
    from scout import paths

    assert REAL_TODAY(mirrored) == paths._today(mirrored).isoformat()


def test_no_new_copy_is_started_past_the_deadline(mirrored: Path) -> None:
    p = _op(mirrored, "mark-done")
    r = da.apply(mirrored, D, deadline=0.0)  # long past
    assert r.outcomes == {} and "out of time" in r.notes[-1]
    assert da.apply(mirrored, D).outcomes == {p.id: "applied"}


def test_an_applied_change_leaves_an_event_with_ids_only(mirrored: Path) -> None:
    from scout import paths

    p = _op(mirrored, "mark-done")
    da.apply(mirrored, D)
    rows = [
        json.loads(line)
        for f in sorted(paths.logs_dir(mirrored).glob("schedule-events-*.jsonl"))
        for line in f.read_text(encoding="utf-8").splitlines()
    ]
    mine = [r for r in rows if r["kind"] == "delegate_inbox.applied"]
    assert len(mine) == 1 and mine[0]["source"] == f"delegate:{D}"
    assert set(mine[0]["payload"]) == {"ulid", "op", "tag", "date", "commit"} and mine[0]["payload"]["ulid"] == p.id
