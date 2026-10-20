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
narrow the reader `SCOUT_READER` / `SCOUT_MODE` set. If `scoutctl` isn't on
PATH this exits non-zero without reading the knowledge base: there is no
unlabelled fallback.

This file imports only the standard library. Python callers should use
`scout.kb.ontology.KnowledgeGraph`, or `scoutctl kb ... --json`.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

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
    parser.add_argument("--rels", help="Comma-separated relationship types to restrict a traverse (e.g. 'works_with,works_on')")
    parser.add_argument("--token", help="Name token to resolve against people/aliases (name_lookup)")
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
    elif cmd == "query":
        flag("--type", args.type)
        flag("--status", args.status)
        flag("--exclude-status", args.exclude_status)
        flag("--domain", args.domain)
        flag("--deadline-before", args.deadline_before)
    flag("--reader", args.reader)
    return out


def main() -> int:
    args = build_parser().parse_args()
    argv = scoutctl_argv(args)
    if argv is None:
        return 0
    scoutctl = shutil.which("scoutctl")
    if scoutctl is None:
        print(
            "parser.py: scoutctl not found on PATH. The knowledge graph runs in the Scout engine "
            "(scoutctl kb), which labels private notes; this parser won't read the knowledge base "
            "without it. Put scoutctl on PATH (see /scout-status), then retry.",
            file=sys.stderr,
        )
        return 127
    sys.stdout.flush()
    os.execvp(scoutctl, [scoutctl, *argv])
    return 1  # not reached


if __name__ == "__main__":
    sys.exit(main())
