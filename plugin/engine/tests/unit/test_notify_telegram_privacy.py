"""`scoutctl notify telegram` classifies its target (#453, spec §8.4, §11.2 Telegram).

A positive chat id is a private chat with the owner (``telegram_private_chat``,
owner-only): it sends as before. A group or channel (id below zero, or a
``@channel`` name) is ``telegram_group_chat`` (shared): the body goes through
``check_outbound`` first, and a hit drops the message with a
``privacy.outbound_blocked`` event and a blocked line in the kb audit log. A
group send with no vault to check against is refused (fail closed); a private
send works without one, as today.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.kb import outbound
from scout.kb.ontology import clear_frontmatter_cache
from scout.scripts import notify_telegram
from scout.scripts.notify_telegram import OutboundBlockedError, send, target_surface
from tests.unit.privacy_vault_helpers import GROUP_CHAT_ID, PRIVATE_CHAT_ID, build_privacy_vault, privacy_secrets

# A wrap-message line naming a private note (the permit task's slug).
PRIVATE_BODY = "Today:\n- Remember task-renew-permit before Friday.\n- Review PROJ-1234 with Priya."
CLEAN_BODY = "Today:\n- Review PROJ-1234 with Priya."


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    clear_frontmatter_cache()
    outbound.clear_cache()
    v = build_privacy_vault(tmp_path)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def _ok() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    return resp


def _events(vault: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted((vault / ".scout-logs").glob("schedule-events-*.jsonl")):
        rows += [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return rows


def _reads(vault: Path) -> list[dict[str, object]]:
    path = vault / ".scout-logs" / "kb-reads.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    ("chat_id", "surface"),
    [
        ("100200300", "telegram_private_chat"),
        ("-100400500", "telegram_group_chat"),
        ("-42", "telegram_group_chat"),
        ("@example_channel", "telegram_group_chat"),
        ("0", "telegram_group_chat"),
        ("", "telegram_group_chat"),
    ],
)
def test_target_surface(chat_id: str, surface: str) -> None:
    assert target_surface(chat_id) == surface


def test_a_private_chat_gets_the_message_as_today(vault: Path) -> None:
    privacy_secrets(Path.home(), PRIVATE_CHAT_ID)
    with patch("scout.scripts.notify_telegram.requests.post", return_value=_ok()) as post:
        ev = send(tier="info", body=PRIVATE_BODY)
    assert post.call_count == 1
    assert post.call_args.kwargs["json"]["text"] == PRIVATE_BODY
    assert ev.kind == "notification.sent"
    assert _events(vault) == [] and _reads(vault) == []


def test_a_group_chat_drops_a_message_naming_a_private_note(vault: Path) -> None:
    privacy_secrets(Path.home(), GROUP_CHAT_ID)
    with patch("scout.scripts.notify_telegram.requests.post", return_value=_ok()) as post:
        with pytest.raises(OutboundBlockedError) as exc:
            send(tier="info", body=PRIVATE_BODY)
    post.assert_not_called()
    assert "line 2" in str(exc.value)
    assert "permit" not in str(exc.value)
    (event,) = _events(vault)
    assert event["kind"] == "privacy.outbound_blocked"
    assert event["payload"] == {"surface": "telegram_group_chat", "channel": "telegram", "lines": [2]}
    (row,) = _reads(vault)
    assert row["blocked"] is True and row["surface"] == "telegram_group_chat"
    assert "permit" not in json.dumps(row)


@pytest.mark.parametrize(
    "body", ["Ask <b>Sam</b>my about it.", "Remember task&#45;renew&#45;permit.", "Ask Sa<i></i>mmy."]
)
def test_html_mode_checks_what_telegram_shows(vault: Path, body: str) -> None:
    privacy_secrets(Path.home(), GROUP_CHAT_ID)
    with patch("scout.scripts.notify_telegram.requests.post", return_value=_ok()) as post:
        with pytest.raises(OutboundBlockedError):
            send(tier="info", body=body, parse_mode="html")
    post.assert_not_called()


def test_a_dry_run_to_a_group_is_checked_too(vault: Path) -> None:
    privacy_secrets(Path.home(), GROUP_CHAT_ID)
    with pytest.raises(OutboundBlockedError):
        send(tier="info", body=PRIVATE_BODY, dry_run=True)


def test_a_group_chat_gets_a_clean_message(vault: Path) -> None:
    privacy_secrets(Path.home(), GROUP_CHAT_ID)
    with patch("scout.scripts.notify_telegram.requests.post", return_value=_ok()) as post:
        send(tier="info", body=CLEAN_BODY)
    assert post.call_count == 1
    assert _events(vault) == [] and _reads(vault) == []


def test_without_a_vault_a_private_send_works_and_a_group_send_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no scout-config.yaml anywhere above, no SCOUT_DATA_DIR, empty HOME
    privacy_secrets(Path.home(), PRIVATE_CHAT_ID)
    with patch("scout.scripts.notify_telegram.requests.post", return_value=_ok()) as post:
        send(tier="info", body=CLEAN_BODY)
    assert post.call_count == 1

    privacy_secrets(Path.home(), GROUP_CHAT_ID)
    with patch("scout.scripts.notify_telegram.requests.post", return_value=_ok()) as post:
        with pytest.raises(OutboundBlockedError) as exc:
            send(tier="info", body=CLEAN_BODY)
    post.assert_not_called()
    assert "no Scout vault" in str(exc.value)


def test_the_cli_exits_non_zero_without_content(vault: Path) -> None:
    privacy_secrets(Path.home(), GROUP_CHAT_ID)
    with patch("scout.scripts.notify_telegram.requests.post", return_value=_ok()) as post:
        result = CliRunner().invoke(app, ["notify", "telegram", "--body", PRIVATE_BODY])
    post.assert_not_called()
    assert result.exit_code == OutboundBlockedError.exit_code != 0
    assert "permit" not in result.output
    assert "refused" in result.output


def test_main_exits_non_zero(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    privacy_secrets(Path.home(), GROUP_CHAT_ID)
    with patch("scout.scripts.notify_telegram.requests.post", return_value=_ok()):
        rc = notify_telegram.main(["--body", PRIVATE_BODY])
    assert rc == OutboundBlockedError.exit_code
    assert "permit" not in capsys.readouterr().out


def test_a_group_send_is_refused_when_the_vault_lookup_errors(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    privacy_secrets(Path.home(), GROUP_CHAT_ID)

    def broken() -> Path:
        raise OSError("cwd gone")

    monkeypatch.setattr(notify_telegram.paths, "data_dir", broken)
    with patch("scout.scripts.notify_telegram.requests.post", return_value=_ok()) as post:
        with pytest.raises(OutboundBlockedError, match="no Scout vault"):
            send(tier="info", body=CLEAN_BODY)
    post.assert_not_called()


def test_a_failed_event_write_still_drops_the_message(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    privacy_secrets(Path.home(), GROUP_CHAT_ID)

    def full_disk(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(notify_telegram, "append_event", full_disk)
    with patch("scout.scripts.notify_telegram.requests.post", return_value=_ok()) as post:
        with pytest.raises(OutboundBlockedError):
            send(tier="info", body=PRIVATE_BODY)
    post.assert_not_called()
    assert _reads(vault)[0]["blocked"] is True
