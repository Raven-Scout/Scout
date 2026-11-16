"""Base vs head: verdicts that are honest at small N (spec 2026-10-12 §8).

Pure: it takes the two arms' samples and returns rows. The rules:

- a check is **stable** on an arm at N/N and **flaky** at 0 < k < N;
- **regression**: base stable-pass and head at most N−2 passes. One head miss
  reads as ``flaky on head``: rerun. At N=5 the chance of two or more misses
  from a check that truly passes 95% of the time is about 2%;
- **improvement**: the mirror image (head stable-pass, base at most N−2), checked
  before noise: a mostly failing base under a stable head is an improvement;
- otherwise a flaky base is ``noisy`` and excluded from the verdict;
- a head that can't be evaluated more often than base (e.g. it stopped rewriting
  today's file) is ``regression (head not evaluated)``: behaviour, not noise;
- fewer than 2 evaluable samples on either arm: ``need N≥2``, never "no regression";
- a metric (lower is better) is worse only when head's median is above base's max;
- a sample that hit a run limit (the session timeout, the runner timeout or the
  budget cap) is left out of the checks and metrics, and only it: a slower or
  costlier run is behaviour, so ``within limits`` is scored like a check (one
  head miss is ``flaky on head``, two a regression);
- any other aborted sample (a harness failure), or arms that ran different models
  or CLI versions, block every verdict. A harness failure never reads as a regression.
- ``across_models`` (``eval.sh compare-models``) inverts the model rule: each
  arm must run one model, and the two arms must differ — if they ran the same
  model the switch did not take, and the comparison would say nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import median

from .report import METRICS
from .runner import SampleResult

# Abort reasons that are the run's own limits, not harness failures: the sample counts
# as "not within limits" and drops out of the checks and metrics, and the rest still score.
LIMIT_REASONS = frozenset({"timeout", "runner_timeout", "error_max_budget_usd"})
LIMITS_ROW = "within limits"


def hit_limit(sample: SampleResult) -> bool:
    return bool(sample.aborted) and sample.aborted.get("reason") in LIMIT_REASONS


@dataclass(frozen=True)
class CheckRow:
    name: str
    base_pass: int
    base_n: int
    head_pass: int
    head_n: int
    verdict: str
    unevaluated: int = 0


@dataclass(frozen=True)
class MetricRow:
    label: str
    base: tuple[float, float, float]  # median, min, max
    head: tuple[float, float, float]
    worse: bool


@dataclass(frozen=True)
class Comparison:
    checks: list[CheckRow]
    metrics: list[MetricRow]
    blocked: str | None
    regressions: list[str] = field(default_factory=list)


def _tally(samples: Sequence[SampleResult], name: str) -> tuple[int, int, int]:
    results = [c.passed for s in samples for c in s.checks if c.name == name]
    evaluable = [p for p in results if p is not None]
    return sum(1 for p in evaluable if p), len(evaluable), len(results) - len(evaluable)


def _verdict(bp: int, bn: int, hp: int, hn: int) -> str:
    if bn < 2 or hn < 2:
        return "need N≥2"
    if bp == bn and hp <= hn - 2:
        return "regression"
    if hp == hn and bp <= bn - 2:
        return "improvement"
    if bp == bn and hp == hn - 1:
        return "flaky on head"
    if 0 < bp < bn:
        return "noisy"
    return "same"


def _spread(values: list[float]) -> tuple[float, float, float]:
    return (median(values), min(values), max(values))


def _blocked(base: Sequence[SampleResult], head: Sequence[SampleResult], *, across_models: bool = False) -> str | None:
    b_ab, h_ab = (sum(1 for s in arm if s.aborted and not hit_limit(s)) for arm in (base, head))
    if b_ab or h_ab:
        return f"aborted samples (base {b_ab}, head {h_ab}): no verdict"
    if across_models:
        per_arm = [{str(s.meta.get("resolved_model")) for s in arm} for arm in (base, head)]
        for arm_models in per_arm:
            if len(arm_models) > 1:
                return f"mixed models within one arm {sorted(arm_models)}: no verdict"
        if per_arm[0] == per_arm[1]:
            return f"both arms ran {sorted(per_arm[0])}: no verdict (the model switch did not take)"
    else:
        models = {s.meta.get("resolved_model") for s in [*base, *head]}
        if len(models) > 1:
            return f"mixed models {sorted(str(m) for m in models)}: no verdict"
    clis = {s.meta.get("cli_version") for s in [*base, *head]}
    if len(clis) > 1:
        return f"mixed CLI versions {sorted(str(c) for c in clis)}: no verdict"
    return None


def compare_arms(
    base: Sequence[SampleResult], head: Sequence[SampleResult], *, across_models: bool = False
) -> Comparison:
    """Score ``head`` against ``base``. ``across_models``: the arms ran different models on purpose."""
    blocked = _blocked(base, head, across_models=across_models)
    names = list(dict.fromkeys(c.name for s in [*base, *head] for c in s.checks))
    rows = []
    for name in names:
        bp, bn, b_un = _tally(base, name)
        hp, hn, h_un = _tally(head, name)
        if blocked:
            verdict = "blocked"
        elif h_un > b_un:  # e.g. head stopped rewriting today's file: behaviour, not noise
            verdict = "regression (head not evaluated)"
        else:
            verdict = _verdict(bp, bn, hp, hn)
        rows.append(CheckRow(name, bp, bn, hp, hn, verdict, b_un + h_un))
    if any(hit_limit(s) for s in [*base, *head]):
        bp, hp = (sum(1 for s in arm if not hit_limit(s)) for arm in (base, head))
        verdict = "blocked" if blocked else _verdict(bp, len(base), hp, len(head))
        rows.append(CheckRow(LIMITS_ROW, bp, len(base), hp, len(head), verdict))
    metrics = []
    scored_base, scored_head = ([s for s in arm if not hit_limit(s)] for arm in (base, head))
    for label, get in METRICS:
        bv = [v for s in scored_base if s.metrics is not None and (v := get(s, s.metrics)) is not None]
        hv = [v for s in scored_head if s.metrics is not None and (v := get(s, s.metrics)) is not None]
        if bv and hv:
            worse = not blocked and len(bv) >= 2 and len(hv) >= 2 and median(hv) > max(bv)
            metrics.append(MetricRow(label, _spread(bv), _spread(hv), worse))
    regressions = [r.name for r in rows if r.verdict.startswith("regression")]
    return Comparison(rows, metrics, blocked, regressions)
