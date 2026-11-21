"""Cheap-message classifier: acknowledgements that need no Scout run."""

from __future__ import annotations

CHEAP_ACKS = frozenset({"thanks", "thank you", "thx", "ty", "ok", "okay", "got it", "cool", "nice"})
_MAX_CHEAP_CHARS = 15
_TRAILING_PUNCT = " !.,"


def is_cheap(text: str) -> bool:
    """True for a short ack ("thanks", "ok") or text with no letters or digits (emoji, punctuation)."""
    t = text.strip().casefold()
    if len(t) > _MAX_CHEAP_CHARS:
        return False
    return t.rstrip(_TRAILING_PUNCT) in CHEAP_ACKS or not any(c.isalnum() for c in t)
