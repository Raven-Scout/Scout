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
from scout.changes.reliance import _rows as _real_rows
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


# --- hook latency: the cheap checks go first (coordinator review item 4) ------------------------------------


@pytest.fixture
def probes(monkeypatch):
    """Counts `git worktree list` (vault_roots) calls, and fails the test if the transcript is ever opened."""
    from scout.changes import reliance

    calls = {"roots": 0, "transcript": 0}
    real_roots = reliance.vault_roots

    def counting_roots(vault):
        calls["roots"] += 1
        return real_roots(vault)

    def no_transcript(path):
        calls["transcript"] += 1
        raise AssertionError(f"transcript opened: {path}")

    monkeypatch.setattr(reliance, "vault_roots", counting_roots)
    monkeypatch.setattr(reliance, "_rows", no_transcript)
    return calls


@pytest.mark.parametrize("answer", [None, False])
def test_unconsented_out_of_vault_session_never_opens_its_transcript(vault, home, other_repo, probes, answer):
    """Not asked, or an explicit no: nothing recorded, the transcript never read, and git asked once per TTL."""
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    if answer is not None:
        set_out_of_vault(answer, env)
    t = transcript(cwd=other_repo, tool_uses=[("Read", {"file_path": str(vault / "knowledge-base/people.md")})])
    for minutes in (0, 1, 5):
        when = NOW + timedelta(minutes=minutes)
        assert record_reliance(payload(t, other_repo), t, owner_ctx(vault), env, now=when) == 0
    assert probes == {"roots": 1, "transcript": 0}
    assert not (owner_dir(env) / "engagement.jsonl").exists()
    assert not (owner_dir(env) / "reliance-seen.json").exists()


def test_explicit_no_records_nothing_even_for_scout_surfaces(vault, home, other_repo):
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    set_out_of_vault(False, env)
    t = transcript(
        cwd=other_repo,
        tool_uses=[("Skill", {"skill": "scout:scout-work"}), ("Bash", {"command": "scoutctl kb delta"})],
        user_texts=["/scout-work"],
    )
    assert record_reliance(payload(t, other_repo), t, owner_ctx(vault), env, now=NOW) == 0
    assert _rows(home) == []


def test_non_owner_and_runs_return_before_any_git_or_consent_read(vault, home, other_repo, probes, monkeypatch):
    from scout.changes import reliance

    def no_consent(env):
        raise AssertionError("consent read")

    monkeypatch.setattr(reliance, "out_of_vault_answer", no_consent)
    t = transcript(cwd=other_repo, tool_uses=[("Bash", {"command": "scoutctl status"})])
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    assert record_reliance(payload(t, other_repo), t, owner_ctx(vault), {**env, "SCOUT_READER": "agent"}, now=NOW) == 0
    assert record_reliance(payload(t, other_repo), t, RunContext(vault, "dreaming", "p"), env, now=NOW) == 0
    assert probes == {"roots": 0, "transcript": 0}


def test_in_vault_cwd_needs_no_consent_read(vault, home, monkeypatch):
    from scout.changes import reliance

    def no_consent(env):
        raise AssertionError("consent read")

    monkeypatch.setattr(reliance, "out_of_vault_answer", no_consent)
    t = transcript(cwd=vault, tool_uses=[("Bash", {"command": "scoutctl status"})])
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    assert record_reliance(payload(t, vault), t, owner_ctx(vault), env, now=NOW) == 2


@pytest.mark.parametrize("where", ["vault", "outside"])
def test_a_session_counted_on_every_surface_skips_the_transcript(vault, home, other_repo, probes, where):
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    set_out_of_vault(True, env)
    cwd = vault if where == "vault" else other_repo
    reachable = sorted({"kb", "action-items", "skill", "scoutctl"} | ({"vault-session"} if where == "vault" else set()))
    session = hashlib.sha256(SESSION_ID.encode()).hexdigest()[:16]
    marker = owner_dir(env) / "reliance-seen.json"
    marker.write_text(json.dumps({"schema": 1, "sessions": {session: {"surfaces": reachable, "at": NOW.isoformat()}}}))
    t = transcript(cwd=cwd, tool_uses=[("Bash", {"command": "scoutctl status"})])
    assert record_reliance(payload(t, cwd), t, owner_ctx(vault), env, now=NOW) == 0
    assert probes["transcript"] == 0
    assert probes["roots"] == (0 if where == "vault" else 1)


def test_vault_roots_cache_expires_after_ten_minutes(vault, home, other_repo, probes):
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    t = transcript(cwd=other_repo, tool_uses=[("Bash", {"command": "scoutctl status"})])
    for when in (NOW, NOW + timedelta(minutes=10), NOW + timedelta(minutes=11), NOW + timedelta(minutes=12)):
        record_reliance(payload(t, other_repo), t, owner_ctx(vault), env, now=when)
    assert probes["roots"] == 2
    cache = json.loads((owner_dir(env) / "vault-roots-cache.json").read_text())
    assert [Path(r) for r in next(iter(cache["vaults"].values()))["roots"]] == [vault.resolve()]
    assert str(other_repo) not in json.dumps(cache)  # the vault's folders only, never the session's project


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        "[]",
        json.dumps({"schema": 2, "vaults": {}}),
        json.dumps({"schema": 1, "vaults": ["x"]}),
        json.dumps({"schema": 1, "vaults": {"KEY": {"roots": [], "at": NOW.isoformat()}}}),
        json.dumps({"schema": 1, "vaults": {"KEY": {"roots": [7], "at": NOW.isoformat()}}}),
        json.dumps({"schema": 1, "vaults": {"KEY": {"roots": ["/x"], "at": "2026-11-17T12:00:00"}}}),  # zoneless
        json.dumps({"schema": 1, "vaults": {"KEY": {"roots": ["/x"], "at": "later"}}}),
        json.dumps({"schema": 1, "vaults": {"KEY": {"roots": ["/x"]}}}),
        json.dumps({"schema": 1, "vaults": {"KEY": "x"}}),
        json.dumps({"schema": 1, "vaults": {"KEY": {"roots": ["/x"], "at": (NOW + timedelta(hours=1)).isoformat()}}}),
    ],
)
def test_unusable_roots_cache_is_recomputed(vault, home, other_repo, probes, text):
    from scout.changes.store import vault_key

    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    path = owner_dir(env) / "vault-roots-cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("KEY", vault_key(vault)))
    t = transcript(cwd=other_repo, tool_uses=[("Bash", {"command": "scoutctl status"})])
    assert record_reliance(payload(t, other_repo), t, owner_ctx(vault), env, now=NOW) == 0
    assert probes["roots"] == 1
    assert json.loads(path.read_text())["schema"] == 1


def test_other_vaults_fresh_cache_entries_are_kept_and_stale_ones_dropped(vault, home, other_repo, probes):
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    path = owner_dir(env) / "vault-roots-cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = {"roots": ["/other"], "at": NOW.isoformat()}
    stale = {"roots": ["/old"], "at": (NOW - timedelta(hours=1)).isoformat()}
    path.write_text(json.dumps({"schema": 1, "vaults": {"fresh": fresh, "stale": stale}}))
    t = transcript(cwd=other_repo, tool_uses=[("Bash", {"command": "scoutctl status"})])
    record_reliance(payload(t, other_repo), t, owner_ctx(vault), env, now=NOW)
    vaults = json.loads(path.read_text())["vaults"]
    assert "fresh" in vaults and "stale" not in vaults and len(vaults) == 2


def test_a_worktree_made_after_the_cache_counts_at_once(git_vault, home, tmp_path, other_repo, probes, monkeypatch):
    """A fresh cache can't know a worktree made a minute ago; its newer `.git` file sends the hook back to git."""
    from scout.changes import reliance

    monkeypatch.setattr(reliance, "_rows", _real_rows)  # this one parses: count git calls only
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(git_vault)}
    _git(other_repo, "init", "-q")  # a repo of its own: its `.git` is a folder, so the cache answer stands
    t = transcript(cwd=other_repo, tool_uses=[("Bash", {"command": "scoutctl status"})])
    assert record_reliance(payload(t, other_repo), t, owner_ctx(git_vault), env, now=NOW) == 0
    assert record_reliance(payload(t, other_repo), t, owner_ctx(git_vault), env, now=NOW) == 0
    assert probes["roots"] == 1
    wt = add_worktree(git_vault, tmp_path / "elsewhere" / "wt")
    t = transcript(cwd=wt, tool_uses=[("Bash", {"command": "scoutctl status"})])
    assert record_reliance(payload(t, wt), t, owner_ctx(git_vault), env, now=NOW) == 2
    assert probes["roots"] == 2
    # Now cached with the worktree in it: no more git.
    assert record_reliance(payload(t, wt), t, owner_ctx(git_vault), env, now=NOW) == 0
    assert probes["roots"] == 2


def test_newer_worktree_needs_a_git_file_somewhere_above():
    from scout.changes.reliance import _newer_worktree

    assert _newer_worktree("", NOW) is False
    assert _newer_worktree("bad\0cwd", NOW) is False
    assert _newer_worktree("/", NOW) is False


def test_stop_hook_writes_the_token_row_before_the_reliance_pass(vault, home, tmp_path, monkeypatch):
    """Review item 5: a slow or failing reliance pass must not be able to lose the token-tracker row: the row must
    already be in the tracker when the reliance pass starts."""
    tracker = tmp_path / "tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))
    seen: list[int] = []

    def probe(*a, **k):
        seen.append(len(tracker.read_text().splitlines()) if tracker.exists() else 0)
        raise RuntimeError("reliance failed")

    monkeypatch.setattr("scout.changes.reliance.record_reliance", probe)
    t = transcript(cwd=vault, tool_uses=[("Bash", {"command": "scoutctl status"})])
    assert session_tokens.run(stdin=io.StringIO(json.dumps(payload(t, vault)))) is not None
    assert seen == [1]
    assert len(tracker.read_text().splitlines()) == 1


def test_empty_session_id_records_no_reliance(vault, home):
    t = transcript(cwd=vault, user_texts=["/scout-work"])
    env = {"HOME": str(home), "SCOUT_DATA_DIR": str(vault)}
    assert record_reliance(payload(t, vault), t, owner_ctx(vault), env, now=NOW) > 0  # control: a real id counts
    for row in home.rglob("engagement.jsonl"):
        row.unlink()
    (owner_dir(env) / "reliance-seen.json").unlink(missing_ok=True)
    assert record_reliance({**payload(t, vault), "session_id": ""}, t, owner_ctx(vault), env, now=NOW) == 0
    assert record_reliance({"cwd": str(vault)}, t, owner_ctx(vault), env, now=NOW) == 0
    assert not any(home.rglob("engagement.jsonl"))


def test_deeply_nested_transcript_line_is_skipped(vault, home, other_repo, tmp_path):
    read = {"type": "tool_use", "name": "Read", "input": {"file_path": str(vault / "knowledge-base" / "a.md")}}
    t = _raw_transcript(tmp_path, ["[" * 100_000, _assistant(read)])
    assert surfaces_used(t, vault, cwd=str(other_repo)) == {"kb"}
