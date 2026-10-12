"""The base-arm cache key and store (spec 2026-10-12 §8)."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from tests.eval.harness.build import PLUGIN_ROOT
from tests.eval.harness.cache import BaselineCache, cache_key, content_hash
from tests.eval.harness.checkout import Checkout
from tests.eval.harness.scenario import load_scenario


@pytest.fixture(scope="module")
def arm(tmp_path_factory: pytest.TempPathFactory) -> Checkout:
    root = tmp_path_factory.mktemp("arm")
    for sub in ("phases", "templates", "engine/scout"):
        shutil.copytree(PLUGIN_ROOT / sub, root / "plugin" / sub, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "plugin" / "README.md").write_text("docs")
    return Checkout(label="base", root=root, commit="a" * 40, pythonpath=root / "plugin" / "engine")


@pytest.fixture(scope="module")
def busy():
    return load_scenario("busy-weekday")


def _key(arm: Checkout, busy, model: str = "sonnet", cli: str = "2.1.278", weekday: int = 0) -> str:
    return cache_key(arm, busy, model=model, cli_version=cli, weekday=weekday)


def test_content_hash_is_stable_and_ignores_caches(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x")
    first = content_hash([tmp_path])
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.pyc").write_bytes(b"\x00")
    assert content_hash([tmp_path]) == first
    (tmp_path / "a.md").write_text("y")
    assert content_hash([tmp_path]) != first


@pytest.mark.parametrize(
    "rel", ["phases/core/00-run-modes.md", "templates/run-scout.sh.tmpl", "engine/scout/__init__.py"]
)
def test_a_change_to_anything_that_shapes_the_run_changes_the_key(arm: Checkout, busy, rel: str) -> None:
    before = _key(arm, busy)
    target = arm.plugin_root / rel
    original = target.read_text()
    try:
        target.write_text(original + "\n# changed\n")
        assert _key(arm, busy) != before
    finally:
        target.write_text(original)
    assert _key(arm, busy) == before


def test_model_cli_and_scenario_change_the_key(arm: Checkout, busy, tmp_path: Path) -> None:
    before = _key(arm, busy)
    assert _key(arm, busy, model="opus") != before
    assert _key(arm, busy, cli="2.1.279") != before
    other = tmp_path / "busy-copy"
    shutil.copytree(busy.path, other)
    assert _key(arm, replace(busy, path=other)) == before  # same content, different place
    (other / "scenario.yaml").write_text((other / "scenario.yaml").read_text() + "\n# changed\n")
    assert _key(arm, replace(busy, path=other)) != before


def test_files_that_dont_shape_the_run_leave_the_key_alone(arm: Checkout, busy) -> None:
    before = _key(arm, busy)
    readme = arm.plugin_root / "README.md"
    readme.write_text("other docs")
    assert _key(arm, busy) == before


def test_the_store_keeps_samples_per_key(tmp_path: Path) -> None:
    sample = tmp_path / "busy-weekday-base-20261012-1"
    sample.mkdir()
    (sample / "meta.json").write_text('{"scenario": "busy-weekday", "rep": 1, "aborted": null, "arm": "base"}')
    (sample / "checks.json").write_text("[]")
    cache = BaselineCache(tmp_path / "cache")
    assert cache.samples("k1") == []
    stored = cache.add("k1", sample)
    assert stored.parent == tmp_path / "cache" / "baselines" / "k1"
    [loaded] = cache.samples("k1")
    assert (loaded.scenario, loaded.rep, loaded.result_dir) == ("busy-weekday", 1, stored)
    assert cache.samples("k2") == []


def test_the_weekday_is_part_of_the_key(arm: Checkout, busy) -> None:
    # Phases branch on the day (Monday preview, weekend scope): a Monday base isn't a Tuesday head's baseline.
    assert _key(arm, busy, weekday=0) != _key(arm, busy, weekday=1)
    assert _key(arm, busy, weekday=0) == _key(arm, busy, weekday=0)


def _stored_sample(tmp_path: Path, name: str, started: str) -> Path:
    d = tmp_path / name
    d.mkdir()
    meta = {"scenario": "busy-weekday", "rep": 1, "aborted": None, "started_at": started}
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "checks.json").write_text("[]")
    return d


def test_samples_come_back_newest_first_and_clear_empties_a_key(tmp_path: Path) -> None:
    cache = BaselineCache(tmp_path / "cache")
    cache.add("k", _stored_sample(tmp_path, "s-a", "2026-10-01T09:00:00+0000"))
    cache.add("k", _stored_sample(tmp_path, "s-b", "2026-10-12T09:00:00+0000"))
    assert [s.meta["started_at"][:10] for s in cache.samples("k")] == ["2026-10-12", "2026-10-01"]
    cache.clear("k")
    assert cache.samples("k") == []
