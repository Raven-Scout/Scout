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
