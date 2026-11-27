"""scout.layout: the path table, layout detection from disk, the README (spec 2026-11-26 §3, §4.1, §6)."""

from __future__ import annotations

from pathlib import Path

from scout import config, layout


def test_every_key_has_a_v1_value_and_v2_paths_live_in_five_categories() -> None:
    assert set(layout.V1.paths) == set(layout.V2.paths)
    tops = {v.split("/", 1)[0] for v in layout.V2.paths.values()}
    known = {t for group in layout.CATEGORIES.values() for t in group} | {"action-items", "drafts", "reports"}
    assert tops <= known | {"inbox"}


def test_helpers() -> None:
    assert layout.brain_path("SKILL") == "scout/brain/SKILL.md"
    assert layout.runner_path("run-scout.sh") == "scout/runners/run-scout.sh"
    assert layout.script_path("scoutctl") == "scout/scripts/scoutctl"
    assert layout.record_path("session_log") == "scout/records/session-log.md"
    assert layout.space_dir("svj", "meetings") == "knowledge-base/svj/meetings"
    assert layout.legacy_rel("inbox") == "inbox.md"
    assert layout.V1.brain("SKILL") == "SKILL.md" and layout.V1.runner("run-scout.sh") == "run-scout.sh"
    assert layout.legacy_of("scout/scripts/heartbeat.sh") == "scripts/heartbeat.sh"
    assert layout.legacy_of("scout/runners/run-scout.sh") == "run-scout.sh"
    assert layout.legacy_of("knowledge-base/ontology/parser.py") is None


def test_detection_is_from_disk(tmp_path: Path) -> None:
    v = tmp_path / "v"
    v.mkdir()
    assert layout.layout_version(v) == 1  # nothing yet
    (v / "SKILL.md").write_text("x")
    (v / "scout" / "brain").mkdir(parents=True)
    assert layout.layout_version(v) == 1  # a real root SKILL.md: not moved yet
    (v / "scout" / "brain" / "SKILL.md").write_text("x")
    (v / "SKILL.md").unlink()
    (v / "SKILL.md").symlink_to("scout/brain/SKILL.md")
    assert layout.layout_version(v) == 2  # the compatibility link isn't a root brain file
    assert layout.for_vault(v) is layout.V2


def test_a_legacy_vault_reads_as_v1(tmp_path: Path) -> None:
    (tmp_path / ".scout-state").mkdir()
    assert layout.layout_version(tmp_path) == 1


def test_migration_state(tmp_path: Path) -> None:
    v = tmp_path
    assert layout.migration_state(v) == "pending"
    (v / layout.DEFERRAL_REL).parent.mkdir(parents=True)
    (v / layout.DEFERRAL_REL).write_text("{}")
    assert layout.migration_state(v) == "deferred"
    (v / layout.JOURNAL_REL).write_text("{}")
    assert layout.migration_state(v) == "interrupted" and layout.move_in_progress(v)


def test_the_readme_names_the_spaces_and_the_default() -> None:
    text = layout.render_readme(config.DEFAULT_SPACES, engine_version="9.9.9")
    assert text.startswith(layout.README_MARKER)
    assert "`knowledge-base/work/`: the work space (the default)" in text
    assert "`knowledge-base/personal/`: the personal space, private by default" in text
    assert "`inbox/inbox.md`" in text and "Scout 9.9.9" in text


def test_the_space_registry() -> None:
    import pytest

    from scout.errors import ConfigError

    assert config.parse_spaces(None) == config.DEFAULT_SPACES
    spaces = config.parse_spaces([{"key": "work", "default": True}, {"key": "svj", "visibility": "private"}])
    assert config.default_space(spaces).key == "work" and spaces[1].visibility == "private"
    for bad in (
        [{"key": "work"}],  # no default
        [{"key": "a", "default": True}, {"key": "b", "default": True}],
        [{"key": "projects", "default": True}],  # a Scout-wide or v1 folder
        [{"key": "Work", "default": True}],
        [{"key": "w", "default": True, "visibility": "secret"}],
        [{"key": "w", "default": True}, {"key": "w"}],
        "work",
    ):
        with pytest.raises(ConfigError):
            config.parse_spaces(bad)


def test_a_stray_real_root_brain_file_does_not_flip_a_moved_vault(tmp_path: Path) -> None:
    """safe_write replaces a compatibility link with a file; the vault stays v2 and the doctor reports it."""
    v = tmp_path
    (v / "scout" / "brain").mkdir(parents=True)
    (v / "scout" / "brain" / "SKILL.md").write_text("x")
    (v / "SKILL.md").write_text("stray")
    assert layout.layout_version(v) == 2
    from scout.scripts.bootstrap_doctor import _check_layout

    _errors, warnings = _check_layout(vault=v, home=tmp_path / "home")
    assert any("real file again" in w for w in warnings)
