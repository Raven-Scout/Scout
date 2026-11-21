"""Cheap-message classifier: acknowledgements that need no Scout run."""

from __future__ import annotations

CHEAP_ACKS = frozenset({"thanks", "thank you", "thx", "ty", "ok", "okay", "got it", "cool", "nice"})
_MAX_CHEAP_CHARS = 15
_TRAILING_PUNCT = " !.,"
_START = "/start"  # Telegram sends it when a chat with the bot opens: nothing to answer


def is_cheap(text: str, *, reply_to_text: str | None = None) -> bool:
    """True for a short ack ("thanks", "ok"), ``/start``, or text with no letters or digits (emoji, punctuation).

    A reply to an earlier message (``reply_to_text`` set) is never cheap: "ok" under Scout's question
    is an approval.
    """
    if reply_to_text is not None:
        return False
    t = text.strip().casefold()
    if len(t) > _MAX_CHEAP_CHARS:
        return False
    return t == _START or t.rstrip(_TRAILING_PUNCT) in CHEAP_ACKS or not any(c.isalnum() for c in t)
