"""`scoutctl notify slack` DMs the owner as the Scout app bot, never as the owner.

The recipient is always ``user.slack_user_id``; the body passes the ``slack``
outbound check (the workspace is shared) before anything is posted; the bot
token rides in the Authorization header and never reaches stderr.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from typer.testing import CliRunner

from scout.cli import app
from scout.errors import ConfigError
from scout.kb import outbound
from scout.kb.ontology import clear_frontmatter_cache
from scout.scripts import notify_slack
from scout.scripts.notify_slack import DuplicateSendError, OutboundBlockedError, SlackAPIError, send
from tests.unit.privacy_vault_helpers import build_privacy_vault

TOKEN = "xoxb-1111-2222-standintoken"
OWNER = "U0OWNER123"
PRIVATE_BODY = "Today:\n- Remember task-renew-permit before Friday.\n- Review PROJ-1234 with Priya."
CLEAN_BODY = "Today:\n- Review PROJ-1234 with Priya."


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    clear_frontmatter_cache()
    outbound.clear_cache()
    v = build_privacy_vault(tmp_path)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    (v / "scout-config.yaml").write_text(f"user:\n  slack_user_id: {OWNER}\n", encoding="utf-8")
    return v


def _token(value: str = TOKEN, mode: int = 0o600) -> Path:
    secrets = Path.home() / ".scout-secrets"
    secrets.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = secrets / "slack-bot-token"
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def _resp(data: dict[str, Any], status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


def _slack(*posted_ts: str) -> list[MagicMock]:
    return [_resp({"ok": True, "channel": {"id": "D0SCOUTDM"}})] + [_resp({"ok": True, "ts": t}) for t in posted_ts]


def test_dms_the_owner_through_the_bot(vault: Path) -> None:
    _token()
    with patch("scout.scripts.notify_slack.requests.post", side_effect=_slack("111.222")) as post:
        ev = send(CLEAN_BODY)

    open_call, post_call = post.call_args_list
    assert open_call.args[0].endswith("/conversations.open")
    assert open_call.kwargs["json"] == {"users": OWNER}
    assert post_call.args[0].endswith("/chat.postMessage")
    assert post_call.kwargs["json"]["channel"] == "D0SCOUTDM"
    assert post_call.kwargs["json"]["text"] == CLEAN_BODY
    assert "thread_ts" not in post_call.kwargs["json"]
    for call in post.call_args_list:
        assert call.kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in call.args[0]
    assert ev.source == "cli:notify_slack"
    assert ev.payload == {"channel": "slack", "dm_channel": "D0SCOUTDM", "ts": "111.222", "body_chars": len(CLEAN_BODY)}


def test_thread_ts_replies_in_the_thread(vault: Path) -> None:
    _token()
    with patch("scout.scripts.notify_slack.requests.post", side_effect=_slack("111.333")) as post:
        ev = send(CLEAN_BODY, thread_ts="100.000")
    assert post.call_args_list[1].kwargs["json"]["thread_ts"] == "100.000"
    assert ev.payload["thread_ts"] == "100.000"


def test_long_bodies_split_and_report_the_first_ts(vault: Path) -> None:
    _token()
    body = ("word " * 700 + "\n\n") * 2
    with patch("scout.scripts.notify_slack.requests.post", side_effect=_slack("1.1", "1.2")) as post:
        ev = send(body)
    texts = [c.kwargs["json"]["text"] for c in post.call_args_list[1:]]
    assert len(texts) == 2 and all(len(t) <= notify_slack.MAX_MESSAGE_LEN for t in texts)
    assert ev.payload["ts"] == "1.1"


def test_a_private_note_is_dropped_before_any_request(vault: Path) -> None:
    _token()
    with patch("scout.scripts.notify_slack.requests.post") as post, pytest.raises(OutboundBlockedError):
        send(PRIVATE_BODY)
    post.assert_not_called()
    events = [
        json.loads(ln)
        for p in (vault / ".scout-logs").glob("schedule-events-*.jsonl")
        for ln in p.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    blocked = [e for e in events if e["kind"] == "privacy.outbound_blocked"]
    assert blocked and blocked[-1]["source"] == "cli:notify_slack"
    assert blocked[-1]["payload"]["channel"] == "slack"


def test_no_vault_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _token()
    monkeypatch.setattr(notify_slack, "_owner_id", lambda: OWNER)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "nowhere"))
    with patch("scout.scripts.notify_slack.requests.post") as post, pytest.raises(OutboundBlockedError):
        send(CLEAN_BODY)
    post.assert_not_called()


def test_a_byte_identical_resend_is_refused(vault: Path) -> None:
    _token()
    with patch("scout.scripts.notify_slack.requests.post", side_effect=_slack("1.1") + _slack("1.2")):
        send(CLEAN_BODY)
        with pytest.raises(DuplicateSendError):
            send(CLEAN_BODY)
        send(CLEAN_BODY, thread_ts="1.1")  # another thread is another conversation


@pytest.mark.parametrize(
    ("value", "mode", "match"),
    [
        (TOKEN, 0o644, "insecure permissions"),
        ("xoxp-user-token", 0o600, "bot token"),
    ],
)
def test_bad_tokens_are_config_errors(vault: Path, value: str, mode: int, match: str) -> None:
    _token(value, mode)
    with pytest.raises(ConfigError, match=match):
        send(CLEAN_BODY)


def test_missing_token_names_the_setup_doc(vault: Path) -> None:
    with pytest.raises(ConfigError, match="slack-bot-setup.md"):
        send(CLEAN_BODY)


def test_missing_owner_id_is_a_config_error(vault: Path) -> None:
    _token()
    (vault / "scout-config.yaml").write_text("user: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="slack_user_id"):
        send(CLEAN_BODY)


def test_slack_ok_false_raises(vault: Path) -> None:
    _token()
    with (
        patch("scout.scripts.notify_slack.requests.post", return_value=_resp({"ok": False, "error": "missing_scope"})),
        pytest.raises(SlackAPIError, match="missing_scope"),
    ):
        send(CLEAN_BODY)


def test_dry_run_posts_nothing(vault: Path) -> None:
    _token()
    with patch("scout.scripts.notify_slack.requests.post") as post:
        ev = send(CLEAN_BODY, dry_run=True)
    post.assert_not_called()
    assert ev.payload["dry_run"] is True


def test_cli_exit_codes_and_no_token_on_stderr(vault: Path) -> None:
    _token()
    runner = CliRunner()
    with patch("scout.scripts.notify_slack.requests.post", side_effect=_slack("9.9")):
        ok = runner.invoke(app, ["notify", "slack", "--body", CLEAN_BODY])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.stdout)["payload"]["ts"] == "9.9"

    blocked = runner.invoke(app, ["notify", "slack", "--body", PRIVATE_BODY])
    assert blocked.exit_code == 4

    err = requests.ConnectionError(f"boom Authorization: Bearer {TOKEN}")
    with patch("scout.scripts.notify_slack.requests.post", side_effect=err):
        failed = runner.invoke(app, ["notify", "slack", "--body", "another body"])
    assert failed.exit_code == 2
    assert TOKEN not in failed.output
