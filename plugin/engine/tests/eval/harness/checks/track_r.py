"""Pure Track R retrieval graders, judge calibration, and Gate G1 statistics (Phase 2).

Pure by construction (``tests/unit/test_eval_purity.py``): takes parsed Python
data structures only and performs zero filesystem, subprocess, or network I/O.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Mapping, Sequence
from typing import Any

from . import CheckResult

TRACK_R_CATEGORIES: tuple[str, ...] = (
    "status",
    "temporal",
    "single_hop",
    "multi_hop",
    "citation",
    "disambiguation",
    "abstention",
)

_ABSTAIN_RE = re.compile(
    r"\b(?:unknown|unclear|unverified|not\s+found|no\s+record|insufficient\s+(?:evidence|information)|"
    r"cannot\s+(?:confirm|determine|verify)|can't\s+(?:confirm|determine|verify)|"
    r"not\s+yet\s+decided|review[- ]queue)\b",
    re.IGNORECASE,
)

_BACKEND_REVEAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"`?(?:scripts/)?scoutctl\s+kb\s+[a-z-]+(?:\s+[^`\n]*)?`?", re.IGNORECASE), "[kb-tool]"),
    (re.compile(r"\b(?:RelateBackend|FilesBackend|relate-db|RelateDB)\b"), "[kb-backend]"),
    (re.compile(r"\.scout-cache/(?:relate|session-context\.json)[^\s`'\"]*"), "[kb-cache]"),
    (re.compile(r"\b[A-Za-z0-9_.-]+\.rl\b"), "[kb-snapshot]"),
    (
        re.compile(
            r"\b(?:MATCH\s*\([^)]*\)|shortestPath\s*\(|allShortestPaths\s*\(|CALL\s+db\.index\.fulltext)",
            re.IGNORECASE,
        ),
        "[graph-query]",
    ),
    (re.compile(r"\bknowledge-base/[A-Za-z0-9_./-]+\.md\b"), "[kb-note]"),
)


def _dedupe_top_k(paths: Sequence[str], k: int) -> list[str]:
    if k <= 0:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
            if len(out) >= k:
                break
    return out


def recall_at_k(retrieved_paths: Sequence[str], gold_paths: Sequence[str], k: int = 5) -> float:
    """Fraction of unique ``gold_paths`` present in the first ``k`` unique ``retrieved_paths``."""
    gold = {p for p in gold_paths if p}
    if not gold:
        return 1.0
    top = set(_dedupe_top_k(retrieved_paths, k))
    return len(gold & top) / len(gold)


def precision_at_k(retrieved_paths: Sequence[str], gold_paths: Sequence[str], k: int = 5) -> float:
    """Fraction of the first ``k`` unique ``retrieved_paths`` that belong to ``gold_paths``."""
    top = _dedupe_top_k(retrieved_paths, k)
    if not top:
        return 0.0
    gold = {p for p in gold_paths if p}
    return sum(1 for p in top if p in gold) / len(top)


def calls_to_first_gold_hit(retrieved_by_call: Sequence[Sequence[str]], gold_paths: Sequence[str]) -> int | None:
    """1-based tool-call index of the first call returning any ``gold_paths`` entry, else ``None``."""
    gold = {p for p in gold_paths if p}
    if not gold:
        return None
    for idx, group in enumerate(retrieved_by_call, start=1):
        if any(p in gold for p in group):
            return idx
    return None


def exact_answer_match(
    answer: str,
    expected_tokens: Sequence[str],
    forbidden_tokens: Sequence[str] = (),
) -> CheckResult:
    """Deterministic correctness check requiring every expected token and zero forbidden tokens."""
    low = answer.lower()
    missing = [tok for tok in expected_tokens if tok.lower() not in low]
    present_bad = [tok for tok in forbidden_tokens if tok.lower() in low]
    problems = [*(f"missing expected {t!r}" for t in missing), *(f"contains forbidden {t!r}" for t in present_bad)]
    return CheckResult("track_r_exact", not problems, tuple(problems))


def citation_faithfulness(
    cited_paths: Sequence[str],
    note_texts: Mapping[str, str],
    required_claims: Sequence[str],
) -> CheckResult:
    """Verify every cited note exists in ``note_texts`` and cited notes support every required claim."""
    problems: list[str] = []
    if not cited_paths and required_claims:
        return CheckResult("track_r_citation", False, ("no notes cited",))
    missing_notes = [p for p in cited_paths if p not in note_texts]
    for p in missing_notes:
        problems.append(f"hallucinated or invisible citation: {p}")
    valid_texts = [note_texts[p] for p in cited_paths if p in note_texts]
    for claim in required_claims:
        pat = re.compile(re.escape(claim), re.IGNORECASE)
        if not any(pat.search(txt) for txt in valid_texts):
            problems.append(f"claim {claim!r} not supported by cited notes")
    return CheckResult("track_r_citation", not problems, tuple(problems))


def abstention_check(answer: str, *, should_abstain: bool) -> CheckResult:
    """Check whether the answer appropriately abstains / routes to review-queue vs answers."""
    abstained = bool(_ABSTAIN_RE.search(answer))
    if should_abstain and not abstained:
        return CheckResult("track_r_abstention", False, ("expected abstention or review-queue routing, got assertion",))
    if not should_abstain and abstained:
        return CheckResult("track_r_abstention", False, ("unexpected abstention on answerable item",))
    return CheckResult("track_r_abstention", True, ())


def snapshot_fallback_check(graph_context: Mapping[str, Any], *, expect_degraded: bool) -> CheckResult:
    """Verify missing/corrupt RelateDB snapshot degrades cleanly to ``files`` and reports why."""
    backend = graph_context.get("backend")
    degraded = graph_context.get("degraded_from")
    reason = graph_context.get("fallback_reason")
    if expect_degraded:
        if backend == "files" and degraded and reason:
            return CheckResult("track_r_fallback", True, ())
        return CheckResult(
            "track_r_fallback",
            False,
            (f"expected clean fallback to files with reason, got backend={backend!r} degraded_from={degraded!r}",),
        )
    if degraded:
        return CheckResult("track_r_fallback", False, (f"unexpected degradation from {degraded}: {reason}",))
    return CheckResult("track_r_fallback", True, ())


def dolphin_removal_check(*, passed_with_gold: bool, passed_without_gold: bool) -> CheckResult:
    """DolphinBench item-validity check: item must pass with gold notes and fail when gold notes are removed."""
    if not passed_with_gold:
        return CheckResult("track_r_dolphin", False, ("item failed even with gold supporting notes present",))
    if passed_without_gold:
        return CheckResult(
            "track_r_dolphin",
            False,
            ("item still passed with gold notes removed (answer leaked or parametric guess)",),
        )
    return CheckResult("track_r_dolphin", True, ())


def strip_backend_revealing_text(text: str) -> str:
    """Scrub backend names, CLI spellings, Cypher syntax, and raw KB paths before rubric judging."""
    out = text
    for pattern, repl in _BACKEND_REVEAL_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def calibrate_judge(
    accepted_planted_wrong: Sequence[bool],
    *,
    min_rejection_rate: float = 0.90,
) -> dict[str, Any]:
    """Validate that a rubric judge rejects at least ``min_rejection_rate`` (90%) of planted wrong answers."""
    total = len(accepted_planted_wrong)
    if total == 0:
        return {"total": 0, "rejected": 0, "rejection_rate": 0.0, "passed": False}
    rejected = sum(1 for accepted in accepted_planted_wrong if not accepted)
    rate = rejected / total
    return {
        "total": total,
        "rejected": rejected,
        "rejection_rate": round(rate, 4),
        "min_rejection_rate": min_rejection_rate,
        "passed": rate >= min_rejection_rate,
    }


def mcnemar_exact_p(ref_pass: Sequence[bool], cand_pass: Sequence[bool]) -> dict[str, Any]:
    """Two-sided exact binomial McNemar test on discordant paired binary outcomes."""
    if len(ref_pass) != len(cand_pass):
        raise ValueError("ref_pass and cand_pass must have identical length")
    b10 = sum(1 for r, c in zip(ref_pass, cand_pass, strict=True) if r and not c)
    b01 = sum(1 for r, c in zip(ref_pass, cand_pass, strict=True) if not r and c)
    n_disc = b10 + b01
    if n_disc == 0:
        p_val = 1.0
    else:
        k_min = min(b10, b01)
        tail = sum(math.comb(n_disc, i) for i in range(k_min + 1)) / (2**n_disc)
        p_val = min(1.0, 2.0 * tail)
    return {"ref_only": b10, "cand_only": b01, "discordant": n_disc, "p_value": round(p_val, 6)}


def paired_cluster_bootstrap(
    records: Sequence[Mapping[str, Any]],
    *,
    n_boot: int = 2000,
    seed: int = 20261126,
    alpha: float = 0.05,
) -> dict[str, float]:
    """Cluster-resampled 95% confidence interval on paired accuracy difference (``cand_pass - ref_pass``).

    Each record provides ``{"id", "project", "ref_pass": bool | float, "cand_pass": bool | float}``.
    Standard errors and quantiles respect within-project correlation by resampling ``project`` clusters.
    """
    if not records:
        return {"mean_diff": 0.0, "ci_low": 0.0, "ci_high": 0.0}
    clusters: dict[str, list[float]] = {}
    all_diffs: list[float] = []
    for idx, rec in enumerate(records):
        diff = float(rec["cand_pass"]) - float(rec["ref_pass"])
        all_diffs.append(diff)
        cid = str(rec.get("project") or f"item_{rec.get('id', idx)}")
        clusters.setdefault(cid, []).append(diff)

    observed = sum(all_diffs) / len(all_diffs)
    keys = sorted(clusters)
    if len(keys) == 1 or n_boot <= 0:
        return {"mean_diff": round(observed, 6), "ci_low": round(observed, 6), "ci_high": round(observed, 6)}

    rng = random.Random(seed)
    boot_means: list[float] = []
    n_clusters = len(keys)
    for _ in range(n_boot):
        total_sum = 0.0
        total_count = 0
        for _ in range(n_clusters):
            picked = clusters[keys[rng.randrange(n_clusters)]]
            total_sum += sum(picked)
            total_count += len(picked)
        boot_means.append(total_sum / total_count if total_count else 0.0)
    boot_means.sort()
    lo_idx = max(0, min(int((alpha / 2.0) * len(boot_means)), len(boot_means) - 1))
    hi_idx = max(0, min(int((1.0 - alpha / 2.0) * len(boot_means)), len(boot_means) - 1))
    return {
        "mean_diff": round(observed, 6),
        "ci_low": round(boot_means[lo_idx], 6),
        "ci_high": round(boot_means[hi_idx], 6),
    }


def evaluate_gate_g1(
    records: Sequence[Mapping[str, Any]],
    *,
    baseline_tokens_median: float,
    candidate_tokens_median: float,
    baseline_calls_median: float,
    candidate_calls_median: float,
    non_inferiority_margin: float = -0.03,
    cost_ratio_cap: float = 1.10,
    accuracy_offset_threshold: float = 0.05,
    n_boot: int = 1000,
    seed: int = 20261126,
) -> dict[str, Any]:
    """Evaluate pre-registered Gate G1 on paired Track R item outcomes.

    Pass criteria (report § 'Pre-registered go/no-go gates'):
    - Overall lower 95% CI > -3 percentage points (``non_inferiority_margin``).
    - ``status``, ``temporal``, and ``citation`` strata (when present) each have lower 95% CI > -3 points.
    - ``abstention`` accuracy no worse than reference.
    - ``multi_hop`` strictly better with 95% CI excluding 0 (``ci_low > 0``).
    - Median tokens and tool calls <= 1.10x baseline unless overall ``mean_diff >= +0.05``.
    """
    if not records:
        return {"passed": False, "reasons": ["no Track R records provided"], "strata": {}}

    overall = paired_cluster_bootstrap(records, n_boot=n_boot, seed=seed)
    by_cat: dict[str, list[Mapping[str, Any]]] = {}
    for rec in records:
        cat = str(rec.get("category") or "unknown")
        by_cat.setdefault(cat, []).append(rec)

    strata = {cat: paired_cluster_bootstrap(rows, n_boot=n_boot, seed=seed) for cat, rows in sorted(by_cat.items())}
    reasons: list[str] = []

    if overall["ci_low"] <= non_inferiority_margin:
        reasons.append(f"overall 95% CI low ({overall['ci_low']:.4f}) <= {non_inferiority_margin:.4f}")

    for guarded in ("status", "temporal", "citation"):
        if guarded in strata and strata[guarded]["ci_low"] <= non_inferiority_margin:
            reasons.append(f"{guarded} 95% CI low ({strata[guarded]['ci_low']:.4f}) <= {non_inferiority_margin:.4f}")

    if "abstention" in by_cat:
        abs_rows = by_cat["abstention"]
        ref_abs = sum(float(r["ref_pass"]) for r in abs_rows) / len(abs_rows)
        cand_abs = sum(float(r["cand_pass"]) for r in abs_rows) / len(abs_rows)
        if cand_abs < ref_abs:
            reasons.append(f"abstention regressed ({cand_abs:.4f} < {ref_abs:.4f})")

    if "multi_hop" not in strata:
        reasons.append("missing multi_hop stratum")
    elif strata["multi_hop"]["mean_diff"] <= 0.0 or strata["multi_hop"]["ci_low"] <= 0.0:
        mh = strata["multi_hop"]
        reasons.append(f"multi_hop not significantly better (diff={mh['mean_diff']:.4f}, ci_low={mh['ci_low']:.4f})")

    token_ratio = (candidate_tokens_median / baseline_tokens_median) if baseline_tokens_median > 0 else 1.0
    call_ratio = (candidate_calls_median / baseline_calls_median) if baseline_calls_median > 0 else 1.0
    offset_ok = overall["mean_diff"] >= accuracy_offset_threshold
    if token_ratio > cost_ratio_cap and not offset_ok:
        reasons.append(f"median tokens {token_ratio:.3f}x > {cost_ratio_cap:.2f}x without accuracy offset")
    if call_ratio > cost_ratio_cap and not offset_ok:
        reasons.append(f"median tool calls {call_ratio:.3f}x > {cost_ratio_cap:.2f}x without accuracy offset")

    mcnemar = mcnemar_exact_p(
        [bool(r["ref_pass"]) for r in records],
        [bool(r["cand_pass"]) for r in records],
    )
    return {
        "passed": not reasons,
        "reasons": reasons,
        "overall": overall,
        "mcnemar": mcnemar,
        "strata": strata,
        "cost": {"token_ratio": round(token_ratio, 4), "call_ratio": round(call_ratio, 4)},
    }
