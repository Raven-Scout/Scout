"""The single read gate: the labelled ``scoutctl kb`` read commands (#453, spec §6.1).

Every result carries its file's privacy label (:class:`~scout.kb.privacy.Label`);
the text form prefixes a private result with ``[private] ``. ``owner`` and
``autonomous`` see every file; any other reader (every ``agent:*``) sees nothing
in v1, and nothing under the vault is listed or opened for it.

- :func:`visible_files` lists the vault, labelled, through a stat-keyed index
  at ``.scout-cache/privacy-index.json`` (below). Machine plumbing is left out
  (ruling R13, below).
- :func:`search` runs ``rg`` over the visible paths, in batches of
  :data:`SEARCH_BATCH`, never over the tree; a Python scan when ``rg`` is absent.
- :func:`read_file`, :func:`resolve`, and the graph commands over the engine's
  labelled :class:`~scout.kb.ontology.KnowledgeGraph`. The parser-compat ones
  (``entity``, ``name-lookup``, ``export``, ``stats``, ``validate``, and
  ``query``/``related``/``traverse``/``path`` in text form) print exactly what the
  vault's pre-shim ``parser.py`` printed, plus labels: the vault's ``parser.py``
  is now a shim that execs them.

The ``cmd_*`` functions return an :class:`Output` (JSON value, text lines, the
labels returned for the audit log); ``scout/cli.py`` only parses arguments and
prints.

**The privacy index.** One entry per file: its stat key (mtime_ns, ctime_ns,
size, inode), its :class:`~scout.kb.privacy.NoteFacts` (what its own text says)
and its label. The header records a hash of the effective policy: when the
policy changes, labels are recomputed from the stored facts without reading any
file. It also records :data:`~scout.kb.privacy.CLASSIFIER_VERSION` and the
engine version: after an upgrade the whole index is rebuilt from the files, so
facts an older classifier stored can't keep labelling a file (fail closed). A
torn or foreign index, or a malformed entry, is ignored and rebuilt; an
unwritable one only costs speed. A file modified less than 2 s before the walk
isn't stored (a coarse-timestamp filesystem could keep its key across a
same-size rewrite), as in the connector-calls index (#80).

**The walk.** Directory symlinks are never followed (the ``knowledge_base``
alias would list every note twice; another could leave the vault) and symlinked
files are skipped: their targets are listed under their own paths. The index
file itself isn't listed.

**What a listing leaves out (ruling R13).** ``kb files`` and ``kb search`` skip
the policy's ``local_only.always`` paths (``.venv/``, ``.scout-logs/``,
``.claude/``, ``.env*``, ``.mcp.json`` ...) except its ``sync_allow`` carve-outs,
and don't even walk those directories, unless a ``--glob`` names them
explicitly: a glob that starts with a literal character (``.scout-logs/**``,
``.env*``) and matches the path. A glob that starts with a wildcard
(``**/*.md``) never pulls plumbing in. ``kb read`` and ``kb classify`` still
reach any vault file. The overlay's ``local_only_add`` paths (``trading/**``)
are vault content: listed and searched as usual, labelled ``local_only``.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scout import __version__
from scout.errors import KBError
from scout.kb.lint_config import glob_match
from scout.kb.ontology import KnowledgeGraph, find_vault
from scout.kb.privacy import (
    CLASSIFIER_VERSION,
    FULL_READERS,
    VISIBILITIES,
    Label,
    NoteFacts,
    Policy,
    classify,
    decode_head,
    label_from_facts,
    load_policy,
    normalize_rel,
    note_facts,
    read_head,
    visible_to,
)
from scout.kb.read_log import log_event

INDEX_FILENAME = "privacy-index.json"
INDEX_VERSION = 2
SEARCH_BATCH = 500
ENGINE_SCHEMA = Path(__file__).with_name("schema.yaml")
_RACY_WINDOW_NS = 2_000_000_000
_INDEX_PREFIXES = (f".scout-cache/{INDEX_FILENAME}", f".scout-cache/.{INDEX_FILENAME}.")
_PRIVATE = "[private] "
_SOURCES = ("frontmatter", "path_default", "default", "forced")
_STATES = ("ok", "unparseable", "unreadable")


class Denied(Exception):
    """The reader may not see this file (or any file)."""


class UsageError(ValueError):
    """A bad argument: the CLI exits 2 (a path outside the vault, a pattern that won't compile ...)."""


class PatternError(UsageError):
    """A search that can't run: a pattern that doesn't compile, or ``rg`` failing outright."""


# ----- the labelled listing -------------------------------------------------------


def policy_hash(policy: Policy) -> str:
    return hashlib.sha256(json.dumps(policy.to_json(), sort_keys=True).encode("utf-8")).hexdigest()


def _literal_prefix(glob: str) -> str:
    """The part of a glob before its first wildcard."""
    cut = [i for i in (glob.find("*"), glob.find("?")) if i >= 0]
    return glob[: min(cut)] if cut else glob


class _Scope:
    """Which paths a listing walks and returns (ruling R13; see the module docstring)."""

    def __init__(self, policy: Policy, globs: Sequence[str]) -> None:
        self.always = policy.local_only_always
        self.allow = policy.sync_allow
        self.explicit = tuple(g for g in globs if g and g[0] not in "*?")
        prefixes = tuple(_literal_prefix(g) for g in self.always)
        # A cheap pre-check: a path that starts with none of these can't be plumbing.
        self._always_prefixes = prefixes if all(prefixes) else ("",)
        # Directories a `P/**` entry covers whole: never walked unless needed.
        self._roots = tuple(g[:-3] for g in self.always if g.endswith("/**") and g[:-3] == _literal_prefix(g[:-3]))
        self._needed = tuple(_literal_prefix(g) for g in (*self.allow, *self.explicit))

    def plumbing(self, rel: str) -> bool:
        """``local_only.always`` and not carved out by ``sync_allow``."""
        if not rel.startswith(self._always_prefixes):
            return False
        return any(glob_match(g, rel) for g in self.always) and not any(glob_match(g, rel) for g in self.allow)

    def skip_file(self, rel: str) -> bool:
        return self.plumbing(rel) and not any(glob_match(g, rel) for g in self.explicit)

    def skip_dir(self, rel: str) -> bool:
        if rel.rsplit("/", 1)[-1] == ".git":
            return True
        if not any(rel == r or rel.startswith(r + "/") for r in self._roots):
            return False
        d = rel + "/"
        return not any(p.startswith(d) or d.startswith(p) for p in self._needed)

    def unwalked(self, rel: str) -> bool:
        """Whether a file at ``rel`` is outside this listing (for keeping its index entry)."""
        parts = rel.split("/")
        if any(self.skip_dir("/".join(parts[:i])) for i in range(1, len(parts))):
            return True
        return self.skip_file(rel)


def _walk(vault: Path, scope: _Scope) -> Iterator[tuple[str, os.stat_result]]:
    """``(vault-relative path, lstat)`` for every regular file ``scope`` keeps."""
    stack = [(str(vault), "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            it = os.scandir(directory)
        except OSError:
            continue
        with it:
            for entry in it:
                rel = prefix + entry.name
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if not scope.skip_dir(rel):
                            stack.append((entry.path, rel + "/"))
                    elif entry.is_file(follow_symlinks=False) and not scope.skip_file(rel):
                        yield rel, entry.stat(follow_symlinks=False)
                except OSError:
                    continue


def _stat_key(st: os.stat_result) -> list[int]:
    return [st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino]


def _facts_json(facts: NoteFacts) -> dict[str, Any]:
    declared = facts.declared
    return {
        "state": facts.state,
        "visibility": facts.visibility,
        "declared": list(declared) if isinstance(declared, tuple) else declared,
        "warnings": list(facts.warnings),
    }


def _str_tuple(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    return None


def _facts_from_json(data: Any) -> NoteFacts | None:
    """The facts an index entry stored, or None when the entry is malformed."""
    if not isinstance(data, dict) or set(data) != {"state", "visibility", "declared", "warnings"}:
        return None
    state, vis, declared, warnings = data["state"], data["visibility"], data["declared"], _str_tuple(data["warnings"])
    if state not in _STATES or (vis is not None and vis not in VISIBILITIES) or warnings is None:
        return None
    if isinstance(declared, list):
        declared = _str_tuple(declared)
    if not isinstance(declared, (str, tuple)):
        return None
    return NoteFacts(state=state, visibility=vis, declared=declared, warnings=warnings)


_LABEL_KEYS = frozenset(Label(path="", visibility="normal", visibility_source="default").to_json())


def _label_from_json(data: Any, rel: str) -> Label | None:
    """The label an index entry stored for ``rel``, or None when it is malformed."""
    if not isinstance(data, dict) or set(data) != _LABEL_KEYS or data["path"] != rel:
        return None
    declared = data["federation_scope_declared"]
    if isinstance(declared, list):
        declared = _str_tuple(declared)
    warnings = _str_tuple(data["warnings"])
    if (
        data["visibility"] not in VISIBILITIES
        or data["visibility_source"] not in _SOURCES
        or data["federation_scope"] != "none"
        or data["federation_source"] != "reserved"
        or not isinstance(declared, (str, tuple))
        or not isinstance(data["local_only"], bool)
        or not isinstance(data["shared_file"], bool)
        or warnings is None
    ):
        return None
    return Label(
        path=rel,
        visibility=data["visibility"],
        visibility_source=data["visibility_source"],
        federation_scope_declared=declared,
        local_only=data["local_only"],
        shared_file=data["shared_file"],
        warnings=warnings,
    )


def _index_header(policy: str) -> dict[str, Any]:
    return {"version": INDEX_VERSION, "classifier": CLASSIFIER_VERSION, "engine": __version__, "policy": policy}


def _read_index(path: Path) -> tuple[str | None, dict[str, Any]]:
    """``(policy hash, entries)``; empty when missing, torn, or written by another
    index version, classifier version or engine version."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, {}
    if (
        not isinstance(data, dict)
        or data.get("version") != INDEX_VERSION
        or data.get("classifier") != CLASSIFIER_VERSION
        or data.get("engine") != __version__
        or not isinstance(data.get("files"), dict)
    ):
        return None, {}
    policy = data.get("policy")
    return (policy if isinstance(policy, str) else None), data["files"]


def _write_index(path: Path, policy: str, files: dict[str, Any]) -> None:
    """Replace the index atomically; a failure only costs the next call a re-read."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({**_index_header(policy), "files": files}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()


def all_labels(vault: Path, policy: Policy, globs: Sequence[str] = ()) -> dict[str, Label]:
    """Every file a listing of ``vault`` (resolved) keeps → its label, through the privacy index.

    ``globs`` only decide which plumbing paths are walked (:class:`_Scope`);
    callers filter by them. Index entries for paths this walk leaves out are kept.
    """
    scope = _Scope(policy, globs)
    index_path = vault / ".scout-cache" / INDEX_FILENAME
    want_policy = policy_hash(policy)
    old_policy, old = _read_index(index_path)
    same_policy = old_policy == want_policy
    fresh: dict[str, Any] = {}
    labels: dict[str, Label] = {}
    now = time.time_ns()
    for rel, st in _walk(vault, scope):
        if rel.startswith(_INDEX_PREFIXES):  # the index itself (and its temp files)
            continue
        key = _stat_key(st)
        entry = old.get(rel)
        label: Label | None = None
        facts: NoteFacts | None = None
        if isinstance(entry, dict) and entry.get("key") == key:
            facts = _facts_from_json(entry.get("facts"))
            if facts is not None and same_policy:
                label = _label_from_json(entry.get("label"), rel)
        if label is None:
            if facts is None:
                text, unreadable = read_head(vault / rel)
                facts = note_facts(text, unreadable=unreadable)
            label = label_from_facts(rel, facts, policy)
            entry = {"key": key, "facts": _facts_json(facts), "label": label.to_json()}
        labels[rel] = label
        if now - st.st_mtime_ns > _RACY_WINDOW_NS:
            fresh[rel] = entry
    if same_policy:  # a policy change drops them: their labels would be stale
        for rel, entry in old.items():
            if rel not in labels and scope.unwalked(rel):
                fresh[rel] = entry
    if not same_policy or fresh != old:
        _write_index(index_path, want_policy, fresh)
    return labels


def visible_files(vault: Path, reader: str, globs: Sequence[str] = ()) -> list[tuple[str, Label]]:
    """The files ``reader`` may see, sorted, each with its label. Nothing is touched for others.

    ``globs`` (vault-relative, ``*`` within a segment, ``**`` across) keep a file
    when any of them matches. Plumbing (``local_only.always`` minus
    ``sync_allow``) is left out unless a glob names it explicitly (ruling R13).
    """
    if reader not in FULL_READERS:
        return []
    root = vault.resolve()
    labels = all_labels(root, load_policy(root), globs)
    return [
        (rel, label)
        for rel, label in sorted(labels.items())
        if visible_to(label, reader) and (not globs or any(glob_match(g, rel) for g in globs))
    ]


# ----- search -----------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Match:
    path: str
    line: int
    text: str
    visibility: str
    label: Label = field(compare=False)


def find_rg() -> str | None:
    """The ``rg`` binary on PATH (a shell function named ``rg`` doesn't count)."""
    return shutil.which("rg")


def _rg_text(obj: Any) -> str:
    """An ``rg --json`` text-or-bytes field as a string (bad UTF-8 replaced)."""
    if isinstance(obj, dict):
        if isinstance(obj.get("text"), str):
            return str(obj["text"])
        if isinstance(obj.get("bytes"), str):
            return base64.b64decode(obj["bytes"]).decode("utf-8", errors="replace")
    return ""


def _rg_path(obj: Any) -> str:
    if isinstance(obj, dict) and isinstance(obj.get("bytes"), str):
        return os.fsdecode(base64.b64decode(obj["bytes"]))
    return _rg_text(obj)


def _search_rg(
    rg: str, vault: Path, rels: list[str], pattern: str, fixed: bool, ignore_case: bool
) -> list[tuple[str, int, str]]:
    base = [rg, "--json", "--no-config", "--no-messages"]
    if fixed:
        base.append("--fixed-strings")
    if ignore_case:
        base.append("--ignore-case")
    base += ["-e", pattern, "--"]
    found: list[tuple[str, int, str]] = []
    for i in range(0, len(rels), SEARCH_BATCH):
        proc = subprocess.run([*base, *rels[i : i + SEARCH_BATCH]], cwd=vault, capture_output=True, check=False)
        batch: list[tuple[str, int, str]] = []
        binary: set[str] = set()
        for raw in proc.stdout.splitlines():
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            data = event.get("data") or {}
            if event.get("type") == "end" and data.get("binary_offset") is not None:
                binary.add(_rg_path(data.get("path")))
            elif event.get("type") == "match" and isinstance(data.get("line_number"), int):
                text = _rg_text(data.get("lines")).rstrip("\n").removesuffix("\r")
                batch.append((_rg_path(data.get("path")), data["line_number"], text))
        # rg searches an explicitly named binary file up to its first NUL and
        # reports `binary_offset` in the file's `end` event; there is no flag to
        # skip such files, so drop their matches here, as the Python scan skips
        # any file with a NUL byte.
        batch = [m for m in batch if m[0] not in binary]
        if proc.returncode == 2 and not batch and proc.stderr.strip():
            message = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
            raise PatternError(message[-1] if message else "rg failed")
        found += batch
    return found


def _search_python(
    vault: Path, rels: list[str], pattern: str, fixed: bool, ignore_case: bool
) -> list[tuple[str, int, str]]:
    try:
        regex = re.compile(re.escape(pattern) if fixed else pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        raise PatternError(str(e)) from e
    found: list[tuple[str, int, str]] = []
    for rel in rels:
        try:
            data = (vault / rel).read_bytes()
        except OSError:
            continue
        if b"\x00" in data:  # binary: skipped, like rg's default
            continue
        for n, line in enumerate(data.decode("utf-8", errors="replace").split("\n"), 1):
            line = line.removesuffix("\r")
            if regex.search(line):
                found.append((rel, n, line))
    return found


def search(
    vault: Path,
    reader: str,
    pattern: str,
    globs: Sequence[str] = (),
    fixed: bool = False,
    ignore_case: bool = False,
) -> list[Match]:
    """Lines matching ``pattern`` in the files ``reader`` may see, each with its file's label.

    ``PatternError`` for a pattern that doesn't compile, or when ``rg`` exits 2
    with an error and no matches. Binary files (a NUL byte) give no matches,
    with ``rg`` or without. Bad UTF-8 is replaced, never fatal.
    """
    files = visible_files(vault, reader, globs)
    if not files:
        return []
    root = vault.resolve()
    labels = dict(files)
    rels = list(labels)
    rg = find_rg()
    raw = (
        _search_rg(rg, root, rels, pattern, fixed, ignore_case)
        if rg
        else _search_python(root, rels, pattern, fixed, ignore_case)
    )
    out = [Match(p, n, t, labels[p].visibility, labels[p]) for p, n, t in raw if p in labels]
    return sorted(out)


# ----- read -------------------------------------------------------------------------


def read_file(vault: Path, reader: str, path: str) -> tuple[Label, bytes]:
    """``(label, bytes)`` of one vault file.

    ``Denied`` for a reader that sees nothing (before anything is touched, so a
    denial never says whether the file exists); ``UsageError`` for a path
    outside the vault, under ``.git/``, or that isn't a file.
    """
    if reader not in FULL_READERS:
        raise Denied(reader)
    root = vault.resolve()
    try:
        rel = normalize_rel(root, path)
    except ValueError as e:
        raise UsageError(str(e)) from e
    if ".git" in rel.split("/"):
        raise UsageError(f"not a vault file: {rel}")
    target = root / rel
    if not target.is_file():
        raise UsageError(f"no such file: {rel}")
    try:
        data = target.read_bytes()
    except OSError as e:
        raise UsageError(f"can't read {rel}: {e.strerror or e}") from e
    text = decode_head(data) if target.suffix.lower() == ".md" else None
    label = classify(rel, text, load_policy(root))
    if not visible_to(label, reader):
        raise Denied(reader)
    return label, data


# ----- the graph ----------------------------------------------------------------------


def graph_vault(repo: Path, kb_root: str | None) -> Path:
    """The vault a graph command labels against: the one holding ``kb_root`` when given."""
    if kb_root is None:
        return repo
    try:
        return find_vault(kb_root)
    except KBError:
        return repo


def load_graph(vault: Path, reader: str, kb_root: str | None = None, schema: str | None = None) -> KnowledgeGraph:
    """The labelled graph of ``kb_root`` (default ``<vault>/knowledge-base``).

    The schema defaults to the vault's ``knowledge-base/ontology/schema.yaml``,
    else the engine's. A reader that sees nothing gets an empty graph, and no
    file is looked at.
    """
    kb = Path(kb_root) if kb_root else vault / "knowledge-base"
    if schema is None:
        own = kb / "ontology" / "schema.yaml"
        schema = str(own if reader in FULL_READERS and own.is_file() else ENGINE_SCHEMA)
    try:
        return KnowledgeGraph(schema, str(kb), reader=reader, vault=vault).load()
    except ValueError as e:  # kb_root outside the vault
        raise KBError(f"KB root {kb} is not inside the vault {vault}") from e


def _public(entity: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entity.items() if not k.startswith("_")}


def _mark(label: Mapping[str, Any] | Label | None) -> str:
    if isinstance(label, Label):
        return _PRIVATE if label.visibility == "private" else ""
    return _PRIVATE if label and label.get("visibility") == "private" else ""


def query_rows(graph: KnowledgeGraph, filters: Mapping[str, Any], visibility: str | None) -> list[dict[str, Any]]:
    """``graph.query`` with the template's truthy-filter rule (empty values ignored)."""
    return graph.query(visibility=visibility, **{k: v for k, v in filters.items() if v})


# ----- command outputs ---------------------------------------------------------------


@dataclass
class Output:
    """One command's result: its JSON form, its text form, and what it returned.

    ``lines`` are printed one ``print()`` each (so ``"\\nN result(s)."`` gives
    the template's blank line); ``raw``, when set, is written as is instead.
    ``labels`` are the labels of the paths returned (the audit log counts them).
    """

    data: Any
    lines: list[str] = field(default_factory=list)
    labels: list[Mapping[str, Any]] = field(default_factory=list)
    denied: int = 0
    code: int = 0
    raw: str | None = None


def _labels_json(labels: Sequence[Label]) -> list[Mapping[str, Any]]:
    return [label.to_json() for label in labels]


def cmd_files(vault: Path, reader: str, globs: Sequence[str]) -> Output:
    rows = visible_files(vault, reader, globs)
    return Output(
        data=[{"path": rel, "label": label.to_json()} for rel, label in rows],
        lines=[f"{_mark(label)}{rel}" for rel, label in rows],
        labels=_labels_json([label for _, label in rows]),
    )


def cmd_search(vault: Path, reader: str, pattern: str, globs: Sequence[str], fixed: bool, ignore_case: bool) -> Output:
    hits = search(vault, reader, pattern, globs, fixed=fixed, ignore_case=ignore_case)
    by_path = {m.path: m.label for m in hits}
    return Output(
        data=[{"path": m.path, "line": m.line, "text": m.text, "label": m.label.to_json()} for m in hits],
        lines=[f"{_mark(m.label)}{m.path}:{m.line}:{m.text}" for m in hits],
        labels=_labels_json(list(by_path.values())),
    )


def cmd_read(vault: Path, reader: str, path: str) -> Output:
    try:
        label, data = read_file(vault, reader, path)
    except Denied:
        denial = {"denied": True, "reason": f"not visible to {reader}"}
        return Output(data=denial, lines=[json.dumps(denial)], denied=1, code=3)
    content = data.decode("utf-8", errors="replace")
    header = f"{_PRIVATE}{label.path}\n" if label.visibility == "private" else ""
    return Output(data={"label": label.to_json(), "content": content}, raw=header + content, labels=[label.to_json()])


def resolve(vault: Path, reader: str, graph: KnowledgeGraph, name: str) -> list[dict[str, Any]]:
    """Canonical files for a name, alias or slug among the visible notes.

    Exact (case-insensitive) matches on an entity's ``name``, ``aliases`` or file
    slug, and on the slug of any visible KB note; when there are none, the fuzzy
    people match of ``name_lookup``. Rows: ``path``, ``name`` (None for a plain
    note), ``match_strength``, ``matched_field``, ``label``.
    """
    if reader not in FULL_READERS:
        return []
    key = name.strip().lower()
    if not key:
        return []
    rows: dict[str, dict[str, Any]] = {}
    for ename, entity in graph.entities.items():
        label = entity.get("_label") or {}
        rel = label.get("path", "")
        fields = [("name", str(ename)), *(("alias", str(a)) for a in entity.get("aliases") or [] if a)]
        fields.append(("slug", Path(rel).stem))
        for fname, value in fields:
            if value.strip().lower() == key and rel not in rows:
                rows[rel] = {"path": rel, "name": ename, "match_strength": "exact", "matched_field": fname}
                rows[rel]["label"] = label
    for rel, flabel in visible_files(vault, reader, ("knowledge-base/**/*.md",)):
        if Path(rel).stem.lower() == key and rel not in rows:
            rows[rel] = {"path": rel, "name": None, "match_strength": "exact", "matched_field": "slug"}
            rows[rel]["label"] = flabel.to_json()
    if not rows:
        for cand in graph.name_lookup(name):
            label = cand.get("_label") or {}
            rel = label.get("path", "")
            rows.setdefault(
                rel,
                {
                    "path": rel,
                    "name": cand["name"],
                    "match_strength": cand["match_strength"],
                    "matched_field": cand["matched_field"],
                    "label": label,
                },
            )
    return sorted(rows.values(), key=lambda r: (r["match_strength"] != "exact", r["path"]))


def cmd_resolve(vault: Path, reader: str, graph: KnowledgeGraph, name: str) -> Output:
    rows = resolve(vault, reader, graph, name)
    return Output(
        data=rows,
        lines=[f"{_mark(r['label'])}{r['path']}" for r in rows],
        labels=[r["label"] for r in rows],
        code=0 if rows else 1,
    )


def _entity_row(entity: Mapping[str, Any]) -> dict[str, Any]:
    label = entity.get("_label") or {}
    return {"name": entity.get("name"), "path": label.get("path"), "entity": _public(entity), "label": label}


def cmd_query(graph: KnowledgeGraph, filters: Mapping[str, Any], visibility: str | None) -> Output:
    results = query_rows(graph, filters, visibility)
    lines = []
    for r in results:
        extras = [f"{k}={r[k]}" for k in ("status", "domain", "deadline") if r.get(k)]
        suffix = f"  ({', '.join(extras)})" if extras else ""
        lines.append(f"  {_mark(r.get('_label'))}[{r.get('type')}] {r.get('name')}{suffix}")
    lines.append(f"\n{len(results)} result(s).")
    return Output(
        data=[_entity_row(r) for r in results], lines=lines, labels=[r["_label"] for r in results if r.get("_label")]
    )


def _node_labels(graph: KnowledgeGraph, names: Sequence[str]) -> list[Mapping[str, Any]]:
    seen: dict[str, Mapping[str, Any]] = {}
    for n in names:
        label = graph.label_of(n)
        if label:
            seen.setdefault(label["path"], label)
    return list(seen.values())


def cmd_related(graph: KnowledgeGraph, name: str) -> Output:
    rels = graph.related(name)
    rows: list[dict[str, Any]] = [{**r, "label": graph.label_of(r["target"])} for r in rels]
    if rows:
        lines = [f"  {_mark(graph.label_of(r['target']))}{r['type']} → {r['target']}" for r in rels]
    else:
        lines = [f"No relationships found for '{name}'."]
    return Output(data=rows, lines=lines, labels=_node_labels(graph, [r["target"] for r in rels]))


def cmd_traverse(graph: KnowledgeGraph, name: str, hops: int | None, rels: str | None) -> Output:
    rel_types = [s.strip() for s in rels.split(",")] if rels else None
    results = graph.traverse(name, max_hops=hops or 2, rel_types=rel_types)
    rows = [{**{k: v for k, v in r.items() if k != "_label"}, "label": r["_label"]} for r in results]
    if not results:
        lines = [f"No entities reachable from '{name}'."]
    else:
        lines = [
            f"  {_mark(r['label'])}[{r['hops']} hop{'s' if r['hops'] != 1 else ''}] {r['entity']} (via {r['via']})"
            for r in rows
        ]
        lines.append(f"\n{len(results)} reachable entit{'ies' if len(results) != 1 else 'y'}.")
    return Output(data=rows, lines=lines, labels=_node_labels(graph, [r["entity"] for r in results]))


def cmd_path(graph: KnowledgeGraph, source: str, target: str, hops: int | None) -> Output:
    steps = graph.path(source, target, max_hops=hops or 6)
    if steps is None:
        return Output(data=None, lines=[f"No path from '{source}' to '{target}'."])
    data = {
        "source": source,
        "target": target,
        "source_label": graph.label_of(source),
        "steps": [{"type": t, "node": n, "label": graph.label_of(n)} for t, n in steps],
    }
    labels = _node_labels(graph, [source, *(n for _, n in steps)])
    if not steps:
        return Output(data=data, lines=[f"'{source}' and '{target}' are the same entity."], labels=labels)
    chain = (
        _mark(graph.label_of(source)) + source + "".join(f" --{t}--> {_mark(graph.label_of(n))}{n}" for t, n in steps)
    )
    return Output(data=data, lines=[f"  {chain}", f"\n{len(steps)} hop(s)."], labels=labels)


def cmd_entity(graph: KnowledgeGraph, name: str) -> Output:
    entity = graph.entity(name)
    if not entity:
        return Output(data=None, lines=[f"Entity '{name}' not found."])
    shown = {**_public(entity), "_label": entity.get("_label")}
    return Output(
        data=_entity_row(entity),
        lines=[json.dumps(shown, indent=2, default=str)],
        labels=[entity["_label"]] if entity.get("_label") else [],
    )


def cmd_name_lookup(graph: KnowledgeGraph, token: str, threshold: float) -> Output:
    results = graph.name_lookup(token, distance_threshold=threshold)
    rows = [
        {
            **{k: v for k, v in r.items() if k != "_label"},
            "path": (r.get("_label") or {}).get("path"),
            "label": r.get("_label"),
        }
        for r in results
    ]
    if not results:
        lines = [f"No KB match for '{token}' (threshold={threshold}). Route to review-queue; do NOT elevate to 🔴."]
    else:
        lines = [
            f"  {_mark(r['label'])}[{r['match_strength']:5s}] {r['name']} "
            f"(slug={r['slug']}, matched_field={r['matched_field']}, score={r['score']})"
            for r in rows
        ]
        lines.append(f"\n{len(results)} candidate(s).")
    return Output(data=rows, lines=lines, labels=[r["label"] for r in rows if r["label"]])


def cmd_export(graph: KnowledgeGraph) -> Output:
    text = graph.export_json()
    return Output(data=json.loads(text), lines=[text], labels=_node_labels(graph, list(graph.entities)))


def cmd_stats(graph: KnowledgeGraph) -> Output:
    by_type: dict[str, int] = {}
    for e in graph.entities.values():
        t = e.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
    lines = [f"Entities: {len(graph.entities)}", f"Relationships: {len(graph.relationships)}"]
    lines += [f"  {t}: {count}" for t, count in sorted(by_type.items())]
    data = {"entities": len(graph.entities), "relationships": len(graph.relationships), "by_type": by_type}
    return Output(data=data, lines=lines)


def cmd_validate(graph: KnowledgeGraph) -> Output:
    errors = graph.validate()
    rows: list[dict[str, Any]] = [{**e, "label": graph.label_of(e["entity"])} for e in errors]
    if rows:
        lines = [f"  {_mark(graph.label_of(e['entity']))}[{e['entity']}] {e['message']}" for e in errors]
        lines.append(f"\n{len(errors)} validation error(s) found.")
    else:
        lines = ["No validation errors."]
    return Output(data=rows, lines=lines, labels=_node_labels(graph, [e["entity"] for e in errors]))


# ----- the audit -----------------------------------------------------------------------


def audit(vault: Path, reader: str, command: str, args: Sequence[str], out: Output) -> None:
    """Log one command's call (spec §7.4): counts of what it returned, never the paths."""
    paths = {str(label.get("path")): label for label in out.labels if label}
    log_event(
        vault,
        reader=reader,
        command=command,
        surface=None,
        args=args,
        returned=len(paths),
        denied=out.denied,
        private_paths=sum(1 for label in paths.values() if label.get("visibility") == "private"),
        blocked=False,
    )
