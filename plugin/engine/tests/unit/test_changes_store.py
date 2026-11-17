from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from scout.changes.models import Change, Signal, Why
from scout.changes.store import (
    append_change,
    append_signal,
    is_owner_session,
    owner_dir,
    read_changes,
    read_signals,
    vault_key,
)
from scout.kb import hook_paths

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def _change(**kw) -> Change:
    base = dict(
        id="chg-abcdef123456",
        ts=NOW,
        kind="brain-edit",
        summary="Tightened the red-carry rule",
        why=Why(type="pattern", ref="Pattern #249", gloss="g", inferred=True),
        commit="abcdef1234567890",
        files=("SKILL.md",),
        targets=("briefing",),
        governance=True,
        author_lane="dreaming",
        backfilled=False,
    )
    base.update(kw)
    return Change(**base)


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    return v


def test_change_round_trips():
    c = _change()
    row = c.to_row()
    assert row["schema_version"] == 1
    assert Change.from_row(row) == c


def test_change_rejects_unknown_kind_and_long_summary():
    row = _change().to_row()
    with pytest.raises(ValueError):
        Change.from_row({**row, "kind": "vibes"})
    with pytest.raises(ValueError):
        Change.from_row({**row, "summary": "x" * 121})
    Change.from_row({**row, "summary": "x" * 120, "unknown_future_key": 1})


def test_signal_round_trips_and_rejects_bad_values():
    s = Signal(ts=NOW, kind="dm-reply", surface="slack-dm", ref="r", sentiment="positive")
    assert Signal.from_row(s.to_row()) == s
    with pytest.raises(ValueError):
        Signal.from_row({**s.to_row(), "surface": "nowhere"})


def test_changes_append_and_read(vault):
    append_change(vault, _change())
    rows, skipped = read_changes(vault)
    assert rows == [_change()] and skipped == 0


def test_owner_kind_routed_to_owner_dir(tmp_path, vault):
    env = {"HOME": str(tmp_path / "home")}
    append_signal(Signal(ts=NOW, kind="session-reliance", surface="kb", session="ab12"), vault=vault, env=env)
    f = tmp_path / "home/.local/state/scout/change-map/engagement.jsonl"
    assert f.exists()
    assert (f.stat().st_mode & 0o777) == 0o600
    assert json.loads(f.read_text())["vault_key"] == vault_key(vault)
    assert not (vault / ".scout-logs/engagement.jsonl").exists()


def test_owner_kind_refused_in_a_run(tmp_path, vault):
    env = {"HOME": str(tmp_path / "home"), "SCOUT_MODE": "dreaming"}
    with pytest.raises(PermissionError):
        append_signal(Signal(ts=NOW, kind="approval", surface="app"), vault=vault, env=env)
    assert not any((tmp_path / "home").rglob("*.jsonl"))


def test_run_kind_goes_to_vault_logs(tmp_path, vault):
    env = {"HOME": str(tmp_path / "home"), "SCOUT_MODE": "briefing"}
    append_signal(Signal(ts=NOW, kind="dm-reply", surface="slack-dm"), vault=vault, env=env)
    assert (vault / ".scout-logs/engagement.jsonl").exists()
    assert not any((tmp_path / "home").rglob("*.jsonl"))


def test_is_owner_session():
    assert is_owner_session({})
    assert is_owner_session({"SCOUT_READER": "owner"})
    assert not is_owner_session({"SCOUT_MODE": "x"})
    assert not is_owner_session({"SCOUT_READER": "run"})


def test_read_signals_filters_other_vaults_and_skips_torn_lines(tmp_path, vault):
    env = {"HOME": str(tmp_path / "home")}
    append_signal(Signal(ts=NOW, kind="app-edit", surface="app"), vault=vault, env=env)
    other = tmp_path / "other"
    other.mkdir()
    append_signal(Signal(ts=NOW, kind="app-edit", surface="app"), vault=other, env=env)
    with (owner_dir(env) / "engagement.jsonl").open("a") as fh:
        fh.write('{"ts": "2026-11')
    sigs, skipped = read_signals(vault, env)
    assert len(sigs) == 1 and sigs[0].vault_key == vault_key(vault)
    assert skipped == 1


def test_read_signals_merges_and_sorts(tmp_path, vault):
    env = {"HOME": str(tmp_path / "home")}
    late = datetime(2026, 11, 18, tzinfo=timezone.utc)
    append_signal(Signal(ts=late, kind="app-edit", surface="app"), vault=vault, env=env)
    append_signal(Signal(ts=NOW, kind="dm-sent", surface="slack-dm"), vault=vault, env={**env, "SCOUT_MODE": "x"})
    sigs, _ = read_signals(vault, env)
    assert [s.ts for s in sigs] == [NOW, late]


def test_owner_dir_is_a_protected_engine_root(tmp_path):
    env = {"HOME": str(tmp_path)}
    assert tmp_path / ".local/state/scout/change-map" in hook_paths.engine_roots(env)


def test_owner_kind_planted_in_vault_log_is_dropped(tmp_path, vault):
    env = {"HOME": str(tmp_path / "home")}
    logs = vault / ".scout-logs"
    logs.mkdir()
    forged = Signal(ts=NOW, kind="approval", surface="app").to_row()
    genuine = Signal(ts=NOW, kind="dm-reply", surface="slack-dm").to_row()
    (logs / "engagement.jsonl").write_text(json.dumps(forged) + "\n" + json.dumps(genuine) + "\n")
    sigs, skipped = read_signals(vault, env)
    assert [s.kind for s in sigs] == ["dm-reply"]
    assert skipped == 1
