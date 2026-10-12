"""``plugin/scripts/eval.sh``: the eval harness's command line (spec 2026-10-12 §9).

A maintainer tool, not a ``scoutctl`` subcommand: it needs the repo checkout,
the stubs, and (for model-backed commands) a logged-in Claude Code.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
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


CACHE_STALE_DAYS = 14


def _cli_version(claude: str) -> str:
    done = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=60)
    return done.stdout.strip().splitlines()[0] if done.stdout.strip() else "unknown"


def _age_days(sample: object) -> int | None:
    started = getattr(sample, "meta", {}).get("started_at")
    try:
        return (datetime.now(UTC) - datetime.strptime(started, "%Y-%m-%dT%H:%M:%S%z")).days
    except (TypeError, ValueError):
        return None


def _cmd_compare(args: argparse.Namespace) -> int:
    from .cache import DEFAULT_CACHE_ROOT, BaselineCache, cache_key
    from .checkout import CheckoutError, base_checkout, head_checkout
    from .plan import DEFAULT_MODEL, MAX_WITHOUT_MANY
    from .report import format_comparison
    from .runner import LoginRequired, run_sample
    from .scenario import load_scenario
    from .verdicts import compare_arms

    today = date.fromisoformat(args.today) if args.today else _utc_today()
    model = "opus" if args.opus else DEFAULT_MODEL
    real_claude = args.claude or shutil.which("claude")
    if not real_claude:
        print("eval: no claude on PATH (or pass --claude PATH)", file=sys.stderr)
        return 2
    cache_root = Path(args.cache).resolve() if args.cache else DEFAULT_CACHE_ROOT
    try:
        scenarios = [load_scenario(n) for n in args.scenarios]
        base_co = base_checkout(args.base, cache_root)
    except (FileNotFoundError, CheckoutError) as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    if args.reps < 1:
        print("eval: --reps must be at least 1", file=sys.stderr)
        return 2
    if today.weekday() >= 5 and any(s.weekdays_only for s in scenarios) and not args.allow_weekend:
        print("eval: a weekday-only scenario on a weekend; pass --allow-weekend to run anyway", file=sys.stderr)
        return 2
    cli_version = args.cli_version or _cli_version(real_claude)
    cache = BaselineCache(cache_root)
    head_co = head_checkout()
    jobs = []
    for s in scenarios:
        key = cache_key(base_co, s, model=model, cli_version=cli_version, weekday=today.weekday())
        cached = [] if args.fresh_base else cache.samples(key)[: args.reps]
        jobs.append((s, key, cached, args.reps - len(cached)))
    sessions = sum(args.reps + missing for _s, _k, _c, missing in jobs)
    if sessions > MAX_WITHOUT_MANY and not args.many:
        print(f"eval: {sessions} sessions is more than {MAX_WITHOUT_MANY}; pass --many", file=sys.stderr)
        return 2
    parts = []
    for s, _k, _c, missing in jobs:
        parts.append(f"head {s.name} ×{args.reps}")
        if missing:
            parts.append(f"base {s.name} ×{missing}")
    noun = "session" if sessions == 1 else "sessions"
    print(f"Will start {sessions} {noun} ({', '.join(parts)}) on {model} via your Claude login.")
    print(f"Base: {args.base} ({(base_co.commit or '')[:12]}) at {base_co.root}")
    for s, _k, cached, _m in jobs:
        if cached:
            ages = [a for a in (_age_days(c) for c in cached) if a is not None]
            oldest = f", oldest {max(ages)} days" if ages else ""
            stale = "  (stale: rerun with --fresh-base)" if ages and max(ages) > CACHE_STALE_DAYS else ""
            noun = "sample" if len(cached) == 1 else "samples"
            print(f"{s.name}: {len(cached)} cached base {noun}{oldest}{stale}")
    if not args.yes:
        input("Press Enter to start, Ctrl-C to cancel: ")
    results_root = Path(args.results).resolve()
    common = {
        "today": today,
        "model": model,
        "results_root": results_root,
        "real_claude": real_claude,
        "cli_version": cli_version,
    }
    regressions = False
    try:
        for s, key, cached, missing in jobs:
            base_samples, head_samples = list(cached), []
            replaced = False
            for i in range(max(args.reps, missing)):  # interleaved: base, head, base, head…
                runs = []
                if i < missing:
                    runs.append(("base", base_co, f"{s.name} base sample {i + 1}/{missing}"))
                if i < args.reps:
                    runs.append(("head", head_co, f"{s.name} head sample {i + 1}/{args.reps}"))
                for arm, checkout, label in runs:
                    print(f"… {label}", flush=True)
                    sample = run_sample(s, rep=i + 1, arm=arm, checkout=checkout, **common)
                    (base_samples if arm == "base" else head_samples).append(sample)
                    if sample.aborted:
                        # The verdict is already blocked: don't spend more of the subscription.
                        left = (missing - len(base_samples) + len(cached)) + (args.reps - len(head_samples))
                        print(f"\n== {s.name}: stopped after an aborted sample ({sample.aborted.get('reason')}); "
                              f"{left} sample(s) never ran ==")  # fmt: skip
                        print(format_comparison(compare_arms(base_samples, head_samples), base_samples, head_samples))
                        return 2
                    if arm == "base":
                        if args.fresh_base and not replaced:  # replace, only once a fresh sample exists
                            cache.clear(key)
                            replaced = True
                        cache.add(key, sample.result_dir)
            cmp = compare_arms(base_samples, head_samples)
            print(f"\n== {s.name}: base {args.base} vs head (working tree) ==")
            print(format_comparison(cmp, base_samples, head_samples))
            if cmp.blocked and cmp.blocked.startswith("mixed models"):
                print("The cached base ran another model: rerun with --fresh-base to replace it.")
            regressions = regressions or bool(cmp.regressions)
    except LoginRequired as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    return 1 if regressions else 0


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
    cmp = sub.add_parser("compare", help="score the working tree against a base ref (reuses cached base samples)")
    cmp.add_argument("scenarios", nargs="+", help="scenario names from tests/eval/scenarios/")
    cmp.add_argument("--base", default="origin/main", help="the ref to compare against (default origin/main)")
    cmp.add_argument("--reps", type=int, default=3, help="samples per arm (default 3)")
    cmp.add_argument("--fresh-base", action="store_true", help="ignore cached base samples; interleave both arms")
    cmp.add_argument("--opus", action="store_true", help="use Opus instead of the default Sonnet")
    cmp.add_argument("--many", action="store_true", help="allow more than 10 sessions")
    cmp.add_argument("--yes", action="store_true", help="don't wait for Enter after printing the plan")
    cmp.add_argument("--allow-weekend", action="store_true", help="run weekday-only scenarios on a weekend")
    cmp.add_argument("--today", help="render fixture dates as if today were YYYY-MM-DD (default: today, UTC)")
    cmp.add_argument("--results", default=str(REPO_ROOT / ".eval-results"), help="where result dirs go")
    cmp.add_argument("--cache", help="cache root (default ~/.cache/scout-eval)")
    cmp.add_argument("--claude", help=argparse.SUPPRESS)  # a stand-in claude binary, for tests
    cmp.add_argument("--cli-version", help=argparse.SUPPRESS)  # skip `claude --version`, for tests
    cmp.set_defaults(func=_cmd_compare)
    args = parser.parse_args(argv)
    return int(args.func(args))
