"""claude-with-retry.sh and the stored long-lived token (#267, run-health P6).

`scoutctl auth setup-token` stores a `claude setup-token` token at
~/.config/scout/claude-oauth-token. The wrapper, which every runner launches Claude
through, exports it as CLAUDE_CODE_OAUTH_TOKEN when the variable is unset, so
scheduled runs survive the interactive login's expiry. HOME is a per-test temp dir
(conftest), and every token is an obviously fake value.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = REPO_ROOT / "templates" / "scripts" / "claude-with-retry.sh.tmpl"
FAKE = "sk-ant-oat01-FAKE-TEST-TOKEN-0000000000000000"
AUTH_ERROR = "Failed to authenticate: OAuth session expired and could not be refreshed"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    return h


def _store_token(home: Path, text: str = FAKE + "\n") -> Path:
    path = home / ".config" / "scout" / "claude-oauth-token"
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def _render(scout_dir: Path) -> Path:
    out = scout_dir / "scripts" / "claude-with-retry.sh"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.read_text(encoding="utf-8").replace("{{INSTANCE_NAME}}", "Scout"), encoding="utf-8")
    out.chmod(0o755)
    return out


def _claude(scout_dir: Path, *, output: str = "session complete", exit_code: int = 0) -> tuple[Path, Path]:
    """A `claude` that records what CLAUDE_CODE_OAUTH_TOKEN it saw, without printing it."""
    seen = scout_dir / "token-seen"
    bin_path = scout_dir / "fake-claude.sh"
    bin_path.write_text(
        "#!/bin/bash\n"
        f'if [ -z "${{CLAUDE_CODE_OAUTH_TOKEN+x}}" ]; then echo unset > "{seen}";\n'
        f'elif [ "$CLAUDE_CODE_OAUTH_TOKEN" = "{FAKE}" ]; then echo stored > "{seen}";\n'
        f'else echo other > "{seen}"; fi\n'
        f"echo {output!r}\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    bin_path.chmod(0o755)
    return bin_path, seen


def _notifier(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    record = tmp_path / "notifications.log"
    for name in ("osascript", "notify-send"):
        p = fake_bin / name
        p.write_text(f'#!/bin/bash\necho "{name} $*" >> "{record}"\ncat > /dev/null\nexit 0\n', encoding="utf-8")
        p.chmod(0o755)
    # The wrapper picks the store by `uname -s`: Darwin reads the Keychain, the rest the file.
    uname = fake_bin / "uname"
    uname.write_text('#!/bin/bash\necho "${FAKE_UNAME:-Linux}"\n', encoding="utf-8")
    uname.chmod(0o755)
    return fake_bin, record


def _run(script: Path, claude: Path, log_file: Path, fake_bin: Path, **env: str) -> subprocess.CompletedProcess:
    full = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "SCOUT_RETRY_BACKOFF_S": "0",
        "SCOUT_RETRY_MAX": "1",
        **env,
    }
    return subprocess.run([str(script), str(log_file), str(claude)], env=full, capture_output=True, text=True)


def _setup(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    scout_dir = tmp_path / "Scout"
    logs = scout_dir / ".scout-logs"
    logs.mkdir(parents=True)
    fake_bin, record = _notifier(tmp_path)
    return _render(scout_dir), logs / "scout-1.log", fake_bin, record


def _read(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def test_stored_token_is_exported_to_claude(tmp_path: Path, home: Path) -> None:
    script, log, fake_bin, _ = _setup(tmp_path)
    _store_token(home)
    claude, seen = _claude(tmp_path / "Scout")
    result = _run(script, claude, log, fake_bin)
    assert result.returncode == 0, result.stderr
    assert seen.read_text().strip() == "stored"


def test_stored_token_tolerates_surrounding_whitespace(tmp_path: Path, home: Path) -> None:
    script, log, fake_bin, _ = _setup(tmp_path)
    _store_token(home, f"  {FAKE}  \n\n")
    claude, seen = _claude(tmp_path / "Scout")
    _run(script, claude, log, fake_bin)
    assert seen.read_text().strip() == "stored"


def test_no_stored_token_leaves_the_interactive_login_in_charge(tmp_path: Path, home: Path) -> None:
    script, log, fake_bin, _ = _setup(tmp_path)
    claude, seen = _claude(tmp_path / "Scout")
    _run(script, claude, log, fake_bin)
    assert seen.read_text().strip() == "unset"


def test_blank_token_file_is_ignored(tmp_path: Path, home: Path) -> None:
    script, log, fake_bin, _ = _setup(tmp_path)
    _store_token(home, "\n")
    claude, seen = _claude(tmp_path / "Scout")
    _run(script, claude, log, fake_bin)
    assert seen.read_text().strip() == "unset"


def test_an_explicit_environment_token_wins(tmp_path: Path, home: Path) -> None:
    script, log, fake_bin, _ = _setup(tmp_path)
    _store_token(home)
    claude, seen = _claude(tmp_path / "Scout")
    _run(script, claude, log, fake_bin, CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat01-FAKE-FROM-ENVIRONMENT-00000")
    assert seen.read_text().strip() == "other"


def test_unset_HOME_does_not_break_the_wrapper(tmp_path: Path, home: Path) -> None:
    script, log, fake_bin, _ = _setup(tmp_path)
    claude, seen = _claude(tmp_path / "Scout")
    env = {k: v for k, v in os.environ.items() if k != "HOME"}
    env.update(PATH=f"{fake_bin}{os.pathsep}{os.environ['PATH']}", SCOUT_RETRY_MAX="1")
    result = subprocess.run([str(script), str(log), str(claude)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert seen.read_text().strip() == "unset"


def test_the_token_never_reaches_the_log_or_output(tmp_path: Path, home: Path) -> None:
    script, log, fake_bin, record = _setup(tmp_path)
    _store_token(home)
    claude, _ = _claude(tmp_path / "Scout", output=AUTH_ERROR, exit_code=1)
    result = _run(script, claude, log, fake_bin, SCOUT_RETRY_MAX="2")
    for text in (log.read_text(), result.stdout, result.stderr, _read(record)):
        assert FAKE not in text


def test_a_rejected_stored_token_names_the_token_fix(tmp_path: Path, home: Path) -> None:
    """`claude auth login` can't help here: the exported token overrides the login."""
    script, log, fake_bin, record = _setup(tmp_path)
    path = _store_token(home)
    claude, _ = _claude(tmp_path / "Scout", output=AUTH_ERROR, exit_code=1)
    result = _run(script, claude, log, fake_bin)
    assert result.returncode == 1
    text = log.read_text()
    assert "=== Authentication failure" in text
    assert f"stored long-lived token in {path} was rejected" in text
    assert "scoutctl auth setup-token" in text and "scoutctl auth clear" in text
    sent = _read(record)
    assert "run: scoutctl auth setup-token" in sent
    assert "claude auth login" not in sent


def test_a_rejected_environment_token_says_where_it_came_from(tmp_path: Path, home: Path) -> None:
    script, log, fake_bin, _ = _setup(tmp_path)
    claude, _ = _claude(tmp_path / "Scout", output=AUTH_ERROR, exit_code=1)
    _run(script, claude, log, fake_bin, CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat01-FAKE-FROM-ENVIRONMENT-00000")
    assert "CLAUDE_CODE_OAUTH_TOKEN from this runner's environment was rejected" in log.read_text()


def test_a_dead_login_without_a_token_offers_scoutctl_auth_setup_token(tmp_path: Path, home: Path) -> None:
    script, log, fake_bin, record = _setup(tmp_path)
    claude, _ = _claude(tmp_path / "Scout", output=AUTH_ERROR, exit_code=1)
    _run(script, claude, log, fake_bin)
    text = log.read_text()
    assert "run: claude auth login" in text
    assert "scoutctl auth setup-token" in text
    assert "run: claude auth login" in _read(record)


def test_not_logged_in_is_an_auth_failure(tmp_path: Path, home: Path) -> None:
    """A Mac set up again comes up logged out, not expired (#267 comment, 2026-10-10)."""
    script, log, fake_bin, record = _setup(tmp_path)
    claude, _ = _claude(tmp_path / "Scout", output="Not logged in · Please run /login", exit_code=1)
    result = _run(script, claude, log, fake_bin, SCOUT_RETRY_MAX="2")
    assert result.returncode == 1
    assert "=== Authentication failure" in log.read_text()
    assert len(_read(record).splitlines()) == 1


# --- macOS: the Keychain (conftest's fake `security`) --------------------------------


def _keychain_item(fake_keychain: Path, token: str = FAKE) -> Path:
    user = subprocess.run(["id", "-un"], capture_output=True, text=True, check=True).stdout.strip()
    item = fake_keychain / f"scout-claude-oauth-token@{user}"
    item.write_text(token)
    return item


def _sign_in(log: Path) -> dict:
    import json

    return json.loads((log.parent / ".claude-sign-in.json").read_text())


def test_macos_exports_the_keychain_token(tmp_path: Path, home: Path, fake_keychain: Path) -> None:
    script, log, fake_bin, _ = _setup(tmp_path)
    _keychain_item(fake_keychain)
    _store_token(home, "sk-ant-oat01-FAKE-FILE-IS-IGNORED-ON-MACOS\n")
    claude, seen = _claude(tmp_path / "Scout")
    result = _run(script, claude, log, fake_bin, FAKE_UNAME="Darwin")
    assert result.returncode == 0, result.stderr
    assert seen.read_text().strip() == "stored"
    assert _sign_in(log)["source"] == "keychain"
    calls = (fake_keychain / ".calls").read_text()
    assert "find-generic-password -s scout-claude-oauth-token" in calls
    assert FAKE not in calls + log.read_text()


def test_macos_without_an_item_uses_the_login(tmp_path: Path, home: Path, fake_keychain: Path) -> None:
    script, log, fake_bin, _ = _setup(tmp_path)
    claude, seen = _claude(tmp_path / "Scout")
    _run(script, claude, log, fake_bin, FAKE_UNAME="Darwin")
    assert seen.read_text().strip() == "unset"
    assert _sign_in(log) == {"at": _sign_in(log)["at"], "source": "login", "token_unreadable": False}
    assert "couldn't read" not in log.read_text()


def test_macos_locked_keychain_falls_back_to_the_login_and_says_so(
    tmp_path: Path, home: Path, fake_keychain: Path
) -> None:
    script, log, fake_bin, _ = _setup(tmp_path)
    _keychain_item(fake_keychain)
    claude, seen = _claude(tmp_path / "Scout")
    result = _run(script, claude, log, fake_bin, FAKE_UNAME="Darwin", FAKE_KEYCHAIN_LOCKED="1")
    assert result.returncode == 0
    assert seen.read_text().strip() == "unset"
    text = log.read_text()
    assert "couldn't read the stored token from the macOS Keychain" in text and "security exit 36" in text
    assert _sign_in(log)["source"] == "login" and _sign_in(log)["token_unreadable"] is True


def test_macos_locked_keychain_and_dead_login_banner_mentions_both(
    tmp_path: Path, home: Path, fake_keychain: Path
) -> None:
    script, log, fake_bin, record = _setup(tmp_path)
    _keychain_item(fake_keychain)
    claude, _ = _claude(tmp_path / "Scout", output=AUTH_ERROR, exit_code=1)
    _run(script, claude, log, fake_bin, FAKE_UNAME="Darwin", FAKE_KEYCHAIN_LOCKED="1")
    text = log.read_text()
    assert "this run couldn't read it, so it used your login" in text
    assert "run: claude auth login" in _read(record)


def test_macos_rejected_keychain_token_names_the_token_fix(tmp_path: Path, home: Path, fake_keychain: Path) -> None:
    script, log, fake_bin, record = _setup(tmp_path)
    _keychain_item(fake_keychain)
    claude, _ = _claude(tmp_path / "Scout", output=AUTH_ERROR, exit_code=1)
    _run(script, claude, log, fake_bin, FAKE_UNAME="Darwin")
    expected = "stored long-lived token in the macOS Keychain (item scout-claude-oauth-token) was rejected"
    assert expected in log.read_text()
    assert "run: scoutctl auth setup-token" in _read(record)


def test_unreadable_token_file_falls_back_to_the_login(tmp_path: Path, home: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads any file")
    script, log, fake_bin, _ = _setup(tmp_path)
    path = _store_token(home)
    path.chmod(0o000)
    claude, seen = _claude(tmp_path / "Scout")
    try:
        _run(script, claude, log, fake_bin)
    finally:
        path.chmod(0o600)
    assert seen.read_text().strip() == "unset"
    assert _sign_in(log)["token_unreadable"] is True


def test_sign_in_record_for_file_and_environment(tmp_path: Path, home: Path) -> None:
    script, log, fake_bin, _ = _setup(tmp_path)
    _store_token(home)
    claude, _ = _claude(tmp_path / "Scout")
    _run(script, claude, log, fake_bin)
    assert _sign_in(log)["source"] == "file"
    _run(script, claude, log, fake_bin, CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat01-FAKE-FROM-ENVIRONMENT-00000")
    assert _sign_in(log)["source"] == "environment"
    assert FAKE not in (log.parent / ".claude-sign-in.json").read_text()
