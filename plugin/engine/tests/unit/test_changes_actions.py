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


def _decide(repo: Path, env: dict[str, str], verb: str, gov_id: str) -> None:
    append_signal(Signal(ts=NOW, kind="approval", surface="scoutctl", ref=f"{verb}:{gov_id}"), vault=repo, env=env)


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


def test_auto_modes_need_calibration_to_act(repo, env):
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


def test_an_owner_undo_and_an_auto_match_list_the_change_once(repo, env):
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


def test_an_uncommitted_edit_blocks_too(repo, env):
    cid = _cid(_brain_edit(repo))
    (repo / "SKILL.md").write_text("work in progress\n")
    _undo(repo, env, cid)
    assert [b["why_blocked"] for b in _due(repo, env)["blocked"]] == ["edited-since"]
    with pytest.raises(ActionRefused):
        rollback(repo, env, cid, now=NOW, tz=TZ)
    assert (repo / "SKILL.md").read_text() == "work in progress\n"


def test_governance_change_is_blocked(repo, env):
    cid = _cid(_brain_edit(repo, trailers="Scout-Change-Governance: yes"))
    _undo(repo, env, cid)
    doc = _due(repo, env)
    assert doc["rollbacks"] == [] and [(b["id"], b["why_blocked"]) for b in doc["blocked"]] == [(cid, "governance")]
    with pytest.raises(ActionRefused) as exc:
        rollback(repo, env, cid, now=NOW, tz=TZ)
    assert exc.value.reason == "governance"


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
    assert not (repo / ".scout-cache/skip-red.diff").exists()
    assert _git(repo, "diff", "--cached", "--name-only") == ""
    assert _due(repo, env)["apply"] == []


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
    assert not (repo / ".scout-cache/skip-red.diff").exists()
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
        {"id": cid, "summary": "SKILL.md self-edit — tighten the wrap", "reasons": ["named-negative"]}
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
