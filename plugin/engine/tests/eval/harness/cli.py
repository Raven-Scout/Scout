"""``plugin/scripts/eval.sh``: the eval harness's command line (spec 2026-10-12 §9).

A maintainer tool, not a ``scoutctl`` subcommand: it needs the repo checkout,
the stubs, and (for model-backed commands) a logged-in Claude Code.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .build import EVAL_ROOT, build_vault

if TYPE_CHECKING:
    from .scenario import Scenario

SCENARIOS_DIR = EVAL_ROOT / "scenarios"
REPO_ROOT = EVAL_ROOT.parents[3]


def _scenario_dir(name: str | None) -> Path | None:
    if name is None:
        return None
    path = SCENARIOS_DIR / name
    if not path.is_dir():
        raise ValueError(f"unknown scenario {name!r} (looked in {SCENARIOS_DIR})")
    from .scenario import load_scenario

    try:
        return load_scenario(name).files  # a variant's overlays live in its files_from scenario
    except (FileNotFoundError, KeyError):
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


def _kb_scenario(args: argparse.Namespace) -> Scenario:
    """``--scenario`` with ``--max-growth`` applied. Raises ValueError or FileNotFoundError."""
    import dataclasses

    from .checks.kb_writing import KB_CHECKS
    from .scenario import load_scenario

    scenario = load_scenario(args.scenario)
    if not set(KB_CHECKS) & set(scenario.checks):
        raise ValueError(f"scenario {scenario.name!r} has no KB checks")
    if args.max_growth is not None:
        if args.max_growth < 0:
            raise ValueError("--max-growth must be 0 or more bytes")
        scenario = dataclasses.replace(scenario, max_front_page_growth=args.max_growth)
    return scenario


def _write_report(report: dict[str, Any], paths: list[Path]) -> None:
    import json

    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text, end="")


def _cmd_kb_gate(args: argparse.Namespace) -> int:
    """The KB-writing release gate: ONE consolidation session, scored, with a JSON report.

    Exit 0 pass, 1 fail, 2 when it couldn't decide (no login, an aborted session, an unscored check),
    3 for a void run (the session finished but did nothing; the report lists its denied calls).
    """
    from .checks.kb_writing import KB_CHECKS
    from .kb_gate import (
        REPORT_NAME,
        checkout_state,
        commits_since_fixture,
        exit_code,
        gate_report,
        pages_changed,
        spec_for,
        void_run,
    )
    from .plan import DEFAULT_MODEL
    from .runner import LoginRequired, run_sample

    today = date.fromisoformat(args.today) if args.today else _utc_today()
    try:
        scenario = _kb_scenario(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    real_claude = args.claude or shutil.which("claude")
    if not real_claude:
        print("eval: no claude on PATH (or pass --claude PATH)", file=sys.stderr)
        return 2
    model = "opus" if args.opus else DEFAULT_MODEL
    print(
        f"Will start 1 session ({scenario.name} ×1, {scenario.mode}) on {model} via your Claude login: "
        f"the KB-writing release gate (front page may grow {scenario.max_front_page_growth} bytes).",
        file=sys.stderr,
    )
    if not args.yes:
        print("Press Enter to start, Ctrl-C to cancel: ", end="", file=sys.stderr, flush=True)
        input()
    cli_version = args.cli_version or _cli_version(real_claude)
    checkout = checkout_state(REPO_ROOT)  # before the run: what code it tests
    try:
        sample = run_sample(
            scenario, today=today, rep=1, model=model, results_root=Path(args.results).resolve(),
            real_claude=real_claude, arm="kb-gate", cli_version=cli_version,
        )  # fmt: skip
    except LoginRequired as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    checks = [c for c in sample.checks if c.name in KB_CHECKS]
    meta = {k: sample.meta.get(k) for k in ("model", "resolved_model", "cli_version", "today", "started_at")}
    vault, spec = sample.result_dir / "vault", spec_for(scenario)
    void = None if sample.aborted else void_run(
        sample.metrics, commits_since_fixture(vault), pages_changed(vault, spec.project)
    )  # fmt: skip
    report = gate_report(
        vault, spec, checks, scenario=scenario.name, void=void,
        sample={"result_dir": str(sample.result_dir), **meta, "checkout": checkout, "aborted": sample.aborted},
    )  # fmt: skip
    if void:
        denied = "; ".join(f"{d['tool']} ({d['by']}): {str(d['input'])[:120]}" for d in void["denials"])
        print(
            f"eval: VOID run, not a verdict on the release: {void['reason']}. Denied calls: {denied}. Rerun the gate.",
            file=sys.stderr,
        )
    _write_report(report, [sample.result_dir / REPORT_NAME] + ([Path(args.report)] if args.report else []))
    return exit_code(report)


def _cmd_kb_score(args: argparse.Namespace) -> int:
    """Score a vault that already exists (a kept result dir's ``vault/``, say). Starts no session."""
    from .kb_gate import exit_code, gate_report, score_kb, spec_for

    vault = Path(args.vault).resolve()
    if not (vault / ".git").is_dir():
        print(f"eval: {vault} is not a vault built by the harness (no .git)", file=sys.stderr)
        return 2
    try:
        scenario = _kb_scenario(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    spec = spec_for(scenario)
    report = gate_report(vault, spec, score_kb(vault, spec), scenario=scenario.name)
    _write_report(report, [Path(args.report)] if args.report else [])
    return exit_code(report)


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


def _cmd_compare_arms(args: argparse.Namespace) -> int:
    """Run multiple KB backend arms (e.g. a,b,c) interleaved on HEAD and score against the first arm."""
    from .arms import parse_arms
    from .checkout import head_checkout
    from .metrics import verb_uptake_rate
    from .plan import DEFAULT_MODEL, MAX_WITHOUT_MANY
    from .report import format_comparison
    from .runner import LoginRequired, run_sample
    from .scenario import load_scenario
    from .verdicts import compare_arms, hit_limit

    try:
        arm_specs = parse_arms(args.arms)
    except ValueError as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    today = date.fromisoformat(args.today) if args.today else _utc_today()
    model = "opus" if args.opus else DEFAULT_MODEL
    real_claude = args.claude or shutil.which("claude")
    if not real_claude:
        print("eval: no claude on PATH (or pass --claude PATH)", file=sys.stderr)
        return 2
    try:
        scenarios = [load_scenario(n) for n in args.scenarios]
    except FileNotFoundError as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    if args.reps < 1:
        print("eval: --reps must be at least 1", file=sys.stderr)
        return 2
    if today.weekday() >= 5 and any(s.weekdays_only for s in scenarios) and not args.allow_weekend:
        print("eval: a weekday-only scenario on a weekend; pass --allow-weekend to run anyway", file=sys.stderr)
        return 2
    sessions = len(arm_specs) * args.reps * len(scenarios)
    if sessions > MAX_WITHOUT_MANY and not args.many:
        print(f"eval: {sessions} sessions is more than {MAX_WITHOUT_MANY}; pass --many", file=sys.stderr)
        return 2
    cli_version = args.cli_version or _cli_version(real_claude)
    head_co = head_checkout()
    if head_co.pythonpath is None and not (REPO_ROOT / "plugin" / ".venv").is_dir():
        import dataclasses

        head_co = dataclasses.replace(head_co, pythonpath=REPO_ROOT / "plugin" / "engine")
    arm_desc = ", ".join(f"{a.id} ({a.slug}) ×{args.reps}" for a in arm_specs)
    names = ", ".join(s.name for s in scenarios)
    print(f"Will start {sessions} sessions ({names}: {arm_desc}, interleaved) on {model} via your Claude login.")
    print(f"Reference arm: {arm_specs[0].id} ({arm_specs[0].slug}).")
    if not args.yes:
        input("Press Enter to start, Ctrl-C to cancel: ")

    results_root = Path(args.results).resolve()
    regressions = False
    try:
        for s in scenarios:
            samples_by_arm: dict[str, list] = {a.id: [] for a in arm_specs}
            for i in range(args.reps):
                for spec in arm_specs:
                    label = f"arm-{spec.id}"
                    print(f"… {s.name} {label} ({spec.slug}) sample {i + 1}/{args.reps}", flush=True)
                    sample = run_sample(
                        s,
                        today=today,
                        rep=i + 1,
                        model=model,
                        results_root=results_root,
                        real_claude=real_claude,
                        arm=label,
                        cli_version=cli_version,
                        checkout=head_co,
                        arm_spec=spec,
                        extra_pythonpath=args.extra_pythonpath,
                    )
                    samples_by_arm[spec.id].append(sample)
                    if sample.aborted and hit_limit(sample):
                        print(f"   hit a run limit ({sample.aborted.get('reason')}): left out of checks and metrics")
                        continue
                    if sample.aborted:
                        print(f"\n== {s.name}: stopped after an aborted sample ({sample.aborted.get('reason')}) ==")
                        return 2

            ref = arm_specs[0]
            uptake_parts = []
            for spec in arm_specs:
                scored_calls = [
                    smp.metrics.calls for smp in samples_by_arm[spec.id] if not smp.aborted and smp.metrics is not None
                ]
                rate = verb_uptake_rate(scored_calls)
                uptake_parts.append(f"{spec.id}={rate * 100:.0f}%")
            print(f"\n== {s.name}: graph verb uptake ({', '.join(uptake_parts)}) ==")

            for cand in arm_specs[1:]:
                cmp = compare_arms(samples_by_arm[ref.id], samples_by_arm[cand.id])
                print(f"\n== {s.name}: arm {cand.id} ({cand.slug}) vs arm {ref.id} ({ref.slug}) ==")
                print(
                    format_comparison(
                        cmp,
                        samples_by_arm[ref.id],
                        samples_by_arm[cand.id],
                        labels=(f"arm-{ref.id}", f"arm-{cand.id}"),
                    )
                )
                regressions = regressions or bool(cmp.regressions)

            if len(arm_specs) >= 3:
                second, third = arm_specs[1], arm_specs[2]
                cmp_bc = compare_arms(samples_by_arm[second.id], samples_by_arm[third.id])
                print(f"\n== {s.name}: arm {third.id} ({third.slug}) vs arm {second.id} ({second.slug}) ==")
                print(
                    format_comparison(
                        cmp_bc,
                        samples_by_arm[second.id],
                        samples_by_arm[third.id],
                        labels=(f"arm-{second.id}", f"arm-{third.id}"),
                    )
                )
    except LoginRequired as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    return 1 if regressions else 0


def _cmd_track_r_seed(args: argparse.Namespace) -> int:
    """Seed local-only candidate Track R items from a vault into an owner-only JSONL file."""
    import json

    from .track_r_golden import seed_candidates_from_vault, summarize_golden_set, write_golden_set

    vault = Path(args.vault).expanduser().resolve()
    dest = Path(args.out).expanduser().resolve()
    if not (vault / "knowledge-base").is_dir():
        print(f"eval: {vault} has no knowledge-base/ directory", file=sys.stderr)
        return 2
    try:
        items = seed_candidates_from_vault(vault, max_items=args.max_items)
        written = write_golden_set(dest, items)
    except (PermissionError, ValueError) as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    summary = summarize_golden_set(items)
    print(json.dumps({"written": str(written), **summary}, indent=2))
    return 0


def _cmd_track_r_run(args: argparse.Namespace) -> int:
    """Run read-only Track R Q&A items interleaved across KB backend arms and evaluate Gate G1."""
    import json

    from scout import safe_write
    from scout.errors import ScoutError

    from .plan import DEFAULT_MODEL, MAX_WITHOUT_MANY
    from .runner import LoginRequired
    from .track_r_golden import load_golden_set, refuse_public_repo_destination
    from .track_r_runner import format_track_r_summary, parse_track_r_arms, run_track_r

    try:
        arm_specs = parse_track_r_arms(args.arms)
    except ValueError as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    golden_path = Path(args.golden).expanduser().resolve()
    vault_repo = Path(args.vault).expanduser().resolve()
    results_root = Path(args.results).expanduser().resolve()
    if not golden_path.is_file():
        print(f"eval: golden file not found: {golden_path}", file=sys.stderr)
        return 2
    if not (vault_repo / "knowledge-base").is_dir():
        print(f"eval: {vault_repo} has no knowledge-base/ directory", file=sys.stderr)
        return 2
    try:
        refuse_public_repo_destination(results_root)
        if args.report:
            refuse_public_repo_destination(Path(args.report))
        preview = load_golden_set(golden_path)
    except (PermissionError, ValueError) as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2

    if args.only_audited:
        preview = [it for it in preview if it.audited]
    if args.category:
        cats = set(args.category)
        preview = [it for it in preview if it.category in cats]
    if args.limit is not None:
        preview = preview[: max(0, args.limit)]
    if not preview:
        print("eval: no Track R items matched the requested filters", file=sys.stderr)
        return 2

    real_claude = args.claude or shutil.which("claude")
    if not real_claude:
        print("eval: no claude on PATH (or pass --claude PATH)", file=sys.stderr)
        return 2
    model = "opus" if args.opus else DEFAULT_MODEL
    sessions = len(preview) * len(arm_specs)
    if sessions > MAX_WITHOUT_MANY and not args.many:
        print(f"eval: {sessions} sessions is more than {MAX_WITHOUT_MANY}; pass --many", file=sys.stderr)
        return 2

    arm_desc = ", ".join(f"{a.id} ({a.slug})" for a in arm_specs)
    print(
        f"Will start {sessions} read-only Track R Q&A sessions "
        f"({len(preview)} items × [{arm_desc}], interleaved) on {model} via your Claude login."
    )
    if not args.yes:
        input("Press Enter to start, Ctrl-C to cancel: ")

    cli_version = args.cli_version or _cli_version(real_claude)
    try:
        report = run_track_r(
            golden_path,
            vault_repo=vault_repo,
            arm_specs=arm_specs,
            results_root=results_root,
            real_claude=real_claude,
            model=model,
            budget=args.budget,
            timeout_s=args.timeout,
            reader=args.reader,
            limit=args.limit,
            categories=args.category,
            only_audited=args.only_audited,
            dolphin_check=args.dolphin_check,
            extra_pythonpath=args.extra_pythonpath,
            cli_version=cli_version,
        )
    except (LoginRequired, PermissionError, RuntimeError, ScoutError, ValueError) as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2

    if args.report:
        out_report = Path(args.report).expanduser().resolve()
        out_report.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(out_report.parent, 0o700)
        safe_write.atomic_write_text(
            out_report,
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            mode=0o600,
        )
    print("\n" + format_track_r_summary(report))
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    if getattr(args, "arms", None):
        return _cmd_compare_arms(args)
    from .cache import DEFAULT_CACHE_ROOT, BaselineCache, cache_key
    from .checkout import CheckoutError, base_checkout, head_checkout
    from .plan import DEFAULT_MODEL, MAX_WITHOUT_MANY
    from .report import format_comparison
    from .runner import LoginRequired, run_sample
    from .scenario import load_scenario
    from .verdicts import compare_arms, hit_limit

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
                    if sample.aborted and hit_limit(sample):
                        # Only this sample is out: the rest still score, and "within limits" counts it.
                        print(f"   hit a run limit ({sample.aborted.get('reason')}): left out of checks and metrics")
                        continue  # never cached as a base sample
                    if sample.aborted:
                        # A harness failure blocks the verdict: don't spend more of the subscription.
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


_MODEL_RE = re.compile(r"^(opus|sonnet|haiku|claude-[a-z0-9][a-z0-9.\-]*)$")


def _cmd_compare_models(args: argparse.Namespace) -> int:
    """Run the working tree on two models and score B against A (spec 2026-10-13 slot models §8).

    Answers "can this slot run on a cheaper model?": A is the reference (default
    opus), B the candidate (default sonnet). A check that is stable on A and
    misses twice on B is a regression; the exit code is 1 when B regresses.
    """
    from .plan import MAX_WITHOUT_MANY
    from .report import format_comparison
    from .runner import LoginRequired, run_sample
    from .scenario import load_scenario
    from .verdicts import compare_arms, hit_limit

    model_a, model_b = args.a.strip().lower(), args.b.strip().lower()
    for model in (model_a, model_b):
        if not _MODEL_RE.match(model):
            print(f"eval: model {model!r} is not opus, sonnet, haiku or a full claude-… ID", file=sys.stderr)
            return 2
    if model_a == model_b:
        print(f"eval: --a and --b are both {model_a}; compare-models needs two different models", file=sys.stderr)
        return 2
    today = date.fromisoformat(args.today) if args.today else _utc_today()
    real_claude = args.claude or shutil.which("claude")
    if not real_claude:
        print("eval: no claude on PATH (or pass --claude PATH)", file=sys.stderr)
        return 2
    try:
        scenarios = [load_scenario(n) for n in args.scenarios]
    except FileNotFoundError as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    if args.reps < 1:
        print("eval: --reps must be at least 1", file=sys.stderr)
        return 2
    if today.weekday() >= 5 and any(s.weekdays_only for s in scenarios) and not args.allow_weekend:
        print("eval: a weekday-only scenario on a weekend; pass --allow-weekend to run anyway", file=sys.stderr)
        return 2
    sessions = 2 * args.reps * len(scenarios)
    if sessions > MAX_WITHOUT_MANY and not args.many:
        print(f"eval: {sessions} sessions is more than {MAX_WITHOUT_MANY}; pass --many", file=sys.stderr)
        return 2
    cli_version = args.cli_version or _cli_version(real_claude)
    names = ", ".join(s.name for s in scenarios)
    print(
        f"Will start {sessions} sessions ({names}: {model_a} ×{args.reps} and {model_b} ×{args.reps}, "
        "interleaved) via your Claude login."
    )
    print(f"Reference: {model_a}. Candidate: {model_b}. A regression means {model_b} did worse.")
    if not args.yes:
        input("Press Enter to start, Ctrl-C to cancel: ")
    results_root = Path(args.results).resolve()
    regressions = False
    try:
        for s in scenarios:
            arms: dict[str, list] = {model_a: [], model_b: []}
            for i in range(args.reps):  # interleaved: a, b, a, b… so drift hits both arms alike
                for model in (model_a, model_b):
                    print(f"… {s.name} {model} sample {i + 1}/{args.reps}", flush=True)
                    sample = run_sample(
                        s, today=today, rep=i + 1, model=model, results_root=results_root,
                        real_claude=real_claude, arm=model, cli_version=cli_version,
                    )  # fmt: skip
                    arms[model].append(sample)
                    if sample.aborted and hit_limit(sample):
                        print(f"   hit a run limit ({sample.aborted.get('reason')}): left out of checks and metrics")
                        continue
                    if sample.aborted:
                        print(f"\n== {s.name}: stopped after an aborted sample ({sample.aborted.get('reason')}) ==")
                        cmp = compare_arms(arms[model_a], arms[model_b], across_models=True)
                        print(format_comparison(cmp, arms[model_a], arms[model_b], labels=(model_a, model_b)))
                        return 2
            cmp = compare_arms(arms[model_a], arms[model_b], across_models=True)
            print(f"\n== {s.name}: {model_b} vs {model_a} (working tree) ==")
            print(format_comparison(cmp, arms[model_a], arms[model_b], labels=(model_a, model_b)))
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
    cmp.add_argument(
        "--arms",
        help="comma-separated KB backend arms to compare on HEAD instead of base vs head (e.g. a,b,c)",
    )
    cmp.add_argument("--reps", type=int, default=3, help="samples per arm (default 3)")
    cmp.add_argument("--fresh-base", action="store_true", help="ignore cached base samples; interleave both arms")
    cmp.add_argument("--opus", action="store_true", help="use Opus instead of the default Sonnet")
    cmp.add_argument("--many", action="store_true", help="allow more than 10 sessions")
    cmp.add_argument("--yes", action="store_true", help="don't wait for Enter after printing the plan")
    cmp.add_argument("--allow-weekend", action="store_true", help="run weekday-only scenarios on a weekend")
    cmp.add_argument("--today", help="render fixture dates as if today were YYYY-MM-DD (default: today, UTC)")
    cmp.add_argument("--results", default=str(REPO_ROOT / ".eval-results"), help="where result dirs go")
    cmp.add_argument("--cache", help="cache root (default ~/.cache/scout-eval)")
    cmp.add_argument("--extra-pythonpath", help=argparse.SUPPRESS)
    cmp.add_argument("--claude", help=argparse.SUPPRESS)  # a stand-in claude binary, for tests
    cmp.add_argument("--cli-version", help=argparse.SUPPRESS)  # skip `claude --version`, for tests
    cmp.set_defaults(func=_cmd_compare)
    cm = sub.add_parser(
        "compare-models",
        help="score a candidate model (--b) against a reference (--a) on the working tree",
    )
    cm.add_argument("scenarios", nargs="+", help="scenario names from tests/eval/scenarios/")
    cm.add_argument("--a", default="opus", help="the reference model (default opus)")
    cm.add_argument("--b", default="sonnet", help="the candidate model (default sonnet)")
    cm.add_argument("--reps", type=int, default=3, help="samples per model (default 3)")
    cm.add_argument("--many", action="store_true", help="allow more than 10 sessions")
    cm.add_argument("--yes", action="store_true", help="don't wait for Enter after printing the plan")
    cm.add_argument("--allow-weekend", action="store_true", help="run weekday-only scenarios on a weekend")
    cm.add_argument("--today", help="render fixture dates as if today were YYYY-MM-DD (default: today, UTC)")
    cm.add_argument("--results", default=str(REPO_ROOT / ".eval-results"), help="where result dirs go")
    cm.add_argument("--claude", help=argparse.SUPPRESS)  # a stand-in claude binary, for tests
    cm.add_argument("--cli-version", help=argparse.SUPPRESS)  # skip `claude --version`, for tests
    cm.set_defaults(func=_cmd_compare_models)
    gate = sub.add_parser(
        "kb-gate",
        help="the KB-writing release gate: one consolidation session on your Claude login, scored, JSON report",
    )
    gate.add_argument("--scenario", default="kb-front-page", help="a scenario with KB checks (default kb-front-page)")
    gate.add_argument("--max-growth", type=int, help="bytes the front page may grow (default: the scenario's)")
    gate.add_argument("--report", help="also write the JSON report here (it always goes in the result dir)")
    gate.add_argument("--opus", action="store_true", help="use Opus instead of the default Sonnet")
    gate.add_argument("--yes", action="store_true", help="don't wait for Enter after printing the plan")
    gate.add_argument("--today", help="render fixture dates as if today were YYYY-MM-DD (default: today, UTC)")
    gate.add_argument("--results", default=str(REPO_ROOT / ".eval-results"), help="where result dirs go")
    gate.add_argument("--claude", help=argparse.SUPPRESS)  # a stand-in claude binary, for tests
    gate.add_argument("--cli-version", help=argparse.SUPPRESS)  # skip `claude --version`, for tests
    gate.set_defaults(func=_cmd_kb_gate)
    score = sub.add_parser("kb-score", help="score an existing vault against the KB-writing gate (starts no session)")
    score.add_argument("vault", help="a vault the harness built, e.g. .eval-results/<dir>/vault")
    score.add_argument("--scenario", default="kb-front-page", help="a scenario with KB checks (default kb-front-page)")
    score.add_argument("--max-growth", type=int, help="bytes the front page may grow (default: the scenario's)")
    score.add_argument("--report", help="also write the JSON report here")
    score.set_defaults(func=_cmd_kb_score)
    tr = sub.add_parser(
        "track-r-seed",
        help="mine candidate Track R read-eval items from a vault into a local owner-only JSONL file",
    )
    tr.add_argument("--vault", required=True, help="source Scout vault path")
    tr.add_argument("--out", required=True, help="local output JSONL path outside the public repo")
    tr.add_argument("--max-items", type=int, default=220, help="maximum candidate items to emit (default 220)")
    tr.set_defaults(func=_cmd_track_r_seed)
    tr_run = sub.add_parser(
        "track-r-run",
        help="run read-only Track R Q&A items interleaved across KB backend arms and evaluate Gate G1",
    )
    tr_run.add_argument("--golden", required=True, help="local Track R JSONL golden file")
    tr_run.add_argument("--vault", required=True, help="source Scout vault git repo")
    tr_run.add_argument("--arms", default="a,b,c", help="comma-separated KB arms (default a,b,c)")
    tr_run.add_argument(
        "--results",
        default=str(Path.home() / ".scout-worktrees" / "relatedb-eval-data" / "runs"),
        help="owner-only local results root outside any public repo",
    )
    tr_run.add_argument("--report", help="optional additional path for track-r-report.json (outside public repo)")
    tr_run.add_argument("--limit", type=int, help="run only the first N matching items")
    tr_run.add_argument("--category", action="append", help="filter to specific Track R category (repeatable)")
    tr_run.add_argument("--only-audited", action="store_true", help="run only items with audited=true")
    tr_run.add_argument("--dolphin-check", action="store_true", help="run DolphinBench gold-removal check on arm[0]")
    tr_run.add_argument("--reader", default="autonomous", help="Scout reader identity (default autonomous)")
    tr_run.add_argument("--budget", default="0.35", help="max USD budget per Q&A session (default 0.35)")
    tr_run.add_argument("--timeout", type=int, default=180, help="timeout seconds per Q&A session (default 180)")
    tr_run.add_argument("--opus", action="store_true", help="use Opus instead of the default Sonnet")
    tr_run.add_argument("--many", action="store_true", help="allow more than 10 sessions")
    tr_run.add_argument("--yes", action="store_true", help="don't wait for Enter after printing the plan")
    tr_run.add_argument("--extra-pythonpath", help="extra PYTHONPATH (e.g. relate-db site-packages)")
    tr_run.add_argument("--claude", help=argparse.SUPPRESS)
    tr_run.add_argument("--cli-version", help=argparse.SUPPRESS)
    tr_run.set_defaults(func=_cmd_track_r_run)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
