"""A vault with Telegram set up gets a wrap notification, with or without Slack (#484).

After #481 the runners defer to the brain file's notification sections, and the
only stock ones were Slack's (`requires: slack`). A vault with Telegram and no
Slack therefore got no wrap at all. `telegram` is now a probe key (enabled when
both secrets exist), and a `requires: telegram` notification section is assembled
into SKILL.md and RESEARCH.md. When both are present, Telegram replaces the Slack
DM, and both sections say so, so a session reading either one first ends up in
the same place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.connector_probes import ProbeKind, load_registry
from scout.scripts.phase_assembly import parse_phase_file, select_sections

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
PHASES = REPO_ROOT / "phases"

TARGETS = {
    "SKILL": (["core", "connectors"], {"briefing", "consolidation"}),
    "RESEARCH": (["core", "research"], {"research"}),
}


def _notification_names(target: str, enabled: set[str]) -> list[str]:
    dirs, modes = TARGETS[target]
    names = []
    for d in dirs:
        for pf in sorted((PHASES / d).glob("*.md")):
            for s in select_sections(parse_phase_file(pf), enabled_connectors=enabled, modes=modes):
                if s.slot == "notification":
                    names.append(s.name)
    return names


@pytest.mark.parametrize(
    ("target", "enabled", "expected"),
    [
        ("SKILL", {"telegram"}, ["telegram"]),
        ("SKILL", {"slack"}, ["slack"]),
        ("SKILL", {"slack", "telegram"}, ["slack", "telegram"]),
        ("SKILL", set(), []),
        ("RESEARCH", {"telegram"}, ["Telegram Research Notification"]),
        ("RESEARCH", {"slack"}, ["Slack Research Notification"]),
        ("RESEARCH", {"slack", "telegram"}, ["Slack Research Notification", "Telegram Research Notification"]),
    ],
)
def test_notification_sections_follow_enabled_connectors(target: str, enabled: set[str], expected: list[str]) -> None:
    assert _notification_names(target, enabled) == expected


def test_telegram_probe_checks_both_secrets_without_network() -> None:
    probe = load_registry(REPO_ROOT / "templates" / "connector-probes.yaml")["telegram"]
    assert probe.kind is ProbeKind.BASH
    assert "telegram-bot-token" in probe.bash_command
    assert "telegram-chat-id" in probe.bash_command
    assert "scoutctl" not in probe.bash_command  # setup may run before the shim is on PATH


def test_slack_sections_yield_to_telegram() -> None:
    """Slack's sections assemble first (filename order), so they must name the override too."""
    slack = (PHASES / "connectors" / "slack.md").read_text(encoding="utf-8")
    research = (PHASES / "research" / "commit-notify.md").read_text(encoding="utf-8")
    assert "Skip this DM when SKILL.md also has a Telegram Notification section" in slack
    assert "Skip this step when this file also has a Step 4d: Telegram Notification" in research


def test_telegram_sections_send_through_scoutctl_html_mode() -> None:
    for path in (PHASES / "connectors" / "telegram.md", PHASES / "research" / "commit-notify.md"):
        text = path.read_text(encoding="utf-8")
        assert "scoutctl notify telegram --tier info --parse-mode html --body-file - <<'SCOUT_WRAP'" in text
        assert "do not fall back to slack" in text.lower()
