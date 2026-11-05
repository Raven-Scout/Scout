"""The shared-reads ledger only grows, and the outbound gate reads it inside a hook with a 15 s timeout.

A hook that times out fails closed, so a reader with a long history would lose
every Slack send. 500 whole notes of about 3,500 words is well beyond a pilot.
Budgets are generous for CI; the point is the shape, not the millisecond.
"""

from __future__ import annotations

import random
import time
from pathlib import Path

import pytest

from scout.kb import outbound, shared_reads
from tests.unit.privacy_vault_helpers import build_privacy_vault

pytestmark = pytest.mark.perf

NOTES = 500
WORDS = 3500
COLD_BUDGET_S = 3.0
WARM_BUDGET_S = 0.3


def _text(rng: random.Random, vocabulary: list[str]) -> str:
    words = rng.choices(vocabulary, k=WORDS)
    return " ".join(w + ("." if i % 14 == 13 else "") for i, w in enumerate(words))


@pytest.fixture(scope="module")
def vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    v = build_privacy_vault(tmp_path_factory.mktemp("shared-reads-perf"))
    rng = random.Random(7)
    vocabulary = [f"word{n}" for n in range(20000)]
    for n in range(NOTES):
        answer = {"path": f"knowledge-base/projects/p{n}/note-number-{n}.md", "title": f"Note {n}", "text": ""}
        answer["text"] = _text(rng, vocabulary)
        answer["owner_email"] = "priya@example.com"
        shared_reads.record_tool_call(v, {}, answer)
    return v


def test_a_long_ledger_stays_inside_the_hook_timeout(vault: Path) -> None:
    """Best of three, as the other perf tests do: one slow runner tick isn't a regression."""
    text = "The standup moved to ten, and the widget rollout is on track for the review.\n" * 20
    colds, warms = [], []
    for _ in range(3):
        outbound.clear_cache()
        start = time.perf_counter()
        assert outbound.check_outbound(vault, text, "slack") == []
        colds.append(time.perf_counter() - start)
        start = time.perf_counter()
        assert outbound.check_outbound(vault, text, "slack") == []
        warms.append(time.perf_counter() - start)
    assert min(colds) < COLD_BUDGET_S, f"cold check took {min(colds):.2f}s (budget {COLD_BUDGET_S}s)"
    assert min(warms) < WARM_BUDGET_S, f"warm check took {min(warms):.2f}s (budget {WARM_BUDGET_S}s)"


def test_a_hit_is_found_in_a_long_ledger(vault: Path) -> None:
    outbound.clear_cache()
    assert outbound.check_outbound(vault, "see projects/p499/note-number-499 please", "slack") == [1]
