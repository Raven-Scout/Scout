"""privacy.snapshot.json is generated from defaults/privacy.yaml, never hand-edited (#453, spec §5.1)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

import scout
from scout.kb.privacy import load_policy
from scout.scripts import privacy_snapshot as snap

_ENGINE = Path(__file__).resolve().parents[2]
_YAML = _ENGINE / "scout" / "defaults" / "privacy.yaml"
_COMMITTED = _ENGINE / "scout" / "privacy.snapshot.json"


def test_canonical_path_is_the_committed_file() -> None:
    assert snap.canonical_snapshot_path() == _COMMITTED


def test_snapshot_matches_yaml(tmp_path: Path) -> None:
    """Regenerate in tmp and compare bytes with the committed snapshot.

    ``engine_version`` is pinned to the committed value first: a release bumps
    the version without regenerating, and that alone isn't policy drift.
    """
    committed = _COMMITTED.read_text(encoding="utf-8")
    pinned = json.loads(committed)["engine_version"]
    target = tmp_path / "privacy.snapshot.json"
    snap.write_snapshot(target, engine_version=pinned)
    assert target.read_bytes() == _COMMITTED.read_bytes()


def test_snapshot_shape() -> None:
    data = json.loads(_COMMITTED.read_text(encoding="utf-8"))
    assert set(data) == {"schema_version", "engine_version", "policy"}
    assert data["schema_version"] == 1
    assert data["policy"] == {k: v for k, v in load_policy(None).to_json().items() if k != "warnings"}


def test_snapshot_policy_reflects_raw_yaml() -> None:
    """Read the YAML independently so a loader bug can't hide in both layers."""
    raw = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    policy = json.loads(_COMMITTED.read_text(encoding="utf-8"))["policy"]
    assert raw["schema_version"] == 1
    assert policy["local_only"] == raw["local_only"]
    assert policy["visibility_defaults"] == raw["visibility_defaults"]
    assert policy["shared_files"] == raw["shared_files"]
    assert policy["surfaces"] == {k: sorted(v) for k, v in raw["surfaces"].items()}


def test_serialize_is_sorted_with_trailing_newline() -> None:
    text = snap.serialize({"b": 1, "a": {"d": 2, "c": 3}})
    assert text == '{\n  "a": {\n    "c": 3,\n    "d": 2\n  },\n  "b": 1\n}\n'


def test_build_snapshot_defaults_to_engine_version() -> None:
    assert snap.build_snapshot()["engine_version"] == scout.__version__


def test_check_ignores_engine_version_only(tmp_path: Path) -> None:
    target = tmp_path / "s.json"
    snap.write_snapshot(target, engine_version="0.0.1")
    assert snap.check_snapshot(target) == (True, "")
    data = json.loads(target.read_text(encoding="utf-8"))
    data["policy"]["shared_files"].append("outbox/**")
    target.write_text(snap.serialize(data), encoding="utf-8")
    ok, diff = snap.check_snapshot(target)
    assert not ok
    assert "outbox/**" in diff


def test_check_missing_target(tmp_path: Path) -> None:
    ok, diff = snap.check_snapshot(tmp_path / "nope.json")
    assert not ok
    assert "does not exist" in diff


def test_check_unreadable_json_is_drift(tmp_path: Path) -> None:
    for body in ("not json\n", "[1, 2]\n"):
        target = tmp_path / "s.json"
        target.write_text(body, encoding="utf-8")
        ok, diff = snap.check_snapshot(target)
        assert not ok
        assert diff


def test_main_write_and_check(tmp_path: Path, capsys) -> None:
    target = tmp_path / "out" / "privacy.snapshot.json"
    assert snap.main(["--target", str(target)]) == 0
    assert target.exists()
    assert snap.main(["--check", "--target", str(target)]) == 0
    assert "OK" in capsys.readouterr().out
    target.write_text("{}\n", encoding="utf-8")
    assert snap.main(["--check", "--target", str(target)]) == 1
    assert "Drift detected" in capsys.readouterr().err
