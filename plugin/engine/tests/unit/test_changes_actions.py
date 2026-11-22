"""What a run must do with the changes map (`changes due`), and doing it: rollback, governance apply and discard."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scout.changes.actions import (
    ANNOUNCED_NAME,
    ActionRefused,
    apply_governance,
    discard_governance,
    due,
    mark_announced,
    rollback,
)
from scout.changes.models import Change, Signal, Why
from scout.changes.scan import scan
from scout.changes.settings import Settings, write_settings
from scout.changes.store import append_change, append_signal

TZ = ZoneInfo("UTC")
NOW = datetime.now(UTC)


def _git(repo: Path, *args: str, date: datetime | None = None) -> str:
    env = dict(os.environ)
    if date is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date.isoformat()
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env)
    return out.stdout.strip()


def _commit(repo: Path, message: str, files: dict[str, str | None], *, date: datetime | None = None) -> str:
    """Write (or, for None, delete) each file, stage everything and commit. Returns the full sha."""
    for path, content in files.items():
        target = repo / path
        if content is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message, date=date or NOW - timedelta(days=1))
    return _git(repo, "rev-parse", "HEAD")


def _cid(sha: str) -> str:
    return f"chg-{sha[:12]}"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def env(home: Path) -> dict[str, str]:
    """An owner env: no SCOUT_MODE, its own HOME (the owner store lives under it)."""
    return {"HOME": str(home)}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "vault"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.name", "Scout Test")
    _git(r, "config", "user.email", "scout@example.invalid")
    _git(r, "config", "commit.gpgsign", "false")
    _commit(
        r,
        "seed",
        {"SKILL.md": "skill v1\n", "knowledge-base/a.md": "note a\n", "README.md": "readme\n"},
        date=NOW - timedelta(days=60),
    )
    return r


def _undo(repo: Path, env: dict[str, str], cid: str, *, ts: datetime | None = None) -> None:
    append_signal(Signal(ts=ts or NOW, kind="approval", surface="scoutctl", ref=f"undo:{cid}"), vault=repo, env=env)


def _decide(repo: Path, env: dict[str, str], verb: str, gov_id: str, *, digest: str | None = None) -> None:
    """The owner's decision on the pending file's current diff (or on ``digest``, a diff they saw earlier)."""
    from scout.changes.governance import read_pending

    if digest is None:
        digest = next(p.hash for p in read_pending(repo)[0] if p.id == gov_id)
    ref = f"{verb}:{gov_id}@{digest}"
    append_signal(Signal(ts=NOW, kind="approval", surface="scoutctl", ref=ref), vault=repo, env=env)


def _due(repo: Path, env: dict[str, str]) -> dict:
    return due(repo, env, now=NOW, tz=TZ)


def _tree(repo: Path) -> dict[str, bytes]:
    return {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts}


def _brain_edit(repo: Path, *, days_ago: float = 1, trailers: str = "", content: str = "skill v2\n") -> str:
    msg = "dreaming [22:12]: SKILL.md self-edit — tighten the wrap" + (f"\n\n{trailers}" if trailers else "")
    sha = _commit(repo, msg, {"SKILL.md": content}, date=NOW - timedelta(days=days_ago))
    scan(repo, since=None)
    return sha


def _hurt(repo: Path, cid: str, *, days_ago: float) -> None:
    """A negative DM reply naming the change, two days into its review window: the verdict will be hurt."""
    ts = NOW - timedelta(days=days_ago - 2)
    append_signal(
        Signal(
            ts=ts, kind="dm-reply", surface="slack-dm", ref=f"{ts.timestamp()}/1.2", sentiment="negative", change=cid
        ),
        vault=repo,
        env={},
    )


# --- due: owner undo and auto-rollback -----------------------------------------------------------------------------


def test_owner_undo_is_due_once_then_not_after_the_rollback_commit(repo, env):
    sha = _brain_edit(repo)
    cid = _cid(sha)
    assert _due(repo, env)["rollbacks"] == []
    _undo(repo, env, cid)
    _undo(repo, env, cid, ts=NOW - timedelta(minutes=5))  # a duplicated request is still one rollback
    doc = _due(repo, env)
    assert doc["schema_version"] == 1 and doc["verified"] is True
    assert doc["rollbacks"] == [{"id": cid, "summary": "SKILL.md self-edit — tighten the wrap", "reason": "owner-undo"}]
    assert doc["blocked"] == []
    paths = rollback(repo, env, cid, now=NOW, tz=TZ)
    assert paths == ["SKILL.md"]
    _commit(repo, f"dreaming [22:30]: roll back {cid}\n\nScout-Change-Reverts: {cid}", {}, date=NOW)
    scan(repo, since=None)
    doc = _due(repo, env)
    assert doc["rollbacks"] == [] and doc["blocked"] == []


def test_a_commit_that_only_claims_to_revert_keeps_the_undo_and_the_suspect(repo, env):
    """I1: `Scout-Change-Reverts: X` on a commit that doesn't touch X's own files undoes nothing, so X's undo request
    stays due and its verdict and suspect flag stand."""
    cid = _cid(_brain_edit(repo, days_ago=20))
    _hurt(repo, cid, days_ago=20)
    _undo(repo, env, cid)
    assert [r["id"] for r in _due(repo, env)["rollbacks"]] == [cid]
    _commit(repo, f"dreaming [22:30]: tidy\n\nScout-Change-Reverts: {cid}", {"README.md": "r2\n"}, date=NOW)
    _commit(repo, f'Revert "x"\n\nThis reverts commit {"0" * 40}.', {"README.md": "r3\n"}, date=NOW)
    # A self-change path, but not the one the target changed: still not an undo of it.
    _commit(repo, f"dreaming [22:40]: tidy\n\nScout-Change-Reverts: {cid}", {"scripts/other.py": "x\n"}, date=NOW)
    scan(repo, since=None)
    doc = _due(repo, env)
    assert [r["id"] for r in doc["rollbacks"]] == [cid]
    assert [s["id"] for s in doc["suspect_unannounced"]] == [cid]
    from scout.changes.report import changes_report

    rows = changes_report(repo, now=NOW, since=NOW - timedelta(days=60), env=env, tz=TZ)["changes"]
    # None of them is a rollback: the README commits are no change at all, the script edit is a driver under test.
    assert [r["kind"] for r in rows if r["kind"] == "rollback"] == []
    driver = next(r for r in rows if r["kind"] == "driver")
    assert driver["reverts"] is None and driver["test"]["state"] == "testing"
    assert next(r for r in rows if r["id"] == cid)["test"]["rolled_back_by"] is None


def test_a_real_rollback_commit_clears_the_undo(repo, env):
    cid = _cid(_brain_edit(repo, days_ago=20))
    _undo(repo, env, cid)
    paths = rollback(repo, env, cid, now=NOW, tz=TZ)
    _git(repo, "add", "--", *paths)
    # Dated `NOW`, the report's clock: a commit after it is not seen yet (the test once passed only because the
    # already-undone tree no longer reversed cleanly).
    _git(repo, "commit", "-q", "-m", f"dreaming [22:30]: roll back {cid}\n\nScout-Change-Reverts: {cid}", date=NOW)
    scan(repo, since=None)
    doc = _due(repo, env)
    assert doc["rollbacks"] == [] and doc["blocked"] == []


@pytest.fixture
def auto_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The automatic rollback modes acting, as they will once Plan 3 lets them (actions.AUTO_ROLLBACK_LIVE)."""
    from scout.changes import actions

    monkeypatch.setattr(actions, "AUTO_ROLLBACK_LIVE", True)


def test_auto_modes_are_inert_for_now_whatever_the_settings_say(repo, env):
    """#747 review: the CLI refuses to set an automatic mode, and `due` ignores one a settings file already holds."""
    _two_hurt_changes(repo)
    for mode in ("targeted", "one-at-a-time"):
        write_settings(Settings(auto_rollback=mode, calibrated_on=NOW.date()), env)
        assert _due(repo, env)["rollbacks"] == [], mode


def _two_hurt_changes(repo: Path) -> tuple[str, str]:
    """Two run changes four days apart (one shared window), both hurt."""
    a = _cid(_brain_edit(repo, days_ago=22))
    b = _cid(
        _commit(
            repo, "dreaming [22:12]: DREAMING.md self-edit — b", {"DREAMING.md": "d2\n"}, date=NOW - timedelta(days=18)
        )
    )
    scan(repo, since=None)
    _hurt(repo, a, days_ago=22)
    _hurt(repo, b, days_ago=18)
    return a, b


def test_one_at_a_time_waits_a_week_after_a_rollback_in_the_window(repo, env, auto_live):
    """I7: after the newest suspect is rolled back, the older one is not undone on the next run, only a week later."""
    a, b = _two_hurt_changes(repo)
    write_settings(Settings(auto_rollback="one-at-a-time", calibrated_on=NOW.date()), env)
    assert [(r["id"], r["reason"]) for r in _due(repo, env)["rollbacks"]] == [(b, "auto-one-at-a-time")]
    paths = rollback(repo, env, b, now=NOW, tz=TZ)
    _git(repo, "add", "--", *paths)
    _git(repo, "commit", "-q", "-m", f"dreaming [22:30]: roll back {b}\n\nScout-Change-Reverts: {b}", date=NOW)
    scan(repo, since=None)
    assert _due(repo, env)["rollbacks"] == []
    later = NOW + timedelta(days=6, hours=23)
    assert due(repo, env, now=later, tz=TZ)["rollbacks"] == []
    after_a_week = NOW + timedelta(days=7, hours=1)
    assert [(r["id"], r["reason"]) for r in due(repo, env, now=after_a_week, tz=TZ)["rollbacks"]] == [
        (a, "auto-one-at-a-time")
    ]


def test_auto_modes_skip_a_verdict_the_owner_marked_wrong(repo, env, auto_live):
    cid = _cid(_brain_edit(repo, days_ago=20))
    _hurt(repo, cid, days_ago=20)
    append_signal(Signal(ts=NOW, kind="verdict-wrong", surface="scoutctl", ref=cid), vault=repo, env=env)
    for mode in ("targeted", "one-at-a-time"):
        write_settings(Settings(auto_rollback=mode, calibrated_on=NOW.date()), env)
        assert _due(repo, env)["rollbacks"] == [], mode
    _undo(repo, env, cid)  # the owner may still undo it by hand
    assert [r["reason"] for r in _due(repo, env)["rollbacks"]] == ["owner-undo"]


def test_auto_modes_need_calibration_to_act(repo, env, auto_live):
    sha = _brain_edit(repo, days_ago=20)
    cid = _cid(sha)
    _hurt(repo, cid, days_ago=20)
    write_settings(Settings(auto_rollback="targeted"), env)
    assert _due(repo, env)["rollbacks"] == []  # calibrating: verdicts are shown, nothing acts on them
    write_settings(Settings(auto_rollback="targeted", calibrated_on=NOW.date()), env)
    assert [(r["id"], r["reason"]) for r in _due(repo, env)["rollbacks"]] == [(cid, "auto-targeted")]
    write_settings(Settings(auto_rollback="one-at-a-time", calibrated_on=NOW.date()), env)
    assert [(r["id"], r["reason"]) for r in _due(repo, env)["rollbacks"]] == [(cid, "auto-one-at-a-time")]
    write_settings(Settings(auto_rollback="off", calibrated_on=NOW.date()), env)
    assert _due(repo, env)["rollbacks"] == []


def test_an_owner_undo_and_an_auto_match_list_the_change_once(repo, env, auto_live):
    sha = _brain_edit(repo, days_ago=20)
    cid = _cid(sha)
    _hurt(repo, cid, days_ago=20)
    write_settings(Settings(auto_rollback="targeted", calibrated_on=NOW.date()), env)
    _undo(repo, env, cid)
    assert [(r["id"], r["reason"]) for r in _due(repo, env)["rollbacks"]] == [(cid, "owner-undo")]


# --- rollback ------------------------------------------------------------------------------------------------------


def test_rollback_restores_modified_deletes_added_and_unrenames(repo, env):
    (repo / "knowledge-base/b").mkdir()
    _git(repo, "mv", "knowledge-base/a.md", "knowledge-base/b/a.md")
    sha = _commit(
        repo,
        "dreaming [22:12]: SKILL.md self-edit — and a new driver",
        {"SKILL.md": "skill v2\n", "scripts/new.py": "print('new')\n"},
    )
    scan(repo, since=None)
    cid = _cid(sha)
    _undo(repo, env, cid)
    paths = rollback(repo, env, cid, now=NOW, tz=TZ)
    assert paths == ["SKILL.md", "knowledge-base/a.md", "knowledge-base/b/a.md", "scripts/new.py"]
    assert (repo / "SKILL.md").read_text() == "skill v1\n"
    assert not (repo / "scripts/new.py").exists()
    assert (repo / "knowledge-base/a.md").read_text() == "note a\n"
    assert not (repo / "knowledge-base/b/a.md").exists()
    assert _git(repo, "diff", "--cached", "--name-only") == ""  # working tree only: nothing staged
    assert _git(repo, "rev-parse", "HEAD") == sha  # and nothing committed


def test_rollback_keeps_an_executable_bit(repo, env):
    (repo / "scripts").mkdir()
    (repo / "scripts/run.sh").write_text("echo v1\n")
    (repo / "scripts/run.sh").chmod(0o755)
    _commit(repo, "seed a script", {}, date=NOW - timedelta(days=30))
    (repo / "scripts/run.sh").chmod(0o644)
    sha = _commit(repo, "dreaming [22:12]: driver tweak", {"scripts/run.sh": "echo v2\n"})
    assert not os.access(repo / "scripts/run.sh", os.X_OK)
    scan(repo, since=None)
    _undo(repo, env, _cid(sha))
    assert rollback(repo, env, _cid(sha), now=NOW, tz=TZ) == ["scripts/run.sh"]
    assert (repo / "scripts/run.sh").read_text() == "echo v1\n"
    assert os.access(repo / "scripts/run.sh", os.X_OK)


def test_a_later_edit_blocks_and_nothing_is_written(repo, env):
    sha = _brain_edit(repo, days_ago=2)
    cid = _cid(sha)
    _commit(repo, "scout: tweak SKILL.md by hand", {"SKILL.md": "skill v3\n"})
    _undo(repo, env, cid)
    doc = _due(repo, env)
    assert doc["rollbacks"] == []
    assert doc["blocked"] == [
        {"id": cid, "summary": "SKILL.md self-edit — tighten the wrap", "why_blocked": "edited-since"}
    ]
    before = _tree(repo)
    with pytest.raises(ActionRefused) as exc:
        rollback(repo, env, cid, now=NOW, tz=TZ)
    assert exc.value.reason == "edited-since"
    assert _tree(repo) == before


_LONG = "".join(f"line {i}\n" for i in range(1, 31))


def test_a_later_edit_to_other_lines_does_not_block(repo, env):
    """I5: rollback is hunk-level. A later commit that edits other lines of the same SKILL.md (24 brain commits a
    month is normal) leaves the undo possible, and the later edit survives it."""
    _commit(repo, "scout: a longer SKILL.md", {"SKILL.md": _LONG}, date=NOW - timedelta(days=30))
    cid = _cid(_brain_edit(repo, days_ago=3, content=_LONG.replace("line 3\n", "line 3, tightened\n")))
    later = _LONG.replace("line 3\n", "line 3, tightened\n").replace("line 28\n", "line 28, by hand\n")
    _commit(repo, "scout: tweak the end of SKILL.md", {"SKILL.md": later})
    _undo(repo, env, cid)
    doc = _due(repo, env)
    assert [r["id"] for r in doc["rollbacks"]] == [cid] and doc["blocked"] == []
    assert rollback(repo, env, cid, now=NOW, tz=TZ) == ["SKILL.md"]
    assert (repo / "SKILL.md").read_text() == _LONG.replace("line 28\n", "line 28, by hand\n")


def test_a_later_edit_to_the_same_hunk_blocks_and_writes_nothing(repo, env):
    _commit(repo, "scout: a longer SKILL.md", {"SKILL.md": _LONG}, date=NOW - timedelta(days=30))
    cid = _cid(_brain_edit(repo, days_ago=3, content=_LONG.replace("line 3\n", "line 3, tightened\n")))
    _commit(repo, "scout: reword it again", {"SKILL.md": _LONG.replace("line 3\n", "line 3, reworded\n")})
    _undo(repo, env, cid)
    assert [b["why_blocked"] for b in _due(repo, env)["blocked"]] == ["edited-since"]
    before = _tree(repo)
    with pytest.raises(ActionRefused) as exc:
        rollback(repo, env, cid, now=NOW, tz=TZ)
    assert exc.value.reason == "edited-since" and _tree(repo) == before


_TWIN = "".join(f"boilerplate {i}\n" for i in range(1, 8))  # 7 lines: a hunk's ±3 lines of context fit inside
_TIGHT = _TWIN.replace("boilerplate 4\n", "boilerplate 4, tightened\n")


def _twin_skill(first: str, second: str) -> str:
    return f"# Skill\n\nintro\n\n{first}\nmiddle 1\nmiddle 2\nmiddle 3\nmiddle 4\n\n{second}\nend\n"


def _twin_change(repo: Path) -> str:
    """SKILL.md where the run change makes the first block byte-identical to the second (the boilerplate a brain
    file repeats). Returns the change id."""
    _commit(
        repo,
        "scout: a SKILL.md with a repeated block",
        {"SKILL.md": _twin_skill(_TWIN, _TIGHT)},
        date=NOW - timedelta(days=30),
    )
    return _cid(_brain_edit(repo, days_ago=3, content=_twin_skill(_TIGHT, _TIGHT)))


def test_an_undo_never_lands_on_an_identical_twin_block(repo, env):
    """R-a: the change's own line was edited again since, and an identical block (same lines, same context) stands
    elsewhere in the file. The undo must refuse, not rewrite the other copy."""
    cid = _twin_change(repo)
    reworded = _TWIN.replace("boilerplate 4\n", "boilerplate 4, reworded\n")
    _commit(repo, "scout: reword the first block again", {"SKILL.md": _twin_skill(reworded, _TIGHT)})
    _undo(repo, env, cid)
    from scout.changes import actions

    change = Change.from_row(next(r for r in _changes_rows(repo, env) if r["id"] == cid))
    assert actions.rollback_problem(repo, change, {"state": "testing"}) == "edited-since"
    before = (repo / "SKILL.md").read_bytes()
    with pytest.raises(ActionRefused) as exc:
        rollback(repo, env, cid, now=NOW, tz=TZ)
    assert exc.value.reason == "edited-since"
    assert (repo / "SKILL.md").read_bytes() == before


def test_an_undo_beside_an_edited_twin_block_restores_the_first_and_keeps_the_edit(repo, env):
    cid = _twin_change(repo)
    second = _TIGHT.replace("boilerplate 6\n", "boilerplate 6, by hand\n")
    _commit(repo, "scout: edit the second block", {"SKILL.md": _twin_skill(_TIGHT, second)})
    _undo(repo, env, cid)
    assert [r["id"] for r in _due(repo, env)["rollbacks"]] == [cid]
    assert rollback(repo, env, cid, now=NOW, tz=TZ) == ["SKILL.md"]
    assert (repo / "SKILL.md").read_text() == _twin_skill(_TWIN, second)


_SEP = "---\n"


@pytest.mark.parametrize(
    ("parent", "change", "since"),
    [
        # Adjacent twins: the run made the first block match the second, a later commit deleted the first. Deleting
        # either copy gives the same bytes, and a forward merge alone takes back the copy that was never the change's.
        (f"# Skill\n\n{_TWIN}{_TIGHT}end\n", f"# Skill\n\n{_TIGHT}{_TIGHT}end\n", f"# Skill\n\n{_TIGHT}end\n"),
        # Periodic twins (S B S B S, the separators repeat too), the first "B S" deleted.
        (f"{_SEP}{_TWIN}{_SEP}{_TIGHT}{_SEP}", f"{_SEP}{_TIGHT}{_SEP}{_TIGHT}{_SEP}", f"{_SEP}{_TIGHT}{_SEP}"),
    ],
    ids=["adjacent", "periodic"],
)
def test_an_undo_never_takes_back_the_other_copy_when_the_changes_own_block_was_deleted(
    repo, env, parent, change, since
):
    """I-1: the merge is also run on the line-reversed files, and the two must agree."""
    _commit(repo, "scout: a SKILL.md with a repeated block", {"SKILL.md": parent}, date=NOW - timedelta(days=30))
    cid = _cid(_brain_edit(repo, days_ago=3, content=change))
    _commit(repo, "scout: drop the first block", {"SKILL.md": since})
    assert _refused(repo, env, cid) == "edited-since"


def test_a_later_chmod_survives_the_undo(repo, env):
    """I-2: the mode merges like the content. The change didn't touch the bit, a later commit set it: it stays."""
    _commit(repo, "seed a script", {"scripts/run.sh": _LONG}, date=NOW - timedelta(days=30))
    cid = _cid(
        _commit(repo, "dreaming [22:12]: driver tweak", {"scripts/run.sh": _LONG.replace("line 3\n", "line 3!\n")})
    )
    (repo / "scripts/run.sh").chmod(0o755)
    _commit(repo, "scout: make it executable", {})
    scan(repo, since=None)
    _undo(repo, env, cid)
    assert rollback(repo, env, cid, now=NOW, tz=TZ) == ["scripts/run.sh"]
    assert (repo / "scripts/run.sh").read_text() == _LONG and os.access(repo / "scripts/run.sh", os.X_OK)
    assert "mode change" not in _git(repo, "diff", "--summary")


def test_a_later_chmod_at_a_renames_new_path_moves_back_with_it(repo, env):
    cid = _renaming_change(repo)
    (repo / "knowledge-base/b/a.md").chmod(0o755)
    _commit(repo, "scout: make the note executable", {})
    _undo(repo, env, cid)
    rollback(repo, env, cid, now=NOW, tz=TZ)
    assert (repo / "knowledge-base/a.md").read_text() == _LONG and os.access(repo / "knowledge-base/a.md", os.X_OK)


@pytest.mark.parametrize("direction", ["file to folder", "folder to file"])
def test_a_file_and_a_folder_trading_places_is_refused(repo, env, direction):
    """M-3: undoing it would mean removing a folder before writing a file there (or the reverse), which the undo
    doesn't do; it refuses as edited-since (no truer reason exists) and writes nothing."""
    first, second = ("scripts/x", "scripts/x/y") if direction == "file to folder" else ("scripts/x/y", "scripts/x")
    _commit(repo, "seed", {first: "one\n"}, date=NOW - timedelta(days=30))
    (repo / first).unlink()
    if direction == "folder to file":
        (repo / "scripts/x").rmdir()
    sha = _commit(
        repo, "dreaming [22:12]: SKILL.md self-edit — reshape a driver", {"SKILL.md": "v2\n", second: "two\n"}
    )
    scan(repo, since=None)
    assert _refused(repo, env, _cid(sha)) == "edited-since"


def test_an_undo_blocked_on_one_file_writes_no_other(repo, env):
    """M-4: all or nothing across files. SKILL.md would undo cleanly, but the driver's line was edited since."""
    sha = _commit(
        repo,
        "dreaming [22:12]: SKILL.md self-edit — and its driver",
        {"SKILL.md": "skill v2\n", "scripts/x.py": "print(2)\n"},
        date=NOW - timedelta(days=3),
    )
    _commit(repo, "scout: the driver by hand", {"scripts/x.py": "print(3)\n"})
    scan(repo, since=None)
    assert _refused(repo, env, _cid(sha)) == "edited-since"
    assert (repo / "SKILL.md").read_text() == "skill v2\n"


def _changes_rows(repo: Path, env: dict[str, str]) -> list[dict]:
    from scout.changes.report import changes_report

    return changes_report(repo, now=NOW, since=NOW - timedelta(days=60), env=env, tz=TZ)["changes"]


def _problem(repo: Path, env: dict[str, str], cid: str) -> str | None:
    from scout.changes import actions

    change = Change.from_row(next(r for r in _changes_rows(repo, env) if r["id"] == cid))
    return actions.rollback_problem(repo, change, {"state": "testing"})


def _refused(repo: Path, env: dict[str, str], cid: str) -> str:
    """The undo is refused, as `due` says and as `rollback` does, and nothing in the tree changed."""
    _undo(repo, env, cid)
    problem = _problem(repo, env, cid)
    assert problem is not None and [b["why_blocked"] for b in _due(repo, env)["blocked"]] == [problem]
    before = _tree(repo)
    links = {p: os.readlink(p) for p in repo.rglob("*") if p.is_symlink()}
    with pytest.raises(ActionRefused) as exc:
        rollback(repo, env, cid, now=NOW, tz=TZ)
    assert exc.value.reason == problem
    assert _tree(repo) == before and {p: os.readlink(p) for p in repo.rglob("*") if p.is_symlink()} == links
    return problem


def test_an_added_file_edited_since_is_not_removed(repo, env):
    cid = _cid(_commit(repo, "dreaming [22:12]: a new driver", {"scripts/new.py": "v1\n"}, date=NOW - timedelta(3)))
    _commit(repo, "scout: improve the driver by hand", {"scripts/new.py": "v2\n"})
    scan(repo, since=None)
    assert _refused(repo, env, cid) == "edited-since"


def _deleting_change(repo: Path) -> str:
    (repo / "scripts").mkdir()
    (repo / "scripts/old.sh").write_text("echo old\n")
    (repo / "scripts/old.sh").chmod(0o755)
    _commit(repo, "seed a script", {}, date=NOW - timedelta(days=30))
    sha = _commit(
        repo,
        "dreaming [22:12]: SKILL.md self-edit — drop the old driver",
        {"SKILL.md": "skill v2\n", "scripts/old.sh": None},
        date=NOW - timedelta(days=3),
    )
    (repo / "scripts").rmdir()  # git leaves no empty folder behind; the undo makes it again
    scan(repo, since=None)
    return _cid(sha)


def test_a_deleted_file_is_restored_with_its_mode_and_folder(repo, env):
    cid = _deleting_change(repo)
    _undo(repo, env, cid)
    assert rollback(repo, env, cid, now=NOW, tz=TZ) == ["SKILL.md", "scripts/old.sh"]
    assert (repo / "scripts/old.sh").read_text() == "echo old\n" and os.access(repo / "scripts/old.sh", os.X_OK)
    assert (repo / "SKILL.md").read_text() == "skill v1\n"


@pytest.mark.parametrize("what", ["a file, committed", "an empty folder", "a file where its folder was"])
def test_a_deleted_file_made_again_since_is_not_overwritten(repo, env, what):
    cid = _deleting_change(repo)
    if what == "a file, committed":
        _commit(repo, "scout: a new old.sh", {"scripts/old.sh": "echo new\n"})
    elif what == "an empty folder":
        (repo / "scripts/old.sh").mkdir(parents=True)  # git doesn't see an empty folder: status stays clean
    else:
        (repo / "scripts").write_text("not a folder\n")  # lstat of scripts/old.sh: not a directory
    assert _refused(repo, env, cid) == "edited-since"


def _renaming_change(repo: Path) -> str:
    _commit(repo, "scout: a longer note", {"knowledge-base/a.md": _LONG}, date=NOW - timedelta(days=30))
    (repo / "knowledge-base/b").mkdir()
    _git(repo, "mv", "knowledge-base/a.md", "knowledge-base/b/a.md")
    edited = _LONG.replace("line 3\n", "line 3, moved\n")
    sha = _commit(
        repo,
        "dreaming [22:12]: SKILL.md self-edit — file the note",
        {"SKILL.md": "skill v2\n", "knowledge-base/b/a.md": edited},
        date=NOW - timedelta(days=3),
    )
    scan(repo, since=None)
    return _cid(sha)


def test_a_rename_whose_old_path_was_made_again_is_refused(repo, env):
    cid = _renaming_change(repo)
    _commit(repo, "scout: a new a.md", {"knowledge-base/a.md": "a fresh note\n"})
    assert _refused(repo, env, cid) == "edited-since"


def test_a_rename_merges_later_edits_at_the_new_path_back_to_the_old_one(repo, env):
    cid = _renaming_change(repo)
    moved = (repo / "knowledge-base/b/a.md").read_text()
    _commit(
        repo, "scout: edit the filed note", {"knowledge-base/b/a.md": moved.replace("line 28\n", "line 28, later\n")}
    )
    _undo(repo, env, cid)
    rollback(repo, env, cid, now=NOW, tz=TZ)
    assert (repo / "knowledge-base/a.md").read_text() == _LONG.replace("line 28\n", "line 28, later\n")
    assert not (repo / "knowledge-base/b").exists()


def _binary_change(repo: Path) -> str:
    (repo / "scripts").mkdir()
    (repo / "scripts/blob.bin").write_bytes(b"\0v1\n" + _LONG.encode())
    _commit(repo, "seed a binary", {}, date=NOW - timedelta(days=30))
    (repo / "scripts/blob.bin").write_bytes(b"\0v1\n" + _LONG.replace("line 3\n", "line 3!\n").encode())
    sha = _commit(repo, "dreaming [22:12]: driver data", {}, date=NOW - timedelta(days=3))
    scan(repo, since=None)
    return _cid(sha)


def test_a_binary_file_is_restored_only_exactly_as_the_change_left_it(repo, env):
    cid = _binary_change(repo)
    _undo(repo, env, cid)
    assert rollback(repo, env, cid, now=NOW, tz=TZ) == ["scripts/blob.bin"]
    assert (repo / "scripts/blob.bin").read_bytes() == b"\0v1\n" + _LONG.encode()


def test_a_binary_file_changed_since_is_refused_even_on_other_lines(repo, env):
    cid = _binary_change(repo)
    later = (repo / "scripts/blob.bin").read_bytes().replace(b"line 28\n", b"line 28!\n")
    (repo / "scripts/blob.bin").write_bytes(later)
    _commit(repo, "scout: more driver data", {})
    assert _refused(repo, env, cid) == "edited-since"


def test_a_change_already_put_back_by_hand_is_edited_since(repo, env):
    """Every line the change made is back as it was (a commit not counted as its rollback): an undo would write
    nothing and the request would stay due every run."""
    cid = _cid(_brain_edit(repo, days_ago=3))
    _commit(repo, "scout: put SKILL.md back by hand", {"SKILL.md": "skill v1\n"})
    assert _refused(repo, env, cid) == "edited-since"


def _link(repo: Path, rel: str, target: str) -> None:
    path = repo / rel
    if path.is_symlink() or path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)


def test_an_added_symlink_is_removed_only_while_it_points_where_the_change_left_it(repo, env):
    _link(repo, "scripts/tool", "../README.md")
    cid = _cid(_commit(repo, "dreaming [22:12]: a tool link", {}, date=NOW - timedelta(days=3)))
    scan(repo, since=None)
    _undo(repo, env, cid)
    assert rollback(repo, env, cid, now=NOW, tz=TZ) == ["scripts/tool"]
    assert not (repo / "scripts/tool").is_symlink() and not (repo / "scripts").exists()


def test_an_added_symlink_pointed_elsewhere_since_is_refused(repo, env):
    _link(repo, "scripts/tool", "../README.md")
    cid = _cid(_commit(repo, "dreaming [22:12]: a tool link", {}, date=NOW - timedelta(days=3)))
    _link(repo, "scripts/tool", "../SKILL.md")
    _commit(repo, "scout: point the tool elsewhere", {})
    scan(repo, since=None)
    assert _refused(repo, env, cid) == "edited-since"


def _file_to_link_change(repo: Path) -> str:
    (repo / "scripts").mkdir()
    (repo / "scripts/tool.sh").write_text("echo tool\n")
    (repo / "scripts/tool.sh").chmod(0o755)
    _commit(repo, "seed a tool", {}, date=NOW - timedelta(days=30))
    _link(repo, "scripts/tool.sh", "../README.md")
    sha = _commit(repo, "dreaming [22:12]: SKILL.md self-edit — link the tool", {"SKILL.md": "skill v2\n"})
    scan(repo, since=None)
    return _cid(sha)


def test_a_file_the_change_made_a_symlink_comes_back_as_a_file(repo, env):
    cid = _file_to_link_change(repo)
    _undo(repo, env, cid)
    assert rollback(repo, env, cid, now=NOW, tz=TZ) == ["SKILL.md", "scripts/tool.sh"]
    tool = repo / "scripts/tool.sh"
    assert not tool.is_symlink() and tool.read_text() == "echo tool\n" and os.access(tool, os.X_OK)
    assert (repo / "README.md").read_text() == "readme\n"  # never written through the link


@pytest.mark.parametrize("since", ["retargeted", "made a file again"])
def test_a_symlink_the_change_made_and_someone_changed_since_is_refused(repo, env, since):
    cid = _file_to_link_change(repo)
    if since == "retargeted":
        _link(repo, "scripts/tool.sh", "../SKILL.md")
        _commit(repo, "scout: point the tool elsewhere", {})
    else:
        (repo / "scripts/tool.sh").unlink()
        _commit(repo, "scout: a real tool again", {"scripts/tool.sh": "echo tool\n"})
    assert _refused(repo, env, cid) == "edited-since"


def test_a_file_the_change_edited_and_someone_made_a_symlink_since_is_refused(repo, env):
    cid = _cid(_brain_edit(repo, days_ago=3))
    _link(repo, "SKILL.md", "README.md")
    _commit(repo, "scout: SKILL.md is a link now", {})
    assert _refused(repo, env, cid) == "edited-since"


def test_rollback_restores_only_the_changes_own_paths(repo, env):
    """I5: the session log, audit row and action items the same commit wrote are not part of the change."""
    sha = _commit(
        repo,
        "dreaming [22:12]: SKILL.md self-edit — with its bookkeeping",
        {
            "SKILL.md": "skill v2\n",
            "knowledge-base/scout-mistake-audit.md": "pattern 12 fixed\n",
            "action-items/action-items-2026-11-19.md": "- item\n",
            "knowledge-base/session-log/2026-11.md": "dreaming ran\n",
        },
    )
    scan(repo, since=None)
    cid = _cid(sha)
    _commit(repo, "dreaming [23:00]: session log", {"knowledge-base/session-log/2026-11.md": "dreaming ran twice\n"})
    _undo(repo, env, cid)
    assert rollback(repo, env, cid, now=NOW, tz=TZ) == ["SKILL.md"]
    assert (repo / "SKILL.md").read_text() == "skill v1\n"
    assert (repo / "knowledge-base/scout-mistake-audit.md").read_text() == "pattern 12 fixed\n"
    assert (repo / "action-items/action-items-2026-11-19.md").exists()
    assert (repo / "knowledge-base/session-log/2026-11.md").read_text() == "dreaming ran twice\n"


def test_a_blocked_undo_is_listed_until_announced(repo, env):
    """I5: a blocked owner undo is reported once; `changes announced --blocked` stops the wrap repeating it. The
    suspect list is separate, so an announced suspect whose undo is then blocked is still reported."""
    cid = _cid(_brain_edit(repo, days_ago=20))
    _hurt(repo, cid, days_ago=20)
    mark_announced(repo, [cid])  # announced as a suspect
    _commit(repo, "scout: rewrite SKILL.md", {"SKILL.md": "skill v3\n"})
    _undo(repo, env, cid)
    assert [(b["id"], b["why_blocked"]) for b in _due(repo, env)["blocked"]] == [(cid, "edited-since")]
    mark_announced(repo, [cid], blocked=True)
    doc = _due(repo, env)
    assert doc["blocked"] == [] and doc["rollbacks"] == [] and doc["suspect_unannounced"] == []


def test_a_suspect_says_why_undo_would_not_help(repo, env):
    """m2: the wrap words a suspect that `undo` would refuse (here: later edits overlap it) instead of offering it."""
    cid = _cid(_brain_edit(repo, days_ago=20))
    _hurt(repo, cid, days_ago=20)
    _commit(repo, "scout: rewrite SKILL.md", {"SKILL.md": "skill v3\n"})
    [suspect] = _due(repo, env)["suspect_unannounced"]
    assert (suspect["id"], suspect["undo_blocked"]) == (cid, "edited-since")


def test_an_uncommitted_edit_blocks_too(repo, env):
    cid = _cid(_brain_edit(repo))
    (repo / "SKILL.md").write_text("work in progress\n")
    _undo(repo, env, cid)
    assert [b["why_blocked"] for b in _due(repo, env)["blocked"]] == ["edited-since"]
    with pytest.raises(ActionRefused):
        rollback(repo, env, cid, now=NOW, tz=TZ)
    assert (repo / "SKILL.md").read_text() == "work in progress\n"


def test_a_governance_trailer_alone_does_not_make_a_change_immune(repo, env):
    """I2: an ordinary SKILL.md edit that claims `Scout-Change-Governance: yes` is still undoable."""
    cid = _cid(_brain_edit(repo, trailers="Scout-Change-Governance: yes"))
    _undo(repo, env, cid)
    doc = _due(repo, env)
    assert [(r["id"], r["reason"]) for r in doc["rollbacks"]] == [(cid, "owner-undo")] and doc["blocked"] == []
    assert rollback(repo, env, cid, now=NOW, tz=TZ) == ["SKILL.md"]


def _apply_and_commit(repo: Path, env: dict[str, str], gov: str) -> str:
    """What Step 1e does: apply the approved item and commit exactly the printed paths. Returns the change id."""
    paths = apply_governance(repo, env, gov)
    _git(repo, "add", "-A", "--", *paths)
    _git(repo, "commit", "-q", "-m", f"dreaming [22:40]: apply {gov}", date=NOW - timedelta(days=1))
    scan(repo, since=None)
    return _cid(_git(repo, "rev-parse", "HEAD"))


def test_the_commit_that_moves_an_approved_item_to_applied_is_governance(repo, env):
    gov = _pending(repo)
    _commit(repo, "dreaming [22:00]: governance-pending — skip red", {}, date=NOW - timedelta(days=2))
    _decide(repo, env, "approve", gov)
    cid = _apply_and_commit(repo, env, gov)
    _undo(repo, env, cid)
    doc = _due(repo, env)
    assert doc["rollbacks"] == [] and [(b["id"], b["why_blocked"]) for b in doc["blocked"]] == [(cid, "governance")]
    with pytest.raises(ActionRefused) as exc:
        rollback(repo, env, cid, now=NOW, tz=TZ)
    assert exc.value.reason == "governance"


def test_a_harness_fallback_item_is_deleted_on_apply_and_stays_undoable(repo, env):
    folder = repo / "governance-pending"
    folder.mkdir()
    (folder / "plain.md").write_text(_PENDING.replace("---\n\n```diff", "governance: false\n---\n\n```diff"))
    _commit(repo, "dreaming [22:00]: governance-pending — plain", {}, date=NOW - timedelta(days=2))
    _decide(repo, env, "approve", "gov-plain")
    assert apply_governance(repo, env, "gov-plain") == ["SKILL.md", "governance-pending/plain.md"]
    assert not (folder / "plain.md").exists() and not (folder / "applied").exists()
    _git(repo, "add", "-A", "--", "SKILL.md", "governance-pending/plain.md")
    _git(repo, "commit", "-q", "-m", "dreaming [22:40]: apply gov-plain", date=NOW - timedelta(days=1))
    scan(repo, since=None)
    cid = _cid(_git(repo, "rev-parse", "HEAD"))
    _undo(repo, env, cid)
    assert [r["id"] for r in _due(repo, env)["rollbacks"]] == [cid]


def test_an_interactive_change_is_not_a_run_change(repo, env):
    sha = _commit(repo, "scout: SKILL.md tweak", {"SKILL.md": "skill v2\n"})
    scan(repo, since=None)
    _undo(repo, env, _cid(sha))
    assert [b["why_blocked"] for b in _due(repo, env)["blocked"]] == ["not-a-run-change"]


def test_rollback_refuses_a_change_nobody_asked_to_undo(repo, env):
    cid = _cid(_brain_edit(repo))
    before = _tree(repo)
    with pytest.raises(ActionRefused):
        rollback(repo, env, cid, now=NOW, tz=TZ)
    with pytest.raises(ActionRefused):
        rollback(repo, env, "chg-ffffffffffff", now=NOW, tz=TZ)
    assert _tree(repo) == before


def test_unverified_rows_list_no_rollback_but_governance_still_counts(tmp_path, env):
    """R7: git can't be read, so change rows can't be trusted: no rollbacks, blocked or suspects; apply still lists."""
    vault = tmp_path / "not-a-repo"
    (vault / ".scout-logs").mkdir(parents=True)
    sha = "ab" * 20
    cid = _cid(sha)
    append_change(
        vault,
        Change(
            id=cid,
            ts=NOW - timedelta(days=1),
            kind="brain-edit",
            summary="a change",
            why=Why("unknown", "", inferred=True),
            commit=sha,
            files=("SKILL.md",),
            author_lane="dreaming",
        ),
    )
    _undo(vault, env, cid)
    gov = _pending(vault)
    _decide(vault, env, "approve", gov)
    doc = due(vault, env, now=NOW, tz=TZ)
    assert doc["verified"] is False
    assert doc["rollbacks"] == [] and doc["blocked"] == [] and doc["suspect_unannounced"] == []
    assert doc["apply"] == [gov]


def test_a_run_set_home_cannot_plant_owner_decisions_or_settings(
    repo, tmp_path, monkeypatch, _owner_store_follows_home
):
    """C1: the owner store comes from the account database. A run that sets HOME to a folder it wrote (owner rows,
    settings) is read as nothing; the genuine store under the account's home still counts."""
    import json
    import pwd
    import types

    from scout.changes import store
    from scout.changes.governance import decision, read_pending
    from scout.changes.settings import read_settings
    from scout.kb import hook_paths

    account = tmp_path / "account"
    account.mkdir()
    monkeypatch.setattr(store, "_account_home", _owner_store_follows_home)  # the real lookup, not the test patch
    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: types.SimpleNamespace(pw_dir=str(account)))

    cid = _cid(_brain_edit(repo, days_ago=20))
    _hurt(repo, cid, days_ago=20)
    gov = _pending(repo)
    digest = read_pending(repo)[0][0].hash
    fake = repo / ".scout-cache" / "fakehome"
    planted = fake / ".local" / "state" / "scout" / hook_paths.CHANGES_OWNER_NAME
    planted.mkdir(parents=True)
    key = store.vault_key(repo)
    rows = [
        {"ts": NOW.isoformat(), "kind": "approval", "surface": "scoutctl", "ref": ref, "vault_key": key}
        for ref in (f"undo:{cid}", f"approve:{gov}@{digest}")
    ]
    (planted / "engagement.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (planted / "settings.json").write_text(
        json.dumps({"schema": 1, "auto_rollback": "targeted", "review_days": 14, "calibrated_on": "2026-01-01"})
    )
    run_env = {"HOME": str(fake), "SCOUT_MODE": "dreaming"}
    assert store.owner_dir(run_env) == account / ".local" / "state" / "scout" / hook_paths.CHANGES_OWNER_NAME
    doc = due(repo, run_env, now=NOW, tz=TZ)
    assert doc["rollbacks"] == [] and doc["blocked"] == [] and doc["apply"] == []
    assert read_settings(run_env) == Settings()
    signals, _ = store.read_signals(repo, run_env)
    assert decision(signals, gov, digest) == "awaiting-ok"
    with pytest.raises(ActionRefused):
        rollback(repo, run_env, cid, now=NOW, tz=TZ)
    with pytest.raises(ActionRefused):
        apply_governance(repo, run_env, gov)
    # The genuine owner store (the account's) still counts, whatever HOME says.
    _undo(repo, {"HOME": str(fake)}, cid)
    assert (account / ".local/state/scout" / hook_paths.CHANGES_OWNER_NAME / "engagement.jsonl").exists()
    assert [r["id"] for r in due(repo, run_env, now=NOW, tz=TZ)["rollbacks"]] == [cid]


def test_the_account_home_lookup_never_falls_back_to_home(monkeypatch, _owner_store_follows_home):
    import pwd

    from scout.changes import store

    monkeypatch.setattr(store, "_account_home", _owner_store_follows_home)

    def missing(_uid):
        raise KeyError("no such user")

    monkeypatch.setattr(pwd, "getpwuid", missing)
    assert store._account_home() == store._NO_ACCOUNT_HOME
    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: type("P", (), {"pw_dir": ""})())
    assert store._account_home() == store._NO_ACCOUNT_HOME


def test_due_never_raises(tmp_path, env, monkeypatch):
    import scout.changes.report as report

    def broken(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(report, "changes_report", broken)
    doc = due(tmp_path, env, now=NOW, tz=TZ)
    assert doc["rollbacks"] == [] and doc["apply"] == [] and doc["verified"] is False


# --- governance apply / discard ------------------------------------------------------------------------------------

_PENDING = """---
summary: Let the briefing skip a carried red item
why: The same red item was carried for six days with no reply.
---

```diff
--- a/SKILL.md
+++ b/SKILL.md
@@ -1 +1 @@
-skill v1
+skill v1, governed
```
"""


def _pending(vault: Path, slug: str = "skip-red") -> str:
    folder = vault / "governance-pending"
    folder.mkdir(exist_ok=True)
    (folder / f"{slug}.md").write_text(_PENDING, encoding="utf-8")
    return f"gov-{slug}"


def test_apply_governance_patches_and_moves_the_file(repo, env):
    gov = _pending(repo)
    _decide(repo, env, "approve", gov)
    assert _due(repo, env)["apply"] == [gov]
    paths = apply_governance(repo, env, gov)
    assert paths == ["SKILL.md", "governance-pending/applied/skip-red.md", "governance-pending/skip-red.md"]
    assert (repo / "SKILL.md").read_text() == "skill v1, governed\n"
    assert not (repo / "governance-pending/skip-red.md").exists()
    assert (repo / "governance-pending/applied/skip-red.md").read_text() == _PENDING
    assert list((repo / ".scout-cache").iterdir()) == []  # the temp diff is gone
    assert _git(repo, "diff", "--cached", "--name-only") == ""
    assert _due(repo, env)["apply"] == []


def _decide_at(repo: Path, env: dict[str, str], verb: str, gov: str, ts: datetime) -> None:
    from scout.changes.governance import read_pending

    digest = next(p.hash for p in read_pending(repo)[0] if p.id == gov)
    append_signal(Signal(ts=ts, kind="approval", surface="scoutctl", ref=f"{verb}:{gov}@{digest}"), vault=repo, env=env)


def test_a_bound_approval_is_used_up_by_commit_order_not_dates(repo, env):
    """#747 review N1: the approval names the commit that filed the file, so a re-filing rides nothing, even when the
    re-filing's commits are dated before the approval (a run can set GIT_COMMITTER_DATE)."""
    from scout.changes.governance import decision_ref, filed_by, read_pending, state_of
    from scout.changes.store import read_signals

    gov = "gov-skip-red"
    files: dict[str, str | None] = {"governance-pending/skip-red.md": _PENDING}
    _commit(repo, "dreaming [22:00]: file it", files, date=NOW - timedelta(minutes=10))
    item = read_pending(repo)[0][0]
    filing = filed_by(repo, item)
    assert filing == _git(repo, "rev-parse", "HEAD")
    ref = decision_ref("approve", gov, item.hash, filing)
    append_signal(Signal(ts=NOW, kind="approval", surface="app", ref=ref), vault=repo, env=env)
    assert _due(repo, env)["apply"] == [gov]
    paths = apply_governance(repo, env, gov)
    _git(repo, "add", "-A", "--", *paths)
    past = NOW - timedelta(days=3)  # every later commit back-dated before the approval
    _git(repo, "commit", "-q", "-m", f"dreaming [22:12]: apply {gov}", date=past)
    _commit(repo, "scout: revert by hand", {"SKILL.md": "skill v1\n", "governance-pending/applied/skip-red.md": None},
            date=past)  # fmt: skip
    _commit(repo, "dreaming [22:30]: file it again", files, date=past)
    item = read_pending(repo)[0][0]
    assert filed_by(repo, item) not in (None, filing)
    signals, _ = read_signals(repo, env)
    assert state_of(repo, signals, item) == "awaiting-ok"
    assert _due(repo, env)["apply"] == []
    # Uncommitted as it is now: no filing, so a bound decision matches nothing.
    (repo / "governance-pending/skip-red.md").write_text(_PENDING.replace("governed", "edited"))
    assert filed_by(repo, read_pending(repo)[0][0]) is None


def test_a_filing_needs_the_file_committed_as_it_is(repo):
    from scout.changes.governance import filed_by, read_pending

    _commit(repo, "dreaming [22:00]: a broken file", {"governance-pending/skip-red.md": "---\n: [\n---\n"})
    gov = _pending(repo)  # the working tree holds a good one; HEAD's doesn't parse
    item = next(p for p in read_pending(repo)[0] if p.id == gov)
    assert filed_by(repo, item) is None


def test_an_unbound_approval_applied_in_the_same_second_is_used_up(repo, env):
    """N1: an older engine's approval row (no filing sha) has microseconds, git's commit time has none."""
    second = NOW.replace(microsecond=0)
    _commit(repo, "dreaming [22:00]: file it", {"governance-pending/skip-red.md": _PENDING},
            date=second - timedelta(minutes=1))  # fmt: skip
    gov = "gov-skip-red"
    _decide_at(repo, env, "approve", gov, second + timedelta(microseconds=700_000))
    paths = apply_governance(repo, env, gov)
    _git(repo, "add", "-A", "--", *paths)
    _git(repo, "commit", "-q", "-m", f"dreaming [22:12]: apply {gov}", date=second)
    _commit(repo, "scout: revert by hand", {"SKILL.md": "skill v1\n", "governance-pending/applied/skip-red.md": None},
            date=second + timedelta(seconds=1))  # fmt: skip
    _commit(repo, "dreaming [22:30]: file it again", {"governance-pending/skip-red.md": _PENDING},
            date=second + timedelta(seconds=2))  # fmt: skip
    assert _due(repo, env)["apply"] == []


def test_an_approval_is_used_up_once_applied(repo, env):
    """#747 review: the owner reverts an applied governance change by hand and a run re-files the identical file; the
    old approval must not apply it again. A fresh approval does."""
    _commit(repo, "dreaming [22:00]: governance-pending — skip red", {"governance-pending/skip-red.md": _PENDING},
            date=NOW - timedelta(minutes=10))  # fmt: skip
    gov = "gov-skip-red"
    _decide_at(repo, env, "approve", gov, NOW - timedelta(minutes=5))
    paths = apply_governance(repo, env, gov)
    _git(repo, "add", "-A", "--", *paths)
    _git(repo, "commit", "-q", "-m", f"dreaming [22:12]: apply {gov}", date=NOW - timedelta(minutes=4))
    # By hand: SKILL.md back, the applied record gone; then a run files the same item again.
    _commit(repo, "scout: revert the governance change", {"SKILL.md": "skill v1\n",
            "governance-pending/applied/skip-red.md": None}, date=NOW - timedelta(minutes=3))  # fmt: skip
    _commit(repo, "dreaming [22:30]: governance-pending — skip red", {"governance-pending/skip-red.md": _PENDING},
            date=NOW - timedelta(minutes=2))  # fmt: skip
    assert _due(repo, env)["apply"] == []
    with pytest.raises(ActionRefused, match="awaiting-ok"):
        apply_governance(repo, env, gov)
    from scout.changes.report import changes_report

    doc = changes_report(repo, now=NOW, since=NOW - timedelta(days=1), env=env, tz=TZ)
    assert [(n["id"], n["state"]) for n in doc["needs_ok"]] == [(gov, "awaiting-ok")]
    _decide_at(repo, env, "approve", gov, NOW - timedelta(minutes=1))
    assert _due(repo, env)["apply"] == [gov]


def test_a_rejection_is_used_up_once_discarded(repo, env):
    _commit(repo, "dreaming [22:00]: file it", {"governance-pending/skip-red.md": _PENDING},
            date=NOW - timedelta(minutes=10))  # fmt: skip
    gov = "gov-skip-red"
    _decide_at(repo, env, "reject", gov, NOW - timedelta(minutes=5))
    paths = discard_governance(repo, env, gov)
    _git(repo, "add", "-A", "--", *paths)
    _git(repo, "commit", "-q", "-m", f"dreaming [22:12]: discard {gov}", date=NOW - timedelta(minutes=4))
    _commit(repo, "dreaming [22:30]: file it again", {"governance-pending/skip-red.md": _PENDING},
            date=NOW - timedelta(minutes=2))  # fmt: skip
    assert _due(repo, env)["discard"] == []  # waiting for the owner again, not thrown away unseen


def test_unreadable_git_spends_every_decision(repo, env, monkeypatch):
    from scout.changes import governance, scan
    from scout.changes.store import read_signals

    gov = _pending(repo)
    _decide(repo, env, "approve", gov)
    item = governance.read_pending(repo)[0][0]
    assert governance.spent_at(repo, item) is None
    real = scan._git

    def no_log(vault, *args):
        return None if args[:1] == ("log",) else real(vault, *args)

    monkeypatch.setattr(scan, "_git", no_log)
    assert governance.spent_at(repo, item) == governance._ALL_SPENT
    assert governance.spent_at(repo.parent, item) is None  # not a repository: nothing was ever applied
    signals, _ = read_signals(repo, env)
    assert governance.state_of(repo, signals, item) == "awaiting-ok"


def test_a_removal_of_some_other_version_spends_nothing(repo, env):
    from scout.changes import governance

    _commit(repo, "file v1", {"governance-pending/skip-red.md": _PENDING.replace("governed", "v1 governed")},
            date=NOW - timedelta(minutes=10))  # fmt: skip
    _commit(repo, "drop v1", {"governance-pending/skip-red.md": None}, date=NOW - timedelta(minutes=9))
    _commit(repo, "file bad yaml", {"governance-pending/skip-red.md": "---\n: [\n---\n"},
            date=NOW - timedelta(minutes=8))  # fmt: skip
    _commit(repo, "drop it", {"governance-pending/skip-red.md": None}, date=NOW - timedelta(minutes=7))
    gov = _pending(repo)
    _decide(repo, env, "approve", gov)
    assert governance.spent_at(repo, governance.read_pending(repo)[0][0]) is None
    assert _due(repo, env)["apply"] == [gov]


def test_apply_without_approval_refuses(repo, env):
    gov = _pending(repo)
    with pytest.raises(ActionRefused):
        apply_governance(repo, env, gov)
    _decide(repo, env, "reject", gov)
    with pytest.raises(ActionRefused):
        apply_governance(repo, env, gov)
    with pytest.raises(ActionRefused):
        apply_governance(repo, env, "gov-nope")
    assert (repo / "SKILL.md").read_text() == "skill v1\n"
    assert (repo / "governance-pending/skip-red.md").exists()


def test_failed_patch_leaves_the_pending_file(repo, env):
    gov = _pending(repo)
    _decide(repo, env, "approve", gov)
    (repo / "SKILL.md").write_text("something else\n")
    with pytest.raises(ActionRefused) as exc:
        apply_governance(repo, env, gov)
    assert exc.value.reason == "patch does not apply"
    assert (repo / "governance-pending/skip-red.md").exists()
    assert not (repo / "governance-pending/applied").exists()
    assert list((repo / ".scout-cache").iterdir()) == []  # the temp diff is gone
    assert (repo / "SKILL.md").read_text() == "something else\n"


def test_apply_refuses_a_path_through_a_symlinked_folder(repo, env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "ext").symlink_to(outside)
    folder = repo / "governance-pending"
    folder.mkdir()
    (folder / "escape.md").write_text(
        "---\nsummary: s\nwhy: w\n---\n\n```diff\n--- /dev/null\n+++ b/ext/evil.md\n@@ -0,0 +1 @@\n+evil\n```\n"
    )
    _decide(repo, env, "approve", "gov-escape")
    with pytest.raises(ActionRefused) as exc:
        apply_governance(repo, env, "gov-escape")
    assert exc.value.reason == "path outside the vault"
    assert list(outside.iterdir()) == [] and (folder / "escape.md").exists()


def test_apply_refuses_a_symlinked_scout_cache(repo, env, tmp_path):
    """A run-planted `.scout-cache` link must not carry the diff (or its delete) out of the vault."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "skip-red.diff").write_text("owner's file\n")
    (repo / ".scout-cache").symlink_to(outside)
    gov = _pending(repo)
    _decide(repo, env, "approve", gov)
    with pytest.raises(ActionRefused) as exc:
        apply_governance(repo, env, gov)
    assert exc.value.reason == "path outside the vault"
    assert [p.name for p in outside.iterdir()] == ["skip-red.diff"]
    assert (outside / "skip-red.diff").read_text() == "owner's file\n"
    assert (repo / "SKILL.md").read_text() == "skill v1\n" and (repo / "governance-pending/skip-red.md").exists()


def test_apply_refuses_a_symlinked_applied_folder(repo, env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    gov = _pending(repo)
    (repo / "governance-pending/applied").symlink_to(outside)
    _decide(repo, env, "approve", gov)
    with pytest.raises(ActionRefused) as exc:
        apply_governance(repo, env, gov)
    assert exc.value.reason == "path outside the vault"
    assert list(outside.iterdir()) == []
    assert (repo / "governance-pending/skip-red.md").exists() and (repo / "SKILL.md").read_text() == "skill v1\n"
    assert not (repo / ".scout-cache").exists()  # refused before anything was written


def test_discard_refuses_a_symlinked_md(repo, env, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text(_PENDING)
    folder = repo / "governance-pending"
    folder.mkdir()
    (folder / "skip-red.md").symlink_to(outside)
    _decide(repo, env, "reject", "gov-skip-red", digest="0" * 16)  # the link isn't read, so it has no hash
    with pytest.raises(ActionRefused):
        discard_governance(repo, env, "gov-skip-red")
    assert (folder / "skip-red.md").is_symlink() and outside.read_text() == _PENDING


def test_discard_needs_a_rejection(repo, env):
    gov = _pending(repo)
    with pytest.raises(ActionRefused):
        discard_governance(repo, env, gov)
    _decide(repo, env, "approve", gov)
    with pytest.raises(ActionRefused):
        discard_governance(repo, env, gov)
    _decide(repo, env, "reject", gov)
    assert _due(repo, env)["discard"] == [gov]
    assert discard_governance(repo, env, gov) == ["governance-pending/skip-red.md"]
    assert not (repo / "governance-pending/skip-red.md").exists()
    assert _due(repo, env)["discard"] == []


# --- counts and announcements --------------------------------------------------------------------------------------


def test_engagement_tests_running_counts_only_testing_engagement_changes(repo, env):
    _brain_edit(repo, days_ago=30, trailers="Scout-Change-Targets: engagement", content="old engagement\n")  # done
    _brain_edit(repo, days_ago=3, trailers="Scout-Change-Targets: engagement", content="new engagement\n")
    _brain_edit(repo, days_ago=2, trailers="Scout-Change-Targets: pattern-12", content="pattern\n")
    _commit(repo, "scout: SKILL.md by hand\n\nScout-Change-Targets: engagement", {"SKILL.md": "mine\n"})
    scan(repo, since=None)
    assert _due(repo, env)["engagement_tests_running"] == 1


def test_suspects_are_announced_once(repo, env):
    cid = _cid(_brain_edit(repo, days_ago=20))
    _hurt(repo, cid, days_ago=20)
    doc = _due(repo, env)
    assert doc["suspect_unannounced"] == [
        {
            "id": cid,
            "summary": "SKILL.md self-edit — tighten the wrap",
            "reasons": ["named-negative"],
            "undo_blocked": None,
        }
    ]
    mark_announced(repo, [cid])
    assert (repo / ".scout-logs" / ANNOUNCED_NAME).exists()
    assert _due(repo, env)["suspect_unannounced"] == []
    mark_announced(repo, [])  # keeps what is there
    assert _due(repo, env)["suspect_unannounced"] == []
    with pytest.raises(ValueError):
        mark_announced(repo, ["../not-an-id"])


def test_calibration_report_due(repo, env):
    _brain_edit(repo, days_ago=30)  # done, review_on 16 days ago: not 28 yet
    assert _due(repo, env)["calibration_report_due"] is False
    _brain_edit(repo, days_ago=43, content="older\n")  # review_on 29 days ago
    assert _due(repo, env)["calibration_report_due"] is True
    (repo / "docs").mkdir()
    (repo / "docs/changes-calibration-report.md").write_text("report\n")
    assert _due(repo, env)["calibration_report_due"] is False
    (repo / "docs/changes-calibration-report.md").unlink()
    write_settings(Settings(calibrated_on=NOW.date()), env)
    assert _due(repo, env)["calibration_report_due"] is False


def test_git_calls_set_optional_locks_off(monkeypatch, repo, env):
    seen: list[dict[str, str] | None] = []
    real = subprocess.run

    def spy(cmd, *a, **k):
        if cmd and cmd[0] == "git":
            seen.append(k.get("env"))
        return real(cmd, *a, **k)

    cid = _cid(_brain_edit(repo))
    gov = _pending(repo)
    _decide(repo, env, "approve", gov)
    _undo(repo, env, cid)
    monkeypatch.setattr(subprocess, "run", spy)
    _due(repo, env)
    rollback(repo, env, cid, now=NOW, tz=TZ)
    (repo / "SKILL.md").write_text("skill v1\n")
    apply_governance(repo, env, gov)
    assert seen and all(e is not None and e.get("GIT_OPTIONAL_LOCKS") == "0" for e in seen)


# --- fail-closed branches (I8) ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        None,  # git failed
        b"not-a-meta-line\0SKILL.md\0",  # no leading colon
        b":100644 100644 a b M\0",  # a meta line with no path after it
        b":100644 100644 a b X\0SKILL.md\0",  # a status a commit diff never has
        b":120000 100644 a b T\0SKILL.md\0",  # a symlink in the parent would be written back
        b":100644 160000 a b M\0SKILL.md\0",  # a submodule in the commit
    ],
)
def test_modes_refuse_what_cannot_be_written_back(repo, monkeypatch, raw):
    from scout.changes import actions

    monkeypatch.setattr(actions, "_git_bytes", lambda *a, **k: raw)
    assert actions._sides(repo, "p" * 40, "c" * 40, ["SKILL.md"]) is None


def test_a_change_that_turned_a_symlink_into_a_file_is_unreadable(repo, env):
    (repo / "scripts").mkdir()
    (repo / "scripts/tool.sh").symlink_to("../README.md")
    _commit(repo, "seed a link", {}, date=NOW - timedelta(days=30))
    (repo / "scripts/tool.sh").unlink()
    sha = _commit(
        repo, "dreaming [22:12]: SKILL.md self-edit — a real tool", {"SKILL.md": "v2\n", "scripts/tool.sh": "echo\n"}
    )
    scan(repo, since=None)
    _undo(repo, env, _cid(sha))
    assert [b["why_blocked"] for b in _due(repo, env)["blocked"]] == ["git-unreadable"]


def test_plan_refusals(repo, monkeypatch):
    from scout.changes import actions

    assert actions._plan(repo, "not-a-sha") == "git-unreadable"
    assert actions._plan(repo, "e" * 40) == "git-unreadable"  # well-formed, not in the repository
    readme_only = _commit(repo, "dreaming [22:12]: README tweak", {"README.md": "new readme\n"})
    assert actions._plan(repo, readme_only) == "git-unreadable"  # no own paths to undo
    # A commit that isn't on HEAD (another branch).
    _git(repo, "checkout", "-q", "-b", "side")
    side = _commit(repo, "dreaming [22:12]: SKILL.md on a side branch", {"SKILL.md": "side\n"})
    _git(repo, "checkout", "-q", "-")
    assert actions._plan(repo, side) == "git-unreadable"
    # A vault that is a subfolder of its repository: git's paths aren't vault-relative.
    assert actions._plan(repo / "knowledge-base", readme_only) == "git-unreadable"
    # A rename (or any path) git's raw diff doesn't list: no side to undo from.
    sha = _brain_edit(repo)
    real = actions.name_status
    monkeypatch.setattr(actions, "name_status", lambda v, s: [*real(v, s), ("R100", ("scripts/a.py", "scripts/b.py"))])
    assert actions._plan(repo, sha) == "git-unreadable"
    monkeypatch.setattr(actions, "name_status", lambda v, s: [*real(v, s), ("A", ("scripts/ghost.py",))])
    assert actions._plan(repo, sha) == "git-unreadable"


@pytest.fixture
def tmpdir_for_merges(tmp_path, monkeypatch) -> Path:
    """The temp root the merges' folder is made in, watched: it must be empty after every undo."""
    from scout.changes import actions

    folder = tmp_path / "account-tmp"
    folder.mkdir()
    monkeypatch.setattr(actions, "_temp_root", lambda: str(folder))
    return folder


def _merge_inputs_spy(monkeypatch, check) -> list[str]:
    """Record every `merge-file` input path, calling ``check(paths)`` while the inputs exist."""
    from scout.changes import actions

    seen: list[str] = []
    real = actions._git_bytes

    def spy(vault, *args, **kw):
        if "merge-file" in args:
            names = list(args[args.index("--") + 1 :])
            check(names)
            seen.extend(names)
        return real(vault, *args, **kw)

    monkeypatch.setattr(actions, "_git_bytes", spy)
    return seen


def test_merge_temp_files_live_outside_the_vault_and_are_always_removed(repo, env, tmpdir_for_merges, monkeypatch):
    def check(names: list[str]) -> None:
        assert len(names) == 3 and all(Path(n).is_file() for n in names)
        [folder] = tmpdir_for_merges.iterdir()  # one fresh folder for the forward and the reversed merge
        assert all(Path(n).parent == folder for n in names)

    seen = _merge_inputs_spy(monkeypatch, check)
    cid = _cid(_brain_edit(repo, days_ago=3))
    _undo(repo, env, cid)
    assert _problem(repo, env, cid) is None  # a clean merge
    assert rollback(repo, env, cid, now=NOW, tz=TZ) == ["SKILL.md"]
    _commit(repo, "scout: rewrite SKILL.md", {"SKILL.md": "skill v3\n"})
    assert _problem(repo, env, cid) == "edited-since"  # a conflict
    assert len(seen) >= 18 and not any(s.startswith(str(repo)) for s in seen)
    assert list(tmpdir_for_merges.iterdir()) == [] and not list(repo.glob("**/scout-undo.*"))


def test_a_run_set_tmpdir_never_puts_merge_inputs_in_the_vault(repo, env, monkeypatch):
    """M-1: a run's environment doesn't choose where the merge inputs go (it could point TMPDIR into the vault, or
    at a folder it watches to swap an input before merge-file reads it)."""
    import tempfile

    planted = repo / ".scout-cache" / "tmp"
    planted.mkdir(parents=True)
    monkeypatch.setenv("TMPDIR", str(planted))
    monkeypatch.setattr(tempfile, "tempdir", None)  # re-read TMPDIR
    assert tempfile.gettempdir() == str(planted)
    seen = _merge_inputs_spy(monkeypatch, lambda names: None)
    cid = _cid(_brain_edit(repo, days_ago=3))
    _undo(repo, env, cid)
    assert rollback(repo, env, cid, now=NOW, tz=TZ) == ["SKILL.md"]
    assert seen and not any(Path(n).resolve().is_relative_to(repo.resolve()) for n in seen)
    assert list(planted.iterdir()) == []


def test_the_temp_root_ignores_the_environment(monkeypatch):
    from scout.changes import actions

    monkeypatch.setenv("TMPDIR", "/somewhere/a/run/chose")
    monkeypatch.setattr(actions.sys, "platform", "darwin")
    monkeypatch.setattr(actions.os, "confstr", lambda name: "/var/folders/xx/T/" if name == 65537 else None)
    assert actions._temp_root() == "/var/folders/xx/T/"
    for answer in (None, ""):
        monkeypatch.setattr(actions.os, "confstr", lambda name, a=answer: a)
        assert actions._temp_root() == "/tmp"

    def unknown(name):
        raise ValueError("unrecognized configuration name")

    monkeypatch.setattr(actions.os, "confstr", unknown)
    assert actions._temp_root() == "/tmp"
    monkeypatch.setattr(actions.sys, "platform", "linux")
    assert actions._temp_root() == "/tmp"


def test_a_temp_root_inside_the_vault_is_refused(repo, env, monkeypatch):
    from scout.changes import actions

    inside = repo / ".scout-cache"
    inside.mkdir()
    monkeypatch.setattr(actions, "_temp_root", lambda: str(inside))
    cid = _cid(_brain_edit(repo, days_ago=3))
    assert _refused(repo, env, cid) == "git-unreadable"
    assert list(inside.iterdir()) == []


def test_a_merge_file_error_is_edited_since(repo, env, tmpdir_for_merges, monkeypatch):
    """The shared runner can't tell a conflict (exit > 0) from an error (exit 255): both refuse as edited-since."""
    from scout.changes import actions

    cid = _cid(_brain_edit(repo, days_ago=3))
    real = actions._git_bytes
    monkeypatch.setattr(actions, "_git_bytes", lambda v, *a, **k: None if "merge-file" in a else real(v, *a, **k))
    assert _refused(repo, env, cid) == "edited-since"
    assert list(tmpdir_for_merges.iterdir()) == []


@pytest.mark.parametrize("fails", ["the folder", "an input"])
def test_temp_files_that_cannot_be_made_are_git_unreadable_and_leave_nothing(
    repo, env, tmpdir_for_merges, monkeypatch, fails
):
    import builtins
    import tempfile

    from scout.changes import actions

    cid = _cid(_brain_edit(repo, days_ago=3))

    def full(*a, **k):
        raise OSError(28, "No space left on device")

    if fails == "the folder":
        monkeypatch.setattr(tempfile, "mkdtemp", full)
    else:
        calls = {"n": 0}

        def second_input_fails(*a, **k):
            calls["n"] += 1
            return full() if calls["n"] % 3 == 2 else builtins.open(*a, **k)  # every merge loses an input

        monkeypatch.setattr(actions, "open", second_input_fails, raising=False)
    assert _refused(repo, env, cid) == "git-unreadable"
    assert list(tmpdir_for_merges.iterdir()) == []


def test_a_blob_git_cannot_read_is_git_unreadable(repo, env, monkeypatch):
    from scout.changes import actions

    cid = _cid(_brain_edit(repo, days_ago=3))
    real = actions._git_bytes
    monkeypatch.setattr(actions, "_git_bytes", lambda v, *a, **k: None if "cat-file" in a else real(v, *a, **k))
    assert _refused(repo, env, cid) == "git-unreadable"


def test_rollback_problem_when_git_status_fails(repo, monkeypatch):
    from scout.changes import actions

    sha = _brain_edit(repo)
    real = actions._git_bytes
    monkeypatch.setattr(actions, "_git_bytes", lambda vault, *a, **k: None if "status" in a else real(vault, *a, **k))
    change = Change(
        id=_cid(sha),
        ts=NOW,
        kind="brain-edit",
        summary="s",
        why=Why("unknown", "", inferred=True),
        commit=sha,
        files=("SKILL.md",),
        author_lane="dreaming",
    )
    assert actions.rollback_problem(repo, change, {"state": "testing"}) == "git-unreadable"


def _due_then(monkeypatch, after) -> None:
    """Run ``after`` once `due` has been worked out inside `rollback`, before it writes: a change racing the run."""
    from scout.changes import actions

    real = actions._safe_compute

    def racing(*a, **k):
        out = real(*a, **k)
        after()
        return out

    monkeypatch.setattr(actions, "_safe_compute", racing)


def test_rollback_refuses_what_changed_after_due(repo, env, monkeypatch):
    cid = _cid(_brain_edit(repo))
    _undo(repo, env, cid)
    _due_then(monkeypatch, lambda: (repo / "SKILL.md").write_text("edited meanwhile\n"))
    with pytest.raises(ActionRefused) as exc:
        rollback(repo, env, cid, now=NOW, tz=TZ)
    assert exc.value.reason == "edited-since" and (repo / "SKILL.md").read_text() == "edited meanwhile\n"


def test_rollback_refuses_a_folder_swapped_for_a_link_after_due(repo, env, monkeypatch, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sha = _commit(repo, "dreaming [22:12]: driver", {"scripts/x.py": "print(2)\n"})
    scan(repo, since=None)
    cid = _cid(sha)
    _undo(repo, env, cid)

    def swap() -> None:
        (repo / "scripts/x.py").unlink()
        (repo / "scripts").rmdir()
        (repo / "scripts").symlink_to(outside)

    _due_then(monkeypatch, swap)
    with pytest.raises(ActionRefused) as exc:
        rollback(repo, env, cid, now=NOW, tz=TZ)
    assert exc.value.reason == "path outside the vault" and list(outside.iterdir()) == []


def test_rollback_refuses_a_plan_that_went_away_after_due(repo, env, monkeypatch):
    from scout.changes import actions

    cid = _cid(_brain_edit(repo))
    _undo(repo, env, cid)
    _due_then(monkeypatch, lambda: monkeypatch.setattr(actions, "_plan", lambda *_a: "git-unreadable"))
    with pytest.raises(ActionRefused) as exc:
        rollback(repo, env, cid, now=NOW, tz=TZ)
    assert exc.value.reason == "git-unreadable"


def test_apply_refuses_a_git_path_and_an_existing_applied_file(repo, env):
    folder = repo / "governance-pending"
    folder.mkdir()
    (folder / "git.md").write_text(
        "---\nsummary: s\nwhy: w\n---\n\n```diff\n--- a/.git/config\n+++ b/.git/config\n@@ -1 +1 @@\n-a\n+b\n```\n"
    )
    _decide(repo, env, "approve", "gov-git")
    with pytest.raises(ActionRefused) as exc:
        apply_governance(repo, env, "gov-git")
    assert exc.value.reason == "path outside the vault"
    gov = _pending(repo)
    _decide(repo, env, "approve", gov)
    (folder / "applied").mkdir()
    (folder / "applied/skip-red.md").write_text("an earlier one\n")
    with pytest.raises(ActionRefused) as exc:
        apply_governance(repo, env, gov)
    assert "already exists" in exc.value.reason and (repo / "SKILL.md").read_text() == "skill v1\n"


def test_apply_refuses_a_pending_folder_linked_out_of_the_vault(repo, env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "skip-red.md").write_text(_PENDING)
    (repo / "governance-pending").symlink_to(outside)
    _decide(repo, env, "approve", "gov-skip-red")
    for act in (apply_governance, discard_governance):
        with pytest.raises(ActionRefused) as exc:
            act(repo, env, "gov-skip-red")
        assert exc.value.reason == "path outside the vault"
    assert (outside / "skip-red.md").read_text() == _PENDING


def test_apply_refuses_a_scout_cache_that_is_a_file(repo, env):
    (repo / ".scout-cache").write_text("not a folder\n")
    gov = _pending(repo)
    _decide(repo, env, "approve", gov)
    with pytest.raises(ActionRefused) as exc:
        apply_governance(repo, env, gov)
    assert exc.value.reason == "path outside the vault" and (repo / "SKILL.md").read_text() == "skill v1\n"


def test_apply_needs_the_vault_at_its_repository_top(repo, env):
    sub = repo / "inner"
    sub.mkdir()
    gov = _pending(sub)
    _decide(sub, env, "approve", gov)
    with pytest.raises(ActionRefused) as exc:
        apply_governance(sub, env, gov)
    assert exc.value.reason == "git-unreadable"


def test_announced_file_that_does_not_fit_is_empty_and_a_failed_save_raises(repo, monkeypatch):
    from scout.changes import actions, store

    path = repo / ".scout-logs" / ANNOUNCED_NAME
    path.parent.mkdir()
    for text in ("[1]", '{"schema": 2, "ids": ["chg-0123456789ab"]}', '{"schema": 1, "ids": "chg-0123456789ab"}'):
        path.write_text(text)
        assert actions.read_announced(repo) == set() and actions.read_announced(repo, blocked=True) == set()
    monkeypatch.setattr(store, "atomic_write_json", lambda *_a: False)
    with pytest.raises(OSError):
        mark_announced(repo, ["chg-0123456789ab"])


# --- all or nothing on the write side, and rollbacks git can verify (#747 review) -----------------------------------


def _two_renames(repo: Path) -> str:
    """A change that files two notes away: a/x.md → c/x.md and b/y.md → e/y.md."""
    seed: dict[str, str | None] = {"knowledge-base/a/x.md": "x\n", "knowledge-base/b/y.md": "y\n"}
    _commit(repo, "seed two notes", seed, date=NOW - timedelta(days=30))
    for old, new in (("a/x.md", "c/x.md"), ("b/y.md", "e/y.md")):
        (repo / "knowledge-base" / new).parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "mv", f"knowledge-base/{old}", f"knowledge-base/{new}")
        (repo / "knowledge-base" / old).parent.rmdir()  # as a fresh checkout has it: git keeps no empty folder
    message = "dreaming [22:12]: SKILL.md self-edit — file two notes"
    sha = _commit(repo, message, {"SKILL.md": "skill v2\n"}, date=NOW - timedelta(days=3))
    scan(repo, since=None)
    return _cid(sha)


def test_a_file_where_a_renames_old_folder_was_blocks_the_undo(repo, env):
    """A later commit put a FILE at knowledge-base/b, so b/y.md can't be written back (ENOTDIR, not "absent"): the
    undo is refused before anything is written, never half-applied with c/x.md left beside a/x.md."""
    cid = _two_renames(repo)
    _commit(repo, "scout: a file named b", {"knowledge-base/b": "now a file\n"})
    assert _refused(repo, env, cid) == "edited-since"


def test_a_write_that_fails_midway_leaves_the_tree_as_it_was(repo, env, monkeypatch):
    from scout.changes import actions

    cid = _two_renames(repo)
    _undo(repo, env, cid)
    before = _tree(repo)
    real, calls = os.replace, []

    def flaky(src, dst, *a, **k):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(actions.os, "replace", flaky)
    with pytest.raises(OSError, match="disk full"):
        rollback(repo, env, cid, now=NOW, tz=TZ)
    monkeypatch.setattr(actions.os, "replace", real)
    assert _tree(repo) == before  # the file already renamed into place was put back; no temp file is left
    assert not (repo / "knowledge-base/a").exists() and not (repo / "knowledge-base/b").exists()
    assert _git(repo, "status", "--porcelain", "--", "knowledge-base", "SKILL.md") == ""
    assert [r["id"] for r in _due(repo, env)["rollbacks"]] == [cid]  # still due, not edited-since
    rollback(repo, env, cid, now=NOW, tz=TZ)
    assert (repo / "knowledge-base/a/x.md").read_text() == "x\n"
    assert (repo / "knowledge-base/b/y.md").read_text() == "y\n"
    assert not (repo / "knowledge-base/c").exists() and not (repo / "knowledge-base/e").exists()


def test_a_write_that_fails_while_staging_changes_nothing(repo, env, monkeypatch):
    from scout.changes import actions

    cid = _two_renames(repo)
    _undo(repo, env, cid)
    before = _tree(repo)
    real, calls = actions._stage, []

    def flaky(target, data, mode):
        calls.append(target)
        if len(calls) == 3:
            raise OSError("no space left")
        return real(target, data, mode)

    monkeypatch.setattr(actions, "_stage", flaky)
    with pytest.raises(OSError, match="no space left"):
        rollback(repo, env, cid, now=NOW, tz=TZ)
    assert _tree(repo) == before
    assert not (repo / "knowledge-base/a").exists() and not (repo / "knowledge-base/b").exists()


def test_a_committed_rollback_of_renames_and_an_added_file_is_recognized(repo, env):
    """The rollback check works against git's trees: a restored rename (both sides), a removed added file and a
    merged brain file all match, so the undo request clears and the rollback is untested."""
    from scout.changes.report import changes_report

    _commit(repo, "seed a note", {"knowledge-base/a/x.md": "x\n"}, date=NOW - timedelta(days=30))
    (repo / "knowledge-base/c").mkdir()
    _git(repo, "mv", "knowledge-base/a/x.md", "knowledge-base/c/x.md")
    files: dict[str, str | None] = {"SKILL.md": "skill v2\n", "scripts/new.py": "x\n"}
    sha = _commit(repo, "dreaming [22:12]: SKILL.md self-edit — and a driver", files, date=NOW - timedelta(days=3))
    scan(repo, since=None)
    cid = _cid(sha)
    _undo(repo, env, cid)
    paths = rollback(repo, env, cid, now=NOW, tz=TZ)
    _git(repo, "add", "-A", "--", *paths)
    _git(repo, "commit", "-q", "-m", f"dreaming [22:30]: roll back {cid}\n\nScout-Change-Reverts: {cid}", date=NOW)
    scan(repo, since=None)
    doc = changes_report(repo, now=NOW, since=NOW - timedelta(days=60), env=env, tz=TZ)
    rows = {r["id"]: r for r in doc["changes"]}
    rolled = next(r for r in rows.values() if r["kind"] == "rollback")
    assert rolled["reverts"] == cid and rolled["test"]["state"] == "not-tested"
    assert rows[cid]["test"]["rolled_back_by"] == rolled["id"]
    assert _due(repo, env)["rollbacks"] == []


def test_a_rollback_sweeps_undo_temp_files_a_crash_left(repo, env):
    cid = _cid(_brain_edit(repo, days_ago=3))
    _undo(repo, env, cid)
    stale, fresh = repo / ".SKILL.md.0123456789ab.scout-undo", repo / ".SKILL.md.ba9876543210.scout-undo"
    for p in (stale, fresh):
        p.write_text("half\n")
    old = (NOW - timedelta(hours=1)).timestamp()
    os.utime(stale, (old, old))
    (repo / ".kept.scout-undo").mkdir()  # not a regular file: left alone
    os.utime(repo / ".kept.scout-undo", (old, old))
    rollback(repo, env, cid, now=NOW, tz=TZ)
    assert not stale.exists() and fresh.exists() and (repo / ".kept.scout-undo").is_dir()


def test_the_tree_reader_reads_a_commit_like_the_working_tree(repo):
    from scout.changes.actions import _tree_reader

    (repo / "scripts").mkdir()
    (repo / "scripts/run.sh").write_text("echo\n")
    (repo / "scripts/run.sh").chmod(0o755)
    (repo / "scripts/link").symlink_to("run.sh")
    sha = _commit(repo, "seed", {"knowledge-base/b": "a file\n"})
    read = _tree_reader(repo, sha)
    assert read("scripts/run.sh") == ("file", b"echo\n", 0o755)
    assert read("SKILL.md") == ("file", b"skill v1\n", 0o644)
    assert read("scripts/link") == ("link", b"run.sh", 0)
    assert read("scripts") == ("other", b"", 0)  # a folder
    assert read("knowledge-base/b/y.md") == ("other", b"", 0)  # under a file: it can't be written
    assert read("knowledge-base/nope/y.md") == ("absent", b"", 0)
    assert read("nope.md") == ("absent", b"", 0)
    assert _tree_reader(repo, "f" * 40)("SKILL.md") == ("other", b"", 0)  # git can't read it: fail closed


def test_restores_is_false_for_anything_it_cannot_confirm(repo):
    from scout.changes.actions import restores

    seed = _git(repo, "rev-parse", "HEAD")
    target = _commit(repo, "dreaming [22:12]: driver", {"scripts/new.py": "x\n", "SKILL.md": "skill v2\n"})
    assert not restores(repo, "not-a-sha", target)
    assert not restores(repo, seed, target)  # the root commit: no undo to work out
    assert not restores(repo, target, seed)  # the "rollback" comes before its target
    # Removes the added file but leaves SKILL.md as the change made it: not the undo.
    half = _commit(repo, "dreaming [22:30]: half", {"scripts/new.py": None})
    assert not restores(repo, target, half)
    # Puts SKILL.md back but keeps the added file: a path the undo removes is still there.
    _commit(repo, "scout: again", {"scripts/new.py": "x\n"})
    kept = _commit(repo, "dreaming [22:40]: kept", {"SKILL.md": "skill v1\n"})
    assert not restores(repo, target, kept)
    # Its parent already had every line back: the undo is worked out as edited-since.
    _commit(repo, "scout: by hand", {"scripts/new.py": None})
    nothing = _commit(repo, "dreaming [22:50]: nothing", {"README.md": "r2\n"})
    assert not restores(repo, target, nothing)


def test_staging_retries_a_taken_name_and_cleans_up_a_failed_write(tmp_path, monkeypatch):
    from scout.changes import actions

    target = tmp_path / "f.md"
    names = iter([b"\0" * 6, b"\0" * 6, b"\1" * 6])
    monkeypatch.setattr(actions.os, "urandom", lambda _n: next(names))
    first = actions._stage(target, b"one", 0o644)
    second = actions._stage(target, b"two", 0o755)  # the first name is taken: the next one is used
    assert first != second and second.read_bytes() == b"two" and os.access(second, os.X_OK)
    monkeypatch.setattr(actions.os, "urandom", lambda _n: b"\2" * 6)

    def broken(*_a):
        raise OSError("read-only")

    monkeypatch.setattr(actions.os, "fchmod", broken)
    with pytest.raises(OSError, match="read-only"):
        actions._stage(target, b"three", 0o644)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([first.name, second.name])


def test_putting_back_restores_a_file_a_link_or_nothing(tmp_path):
    from scout.changes.actions import _put_back

    path = tmp_path / "x"
    path.write_text("new")
    _put_back(path, ("file", b"old", 0o755))
    assert path.read_bytes() == b"old" and os.access(path, os.X_OK)
    _put_back(path, ("link", b"elsewhere", 0))
    assert os.readlink(path) == "elsewhere"
    _put_back(path, ("absent", b"", 0))
    assert not os.path.lexists(path)
