"""The upgrade's version stamp keeps the comments in scout-config.yaml.

``_stage_version_stamp`` rewrote the whole file through a pyyaml round-trip, so
every upgrade deleted every comment — including the one ``scoutctl budget set``
writes above the ``budget:`` block. Untouched blocks now come back verbatim.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout.scripts.bootstrap import BootstrapConfig, _dump_keeping_comments, install, upgrade
from scout.scripts.budget_config import apply_updates

PLUGIN_ROOT = Path(__file__).resolve().parents[3]


def _config(vault: Path, *, version: str = "0.4.0", email: str = "alex@example.com") -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=PLUGIN_ROOT,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email=email,
        timezone="America/New_York",
        platform="macos",
        plugin_version=version,
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    install(_config(v))
    return v


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_upgrade_keeps_the_comment_above_the_budget_block(vault: Path) -> None:
    config = vault / "scout-config.yaml"
    # `scoutctl budget set` appends the block with its explanatory comment.
    config.write_text(apply_updates(config.read_text(encoding="utf-8"), {"daily_budget_usd": 120}), encoding="utf-8")
    assert "# Written by `scoutctl budget set`; safe to edit by hand." in _lines(config)

    upgrade(_config(vault, version="0.4.1"))

    after = config.read_text(encoding="utf-8")
    assert "# Budget gating for scheduled runs — inspect with `scoutctl budget show`.\n" in after
    assert "# Written by `scoutctl budget set`; safe to edit by hand.\nbudget:\n" in after
    parsed = yaml.safe_load(after)
    assert parsed["plugin"]["version_at_last_update"] == "0.4.1"
    assert parsed["budget"]["daily_usd"] == 120


def test_upgrade_keeps_comments_throughout_the_config(vault: Path) -> None:
    config = vault / "scout-config.yaml"
    config.write_text(
        "# TestScout configuration — hand-tuned\n"
        + config.read_text(encoding="utf-8")
        + "\n# Off-peak hours, local time\n"
        "off_peak:\n"
        "  # prefer the small hours\n"
        "  start: 23\n"
        "  end: 6  # inclusive\n",
        encoding="utf-8",
    )

    upgrade(_config(vault, version="0.4.1"))

    after = config.read_text(encoding="utf-8")
    assert after.startswith("# TestScout configuration — hand-tuned\n")
    assert (
        "\n# Off-peak hours, local time\noff_peak:\n  # prefer the small hours\n  start: 23\n  end: 6  # inclusive\n"
        in after
    )
    assert yaml.safe_load(after)["plugin"]["version_at_last_update"] == "0.4.1"


def test_upgrade_keeps_the_comment_above_a_block_it_rewrites(vault: Path) -> None:
    """`plugin:` changes on every upgrade; the comment above it still survives."""
    config = vault / "scout-config.yaml"
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text.replace("\nplugin:\n", "\n# Stamped by bootstrap — do not hand-edit\nplugin:\n"), encoding="utf-8"
    )

    upgrade(_config(vault, version="0.4.1"))

    after = config.read_text(encoding="utf-8")
    assert "\n# Stamped by bootstrap — do not hand-edit\nplugin:\n" in after
    assert yaml.safe_load(after)["plugin"]["version_at_last_update"] == "0.4.1"


def test_upgrade_adds_missing_blocks_to_a_config_without_a_trailing_newline(vault: Path) -> None:
    config = vault / "scout-config.yaml"
    config.write_text("# hand-written\ntimezone: America/New_York\nextra: 1", encoding="utf-8")

    upgrade(_config(vault, version="0.4.1"))

    after = config.read_text(encoding="utf-8")
    parsed = yaml.safe_load(after)
    assert after.startswith("# hand-written\n")
    assert parsed["extra"] == 1
    assert parsed["plugin"]["version_at_last_update"] == "0.4.1"
    assert parsed["user"]["name"] == "Alex"


def test_upgrade_still_collapses_a_duplicated_block(vault: Path) -> None:
    """A duplicated top-level key cannot be rewritten block by block; the stamp
    falls back to a plain dump, which keeps the last block as PyYAML reads it."""
    config = vault / "scout-config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "plugin:\n  version_at_last_setup: 0.3.0\n", encoding="utf-8"
    )

    upgrade(_config(vault, version="0.4.1"))

    after = config.read_text(encoding="utf-8")
    assert after.count("\nplugin:\n") == 1
    parsed = yaml.safe_load(after)
    assert parsed["plugin"]["version_at_last_setup"] == "0.3.0"
    assert parsed["plugin"]["version_at_last_update"] == "0.4.1"


def test_upgrade_warns_when_it_cannot_keep_the_comments(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A document marker defeats the block scanner, so the stamp falls back to a
    plain dump. The comments are lost, as before; the upgrade must say so."""
    config = vault / "scout-config.yaml"
    config.write_text("---\n# hand-written note\n" + config.read_text(encoding="utf-8"), encoding="utf-8")

    upgrade(_config(vault, version="0.4.1"))

    assert yaml.safe_load(config.read_text(encoding="utf-8"))["plugin"]["version_at_last_update"] == "0.4.1"
    assert "scout-config.yaml comments not preserved" in capsys.readouterr().err


def test_upgrade_is_silent_when_there_were_no_comments_to_keep(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = vault / "scout-config.yaml"
    config.write_text("---\n" + config.read_text(encoding="utf-8"), encoding="utf-8")

    upgrade(_config(vault, version="0.4.1"))

    assert "comments not preserved" not in capsys.readouterr().err


def test_upgrade_does_not_duplicate_a_key_that_starts_with_a_dash(vault: Path) -> None:
    config = vault / "scout-config.yaml"
    config.write_text("-legacy: 1\n" + config.read_text(encoding="utf-8"), encoding="utf-8")

    upgrade(_config(vault, version="0.4.1"))
    upgrade(_config(vault, version="0.4.2"))

    assert config.read_text(encoding="utf-8").count("-legacy:") == 1


def test_upgrade_keeps_a_block_scalar_line_that_looks_like_a_comment(vault: Path) -> None:
    """A `|` block-scalar line that happens to start with `#` is literal content,
    not a YAML comment -- the tail-trim used while splitting unchanged blocks
    must not mistake it for a gap comment belonging to the next key (fix round 1,
    item 1/4). An unrelated key (`plugin:`) changes on every upgrade; this block
    must survive untouched, with its scalar line intact and not duplicated."""
    config = vault / "scout-config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "runbook: |\n  Steps for escalation:\n  #2 is the escalation path\n",
        encoding="utf-8",
    )

    upgrade(_config(vault, version="0.4.1"))

    after = config.read_text(encoding="utf-8")
    assert after.count("#2 is the escalation path") == 1
    assert "runbook: |\n  Steps for escalation:\n  #2 is the escalation path\n" in after
    parsed = yaml.safe_load(after)
    assert parsed["runbook"] == "Steps for escalation:\n#2 is the escalation path\n"
    assert parsed["plugin"]["version_at_last_update"] == "0.4.1"


def test_upgrade_keeps_a_block_scalar_hash_line_in_a_block_that_is_rewritten(vault: Path) -> None:
    """Item 4: the test above never exercises the bug it's named for -- its
    `runbook:` block is unchanged *and* last, so the tail-trim never runs on it.
    This one puts the hash-shaped scalar line in `user:`, which both changes
    (a new email) on this upgrade and is NOT the last key in the file, so the
    redump path and the tail-trim both actually run. Confirmed against the
    pre-fix splitter (the initial commit, 98f71f7, before any of this task's
    fix rounds): there the line showed up twice -- once correctly inside the
    redumped `user:` block, and once more as a stray orphan comment ahead of
    `instance:` (the misattributed "leading" of the next key)."""
    config = vault / "scout-config.yaml"
    text = config.read_text(encoding="utf-8")
    assert text.index("user:") < text.index("instance:")
    text = text.replace(
        "  email: alex@example.com\n",
        "  email: alex@example.com\n  bio: |\n    Steps:\n    #2 is the escalation path\n",
        1,
    )
    config.write_text(text, encoding="utf-8")

    upgrade(_config(vault, version="0.4.1", email="sam@example.com"))

    after = config.read_text(encoding="utf-8")
    assert after.count("#2 is the escalation path") == 1
    parsed = yaml.safe_load(after)
    assert parsed["user"]["email"] == "sam@example.com"
    assert parsed["user"]["bio"] == "Steps:\n#2 is the escalation path\n"


def test_upgrade_keeps_an_indented_comment_directly_above_the_next_key(vault: Path) -> None:
    """Item C: an indented (not column-0) comment sitting at the very end of a
    block that changes on every upgrade (`plugin:`) must still be attached to
    the next key it documents, not silently dropped along with the rewritten
    block it happened to trail."""
    config = vault / "scout-config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "  # about off_peak?\noff_peak:\n  start: 23\n", encoding="utf-8"
    )

    upgrade(_config(vault, version="0.4.1"))

    after = config.read_text(encoding="utf-8")
    assert "  # about off_peak?\noff_peak:\n" in after
    parsed = yaml.safe_load(after)
    assert parsed["off_peak"]["start"] == 23
    assert parsed["plugin"]["version_at_last_update"] == "0.4.1"


def test_upgrade_keeps_the_blank_line_after_user_in_the_shipped_default_config(vault: Path) -> None:
    """Item D: the real, shipped default config has a blank line between `user:`
    (rewritten here by a new email) and the comment documenting `budget:`. That
    blank line is exactly the kind of gap the tail-trim must hand to the next
    key's leading run rather than discard with the rewritten `user:` block."""
    config = vault / "scout-config.yaml"
    defaults_text = (PLUGIN_ROOT / "engine" / "scout" / "defaults" / "scout-config.yaml").read_text(encoding="utf-8")
    assert "\nuser:\n" in defaults_text and "\n\n# The four knobs" in defaults_text
    config.write_text(defaults_text, encoding="utf-8")

    upgrade(_config(vault, version="0.4.1", email="sam@example.com"))

    after = config.read_text(encoding="utf-8")
    assert "\n\n# The four knobs `scoutctl budget check` enforces before every scheduled run.\n" in after
    parsed = yaml.safe_load(after)
    assert parsed["user"]["email"] == "sam@example.com"


# --- Fix round 3 --------------------------------------------------------------------


def test_upgrade_keeps_comments_despite_a_tab_inside_block_scalar_content(vault: Path) -> None:
    """Item 3 (reviewer's `exp_tabs` case): a tab appearing deep inside a `|`
    scalar's own content (e.g. a shell command line indented past the
    scalar's content level) is just data -- it can't be structural, since the
    tab check only looks at the first `indent + 1` columns of a line's
    leading whitespace. It must not force the full-dump fallback."""
    config = vault / "scout-config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "# keep me\nrunbook: |\n  make:\n  \tcc -o x x.c\n",
        encoding="utf-8",
    )

    upgrade(_config(vault, version="0.4.1"))

    after = config.read_text(encoding="utf-8")
    assert "# keep me" in after
    parsed = yaml.safe_load(after)
    assert parsed["runbook"] == "make:\n\tcc -o x x.c\n"
    assert parsed["plugin"]["version_at_last_update"] == "0.4.1"


def test_upgrade_does_not_crash_on_a_recursive_alias_in_an_untouched_key(vault: Path) -> None:
    """Item 4: `_dump_keeping_comments` compares `original[key] == data[key]`
    with two SEPARATELY parsed copies of the same text (`original` freshly
    parsed inside the function; `data` built earlier in `_stage_version_stamp`
    from its own parse). A cyclic alias in an untouched top-level key makes
    that `==` raise RecursionError rather than return False. It must count as
    "changed" (redumping just that key), not crash the whole upgrade."""
    config = vault / "scout-config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "loop: &l\n  self: *l\n", encoding="utf-8")

    upgrade(_config(vault, version="0.4.1"))  # must not raise RecursionError

    after = config.read_text(encoding="utf-8")
    parsed = yaml.safe_load(after)
    assert parsed["loop"] is parsed["loop"]["self"]
    assert parsed["plugin"]["version_at_last_update"] == "0.4.1"


def test_dump_keeping_comments_falls_back_if_the_rebuild_does_not_parse(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Defense in depth, mirroring custom_connectors' own analogous test: even
    if the per-key redump ever produced text that isn't valid YAML at all,
    `_dump_keeping_comments` must not raise -- it falls back to a plain dump
    and warns, rather than propagating a YAMLError."""
    import scout.scripts.bootstrap as bootstrap_mod

    monkeypatch.setattr(bootstrap_mod.yaml, "safe_dump", lambda data, **kw: "x: [unterminated\n")

    result = _dump_keeping_comments("# a comment\na: 1\n", {"a": 2})  # "a" changed -> triggers the redump

    assert result == "x: [unterminated\n"
    assert "did not parse" in capsys.readouterr().err


def test_dump_keeping_comments_removed_key_does_not_absorb_into_the_previous_scalar() -> None:
    """Item 2 on the bootstrap side: `_stage_version_stamp` never actually
    drops a top-level key through the public `upgrade()` entry point (it only
    adds/overwrites), so this calls `_dump_keeping_comments` directly to
    exercise the key-removal boundary it still has to handle correctly.
    Removing `beta` means `gamma`'s comment -- deep enough to be absorbed by
    `alpha`'s scalar once it's gamma's new (not its original) predecessor --
    must be protected, not silently swallowed into alpha's value."""
    text = "alpha:\n  note: |\n    Watch for changes.\nbeta:\n  x: 1\n    # About gamma\ngamma:\n  y: 2\n"
    data = {"alpha": {"note": "Watch for changes.\n"}, "gamma": {"y": 2}}  # beta dropped

    result = _dump_keeping_comments(text, data)

    assert "# About gamma" in result
    assert yaml.safe_load(result) == data
