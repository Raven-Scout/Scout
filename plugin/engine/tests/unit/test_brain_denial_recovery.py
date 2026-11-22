"""A denied tool call must not end a run (v0.18.19 KB gate).

The gate's morning-consolidation ran one compound Bash command holding `TZ="$(scripts/scout-tz.sh)" date …`.
Claude Code's dontAsk mode denies every command substitution, whatever the allowlist says, so that call was
refused. The model read it as "Bash is now denied in this session" and stopped after 28 s, committing
nothing. Two things guard against it, in every brain file every run reads:

- Step 0 says a denial covers that one call, and how to redo it;
- the date call every run makes (the brain's timezone rule) has a form an allowlist can grant.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

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
    # A gate's deny is no invitation to get the same thing by another route (#742 review: after a kb-read-gate
    # deny of `cat`, a python or sort read passes the gate), and only a shell command is redone as plainer ones.
    assert "never get the same thing another way" in line
    assert line.index("never get the same thing another way") < line.index("otherwise redo a shell command")
    # Before any section after Step 0: it must already hold when a run's first shell call is refused.
    assert text.index(RECOVERY) < text.index("## Using Git History")


TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
RUNNERS = ("run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl")
PROMPT_RULE = (
    "- A denied tool call denies that call only, never Bash or the run. If its message says what to do instead, "
    "do that and never get the same thing another way; otherwise redo a shell command as plain single commands "
    "on paths inside /v/vault, or with Read/Edit, and carry on."
)


def _runner_prompt(name: str) -> str:
    """The runner's PROMPT as bash hands it to claude: placeholders filled, quoting resolved."""
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    start = text.index('PROMPT="')
    block = text[start : text.index('"\n', start + len('PROMPT="')) + 1]
    block = re.sub(r"\{\{SCOUT_DIR\}\}", "/v/vault", block)
    block = re.sub(r"\{\{[A-Z_]+\}\}", "X", block)
    done = subprocess.run(
        ["bash", "-c", f'MODE=morning-consolidation\n{block}\nprintf %s "$PROMPT"'],
        capture_output=True, text=True, check=True, env={"PATH": "/usr/bin:/bin"},
    )  # fmt: skip
    return done.stdout


@pytest.mark.parametrize("name", RUNNERS)
def test_every_runner_prompt_says_a_denial_covers_one_call(name):
    """v0.18.21's KB gate: the session read 473 of SKILL.md's 1926 lines (one Read's share), so Step 0's
    rule never reached it, and one denied compound Bash (a path outside the vault) ended the run. The
    runner prompt is the one text every session (scheduled or eval, any mode) reads before its first call."""
    prompt = _runner_prompt(name)
    assert prompt.count(PROMPT_RULE) == 1, prompt
    assert prompt.index(PROMPT_RULE) > prompt.index("Important:")


@pytest.mark.parametrize("kind", sorted(PINS))
def test_the_brain_dates_through_the_helper_not_a_substitution(brains, kind):
    text = brains[kind]
    assert 'TZ="$(scripts/scout-tz.sh)" date' not in text
    assert "scripts/scout-tz.sh date '+%Y-%m-%d'" in text
    assert "scripts/scout-tz.sh date '+%H:%M'" in text
