"""The Claude Code commands route every tool to custom connectors and never dead-end the user."""

from __future__ import annotations

from pathlib import Path

COMMANDS = Path(__file__).parent.parent.parent.parent / "commands"
TEMPLATE = Path(__file__).parent.parent.parent / "scout" / "defaults" / "draft-connector.md"
DEAD_ENDS = (
    "not supported",
    "isn't supported",
    "doesn't come with",
    "does not come with",
    "no probe for it",
    "can't read",
    "can't be read",
    "has no probe",
)


def _text(name: str) -> str:
    return (COMMANDS / name).read_text(encoding="utf-8")


def test_scout_connect_calls_the_engine_contract():
    text = _text("scout-connect.md")
    assert text.startswith("---\nname: scout-connect\n")
    for call in (
        "connectors uncovered --json",
        "connectors custom draft --server",
        "connectors custom add --file -",
        "connectors custom remove",
        "busy",
    ):
        assert call in text, call


def test_scout_connect_does_not_restate_the_drafting_rules():
    assert "1–4 read tools per activity" in TEMPLATE.read_text(encoding="utf-8")
    assert "1–4 read tools per activity" not in _text("scout-connect.md")


def test_scout_connect_adjustments_never_add_tools():
    text = _text("scout-connect.md")
    assert "never add, rename or swap a tool" in text


def test_scout_connect_never_shows_raw_engine_messages_on_a_failed_draft():
    text = _text("scout-connect.md")
    assert "Scout couldn't find anything to read in" in text
    assert "(<message>)" not in text
    assert "Never show the raw engine `message`" in text


def test_scout_connect_handles_the_probe_failed_status():
    assert "probe-failed" in _text("scout-connect.md")


def test_scout_connect_shell_quotes_what_it_passes_to_the_shell():
    text = _text("scout-connect.md")
    assert "--server '<server name exactly as listed>'" in text
    assert "--input '<name>=<value>'" in text
    assert "custom remove '<key>'" in text and "--remove '<key>'" in text
    assert "<<'SCOUT_DEFINITION_EOF'" in text and "\nSCOUT_DEFINITION_EOF\n" in text
    assert "'\\''" in text
    assert '--server "' not in text and "<<'EOF'" not in text


def test_scout_connect_summary_wording_and_every_unconnected_status():
    text = _text("scout-connect.md")
    assert "from `definitions[]`" in text and "same `key`" in text
    for status in ("needs_auth", "unavailable", "unknown"):
        assert f"`{status}`" in text


def test_scout_update_points_at_scout_connect_and_has_no_nudge():
    text = _text("scout-update.md")
    assert "/scout-connect" in text and "connectors uncovered --json" in text
    assert "set-auto-update" not in text and "Auto-update nudge" not in text
    assert "/scout-setup" not in text


def test_scout_status_no_longer_reports_auto_update():
    text = _text("scout-status.md")
    assert "AUTO_UPDATE_ON" not in text and "Auto-update:" not in text


def test_no_command_dead_ends_the_user():
    for md in sorted(COMMANDS.glob("*.md")):
        lowered = md.read_text(encoding="utf-8").lower()
        for phrase in DEAD_ENDS:
            assert phrase not in lowered, f"{md.name} contains {phrase!r}"
