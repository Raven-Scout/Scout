"""A vault with Telegram set up gets a wrap notification, with or without Slack (#484).

After #481 the runners defer to the brain file's notification sections, and the
only stock ones were Slack's (`requires: slack`). A vault with Telegram and no
Slack therefore got no wrap at all. `telegram` is now a probe key (enabled when
both secrets exist), and a `requires: telegram` notification section is assembled
into SKILL.md and RESEARCH.md.

With both enabled the wrap goes to BOTH: the Slack DM stays and Telegram is an
added push. `notify.wrap_channels` in scout-config.yaml picks one channel, and it
is applied at assembly time: the other channel's notification section is simply
not assembled, so no section has to tell the session to skip itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.bootstrap import _assemble, config_from_vault
from scout.scripts.connector_probes import ProbeKind, load_registry
from scout.scripts.phase_assembly import parse_phase_file, parse_wrap_channels, select_sections

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/plugin
PHASES = REPO_ROOT / "phases"

TARGETS = {
    "SKILL": (["core", "connectors"], {"briefing", "consolidation"}),
    "RESEARCH": (["core", "research"], {"research"}),
}


def _notification_names(target: str, enabled: set[str], wrap_channels: set[str] | None = None) -> list[str]:
    dirs, modes = TARGETS[target]
    names = []
    for d in dirs:
        for pf in sorted((PHASES / d).glob("*.md")):
            sections = parse_phase_file(pf)
            for s in select_sections(sections, enabled_connectors=enabled, modes=modes, wrap_channels=wrap_channels):
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


@pytest.mark.parametrize(
    ("target", "wrap_channels", "expected"),
    [
        # Both by default: unset keeps every enabled wrap channel.
        ("SKILL", None, ["slack", "telegram"]),
        ("SKILL", {"slack", "telegram"}, ["slack", "telegram"]),
        ("SKILL", {"telegram"}, ["telegram"]),
        ("SKILL", {"slack"}, ["slack"]),
        ("RESEARCH", None, ["Slack Research Notification", "Telegram Research Notification"]),
        ("RESEARCH", {"telegram"}, ["Telegram Research Notification"]),
        ("RESEARCH", {"slack"}, ["Slack Research Notification"]),
    ],
)
def test_wrap_channels_pick_the_notification_sections(
    target: str, wrap_channels: set[str] | None, expected: list[str]
) -> None:
    assert _notification_names(target, {"slack", "telegram"}, wrap_channels) == expected


def test_wrap_channels_never_add_a_channel_that_is_not_enabled() -> None:
    assert _notification_names("SKILL", {"slack"}, {"telegram"}) == []
    assert _notification_names("SKILL", {"slack"}, {"slack", "telegram"}) == ["slack"]


def test_wrap_channels_leave_slack_scans_alone() -> None:
    """Only the notification slot is filtered: a Telegram-only wrap still scans Slack."""
    pf = PHASES / "connectors" / "slack.md"
    kept = select_sections(
        parse_phase_file(pf),
        enabled_connectors={"slack", "telegram"},
        modes={"briefing", "consolidation"},
        wrap_channels={"telegram"},
    )
    slots = {s.slot for s in kept}
    assert "notification" not in slots
    assert {"outbound-scan", "inbound-scan", "cross-check", "update"} <= slots


def test_parse_wrap_channels_accepts_known_channels() -> None:
    assert parse_wrap_channels(None) is None
    assert parse_wrap_channels(["slack"]) == frozenset({"slack"})
    assert parse_wrap_channels(["telegram", "slack"]) == frozenset({"slack", "telegram"})


@pytest.mark.parametrize(
    ("value", "match"),
    [
        (["slak"], "unknown channel"),
        (["slack", "email"], "unknown channel"),
        ([], "non-empty list"),
        ("telegram", "non-empty list"),
        ({"telegram": True}, "non-empty list"),
    ],
)
def test_parse_wrap_channels_rejects_unknown_or_misshapen_values(value: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_wrap_channels(value)


def _vault(tmp_path: Path, config: str) -> Path:
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text(
        "user:\n  name: Alex\nconnectors:\n  enabled: [slack, telegram]\n" + config, encoding="utf-8"
    )
    return vault


@pytest.mark.parametrize(
    ("config", "slack", "telegram"),
    [
        ("", True, True),  # both by default
        ("notify:\n  wrap_channels: [slack, telegram]\n", True, True),
        ("notify:\n  wrap_channels: [telegram]\n", False, True),
        ("notify:\n  wrap_channels: [slack]\n", True, False),
    ],
)
def test_assembled_brain_files_follow_notify_wrap_channels(
    tmp_path: Path, config: str, slack: bool, telegram: bool
) -> None:
    cfg = config_from_vault(_vault(tmp_path, config), plugin_root=REPO_ROOT, plugin_version="0.0.0")
    skill = _assemble(cfg, "SKILL", custom={})
    research = _assemble(cfg, "RESEARCH", custom={})
    assert ("## Slack Notification" in skill) is slack
    assert ("## Step 4d: Slack Notification" in research) is slack
    assert ("## Telegram Notification" in skill) is telegram
    assert ("## Step 4d: Telegram Notification" in research) is telegram
    # Slack scans are not wrap sections: they stay whatever the wrap choice.
    assert "## Slack Inbound Scan" in skill


def test_config_from_vault_rejects_an_unknown_wrap_channel(tmp_path: Path) -> None:
    vault = _vault(tmp_path, "notify:\n  wrap_channels: [slack, discord]\n")
    with pytest.raises(ValueError, match="notify.wrap_channels: unknown channel"):
        config_from_vault(vault, plugin_root=REPO_ROOT, plugin_version="0.0.0")


def test_config_from_vault_warns_when_no_chosen_channel_is_enabled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text(
        "connectors:\n  enabled: [slack]\nnotify:\n  wrap_channels: [telegram]\n", encoding="utf-8"
    )
    cfg = config_from_vault(vault, plugin_root=REPO_ROOT, plugin_version="0.0.0")
    assert cfg.wrap_channels == frozenset({"telegram"})
    assert "runs will send no wrap notification" in capsys.readouterr().err


def test_no_section_tells_the_session_to_skip_the_other_channel() -> None:
    """The wrap choice is made at assembly; a present section is always sent (both by default)."""
    slack = (PHASES / "connectors" / "slack.md").read_text(encoding="utf-8")
    telegram = (PHASES / "connectors" / "telegram.md").read_text(encoding="utf-8")
    research = (PHASES / "research" / "commit-notify.md").read_text(encoding="utf-8")
    for text in (slack, telegram, research):
        assert "Skip this" not in text
        assert "replaces the Slack DM" not in text
        assert "instead of** any Slack DM" not in text
        assert "notify.wrap_channels" in text
    assert "Send this DM even when SKILL.md also has a Telegram Notification section" in slack
    assert "Send this DM even when this file also has a Step 4d: Telegram Notification" in research


def test_slack_wrap_stays_behind_the_outbound_check_and_telegram_is_owner_only() -> None:
    """Privacy (#453) holds with both channels on: Slack is shared, Telegram is owner-only."""
    slack = (PHASES / "connectors" / "slack.md").read_text(encoding="utf-8")
    research = (PHASES / "research" / "commit-notify.md").read_text(encoding="utf-8")
    telegram = (PHASES / "connectors" / "telegram.md").read_text(encoding="utf-8")
    for text in (slack, research):
        assert "kb check-outbound" in text
        assert "--surface slack" in text
    for text in (telegram, research):
        assert "owner-only surface" in text
    assert "Never copy that private content into the Slack DM" in telegram


def test_telegram_sections_send_through_scoutctl_html_mode() -> None:
    for path in (PHASES / "connectors" / "telegram.md", PHASES / "research" / "commit-notify.md"):
        text = path.read_text(encoding="utf-8")
        assert "scoutctl notify telegram --tier info --parse-mode html --body-file - <<'SCOUT_WRAP'" in text
        assert "do not fall back to slack" in text.lower()
