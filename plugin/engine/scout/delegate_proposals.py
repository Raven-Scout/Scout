"""The proposals an agent puts in its inbox (#550 V4), and the strict check every one passes.

A proposal is one JSON file, ``inbox/<delegate>/<ULID>.json``, in the
delegate's inbox repository. Everything in it comes from outside the laptop,
so it is data, never an instruction, and :func:`parse` accepts only the exact
shape below; anything else is refused with a reason code, never repaired.

``schema_version`` 1, fixed fields:

- ``id``: the ULID in the file name; ``delegate``: the inbox's delegate id;
  ``made_at``: ``YYYY-MM-DDTHH:MM:SSZ``; ``kind``.
- ``kind: observation``: ``text`` (1–4000 characters), optional ``about`` (a
  name or slug, at most 200) and ``source`` (a URL or a note, at most 500).
- ``kind: action_item_op``: ``op``, ``tag`` (the item's ``[#TAG]``), ``date``
  (the daily file's ``YYYY-MM-DD``) and ``args``, whose keys are fixed per op:

  ========================  =============================================
  ``mark-done``             ``undo`` (bool, optional)
  ``snooze``                ``until`` (``YYYY-MM-DD``)
  ``add-comment``           ``comment`` (one line, 1–1000 characters)
  ``set-estimate``          ``minutes`` (1–1440), ``kind`` (optional word)
  ``set-block``             ``day`` (``YYYY-MM-DD``), ``start``/``end`` (``HH:MM``)
  ``clear-block``           nothing
  ========================  =============================================

  The engine's own command checks what the shape can't (an estimate on the
  owner's grid, a block window); its refusal becomes the proposal's outcome.

Text that can reach the vault can't forge a line there, hide one, or speak
for the owner. No text field holds a control, format, surrogate, private-use,
unassigned or invisible character (``text`` alone may keep a newline and a
tab). None holds markup: ``[[`` or ``[#`` (a proposal can neither link a note
nor name a task), ``<!--``, ``-->``, ``![``, ``](``, ``%%`` (an Obsidian
comment, or a ``%%privacy:allow%%`` marker), ``[from-agent`` (Scout's own
mark, any case), or a task checkbox at a line's start. And none holds the owner-directive marker ``//==<<`` or
``>>==//``, even with whitespace inside it. No key may repeat, and no float
may be NaN or infinite. Every pattern is anchored with ``\\Z``, so a trailing
newline never passes. ``made_at`` is the agent's claim, shown and never used
for order. The ULID's own time may not be ahead of the laptop's clock
(``parse(now=...)``). Any other failure is ``bad_entry``: parse never raises
anything but :class:`ProposalError`.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from scout.ids import new_ulid

SCHEMA_VERSION = 1
MAX_BYTES = 16 * 1024  # one proposal file
MAX_PER_PULL = 50  # proposals taken from one inbox in one pull, in ULID order
KINDS = ("observation", "action_item_op")
OPS: dict[str, dict[str, bool]] = {  # op -> {arg: required}
    "mark-done": {"undo": False},
    "snooze": {"until": True},
    "add-comment": {"comment": True},
    "set-estimate": {"minutes": True, "kind": False},
    "set-block": {"day": True, "start": True, "end": True},
    "clear-block": {},
}
# Every pattern ends at \Z, never $: $ also matches before a final newline, so "<ULID>.json\n" would alias the
# real file. A ULID's first character is 0-7: 26 Crockford characters hold 130 bits, a ULID 128.
ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}\Z")  # Crockford base32, as ids.new_ulid makes them
PATH_RE = re.compile(r"^inbox/([a-z0-9][a-z0-9-]{0,39})/([0-7][0-9A-HJKMNP-TV-Z]{25})\.json\Z")
TAG_RE = re.compile(r"^(?=[A-Z0-9]{2,8}\Z)[A-Z0-9]*[A-Z][A-Z0-9]*\Z")  # ids._TAG_BODY, anchored
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d\Z")
_MADE_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_KIND_WORD_RE = re.compile(r"^[a-z][a-z0-9-]{0,23}\Z")
# Characters that can hide or forge text: every control (Cc; ``text`` alone keeps \n and \t), format (Cf: bidi,
# zero-width, soft hyphen, the tag characters U+E0000-E007F), surrogate (Cs), private-use (Co) and unassigned (Cn)
# character, the line and paragraph separators (Zl, Zp), and invisible marks that aren't in those categories.
_BAD_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
_INVISIBLE = frozenset(
    [chr(c) for c in range(0xFE00, 0xFE10)]  # variation selectors
    + [chr(c) for c in range(0xE0100, 0xE01F0)]  # variation selectors supplement
    + ["\u034f", "\u115f", "\u1160", "\u3164", "\uffa0", "\u180e", "\u00ad"]
)
# Markup that means something to Scout or to a renderer: a wikilink, a task tag, an HTML comment, an image or a
# link. A task checkbox at a line's start, and the owner-directive marker (//==<< … >>==//, matched with all
# whitespace removed, as the phases sweep for it), are refused too.
# %% opens an Obsidian comment (hidden text) and a %%privacy:allow%% marker; [from-agent is Scout's own mark.
_MARKUP_RE = re.compile(r"\[\[|\[#|<!--|-->|!\[|\]\(|%%|\[from-agent", re.IGNORECASE)
_CHECKBOX_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s*\[[ xX]\]", re.MULTILINE)
_DIRECTIVES = ("//==<<", ">>==//")
FUTURE_SLACK = dt.timedelta(minutes=10)  # a ULID's time may run this far ahead of the laptop's clock
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class ProposalError(ValueError):
    """A proposal that isn't the exact shape. ``code`` names why, for the record; never the content."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class Proposal:
    id: str
    delegate: str
    made_at: str
    kind: str
    body: dict[str, Any]  # the kind's own fields, checked

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "delegate": self.delegate,
            "made_at": self.made_at,
            "kind": self.kind,
            **self.body,
        }

    def to_bytes(self) -> bytes:
        return (json.dumps(self.to_json(), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

    @property
    def path(self) -> str:
        return f"inbox/{self.delegate}/{self.id}.json"


def _no_repeats(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ProposalError("duplicate_key", key)
        seen[key] = value
    return seen


def _no_nan(token: str) -> float:
    raise ProposalError("not_a_number", token)


def _exact_keys(obj: dict[str, Any], required: set[str], optional: set[str], where: str) -> None:
    keys = set(obj)
    if not required <= keys:
        raise ProposalError("missing_field", f"{where}: {', '.join(sorted(required - keys))}")
    if not keys <= required | optional:
        raise ProposalError("unknown_field", f"{where}: {', '.join(sorted(keys - required - optional))}")


def _text(value: Any, field: str, limit: int, *, one_line: bool, minimum: int = 1) -> str:
    if not isinstance(value, str):
        raise ProposalError("bad_field", f"{field} isn't a string")
    if not minimum <= len(value) <= limit or not value.strip():
        raise ProposalError("bad_field", f"{field} must be {minimum}-{limit} characters, not blank")
    if _hidden(value, keep="" if one_line else "\n\t"):
        raise ProposalError("bad_field", f"{field} holds a control, format, invisible or unassigned character")
    if _MARKUP_RE.search(value) or _CHECKBOX_RE.search(value):
        raise ProposalError(
            "bad_field", f"{field} holds markup: [[, [#, <!--, -->, ![, ](, %%, [from-agent or a task checkbox"
        )
    squeezed = re.sub(r"\s+", "", value)
    if any(d in squeezed for d in _DIRECTIVES):
        raise ProposalError("bad_field", f"{field} holds the owner-directive marker")
    return value


def _hidden(value: str, *, keep: str) -> bool:
    """Whether ``value`` holds a character that can hide or forge text (``keep`` excepted)."""
    return any(ch not in keep and (unicodedata.category(ch) in _BAD_CATEGORIES or ch in _INVISIBLE) for ch in value)


def ulid_time(ulid: str) -> dt.datetime:
    """The time a canonical ULID was made (its first 10 characters, milliseconds since the epoch)."""
    ms = 0
    for ch in ulid[:10]:
        ms = ms * 32 + _CROCKFORD.index(ch)
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC)


def _date(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ProposalError("bad_field", f"{field} isn't YYYY-MM-DD")
    try:
        dt.date.fromisoformat(value)
    except ValueError as e:
        raise ProposalError("bad_field", f"{field} isn't a real date") from e
    return value


def _args(op: str, args: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise ProposalError("bad_field", "args isn't an object")
    spec = OPS[op]
    _exact_keys(args, {k for k, req in spec.items() if req}, {k for k, req in spec.items() if not req}, "args")
    out: dict[str, Any] = {}
    if "undo" in args:
        if not isinstance(args["undo"], bool):
            raise ProposalError("bad_field", "undo isn't true or false")
        out["undo"] = args["undo"]
    if "until" in args:
        out["until"] = _date(args["until"], "until")
    if "comment" in args:
        out["comment"] = _text(args["comment"], "comment", 1000, one_line=True)
    if "minutes" in args:
        minutes = args["minutes"]
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 1440:
            raise ProposalError("bad_field", "minutes must be a whole number from 1 to 1440")
        out["minutes"] = minutes
    if "kind" in args:
        if not isinstance(args["kind"], str) or not _KIND_WORD_RE.match(args["kind"]):
            raise ProposalError("bad_field", "kind must be a short lowercase word")
        out["kind"] = args["kind"]
    if "day" in args:
        out["day"] = _date(args["day"], "day")
    for key in ("start", "end"):
        if key in args:
            if not isinstance(args[key], str) or not _TIME_RE.match(args[key]):
                raise ProposalError("bad_field", f"{key} isn't HH:MM")
            out[key] = args[key]
    return out


def parse(path: str, data: bytes, delegate: str, *, now: dt.datetime | None = None) -> Proposal:
    """The proposal at inbox ``path`` holding ``data``, from ``delegate``'s inbox, or :class:`ProposalError`.

    With ``now``, a ULID made more than :data:`FUTURE_SLACK` after it is refused (``future_id``). Any other
    failure (deep nesting, an odd type) is refused as ``bad_entry``: nothing an agent writes can raise past here.
    """
    try:
        return _parse(path, data, delegate, now=now)
    except ProposalError:
        raise
    except Exception as e:  # noqa: BLE001 - the input is hostile; every failure is a refusal
        raise ProposalError("bad_entry", type(e).__name__) from None


def _parse(path: str, data: bytes, delegate: str, *, now: dt.datetime | None) -> Proposal:
    match = PATH_RE.match(path)
    if match is None:
        raise ProposalError("bad_path")
    if match[1] != delegate:
        raise ProposalError("wrong_delegate", "the path names another delegate")
    if len(data) > MAX_BYTES:
        raise ProposalError("too_big", f"{len(data)} bytes")
    try:
        text = data.decode("utf-8")
        doc = json.loads(text, object_pairs_hook=_no_repeats, parse_constant=_no_nan)
    except ProposalError:
        raise
    except (UnicodeDecodeError, ValueError) as e:
        raise ProposalError("not_json") from e
    if not isinstance(doc, dict):
        raise ProposalError("not_an_object")
    if doc.get("schema_version") != SCHEMA_VERSION or isinstance(doc.get("schema_version"), bool):
        raise ProposalError("bad_schema_version")
    kind = doc.get("kind")
    if kind not in KINDS:
        raise ProposalError("bad_kind")
    common = {"schema_version", "id", "delegate", "made_at", "kind"}
    if kind == "observation":
        _exact_keys(doc, common | {"text"}, {"about", "source"}, "proposal")
    else:
        _exact_keys(doc, common | {"op", "tag", "date", "args"}, set(), "proposal")
    if doc["id"] != match[2] or not isinstance(doc["id"], str) or not ULID_RE.match(doc["id"]):
        raise ProposalError("bad_id", "id isn't the ULID in the file name")
    if now is not None and ulid_time(doc["id"]) > now + FUTURE_SLACK:
        raise ProposalError("future_id", "the ULID's time is ahead of this computer's clock")
    if doc["delegate"] != delegate:
        raise ProposalError("wrong_delegate", "the proposal names another delegate")
    if not isinstance(doc["made_at"], str) or not _MADE_AT_RE.match(doc["made_at"]):
        raise ProposalError("bad_field", "made_at isn't YYYY-MM-DDTHH:MM:SSZ")
    try:
        dt.datetime.strptime(doc["made_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as e:
        raise ProposalError("bad_field", "made_at isn't a real time") from e
    body: dict[str, Any]
    if kind == "observation":
        body = {"text": _text(doc["text"], "text", 4000, one_line=False)}
        if "about" in doc:
            body["about"] = _text(doc["about"], "about", 200, one_line=True)
        if "source" in doc:
            body["source"] = _text(doc["source"], "source", 500, one_line=True)
    else:
        op = doc["op"]
        if not isinstance(op, str) or op not in OPS:
            raise ProposalError("bad_op")
        if not isinstance(doc["tag"], str) or not TAG_RE.match(doc["tag"]):
            raise ProposalError("bad_field", "tag isn't an item's [#TAG]")
        body = {"op": op, "tag": doc["tag"], "date": _date(doc["date"], "date"), "args": _args(op, doc["args"])}
    return Proposal(id=doc["id"], delegate=delegate, made_at=doc["made_at"], kind=kind, body=body)


def make(delegate: str, kind: str, body: dict[str, Any], *, now: dt.datetime | None = None) -> Proposal:
    """A new proposal from the agent's side, checked exactly as the laptop will check it."""
    when = (now or dt.datetime.now(dt.UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    draft = Proposal(id=new_ulid(), delegate=delegate, made_at=when, kind=kind, body=body)
    return parse(draft.path, draft.to_bytes(), delegate)
