"""Telegram Bot API outbound — wraps sendMessage behind ``scoutctl notify telegram``.

Reads ``~/.scout-secrets/telegram-bot-token`` and ``~/.scout-secrets/telegram-chat-id``
(both gitignored, mode 600). Tier controls Telegram's ``disable_notification`` flag:

  - ``info``            → ``disable_notification=True``  (silent push)
  - ``action_required`` → ``disable_notification=False`` (loud push)

Returns an :class:`~scout.events.Event` with ``kind="notification.sent"`` and
``source="cli:notify_telegram"``. Payload shape::

    {"tier": <tier>, "channel": "telegram", "body_chars": <int>, "dry_run": <bool>?,
     "parse_mode": "html"?, "parse_fallback": <bool>?}

``parse_mode`` appears only for ``--parse-mode html``; ``parse_fallback`` only
when Telegram rejected the HTML of at least one chunk and it went out as plain
text instead.

**HTML mode.** ``--parse-mode html`` sends with Telegram's ``parse_mode=HTML``.
HTML, not MarkdownV2: HTML needs only ``<``, ``>`` and ``&`` escaped, where
MarkdownV2 needs ~18 characters escaped, and one miss in an LLM-written body
gets the whole message refused. Telegram accepts ``<b> <i> <u> <s> <a href>
<code> <pre> <blockquote>`` (and a few more). Before sending, a link whose
href is not http(s) or mailto is replaced by its label, with a warning on
stderr: Telegram would keep a ``tg://`` deep link live, and silently drop any
other scheme. Two safety nets:

  - Long bodies split tag-aware: a cut never lands inside a tag or an entity,
    and tags open at the cut are closed at the end of the chunk and reopened
    at the start of the next.
  - If Telegram answers 400 "can't parse entities", that chunk is resent as
    plain text (tags stripped, link URLs kept in parentheses, entities
    unescaped), with a warning on stderr. A formatting mistake never loses the
    message.

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

import functools
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from scout import paths
from scout.action_items.render import safe_href
from scout.errors import ConfigError, ScoutError
from scout.events import Event
from scout.ids import new_ulid
from scout.redact import redact_secrets

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4096  # Telegram hard limit per sendMessage

VALID_TIERS = ("info", "action_required")
VALID_PARSE_MODES = ("none", "html")
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
        cut, consumed = _find_cut(remaining[:limit])
        chunks.append(remaining[:cut])
        remaining = remaining[cut + consumed :]
    if remaining:
        chunks.append(remaining)
    return chunks


def _find_cut(window: str, ok: Callable[[int], bool] = lambda _: True) -> tuple[int, int]:
    """Where to end a chunk inside ``window``: ``(cut, delimiter chars consumed)``.

    Paragraph, then line, then word boundary; a hard cut at the window's end
    (nothing consumed) when none of them is past position 0. A boundary at a
    position ``ok`` rejects is skipped, and the search goes on to its left.
    """
    for delim in ("\n\n", "\n", " "):
        idx = window.rfind(delim)
        while idx > 0 and not ok(idx):
            idx = window.rfind(delim, 0, idx)
        if idx > 0:
            return idx, len(delim)
    return len(window), 0


# ----- HTML mode -------------------------------------------------------------

_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)[^<>]*>")
_ENTITY_RE = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);")
# A link with its href quoted or not (Telegram accepts both): groups `qurl` or `url`, then `label`.
_LINK_RE = re.compile(
    r"""<a\s[^<>]*?href\s*=\s*(?:(["'])(?P<qurl>.*?)\1|(?P<url>[^\s"'<>]+))[^<>]*>(?P<label>.*?)</a\s*>""",
    re.IGNORECASE | re.DOTALL,
)


def _safe_html_cut(text: str, cut: int) -> int:
    """Move ``cut`` back to the start of a tag or entity it would split."""
    lt = text.rfind("<", 0, cut)
    if lt != -1 and ">" not in text[lt:cut]:
        return lt
    amp = text.rfind("&", 0, cut)
    if amp != -1:
        m = _ENTITY_RE.match(text, amp)
        if m and m.end() > cut:
            return amp
    return cut


def _open_tags_after(piece: str, stack: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The ``(name, opening tag)`` stack still open after ``piece``, given ``stack`` before it."""
    stack = list(stack)
    for m in _TAG_RE.finditer(piece):
        name = m.group(2).lower()
        if not m.group(1):
            stack.append((name, m.group(0)))
            continue
        for i in range(len(stack) - 1, -1, -1):
            if stack[i][0] == name:
                del stack[i:]
                break
    return stack


def _split_html(body: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """:func:`_split_message` for Telegram HTML: every chunk is balanced on its own.

    A cut never lands inside a tag or an entity. Tags still open at a cut are
    closed at the end of that chunk and reopened (with their attributes) at the
    start of the next, and both count against ``limit``. Unbalanced input is
    not repaired beyond that: a stray closing tag still goes out, Telegram
    refuses it, and the plain-text fallback in :func:`send` takes over.

    A body without tags or entities splits as :func:`_split_message` does,
    except that a chunk of only whitespace is skipped (Telegram refuses an
    empty message).
    """
    if len(body) <= limit:
        return [body]

    chunks: list[str] = []
    stack: list[tuple[str, str]] = []
    remaining = body
    while remaining:
        stack = _carried(stack, limit)
        prefix = "".join(tag for _, tag in stack)
        if len(prefix) + len(remaining) <= limit:
            chunks.append(prefix + remaining)
            break
        chunk, advance, stack = _next_chunk(remaining, stack, limit)
        if chunk:
            chunks.append(chunk)
        remaining = remaining[advance:]
    return chunks


def _closers(stack: list[tuple[str, str]]) -> str:
    return "".join(f"</{name}>" for name, _ in reversed(stack))


def _carried(stack: list[tuple[str, str]], limit: int) -> list[tuple[str, str]]:
    """The open tags to reopen in the next chunk, or none when they would crowd it out.

    The reopening tags and their closers together may take at most half the
    chunk. Past that, give up on carrying the formatting over: the stray
    closers that follow make Telegram refuse the chunk, and it goes out as
    plain text instead.
    """
    if sum(len(tag) for _, tag in stack) + len(_closers(stack)) > limit // 2:
        return []
    return stack


def _next_chunk(
    remaining: str, stack: list[tuple[str, str]], limit: int
) -> tuple[str | None, int, list[tuple[str, str]]]:
    """Cut one chunk off ``remaining``: ``(chunk, chars of remaining used, tag stack after it)``.

    ``chunk`` is ``None`` when the piece holds only tags, which Telegram refuses
    as an empty message. They are not lost: the ones still open are reopened by
    the next chunk.
    """
    prefix = "".join(tag for _, tag in stack)
    budget = limit - len(prefix)

    def outside_markup(i: int) -> bool:  # a space inside `<a href=…>` is not a word boundary
        return _safe_html_cut(remaining, i) == i

    while True:
        cut, consumed = _find_cut(remaining[:budget], ok=outside_markup)
        if not consumed:
            # Hard cut. 0 means the chunk starts with a tag or entity longer
            # than the budget: cut it anyway. Telegram refuses that chunk and
            # the plain-text fallback sends it, where an over-long chunk
            # would be refused for good.
            cut = _safe_html_cut(remaining, cut) or cut
        piece = remaining[:cut]
        after = _open_tags_after(piece, stack)
        closers = _closers(after)
        overflow = len(prefix) + len(piece) + len(closers) - limit
        if overflow <= 0:
            break
        # Ends: at budget 1 the piece is one character and opens no tag, so
        # the chunk is at most prefix + 1 + the stack's closers, which
        # _carried keeps within half the limit.
        budget = max(budget - overflow, 1)
    chunk = prefix + piece + closers if _html_to_plain(piece).strip() else None
    return chunk, cut + consumed, after


# The only named entities Telegram knows; numeric ones are all accepted.
_NAMED_ENTITIES = {"lt": "<", "gt": ">", "amp": "&", "quot": '"'}


def _unescape_telegram(text: str) -> str:
    """Decode the entities Telegram decodes, and leave everything else as typed.

    Not :func:`html.unescape`: HTML5 also decodes legacy entities without a
    ``;``, so ``?id=1&copy=2`` in a URL would become ``?id=1©=2``.
    """

    def entity(m: re.Match[str]) -> str:
        ref = m.group(0)[1:-1]
        if not ref.startswith("#"):
            return _NAMED_ENTITIES.get(ref, m.group(0))
        try:
            return chr(int(ref[2:], 16) if ref[1] in "xX" else int(ref[1:]))
        except (ValueError, OverflowError):
            return m.group(0)

    return _ENTITY_RE.sub(entity, text)


def _html_to_plain(text: str) -> str:
    """Telegram-HTML to readable plain text: links as ``label (url)``, tags dropped, entities unescaped."""

    def link(m: re.Match[str]) -> str:
        url = _link_url(m)
        label = _TAG_RE.sub("", m["label"])
        return label if _unescape_telegram(label).strip() == url else f"{label} ({url})"

    return _unescape_telegram(_TAG_RE.sub("", _LINK_RE.sub(link, text)))


def _link_url(m: re.Match[str]) -> str:
    """The unescaped href of a :data:`_LINK_RE` match."""
    return _unescape_telegram(m["qurl"] if m["qurl"] is not None else m["url"])


# Any <a> opening tag (a quoted attribute value may hold a `>`) or closing tag.
_A_TAG_RE = re.compile(r"""<a(?=[\s>])(?:"[^"]*"|'[^']*'|[^"'>])*>|</a\s*>""", re.IGNORECASE)
# The one <a> form the sanitizer keeps: a single href and nothing else.
_PLAIN_A_RE = re.compile(r"""<a\s+href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'<>]+))\s*>""", re.IGNORECASE)


def _drop_unsafe_links(body: str) -> tuple[str, int]:
    """Keep only http(s) and mailto links, each rebuilt as ``<a href="…">``: ``(body, links dropped)``.

    A body can carry text Scout did not write (Slack, GitHub, mail), and
    Telegram keeps ``tg://`` deep links live (join a chat, add a proxy). The
    scheme rule is the action-items dashboard's
    (:func:`~scout.action_items.render.safe_href`).

    Fails closed: every ``<a>`` tag is read on its own, and one that is not
    exactly ``<a href=…>`` (another attribute, a second href, a ``data-href``)
    is dropped with its ``</a>``, as is a link nested in another. The label
    always stays. A rebuilt tag carries only the href that was checked, so
    Telegram cannot read a different one.
    """
    dropped = 0
    open_links: list[bool] = []  # per open <a>: kept?

    def tag(m: re.Match[str]) -> str:
        nonlocal dropped
        if m.group(0).startswith("</"):
            return "</a>" if open_links and open_links.pop() else ""
        plain = _PLAIN_A_RE.fullmatch(m.group(0))
        url = _unescape_telegram(next(g for g in plain.groups() if g is not None)) if plain else ""
        href = safe_href(url) if plain and not open_links else None
        open_links.append(href is not None)
        if href is None:
            dropped += 1
            return ""
        return f'<a href="{href}">'

    return _A_TAG_RE.sub(tag, body), dropped


def _prepare(body: str, parse_mode: str) -> tuple[list[str], int]:
    """The chunks to send, and how many unsafe links were dropped from an HTML body."""
    if parse_mode != "html":
        return _split_message(body), 0
    body, dropped = _drop_unsafe_links(body)
    return _split_html(body), dropped


def _is_parse_error(resp: requests.Response) -> bool:
    """True when Telegram refused the message because of its formatting entities."""
    if resp.status_code != 400:
        return False
    try:
        description = str(resp.json().get("description", ""))
    except (ValueError, AttributeError):
        return False
    return "can't parse entities" in description.lower()


# ----- send ----------------------------------------------------------------


def _build_payload(*, chat_id: str, text: str, tier: str, parse_mode: str = "none") -> dict[str, Any]:
    """Build the JSON body for a single sendMessage call."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_notification": tier == "info",
    }
    if parse_mode == "html":
        payload["parse_mode"] = "HTML"
    return payload


# ----- error scrubbing -------------------------------------------------------
#
# The request URL embeds the bot token (``/bot<token>/sendMessage``) and
# ``requests`` copies it into its exceptions: ConnectionError's str() has the
# path, HTTPError's the full URL, and both carry the request (and HTTPError the
# response) whose ``.url`` holds it. Anything send() raises passes through
# ``_scrubbed`` so no caller — CLI, trigger log, health push — can print it.


def _leaks(exc: BaseException, token: str | None) -> bool:
    seen: set[int] = set()
    todo: list[BaseException] = [exc]
    while todo:
        e = todo.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, requests.RequestException):
            return True  # carries the URL on its request/response whatever its message says
        texts = [str(e), *(repr(a) for a in e.args)]
        if any(redact_secrets(t, *([token] if token else [])) != t for t in texts):
            return True
        todo += [c for c in (e.__cause__, e.__context__) if c is not None]
    return False


def _clean_response(resp: Any, token: str | None) -> requests.Response | None:
    """A bare Response with the status of ``resp`` and a redacted URL, no request."""
    if resp is None:
        return None
    clean = requests.Response()
    clean.status_code = getattr(resp, "status_code", None)  # type: ignore[assignment]
    clean.reason = getattr(resp, "reason", None)  # type: ignore[assignment]
    clean.url = redact_secrets(str(getattr(resp, "url", "") or ""), *([token] if token else []))
    return clean


def _clean_exception(exc: Exception, token: str | None) -> Exception:
    """The same kind of error, with the token gone from its message, args and URLs."""
    message = redact_secrets(str(exc), *([token] if token else []))
    if isinstance(exc, requests.RequestException):
        try:
            return type(exc)(message, response=_clean_response(exc.response, token))
        except Exception:  # noqa: BLE001 - an odd subclass signature: keep the family
            return requests.RequestException(message, response=_clean_response(exc.response, token))
    try:
        return type(exc)(message)
    except Exception:  # noqa: BLE001
        return RuntimeError(f"{type(exc).__name__}: {message}")


def _scrubbed(fn: Callable[..., Event]) -> Callable[..., Event]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Event:
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            try:
                token: str | None = _read_secret("telegram-bot-token")
            except Exception:  # noqa: BLE001 - redact by pattern alone
                token = None
            if not _leaks(e, token):
                raise
            clean = _clean_exception(e, token)
        # Raised outside the except block, so the original (with the URL) is
        # neither the __cause__ nor the __context__ of the clean one.
        raise clean

    return wrapper


@_scrubbed
def send(
    tier: str,
    body: str,
    *,
    dry_run: bool = False,
    parse_mode: str = "none",
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
    caller bypasses the CLI. ``parse_mode`` is ``none`` (plain text) or
    ``html`` (see the module docstring for the split and fallback rules).

    ``receipt``, when given, is called with the Event id as soon as it is known
    (before any dry-run output; after the last chunk of a real send), so the CLI
    can put ``id=<id>`` first on stderr.

    Raises:
        ValueError: empty body, unknown tier or unknown parse mode.
        ConfigError: missing secret files (exit code 10).
        DuplicateSendError: a byte-identical body went to this chat inside
            ``DUPLICATE_WINDOW`` and ``allow_duplicate`` is false (exit code 3).
        requests.RequestException: HTTP-layer failure during a real send.
            Bubbles up so the caller knows the send did not complete. Like
            any error from here, it is scrubbed of the bot token first.
    """
    if not body:
        raise ValueError("body cannot be empty")
    if tier not in VALID_TIERS:
        raise ValueError(f"unknown tier {tier!r}; valid tiers are: {', '.join(VALID_TIERS)}")
    if parse_mode not in VALID_PARSE_MODES:
        raise ValueError(f"unknown parse mode {parse_mode!r}; valid modes are: {', '.join(VALID_PARSE_MODES)}")

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

    chunks, dropped = _prepare(body, parse_mode)
    if dropped:
        print(f"warning: dropped {dropped} link(s) whose href is not http(s) or mailto", file=sys.stderr)
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
                **({"parse_mode": "html"} if parse_mode == "html" else {}),
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
            payload = _build_payload(chat_id=chat_id, text=chunk, tier=tier, parse_mode=parse_mode)
            print(f"[dry-run] POST {redacted_url}", file=sys.stderr)
            print(
                f"[dry-run] body: {json.dumps(payload, ensure_ascii=False)}",
                file=sys.stderr,
            )
        return dry_event

    parse_fallback = False
    for n, chunk in enumerate(chunks, 1):
        payload = _build_payload(chat_id=chat_id, text=chunk, tier=tier, parse_mode=parse_mode)
        resp = requests.post(url, json=payload, timeout=timeout)
        if parse_mode == "html" and _is_parse_error(resp):
            # Telegram's description names the offending tag and offset, never
            # the URL, so it is safe to print.
            print(
                f"warning: Telegram refused the HTML of chunk {n}/{len(chunks)} "
                f"({resp.json().get('description')}); resent it as plain text",
                file=sys.stderr,
            )
            parse_fallback = True
            payload = _build_payload(chat_id=chat_id, text=_html_to_plain(chunk), tier=tier)
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
            **({"parse_mode": "html"} if parse_mode == "html" else {}),
            **({"parse_fallback": True} if parse_fallback else {}),
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
    parser.add_argument("--parse-mode", default="none", choices=list(VALID_PARSE_MODES))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        ev = send(tier=args.tier, body=args.body, dry_run=args.dry_run, parse_mode=args.parse_mode)
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
        # Non-HTTP failures (timeout, DNS, connection refused). send() already
        # scrubbed the URL from these; redact again in case a caller didn't.
        print(f"notify-telegram: HTTP error: {redact_secrets(str(e))}")
        return 2

    print(json.dumps(asdict(ev), indent=2))
    return 0
