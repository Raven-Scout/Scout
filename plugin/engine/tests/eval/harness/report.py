"""Plain-text report of a set of samples (spec 2026-10-12 §6, §8)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from statistics import median

from .metrics import SampleMetrics, longest_search_run, outside_reads, repeated_calls, whole_reads
from .runner import SampleResult
from .runner_env import ENGINE

PLUGIN_ROOT = str(ENGINE.parent)
METRICS: list[tuple[str, Callable[[SampleResult, SampleMetrics], float | None]]] = [
    ("tool calls", lambda r, m: m.tool_call_count),
    ("turns", lambda r, m: m.turns),
    ("duration s", lambda r, m: m.duration_s),
    ("usage-equivalent $", lambda r, m: m.cost_usd),
    ("longest search run", lambda r, m: longest_search_run(m.calls)),
    ("whole reads of knowledge-base.md", lambda r, m: whole_reads(m.calls, "knowledge-base.md")),
    ("repeated calls", lambda r, m: repeated_calls(m.calls)),
    ("reads outside the vault", lambda r, m: outside_reads(m.calls, [str(r.result_dir / "vault"), PLUGIN_ROOT])),
    ("unstubbed connector calls", lambda r, m: r.unstubbed),
    ("permission denials", lambda r, m: len(m.denials)),
]


def _fmt(value: float) -> str:
    return f"{value:g}" if isinstance(value, int) or value == int(value) else f"{value:.2f}"


def summarize(results: Sequence[SampleResult]) -> str:
    lines: list[str] = []
    for name in dict.fromkeys(r.scenario for r in results):
        mine = [r for r in results if r.scenario == name]
        scored = [r for r in mine if r.aborted is None and r.metrics is not None]
        lines.append(f"{name}: {len(mine)} sample(s), {len(mine) - len(scored)} aborted")
        for r in mine:
            if r.aborted:
                lines.append(f"  aborted  {r.aborted.get('reason')}  {r.result_dir}")
        checks = dict.fromkeys(c.name for r in scored for c in r.checks)
        if checks:
            lines.append("  checks")
        for check in checks:
            results_for = [c for r in scored for c in r.checks if c.name == check]
            passed = sum(1 for c in results_for if c.passed is True)
            unknown = sum(1 for c in results_for if c.passed is None)
            tail = f" ({unknown} could not evaluate)" if unknown else ""
            lines.append(f"    {check}  {passed}/{len(results_for)}{tail}")
        if scored:
            lines.append("  process (median over scored samples)")
        for label, get in METRICS:
            values = [v for r in scored if r.metrics is not None and (v := get(r, r.metrics)) is not None]
            if values:
                lines.append(f"    {label}  {_fmt(median(values))}")
        for r in scored:
            failing = [c for c in r.checks if c.passed is not True]
            lines.append(f"  sample {r.rep}  {r.result_dir}")
            for c in failing:
                for detail in c.details:
                    lines.append(f"    {c.name}: {detail}")
    return "\n".join(lines)
