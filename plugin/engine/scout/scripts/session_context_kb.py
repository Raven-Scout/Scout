"""The KB parts of ``session-context.json`` a run otherwise spends dozens of reads on.

A usage audit of scheduled runs found three gaps the pre-session step can close:

- **Project knowledge.** A front page edit rarely came after a read of that
  project's ``knowledge/`` notes. :func:`project_knowledge` lists, for each
  active project, the front page's **Go deeper** map and every note under its
  ``knowledge/`` folder with the note's ``as_of``, so the run knows what to read
  before it rewrites "Where it stands".
- **People.** The leave-state and leadership-ping rules need a person's leave
  state and manager, and runs rarely looked them up. :func:`people_context`
  precomputes both from the same fields the rules name: a person's ``status:``,
  a dated ``oof:``/``leave:`` frontmatter block, a ``### 🚨 ACTIVE STATUS`` body
  header, ``role``/``team``, and the ontology's ``manages``/``managed_by``/
  ``reports_to`` relations.
- **Research queue.** Each research run listed ``knowledge-base/research-queue/``
  and read every entry just to filter by ``status:``. :func:`research_queue`
  reads the frontmatter once and keeps a compact row per open entry.

Every row carries the path's ``visibility`` from the same labels ``scoutctl kb``
uses; a path the reader may not see is left out. :func:`fit_budget` keeps each
of these sections, and the file as a whole, within :data:`SESSION_CONTEXT_MAX_BYTES`.
Nothing here raises: a part that can't be built is empty.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# ----- size budget ---------------------------------------------------------------

# The written file (indent=2). Before these sections a mature vault's file was ~150 KB,
# nearly all of it `kb_file_dates`. Only the sections below are ever trimmed to fit.
SESSION_CONTEXT_MAX_BYTES = 320_000
PROJECT_KNOWLEDGE_MAX_BYTES = 64_000
PEOPLE_CONTEXT_MAX_BYTES = 24_000
RESEARCH_QUEUE_MAX_BYTES = 16_000

GO_DEEPER_MAX_ENTRIES = 25
GO_DEEPER_ENTRY_CHARS = 200
KNOWLEDGE_MAX_NOTES = 40
RELEVANT_PEOPLE_MAX = 40
RESEARCH_TITLE_CHARS = 140
HEAD_LINES = 60  # a note's frontmatter and its top-of-file status block live here

# A project whose front page `status:` (or priority) says it is over or on hold.
_INACTIVE = re.compile(
    r"⚪|\b(done|completed?|archived?|paused|on hold|dropped|cancell?ed|closed|inactive|shelved|retired|abandoned)\b",
    re.IGNORECASE,
)
_LEAVE_WORDS = re.compile(
    r"\b(leave|oof|ooo|out of office|vacation|holiday|paternity|maternity|parental|sabbatical)\b", re.IGNORECASE
)
_BACK = re.compile(r"^\s*(back|returned|active)\b", re.IGNORECASE)
_RETURNED = re.compile(r"^\s*(back|returned)\b", re.IGNORECASE)
_DEPARTED = re.compile(r"\b(departed|left the company|former|offboarded)\b", re.IGNORECASE)
_ENDED = re.compile(r"\b(ended|closed|cancell?ed|over|done)\b", re.IGNORECASE)
_ACTIVE_STATUS_HEADER = re.compile(r"^#{1,4}\s.*\bACTIVE STATUS\b", re.IGNORECASE)
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
_MANAGER_RELS = ("managed_by", "reports_to")
_REPORT_RELS = ("manages",)
_QUEUE_OPEN = ("open", "in-progress", "in_progress", "")
_PRIORITY_RANK = {"urgent": 0, "high": 1, "medium": 2, "low": 3}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(_text(value)[:10]) if value else None
    except ValueError:
        return None


def _head(path: Path, lines: int = HEAD_LINES) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return "".join(line for _, line in zip(range(lines), f, strict=False))
    except OSError:
        return ""


def _frontmatter(text: str) -> dict[str, Any]:
    from scout.kb.structure import split_page

    try:
        return split_page(text)[0]
    except Exception:
        return {}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _visibility(rel: str, labels: Mapping[str, str], full: bool) -> str | None:
    """The path's label; None when a delegate may not see it. A full reader's unlabelled path is private."""
    if rel in labels:
        return "normal" if labels[rel] == "normal" else "private"
    return "private" if full else None


# ----- project knowledge ---------------------------------------------------------


def is_active_project(frontmatter: Mapping[str, Any]) -> bool:
    """True unless the front page's ``status:`` (or ``priority:``) says it is done, paused or archived."""
    return not _INACTIVE.search(f"{_text(frontmatter.get('status'))} {_text(frontmatter.get('priority'))}")


def project_knowledge(
    vault: Path, labels: Mapping[str, str], *, full: bool, projects_dir: str | None = None
) -> list[dict[str, Any]]:
    """One row per active project folder ``<projects_dir>/<name>/<name>.md``.

    ``{"project", "path", "visibility", "as_of", "go_deeper": [entry, …], "knowledge":
    [{"path", "as_of", "visibility"}, …], "knowledge_total"}``. ``go_deeper`` is the
    front page's **Go deeper** section, one entry per bullet, clipped; ``knowledge``
    is every ``.md`` under the folder's ``knowledge/``, newest ``as_of`` first, at most
    :data:`KNOWLEDGE_MAX_NOTES` (``knowledge_total`` counts them all).
    """
    from scout.kb.structure import load_structure_config, page_as_of, section_entries, split_page

    try:
        cfg = load_structure_config(vault)
        root = projects_dir or cfg.projects_dir
        skip = cfg.skip_projects
    except Exception:
        root, skip = projects_dir or "knowledge-base/projects", frozenset()
    base = vault / root
    if not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        name = folder.name
        front = folder / f"{name}.md"
        rel = f"{root}/{name}/{name}.md"
        if name in skip or not front.is_file():
            continue
        visibility = _visibility(rel, labels, full)
        if visibility is None:
            continue
        try:
            text = front.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, _ = split_page(text)
        if not is_active_project(fm):
            continue
        go_deeper = [_clip(entry, GO_DEEPER_ENTRY_CHARS) for _, _, entry in section_entries(text, ["Go deeper"])]
        notes: list[dict[str, Any]] = []
        knowledge = folder / "knowledge"
        if knowledge.is_dir():
            for note in sorted(knowledge.rglob("*.md")):
                note_rel = note.relative_to(vault).as_posix()
                note_vis = _visibility(note_rel, labels, full)
                if note_vis is None:
                    continue
                as_of = page_as_of(_frontmatter(_head(note)))
                notes.append({"path": note_rel, "as_of": as_of.isoformat() if as_of else None, "visibility": note_vis})
        notes.sort(key=lambda n: (n["as_of"] or "", n["path"]), reverse=True)
        page_as = page_as_of(fm)
        out.append(
            {
                "project": name,
                "path": rel,
                "visibility": visibility,
                "as_of": page_as.isoformat() if page_as else None,
                "go_deeper": go_deeper[:GO_DEEPER_MAX_ENTRIES],
                "go_deeper_total": len(go_deeper),
                "knowledge": notes[:KNOWLEDGE_MAX_NOTES],
                "knowledge_total": len(notes),
            }
        )
    return out


# ----- people --------------------------------------------------------------------


def _leave_block(fm: Mapping[str, Any], day: dt.date) -> dict[str, Any] | None:
    """A dated ``oof:``/``leave:`` frontmatter mapping that covers ``day`` and hasn't ended."""
    for key in ("oof", "leave", "ooo"):
        block = fm.get(key)
        if not isinstance(block, Mapping):
            continue
        if _ENDED.search(_text(block.get("status"))):
            continue
        start = _date(block.get("from") or block.get("start") or block.get("from_approx"))
        end = _date(block.get("until") or block.get("end") or block.get("to"))
        if start is None and end is None:
            continue
        if (start is None or start <= day) and (end is None or day <= end):
            return {
                "since": start.isoformat() if start else None,
                "until": end.isoformat() if end else None,
                "source": key,
            }
    return None


def leave_state(fm: Mapping[str, Any], head: str, day: dt.date) -> dict[str, Any] | None:
    """The person's current leave state, by the Leave-State Compose Gate's own signals, or None.

    ``{"state": "leave" | "departed" | "active_status_block", "since", "until", "source"}``.
    ``active_status_block`` means the note opens with a ``### 🚨 ACTIVE STATUS``
    header (unless ``status:`` says they are back): read it before naming them as an owner.
    """
    block = _leave_block(fm, day)
    if block:
        return {"state": "leave", **block}
    status = _text(fm.get("status"))
    if status and _LEAVE_WORDS.search(status) and not _BACK.match(status):
        return {"state": "leave", "since": None, "until": None, "source": "status"}
    if status and _DEPARTED.search(status):
        return {"state": "departed", "since": None, "until": None, "source": "status"}
    from scout.kb.structure import split_page

    _, body = split_page(head)
    if not _RETURNED.match(status) and any(_ACTIVE_STATUS_HEADER.match(line) for line in body.splitlines()):
        return {"state": "active_status_block", "since": None, "until": None, "source": "body"}
    return None


def _names_index(people: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """Lower-cased name, alias and filename stem → the person's entity name. A key two people share is dropped."""
    index: dict[str, str] = {}
    clashes: set[str] = set()
    for name, entity in people.items():
        keys = {name, Path(_text((entity.get("_label") or {}).get("path"))).stem}
        aliases = entity.get("aliases")
        if isinstance(aliases, list):
            keys.update(_text(a) for a in aliases)
        for key in {k.lower() for k in keys if k}:
            if index.get(key, name) != name:
                clashes.add(key)
            index.setdefault(key, name)
    return {k: v for k, v in index.items() if k not in clashes}


def mentioned_people(text: str, people: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """The people ``text`` names: a ``[[wikilink]]`` to their note (name, alias or slug) or their full name."""
    index = _names_index(people)
    found: dict[str, None] = {}
    for m in _WIKILINK.finditer(text):
        for part in (m.group(2), m.group(1)):
            key = _text(part).split("/")[-1].removesuffix(".md").lower()
            if key in index:
                found.setdefault(index[key])
                break
    for name in people:
        if " " in name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text):
            found.setdefault(name)
    return list(found)


def _relations(graph: Any, name: str, kinds: tuple[str, ...]) -> list[str]:
    try:
        return sorted({r["target"] for r in graph.related(name) if r.get("type") in kinds})
    except Exception:
        return []


def _owner(people: Mapping[str, Mapping[str, Any]], owner_keys: list[str]) -> str | None:
    keys = {k.strip().lower() for k in owner_keys if k and k.strip()}
    for name, entity in people.items():
        if name.lower() in keys or _text(entity.get("email")).lower() in keys:
            return name
    return None


def people_context(
    graph: Any | None,
    day: dt.date,
    *,
    items_text: str = "",
    owner_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Leave states and manager relations, so the leave and leadership rules need no lookups.

    - ``on_leave``: every person (not under ``archive/``) whose note shows a current
      leave state (:func:`leave_state`), with ``name``, ``path``, ``visibility``.
    - ``relevant``: each person today's items name (``items_text``, the daily file),
      with ``role``, ``team``, ``managers``, ``reports`` and ``leave``.
    - ``owner_managers``: who manages the vault owner (``managed_by``/``reports_to``,
      and the inverse ``manages``): a project-update comment from one is a leadership ping.
    """
    empty: dict[str, Any] = {"on_leave": [], "relevant": [], "relevant_total": 0, "owner_managers": []}
    if graph is None:
        return empty
    try:
        people = {
            n: e
            for n, e in graph.entities.items()
            if e.get("type") == "person" and "/archive/" not in _text((e.get("_label") or {}).get("path"))
        }
    except Exception:
        return empty

    def row(name: str) -> dict[str, Any]:
        label = people[name].get("_label") or {}
        return {
            "name": name,
            "path": _text(label.get("path")),
            "visibility": "normal" if label.get("visibility") == "normal" else "private",
        }

    leave: dict[str, dict[str, Any]] = {}
    for name, entity in people.items():
        source = entity.get("_source_path")
        head = _head(Path(source)) if isinstance(source, str) and source else ""
        try:
            state = leave_state(entity, head, day)
        except Exception:
            state = None
        if state:
            leave[name] = state
    out: dict[str, Any] = {
        "on_leave": [{**row(n), **leave[n]} for n in sorted(leave)],
        "relevant": [],
        "relevant_total": 0,
        "owner_managers": [],
    }
    named = mentioned_people(items_text, people) if items_text else []
    out["relevant_total"] = len(named)
    for name in sorted(named)[:RELEVANT_PEOPLE_MAX]:
        entity = people[name]
        out["relevant"].append(
            {
                **row(name),
                "role": _text(entity.get("role")) or None,
                "team": _text(entity.get("team")) or None,
                "managers": _relations(graph, name, _MANAGER_RELS),
                "reports": _relations(graph, name, _REPORT_RELS),
                "leave": leave.get(name, {}).get("state"),
            }
        )
    owner = _owner(people, owner_keys or [])
    if owner:
        managers = set(_relations(graph, owner, _MANAGER_RELS))
        managers.update(n for n in people if owner in _relations(graph, n, _REPORT_RELS))
        out["owner_managers"] = [row(n) for n in sorted(managers) if n in people]
    return out


def items_text_for(vault: Path, day: dt.date) -> str:
    """The newest daily action-items file on or before ``day``; "" when there is none."""
    from scout import paths

    today = paths.action_items_daily_path(vault, date=day)
    candidates = [today] if today.is_file() else []
    if not candidates:
        folder = today.parent
        dated = sorted(
            p
            for p in (folder.glob("action-items-*.md") if folder.is_dir() else [])
            if (d := _date(p.stem.removeprefix("action-items-"))) is not None and d <= day
        )
        candidates = dated[-1:]
    try:
        return candidates[0].read_text(encoding="utf-8", errors="replace") if candidates else ""
    except OSError:
        return ""


# ----- research queue ------------------------------------------------------------


def research_queue(vault: Path, labels: Mapping[str, str], *, full: bool) -> dict[str, Any]:
    """``{"open": [{"id", "path", "status", "priority", "title", "area", "date", "visibility"}], "counts": {…}}``.

    One frontmatter read per ``knowledge-base/research-queue/*.md``. ``open`` holds the work
    queue (``status`` open or in-progress, or none), ``urgent`` first, then by priority and
    newest ``date``; ``counts`` counts every entry by status.
    """
    folder = vault / "knowledge-base" / "research-queue"
    counts: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    if not folder.is_dir():
        return {"open": [], "open_total": 0, "counts": counts}
    for entry in sorted(folder.glob("*.md")):
        rel = entry.relative_to(vault).as_posix()
        visibility = _visibility(rel, labels, full)
        if visibility is None:
            continue
        fm = _frontmatter(_head(entry))
        status = _text(fm.get("status")).lower()
        counts[status or "none"] = counts.get(status or "none", 0) + 1
        if status not in _QUEUE_OPEN:
            continue
        when = _date(fm.get("date"))
        rows.append(
            {
                "id": entry.stem,
                "path": rel,
                "status": status or None,
                "priority": _text(fm.get("priority")).lower() or None,
                "title": _clip(_text(fm.get("title")) or entry.stem, RESEARCH_TITLE_CHARS),
                "area": _text(fm.get("area")) or None,
                "date": when.isoformat() if when else None,
                "visibility": visibility,
            }
        )
    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    rows.sort(key=lambda r: _PRIORITY_RANK.get(r["priority"] or "", 4))
    return {"open": rows, "open_total": len(rows), "counts": dict(sorted(counts.items()))}


# ----- budget --------------------------------------------------------------------


def _size(value: Any) -> int:
    return len(json.dumps(value, indent=2, default=str).encode("utf-8"))


def _trim_projects(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Halve every project's lists until the section fits; then drop the go-deeper text, then the oldest notes."""
    rows = [dict(r) for r in rows]
    for cap in (20, 12, 8, 4, 2, 0):
        if _size(rows) <= limit:
            break
        for r in rows:
            r["go_deeper"] = r["go_deeper"][:cap]
            r["knowledge"] = r["knowledge"][: max(cap, 3)]
            r["truncated"] = True
    while rows and _size(rows) > limit:
        for r in rows:
            r["knowledge"] = r["knowledge"][:1]
        if _size(rows) > limit:
            rows.pop()
    return rows


def _trim_people(ctx: dict[str, Any], limit: int) -> dict[str, Any]:
    ctx = {**ctx, "relevant": list(ctx.get("relevant") or []), "on_leave": list(ctx.get("on_leave") or [])}
    while ctx["relevant"] and _size(ctx) > limit:
        ctx["relevant"].pop()
        ctx["truncated"] = True
    while ctx["on_leave"] and _size(ctx) > limit:
        ctx["on_leave"].pop()
        ctx["truncated"] = True
    return ctx


def _trim_queue(queue: dict[str, Any], limit: int) -> dict[str, Any]:
    queue = {**queue, "open": list(queue.get("open") or [])}
    while queue["open"] and _size(queue) > limit:
        queue["open"].pop()  # sorted urgent first: the tail is the lowest priority
        queue["truncated"] = True
    return queue


_SECTIONS: tuple[tuple[str, int, Callable[[Any, int], Any]], ...] = (
    ("project_knowledge", PROJECT_KNOWLEDGE_MAX_BYTES, _trim_projects),
    ("people_context", PEOPLE_CONTEXT_MAX_BYTES, _trim_people),
    ("research_queue", RESEARCH_QUEUE_MAX_BYTES, _trim_queue),
)


def fit_budget(data: dict[str, Any], max_bytes: int = SESSION_CONTEXT_MAX_BYTES) -> dict[str, Any]:
    """Trim this module's sections to their own budgets, then shrink them further while the file is over ``max_bytes``.

    The older fields (the checks read them) are never touched, so a file whose older fields
    alone pass ``max_bytes`` keeps them whole and these sections at their floor.
    """
    out = dict(data)
    for key, limit, trim in _SECTIONS:
        if key in out and _size(out[key]) > limit:
            out[key] = trim(out[key], limit)
    for scale in (2, 4, 16):
        if _size(out) <= max_bytes:
            break
        for key, limit, trim in _SECTIONS:
            if key in out:
                out[key] = trim(out[key], limit // scale)
    return out


__all__ = [
    "PEOPLE_CONTEXT_MAX_BYTES",
    "PROJECT_KNOWLEDGE_MAX_BYTES",
    "RESEARCH_QUEUE_MAX_BYTES",
    "SESSION_CONTEXT_MAX_BYTES",
    "fit_budget",
    "is_active_project",
    "items_text_for",
    "leave_state",
    "mentioned_people",
    "people_context",
    "project_knowledge",
    "research_queue",
]
