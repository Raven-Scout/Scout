"""
SCOUT Knowledge Graph Parser: a shim over `scoutctl kb`.

The knowledge graph lives in the Scout engine (`scout.kb.ontology`), which
labels every note `normal` or `private` (#453). This file keeps the parser's
command line and hands each command to `scoutctl kb`, so there is one
implementation of the rules and every result carries its privacy label:

    python knowledge-base/ontology/parser.py query --type task
      -> scoutctl kb query --kb-root <this KB> --schema <this schema> --type task

Commands: validate, export, query, entity, related, stats, name_lookup,
traverse, path (`name_lookup` runs `scoutctl kb name-lookup`). Output is what
this parser always printed, plus labels: a `[private] ` marker before each
private result in text, and a `_label` key on each entity in JSON.

`--reader` (owner | autonomous | agent:...) is passed through and can only
narrow the reader `SCOUT_READER` / `SCOUT_MODE` set, as is `--lookup-type`
(name_lookup: person | organization | any | a comma-separated list);
`--rel-types` is another spelling of `--rels`. `scoutctl` is the one on PATH,
else this vault's `scripts/scoutctl`. With neither, this exits non-zero
without reading the knowledge base: there is no unlabelled fallback.

Python callers: `from knowledge_base.ontology.parser import KnowledgeGraph`
still works, with the old constructor `KnowledgeGraph(schema_path=..., kb_root=...)`
and methods (load, entity, query, related, export_json, validate, name_lookup,
traverse, path, to_networkx). It is the engine's `scout.kb.ontology.KnowledgeGraph`
for the ambient reader (`SCOUT_READER`; else `autonomous` when `SCOUT_MODE` is
set; else `owner`), so each entity carries `_label` and an agent reader gets an
empty graph. The class is looked up only when a caller asks for it: importing
this file imports only the standard library and reads nothing, and the command
line never needs the engine importable. Without the engine, asking for
`KnowledgeGraph` raises ImportError; use `scoutctl kb ... --json` instead.
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import sys
from typing import Any

_COMPAT: dict[str, Any] = {}


def _knowledge_graph_class() -> Any:
    """The engine-backed `KnowledgeGraph` with the pre-shim constructor (built once)."""
    if "KnowledgeGraph" in _COMPAT:
        return _COMPAT["KnowledgeGraph"]
    try:
        ontology = importlib.import_module("scout.kb.ontology")
        reader_mod = importlib.import_module("scout.kb.reader")
    except ImportError as exc:
        raise ImportError(
            "knowledge_base.ontology.parser.KnowledgeGraph needs the Scout engine (the `scout` package), "
            "which isn't importable here. The knowledge graph runs in the engine and labels private notes; "
            "use `scoutctl kb query|entity|related|export ... --json` instead, or run this script with the "
            "engine's Python."
        ) from exc

    class KnowledgeGraph(ontology.KnowledgeGraph):  # type: ignore[misc, name-defined]
        """The pre-shim parser's class, served by the engine: same methods, plus `_label`."""

        def __init__(self, schema_path: str, kb_root: str) -> None:
            reader = reader_mod.resolve_reader(None, os.environ)
            super().__init__(schema_path, kb_root, reader=reader)

        def to_networkx(self) -> Any:
            """A networkx.MultiDiGraph of the loaded (labelled) entities; needs networkx."""
            try:
                import networkx as nx
            except ImportError as exc:
                raise RuntimeError(
                    "networkx not installed — traverse()/path() work without it; "
                    "only to_networkx() needs `pip install networkx`."
                ) from exc
            g = nx.MultiDiGraph()
            for name, ent in self.entities.items():
                g.add_node(name, type=ent.get("type"))
            for r in self.relationships:
                g.add_edge(r["source"], r["target"], type=r["type"])
            return g

    KnowledgeGraph.__module__ = __name__
    _COMPAT["KnowledgeGraph"] = KnowledgeGraph
    return KnowledgeGraph


def __getattr__(name: str) -> Any:
    """`KnowledgeGraph` on first use (PEP 562), so importing this file needs no engine."""
    if name == "KnowledgeGraph":
        return _knowledge_graph_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

COMMANDS = ["validate", "export", "query", "entity", "related", "stats", "name_lookup", "traverse", "path"]
SUBCOMMAND = {"name_lookup": "name-lookup"}


def build_parser() -> argparse.ArgumentParser:
    default_schema = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.yaml")
    default_kb = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    parser = argparse.ArgumentParser(description="SCOUT Knowledge Graph Parser (runs scoutctl kb)")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--name", help="Entity name (for entity/related/traverse, and the source for path)")
    parser.add_argument("--to", dest="to", help="Target entity name (for the path command)")
    parser.add_argument("--hops", type=int, help="Max hops (traverse: default 2; path: default 6)")
    parser.add_argument("--rels", "--rel-types", dest="rels", help="Comma-separated relationship types to restrict a traverse (e.g. 'works_with,works_on')")
    parser.add_argument("--token", help="Name token to resolve against people/aliases (name_lookup)")
    parser.add_argument("--lookup-type", dest="lookup_type", help="name_lookup: entity type(s) to search — person (default), organization, any, or a comma-separated list")
    parser.add_argument("--type", help="Entity type filter (for query command)")
    parser.add_argument("--status", help="Status filter (e.g. 'open', 'completed') — query command")
    parser.add_argument("--exclude-status", dest="exclude_status", help="Comma-separated statuses to exclude (e.g. 'completed,cancelled') — query command. Use this for 'currently-actionable' tasks since entities use mixed statuses (open / in-progress / scheduled / in_service / ...).")
    parser.add_argument("--domain", help="Domain filter (e.g. 'personal', 'work') — query command")
    parser.add_argument("--deadline-before", dest="deadline_before", help="ISO date — return tasks with deadline <= this date (query command)")
    parser.add_argument("--threshold", type=float, default=0.7, help="Fuzzy match cutoff for name_lookup (0.0–1.0; default 0.7 ≈ Levenshtein-2)")
    parser.add_argument("--schema", default=default_schema, help="Path to schema.yaml")
    parser.add_argument("--kb-root", default=default_kb, help="Path to knowledge-base root")
    parser.add_argument("--reader", help="owner | autonomous | agent:person:<email> | agent:group:<id> (can only narrow)")
    return parser


def scoutctl_argv(args: argparse.Namespace) -> list[str] | None:
    """The `scoutctl kb ...` arguments for `args`, or None after printing the
    parser's own message for a missing argument (exit 0, as it always did)."""
    cmd = args.command
    out = ["kb", SUBCOMMAND.get(cmd, cmd), "--kb-root", args.kb_root, "--schema", args.schema]

    def flag(name: str, value: object) -> None:
        if value:  # empty values were always ignored
            out.extend([name, str(value)])

    if cmd in ("entity", "related", "traverse"):
        if not args.name:
            print(f"Error: --name required for {cmd} command")
            return None
        flag("--name", args.name)
        if cmd == "traverse":
            flag("--hops", args.hops)
            flag("--rels", args.rels)
    elif cmd == "path":
        if not args.name or not args.to:
            print("Error: --name (source) and --to (target) required for path command")
            return None
        flag("--name", args.name)
        flag("--to", args.to)
        flag("--hops", args.hops)
    elif cmd == "name_lookup":
        if not args.token:
            print("Error: --token required for name_lookup command")
            return None
        flag("--token", args.token)
        out.extend(["--threshold", str(args.threshold)])
        flag("--lookup-type", args.lookup_type)
    elif cmd == "query":
        flag("--type", args.type)
        flag("--status", args.status)
        flag("--exclude-status", args.exclude_status)
        flag("--domain", args.domain)
        flag("--deadline-before", args.deadline_before)
    flag("--reader", args.reader)
    return out


def find_scoutctl() -> str | None:
    """`scoutctl` on PATH, else this vault's `scripts/scoutctl` (<vault>/knowledge-base/ontology/parser.py)."""
    found = shutil.which("scoutctl")
    if found:
        return found
    vault = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    local = os.path.join(vault, "scripts", "scoutctl")
    return local if os.path.isfile(local) and os.access(local, os.X_OK) else None


def main() -> int:
    args = build_parser().parse_args()
    argv = scoutctl_argv(args)
    if argv is None:
        return 0
    scoutctl = find_scoutctl()
    if scoutctl is None:
        print(
            "parser.py: scoutctl not found on PATH or in this vault's scripts/. The knowledge graph "
            "runs in the Scout engine (scoutctl kb), which labels private notes; this parser won't "
            "read the knowledge base without it. Put scoutctl on PATH (see /scout-status), then retry.",
            file=sys.stderr,
        )
        return 127
    sys.stdout.flush()
    os.execvp(scoutctl, [scoutctl, *argv])
    return 1  # not reached


if __name__ == "__main__":
    sys.exit(main())
