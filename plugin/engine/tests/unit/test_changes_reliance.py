from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scout.changes.consent import set_out_of_vault
from scout.changes.reliance import record_reliance, surfaces_used
from scout.changes.store import owner_dir
from scout.hooks import session_tokens
from scout.hooks.run_context import RunContext

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
    env = {"HOME": str(home)}
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
    env = {"HOME": str(home)}
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
