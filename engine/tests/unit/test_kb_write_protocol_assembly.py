"""The write protocol must land in every assembled brain (SKILL/DREAMING/RESEARCH)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.bootstrap import BootstrapConfig, _assemble

PLUGIN_ROOT = Path(__file__).resolve().parents[3]


def _cfg(tmp_path: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=tmp_path / "Scout",
        plugin_root=PLUGIN_ROOT,
        instance_name="Scout",
        instance_name_lower="scout",
        user_name="Sam",
        user_email="sam@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


@pytest.mark.parametrize("kind", ["SKILL", "DREAMING", "RESEARCH"])
def test_write_protocol_in_every_brain(kind: str, tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path), kind)
    assert "KB WRITE PROTOCOL" in text
    assert "knowledge-base/topics/" in text and "knowledge-base/sources/" in text


def test_dreaming_has_shrink_pass(tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path), "DREAMING")
    assert "Step 2a-shrink" in text and "scoutctl kb lint --report" in text


def test_shrink_pass_never_targets_daily_files_or_legacy_session_log(tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path), "DREAMING")
    shrink = text[text.index("Step 2a-shrink") : text.index("### Step 2b")]
    assert "Never shrink a daily action-items file" in shrink
    assert "`knowledge-base/session-log.md`" in shrink


def test_dreaming_session_log_row_is_one_line(tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path), "DREAMING")
    assert "| Date | Time | Mode | Summary |" not in text


@pytest.mark.parametrize("kind", ["SKILL", "DREAMING", "RESEARCH"])
def test_no_stale_recent_sessions_references(kind: str, tmp_path: Path) -> None:
    text = _assemble(_cfg(tmp_path), kind)
    assert "Recent Sessions table in `knowledge-base.md`" not in text
    assert "`knowledge-base.md` Recent Sessions" not in text


def test_digest_stays_a_capped_section_in_the_daily_file(tmp_path: Path) -> None:
    """The apps build their Digest view from a `📋` H2 in the daily file (spec goal 4)."""
    for kind in ("SKILL", "DREAMING"):
        text = _assemble(_cfg(tmp_path), kind)
        assert "action-items/digests/" not in text
        assert "## 📋 Scout Digest — " in text
        assert "bottom of today's action-items file" in text
        assert "6 KB" in text
    skill = _assemble(_cfg(tmp_path), "SKILL")
    fixed = skill[skill.index("**Fixed sections, in order:**") :].split("\n", 1)[0]
    assert fixed.index("`## 📋 Scout Digest`") < fixed.index("`## 🪵 Run notes")
