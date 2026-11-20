"""The upgrade says once per run that scheduled runs may now edit shared notes (privacy.shared_edits)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.bootstrap import SHARED_EDITS_NOTICE, _stage_shared_edits_notice

SHARING_ON = "privacy:\n  federation: {enabled: true}\n"


@pytest.mark.parametrize(
    ("config", "said"),
    [
        pytest.param(SHARING_ON, True, id="sharing on, mode unset"),
        pytest.param(SHARING_ON + "  shared_edits: checked\n", False, id="checked chosen"),
        pytest.param(SHARING_ON + "  shared_edits: refuse\n", False, id="refuse chosen"),
        pytest.param("privacy:\n  federation: {enabled: false}\n", False, id="sharing off"),
        pytest.param("user:\n  timezone: UTC\n", False, id="no privacy block"),
        pytest.param("user: [unclosed\n", False, id="unreadable"),
    ],
)
def test_the_notice(tmp_path: Path, capsys: pytest.CaptureFixture[str], config: str, said: bool) -> None:
    (tmp_path / "scout-config.yaml").write_text(config, encoding="utf-8")
    _stage_shared_edits_notice(tmp_path)
    err = capsys.readouterr().err
    assert (SHARED_EDITS_NOTICE in err) is said
    if said:
        assert "shared_edits: refuse" in err


def test_the_notice_never_edits_the_vault(tmp_path: Path) -> None:
    config = tmp_path / "scout-config.yaml"
    config.write_text(SHARING_ON, encoding="utf-8")
    _stage_shared_edits_notice(tmp_path)
    assert config.read_text(encoding="utf-8") == SHARING_ON
    assert sorted(p.name for p in tmp_path.iterdir()) == ["scout-config.yaml"]
