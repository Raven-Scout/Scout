"""scout.claude_auth: where the long-lived Claude token for scheduled runs lives (#267, run-health P6).

Every token here is an obviously fake value; nothing reads the real HOME (conftest points
HOME at a per-test temp dir) and nothing reaches a real keychain.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import claude_auth

FAKE = "sk-ant-oat01-FAKE-TEST-TOKEN-0000000000000000"
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def _store(home: Path) -> claude_auth.FileTokenStore:
    return claude_auth.FileTokenStore(home / ".config" / "scout" / claude_auth.TOKEN_FILENAME)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- the file store ---------------------------------------------------------


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_auth, "_system", lambda: "Linux")


@pytest.fixture
def macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_auth, "_system", lambda: "Darwin")


def test_default_store_off_macos_is_the_0600_file_under_dot_config(tmp_path: Path, linux: None) -> None:
    store = claude_auth.default_store(home=tmp_path)
    assert isinstance(store, claude_auth.FileTokenStore)
    assert store.path == tmp_path / ".config" / "scout" / "claude-oauth-token"


def test_default_store_follows_HOME(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linux: None) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    store = claude_auth.default_store()
    assert isinstance(store, claude_auth.FileTokenStore)
    assert store.path == tmp_path / ".config" / "scout" / "claude-oauth-token"


def test_default_store_on_macos_is_the_keychain(tmp_path: Path, macos: None) -> None:
    store = claude_auth.default_store(home=tmp_path)
    assert isinstance(store, claude_auth.KeychainTokenStore)
    assert store.meta_path == tmp_path / ".config" / "scout" / "claude-oauth-token.meta.json"
    assert "Keychain" in store.location


def test_system_is_the_real_platform() -> None:
    import platform

    assert claude_auth._system() == platform.system()


# --- the Keychain store (through the fake `security` from conftest) ------------------


def _calls(fake_keychain: Path) -> list[str]:
    path = fake_keychain / ".calls"
    return path.read_text().splitlines() if path.exists() else []


def test_keychain_calls_security_through_PATH_only(tmp_path: Path) -> None:
    """conftest's guard works by shadowing `security` on PATH; an absolute path would bypass it."""
    assert claude_auth.KeychainTokenStore(tmp_path).security == "security"
    source = Path(claude_auth.__file__).read_text(encoding="utf-8")
    assert "/usr/bin/security" not in source


def test_keychain_round_trip(tmp_path: Path, fake_keychain: Path) -> None:
    store = claude_auth.KeychainTokenStore(tmp_path, account="alex")
    assert not store.exists() and store.read() is None
    store.write(FAKE)
    assert store.exists() and store.read() == FAKE
    assert (fake_keychain / "scout-claude-oauth-token@alex").read_text() == FAKE
    store.write("sk-ant-oat01-FAKE-REPLACEMENT-0000000000")
    assert store.read() == "sk-ant-oat01-FAKE-REPLACEMENT-0000000000"
    assert store.delete() is True
    assert store.delete() is False
    assert store.permission_problem() is None


def test_keychain_token_never_goes_into_argv(tmp_path: Path, fake_keychain: Path) -> None:
    claude_auth.KeychainTokenStore(tmp_path, account="alex").write(FAKE)
    calls = _calls(fake_keychain)
    adds = [c for c in calls if "add-generic-password" in c]
    assert adds and all(c.startswith("-i ") for c in adds), calls
    assert any(c == "-i" for c in calls)  # `security -i`, command on stdin
    assert FAKE not in "\n".join(calls)


def test_keychain_default_account_is_the_login_user(tmp_path: Path) -> None:
    import pwd

    assert claude_auth.KeychainTokenStore(tmp_path).account == pwd.getpwuid(os.getuid()).pw_name


def test_keychain_locked_read_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = claude_auth.KeychainTokenStore(tmp_path, account="alex")
    store.write(FAKE)
    monkeypatch.setenv("FAKE_KEYCHAIN_LOCKED", "1")
    with pytest.raises(OSError, match="may be locked"):
        store.read()


def test_keychain_write_that_does_not_stick_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_KEYCHAIN_LOCKED", "1")
    with pytest.raises(OSError):
        claude_auth.KeychainTokenStore(tmp_path, account="alex").write(FAKE)


def test_keychain_refuses_odd_account_names(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        claude_auth.KeychainTokenStore(tmp_path, account="a b").write(FAKE)


def test_keychain_missing_security_binary_is_an_oserror(tmp_path: Path) -> None:
    store = claude_auth.KeychainTokenStore(tmp_path, account="alex", security=str(tmp_path / "no-such-security"))
    with pytest.raises(OSError, match="couldn't run"):
        store.exists()


@pytest.mark.parametrize("method", ["exists", "delete"])
def test_keychain_other_failures_raise(tmp_path: Path, method: str) -> None:
    failing = tmp_path / "security"
    failing.write_text("#!/bin/sh\nexit 51\n")
    failing.chmod(0o755)
    store = claude_auth.KeychainTokenStore(tmp_path, account="alex", security=str(failing))
    with pytest.raises(OSError, match="exit 51"):
        getattr(store, method)()


def test_status_with_a_locked_keychain_reports_a_problem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = claude_auth.KeychainTokenStore(tmp_path, account="alex")
    claude_auth.save_token(store, FAKE, now=NOW)
    monkeypatch.setenv("FAKE_KEYCHAIN_LOCKED", "1")
    st = claude_auth.status(store, now=NOW)
    assert st.mode == "token"
    assert any("may be locked" in p for p in st.problems)


def test_status_keychain_lookup_failure_is_a_problem(tmp_path: Path) -> None:
    failing = tmp_path / "security"
    failing.write_text("#!/bin/sh\nexit 51\n")
    failing.chmod(0o755)
    st = claude_auth.status(claude_auth.KeychainTokenStore(tmp_path, account="a", security=str(failing)), now=NOW)
    assert st.mode == "login"
    assert any("can't check for a stored token" in p for p in st.problems)


def test_fake_security_refuses_anything_but_scouts_item(fake_keychain: Path) -> None:
    import subprocess

    for argv in (
        ["find-generic-password", "-s", "some-other-service", "-a", "x", "-w"],
        ["delete-keychain", "login.keychain"],
        ["find-identity", "-v"],
        ["dump-keychain", "-d"],
    ):
        proc = subprocess.run(["security", *argv], capture_output=True, text=True, check=False)
        assert proc.returncode == 99, argv
        assert "blocked in tests" in proc.stderr


def test_write_creates_0600_file_in_0700_dir(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(FAKE)
    assert store.path.read_text(encoding="utf-8") == FAKE + "\n"
    assert _mode(store.path) == 0o600
    assert _mode(store.path.parent) == 0o700


def test_write_replaces_an_existing_token_and_tightens_loose_permissions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write("sk-ant-oat01-FAKE-OLD")
    store.path.chmod(0o644)
    store.write(FAKE)
    assert store.read() == FAKE
    assert _mode(store.path) == 0o600


def test_write_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(FAKE)
    assert sorted(p.name for p in store.path.parent.iterdir()) == ["claude-oauth-token"]


def test_read_absent_is_none(tmp_path: Path) -> None:
    assert _store(tmp_path).read() is None
    assert not _store(tmp_path).exists()


def test_read_strips_whitespace_and_treats_blank_as_absent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text("  \n", encoding="utf-8")
    assert store.read() is None


def test_permission_problem_names_the_mode_and_the_fix(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(FAKE)
    assert store.permission_problem() is None
    store.path.chmod(0o644)
    problem = store.permission_problem()
    assert problem is not None
    assert "0644" in problem and f"chmod 600 {store.path}" in problem
    assert FAKE not in problem


def test_permission_problem_absent_file_is_none(tmp_path: Path) -> None:
    assert _store(tmp_path).permission_problem() is None


def test_clear_token_removes_token_and_meta(tmp_path: Path) -> None:
    store = _store(tmp_path)
    claude_auth.save_token(store, FAKE, now=NOW)
    assert claude_auth.clear_token(store) is True
    assert not store.path.exists()
    assert not claude_auth.meta_path(store).exists()
    assert claude_auth.clear_token(store) is False


# --- normalising what the user pastes -----------------------------------------


@pytest.mark.parametrize(
    "pasted",
    [
        FAKE,
        f"  {FAKE}  \n",
        f"export CLAUDE_CODE_OAUTH_TOKEN={FAKE}",
        f"CLAUDE_CODE_OAUTH_TOKEN={FAKE}",
        f"export CLAUDE_CODE_OAUTH_TOKEN='{FAKE}'",
        f'CLAUDE_CODE_OAUTH_TOKEN="{FAKE}"',
    ],
)
def test_normalize_token_accepts_bare_or_export_line(pasted: str) -> None:
    assert claude_auth.normalize_token(pasted) == FAKE


@pytest.mark.parametrize("bad", ["", "   ", "sk-ant oat01 has spaces", "short", "sk-ant-\x07bell-0000000000000"])
def test_normalize_token_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError) as e:
        claude_auth.normalize_token(bad)
    # The message never echoes what was pasted.
    assert bad.strip() == "" or bad.strip() not in str(e.value)


# --- meta + status ------------------------------------------------------------


def test_save_token_writes_meta_without_the_token(tmp_path: Path) -> None:
    store = _store(tmp_path)
    claude_auth.save_token(store, FAKE, now=NOW)
    meta_file = claude_auth.meta_path(store)
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta == {"created_at": "2026-10-12T09:00:00Z", "source": "claude setup-token", "store": "file"}
    assert FAKE not in meta_file.read_text(encoding="utf-8")
    assert _mode(meta_file) == 0o600


def test_status_without_a_token_is_login_mode(tmp_path: Path) -> None:
    st = claude_auth.status(_store(tmp_path), now=NOW)
    assert st.mode == "login"
    assert st.created_at is None and st.expires_at is None
    assert st.problems == ()
    d = st.to_dict()
    assert d["mode"] == "login" and d["store"] == "file"


def test_status_fresh_token(tmp_path: Path) -> None:
    store = _store(tmp_path)
    claude_auth.save_token(store, FAKE, now=NOW)
    st = claude_auth.status(store, now=NOW + timedelta(days=10))
    assert st.mode == "token"
    assert st.created_at == NOW
    assert st.expires_at == NOW + timedelta(days=365)
    assert st.days_left == 355
    assert st.expiring is False and st.problems == ()
    assert FAKE not in json.dumps(st.to_dict())


def test_status_expiring_within_30_days(tmp_path: Path) -> None:
    store = _store(tmp_path)
    claude_auth.save_token(store, FAKE, now=NOW)
    st = claude_auth.status(store, now=NOW + timedelta(days=336))
    assert st.expiring is True
    assert st.days_left == 29


def test_status_exactly_30_days_left_is_not_expiring_yet(tmp_path: Path) -> None:
    store = _store(tmp_path)
    claude_auth.save_token(store, FAKE, now=NOW)
    assert claude_auth.status(store, now=NOW + timedelta(days=335)).expiring is False


def test_status_past_estimated_expiry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    claude_auth.save_token(store, FAKE, now=NOW)
    st = claude_auth.status(store, now=NOW + timedelta(days=400))
    assert st.expiring is True
    assert st.days_left == -35


def test_status_token_without_meta_has_unknown_expiry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write(FAKE)  # e.g. written by hand
    st = claude_auth.status(store, now=NOW)
    assert st.mode == "token"
    assert st.created_at is None and st.expires_at is None and st.days_left is None
    assert st.expiring is False


def test_status_corrupt_meta_reads_as_unknown(tmp_path: Path) -> None:
    store = _store(tmp_path)
    claude_auth.save_token(store, FAKE, now=NOW)
    claude_auth.meta_path(store).write_text("{not json", encoding="utf-8")
    assert claude_auth.status(store, now=NOW).created_at is None
    claude_auth.meta_path(store).write_text('{"created_at": "yesterday"}', encoding="utf-8")
    assert claude_auth.status(store, now=NOW).created_at is None
    claude_auth.meta_path(store).write_text("[]", encoding="utf-8")
    assert claude_auth.status(store, now=NOW).created_at is None


def test_status_reports_loose_permissions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    claude_auth.save_token(store, FAKE, now=NOW)
    store.path.chmod(0o640)
    st = claude_auth.status(store, now=NOW)
    assert len(st.problems) == 1 and "0640" in st.problems[0]


def test_status_unreadable_token_file_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads any file")
    store = _store(tmp_path)
    claude_auth.save_token(store, FAKE, now=NOW)
    store.path.chmod(0o000)
    try:
        st = claude_auth.status(store, now=NOW)
    finally:
        store.path.chmod(0o600)
    assert st.mode == "token"
    assert any("can't read" in p for p in st.problems)


def test_describe_lines(tmp_path: Path) -> None:
    store = _store(tmp_path)
    login = claude_auth.status(store, now=NOW).describe()
    assert "interactive Claude login" in login and "scoutctl auth setup-token" in login
    claude_auth.save_token(store, FAKE, now=NOW)
    tok = claude_auth.status(store, now=NOW).describe()
    assert "long-lived token" in tok and "2027-10-12" in tok and str(store.path) in tok
    store.path.unlink()
    store.write(FAKE)
    claude_auth.meta_path(store).unlink()
    assert "expiry unknown" in claude_auth.status(store, now=NOW).describe()


def test_store_protocol_is_satisfied_by_the_file_store(tmp_path: Path) -> None:
    assert isinstance(_store(tmp_path), claude_auth.TokenStore)


def test_status_blank_token_file_is_login_mode(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text("\n", encoding="utf-8")
    assert claude_auth.status(store, now=NOW).mode == "login"


def test_store_protocol_is_satisfied_by_the_keychain_store(tmp_path: Path) -> None:
    assert isinstance(claude_auth.KeychainTokenStore(tmp_path, account="alex"), claude_auth.TokenStore)


def test_a_failed_write_leaves_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)

    def boom(src: str, dst: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(claude_auth.os, "replace", boom)
    with pytest.raises(OSError):
        store.write(FAKE)
    assert list(store.path.parent.iterdir()) == []


# --- the terminal helpers behind `scoutctl auth setup-token` --------------------------


def test_run_inherited_returns_the_exit_code() -> None:
    assert claude_auth._run_inherited(["/bin/sh", "-c", "exit 3"]) == 3


def test_read_secret_uses_getpass(monkeypatch: pytest.MonkeyPatch) -> None:
    import getpass

    seen: list[str] = []
    monkeypatch.setattr(getpass, "getpass", lambda prompt: seen.append(prompt) or FAKE)
    assert claude_auth._read_secret("Paste: ") == FAKE
    assert seen == ["Paste: "]


@pytest.mark.parametrize(("ttys", "expected"), [((True, True), True), ((True, False), False), ((False, True), False)])
def test_has_terminal_needs_stdin_and_stdout(
    monkeypatch: pytest.MonkeyPatch, ttys: tuple[bool, bool], expected: bool
) -> None:
    monkeypatch.setattr(claude_auth.os, "isatty", lambda fd: ttys[fd])
    assert claude_auth._has_terminal() is expected
