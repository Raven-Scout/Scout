from __future__ import annotations

import json
from datetime import UTC, datetime

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

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


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


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """The owner store follows HOME in tests (conftest); these tests name it tmp_path/home."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


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
    f = tmp_path / "home/.local/state/scout/scout-change-ledger-owner/engagement.jsonl"
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
    late = datetime(2026, 11, 18, tzinfo=UTC)
    append_signal(Signal(ts=late, kind="app-edit", surface="app"), vault=vault, env=env)
    append_signal(Signal(ts=NOW, kind="dm-sent", surface="slack-dm"), vault=vault, env={**env, "SCOUT_MODE": "x"})
    sigs, _ = read_signals(vault, env)
    assert [s.ts for s in sigs] == [NOW, late]


def test_owner_dir_is_a_protected_engine_root(tmp_path):
    env = {"HOME": str(tmp_path)}
    assert tmp_path / ".local/state/scout/scout-change-ledger-owner" in hook_paths.engine_roots(env)


def test_readers_survive_deep_nesting_and_unreadable_files(tmp_path, vault):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from scout.changes.report import changes_report, engagement_report

    env = {"HOME": str(tmp_path / "home")}
    logs = vault / ".scout-logs"
    logs.mkdir()
    (logs / "changes.jsonl").write_text("[" * 200_000 + "\n" + json.dumps(_change().to_row()) + "\n")
    assert read_changes(vault) == ([_change()], 1)
    (logs / "engagement.jsonl").write_text('{"a":' * 200_000 + "\n")
    assert read_signals(vault, env) == ([], 1)
    # An unreadable ledger (here a directory where the file should be) is empty, counted as one skipped.
    (logs / "engagement.jsonl").unlink()
    (logs / "engagement.jsonl").mkdir()
    owner_dir(env).mkdir(parents=True)
    (owner_dir(env) / "engagement.jsonl").mkdir()
    assert read_signals(vault, env) == ([], 2)
    (logs / "changes.jsonl").unlink()
    (logs / "changes.jsonl").mkdir()
    assert read_changes(vault) == ([], 1)
    since = NOW - timedelta(days=1)
    assert changes_report(vault, now=NOW, since=since, env=env, tz=ZoneInfo("UTC"))["skipped_rows"] == 1
    assert engagement_report(vault, env, now=NOW, since=since, tz=ZoneInfo("UTC"))["skipped_rows"] == 2


def test_readers_skip_rows_from_a_newer_schema(tmp_path, vault):
    env = {"HOME": str(tmp_path / "home")}
    append_change(vault, _change())
    future_change = {**_change(id="chg-future000000").to_row(), "schema_version": 2}
    with (vault / ".scout-logs" / "changes.jsonl").open("a") as fh:
        fh.write(json.dumps(future_change) + "\n")
    assert read_changes(vault) == ([_change()], 1)

    append_signal(Signal(ts=NOW, kind="app-edit", surface="app"), vault=vault, env=env)
    owner_future = {**Signal(ts=NOW, kind="app-edit", surface="app").to_row(), "vault_key": vault_key(vault)}
    run_future = Signal(ts=NOW, kind="dm-reply", surface="slack-dm").to_row()
    with (owner_dir(env) / "engagement.jsonl").open("a") as fh:
        fh.write(json.dumps({**owner_future, "schema_version": 2}) + "\n")
    with (vault / ".scout-logs" / "engagement.jsonl").open("a") as fh:
        fh.write(json.dumps({**run_future, "schema_version": 99}) + "\n")
        fh.write(json.dumps({**run_future, "schema_version": "1"}) + "\n")
    sigs, skipped = read_signals(vault, env)
    assert [s.kind for s in sigs] == ["app-edit"] and skipped == 3
    # A row with no schema_version is a v1 row.
    legacy = {k: v for k, v in run_future.items() if k != "schema_version"}
    assert Signal.from_row(legacy).kind == "dm-reply"


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


# ---- fallback paths ----------------------------------------------------------


def test_change_and_why_reject_malformed_fields():
    row = _change().to_row()
    with pytest.raises(ValueError, match="not an ISO timestamp"):
        Change.from_row({**row, "ts": "last tuesday"})
    with pytest.raises(ValueError, match="expected a list of strings"):
        Change.from_row({**row, "files": "SKILL.md"})
    with pytest.raises(ValueError, match="expected a list of strings"):
        Change.from_row({**row, "targets": [1]})
    with pytest.raises(ValueError, match="expected a bool"):
        Change.from_row({**row, "governance": "yes"})
    with pytest.raises(ValueError, match="why: expected an object"):
        Change.from_row({**row, "why": "pattern-1"})


def test_vault_key_falls_back_to_absolute_when_resolve_fails(tmp_path):
    class Unresolvable:
        def resolve(self):
            raise OSError("symlink loop")

        def absolute(self):
            return tmp_path / "vault"

    assert vault_key(Unresolvable()) == vault_key(tmp_path / "vault")  # type: ignore[arg-type]


def test_read_skips_blank_lines_and_non_object_rows(vault):
    (vault / ".scout-logs").mkdir()
    good = json.dumps(_change().to_row())
    (vault / ".scout-logs/changes.jsonl").write_text(f"\n   \n{good}\n[1, 2]\n")
    rows, skipped = read_changes(vault)
    assert rows == [_change()] and skipped == 1


def test_is_owner_caller_fails_closed_when_the_process_check_raises(tmp_path, vault, monkeypatch):
    from scout.changes.store import is_owner_caller
    from scout.kb import live_run

    def boom(_vault):
        raise RuntimeError("ps unavailable")

    monkeypatch.setattr(live_run, "commit_source_now", boom)
    assert is_owner_caller({"HOME": str(tmp_path / "home")}, vault) is False


def test_consent_record_that_does_not_parse_is_unanswered(tmp_path):
    from scout.changes.consent import out_of_vault_answer

    env = {"HOME": str(tmp_path / "home")}
    path = owner_dir(env) / "consent.json"
    path.parent.mkdir(parents=True)
    for text in ('{"schema": 2, "out_of_vault": true}', '{"schema": 1, "out_of_vault": "yes"}', "[true]"):
        path.write_text(text)
        assert out_of_vault_answer(env) is None, text


def test_consent_cannot_be_written_by_a_run(tmp_path):
    from scout.changes.consent import set_out_of_vault

    env = {"HOME": str(tmp_path / "home"), "SCOUT_MODE": "dreaming"}
    with pytest.raises(PermissionError):
        set_out_of_vault(True, env)
    assert not any((tmp_path / "home").rglob("consent.json"))


def test_consent_write_failure_leaves_no_temp_file(tmp_path, monkeypatch):
    from scout.changes import consent

    env = {"HOME": str(tmp_path / "home")}

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(consent.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        consent.set_out_of_vault(True, env)
    assert list(owner_dir(env).iterdir()) == []


def test_signal_change_field_round_trips_and_rejects_bad_ids():
    sig = Signal(ts=NOW, kind="dm-reply", surface="slack-dm", ref="1.2/3.4", change="chg-0123456789ab")
    row = sig.to_row()
    assert row["change"] == "chg-0123456789ab"
    assert Signal.from_row(row) == sig
    assert Signal.from_row({**row, "change": None}).change is None
    without = {k: v for k, v in row.items() if k != "change"}
    assert Signal.from_row(without).change is None
    for bad in ("chg-xyz", "chg-0123456789AB", "chg-0123456789abc", 7):
        with pytest.raises(ValueError):
            Signal.from_row({**row, "change": bad})


def test_change_reverts_validates_like_a_change_id():
    row = _change().to_row()
    assert Change.from_row(row).reverts is None
    assert Change.from_row({**row, "reverts": "chg-0123456789ab"}).reverts == "chg-0123456789ab"
    assert _change(reverts="chg-0123456789ab").to_row()["reverts"] == "chg-0123456789ab"
    with pytest.raises(ValueError):
        Change.from_row({**row, "reverts": "nope"})


def test_new_kinds_surfaces_and_why_types_are_known():
    from scout.changes.models import OWNER_KINDS, REF_REQUIRED, RUN_KINDS, SURFACES, WHY_TYPES

    assert {"recurrence", "owner-away", "red-carried", "dm-sent"} <= RUN_KINDS
    assert "verdict-wrong" in OWNER_KINDS
    assert {"mistake-audit", "calendar"} <= SURFACES
    assert "verdict" in WHY_TYPES
    assert REF_REQUIRED == RUN_KINDS | {"approval", "verdict-wrong"}
    Signal.from_row({"ts": NOW.isoformat(), "kind": "owner-away", "surface": "calendar", "ref": "2026-11-17"})


def test_append_signal_once_skips_a_duplicate_kind_and_ref(tmp_path, vault):
    from scout.changes.store import append_signal_once, has_signal

    env = {"HOME": str(tmp_path / "home")}
    sig = Signal(ts=NOW, kind="dm-reply", surface="slack-dm", ref="1.2/3.4")
    assert has_signal(vault, env, "dm-reply", "1.2/3.4") is False
    assert append_signal_once(sig, vault=vault, env=env) is True
    assert has_signal(vault, env, "dm-reply", "1.2/3.4") is True
    assert append_signal_once(sig, vault=vault, env=env) is False
    rows, _ = read_signals(vault, env)
    assert len(rows) == 1
    # The same ref under another kind is a different signal.
    other = Signal(ts=NOW, kind="dm-reaction", surface="slack-dm", ref="1.2/3.4")
    assert append_signal_once(other, vault=vault, env=env) is True


def test_append_signal_once_reads_the_owner_store_for_owner_kinds(tmp_path, vault):
    from scout.changes.store import append_signal_once, has_signal

    env = {"HOME": str(tmp_path / "home")}
    sig = Signal(ts=NOW, kind="approval", surface="app", ref="approve:chg-0123456789ab")
    assert append_signal_once(sig, vault=vault, env=env) is True
    assert has_signal(vault, env, "approval", "approve:chg-0123456789ab") is True
    assert append_signal_once(sig, vault=vault, env=env) is False
    # Owner rows are keyed to one vault: another vault has not recorded it.
    other = tmp_path / "other"
    other.mkdir()
    assert has_signal(other, env, "approval", "approve:chg-0123456789ab") is False


def test_append_signal_once_without_a_ref_always_writes(tmp_path, vault):
    from scout.changes.store import append_signal_once

    env = {"HOME": str(tmp_path / "home")}
    sig = Signal(ts=NOW, kind="session-reliance", surface="kb", session="ab12")
    assert append_signal_once(sig, vault=vault, env=env) is True
    assert append_signal_once(sig, vault=vault, env=env) is True


def test_has_signal_never_raises(tmp_path, vault):
    from scout.changes.store import has_signal

    (vault / ".scout-logs").mkdir()
    (vault / ".scout-logs" / "engagement.jsonl").write_text('not json\n[1]\n{"kind": "dm-sent", "ref": "9.9"}\n')
    assert has_signal(vault, {"HOME": str(tmp_path / "home")}, "dm-sent", "9.9") is True
    assert has_signal(vault, {}, "nonsense-kind", "x") is False
    assert has_signal(vault, {}, ["unhashable"], "x") is False  # type: ignore[arg-type]
