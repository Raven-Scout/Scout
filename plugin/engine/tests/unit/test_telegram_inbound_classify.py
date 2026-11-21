from __future__ import annotations

import pytest

from scout.telegram_inbound.classify import is_cheap


@pytest.mark.parametrize("text", ["Thanks!", "👍", "ok", "", "  Thank you  ", "OKAY", "🙏🙏"])
def test_cheap(text: str) -> None:
    assert is_cheap(text)


@pytest.mark.parametrize("text", ["ok drop the invoice item", "why?", "thanks a lot for that", "42"])
def test_not_cheap(text: str) -> None:
    assert not is_cheap(text)


def test_long_emoji_only_is_not_cheap() -> None:
    assert not is_cheap("👍" * 16)


@pytest.mark.parametrize("text", ["ok", "👍", "thanks", ""])
def test_a_reply_to_a_scout_message_is_never_cheap(text: str) -> None:
    """An "ok" under Scout's question is an approval, not an ack."""
    assert not is_cheap(text, reply_to_text="Shall I open the draft PR?")
    assert not is_cheap(text, reply_to_text="")  # a reply at all, even to a message with no text
    assert is_cheap(text, reply_to_text=None)


@pytest.mark.parametrize("text", ["/start", "/START", " /Start "])
def test_start_command_is_cheap(text: str) -> None:
    assert is_cheap(text)


@pytest.mark.parametrize("text", ["/start now", "/started", "/stop", "start"])
def test_other_commands_and_words_are_not_cheap(text: str) -> None:
    assert not is_cheap(text)
