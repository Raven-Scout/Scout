"""What this computer's Scout has read of other people's shared notes (Agnes Shared Areas §7, slice S2a).

A person's Scout reads a note someone shared with them through Agnes's
``shared_with_me``, ``shared_knowledge_search`` and ``shared_knowledge_read``.
That content is private to the reader (ruling 10). It never enters their
knowledge base by itself, so the private-note matcher can't know it. This module
is what lets ``kb check-outbound`` recognise it:

- **The ledger**, ``.scout-cache/shared-reads/``: one file per answer, written by
  the ``shared-read-record`` PostToolUse hook. It holds the owner, the note's
  path and title, and a hash per word of the text. A hashed word list is not a
  secret (a dictionary reverses it). It keeps a readable copy of someone else's
  note out of a cache file, and out of anything that greps one. Like the rest of
  ``.scout-cache/``, it never leaves this computer. Nothing expires it: what a
  reader read stays with them (ruling 3), and so does the refusal.
- **Provenance notes**: a private vault note with ``source: shared:<owner>:<path>``
  (the citing rule, spec §7) gives the same path and text. So the refusal
  survives a cleared cache for whatever the run filed in the vault.

**What matches** (:class:`ExcerptIndex`). Text is compared as words: case,
accents, punctuation and Markdown don't count, and plurals and verb endings are
cut. A *quote* is :data:`QUOTE_WORDS` words in a row of the note, at least
:data:`QUOTE_MIN_CONTENT` of them content words, so a stock phrase isn't one. A
*close rewording* is a sentence holding at least :data:`REWORD_SHARE` of the
content words of one of the note's sentences (:data:`REWORD_MIN_CONTENT` at
least), in any order. Terms for the path and title are built in
:func:`scout.kb.outbound.with_shared`.

**What it can't see.** A paraphrase in other words, a single fact, a quote under
six words, a translation, and anything read where the hook doesn't run (the
``agnes`` CLI in a shell, another computer, a cleared cache with nothing filed in
the vault). The phase rule covers those, and only it does.

``source:`` is lint and matching only: the classifier never reads it, so labels
and the conformance corpus Agnes vendors don't change.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from scout.kb.privacy import classifier_block, load_frontmatter_yaml, read_head

LEDGER_DIR = ".scout-cache/shared-reads"
SHARED_SOURCE_PREFIX = "shared:"

QUOTE_WORDS = 6
QUOTE_MIN_CONTENT = 3
REWORD_MIN_CONTENT = 5
REWORD_SHARE = 0.7

_TOKEN = re.compile(r"[^\W_]+")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n\s*\n|\n\s*(?:[-*+]|\d+\.|#+)\s+")
_FENCE = re.compile(r"^<<<(?:END_)?UNTRUSTED_ARTIFACT_CONTENT [0-9a-f]+>>>[ \t]*$", re.MULTILINE)
_SHARED_SOURCE = re.compile(r"^shared:([^\s:@]+@[^\s:@]+):(\S.*)$", re.IGNORECASE)
_SUFFIXES = ("ing", "ed", "es", "s")
_STOP = frozenset(
    "about after again also because been before being between both could does doing down each from further have "
    "having here into just like made make many more most much must need only other over same should since some "
    "such than that their them then there these they this those through under until very want were what when "
    "where which while will with would your yours".split()
)


class SharedReadsUnreadable(Exception):
    """A ledger file can't be read, so the check can't say what it covers (fail closed)."""


@dataclass(frozen=True)
class SharedRecord:
    """One answer's worth of one shared note or folder. ``sentences`` holds :func:`_word` hashes."""

    owner: str
    path: str
    title: str | None
    sentences: tuple[tuple[int, ...], ...]
    owner_name: str | None = None  # for the label only; never written

    @property
    def key(self) -> str:
        return f"shared:{self.owner}:{self.path}"


# ----- words --------------------------------------------------------------------


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            cut = word[: -len(suffix)]
            if suffix in ("ing", "ed") and cut[-1] == cut[-2] and cut[-1] not in "lsz":
                cut = cut[:-1]
            return cut
    return word


@lru_cache(maxsize=65536)
def _word(raw: str) -> int:
    """A word's hash: positive for a content word, negative for any other."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", raw.casefold()) if not unicodedata.combining(c))
    stem = _stem(folded)
    value = int.from_bytes(hashlib.blake2b(stem.encode("utf-8"), digest_size=4).digest(), "big") + 1
    content = folded not in _STOP and (len(stem) >= 4 or any(c.isdigit() for c in stem))
    return value if content else -value


def sentences_of(text: str) -> tuple[tuple[int, ...], ...]:
    """``text`` as sentences of word hashes."""
    out = []
    for sentence in _SENTENCE_BREAK.split(unicodedata.normalize("NFC", text)):
        words = tuple(_word(m.group(0)) for m in _TOKEN.finditer(sentence))
        if words:
            out.append(words)
    return tuple(out)


@dataclass(frozen=True)
class _Indexed:
    key: str
    flat: tuple[int, ...]
    words: frozenset[int]
    bags: tuple[frozenset[int], ...]  # sentences with enough content words to be reworded


class ExcerptIndex:
    """Finds the quotes and close rewordings of shared notes in a text.

    It scans from the text, which is short, over a ledger that only grows: a
    note whose words miss the text's is skipped in one set operation. So a hook
    with a timeout stays quick on a long-lived vault (``tests/perf``).
    """

    def __init__(self, records: Iterable[tuple[str, Sequence[Sequence[int]]]]) -> None:
        self._notes: list[_Indexed] = []
        for key, sentences in records:
            flat = tuple(word for sentence in sentences for word in sentence)
            bags = (frozenset(w for w in sentence if w > 0) for sentence in sentences)
            kept = tuple(bag for bag in bags if len(bag) >= REWORD_MIN_CONTENT)
            self._notes.append(_Indexed(key, flat, frozenset(flat), kept))

    def hits(self, text: str) -> list[tuple[str, int, int]]:
        """``(record key, first line, last line)``, 0-based, of every quote and close rewording in ``text``."""
        if not self._notes:
            return []
        text = unicodedata.normalize("NFC", text)
        starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]
        breaks = [m.end() for m in _SENTENCE_BREAK.finditer(text)]
        tokens = [
            (_word(m.group(0)), bisect.bisect_right(starts, m.start()) - 1, bisect.bisect_right(breaks, m.start()))
            for m in _TOKEN.finditer(text)
        ]
        quotes: dict[tuple[int, ...], tuple[int, int]] = {}
        for i in range(len(tokens) - QUOTE_WORDS + 1):
            window = tuple(t[0] for t in tokens[i : i + QUOTE_WORDS])
            if sum(w > 0 for w in window) >= QUOTE_MIN_CONTENT:
                quotes.setdefault(window, (tokens[i][1], tokens[i + QUOTE_WORDS - 1][1]))
        first_words = {window[0] for window in quotes}
        sentences: dict[int, list[tuple[int, int]]] = {}
        for word, line, sentence in tokens:
            if word > 0:
                sentences.setdefault(sentence, []).append((word, line))
        spans = [(frozenset(w for w, _ in members), members[0][1], members[-1][1]) for members in sentences.values()]
        found: list[tuple[str, int, int]] = []
        for note in self._notes:
            if first_words & note.words:
                for i in range(len(note.flat) - QUOTE_WORDS + 1):
                    if note.flat[i] in first_words:
                        span = quotes.get(note.flat[i : i + QUOTE_WORDS])
                        if span is not None:
                            found.append((note.key, *span))
            for span_words, first, last in spans:
                if len(span_words & note.words) < REWORD_MIN_CONTENT:
                    continue
                if any(
                    len(span_words & bag) >= max(REWORD_MIN_CONTENT, math.ceil(REWORD_SHARE * len(bag)))
                    for bag in note.bags
                ):
                    found.append((note.key, first, last))
        return found


# ----- source: shared:<owner-email>:<path> ---------------------------------------


def is_shared_source(value: str) -> bool:
    """Whether a frontmatter ``source:`` value claims a shared note. Other values (slack, dreaming ...) never do."""
    return value.strip().casefold().startswith(SHARED_SOURCE_PREFIX)


def parse_shared_source(value: str) -> tuple[str, str] | None:
    """``(owner email, path)`` of a well-formed ``shared:<owner-email>:<path>``, else None."""
    match = _SHARED_SOURCE.match(value.strip())
    if match is None:
        return None
    owner, path = match.group(1), match.group(2).strip()
    if path.startswith("/") or ".." in path.split("/"):
        return None
    return owner, path


def source_values(text: str | None) -> list[str]:
    """The frontmatter ``source:`` values of a note's (leading) text; [] when it has none or they don't parse."""
    block = classifier_block(text) if text else None
    if block is None:
        return []
    try:
        data = load_frontmatter_yaml("\n".join(block))
    except (yaml.YAMLError, ValueError, TypeError):
        return []
    value = data.get("source") if isinstance(data, dict) else None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def shared_sources(text: str | None) -> list[str]:
    """The ``source:`` values of a note's text that claim a shared note."""
    return [v for v in source_values(text) if is_shared_source(v)]


# ----- reading a tool's answer ---------------------------------------------------


def _unfence(text: str) -> str:
    return _FENCE.sub("", text).strip()


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for inner in value.values():
            yield from _strings(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _strings(inner)


def _answers(response: Any) -> Iterator[Any]:
    """The JSON answers in a tool response: a list of text blocks, a result object, or the answer itself."""
    if isinstance(response, str):
        try:
            parsed = json.loads(response)
        except ValueError:
            yield response
        else:
            yield from _answers(parsed)
    elif isinstance(response, list):
        for item in response:
            yield from _answers(item)
    elif isinstance(response, Mapping):
        if response.get("isError") is True:
            return
        if isinstance(response.get("structuredContent"), Mapping):
            yield response["structuredContent"]
        elif isinstance(response.get("content"), list):
            yield from _answers(response["content"])
        elif response.get("type") == "text" and isinstance(response.get("text"), str):
            yield from _answers(response["text"])
        else:
            yield response


def _text_of(value: Any) -> str:
    return "\n\n".join(_unfence(s) for s in _strings(value))


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _records_from(answer: Any, tool_input: Mapping[str, Any]) -> Iterator[SharedRecord]:
    input_owner = _str(tool_input.get("owner")) or "unknown"
    input_path = _str(tool_input.get("path")) or "unknown"
    if isinstance(answer, str):
        # Plain text: a short one is an error code, a long one is content in a shape nobody expected.
        if len(_TOKEN.findall(answer)) >= QUOTE_WORDS:
            yield SharedRecord(input_owner, input_path, None, sentences_of(_unfence(answer)))
        return
    if not isinstance(answer, Mapping) or "error" in answer or "detail" in answer:
        return
    if isinstance(answer.get("results"), list):
        for hit in answer["results"]:
            if isinstance(hit, Mapping) and _str(hit.get("path")):
                yield _note_record(hit, _text_of([hit.get("section"), hit.get("snippet")]), input_owner)
    elif isinstance(answer.get("owners"), list):
        for owner in answer["owners"]:
            for folder in owner.get("folders", []) if isinstance(owner, Mapping) else []:
                if isinstance(folder, Mapping) and _str(folder.get("folder")):
                    name = _str(owner.get("owner_name"))
                    email = _str(owner.get("owner_email")) or input_owner
                    yield SharedRecord(email, folder["folder"], None, (), name)
    elif _str(answer.get("path")) and ("text" in answer or "frontmatter" in answer):
        yield _note_record(answer, _text_of([answer.get("text"), answer.get("frontmatter")]), input_owner)
    else:
        yield SharedRecord(input_owner, input_path, None, sentences_of(_text_of(answer)))


def _note_record(item: Mapping[str, Any], text: str, input_owner: str) -> SharedRecord:
    title = _str(item.get("title"))
    return SharedRecord(
        owner=_str(item.get("owner_email")) or input_owner,
        path=item["path"],
        title=_unfence(title) if title else None,
        sentences=sentences_of(text),
        owner_name=_str(item.get("owner_name")),
    )


def records_from_response(tool_input: Any, response: Any) -> list[SharedRecord]:
    """The notes and folders one tool answer shows."""
    given = tool_input if isinstance(tool_input, Mapping) else {}
    return [record for answer in _answers(response) for record in _records_from(answer, given)]


# ----- the ledger ----------------------------------------------------------------


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _write(vault: Path, record: SharedRecord) -> None:
    """Atomically, under a name made of the note and the content: an answer seen twice is one file."""
    body = json.dumps(
        {"v": 1, "owner": record.owner, "path": record.path, "title": record.title, "sentences": record.sentences},
        ensure_ascii=False,
    )
    directory = vault / LEDGER_DIR
    target = directory / f"{_digest(record.key)}-{_digest(body)}.json"
    if target.exists():
        return
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def record_tool_call(vault: Path, tool_input: Any, response: Any) -> list[SharedRecord]:
    """Write what one shared-notes tool answer showed into the ledger, and return it."""
    records = records_from_response(tool_input, response)
    for record in records:
        _write(vault, record)
    return records


def load_ledger(vault: Path) -> list[SharedRecord]:
    """Every recorded answer. Raises :class:`SharedReadsUnreadable` on a file it can't read: no silent gap."""
    directory = vault / LEDGER_DIR
    try:
        files = sorted(p for p in directory.iterdir() if p.suffix == ".json")
    except FileNotFoundError:
        return []
    except OSError as e:
        raise SharedReadsUnreadable(f"{LEDGER_DIR}: {e.strerror or type(e).__name__}") from e
    out: list[SharedRecord] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            title = data["title"]
            out.append(
                SharedRecord(
                    owner=str(data["owner"]),
                    path=str(data["path"]),
                    title=title if isinstance(title, str) else None,
                    sentences=tuple(tuple(int(w) for w in sentence) for sentence in data["sentences"]),
                )
            )
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise SharedReadsUnreadable(
                f"{LEDGER_DIR}/{path.name} can't be read ({type(e).__name__}): delete that file"
            ) from e
    return out


def source_note_records(vault: Path, rels: Iterable[str]) -> list[SharedRecord]:
    """What the private notes among ``rels`` that carry ``source: shared:…`` say about the note they cite."""
    out: list[SharedRecord] = []
    for rel in rels:
        if not rel.lower().endswith(".md"):
            continue
        path = vault / rel
        parsed = [p for p in map(parse_shared_source, shared_sources(read_head(path)[0])) if p]
        if not parsed:
            continue
        try:
            sentences = sentences_of(path.read_bytes().decode("utf-8", errors="replace"))
        except OSError:
            sentences = ()
        out += [SharedRecord(owner, cited, None, sentences) for owner, cited in parsed]
    return out
