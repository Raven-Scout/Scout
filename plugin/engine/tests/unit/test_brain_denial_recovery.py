"""A denied tool call must not end a run (v0.18.19 KB gate).

The gate's morning-consolidation ran one compound Bash command holding `TZ="$(scripts/scout-tz.sh)" date …`.
Claude Code's dontAsk mode denies every command substitution, whatever the allowlist says, so that call was
refused. The model read it as "Bash is now denied in this session" and stopped after 28 s, committing
nothing. Two things guard against it, in every brain file every run reads:

- Step 0 says a denial covers that one call, and how to redo it;
- the date call every run makes (the brain's timezone rule) has a form an allowlist can grant.
"""

from __future__ import annotations

import pytest

from tests.unit.test_brain_size_budget import PINS, _assemble, _config

RECOVERY = "**A denied tool call denies that call only, never Bash or the run.**"


@pytest.fixture(scope="module")
def brains() -> dict[str, str]:
    return {kind: _assemble(_config(), kind, custom={}) for kind in PINS}


@pytest.mark.parametrize("kind", sorted(PINS))
def test_every_brain_says_a_denial_covers_one_call(brains, kind):
    text = brains[kind]
    assert text.count(RECOVERY) == 1
    line = next(ln for ln in text.splitlines() if RECOVERY in ln)
    assert "$(…)" in line and "Read/Edit" in line and "carry on" in line
    # Before any section after Step 0: it must already hold when a run's first shell call is refused.
    assert text.index(RECOVERY) < text.index("## Using Git History")


@pytest.mark.parametrize("kind", sorted(PINS))
def test_the_brain_dates_through_the_helper_not_a_substitution(brains, kind):
    text = brains[kind]
    assert 'TZ="$(scripts/scout-tz.sh)" date' not in text
    assert "scripts/scout-tz.sh date '+%Y-%m-%d'" in text
    assert "scripts/scout-tz.sh date '+%H:%M'" in text
