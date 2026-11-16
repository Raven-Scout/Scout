"""A scheduled run's commit to a big ``.csv`` in a share stays inside the pre-commit hook (#594).

A data export can run to a hundred thousand rows. An edit to one already shared
costs a line diff (it blocks on its first new line); only a file new to the
share is scanned for private notes. Budgets are generous for CI; the point is
the shape, not the millisecond.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from scout.kb import outbound
from scout.kb.lint_privacy import privacy_staged
from scout.kb.ontology import clear_frontmatter_cache
from tests.unit.privacy_vault_helpers import build_privacy_vault, git

pytestmark = pytest.mark.perf

ROWS = 100_000
BUDGET_S = 5.0
RUN = {"SCOUT_MODE": "morning-briefing"}
CSV = "knowledge-base/topics/misc/widget-metrics.csv"
_SHARE = (
    "  federation:\n    enabled: true\n  federation_defaults:\n"
    '    - {glob: "knowledge-base/topics/misc/**", scope: ["person:priya@example.com"]}\n'
)
_CSV = "week,region,signups,churn\n" + "".join(
    f"2026-W{n % 52:02d},region-{n % 40},{n % 997},{n % 13}\n" for n in range(ROWS)
)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    outbound.clear_cache()
    v = build_privacy_vault(tmp_path)
    with (v / "scout-config.yaml").open("a", encoding="utf-8") as fh:
        fh.write(_SHARE)
    git(v, "add", "scout-config.yaml")
    git(v, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "share topics/misc")
    return v


def _stage(vault: Path, text: str) -> None:
    (vault / CSV).write_text(text, encoding="utf-8")
    git(vault, "add", "--", CSV)


def _best_of_three(vault: Path) -> tuple[float, list[tuple[str, int | None]]]:
    times, found = [], []
    for _ in range(3):
        outbound.clear_cache()
        start = time.perf_counter()
        found = [(f.check, f.line) for f in privacy_staged(vault, env=RUN)]
        times.append(time.perf_counter() - start)
    return min(times), found


def test_a_new_big_csv_in_a_share_is_scanned_in_budget(vault: Path) -> None:
    _stage(vault, _CSV)
    took, found = _best_of_three(vault)
    assert found == []
    assert took < BUDGET_S, f"scanning a new {ROWS}-row csv took {took:.2f}s (budget {BUDGET_S}s)"


def test_a_row_appended_to_a_big_shared_csv_blocks_in_budget(vault: Path) -> None:
    _stage(vault, _CSV)
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "owner export")
    _stage(vault, _CSV + "2026-W45,region-1,61,1\n")
    took, found = _best_of_three(vault)
    assert found == [("privacy-widening", ROWS + 2)]
    assert took < BUDGET_S, f"an append to a {ROWS}-row csv took {took:.2f}s (budget {BUDGET_S}s)"
