"""Canonical privacy-filtered property-graph export (``scoutctl kb export --format jsonl``).

Compiles the markdown vault and its frontmatter ontology into a single
newline-delimited JSON property graph that every pluggable :class:`~scout.kb.backend.KBBackend`
can load without re-implementing Scout's privacy classifier, ontology inverse
expansion, or convention-aware ``[[wikilink]]`` resolution:

- ``{"kind": "meta", ...}`` — vault HEAD SHA (when available), reader, entity and
  edge counts, and body-link classification buckets.
- ``{"kind": "node", "key", "labels", "props"}`` — every visible ``.md`` note
  (``Note``, plus ``Entity`` and PascalCase schema type for ontology entities),
  plus explicit placeholder nodes (``Attachment``, ``Unresolved``, ``IssueRef``,
  ``MemorySlug``) for non-note link targets.
- ``{"kind": "edge", "src", "dst", "type", "props"}`` — ontology edges with
  ``props.origin == "frontmatter"`` (inverses materialised by
  :class:`~scout.kb.ontology.KnowledgeGraph`) and body wikilinks aggregated per
  ``(src, dst)`` as ``LINKS_TO`` (plus ``CITES`` when ``dst`` is under
  ``knowledge-base/sources/``) with ``props.origin == "body"``.

**Privacy invariants:**

1. A reader for which :func:`~scout.kb.privacy.reads_any` is false (``agent:*``)
   touches no vault file and gets an empty graph.
2. The visible file set comes from :func:`~scout.kb.read.all_labels` (ruling R13),
   so engine plumbing (``.scout-state/``, ``.scout-logs/``, ``.claude/`` ...) is
   never exported.
3. Under narrowed readers (``elsewhere``, ``delegate:<id>``), hidden files are
   absent as nodes *and* any body link or ontology relationship targeting a
   hidden file is dropped rather than turned into an ``Unresolved`` placeholder
   (which would leak the hidden note's name or slug). Ambiguity flags count
   visible candidate notes only.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout.kb.ontology import KnowledgeGraph
from scout.kb.privacy import FULL_READERS, Label, load_policy, reads_any, visible_to

FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")
NEWLINE_RE = re.compile(r"\n")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
ISSUE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}-\d{1,6}$")
MEMORY_SLUG_RE = re.compile(r"^(project|feedback|user|reference)_[a-z0-9_]+$")

_FM_SCALAR_RE = re.compile(r"^(last_verified|as_of|date|status|visibility):\s*(.+)$")
_FM_NAME_KEYS_RE = re.compile(r"^(name|aka|title|aliases):\s*(.*)$")
_FM_LIST_ITEM_RE = re.compile(r"^\s*-\s+(.+?)\s*$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_REL_TYPE_RE = re.compile(r"[^A-Za-z0-9]+")


@dataclass
class PropertyGraph:
    """In-memory representation of the exported property graph."""

    meta: dict[str, Any]
    nodes: dict[str, dict[str, Any]]
    edges: list[dict[str, Any]]
    labels: list[Mapping[str, Any]]
    entity_keys: dict[str, str]


def norm_target(value: str) -> str:
    """Normalized lookup key matching Scout's dangling-link convention."""
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub("-", stripped).strip("-")


def strip_code(text: str) -> str:
    """Strip fenced code blocks and inline code spans while preserving line numbers."""
    out: list[str] = []
    in_fence = False
    fence_tok = ""
    for line in text.split("\n"):
        m = FENCE_RE.match(line)
        if m:
            tok = m.group(1)
            if not in_fence:
                in_fence = True
                fence_tok = tok
                out.append("")
                continue
            if tok == fence_tok:
                in_fence = False
                fence_tok = ""
                out.append("")
                continue
        out.append("" if in_fence else INLINE_CODE_RE.sub("", line))
    return "\n".join(out)


def split_target(raw: str) -> str:
    """Extract target from ``[[target#anchor|alias]]`` (including table ``\\|`` pipes)."""
    target = raw.replace("\\|", "|").split("|", 1)[0]
    return target.split("#", 1)[0].strip()


def layer_of(rel: str) -> str:
    """Classify a vault-relative note path into its architectural layer."""
    parts = rel.split("/")
    if parts[0] != "knowledge-base":
        base = parts[0][:-3] if len(parts) == 1 and parts[0].endswith(".md") else parts[0]
        return f"vault:{base}"
    if len(parts) == 2:
        return "kb-root"
    if "/knowledge/" in rel:
        return "knowledge"
    stem = parts[-1][:-3] if parts[-1].endswith(".md") else parts[-1]
    if len(parts) >= 3 and stem == parts[-2]:
        return "front-page"
    return parts[1]


def _clean_scalar(raw: str) -> str:
    val = raw.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        return val[1:-1].strip()
    return val.split(" #", 1)[0].strip()


def _frontmatter_head_info(text: str) -> tuple[dict[str, str], list[str]]:
    """Extract scalar properties, first H1 title, and resolvable frontmatter names."""
    props: dict[str, str] = {}
    names: list[str] = []
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        in_list = False
        for line in lines[1:80]:
            if line.strip() == "---":
                break
            if in_list:
                item = _FM_LIST_ITEM_RE.match(line)
                if item:
                    cleaned = _clean_scalar(item.group(1))
                    if cleaned:
                        names.append(cleaned)
                    continue
                in_list = False
            sm = _FM_SCALAR_RE.match(line)
            if sm and sm.group(1) not in props:
                cleaned = _clean_scalar(sm.group(2))
                if cleaned:
                    props[sm.group(1)] = cleaned
            nm = _FM_NAME_KEYS_RE.match(line)
            if nm:
                val = nm.group(2).strip()
                if not val or val.startswith("#"):
                    in_list = True
                elif val.startswith("[") and "]" in val:
                    inner = val[1 : val.index("]")]
                    for part in inner.split(","):
                        cleaned = _clean_scalar(part)
                        if cleaned:
                            names.append(cleaned)
                else:
                    cleaned = _clean_scalar(val)
                    if cleaned and cleaned not in ("null", "~", "[]", "|", ">"):
                        names.append(cleaned)
    for line in lines:
        if line.startswith("# "):
            props["title"] = line[2:].strip()
            break
    return props, names


def _path_keys_for_md(rel: str, extra_names: list[str]) -> set[str]:
    """Every spelling by which a markdown note at ``rel`` can be linked."""
    no_ext = rel[:-3]
    stem = os.path.basename(no_ext)
    keys: set[str] = {rel, no_ext, stem, norm_target(stem)}
    parts_no_ext = no_ext.split("/")
    parts_full = rel.split("/")
    for i in range(len(parts_no_ext)):
        sub_no_ext = "/".join(parts_no_ext[i:])
        sub_full = "/".join(parts_full[i:])
        keys.add(sub_no_ext)
        keys.add(norm_target(sub_no_ext))
        keys.add(sub_full)
        keys.add(norm_target(sub_full))
    for nm in extra_names:
        keys.add(nm)
        keys.add(norm_target(nm))
    keys.discard("")
    return keys


def _path_keys_for_attachment(rel: str) -> set[str]:
    parts = rel.split("/")
    keys: set[str] = set()
    for i in range(len(parts)):
        sub = "/".join(parts[i:])
        keys.add(sub)
        keys.add(norm_target(sub))
    keys.discard("")
    return keys


def _git_head_sha(vault: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(vault), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except OSError:
        return ""


def _pascal_label(entity_type: str) -> str:
    parts = [p.capitalize() for p in _REL_TYPE_RE.split(entity_type.strip()) if p]
    return "".join(parts) or "Untyped"


def build_property_graph(
    vault: Path,
    reader: str,
    *,
    graph: KnowledgeGraph | None = None,
    kb_root: str | None = None,
    schema: str | None = None,
    include_body: bool = False,
    body_scope: str = "knowledge-base/",
) -> PropertyGraph:
    """Compile ``vault`` as seen by ``reader`` into a :class:`PropertyGraph`."""
    from scout.kb.read import all_labels, load_graph

    if not reads_any(reader):
        empty_meta = {
            "kind": "meta",
            "reader": reader,
            "sha": "",
            "nodes": 0,
            "notes": 0,
            "entities": 0,
            "edges": 0,
            "ontology_edges": 0,
            "body_edges": 0,
            "buckets": {},
        }
        return PropertyGraph(meta=empty_meta, nodes={}, edges=[], labels=[], entity_keys={})

    root = vault.resolve()
    ont_graph = graph if graph is not None else load_graph(root, reader, kb_root, schema)
    policy = load_policy(root)
    full = reader in FULL_READERS
    file_labels: dict[str, Label] = all_labels(root, policy)

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    returned_labels: list[Mapping[str, Any]] = []
    texts: dict[str, str] = {}

    visible_md_index: dict[str, str] = {}
    visible_att_index: dict[str, str] = {}
    hidden_keys: set[str] = set()
    stem_candidates: dict[str, set[str]] = defaultdict(set)

    for rel, lbl in sorted(file_labels.items()):
        is_vis = full or visible_to(lbl, reader, policy)
        full_path = root / rel
        if rel.endswith(".md"):
            try:
                raw_bytes = full_path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw_bytes:
                continue
            raw_text = raw_bytes.decode("utf-8", errors="replace")
            fm_props, fm_names = _frontmatter_head_info(raw_text)
            if not is_vis:
                hidden_keys.update(_path_keys_for_md(rel, fm_names))
                continue

            texts[rel] = raw_text
            lbl_json = lbl.to_json()
            returned_labels.append(lbl_json)
            stem = os.path.basename(rel)[:-3]
            props: dict[str, Any] = {
                "path": rel,
                "stem": stem,
                "layer": layer_of(rel),
                "bytes": len(raw_bytes),
                "sha": hashlib.sha256(raw_bytes).hexdigest()[:16],
                **fm_props,
            }
            if include_body and (not body_scope or rel.startswith(body_scope)):
                props["body"] = raw_text
            nodes[rel] = {
                "kind": "node",
                "key": rel,
                "labels": ["Note"],
                "props": props,
                "_label": lbl_json,
            }
            for k in _path_keys_for_md(rel, fm_names):
                visible_md_index.setdefault(k, rel)
            for sk in (stem, norm_target(stem)):
                if sk:
                    stem_candidates[sk].add(rel)
        else:
            if not is_vis:
                hidden_keys.update(_path_keys_for_attachment(rel))
            else:
                for k in _path_keys_for_attachment(rel):
                    visible_att_index.setdefault(k, rel)

    # Enrich Note nodes with KnowledgeGraph entities
    entity_keys: dict[str, str] = {}
    for ename, ent in sorted(ont_graph.entities.items()):
        src_path = ent.get("_source_path", "")
        rel_key = (ent.get("_label") or {}).get("path") or (
            os.path.relpath(src_path, root).replace(os.sep, "/") if src_path else ""
        )
        node = nodes.get(rel_key)
        if node is None:
            continue
        etype = str(ent.get("type", "")).strip()
        type_label = _pascal_label(etype)
        node["labels"] = sorted(set(node["labels"]) | {"Entity", type_label})
        node["props"]["name"] = ename
        node["props"]["entity_type"] = etype
        for prop_key in ("status", "domain", "deadline", "priority"):
            if prop_key in ent and ent[prop_key] is not None and not isinstance(ent[prop_key], (dict, list)):
                node["props"][prop_key] = str(ent[prop_key])
        node["_entity"] = dict(ent)
        entity_keys[ename] = rel_key

    def placeholder(label: str, target: str) -> str:
        pkey = f"{label.lower()}:{target}"
        if pkey not in nodes:
            nodes[pkey] = {
                "kind": "node",
                "key": pkey,
                "labels": [label],
                "props": {"target": target},
            }
        return pkey

    # Ontology edges (inverses already expanded by KnowledgeGraph.load)
    ont_edge_count = 0
    for r in ont_graph.relationships:
        src_name, dst_name, rel_type = r["source"], r["target"], r["type"]
        src_key = entity_keys.get(src_name)
        dst_key = entity_keys.get(dst_name)
        if not full and (src_key is None or dst_key is None):
            continue
        resolved_src = src_key if src_key is not None else placeholder("Unresolved", src_name)
        resolved_dst = dst_key if dst_key is not None else placeholder("Unresolved", dst_name)
        cypher_type = _REL_TYPE_RE.sub("_", rel_type).strip("_").upper() or "RELATED_TO"
        edges.append(
            {
                "kind": "edge",
                "src": resolved_src,
                "dst": resolved_dst,
                "type": cypher_type,
                "props": {"origin": "frontmatter", "ontology_type": rel_type},
            }
        )
        ont_edge_count += 1

    # Body wikilink edges
    buckets: Counter[str] = Counter()
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    dropped_hidden = 0

    for rel, raw_text in texts.items():
        stripped = strip_code(raw_text)
        line_starts = [0] + [m.end() for m in NEWLINE_RE.finditer(stripped)]
        headings_by_line: list[str] = []
        current_heading = ""
        for ln in stripped.split("\n"):
            hm = HEADING_RE.match(ln)
            if hm:
                current_heading = hm.group(2)[:120]
            headings_by_line.append(current_heading)

        for m in WIKILINK_RE.finditer(stripped):
            lineno = bisect.bisect_right(line_starts, m.start())
            section = headings_by_line[lineno - 1] if lineno >= 1 else ""
            target = split_target(m.group(1))
            if not target:
                buckets["anchor-only"] += 1
                continue

            norm_t = norm_target(target)
            md_hit = visible_md_index.get(target) or (visible_md_index.get(norm_t) if norm_t else None)
            att_hit = (
                None
                if md_hit
                else (visible_att_index.get(target) or (visible_att_index.get(norm_t) if norm_t else None))
            )

            ambiguous = False
            if md_hit is not None:
                buckets["resolved"] += 1
                dst_key = md_hit
                if "/" not in target:
                    cands = set(stem_candidates.get(target, ())) | set(stem_candidates.get(norm_t, ()))
                    ambiguous = len(cands) > 1
            elif att_hit is not None:
                buckets["resolved"] += 1
                dst_key = placeholder("Attachment", att_hit)
            elif not full and (target in hidden_keys or (norm_t and norm_t in hidden_keys)):
                dropped_hidden += 1
                continue
            elif ISSUE_ID_RE.match(target):
                buckets["issue-id-convention"] += 1
                dst_key = placeholder("IssueRef", target)
            elif MEMORY_SLUG_RE.match(target):
                buckets["memory-slug"] += 1
                dst_key = placeholder("MemorySlug", target)
            else:
                buckets["real"] += 1
                dst_key = placeholder("Unresolved", target)

            rec = agg.setdefault(
                (rel, dst_key),
                {"count": 0, "lines": [], "sections": [], "ambiguous": False},
            )
            rec["count"] += 1
            if len(rec["lines"]) < 20:
                rec["lines"].append(lineno)
            if section and section not in rec["sections"] and len(rec["sections"]) < 10:
                rec["sections"].append(section)
            rec["ambiguous"] = rec["ambiguous"] or ambiguous

    body_edge_count = 0
    for (src_rel, dst_key), rec in sorted(agg.items()):
        edges.append(
            {
                "kind": "edge",
                "src": src_rel,
                "dst": dst_key,
                "type": "LINKS_TO",
                "props": {"origin": "body", **rec},
            }
        )
        body_edge_count += 1
        if dst_key.startswith("knowledge-base/sources/"):
            edges.append(
                {
                    "kind": "edge",
                    "src": src_rel,
                    "dst": dst_key,
                    "type": "CITES",
                    "props": {"origin": "body", "count": rec["count"]},
                }
            )
            body_edge_count += 1

    meta: dict[str, Any] = {
        "kind": "meta",
        "reader": reader,
        "sha": _git_head_sha(root),
        "nodes": len(nodes),
        "notes": len(texts),
        "entities": len(entity_keys),
        "edges": len(edges),
        "ontology_edges": ont_edge_count,
        "body_edges": body_edge_count,
        "dropped_hidden_links": dropped_hidden,
        "buckets": dict(buckets),
    }
    return PropertyGraph(
        meta=meta,
        nodes=nodes,
        edges=edges,
        labels=returned_labels,
        entity_keys=entity_keys,
    )


def format_jsonl(pg: PropertyGraph) -> str:
    """Serialize a :class:`PropertyGraph` as canonical newline-delimited JSON."""
    lines: list[str] = [json.dumps(pg.meta, ensure_ascii=False, default=str)]
    for node in pg.nodes.values():
        pub_node = {k: v for k, v in node.items() if not k.startswith("_") or k == "_label"}
        lines.append(json.dumps(pub_node, ensure_ascii=False, default=str))
    for edge in pg.edges:
        lines.append(json.dumps(edge, ensure_ascii=False, default=str))
    return "\n".join(lines)
