"""The markup pass in find_mentions stays linear on adversarial text (#733).

``find_mentions`` reads a text once more with comments, tags and link targets dropped
(``outbound._demarked``). A lazy ``<!--.*?-->`` made every unclosed comment scan to the
end, quadratic on a note full of them, inside a commit hook with a 15 s timeout that
fails closed. Each case here is a ~50 KB note; the budget is far under that timeout.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from scout.kb import outbound
from scout.kb.privacy import load_policy
from tests.unit.privacy_vault_helpers import build_privacy_vault

pytestmark = pytest.mark.perf

SIZE = 50_000
BUDGET_S = 1.0


def _note(unit: str) -> str:
    return unit * (SIZE // len(unit))


CASES = {
    "unclosed comments, one per line": _note("<!--x\n"),
    "unclosed comments on one line": _note("<!--x"),
    "entity-encoded comments on one line": _note("&lt;!--x"),
    "unclosed tags": _note("<a"),
    "entity-encoded tags": _note("&lt;a"),
    "unclosed link targets": _note("](x"),
    "emphasis marks": _note("*a_"),
    "a less-than in prose": _note("a < b\n"),
}


@pytest.fixture(scope="module")
def terms(tmp_path_factory: pytest.TempPathFactory) -> outbound.PrivateTerms:
    outbound.clear_cache()
    vault = build_privacy_vault(Path(tmp_path_factory.mktemp("markup-perf")))
    return outbound.private_terms(vault, load_policy(vault))


@pytest.mark.parametrize("text", list(CASES.values()), ids=list(CASES))
def test_find_mentions_is_linear_on_adversarial_markup(terms: outbound.PrivateTerms, text: str) -> None:
    assert len(text) >= SIZE * 0.95
    best = float("inf")
    for _ in range(2):
        start = time.perf_counter()
        outbound.find_mentions(text, terms, allow_markers=False)
        best = min(best, time.perf_counter() - start)
    assert best < BUDGET_S, f"{best:.2f}s on {len(text)} chars (budget {BUDGET_S}s)"


def test_a_name_after_an_unclosed_comment_still_blocks_within_budget(terms: outbound.PrivateTerms) -> None:
    """An unclosed `<!--` is literal text: the name markup hides after it, 50 KB on, is still found."""
    filler = "<!-- draft note\n" + "plain prose with a < b and c > d in it\n" * (SIZE // 39)
    text = filler + "Ask Sa**mm**y about it.\n"
    start = time.perf_counter()
    found = outbound.find_mentions(text, terms, allow_markers=False)
    elapsed = time.perf_counter() - start
    assert found == [text.count("\n")]
    assert elapsed < BUDGET_S, f"{elapsed:.2f}s on {len(text)} chars (budget {BUDGET_S}s)"
