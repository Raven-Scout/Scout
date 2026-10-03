"""Unit tests for scout.sessions.settings."""

from __future__ import annotations

from pathlib import Path

from scout.config import load_config
from scout.sessions.settings import AgentSessionsSettings, load_settings


def test_defaults_match_spec() -> None:
    s = AgentSessionsSettings()
    assert (s.stale_after_days, s.running_window_seconds, s.pr_refresh_minutes) == (3, 120, 10)
    assert (s.pr_fetch_cap, s.transcript_window_days, s.done_visible_hours) == (25, 14, 24)
    assert s.render_max_per_bucket == 15
    assert s.use_gh is True
    assert s.desktop_support_dir is None and s.claude_home is None


def test_packaged_defaults_carry_agent_sessions_block(fake_data_dir: Path) -> None:
    cfg = load_config(fake_data_dir)
    block = cfg["agent_sessions"]
    assert block["stale_after_days"] == 3
    assert block["use_gh"] is True
    assert block["desktop_support_dir"] is None


def test_from_config_reads_overrides_and_coerces_ints() -> None:
    s = AgentSessionsSettings.from_config(
        {"agent_sessions": {"stale_after_days": "5", "use_gh": False, "claude_home": "/tmp/ch"}}
    )
    assert s.stale_after_days == 5
    assert s.use_gh is False
    assert s.claude_home == "/tmp/ch"
    assert s.pr_refresh_minutes == 10  # untouched default


def test_from_config_ignores_garbage_values() -> None:
    s = AgentSessionsSettings.from_config({"agent_sessions": {"stale_after_days": "soon", "pr_fetch_cap": -4}})
    assert s.stale_after_days == 3  # non-int falls back
    assert s.pr_fetch_cap == 25  # negative falls back


def test_from_config_tolerates_missing_or_non_mapping_block() -> None:
    assert AgentSessionsSettings.from_config({}) == AgentSessionsSettings()
    assert AgentSessionsSettings.from_config({"agent_sessions": "nope"}) == AgentSessionsSettings()


def test_load_settings_uses_vault_override(fake_data_dir: Path) -> None:
    (fake_data_dir / "scout-config.yaml").write_text("agent_sessions:\n  stale_after_days: 7\n", encoding="utf-8")
    assert load_settings(fake_data_dir).stale_after_days == 7
