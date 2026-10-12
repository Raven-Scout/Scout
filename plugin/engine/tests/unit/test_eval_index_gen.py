"""The generated knowledge-base.md index for the eval fixture (spec 2026-10-12 §5.1)."""

from __future__ import annotations

import re

from tests.eval.harness.index_gen import generate_index


def test_same_seed_same_text() -> None:
    assert generate_index(seed=7, entries=50) == generate_index(seed=7, entries=50)


def test_different_seed_different_text() -> None:
    assert generate_index(seed=7, entries=50) != generate_index(seed=8, entries=50)


def test_default_size_is_big_enough_to_be_the_trap() -> None:
    assert len(generate_index().encode()) >= 60_000


def test_one_line_per_entry_with_lowercase_wikilinks_and_a_dated_stamp() -> None:
    text = generate_index(seed=1, entries=40)
    lines = [ln for ln in text.splitlines() if ln.startswith("- [[")]
    assert len(lines) == 40
    for ln in lines:
        slug = re.match(r"- \[\[([^\]]+)\]\]", ln).group(1)
        assert slug == slug.lower()
        assert re.search(r"\*\*Last verified:\*\* \{\{d-\d+\}\}$", ln), ln
