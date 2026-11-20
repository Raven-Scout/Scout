"""One clean line of text from a field a run, a log or a connector could have written.

Ledgers and alert files under ``.scout-logs/`` are written by runs, so a field meant to hold one sentence can
hold anything: newlines that start a new markdown heading, terminal escapes, bidi overrides, zero-width
characters. ``one_line`` folds every kind of whitespace (including U+2028, U+2029 and U+0085) to one space,
drops Unicode control and format characters (categories Cc and Cf), and clamps the result.
"""

from __future__ import annotations

import unicodedata

LINE_MAX = 300


def one_line(value: object, *, limit: int = LINE_MAX, default: str = "") -> str:
    """``value`` as one printable line of at most ``limit`` characters; ``default`` when it isn't a string."""
    if not isinstance(value, str):
        return default
    kept = "".join(" " if ch.isspace() else ch for ch in value if ch.isspace() or not _hidden(ch))
    text = " ".join(kept.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _hidden(ch: str) -> bool:
    return unicodedata.category(ch) in ("Cc", "Cf")
