"""``plugin/scripts/eval.sh``: the eval harness's command line (spec 2026-10-12 §9).

A maintainer tool, not a ``scoutctl`` subcommand: it needs the repo checkout,
the stubs, and (for model-backed commands) a logged-in Claude Code.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from .build import EVAL_ROOT, build_vault

SCENARIOS_DIR = EVAL_ROOT / "scenarios"
REPO_ROOT = EVAL_ROOT.parents[3]


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


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _cmd_run(args: argparse.Namespace) -> int:
    from .plan import PlanRefused, session_plan
    from .report import summarize
    from .runner import LoginRequired, run_sample
    from .scenario import load_scenario

    today = date.fromisoformat(args.today) if args.today else _utc_today()
    try:
        scenarios = [load_scenario(n) for n in args.scenarios]
        plan = session_plan(scenarios, reps=args.reps, opus=args.opus, many=args.many)
    except (FileNotFoundError, PlanRefused) as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    weekday_only = [s.name for s in scenarios if s.weekdays_only]
    if today.weekday() >= 5 and weekday_only and not args.allow_weekend:
        print(
            f"eval: {', '.join(weekday_only)} needs a weekday (working-day dates); "
            "today is a weekend. Pass --allow-weekend to run anyway.",
            file=sys.stderr,
        )
        return 2
    real_claude = args.claude or shutil.which("claude")
    if not real_claude:
        print("eval: no claude on PATH (or pass --claude PATH)", file=sys.stderr)
        return 2
    print(plan.describe())
    if not args.yes:
        input("Press Enter to start, Ctrl-C to cancel: ")
    results_root = Path(args.results).resolve()
    results = []
    try:
        for scenario in plan.scenarios:
            for rep in range(1, plan.reps + 1):
                print(f"… {scenario.name} sample {rep}/{plan.reps}", flush=True)
                results.append(
                    run_sample(
                        scenario,
                        today=today,
                        rep=rep,
                        model=plan.model,
                        results_root=results_root,
                        real_claude=real_claude,
                    )
                )
    except LoginRequired as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    print(summarize(results))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval.sh", description="Scout agent-behaviour eval harness")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build a fixture vault you can open (starts no session)")
    build.add_argument("dest", help="an empty or missing directory")
    build.add_argument("--scenario", help="overlay a scenario from tests/eval/scenarios/")
    build.add_argument("--today", help="render dates as if today were YYYY-MM-DD")
    build.set_defaults(func=_cmd_build)
    run = sub.add_parser("run", help="run scenarios as real sessions on your Claude login (prints the plan first)")
    run.add_argument("scenarios", nargs="+", help="scenario names from tests/eval/scenarios/")
    run.add_argument("--reps", type=int, default=3, help="samples per scenario (default 3)")
    run.add_argument("--opus", action="store_true", help="use Opus instead of the default Sonnet")
    run.add_argument("--many", action="store_true", help="allow more than 10 sessions")
    run.add_argument("--yes", action="store_true", help="don't wait for Enter after printing the plan")
    run.add_argument("--allow-weekend", action="store_true", help="run weekday-only scenarios on a weekend")
    run.add_argument("--today", help="render fixture dates as if today were YYYY-MM-DD (default: today, UTC)")
    run.add_argument("--results", default=str(REPO_ROOT / ".eval-results"), help="where result dirs go")
    run.add_argument("--claude", help=argparse.SUPPRESS)  # a stand-in claude binary, for tests
    run.set_defaults(func=_cmd_run)
    args = parser.parse_args(argv)
    return int(args.func(args))
