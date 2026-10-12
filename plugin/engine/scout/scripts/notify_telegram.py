"""Telegram Bot API outbound — wraps sendMessage behind ``scoutctl notify telegram``.

Reads ``~/.scout-secrets/telegram-bot-token`` and ``~/.scout-secrets/telegram-chat-id``
(both gitignored, mode 600). Tier controls Telegram's ``disable_notification`` flag:

  - ``info``            → ``disable_notification=True``  (silent push)
  - ``action_required`` → ``disable_notification=False`` (loud push)

Returns an :class:`~scout.events.Event` with ``kind="notification.sent"`` and
``source="cli:notify_telegram"``. Payload shape::

    {"tier": <tier>, "channel": "telegram", "body_chars": <int>, "dry_run": <bool>?}

Used by the Claude session at session-wrap from inside its prompt (via the Bash
tool):

    scoutctl notify telegram --tier action_required --body "..."

This fans out the wrap message to Telegram in addition to Slack DM. The runner
itself stays bash-only in Plan 4; Plan 7 will Pythonize the runner.

Bidirectional Telegram (a return-bridge for inbound replies as feedback signals)
is v0.7+ territory per the event-architecture spec. v0.4 ships the outbound
stub.

**Dry-run semantics.** ``--dry-run`` still reads the secrets so a missing
install fails fast (operator wants to verify, not stub past). It then echoes
what would be POSTed and returns an Event with ``dry_run=True`` instead of
hitting the network.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from scout import paths
from scout.errors import ConfigError, ScoutError
from scout.events import Event
from scout.ids import new_ulid

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4096  # Telegram hard limit per sendMessage

VALID_TIERS = ("info", "action_required")
DEFAULT_TIMEOUT = 10.0

# Duplicate guard (#241): a byte-identical body to the same chat inside this
# window is refused. One row per real send, under the vault's state dir.
DUPLICATE_WINDOW = timedelta(minutes=10)
SENT_LOG = "telegram-sent.jsonl"


class DuplicateSendError(ScoutError):
    """A byte-identical message already reached this chat inside the window."""

    exit_code = 3

    def __init__(self, original_id: str, sent_at: str) -> None:
        super().__init__(f"byte-identical message sent at {sent_at} (id {original_id})")
        self.original_id = original_id
        self.sent_at = sent_at


def secrets_dir() -> Path:
    """The secrets directory, resolved per call (not at import: HOME can change)."""
    return Path.home() / ".scout-secrets"


def _iso(moment: datetime) -> str:
    """Event-style UTC timestamp: millisecond precision, ``Z`` suffix."""
    n = moment.astimezone(UTC)
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}Z"


# ----- duplicate guard -------------------------------------------------------


def _sent_log_path() -> Path | None:
    """The sent-log path, or None when no vault state dir exists (guard skipped).

    Creates only the state dir's leaf, never the vault itself.
    """
    try:
        state = paths.state_dir(paths.data_dir())
        state.mkdir(exist_ok=True)
    except Exception:  # noqa: BLE001 - a send never fails for want of a log
        return None
    return state / SENT_LOG


def _recent_rows(log: Path, now: datetime) -> list[dict[str, str]]:
    """Rows younger than the window; unreadable or malformed content reads as empty."""
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, str]] = []
    for line in lines:
        try:
            row = json.loads(line)
            key, rid, ts = row["key"], row["id"], row["ts"]
            age = now - datetime.fromisoformat(ts)
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(key, str) and isinstance(rid, str) and timedelta(0) <= age < DUPLICATE_WINDOW:
            rows.append({"key": key, "id": rid, "ts": ts})
    return rows


def _record_sent(log: Path, rows: list[dict[str, str]], new_row: dict[str, str]) -> None:
    """Rewrite the log with the live rows plus the new one (atomic; failures swallowed)."""
    tmp = log.with_name(log.name + ".tmp")
    try:
        tmp.write_text("".join(json.dumps(r) + "\n" for r in [*rows, new_row]), encoding="utf-8")
        os.replace(tmp, log)
    except OSError:
        pass


# ----- secret loading ------------------------------------------------------


def _read_secret(name: str) -> str:
    """Read a secret file from :func:`secrets_dir`.

    Raises ``ConfigError`` (exit code 10) with an actionable message if the
    file is missing, unreadable, empty, or has insecure permissions. The
    error names the file path so the operator knows exactly what to fix.

    Permission check: the setup doc tells operators to ``chmod 600`` the
    secret file. We enforce that here — any non-owner permission bit set
    (group/other read/write/execute) is rejected with a chmod hint. A
    token saved with default umask (644) is silently world-readable by
    every other process on the host, so refusing to read it is the
    right default.
    """
    path = secrets_dir() / name
    if not path.exists():
        raise ConfigError(
            f"Missing secret: {path}. Create it (mode 600) — see engine/scout/docs/connectors/telegram-setup.md."
        )
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ConfigError(f"{path} has insecure permissions {oct(mode)}; expected 600. Run: chmod 600 {path}")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise ConfigError(f"Could not read {path}: {e}") from e
    if not value:
        raise ConfigError(f"Secret file is empty: {path}.")
    return value


# ----- body splitting ------------------------------------------------------


def _split_message(body: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Split a body across chunks, each ≤ ``limit`` chars.

    Boundary preference, in order: ``\\n\\n`` (paragraph), ``\\n`` (line),
    ``' '`` (word), then a hard cut at ``limit``. Always returns at least
    one chunk; the empty string returns ``[""]`` (callers should reject
    empty bodies upstream — this function does not).

    The boundary delimiter is consumed by the split (i.e. ``\\n\\n`` between
    two chunks is not preserved in either side). Hard cuts preserve every
    character.
    """
    if len(body) <= limit:
        return [body]

    chunks: list[str] = []
    remaining = body
    while len(remaining) > limit:
        window = remaining[:limit]
        # Prefer paragraph boundary (consume the \n\n).
        cut: int | None = None
        consumed = 0
        idx = window.rfind("\n\n")
        if idx > 0:
            cut = idx
            consumed = 2
        else:
            idx = window.rfind("\n")
            if idx > 0:
                cut = idx
                consumed = 1
            else:
                idx = window.rfind(" ")
                if idx > 0:
                    cut = idx
                    consumed = 1
        if cut is None:
            # Hard cut — no breakable char in the window.
            chunks.append(window)
            remaining = remaining[limit:]
        else:
            chunks.append(remaining[:cut])
            remaining = remaining[cut + consumed :]
    if remaining:
        chunks.append(remaining)
    return chunks


# ----- send ----------------------------------------------------------------


def _build_payload(*, chat_id: str, text: str, tier: str) -> dict[str, Any]:
    """Build the JSON body for a single sendMessage call."""
    return {
        "chat_id": chat_id,
        "text": text,
        "disable_notification": tier == "info",
    }


def send(
    tier: str,
    body: str,
    *,
    dry_run: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    allow_duplicate: bool = False,
    now: datetime | None = None,
    receipt: Callable[[str], None] | None = None,
) -> Event:
    """Send a message via the Telegram Bot API and return an :class:`Event`.

    Validates ``tier`` and ``body``, reads the bot token + chat-id, splits
    long bodies, then issues one POST per chunk (or echoes the request body
    on ``dry_run``). Defense-in-depth: the CLI layer ALSO restricts ``--tier``
    via Typer choices, but this layer rejects unknown tiers in case a Python
    caller bypasses the CLI.

    ``receipt``, when given, is called with the Event id as soon as it is known
    (before any dry-run output; after the last chunk of a real send), so the CLI
    can put ``id=<id>`` first on stderr.

    Raises:
        ValueError: empty body or unknown tier.
        ConfigError: missing secret files (exit code 10).
        DuplicateSendError: a byte-identical body went to this chat inside
            ``DUPLICATE_WINDOW`` and ``allow_duplicate`` is false (exit code 3).
        requests.RequestException: HTTP-layer failure during a real send.
            Bubbles up so the caller knows the send did not complete.
    """
    if not body:
        raise ValueError("body cannot be empty")
    if tier not in VALID_TIERS:
        raise ValueError(f"unknown tier {tier!r}; valid tiers are: {', '.join(VALID_TIERS)}")

    token = _read_secret("telegram-bot-token")
    chat_id = _read_secret("telegram-chat-id")

    now = now or datetime.now(UTC)
    ts = _iso(now)
    key = hashlib.sha256(f"{chat_id}\x00{body}".encode()).hexdigest()
    log = None if dry_run else _sent_log_path()
    rows = _recent_rows(log, now) if log else []
    if not allow_duplicate:
        for row in rows:
            if row["key"] == key:
                raise DuplicateSendError(row["id"], row["ts"])

    chunks = _split_message(body)
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"

    if dry_run:
        dry_event = Event(
            id=new_ulid(),
            ts=ts,
            kind="notification.sent",
            source="cli:notify_telegram",
            payload={
                "tier": tier,
                "channel": "telegram",
                "body_chars": len(body),
                "dry_run": True,
            },
        )
        if receipt:
            receipt(dry_event.id)  # before the preamble, so the id is the first stderr line
        # Dry-run preamble goes to STDERR so stdout stays pure JSON for
        # downstream parsers (the CLI prints the Event to stdout after).
        # The Telegram URL embeds the bot token in the path
        # (``/bot<token>/sendMessage``), so we MUST redact it before
        # printing — operators reading shell history or stderr-redirected
        # logs should never see the raw token.
        redacted_url = f"{TELEGRAM_API}/bot<REDACTED>/sendMessage"
        for chunk in chunks:
            payload = _build_payload(chat_id=chat_id, text=chunk, tier=tier)
            print(f"[dry-run] POST {redacted_url}", file=sys.stderr)
            print(
                f"[dry-run] body: {json.dumps(payload, ensure_ascii=False)}",
                file=sys.stderr,
            )
        return dry_event

    for chunk in chunks:
        payload = _build_payload(chat_id=chat_id, text=chunk, tier=tier)
        resp = requests.post(url, json=payload, timeout=timeout)
        # Surface HTTP errors so the caller sees a non-zero exit. The CLI
        # layer maps requests.RequestException to a non-zero exit code.
        resp.raise_for_status()

    ev = Event(
        id=new_ulid(),
        ts=ts,
        kind="notification.sent",
        source="cli:notify_telegram",
        payload={
            "tier": tier,
            "channel": "telegram",
            "body_chars": len(body),
        },
    )
    if log:
        # Recorded only after every chunk posted: one row per logical send.
        _record_sent(log, rows, {"key": key, "id": ev.id, "ts": ts})
    if receipt:
        receipt(ev.id)
    return ev


# ----- CLI entry -----------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Direct-call entry point (rarely used; the Typer CLI is the real path).

    - ConfigError → exit 10 (default ScoutError code; runner needs to know).
    - ValueError → exit 1.
    - HTTP errors → exit 2.
    - Success → exit 0; the Event JSON is printed to stdout.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="notify-telegram")
    parser.add_argument("--tier", default="info", choices=list(VALID_TIERS))
    parser.add_argument("--body", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        ev = send(tier=args.tier, body=args.body, dry_run=args.dry_run)
    except ConfigError as e:
        print(f"notify-telegram: {e}")
        return ConfigError.exit_code
    except DuplicateSendError as e:
        print(f"notify-telegram: refused — {e}")
        return DuplicateSendError.exit_code
    except ValueError as e:
        print(f"notify-telegram: {e}")
        return 1
    except requests.HTTPError as e:
        # ``str(HTTPError)`` includes the request URL, which embeds the
        # bot token. Rebuild the message from status_code + reason.
        status = getattr(e.response, "status_code", "?")
        reason = getattr(e.response, "reason", "Unknown")
        print(f"notify-telegram: HTTP {status} {reason} (token redacted in URL)")
        return 2
    except requests.RequestException as e:
        # Non-HTTP failures (timeout, DNS, connection refused) — these
        # don't carry the URL in their str() representation.
        print(f"notify-telegram: HTTP error: {e}")
        return 2

    print(json.dumps(asdict(ev), indent=2))
    return 0
