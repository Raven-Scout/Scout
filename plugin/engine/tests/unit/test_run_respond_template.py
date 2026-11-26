"""run-respond.sh: the runner the telegram-respond trigger starts (spec 2026-11-21 §4).

It is run-dreaming.sh with a fixed mode, its own log and budget names, and a prompt
that hands the session RESPOND.md and the trigger's event file.
"""

from __future__ import annotations

from pathlib import Path

from scout import health
from scout.scripts.phase_assembly import render_template

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
SKILLS = TEMPLATES.parent / "skills"
CLAUDE_LAUNCH = '"$SCOUT_DIR/scout/scripts/claude-with-retry.sh"'


def _rendered() -> str:
    text = (TEMPLATES / "run-respond.sh.tmpl").read_text(encoding="utf-8")
    return render_template(
        text,
        {
            "INSTANCE_NAME": "TestScout",
            "INSTANCE_NAME_LOWER": "testscout",
            "SCOUT_DIR": "/vault",
            "CLAUDE_BIN": "/bin/claude",
            "SCOUTCTL_BIN": "/vault/scout/scripts/scoutctl",
            "MAX_BUDGET": "5.00",
        },
    )


def _prompt(text: str) -> str:
    start = text.index('PROMPT="')
    return text[start : text.index('"\n', start + len('PROMPT="'))]


def test_run_respond_template_pins_mode_and_budget() -> None:
    text = _rendered()
    assert "{{" not in text
    # run_skill sets SCOUT_FORCE_MODE=scout-respond, the skill's name, which is no slot: the mode is fixed.
    assert "\nMODE=respond\n" in text
    assert 'MODE="${SCOUT_FORCE_MODE' not in text
    assert "\nRUNNER_KIND=respond\n" in text
    assert 'LOG_FILE="$LOG_DIR/respond-${STAMP%-*}.log"' in text
    assert 'LOG_FILE="$LOG_DIR/respond-$STAMP-$$.log"' in text
    assert 'model resolve --slot "$MODE"' in text
    assert 'budget record-run "respond"' in text
    assert '"$COST_TRACKER" "respond"' in text
    assert "SCOUT_RUN_TYPE=respond" in text
    assert "dreaming" not in text.lower()
    prompt = _prompt(text)
    assert "running in RESPOND mode" in prompt
    assert "Read /vault/scout/brain/RESPOND.md in full and follow it" in prompt


def test_run_respond_prompt_points_at_the_trigger_event_without_expanding_it() -> None:
    """Escaped, so a manual run (no SCOUT_TRIGGER_EVENT_PATH) doesn't trip `set -u`; RESPOND.md reads it."""
    prompt = _prompt(_rendered())
    assert "\\$SCOUT_TRIGGER_EVENT_PATH" in prompt
    assert "printenv SCOUT_TRIGGER_EVENT_PATH" in prompt


def test_run_respond_takes_the_shared_session_lock_before_claude() -> None:
    """The Telegram source and the ledger read a live respond run off .scout-logs/.scout-session.lock."""
    text = _rendered()
    assert 'LOCK_FILE="$LOG_DIR/.scout-session.lock"' in text
    assert 'LOCK_LIB="$SCOUT_DIR/scout/scripts/scout-lock.sh"' in text
    assert '\n. "$LOCK_LIB"\n' in text
    acquire = text.index("if ! scout_lock_acquire; then\n    scout_record_skip skipped_lock\n    exit 0\nfi\n")
    assert text.index('. "$LOCK_LIB"') < acquire < text.index(CLAUDE_LAUNCH)


def test_run_respond_builds_the_session_context_for_this_run_before_claude() -> None:
    """RESPOND.md's manual-run fallback trusts session-context.json only when this run wrote it."""
    text = _rendered()
    pre = text.index('"$PRE_SESSION" respond >> "$LOG_FILE" 2>&1 || true')
    assert pre < text.index(CLAUDE_LAUNCH)


def test_run_respond_exports_the_session_type_for_the_cost_step() -> None:
    """core/git-setup.md's last step runs write-session-cost.sh "$SESSION_TYPE" inside the session."""
    text = _rendered()
    assert text.count("export SESSION_TYPE=respond\n") == 1
    assert text.index("export SESSION_TYPE=respond") < text.index(CLAUDE_LAUNCH)


def test_a_respond_run_ledger_row_names_its_runner() -> None:
    """run-outcome.sh writes `runner: respond` (SCOUT_RUNNER_KIND), and run health reads it back."""
    outcome_sh = (TEMPLATES / "scripts" / "run-outcome.sh.tmpl").read_text(encoding="utf-8")
    assert 'if runner in ("scout", "dreaming", "research", "respond"):' in outcome_sh
    row = health._parse_row({"slot": "respond", "runner": "respond", "started_at": 1, "finished_at": 2, "exit_code": 0})
    assert row is not None
    assert row.runner == "respond"


def test_scout_respond_skill_launches_the_runner_in_the_background() -> None:
    skill = (SKILLS / "scout-respond" / "SKILL.md").read_text(encoding="utf-8")
    head, _, body = skill.partition("\n---\n")
    assert "name: scout-respond" in head
    assert (
        "description: Answer pending Telegram messages from the owner now "
        "(normally started automatically by the telegram-respond trigger)"
    ) in head
    assert (
        'R=./scout/runners/run-respond.sh; [ -x "$R" ] || R=./run-respond.sh; nohup bash "$R" > /dev/null 2>&1 &'
    ) in body
    assert ".scout-logs/respond-*.log" in body
