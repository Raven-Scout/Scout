"""Doctor and run health report how scheduled runs sign in to Claude (#267, run-health P6).

A long-lived token from `scoutctl auth setup-token` keeps scheduled runs alive past the
interactive login's expiry, but it lasts a year and nothing else says when that is.
Every token here is fake; HOME is a temp dir and no keychain is touched.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import claude_auth, health
from scout.scripts.bootstrap_doctor import _check_claude_auth

FAKE = "sk-ant-oat01-FAKE-TEST-TOKEN-0000000000000000"
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _file_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests read the 0600 file directly; the Keychain backend has tests of its own."""
    monkeypatch.setattr(claude_auth, "_system", lambda: "Linux")


def _save(home: Path, *, age_days: int = 0) -> Path:
    store = claude_auth.default_store(home=home)
    claude_auth.save_token(store, FAKE, now=NOW - timedelta(days=age_days))
    return home / ".config" / "scout" / "claude-oauth-token"


def _wrapper(vault: Path, *, exports: bool) -> None:
    scripts = vault / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    body = 'TOKEN_FILE="${HOME:+$HOME/.config/scout/claude-oauth-token}"\n' if exports else "# old wrapper\n"
    (scripts / "claude-with-retry.sh").write_text("#!/bin/bash\n" + body)


def _pointer(home: Path, managed_by: str) -> None:
    from scout.scripts.engine_pointer import pointer_path

    p = pointer_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "0.17.3",
                "engine_root": "/x",
                "python": "/x/python",
                "scoutctl": "/x/scoutctl",
                "vault": "/v",
                "managed_by": managed_by,
                "written_at": "2026-10-12T00:00:00Z",
            }
        )
    )


def _jobs_registered(monkeypatch) -> None:
    """A fake `launchctl list` that shows both jobs: the real one is never run."""
    from subprocess import CompletedProcess

    out = "-\t0\tcom.scout.schedule-tick\n-\t0\tcom.scout.heartbeat\n"
    monkeypatch.setattr(
        "scout.scripts.bootstrap_doctor.subprocess.run", lambda args, **_k: CompletedProcess(args, 0, out, "")
    )


# --- doctor -------------------------------------------------------------------------


def test_login_mode_is_a_note_offering_setup_token(tmp_path: Path) -> None:
    warnings, notes = _check_claude_auth(home=tmp_path, vault=tmp_path / "v", now=NOW)
    assert warnings == []
    assert len(notes) == 1
    assert "interactive Claude login" in notes[0] and "scoutctl auth setup-token" in notes[0]


def test_login_mode_on_an_app_managed_engine_offers_nothing(tmp_path: Path) -> None:
    """The setup token is offered to non-app engines only (spec Q6)."""
    _pointer(tmp_path, "scout-app")
    _, notes = _check_claude_auth(home=tmp_path, vault=tmp_path / "v", now=NOW)
    assert notes and "setup-token" not in notes[0]


def test_token_mode_note_names_store_and_expiry(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    _wrapper(vault, exports=True)
    path = _save(tmp_path)
    warnings, notes = _check_claude_auth(home=tmp_path, vault=vault, now=NOW)
    assert warnings == []
    assert str(path) in notes[0] and "2027-10-12" in notes[0]
    assert FAKE not in notes[0]


def test_loose_permissions_warn(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    _wrapper(vault, exports=True)
    _save(tmp_path).chmod(0o644)
    warnings, _ = _check_claude_auth(home=tmp_path, vault=vault, now=NOW)
    assert len(warnings) == 1 and "chmod 600" in warnings[0]


def test_expiring_token_warns_with_the_date(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    _wrapper(vault, exports=True)
    _save(tmp_path, age_days=350)
    warnings, _ = _check_claude_auth(home=tmp_path, vault=vault, now=NOW)
    assert len(warnings) == 1
    assert "expires in 15 day(s)" in warnings[0] and "scoutctl auth setup-token" in warnings[0]


def test_lapsed_token_warns(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    _wrapper(vault, exports=True)
    _save(tmp_path, age_days=370)
    warnings, _ = _check_claude_auth(home=tmp_path, vault=vault, now=NOW)
    assert "expired about 5 day(s) ago" in warnings[0]


def test_stored_token_with_an_old_wrapper_warns_to_upgrade(tmp_path: Path) -> None:
    """A vault rendered before P6 never exports the token, so the token does nothing."""
    vault = tmp_path / "v"
    _wrapper(vault, exports=False)
    _save(tmp_path)
    warnings, _ = _check_claude_auth(home=tmp_path, vault=vault, now=NOW)
    assert len(warnings) == 1
    assert "scripts/claude-with-retry.sh" in warnings[0] and "/scout-update" in warnings[0]


def test_stored_token_and_no_wrapper_is_left_to_the_cat1_check(tmp_path: Path) -> None:
    _save(tmp_path)
    warnings, _ = _check_claude_auth(home=tmp_path, vault=tmp_path / "v", now=NOW)
    assert warnings == []


def test_run_doctor_reports_auth_with_the_host_checks(tmp_path: Path, monkeypatch) -> None:
    from scout.scripts import bootstrap_doctor

    seen: dict = {}

    def fake(*, home: Path, vault: Path, now: datetime | None = None) -> tuple[list[str], list[str]]:
        seen.update(home=home, vault=vault)
        return ["auth warning"], ["auth note"]

    monkeypatch.setattr(bootstrap_doctor, "_check_claude_auth", fake)
    for name in ("_check_scheduler_bin_path", "_check_engine_pointer", "_check_runner_claude_bin"):
        monkeypatch.setattr(bootstrap_doctor, name, lambda **_k: ([], []))
    monkeypatch.setattr(bootstrap_doctor, "_check_scoutctl_shim", lambda **_k: ([], []))
    _jobs_registered(monkeypatch)
    vault = tmp_path / "v"
    vault.mkdir()
    report = bootstrap_doctor.run_doctor(vault=vault, check_jobs=True, home=tmp_path / "home")
    assert seen == {"home": tmp_path / "home", "vault": vault}
    assert "auth warning" in report.warnings and "auth note" in report.notes
    # Off with the other host checks (the contract fixture and app runs pass check_jobs=False).
    seen.clear()
    report = bootstrap_doctor.run_doctor(vault=vault, check_jobs=False, home=tmp_path / "home")
    assert seen == {} and "auth note" not in report.notes


def test_a_crashing_auth_check_does_not_break_the_doctor(tmp_path: Path, monkeypatch) -> None:
    from scout.scripts import bootstrap_doctor

    def boom(**_k: object) -> tuple[list[str], list[str]]:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(bootstrap_doctor, "_check_claude_auth", boom)
    for name in ("_check_scheduler_bin_path", "_check_engine_pointer", "_check_runner_claude_bin"):
        monkeypatch.setattr(bootstrap_doctor, name, lambda **_k: ([], []))
    monkeypatch.setattr(bootstrap_doctor, "_check_scoutctl_shim", lambda **_k: ([], []))
    _jobs_registered(monkeypatch)
    vault = tmp_path / "v"
    vault.mkdir()
    report = bootstrap_doctor.run_doctor(vault=vault, check_jobs=True, home=tmp_path / "home")
    assert any("could not check how scheduled runs sign in" in n for n in report.notes)


# --- run health ---------------------------------------------------------------------


def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    return v


def _expired_ledger(vault: Path) -> None:
    rows = []
    for i in range(2):
        at = NOW - timedelta(hours=2 * (i + 1))
        rows.append(
            {
                "slot": "morning-briefing",
                "mode": "scout",
                "started_at": int(at.timestamp()) - 5,
                "finished_at": int(at.timestamp()),
                "exit_code": 1,
                "failure_class": "oauth_expired",
            }
        )
    (vault / ".scout-logs" / "run-outcomes.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_health_without_a_token_has_no_auth_finding(tmp_path: Path) -> None:
    report = health.evaluate(_vault(tmp_path), now=NOW, home=tmp_path)
    assert report.findings == ()


def test_health_fresh_token_is_green(tmp_path: Path) -> None:
    _save(tmp_path)
    assert health.evaluate(_vault(tmp_path), now=NOW, home=tmp_path).severity == "green"


def test_health_expiring_token_is_yellow(tmp_path: Path) -> None:
    _save(tmp_path, age_days=340)
    report = health.evaluate(_vault(tmp_path), now=NOW, home=tmp_path)
    assert report.severity == "yellow"
    (f,) = report.findings
    assert f.id == "auth.token_expiring" and f.severity == "yellow"
    assert "25 day(s)" in f.summary and "2026-11-06" in f.summary
    assert f.fix == "Open Terminal and run: scoutctl auth setup-token"
    assert FAKE not in json.dumps(report.to_dict())


def test_health_lapsed_token_says_so(tmp_path: Path) -> None:
    _save(tmp_path, age_days=400)
    (f,) = health.evaluate(_vault(tmp_path), now=NOW, home=tmp_path).findings
    assert "expired about 35 day(s) ago" in f.summary


def test_health_rejected_stored_token_points_at_the_token_not_the_login(tmp_path: Path) -> None:
    """With a token exported, `claude auth login` does nothing for scheduled runs."""
    v = _vault(tmp_path)
    _expired_ledger(v)
    _save(tmp_path)
    report = health.evaluate(v, now=NOW, home=tmp_path)
    auth = next(f for f in report.findings if f.id == "auth.expired")
    assert auth.severity == "red"
    assert "stored Claude token was rejected" in auth.summary
    assert "2 failed run(s)" in auth.summary
    assert "scoutctl auth setup-token" in auth.fix and "claude auth login" not in auth.fix


def test_health_rejected_login_without_a_token_keeps_the_login_fix(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    _expired_ledger(v)
    auth = next(f for f in health.evaluate(v, now=NOW, home=tmp_path).findings if f.id == "auth.expired")
    assert "claude auth login" in auth.fix
    assert "your Claude login expired" in auth.summary


def test_health_defaults_to_the_real_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _save(tmp_path, age_days=340)
    assert health.evaluate(_vault(tmp_path / "x"), now=NOW).severity == "yellow"


# --- how the last scheduled run signed in (.scout-logs/.claude-sign-in.json) ---------


def _sign_in(vault: Path, text: str) -> None:
    (vault / ".scout-logs").mkdir(parents=True, exist_ok=True)
    (vault / ".scout-logs" / ".claude-sign-in.json").write_text(text)


def _record(source: str, unreadable: bool = False) -> str:
    return json.dumps({"at": int(NOW.timestamp()), "source": source, "token_unreadable": unreadable})


def test_doctor_notes_how_the_last_run_signed_in(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    _sign_in(vault, _record("keychain"))
    _, notes = _check_claude_auth(home=tmp_path, vault=vault, now=NOW)
    expected = (
        "The last scheduled run (2026-10-12T09:00:00Z) signed in with the long-lived token from the macOS Keychain."
    )
    assert expected in notes


def test_health_token_unreadable_is_yellow(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    _sign_in(v, _record("login", unreadable=True))
    (f,) = health.evaluate(v, now=NOW, home=tmp_path).findings
    assert f.id == "auth.token_unreadable" and f.severity == "yellow"
    assert f.since == NOW
    assert "Keychain locked" in f.summary


def test_health_readable_sign_in_adds_nothing(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    _sign_in(v, _record("keychain"))
    assert health.evaluate(v, now=NOW, home=tmp_path).findings == ()


def test_last_sign_in_describes_each_source(tmp_path: Path) -> None:
    v = _vault(tmp_path)
    for source, words in [
        ("file", "~/.config/scout/claude-oauth-token"),
        ("environment", "CLAUDE_CODE_OAUTH_TOKEN"),
        ("login", "interactive Claude login"),
        ("mystery", "mystery"),
    ]:
        _sign_in(v, _record(source))
        last = claude_auth.last_sign_in(v)
        assert last is not None and words in last.describe()
    _sign_in(v, _record("login", unreadable=True))
    last = claude_auth.last_sign_in(v)
    assert last is not None and "couldn't read the stored token" in last.describe()


@pytest.mark.parametrize("text", ["", "{not json", "[]", '{"at": "soon", "source": "login"}', '{"source": "login"}'])
def test_last_sign_in_garbage_reads_as_none(tmp_path: Path, text: str) -> None:
    v = _vault(tmp_path)
    _sign_in(v, text)
    assert claude_auth.last_sign_in(v) is None


def test_last_sign_in_absent_is_none(tmp_path: Path) -> None:
    assert claude_auth.last_sign_in(tmp_path / "nowhere") is None
