"""`scoutctl auth setup-token | status | clear` (#267, run-health P6).

`claude` is never run for real: the interactive flow is driven through
``claude_auth.setup_token`` with injected callables, and the CLI's `--stdin` path
doesn't start Claude at all. HOME is a per-test temp dir (conftest).
"""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import claude_auth
from scout.cli import app

runner = CliRunner()
FAKE = "sk-ant-oat01-FAKE-TEST-TOKEN-0000000000000000"
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _file_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests read the 0600 file directly; the Keychain backend has tests of its own."""
    monkeypatch.setattr(claude_auth, "_system", lambda: "Linux")


def _token_path(home: Path) -> Path:
    return home / ".config" / "scout" / "claude-oauth-token"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    return h


# --- the interactive flow -------------------------------------------------------


class _Flow:
    def __init__(self, *, claude_exit: int = 0, pasted: str | BaseException = FAKE, interactive: bool = True):
        self.ran: list[list[str]] = []
        self.said: list[str] = []
        self.claude_exit = claude_exit
        self.pasted = pasted
        self.interactive = interactive

    def run(self, argv: list[str]) -> int:
        self.ran.append(argv)
        return self.claude_exit

    def read_secret(self, prompt: str) -> str:
        self.said.append(prompt)
        if isinstance(self.pasted, BaseException):
            raise self.pasted
        return self.pasted

    def __call__(self, store: claude_auth.TokenStore) -> int:
        return claude_auth.setup_token(
            store,
            claude_bin="/opt/fake/claude",
            interactive=self.interactive,
            run=self.run,
            read_secret=self.read_secret,
            say=self.said.append,
            now=NOW,
        )


def test_setup_token_runs_claude_then_stores_the_pasted_token(home: Path) -> None:
    store = claude_auth.default_store()
    flow = _Flow(pasted=f"export CLAUDE_CODE_OAUTH_TOKEN={FAKE}\n")
    assert flow(store) == 0
    assert flow.ran == [["/opt/fake/claude", "setup-token"]]
    assert store.read() == FAKE
    assert stat.S_IMODE(_token_path(home).stat().st_mode) == 0o600
    out = "\n".join(flow.said)
    assert FAKE not in out
    assert "2027-10-12" in out and "scoutctl auth clear" in out


def test_setup_token_without_a_terminal_refuses_and_points_at_stdin(home: Path) -> None:
    flow = _Flow(interactive=False)
    assert flow(claude_auth.default_store()) == 2
    assert flow.ran == []
    assert any("--stdin" in s for s in flow.said)


def test_setup_token_stops_when_claude_fails(home: Path) -> None:
    store = claude_auth.default_store()
    flow = _Flow(claude_exit=1)
    assert flow(store) == 2
    assert not store.exists()
    assert any("claude setup-token failed (exit 1)" in s for s in flow.said)


def test_setup_token_stops_when_claude_is_missing(home: Path) -> None:
    store = claude_auth.default_store()

    def missing(argv: list[str]) -> int:
        raise FileNotFoundError(argv[0])

    code = claude_auth.setup_token(
        store, claude_bin="/nope/claude", interactive=True, run=missing, read_secret=lambda p: FAKE, say=lambda s: None
    )
    assert code == 2
    assert not store.exists()


def test_setup_token_rejects_a_bad_paste_and_keeps_the_old_token(home: Path) -> None:
    store = claude_auth.default_store()
    claude_auth.save_token(store, "sk-ant-oat01-FAKE-OLD-TOKEN-000000000", now=NOW)
    flow = _Flow(pasted="not a token")
    assert flow(store) == 1
    assert store.read() == "sk-ant-oat01-FAKE-OLD-TOKEN-000000000"


@pytest.mark.parametrize("exc", [EOFError(), KeyboardInterrupt()])
def test_setup_token_cancelled_at_the_paste_stores_nothing(home: Path, exc: BaseException) -> None:
    store = claude_auth.default_store()
    assert _Flow(pasted=exc)(store) == 1
    assert not store.exists()


def test_setup_token_store_write_failure_is_reported(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = claude_auth.default_store()

    def boom(*a: object, **k: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(claude_auth, "save_token", boom)
    flow = _Flow()
    assert flow(store) == 2
    assert any("couldn't store the token" in s for s in flow.said)


# --- the CLI ------------------------------------------------------------------------


def test_cli_setup_token_stdin_stores_without_running_claude(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_auth, "_run_inherited", lambda argv: pytest.fail("claude must not run under --stdin"))
    result = runner.invoke(app, ["auth", "setup-token", "--stdin"], input=FAKE + "\n")
    assert result.exit_code == 0, result.output
    assert _token_path(home).read_text(encoding="utf-8").strip() == FAKE
    assert FAKE not in result.output
    assert "long-lived token" in result.output


def test_cli_setup_token_stdin_rejects_garbage(home: Path) -> None:
    result = runner.invoke(app, ["auth", "setup-token", "--stdin"], input="\n")
    assert result.exit_code == 1
    assert not _token_path(home).exists()


def test_cli_setup_token_interactive_uses_the_resolved_claude(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(claude_auth, "_run_inherited", lambda argv: calls.append(argv) or 0)
    monkeypatch.setattr(claude_auth, "_read_secret", lambda prompt: FAKE)
    monkeypatch.setattr(claude_auth, "_has_terminal", lambda: True)
    result = runner.invoke(app, ["auth", "setup-token", "--claude-bin", "/opt/fake/claude"])
    assert result.exit_code == 0, result.output
    assert calls == [["/opt/fake/claude", "setup-token"]]
    assert _token_path(home).exists()


def test_cli_setup_token_without_terminal(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_auth, "_has_terminal", lambda: False)
    result = runner.invoke(app, ["auth", "setup-token", "--claude-bin", "/opt/fake/claude"])
    assert result.exit_code == 2
    assert "--stdin" in result.output


def test_cli_status_login_mode(home: Path) -> None:
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "interactive Claude login" in result.output


def test_cli_status_json_never_contains_the_token(home: Path) -> None:
    claude_auth.save_token(claude_auth.default_store(), FAKE, now=datetime.now(UTC) - timedelta(days=1))
    result = runner.invoke(app, ["auth", "status", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["mode"] == "token" and data["store"] == "file"
    assert data["location"] == str(_token_path(home))
    assert data["days_left"] == 363
    assert FAKE not in result.output


def test_cli_status_text_lists_problems_and_expiry(home: Path) -> None:
    store = claude_auth.default_store()
    claude_auth.save_token(store, FAKE, now=datetime.now(UTC) - timedelta(days=340))
    _token_path(home).chmod(0o644)
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 1
    assert "chmod 600" in result.output
    assert "renew" in result.output


def test_cli_status_shadowed_by_environment(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    claude_auth.save_token(claude_auth.default_store(), FAKE, now=datetime.now(UTC))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-FAKE-FROM-ENVIRONMENT-00000")
    result = runner.invoke(app, ["auth", "status"])
    assert "CLAUDE_CODE_OAUTH_TOKEN is set in this shell" in result.output
    assert "FAKE-FROM-ENVIRONMENT" not in result.output


def test_cli_clear(home: Path) -> None:
    claude_auth.save_token(claude_auth.default_store(), FAKE, now=NOW)
    result = runner.invoke(app, ["auth", "clear"])
    assert result.exit_code == 0
    assert not _token_path(home).exists()
    assert "interactive Claude login" in result.output
    again = runner.invoke(app, ["auth", "clear"])
    assert again.exit_code == 0
    assert "No stored token" in again.output


# --- macOS: the same commands against the Keychain (conftest's fake `security`) -------


@pytest.fixture
def keychain(monkeypatch: pytest.MonkeyPatch, fake_keychain: Path) -> Path:
    monkeypatch.setattr(claude_auth, "_system", lambda: "Darwin")
    return fake_keychain


def _items(keychain: Path) -> list[Path]:
    return [p for p in keychain.iterdir() if p.name.startswith("scout-claude-oauth-token@")]


def test_cli_keychain_setup_status_clear(home: Path, keychain: Path) -> None:
    result = runner.invoke(app, ["auth", "setup-token", "--stdin"], input=FAKE + "\n")
    assert result.exit_code == 0, result.output
    assert "macOS Keychain" in result.output and FAKE not in result.output
    (item,) = _items(keychain)
    assert item.read_text() == FAKE
    assert not _token_path(home).exists()  # no plaintext copy on macOS
    assert (home / ".config" / "scout" / "claude-oauth-token.meta.json").exists()

    status = runner.invoke(app, ["auth", "status", "--json"])
    data = json.loads(status.output)
    assert data["mode"] == "token" and data["store"] == "keychain"
    assert FAKE not in status.output

    cleared = runner.invoke(app, ["auth", "clear"])
    assert cleared.exit_code == 0
    assert _items(keychain) == []


def test_cli_keychain_locked_status_reports_it(home: Path, keychain: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner.invoke(app, ["auth", "setup-token", "--stdin"], input=FAKE + "\n")
    monkeypatch.setenv("FAKE_KEYCHAIN_LOCKED", "1")
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 1
    assert "may be locked" in result.output


def test_cli_clear_reports_a_keychain_failure(home: Path, keychain: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self: object) -> bool:
        raise OSError("the Keychain refused to remove the token (security exit 51); it may be locked")

    monkeypatch.setattr(claude_auth.KeychainTokenStore, "delete", refuse)
    result = runner.invoke(app, ["auth", "clear"])
    assert result.exit_code == 2
    assert "Nothing removed" in result.output
