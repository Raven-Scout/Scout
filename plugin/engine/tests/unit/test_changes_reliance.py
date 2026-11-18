from __future__ import annotations

import hashlib
import io
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scout.changes.consent import set_out_of_vault
from scout.changes.reliance import record_reliance, surfaces_used
from scout.changes.report import engagement_report
from scout.changes.store import owner_dir
from scout.hooks import session_tokens
from scout.hooks.run_context import RunContext
from scout.scripts.engine_pointer import EnginePointer, write_pointer

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
SESSION_ID = "sess-secret-123"


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "Scout"
    (v / "knowledge-base").mkdir(parents=True)
    (v / "action-items").mkdir()
    (v / ".scout-logs").mkdir()
    return v


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    monkeypatch.delenv("SCOUT_READER", raising=False)
    return h


@pytest.fixture(autouse=True)
def _vault_env(vault, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))


@pytest.fixture
def other_repo(tmp_path):
    r = tmp_path / "other-repo"
    r.mkdir()
    return r


def transcript(*, cwd: Path, tool_uses=(), user_texts=()) -> Path:
    rows = [{"cwd": str(cwd), "message": {"role": "user", "content": t}} for t in user_texts]
    for name, inp in tool_uses:
        rows.append({"message": {"role": "assistant", "content": [{"type": "tool_use", "name": name, "input": inp}]}})
    path = cwd / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def payload(t: Path, cwd: Path) -> dict:
    return {"session_id": SESSION_ID, "transcript_path": str(t), "cwd": str(cwd)}


def owner_ctx(vault: Path) -> RunContext:
    return RunContext(vault, None, None)


def test_in_vault_session_counts_kb_and_skill(vault, home):
    t = transcript(
        cwd=vault,
        tool_uses=[("Read", {"file_path": str(vault / "knowledge-base/people.md")})],
        user_texts=["<command-name>/scout-work</command-name>"],
    )
    assert surfaces_used(t, vault, cwd=str(vault)) == {"vault-session", "kb", "skill"}


def test_symlinked_knowledge_base_counts_as_kb(vault, home, other_repo):
    (vault / "knowledge_base").symlink_to(vault / "knowledge-base")
    t = transcript(cwd=other_repo, tool_uses=[("Grep", {"path": str(vault / "knowledge_base"), "pattern": "x"})])
    assert surfaces_used(t, vault, cwd=str(other_repo)) == {"kb"}


def test_skill_tool_and_scoutctl(vault, home, other_repo):
    t = transcript(
        cwd=other_repo,
        tool_uses=[("Skill", {"skill": "scout:scout-work"}), ("Bash", {"command": "scoutctl kb delta"})],
    )
    assert surfaces_used(t, vault, cwd=str(other_repo)) == {"skill", "scoutctl"}


def test_out_of_vault_needs_consent(vault, home, other_repo):
    t = transcript(
        cwd=other_repo, tool_uses=[("Read", {"file_path": str(vault / "action-items/action-items-2026-11-17.md")})]
    )
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    assert record_reliance(payload(t, other_repo), t, owner_ctx(vault), env, now=NOW) == 0
    set_out_of_vault(True, env)
    assert record_reliance(payload(t, other_repo), t, owner_ctx(vault), env, now=NOW) == 1


def test_runs_never_count(vault, home):
    t = transcript(cwd=vault, tool_uses=[("Bash", {"command": "scoutctl kb delta"})])
    ctx = RunContext(vault, "dreaming", "prompt")  # prompt-named run with SCOUT_MODE unset
    assert record_reliance(payload(t, vault), t, ctx, {"HOME": str(home)}, now=NOW) == 0
    assert not (owner_dir({"HOME": str(home)}) / "engagement.jsonl").exists()


def test_non_owner_session_never_counts(vault, home):
    t = transcript(cwd=vault, tool_uses=[("Bash", {"command": "scoutctl kb delta"})])
    env = {"HOME": str(home), "SCOUT_READER": "agent"}
    assert record_reliance(payload(t, vault), t, owner_ctx(vault), env, now=NOW) == 0


def test_no_transcript_writes_nothing(vault, home):
    assert (
        record_reliance({"session_id": "s", "cwd": str(vault)}, None, owner_ctx(vault), {"HOME": str(home)}, now=NOW)
        == 0
    )


def test_rows_hold_no_paths_or_text(vault, home):
    t = transcript(
        cwd=vault,
        tool_uses=[("Read", {"file_path": str(vault / "knowledge-base/people.md")})],
        user_texts=["please look at my secret plan"],
    )
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    assert record_reliance(payload(t, vault), t, owner_ctx(vault), env, now=NOW) == 2
    text = (owner_dir(env) / "engagement.jsonl").read_text()
    rows = [json.loads(line) for line in text.splitlines()]
    assert {r["surface"] for r in rows} == {"vault-session", "kb"}
    assert all(r["kind"] == "session-reliance" and len(r["session"]) == 16 for r in rows)
    for banned in ("knowledge-base", "people.md", SESSION_ID, "secret plan", str(vault.parent)):
        assert banned not in text


def test_stop_hook_survives_garbage_transcript(vault, home, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(session_tokens, "_POLL_ATTEMPTS", 1)
    tracker = tmp_path / "tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b"\x00not json")
    body = json.dumps({"session_id": SESSION_ID, "transcript_path": str(bad), "cwd": str(vault)})
    event = session_tokens.run(stdin=io.StringIO(body))
    assert event is not None
    assert len(tracker.read_text().splitlines()) == 1


def test_stop_hook_records_reliance_and_survives_failure(vault, home, tmp_path, monkeypatch):
    tracker = tmp_path / "tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))
    t = transcript(cwd=vault, tool_uses=[("Bash", {"command": "scoutctl status"})])
    body = json.dumps(payload(t, vault))
    session_tokens.run(stdin=io.StringIO(body))
    assert (owner_dir({"HOME": str(home)}) / "engagement.jsonl").exists()

    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr("scout.changes.reliance.record_reliance", boom)
    assert session_tokens.run(stdin=io.StringIO(body)) is not None


def test_namespaced_scout_slash_commands_count_as_skill(vault, home, other_repo):
    for text in ("<command-name>/scout:scout-briefing</command-name>", "/scout:scout-update"):
        t = transcript(cwd=other_repo, user_texts=[text])
        assert surfaces_used(t, vault, cwd=str(other_repo)) == {"skill"}, text
    for text in ("<command-name>/superpowers:brainstorming</command-name>", "/scouting-trip", "/other:scout-x"):
        t = transcript(cwd=other_repo, user_texts=[text])
        assert surfaces_used(t, vault, cwd=str(other_repo)) == set(), text


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def git_vault(vault):
    _git(vault, "init", "-q")
    _git(vault, "config", "user.name", "Scout Test")
    _git(vault, "config", "user.email", "scout@example.invalid")
    _git(vault, "config", "commit.gpgsign", "false")
    (vault / "scout-config.yaml").write_text("schedule: {}\n")
    _git(vault, "add", "scout-config.yaml")
    _git(vault, "commit", "-q", "-m", "init")
    return vault


def add_worktree(vault: Path, path: Path) -> Path:
    _git(vault, "worktree", "add", "-q", "--detach", str(path))
    (path / "knowledge-base").mkdir()
    (path / "action-items").mkdir()
    return path


def point_engine_at(home: Path, vault: Path) -> None:
    pointer = EnginePointer(
        version="0.0.0",
        engine_root="/engine",
        python="/engine/.venv/bin/python",
        scoutctl="/engine/bin/scoutctl",
        vault=str(vault),
        managed_by="dev",
        written_at="2026-11-17T00:00:00Z",
    )
    write_pointer(pointer, home=home)


def test_worktree_session_is_keyed_to_the_installed_vault(git_vault, home, monkeypatch):
    """A session in <vault>/.claude/worktrees/<name> (its own scout-config.yaml) counts for the installed vault."""
    wt = add_worktree(git_vault, git_vault / ".claude" / "worktrees" / "wt")
    monkeypatch.delenv("SCOUT_DATA_DIR")
    point_engine_at(home, git_vault)
    monkeypatch.chdir(wt)  # the hook process runs in the session's cwd
    t = transcript(cwd=wt, tool_uses=[("Read", {"file_path": str(wt / "knowledge-base/people.md")})])
    env = dict(HOME=str(home))  # no SCOUT_DATA_DIR: the engine pointer names the vault
    assert record_reliance(payload(t, wt), t, owner_ctx(wt), env, now=NOW) == 2
    doc = engagement_report(git_vault, env, now=NOW, since=NOW - timedelta(days=1), tz=ZoneInfo("UTC"))
    assert doc["totals"]["reliance"] == 2
    assert doc["days"][-1]["by_surface"]["reliance"] == {"kb": 1, "vault-session": 1}


def test_worktree_outside_the_vault_folder_is_in_vault(git_vault, home, tmp_path):
    """A linked worktree of the vault's repo anywhere on disk is the vault: no out-of-vault consent needed."""
    wt = add_worktree(git_vault, tmp_path / "elsewhere" / "wt")
    t = transcript(cwd=wt, tool_uses=[("Read", {"file_path": str(wt / "action-items/action-items-2026-11-17.md")})])
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(git_vault)}
    assert record_reliance(payload(t, wt), t, owner_ctx(git_vault), env, now=NOW) == 2
    assert {r["surface"] for r in _rows(home)} == {"vault-session", "action-items"}


def test_main_vault_session_reading_a_worktree_kb_counts_as_kb(git_vault, home, tmp_path, other_repo):
    wt = add_worktree(git_vault, tmp_path / "elsewhere" / "wt")
    t = transcript(cwd=other_repo, tool_uses=[("Grep", {"path": str(wt / "knowledge-base"), "pattern": "x"})])
    assert surfaces_used(t, git_vault, cwd=str(other_repo)) == {"kb"}


def test_unrelated_repo_with_a_vault_marker_is_not_the_vault(git_vault, home, tmp_path):
    other = tmp_path / "other-vault"
    other.mkdir()
    _git(other, "init", "-q")
    (other / "scout-config.yaml").write_text("x: 1\n")
    t = transcript(cwd=other, tool_uses=[("Bash", {"command": "scoutctl status"})])
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(git_vault)}
    assert record_reliance(payload(t, other), t, owner_ctx(git_vault), env, now=NOW) == 0


def _rows(home: Path) -> list[dict]:
    path = owner_dir({"HOME": str(home)}) / "engagement.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_repeated_stops_count_each_surface_once(vault, home):
    t = transcript(cwd=vault, tool_uses=[("Read", {"file_path": str(vault / "knowledge-base/people.md")})])
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    assert record_reliance(payload(t, vault), t, owner_ctx(vault), env, now=NOW) == 2
    assert record_reliance(payload(t, vault), t, owner_ctx(vault), env, now=NOW) == 0
    assert len(_rows(home)) == 2


def test_later_turn_writes_only_the_new_surface(vault, home):
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    t = transcript(cwd=vault, tool_uses=[("Read", {"file_path": str(vault / "knowledge-base/people.md")})])
    record_reliance(payload(t, vault), t, owner_ctx(vault), env, now=NOW)
    t = transcript(
        cwd=vault,
        tool_uses=[
            ("Read", {"file_path": str(vault / "knowledge-base/people.md")}),
            ("Bash", {"command": "scoutctl status"}),
        ],
    )
    assert record_reliance(payload(t, vault), t, owner_ctx(vault), env, now=NOW) == 1
    assert [r["surface"] for r in _rows(home)][-1] == "scoutctl"


def test_corrupt_marker_is_treated_as_empty(vault, home):
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    marker = owner_dir(env) / "reliance-seen.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{not json")
    t = transcript(cwd=vault, tool_uses=[("Bash", {"command": "scoutctl status"})])
    assert record_reliance(payload(t, vault), t, owner_ctx(vault), env, now=NOW) == 2
    assert json.loads(marker.read_text())["schema"] == 1


def test_stale_marker_entries_are_pruned(vault, home):
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    marker = owner_dir(env) / "reliance-seen.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    old = {"surfaces": ["kb"], "at": "2026-11-01T00:00:00+00:00"}
    marker.write_text(json.dumps({"schema": 1, "sessions": {"oldsession0000000": old}}))
    t = transcript(cwd=vault, tool_uses=[("Bash", {"command": "scoutctl status"})])
    record_reliance(payload(t, vault), t, owner_ctx(vault), env, now=NOW)
    assert "oldsession0000000" not in json.loads(marker.read_text())["sessions"]


def _raw_transcript(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "raw.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def _assistant(*blocks) -> str:
    return json.dumps({"message": {"role": "assistant", "content": list(blocks)}})


def test_surfaces_ignore_malformed_transcript_shapes(vault, home, other_repo, tmp_path):
    outside = str(other_repo / "elsewhere.md")
    t = _raw_transcript(
        tmp_path,
        [
            "[1, 2]",  # valid JSON, not a row
            json.dumps({"message": "not an object"}),
            json.dumps({"message": {"role": "user", "content": 7}}),
            _assistant("a bare string", {"type": "text", "text": "hi"}),
            _assistant({"type": "tool_use", "name": "Read", "input": "nope"}),
            _assistant({"type": "tool_use", "name": "Read", "input": {"file_path": outside}}),
            _assistant({"type": "tool_use", "name": "Read", "input": {"file_path": "bad\0path"}}),
            _assistant({"type": "tool_use", "name": "Skill", "input": {"skill": "superpowers:brainstorming"}}),
            _assistant({"type": "tool_use", "name": "Skill", "input": {"skill": None}}),
            _assistant({"type": "tool_use", "name": "Bash", "input": {"command": ["scoutctl"]}}),
            _assistant({"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}),
            _assistant({"type": "tool_use", "name": "WebFetch", "input": {"url": "https://example.com"}}),
        ],
    )
    assert surfaces_used(t, vault, cwd=str(other_repo)) == set()


def test_surfaces_with_no_cwd_or_unresolvable_cwd_are_not_a_vault_session(vault, home, other_repo):
    t = transcript(cwd=other_repo, tool_uses=[("Bash", {"command": "scoutctl status"})])
    assert surfaces_used(t, vault, cwd="") == {"scoutctl"}
    assert surfaces_used(t, vault, cwd="bad\0cwd") == {"scoutctl"}


def test_missing_transcript_has_no_surfaces(vault, home, other_repo, tmp_path):
    assert surfaces_used(tmp_path / "gone.jsonl", vault, cwd=str(other_repo)) == set()


def test_marker_with_non_object_sessions_is_empty(vault, home):
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    marker = owner_dir(env) / "reliance-seen.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"schema": 1, "sessions": ["kb"]}))
    t = transcript(cwd=vault, tool_uses=[("Bash", {"command": "scoutctl status"})])
    assert record_reliance(payload(t, vault), t, owner_ctx(vault), env, now=NOW) == 2


def test_marker_entries_without_a_readable_time_are_dropped(vault, home):
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    marker = owner_dir(env) / "reliance-seen.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    sessions = {"garbled00000000": {"surfaces": ["kb"], "at": "sometime"}, "missing0000000": {"surfaces": ["kb"]}}
    marker.write_text(json.dumps({"schema": 1, "sessions": sessions}))
    t = transcript(cwd=vault, tool_uses=[("Bash", {"command": "scoutctl status"})])
    record_reliance(payload(t, vault), t, owner_ctx(vault), env, now=NOW)
    assert set(json.loads(marker.read_text())["sessions"]) == {hashlib.sha256(SESSION_ID.encode()).hexdigest()[:16]}


@pytest.mark.parametrize("failing", ["replace", "mkstemp"])
def test_marker_write_failure_is_swallowed_and_leaves_no_temp_file(vault, home, monkeypatch, failing):
    from scout.changes import reliance

    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}

    def boom(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(reliance.os if failing == "replace" else reliance.tempfile, failing, boom)
    t = transcript(cwd=vault, tool_uses=[("Bash", {"command": "scoutctl status"})])
    assert record_reliance(payload(t, vault), t, owner_ctx(vault), env, now=NOW) == 2
    assert not list(owner_dir(env).glob("reliance-seen*"))
    assert not list(owner_dir(env).glob(".reliance-seen*"))
