"""Verdicts for a phase change: base vs head (spec 2026-10-12 §8). Pure, so every rule is tested here."""

from __future__ import annotations

import json
from pathlib import Path

from tests.eval.harness.checks import CheckResult
from tests.eval.harness.metrics import parse_stream
from tests.eval.harness.runner import SampleResult
from tests.eval.harness.verdicts import compare_arms


def _sample(
    passes: dict[str, bool | None],
    *,
    calls: int = 10,
    aborted: dict | None = None,
    model: str = "claude-sonnet-5",
    cli: str = "2.1.278",
) -> SampleResult:
    init = {"type": "system", "subtype": "init", "model": model, "mcp_servers": []}
    uses = [
        {"type": "assistant", "message": {"id": f"m{i}", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Read", "input": {"file_path": f"/v/{i}"}}]}}
        for i in range(calls)
    ]  # fmt: skip
    result = {"type": "result", "subtype": "success", "num_turns": 1}
    lines = [json.dumps(e) for e in (init, *uses, result)]
    return SampleResult(
        scenario="s",
        rep=1,
        result_dir=Path("/r"),
        aborted=aborted,
        metrics=parse_stream(lines),
        checks=tuple(CheckResult(n, p, ()) for n, p in passes.items()),
        unstubbed=0,
        meta={"resolved_model": model, "cli_version": cli},
    )


def _arm(*passed: bool, **kw) -> list[SampleResult]:
    return [_sample({"placement": p}, **kw) for p in passed]


def _row(cmp, name: str = "placement"):
    return next(r for r in cmp.checks if r.name == name)


def test_stable_base_and_two_head_misses_is_a_regression() -> None:
    cmp = compare_arms(_arm(True, True, True, True, True), _arm(True, True, True, False, False))
    assert cmp.blocked is None
    r = _row(cmp)
    assert (r.base_pass, r.base_n, r.head_pass, r.head_n, r.verdict) == (5, 5, 3, 5, "regression")
    assert cmp.regressions == ["placement"]


def test_one_head_miss_is_flaky_not_a_regression() -> None:
    cmp = compare_arms(_arm(True, True, True, True, True), _arm(True, True, True, True, False))
    assert _row(cmp).verdict == "flaky on head"
    assert cmp.regressions == []


def test_the_mirror_is_an_improvement() -> None:
    assert _row(compare_arms(_arm(False, False, True), _arm(True, True, True))).verdict == "improvement"


def test_a_flaky_base_is_noisy_and_excluded() -> None:
    cmp = compare_arms(_arm(True, False, True), _arm(False, False, False))
    assert _row(cmp).verdict == "noisy"
    assert cmp.regressions == []


def test_identical_results_are_the_same() -> None:
    assert _row(compare_arms(_arm(True, True), _arm(True, True))).verdict == "same"
    assert _row(compare_arms(_arm(False, False), _arm(False, False))).verdict == "same"


def test_fewer_than_two_samples_gives_no_verdict() -> None:
    cmp = compare_arms(_arm(True), _arm(False))
    assert _row(cmp).verdict == "need N≥2"
    assert cmp.regressions == []


def test_a_check_that_could_not_be_evaluated_does_not_count() -> None:
    base = [_sample({"placement": True}), _sample({"placement": True}), _sample({"placement": None})]
    r = _row(compare_arms(base, _arm(True, True)))
    assert (r.base_pass, r.base_n, r.unevaluated) == (2, 2, 1)


def test_any_aborted_sample_blocks_every_verdict() -> None:
    cmp = compare_arms(_arm(True, True), [*_arm(False, False), _sample({}, aborted={"reason": "timeout"})])
    assert cmp.blocked == "aborted samples (base 0, head 1): no verdict"
    assert cmp.regressions == []


def test_mixed_models_or_cli_versions_block() -> None:
    assert compare_arms(_arm(True, True), _arm(True, True, model="claude-opus-5")).blocked.startswith("mixed models")
    assert compare_arms(_arm(True, True), _arm(True, True, cli="2.1.300")).blocked.startswith("mixed CLI versions")


def test_a_metric_is_worse_only_when_head_median_exceeds_base_max() -> None:
    base = [_sample({"placement": True}, calls=c) for c in (7, 8, 9)]
    worse = compare_arms(base, [_sample({"placement": True}, calls=c) for c in (10, 11, 12)])
    overlap = compare_arms(base, [_sample({"placement": True}, calls=c) for c in (8, 9, 12)])
    row = next(m for m in worse.metrics if m.label == "tool calls")
    assert (row.base, row.head, row.worse) == ((8, 7, 9), (11, 10, 12), True)
    assert next(m for m in overlap.metrics if m.label == "tool calls").worse is False


def test_a_head_that_cannot_be_evaluated_where_base_could_is_a_regression() -> None:
    # e.g. the head session stopped rewriting today's file (not_enriched): that's behaviour, not noise.
    head = [_sample({"placement": None}) for _ in range(3)]
    cmp = compare_arms(_arm(True, True, True), head)
    r = _row(cmp)
    assert (r.head_n, r.unevaluated, r.verdict) == (0, 3, "regression (head not evaluated)")
    assert cmp.regressions == ["placement"]


def test_improvement_wins_over_a_flaky_base() -> None:
    assert _row(compare_arms(_arm(True, False, False), _arm(True, True, True))).verdict == "improvement"


# ----- compare-models: the arms ran different models on purpose -------------


def test_across_models_scores_two_different_models() -> None:
    opus = _arm(True, True, True, model="claude-opus-5")
    sonnet = _arm(True, False, False, model="claude-sonnet-5")
    cmp = compare_arms(opus, sonnet, across_models=True)
    assert cmp.blocked is None
    assert _row(cmp).verdict == "regression"
    assert cmp.regressions == ["placement"]


def test_across_models_blocks_when_both_arms_ran_the_same_model() -> None:
    # The switch did not take (e.g. a shim that ignored --model): no verdict, not "same".
    cmp = compare_arms(_arm(True, True), _arm(True, True), across_models=True)
    assert cmp.blocked is not None and "did not take" in cmp.blocked
    assert _row(cmp).verdict == "blocked"


def test_across_models_blocks_an_arm_that_mixed_models() -> None:
    a = [*_arm(True, model="claude-opus-5"), *_arm(True, model="claude-opus-4")]
    cmp = compare_arms(a, _arm(True, True, model="claude-sonnet-5"), across_models=True)
    assert cmp.blocked is not None and "within one arm" in cmp.blocked


def test_the_default_still_blocks_mixed_models() -> None:
    cmp = compare_arms(_arm(True, True, model="claude-opus-5"), _arm(True, True, model="claude-sonnet-5"))
    assert cmp.blocked is not None and cmp.blocked.startswith("mixed models")
