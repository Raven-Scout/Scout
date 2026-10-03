"""Unit tests for scout.sessions._atomic — the one temp-file + os.replace helper (spec §4.12)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from scout.sessions._atomic import atomic_write_text


def test_atomic_write_text_replaces_via_a_unique_0600_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "sub" / "out.json"
    sources: list[str] = []
    real_replace = os.replace

    def spy(src: str, dst: str) -> None:
        sources.append(os.fspath(src))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    atomic_write_text(target, "one")
    atomic_write_text(target, "two", fsync=True)
    assert target.read_text(encoding="utf-8") == "two"
    assert len(set(sources)) == 2  # unique per call: concurrent writers never share a temp file
    for src in sources:
        assert Path(src).parent == target.parent and Path(src).name.startswith(".out.json.") and src.endswith(".tmp")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(target.parent.glob("*.tmp"))


def test_atomic_write_text_raises_and_removes_the_temp_on_failure(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    target.mkdir()  # os.replace onto a directory fails
    with pytest.raises(OSError):
        atomic_write_text(target, "x")
    assert not list(tmp_path.glob("*.tmp"))
