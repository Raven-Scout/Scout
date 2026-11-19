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
    assert r.outcomes == {planted.id: "not_taken", edited.id: "not_taken"}
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
    (mirrored / DAILY).write_text(hidden, encoding="utf-8")  # the mirror still shows AB12; the vault no longer would
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
    with pytest.raises(RuntimeError):
        da.apply(mirrored, D)
    monkeypatch.setattr(da, "_run_op", real)
    assert di.load_state(mirrored).applied[D][p.id]["outcome"] == "interrupted"
    assert da.apply(mirrored, D).outcomes == {}


def test_revert_refuses_a_task_changed_since(mirrored: Path) -> None:
    p = _op(mirrored, "mark-done")
    da.apply(mirrored, D)
    (mirrored / DAILY).write_text(_daily(mirrored).replace("Send the draft", "Send the final draft"), encoding="utf-8")
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
    (mirrored / DAILY).write_text("---\nvisibility: private\n---\n" + TASKS, encoding="utf-8")
    _commit(mirrored, "README-x.md", "x\n", "private daily")  # HEAD now has it private
    p = _op(mirrored, "mark-done")
    assert da.apply(mirrored, D).outcomes == {p.id: "unknown_item"}
