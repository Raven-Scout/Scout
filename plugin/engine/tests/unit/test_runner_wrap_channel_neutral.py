"""Static guard: the runner prompts never name the wrap notification's channel.

Regression context: every runner prompt ended with "send a brief Slack DM to
{{USER_NAME}} (Slack ID: {{USER_SLACK_ID}})". A vault that moved its wrap off
Slack in its brain file (SKILL.md said Telegram, never Slack) handed the session
two contradictory instructions in one prompt, and the wrong choice posts personal
content into an employer-controlled workspace. The brain file's notification
section is the single source of truth for the channel and recipient; the runner
only says that a wrap is owed, and where to look for how to send it.

A stock DREAMING.md has no notification section of its own (the Slack one is
assembled into SKILL.md only), so the dreaming runner must point at SKILL.md as
the fallback, or dreaming runs would stop notifying anyone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
TEMPLATES = REPO_ROOT / "templates"

RUNNERS = ["run-scout.sh.tmpl", "run-dreaming.sh.tmpl", "run-research.sh.tmpl"]


def _prompt(name: str) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    start = text.index('PROMPT="')
    return text[start : text.index('"\n', start + len('PROMPT="'))]


@pytest.mark.parametrize("name", RUNNERS)
def test_runner_prompt_does_not_hardcode_slack(name: str) -> None:
    prompt = _prompt(name)
    assert "Slack DM" not in prompt, f"{name}: the wrap channel belongs to the brain file, not the runner"
    assert "{{USER_SLACK_ID}}" not in prompt, f"{name}: the recipient belongs to the brain file, not the runner"
    assert "Do not assume Slack" in prompt


@pytest.mark.parametrize("name", RUNNERS)
def test_runner_prompt_defers_to_skill_md_notification_section(name: str) -> None:
    """Every runner can reach SKILL.md's section; DREAMING.md and RESEARCH.md may lack one."""
    prompt = _prompt(name)
    assert "SKILL.md's notification section" in prompt or "or SKILL.md's when" in prompt
    assert "send nothing and say so" in prompt, f"{name}: no channel configured must mean no send, not a guess"
