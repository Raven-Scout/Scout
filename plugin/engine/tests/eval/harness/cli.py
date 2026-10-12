"""``plugin/scripts/eval.sh``: the eval harness's command line (spec 2026-10-12 §9).

A maintainer tool, not a ``scoutctl`` subcommand: it needs the repo checkout,
the stubs, and (for model-backed commands) a logged-in Claude Code.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .build import EVAL_ROOT, build_vault

SCENARIOS_DIR = EVAL_ROOT / "scenarios"


def _scenario_dir(name: str | None) -> Path | None:
    if name is None:
        return None
    path = SCENARIOS_DIR / name
    if not path.is_dir():
        raise ValueError(f"unknown scenario {name!r} (looked in {SCENARIOS_DIR})")
    return path


def _cmd_build(args: argparse.Namespace) -> int:
    today = date.fromisoformat(args.today) if args.today else date.today()
    try:
        scenario = _scenario_dir(args.scenario)
    except ValueError as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    print(build_vault(Path(args.dest).resolve(), today=today, scenario_dir=scenario))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval.sh", description="Scout agent-behaviour eval harness")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build a fixture vault you can open (starts no session)")
    build.add_argument("dest", help="an empty or missing directory")
    build.add_argument("--scenario", help="overlay a scenario from tests/eval/scenarios/")
    build.add_argument("--today", help="render dates as if today were YYYY-MM-DD")
    build.set_defaults(func=_cmd_build)
    args = parser.parse_args(argv)
    return int(args.func(args))
