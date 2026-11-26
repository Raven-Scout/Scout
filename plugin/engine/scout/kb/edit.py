"""``scoutctl kb edit``: the sanctioned way for a run to change a KB note by rule (follow-up to #824).

Scheduled runs edited notes with python heredocs: read a note, change it (``str.replace``, ``re.sub``, a line put
under a heading, a frontmatter date), write it back. The read gate can't tell such a program from one that sends
the note's text somewhere (a file name, an exit code or an exception can carry it), so #824 records each as
``kb_edit`` behind ``DENY_INTERPRETER_KB_EDITS``. This command does those edits itself:

- ``replace``: a literal string, ``regex``: a Python regular expression (``re.sub`` template), each with the
  number of matches expected (``count``, default 1, or ``"all"`` for one or more). Any other number fails.
- ``insert``: text at the end (``at: "end"``, the default) or the start of the section under a heading. The
  heading must match exactly one line outside the frontmatter and code fences; ``create: true`` adds it at the
  end of the file when none does.
- ``append``: text at the end of the file.
- ``frontmatter``: set (``value``) or ``remove`` one top-level field. The edited block must parse, hold the new
  value, and keep every other field as it was.
- ``patch``: a one-file unified diff. Each hunk applies where it says, else at the one place its old lines are.

The operations of one call apply in order, to the text the one before left. All of them apply or nothing is
written (exit 1), and the file must not change while they run. A note's privacy label may only narrow (a note
made private); anything else that would change it is refused (exit 3), as is a reader other than ``owner`` or
``autonomous``, a path outside ``knowledge-base/`` and ``action-items/context/``, and a file the write gate
protects (:func:`scout.kb.hook_paths.protected_write`). Every other rule is the commit's: the pre-commit
privacy check (``kb lint --staged``, #733's names scan of each added line of a shared note) judges this edit
as it judges one made with the Edit tool.

The output is counts and status only, never the note's text, a pattern or a heading.
"""

from __future__ import annotations

import datetime
import difflib
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from scout import safe_write
from scout.kb.frontmatter import FENCE, frontmatter_block
from scout.kb.privacy import (
    FULL_READERS,
    Label,
    classify,
    decode_head,
    load_frontmatter_yaml,
    load_policy,
    scope_widens,
)
from scout.kb.raw_read import is_kb_note_path
from scout.kb.read import Denied, Output, UsageError, read_file
from scout.plural import count

OPS = ("replace", "regex", "insert", "append", "frontmatter", "patch")
MAX_OPS = 100
MAX_OPS_BYTES = 1 << 20
_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {  # op -> (required, optional) keys
    "replace": (frozenset({"old", "new"}), frozenset({"count"})),
    "regex": (frozenset({"pattern", "new"}), frozenset({"count", "flags"})),
    "insert": (frozenset({"heading", "text"}), frozenset({"at", "create"})),
    "append": (frozenset({"text"}), frozenset()),
    "frontmatter": (frozenset({"field"}), frozenset({"value", "remove"})),
    "patch": (frozenset({"diff"}), frozenset()),
}
_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}
_HEADING = re.compile(r"#{1,6}[ \t]+\S")
_FENCE_OPEN = re.compile(r" {0,3}(`{3,}|~{3,})")
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class OpFailed(Exception):
    """An operation that can't apply as asked (a count that differs, no such heading, a hunk that doesn't fit)."""

    def __init__(self, index: int, op: str, why: str) -> None:
        super().__init__(f"op {index} ({op}): {why}")
        self.index = index
        self.op = op
        self.why = why


@dataclass
class OpResult:
    index: int
    op: str
    status: str  # what it did, in words: "1 match", "heading created", "field set", "2 hunks"
    matches: int | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"index": self.index, "op": self.op, "status": self.status}
        if self.matches is not None:
            out["matches"] = self.matches
        return out


@dataclass
class Edit:
    """The result of applying every op to a text, before anything is written."""

    text: str
    results: list[OpResult] = field(default_factory=list)


# ----- parsing the ops -------------------------------------------------------------------


def load_ops(raw: str) -> list[dict[str, Any]]:
    """The ops of a ``--ops`` document: a JSON list of op objects, or one op object. ``UsageError`` otherwise."""
    if len(raw.encode("utf-8", errors="replace")) > MAX_OPS_BYTES:
        raise UsageError(f"--ops is over {MAX_OPS_BYTES} bytes")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise UsageError(f"--ops is not JSON (line {e.lineno}, column {e.colno})") from None
    return check_ops([data] if isinstance(data, dict) else data)


def check_ops(data: Any) -> list[dict[str, Any]]:
    """Each op's keys and value types, checked before the note is touched."""
    if not isinstance(data, list) or not data:
        raise UsageError("--ops must be a JSON list of operations (or one operation)")
    if len(data) > MAX_OPS:
        raise UsageError(f"at most {MAX_OPS} operations per call")
    for i, op in enumerate(data, 1):
        if not isinstance(op, dict) or op.get("op") not in OPS:
            raise UsageError(f'op {i}: each operation is an object whose "op" is one of {", ".join(OPS)}')
        name = op["op"]
        required, optional = _KEYS[name]
        keys = set(op) - {"op"}
        if missing := sorted(required - keys):
            raise UsageError(f"op {i} ({name}): missing {', '.join(missing)}")
        if extra := sorted(keys - required - optional):
            raise UsageError(f"op {i} ({name}): unknown key {', '.join(extra)}")
        _check_types(i, name, op)
    return data


def _check_types(i: int, name: str, op: Mapping[str, Any]) -> None:
    def bad(why: str) -> UsageError:
        return UsageError(f"op {i} ({name}): {why}")

    for key in ("old", "new", "pattern", "text", "diff", "heading", "field"):
        if key in op and not isinstance(op[key], str):
            raise bad(f"{key} must be a string")
    if "count" in op:
        c = op["count"]
        if c != "all" and (isinstance(c, bool) or not isinstance(c, int) or c < 1):
            raise bad('count must be a whole number of 1 or more, or "all"')
    if name == "replace" and not op["old"]:
        raise bad("old must not be empty")
    if name == "regex":
        flags = op.get("flags", "")
        if not isinstance(flags, str) or any(f not in _FLAGS for f in flags):
            raise bad(f"flags takes letters from {''.join(_FLAGS)}")
        try:
            re.compile(op["pattern"], _regex_flags(flags))
        except re.error as e:
            raise bad(f"pattern doesn't compile ({e.msg})") from None
    if name == "insert":
        if not _HEADING.match(op["heading"].strip()):
            raise bad('heading must be a markdown heading line, e.g. "## Status"')
        if op.get("at", "end") not in ("end", "start"):
            raise bad('at is "end" or "start"')
        if not isinstance(op.get("create", False), bool):
            raise bad("create must be true or false")
    if name in ("insert", "append") and not op["text"].strip():
        raise bad("text must not be empty")
    if name == "frontmatter":
        if not _FIELD.fullmatch(op["field"]):
            raise bad("field must be a plain top-level key")
        remove = op.get("remove", False)
        if not isinstance(remove, bool):
            raise bad("remove must be true or false")
        if remove == ("value" in op):
            raise bad("give value, or remove: true")
        if "value" in op and not _plain_value(op["value"]):
            raise bad("value must be a string, number, true/false, null or a list of those")
    if name == "patch" and not op["diff"].strip():
        raise bad("diff must not be empty")


def _plain_value(value: Any) -> bool:
    scalar = (str, int, float, bool, type(None))
    return isinstance(value, scalar) or (isinstance(value, list) and all(isinstance(v, scalar) for v in value))


def _regex_flags(flags: str) -> int:
    out = 0
    for f in flags:
        out |= _FLAGS[f]
    return out


# ----- applying them ---------------------------------------------------------------------


def apply_ops(text: str, ops: Sequence[Mapping[str, Any]]) -> Edit:
    """Every op in order on ``text``. Raises :class:`OpFailed` on the first that can't apply."""
    edit = Edit(text=text)
    for i, op in enumerate(ops, 1):
        name = str(op["op"])
        new_text, status, matches = _APPLY[name](i, edit.text, op)
        edit.text = new_text
        edit.results.append(OpResult(i, name, status, matches))
    return edit


def _expect(i: int, name: str, op: Mapping[str, Any], found: int) -> None:
    want = op.get("count", 1)
    if want == "all":
        if found == 0:
            raise OpFailed(i, name, "expected 1 or more matches, found 0")
    elif found != want:
        raise OpFailed(i, name, f"expected {count(want, 'match', 'matches')}, found {found}")


def _replace(i: int, text: str, op: Mapping[str, Any]) -> tuple[str, str, int | None]:
    found = text.count(op["old"])
    _expect(i, "replace", op, found)
    return text.replace(op["old"], op["new"]), count(found, "match", "matches"), found


def _regex(i: int, text: str, op: Mapping[str, Any]) -> tuple[str, str, int | None]:
    rx = re.compile(op["pattern"], _regex_flags(op.get("flags", "")))
    found = sum(1 for _ in rx.finditer(text))
    _expect(i, "regex", op, found)
    try:
        new = rx.sub(op["new"], text)
    except (re.error, IndexError) as e:
        raise OpFailed(i, "regex", f"the replacement template doesn't fit the pattern ({e})") from None
    return new, count(found, "match", "matches"), found


def _body_start(lines: Sequence[str]) -> int:
    """The index of the first line after the frontmatter (0 when there is none)."""
    bare = [ln.rstrip("\r\n") for ln in lines]
    block = frontmatter_block(bare)
    return 0 if block is None else len(block) + 2


def _headings(lines: Sequence[str]) -> list[tuple[int, int]]:
    """``(line index, level)`` of every heading outside the frontmatter and fenced code."""
    out: list[tuple[int, int]] = []
    fence: str | None = None
    for idx in range(_body_start(lines), len(lines)):
        line = lines[idx].rstrip("\r\n")
        m = _FENCE_OPEN.match(line)
        if fence is not None:
            if m and m[1][0] == fence[0] and len(m[1]) >= len(fence) and not line[m.end() :].strip():
                fence = None
            continue
        if m:
            fence = m[1]
            continue
        if _HEADING.match(line):
            out.append((idx, len(line) - len(line.lstrip("#"))))
    return out


def _with_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _insert(i: int, text: str, op: Mapping[str, Any]) -> tuple[str, str, int | None]:
    lines = text.splitlines(keepends=True)
    want = op["heading"].strip()
    heads = _headings(lines)
    hits = [(idx, level) for idx, level in heads if lines[idx].strip() == want]
    body = _with_newline(op["text"])
    if not hits:
        if not op.get("create", False):
            raise OpFailed(i, "insert", "no heading matches (create: true adds it)")
        head = text if not text or text.endswith("\n") else text + "\n"
        gap = "" if not head or head.endswith("\n\n") else "\n"
        return f"{head}{gap}{want}\n\n{body}", "heading created", 0
    if len(hits) > 1:
        raise OpFailed(i, "insert", f"{len(hits)} headings match")
    idx, level = hits[0]
    end = next((h for h, lv in heads if h > idx and lv <= level), len(lines))
    if op.get("at", "end") == "start":
        at = idx + 1
        while at < end and not lines[at].strip():
            at += 1
    else:
        at = end
        while at > idx + 1 and not lines[at - 1].strip():
            at -= 1
    if at > 0 and not lines[at - 1].endswith("\n"):
        lines[at - 1] += "\n"
    lines[at:at] = body.splitlines(keepends=True)
    return "".join(lines), "under 1 heading", 1


def _append(i: int, text: str, op: Mapping[str, Any]) -> tuple[str, str, int | None]:
    head = text if not text or text.endswith("\n") else text + "\n"
    return head + _with_newline(op["text"]), "appended", None


def _render_field(name: str, value: Any) -> list[str]:
    if isinstance(value, str) and _ISO_DATE.fullmatch(value) and _is_date(value):
        return [f"{name}: {value}\n"]  # the vault's way of writing a date, unquoted
    dumped = yaml.safe_dump({name: value}, default_flow_style=False, allow_unicode=True, sort_keys=False, width=1 << 20)
    return dumped.splitlines(keepends=True)


def _is_date(value: str) -> bool:
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _same_value(got: Any, want: Any) -> bool:
    if isinstance(got, datetime.date) and isinstance(want, str):
        return got.isoformat() == want
    if isinstance(got, bool) or isinstance(want, bool):
        return got is want
    return bool(got == want)


def _frontmatter(i: int, text: str, op: Mapping[str, Any]) -> tuple[str, str, int | None]:
    lines = text.splitlines(keepends=True)
    bare = [ln.rstrip("\r\n") for ln in lines]
    block = frontmatter_block(bare)
    if block is None:
        raise OpFailed(i, "frontmatter", "the note has no frontmatter block")
    before = _parse_block(i, block)
    name = op["field"]
    key = re.compile(rf"{re.escape(name)}[ \t]*:(?:[ \t]|$)")
    close = len(block) + 1  # the closing fence's line index
    rows = [n for n in range(1, close) if key.match(bare[n])]
    if len(rows) > 1:
        raise OpFailed(i, "frontmatter", f"the field is set on {len(rows)} lines")
    remove = op.get("remove", False)
    if rows:
        start = rows[0]
        stop = start + 1
        while stop < close and bare[stop][:1] in (" ", "\t", "-") and bare[stop].rstrip() != FENCE:
            stop += 1
    elif remove:
        return text, "field absent", 0
    else:
        start = stop = close
    new_rows = [] if remove else _render_field(name, op["value"])
    status = "field removed" if remove else ("field set" if rows else "field added")
    lines[start:stop] = new_rows
    new_text = "".join(lines)
    new_block = frontmatter_block([ln.rstrip("\r\n") for ln in new_text.splitlines(keepends=True)])
    after = _parse_block(i, new_block or [])
    if remove:
        ok = name not in after
    else:
        ok = name in after and _same_value(after[name], op["value"])
    rest_before = {k: v for k, v in before.items() if k != name}
    rest_after = {k: v for k, v in after.items() if k != name}
    if new_block is None or not ok or rest_before != rest_after:
        raise OpFailed(i, "frontmatter", "the edited block wouldn't read back as asked; edit this field another way")
    return new_text, status, len(rows)


def _parse_block(i: int, block: Sequence[str]) -> dict[str, Any]:
    try:
        data = load_frontmatter_yaml("\n".join(block)) if block else {}
    except (yaml.YAMLError, ValueError, TypeError):  # a date like 2026-13-45 fails in its constructor
        raise OpFailed(i, "frontmatter", "the frontmatter doesn't parse") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise OpFailed(i, "frontmatter", "the frontmatter isn't a mapping")
    return data


@dataclass
class _Hunk:
    old_start: int
    old: list[str]
    new: list[str]
    new_ends_bare: bool  # "\ No newline at end of file" after the last added line


def _parse_diff(i: int, diff: str) -> list[_Hunk]:
    hunks: list[_Hunk] = []
    plus_headers = 0
    rows = diff.splitlines()
    n = 0
    while n < len(rows):
        row = rows[n]
        if row.startswith("+++ "):
            plus_headers += 1
            if plus_headers > 1:
                raise OpFailed(i, "patch", "the diff changes more than one file")
            n += 1
            continue
        m = _HUNK.match(row)
        if not m:
            if hunks and row.strip():
                raise OpFailed(i, "patch", f"line {n + 1} of the diff isn't part of a hunk")
            n += 1
            continue
        old_n = 1 if m[2] is None else int(m[2])
        new_n = 1 if m[4] is None else int(m[4])
        hunk = _Hunk(old_start=int(m[1]), old=[], new=[], new_ends_bare=False)
        n += 1
        last = ""
        while n < len(rows) and (len(hunk.old) < old_n or len(hunk.new) < new_n or rows[n].startswith("\\")):
            row = rows[n]
            tag, body = (row[:1], row[1:]) if row else (" ", "")
            if tag == "\\":
                if last == "+":
                    hunk.new_ends_bare = True
            elif tag == " ":
                hunk.old.append(body)
                hunk.new.append(body)
            elif tag == "-":
                hunk.old.append(body)
            elif tag == "+":
                hunk.new.append(body)
            else:
                raise OpFailed(i, "patch", f"line {n + 1} of the diff isn't a hunk line")
            last = tag
            n += 1
        if len(hunk.old) != old_n or len(hunk.new) != new_n:
            raise OpFailed(i, "patch", f"hunk {len(hunks) + 1}'s line counts don't match its header")
        hunks.append(hunk)
    if not hunks:
        raise OpFailed(i, "patch", "the diff has no hunks")
    return hunks


def _matches_at(lines: Sequence[str], at: int, old: Sequence[str]) -> bool:
    if at < 0 or at + len(old) > len(lines):
        return False
    return all(lines[at + k].rstrip("\r\n") == old[k].rstrip("\r") for k in range(len(old)))


def _patch(i: int, text: str, op: Mapping[str, Any]) -> tuple[str, str, int | None]:
    hunks = _parse_diff(i, op["diff"])
    lines = text.splitlines(keepends=True)
    offset = 0
    floor = 0  # a hunk applies after the one before it
    for h, hunk in enumerate(hunks, 1):
        stated = (hunk.old_start - 1 if hunk.old else hunk.old_start) + offset
        if not hunk.old:
            if not floor <= stated <= len(lines):
                raise OpFailed(i, "patch", f"hunk {h} adds lines past the end of the note")
            at = stated
        elif stated >= floor and _matches_at(lines, stated, hunk.old):
            at = stated
        else:
            places = [p for p in range(floor, len(lines) - len(hunk.old) + 1) if _matches_at(lines, p, hunk.old)]
            if len(places) != 1:
                where = "anywhere" if not places else f"in {len(places)} places"
                raise OpFailed(i, "patch", f"hunk {h}'s old lines aren't at its line and match {where}")
            at = places[0]
        if at > 0 and not lines[at - 1].endswith("\n"):
            lines[at - 1] += "\n"
        old_tail_bare = bool(hunk.old) and not lines[at + len(hunk.old) - 1].endswith("\n")
        new_rows = [row + "\n" for row in hunk.new]
        if new_rows and (hunk.new_ends_bare or (old_tail_bare and at + len(hunk.old) == len(lines))):
            new_rows[-1] = new_rows[-1][:-1]
        lines[at : at + len(hunk.old)] = new_rows
        offset += at - stated + len(hunk.new) - len(hunk.old)
        floor = at + len(new_rows)
    return "".join(lines), count(len(hunks), "hunk"), len(hunks)


_APPLY: dict[str, Callable[[int, str, Mapping[str, Any]], tuple[str, str, int | None]]] = {
    "replace": _replace,
    "regex": _regex,
    "insert": _insert,
    "append": _append,
    "frontmatter": _frontmatter,
    "patch": _patch,
}


def line_counts(old: str, new: str) -> tuple[int, int]:
    """Lines added and removed between two texts."""
    added = removed = 0
    for row in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if row.startswith("+") and not row.startswith("+++"):
            added += 1
        elif row.startswith("-") and not row.startswith("---"):
            removed += 1
    return added, removed


# ----- the command -----------------------------------------------------------------------


def label_change(before: Label, after: Label) -> bool:
    """Whether an edit changes the note's label in a way a run may not: anything but a note made private."""
    hidden = ("shared_source", "delegate_source", "delegate_scope", "delegate_author")  # not in Label's equality
    if any(getattr(before, k) != getattr(after, k) for k in hidden):
        return True  # who the note's text came from, and which delegates see it: the owner's to change
    if replace(before, warnings=()) == replace(after, warnings=()):
        return False
    return (
        before.federation_scope_declared != after.federation_scope_declared
        or scope_widens(before.federation_scope, after.federation_scope)
        or (before.local_only and not after.local_only)
        or not (before.visibility == "normal" and after.visibility == "private")
    )


def _denied(path: str, why: str) -> Output:
    data = {"path": path, "written": False, "denied": True, "reason": why}
    return Output(data=data, lines=[f"{path}: nothing written: {why}"], denied=1, code=3)


def cmd_edit(
    vault: Path,
    reader: str,
    path: str,
    ops: Sequence[Mapping[str, Any]],
    *,
    check: bool = False,
    env: Mapping[str, str] | None = None,
) -> Output:
    """Apply ``ops`` to one KB note and write it (``check``: only report). See the module docstring."""
    from scout.kb.hook_paths import protected_write

    env = os.environ if env is None else env
    if reader not in FULL_READERS:
        return _denied(path, f"{reader} doesn't edit notes")
    try:
        label, data = read_file(vault, reader, path)
    except Denied:  # pragma: no cover - owner and autonomous see every file; kept so a change there fails closed
        return _denied(path, f"not visible to {reader}")
    rel = label.path
    if not is_kb_note_path(rel) or not rel.endswith(".md"):
        raise UsageError(f"kb edit changes notes under knowledge-base/ or action-items/context/: {rel}")
    target = vault.resolve() / rel
    form = protected_write(target, vault.resolve(), env, file_tool=True)
    if form is not None:
        return _denied(rel, f"the write gate protects it ({form})")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise UsageError(f"not UTF-8 text: {rel}") from None
    base: dict[str, Any] = {"path": rel, "label": label.to_json()}
    try:
        edit = apply_ops(text, ops)
    except OpFailed as e:
        data_out = base | {"written": False, "failed": {"index": e.index, "op": e.op, "reason": e.why}}
        return Output(data=data_out, lines=[f"{rel}: nothing written: {e}"], labels=[label.to_json()], code=1)
    new_bytes = edit.text.encode("utf-8")
    policy = load_policy(vault.resolve())
    after = classify(rel, decode_head(new_bytes), policy)
    if label_change(label, after):
        refused = _denied(rel, "the edit would change the note's privacy label; only a note made private is allowed")
        refused.labels = [label.to_json()]
        return refused
    added, removed = line_counts(text, edit.text)
    changed = edit.text != text
    written = False
    if changed and not check:
        if target.read_bytes() != data:
            out = base | {"written": False, "failed": {"reason": "the note changed while it was being edited"}}
            return Output(
                data=out,
                lines=[f"{rel}: nothing written: the note changed meanwhile; run it again"],
                labels=[label.to_json()],
                code=1,
            )
        safe_write.atomic_write_bytes(target, new_bytes)
        written = True
    state = "written" if written else ("would write" if changed else "unchanged")
    head = f"{rel}: {count(len(edit.results), 'op')} applied, +{added} -{removed} lines, {state}"
    lines = [head, *(f"  {r.index} {r.op}: {r.status}" for r in edit.results)]
    out_data = base | {
        "written": written,
        "changed": changed,
        "check": check,
        "added": added,
        "removed": removed,
        "ops": [r.to_json() for r in edit.results],
    }
    return Output(data=out_data, lines=lines, labels=[label.to_json()])
