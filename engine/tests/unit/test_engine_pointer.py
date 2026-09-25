"""Unit tests for engine/scout/scripts/engine_pointer.py (spec §4.2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import scout
from scout import __version__
from scout.scripts.engine_pointer import (
    MANAGED_BY_VALUES,
    POINTER_SCHEMA_VERSION,
    EnginePointer,
    current_pointer,
    pointer_path,
    read_pointer,
    resolve_managed_by,
    write_pointer,
)


def test_pointer_path_is_xdg_state(tmp_path):
    assert pointer_path(tmp_path) == tmp_path / ".local" / "state" / "scout" / "engine.json"


def test_current_pointer_describes_the_running_engine(tmp_path):
    p = current_pointer(vault=tmp_path / "Scout", managed_by="scout-app")
    assert p.version == __version__
    assert p.engine_root == str(Path(scout.__file__).parent.parent.parent)
    assert p.python == str(Path(sys.executable).absolute())
    assert p.scoutctl == str(Path(sys.executable).absolute().parent / "scoutctl")
    assert p.vault == str(tmp_path / "Scout")
    assert p.managed_by == "scout-app"
    assert p.written_at.endswith("Z")
    assert p.schema_version == POINTER_SCHEMA_VERSION


def test_write_then_read_round_trips(tmp_path):
    p = current_pointer(vault=tmp_path / "Scout", managed_by="install.sh")
    written = write_pointer(p, home=tmp_path)
    assert written == pointer_path(tmp_path)
    raw = json.loads(written.read_text())
    assert raw["schema_version"] == 1
    assert raw["managed_by"] == "install.sh"
    assert read_pointer(home=tmp_path) == p


def test_read_pointer_returns_none_when_missing_or_malformed(tmp_path):
    assert read_pointer(home=tmp_path) is None
    pointer_path(tmp_path).parent.mkdir(parents=True)
    pointer_path(tmp_path).write_text("{not json")
    assert read_pointer(home=tmp_path) is None
    pointer_path(tmp_path).write_text(json.dumps({"schema_version": 99, "version": "x"}))
    assert read_pointer(home=tmp_path) is None


def test_write_is_atomic_no_tmp_left_behind(tmp_path):
    write_pointer(current_pointer(vault=tmp_path / "Scout", managed_by="dev"), home=tmp_path)
    leftovers = [p for p in pointer_path(tmp_path).parent.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_read_pointer_returns_none_when_a_required_field_is_missing(tmp_path):
    """schema_version matches but the payload is otherwise incomplete — e.g. a
    hand-edited or truncated file. Still "no pointer", never raises."""
    pointer_path(tmp_path).parent.mkdir(parents=True)
    incomplete = {"schema_version": 1, "version": "0.4.0"}  # missing every other field
    pointer_path(tmp_path).write_text(json.dumps(incomplete))
    assert read_pointer(home=tmp_path) is None


# --- resolve_managed_by: the `preserve` default (final review, Ruling 15) ----


def _pointer_for(python: str, managed_by: str, home: Path) -> None:
    write_pointer(
        EnginePointer(
            version="0.10.0",
            engine_root="/nonexistent",
            python=python,
            scoutctl=str(Path(python).parent / "scoutctl"),
            vault=str(home / "Scout"),
            managed_by=managed_by,
            written_at="2026-01-01T00:00:00Z",
        ),
        home=home,
    )


def test_preserve_keeps_the_managers_value_when_the_pointer_is_this_engine(tmp_path):
    """A plain `scoutctl bootstrap upgrade` (the doctor's own fix hint) run by
    the app's venv must not demote the engine to `unknown`."""
    _pointer_for(str(Path(sys.executable).absolute()), "scout-app", tmp_path)
    assert resolve_managed_by("preserve", home=tmp_path) == "scout-app"


def test_preserve_is_unknown_when_the_pointer_names_another_interpreter(tmp_path):
    _pointer_for("/somewhere/else/bin/python", "scout-app", tmp_path)
    assert resolve_managed_by("preserve", home=tmp_path) == "unknown"


def test_preserve_is_unknown_without_a_pointer(tmp_path):
    assert resolve_managed_by("preserve", home=tmp_path) == "unknown"


def test_explicit_managed_by_passes_through_and_invalid_raises(tmp_path):
    _pointer_for(str(Path(sys.executable).absolute()), "scout-app", tmp_path)
    for value in MANAGED_BY_VALUES:
        assert resolve_managed_by(value, home=tmp_path) == value
    with pytest.raises(ValueError, match="bogus"):
        resolve_managed_by("bogus", home=tmp_path)
