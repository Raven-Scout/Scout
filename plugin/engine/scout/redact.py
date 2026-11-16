"""Mask credentials in text bound for output, logs or state files.

Some connectors put a credential in the request URL: the Telegram Bot API
path is ``/bot<token>/sendMessage``. ``requests`` copies that URL into
``str()`` of its exceptions, so an error message is enough to leak the token.
Apply :func:`redact_secrets` to any error text that leaves the process.
"""

from __future__ import annotations

import re

# A Telegram bot token in a URL path: bot<digits>:<secret>, colon maybe percent-encoded.
_TELEGRAM_BOT_PATH = re.compile(r"bot\d+(?::|%3[Aa])[A-Za-z0-9_-]+")
# A bare Telegram token (numeric bot id, 30+ character secret).
_TELEGRAM_TOKEN = re.compile(r"(?<![\w])\d{6,}(?::|%3[Aa])[A-Za-z0-9_-]{30,}")
# Slack tokens and bearer credentials, should one ever reach an error message.
_SLACK_TOKEN = re.compile(r"xox[abposr]-[A-Za-z0-9-]+")
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+")


def redact_secrets(text: str, *secrets: str) -> str:
    """``text`` with known credential shapes, and each of ``secrets``, masked."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    text = _TELEGRAM_BOT_PATH.sub("bot<redacted>", text)
    text = _TELEGRAM_TOKEN.sub("<redacted>", text)
    text = _SLACK_TOKEN.sub("xox-<redacted>", text)
    return _BEARER.sub(r"\1<redacted>", text)
