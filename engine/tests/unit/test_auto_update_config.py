"""`scoutctl config set-auto-update` and the writer behind it.

/scout-update's auto-update nudge used to be an inline pyyaml round-trip
(``safe_load`` → set two keys → ``safe_dump``) that deleted every comment in the
vault's scout-config.yaml — the note `scoutctl budget set` writes above
``budget:``, and anything written by hand. These tests pin the replacement: only
the ``auto_update:`` block changes, every other byte stays.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from scout.cli import app
from scout.scripts import auto_update_config
from scout.scripts.auto_update_config import (
    AutoUpdateWriteError,
    apply_auto_update,
    write_auto_update,
)

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[3]

# A vault config the way it looks after a few months: bootstrap state, a
# `budget set` block with its comment, hand-written notes inside and between
# blocks, an inline comment.
_VAULT_CONFIG = """\
# Scout vault config — hand-edited and bootstrap-written.
user:
  name: Alex
  # Work address; personal mail goes elsewhere.
  email: alex@example.com

# Budget gating for scheduled runs — inspect with `scoutctl budget show`.
# Written by `scoutctl budget set`; safe to edit by hand.
budget:
  daily_usd: 150  # calibrated from tracked sessions
  window_hours: 5

# Whether Scout keeps itself up to date. Set during /scout-setup; toggle via
# /scout-update.
auto_update:
  enabled: false
  channel: stable

# Agent-session index — a separate key from the legacy `sessions:` budget block.
agent_sessions:
  stale_after_days: 3

plugin:
  version_at_last_setup: 0.11.0
  applied_migrations: []
"""

_AUTO_UPDATE_BLOCK = """\
# Whether Scout keeps itself up to date. Set during /scout-setup; toggle via
# /scout-update.
auto_update:
  enabled: false
  channel: stable

"""

_WITHOUT_BLOCK = _VAULT_CONFIG.replace(_AUTO_UPDATE_BLOCK, "")


def _enabled(text: str) -> str:
    return text.replace("  enabled: false\n", "  enabled: true\n")


def _block(text: str) -> object:
    return yaml.safe_load(text)["auto_update"]


# ----- apply_auto_update (pure) ---------------------------------------------


def test_enabling_rewrites_only_the_enabled_line() -> None:
    assert apply_auto_update(_VAULT_CONFIG, enabled=True) == _enabled(_VAULT_CONFIG)


def test_disabling_rewrites_only_the_enabled_line() -> None:
    on = _enabled(_VAULT_CONFIG)
    assert apply_auto_update(on, enabled=False) == _VAULT_CONFIG


def test_appends_a_complete_block_when_absent() -> None:
    result = apply_auto_update(_WITHOUT_BLOCK, enabled=True)

    assert result.startswith(_WITHOUT_BLOCK)  # every existing byte, in place
    tail = result[len(_WITHOUT_BLOCK) :]
    assert tail.startswith("\n# ")  # separated, and explains itself
    assert result.count("auto_update:") == 1
    assert _block(result) == {"enabled": True, "channel": "stable"}


def test_inserts_a_missing_channel_inside_the_block() -> None:
    """The new line lands under `enabled:`, not after the comment that belongs
    to the next top-level key."""
    text = _VAULT_CONFIG.replace("  channel: stable\n", "")
    assert apply_auto_update(text, enabled=True) == _enabled(_VAULT_CONFIG)


def test_fills_an_empty_block() -> None:
    text = "user:\n  name: Alex\nauto_update:\nplugin:\n  applied_migrations: []\n"
    result = apply_auto_update(text, enabled=True)
    assert result == (
        "user:\n  name: Alex\nauto_update:\n  enabled: true\n  channel: stable\nplugin:\n  applied_migrations: []\n"
    )


def test_keeps_a_channel_already_set() -> None:
    """Without --channel the opt-in leaves an existing channel alone, the way
    the old snippet's ``setdefault("channel", "stable")`` did."""
    text = _VAULT_CONFIG.replace("  channel: stable\n", "  channel: beta\n")
    assert apply_auto_update(text, enabled=True) == _enabled(text)


def test_channel_only_on_an_absent_block_writes_enabled_false() -> None:
    result = apply_auto_update(_WITHOUT_BLOCK, channel="stable")
    assert _block(result) == {"enabled": False, "channel": "stable"}


def test_keeps_an_inline_comment_on_the_rewritten_line() -> None:
    text = _VAULT_CONFIG.replace("  enabled: false\n", "  enabled: false  # pending review\n")
    result = apply_auto_update(text, enabled=True)
    assert result == text.replace("  enabled: false  # pending review\n", "  enabled: true  # pending review\n")


def test_keeps_other_keys_inside_the_block() -> None:
    text = _VAULT_CONFIG.replace("  channel: stable\n", "  channel: stable\n  # Ping on conflict.\n  notify: true\n")
    result = apply_auto_update(text, enabled=True)
    assert result == _enabled(text)


def test_is_idempotent() -> None:
    once = apply_auto_update(_WITHOUT_BLOCK, enabled=True)
    assert apply_auto_update(once, enabled=True) == once


def test_returns_the_text_untouched_when_already_set() -> None:
    on = _enabled(_VAULT_CONFIG)
    assert apply_auto_update(on, enabled=True) == on


def test_rewrites_a_flow_style_block_and_nothing_else() -> None:
    text = _VAULT_CONFIG.replace(
        "auto_update:\n  enabled: false\n  channel: stable\n",
        "auto_update: {enabled: false, channel: stable}\n",
    )
    result = apply_auto_update(text, enabled=True)

    head, tail = text.split("auto_update: {enabled: false, channel: stable}\n")
    assert result.startswith(head)
    assert result.endswith(tail)
    assert _block(result) == {"enabled": True, "channel": "stable"}


def test_rewrites_a_quoted_child_key_instead_of_duplicating_it() -> None:
    """pyyaml keeps the last of two duplicate keys, so a second `enabled:` line
    would pass the load-back check while the quoted one goes stale."""
    text = 'user:\n  name: Alex\nauto_update:\n  "enabled": false\n  channel: stable\n'
    result = apply_auto_update(text, enabled=True)

    assert result.startswith("user:\n  name: Alex\n")
    assert result.count("enabled") == 1
    assert _block(result) == {"enabled": True, "channel": "stable"}


def test_keeps_a_comment_that_opens_the_block() -> None:
    text = "auto_update:\n  # Flipped by /scout-update.\n  enabled: false\n"
    assert apply_auto_update(text, enabled=True) == (
        "auto_update:\n  # Flipped by /scout-update.\n  enabled: true\n  channel: stable\n"
    )


def test_rewrites_the_block_when_a_line_edit_would_not_parse() -> None:
    """`enabled:` holding a nested mapping: rewriting that line alone leaves its
    children dangling, so the block is re-dumped instead."""
    text = "user:\n  name: Alex\nauto_update:\n  enabled:\n    nested: 1\n  channel: stable\n"
    result = apply_auto_update(text, enabled=True)
    assert result == "user:\n  name: Alex\nauto_update:\n  enabled: true\n  channel: stable\n"


def test_rejects_an_edit_a_second_quoted_block_would_override() -> None:
    """pyyaml keeps the later, quoted `auto_update`, so no edit to the plain one
    can take effect; nothing is written rather than a no-op claimed as done."""
    text = 'auto_update:\n  enabled: false\n"auto_update": {enabled: false}\n'
    with pytest.raises(AutoUpdateWriteError, match="disturbing"):
        apply_auto_update(text, enabled=True)


def test_rejects_a_block_it_cannot_locate() -> None:
    with pytest.raises(AutoUpdateWriteError, match="locate"):
        apply_auto_update('"auto_update":\n  enabled: false\n', enabled=True)


def test_rejects_an_append_that_would_change_a_trailing_block_scalar() -> None:
    """With no final newline, a `|` scalar ends without one; appending after it
    would add a newline to that value."""
    with pytest.raises(AutoUpdateWriteError, match="change other settings"):
        apply_auto_update("notes: |\n  hello", enabled=True)


def test_ignores_an_auto_update_key_under_another_parent() -> None:
    text = "features:\n  auto_update: true\n"
    result = apply_auto_update(text, enabled=True)

    assert result.startswith(text)
    loaded = yaml.safe_load(result)
    assert loaded["features"] == {"auto_update": True}
    assert loaded["auto_update"] == {"enabled": True, "channel": "stable"}


def test_writes_into_an_empty_file() -> None:
    result = apply_auto_update("", enabled=True)
    assert not result.startswith("\n")
    assert yaml.safe_load(result) == {"auto_update": {"enabled": True, "channel": "stable"}}


def test_keeps_a_comment_only_file() -> None:
    text = "# Notes for later.\n"
    result = apply_auto_update(text, enabled=True)
    assert result.startswith(text)
    assert _block(result) == {"enabled": True, "channel": "stable"}


def test_handles_a_missing_final_newline() -> None:
    result = apply_auto_update("auto_update:\n  enabled: false", enabled=True)
    assert result == "auto_update:\n  enabled: true\n  channel: stable\n"


def test_handles_a_document_start_marker() -> None:
    text = "---\n" + _VAULT_CONFIG
    assert apply_auto_update(text, enabled=True) == _enabled(text)


def test_rejects_an_unknown_channel() -> None:
    with pytest.raises(AutoUpdateWriteError, match="nightly"):
        apply_auto_update(_VAULT_CONFIG, channel="nightly")


def test_rejects_unparseable_yaml() -> None:
    with pytest.raises(AutoUpdateWriteError, match="parse"):
        apply_auto_update("user: [unclosed\n", enabled=True)


def test_rejects_a_config_that_is_not_a_mapping() -> None:
    with pytest.raises(AutoUpdateWriteError, match="mapping"):
        apply_auto_update("- just\n- a list\n", enabled=True)


def test_rejects_a_duplicated_block() -> None:
    text = "auto_update:\n  enabled: false\nauto_update:\n  enabled: false\n"
    with pytest.raises(AutoUpdateWriteError, match="more than once"):
        apply_auto_update(text, enabled=True)


# ----- write_auto_update ------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A vault holding _VAULT_CONFIG, with SCOUT_DATA_DIR pointed at it."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / "scout-config.yaml").write_text(_VAULT_CONFIG, encoding="utf-8")
    return tmp_path


def test_write_persists_and_reports(vault: Path) -> None:
    payload = write_auto_update(enabled=True, data_dir=vault)

    assert (vault / "scout-config.yaml").read_text(encoding="utf-8") == _enabled(_VAULT_CONFIG)
    assert payload == {
        "config_path": str(vault / "scout-config.yaml"),
        "enabled": True,
        "channel": "stable",
        "changed": True,
    }


def test_write_leaves_the_file_alone_when_already_set(vault: Path) -> None:
    config_path = vault / "scout-config.yaml"
    config_path.write_text(_enabled(_VAULT_CONFIG), encoding="utf-8")
    before = config_path.stat().st_mtime_ns

    payload = write_auto_update(enabled=True, data_dir=vault)

    assert payload["changed"] is False
    assert config_path.stat().st_mtime_ns == before


def test_write_refuses_a_vault_without_a_config(tmp_path: Path) -> None:
    """Creating scout-config.yaml here would make `bootstrap upgrade` mistake the
    directory for an installed vault."""
    with pytest.raises(AutoUpdateWriteError, match="/scout-setup"):
        write_auto_update(enabled=True, data_dir=tmp_path)
    assert not (tmp_path / "scout-config.yaml").exists()


def test_write_reports_a_config_it_cannot_read(vault: Path) -> None:
    config_path = vault / "scout-config.yaml"
    config_path.unlink()
    config_path.mkdir()
    with pytest.raises(AutoUpdateWriteError, match="cannot read"):
        write_auto_update(enabled=True, data_dir=vault)


def test_write_reports_a_config_that_is_not_utf8(vault: Path) -> None:
    (vault / "scout-config.yaml").write_bytes(b"user:\n  name: \xff\n")
    with pytest.raises(AutoUpdateWriteError, match="cannot read"):
        write_auto_update(enabled=True, data_dir=vault)


def test_write_reports_an_unwritable_vault(vault: Path) -> None:
    vault.chmod(0o500)  # the tmp file cannot be created beside the config
    try:
        with pytest.raises(AutoUpdateWriteError, match="cannot write"):
            write_auto_update(enabled=True, data_dir=vault)
    finally:
        vault.chmod(0o700)
    assert (vault / "scout-config.yaml").read_text(encoding="utf-8") == _VAULT_CONFIG
    assert [p.name for p in vault.iterdir() if p.name.endswith(".tmp")] == []


def test_a_vanished_file_has_no_mtime(tmp_path: Path) -> None:
    """The guard compares mtimes; a file removed mid-write must read as changed,
    not raise."""
    assert auto_update_config._mtime_ns(tmp_path / "gone.yaml") is None


def test_write_leaves_no_tmp_files(vault: Path) -> None:
    write_auto_update(enabled=True, data_dir=vault)
    assert [p.name for p in vault.iterdir() if p.name.endswith(".tmp")] == []


def test_write_retries_when_a_concurrent_writer_lands(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """scout-config.yaml has several producers (bootstrap's version stamp,
    `budget set`, the macOS app). A write that lands between our read and our
    replace must be re-read and kept, not clobbered.

    Driven through `_mtime_ns`, as in the budget writer's test: the second call
    is the pre-replace check, so making it disagree exercises the retry.
    """
    config_path = vault / "scout-config.yaml"
    calls = {"n": 0}
    real_mtime_ns = auto_update_config._mtime_ns

    def mtime_with_one_collision(path: Path) -> int | None:
        calls["n"] += 1
        if calls["n"] == 2:
            text = config_path.read_text(encoding="utf-8")
            config_path.write_text(text.replace("window_hours: 5", "window_hours: 3"), encoding="utf-8")
            return -1
        return real_mtime_ns(path)

    monkeypatch.setattr(auto_update_config, "_mtime_ns", mtime_with_one_collision)
    write_auto_update(enabled=True, data_dir=vault)

    final = config_path.read_text(encoding="utf-8")
    assert final == _enabled(_VAULT_CONFIG).replace("window_hours: 5", "window_hours: 3")
    assert calls["n"] >= 3  # a second attempt happened


def test_write_gives_up_on_a_writer_that_never_settles(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter(range(1_000))
    monkeypatch.setattr(auto_update_config, "_mtime_ns", lambda path: next(ticks))

    with pytest.raises(AutoUpdateWriteError, match="another writer"):
        write_auto_update(enabled=True, data_dir=vault)
    assert (vault / "scout-config.yaml").read_text(encoding="utf-8") == _VAULT_CONFIG


# ----- CLI ------------------------------------------------------------------


def test_cli_enables_and_emits_json(vault: Path) -> None:
    result = runner.invoke(app, ["config", "set-auto-update", "--enabled", "--json"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "config_path": str(vault / "scout-config.yaml"),
        "enabled": True,
        "channel": "stable",
        "changed": True,
    }
    assert (vault / "scout-config.yaml").read_text(encoding="utf-8") == _enabled(_VAULT_CONFIG)


def test_cli_disables_with_a_readable_report(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text(_enabled(_VAULT_CONFIG), encoding="utf-8")

    result = runner.invoke(app, ["config", "set-auto-update", "--disabled"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "disabled" in result.stdout
    assert str(vault / "scout-config.yaml") in result.stdout
    assert (vault / "scout-config.yaml").read_text(encoding="utf-8") == _VAULT_CONFIG


def test_cli_says_so_when_nothing_changed(vault: Path) -> None:
    result = runner.invoke(app, ["config", "set-auto-update", "--disabled"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "already" in result.stdout


def test_cli_with_no_flags_exits_2(vault: Path) -> None:
    result = runner.invoke(app, ["config", "set-auto-update"])
    assert result.exit_code == 2
    assert (vault / "scout-config.yaml").read_text(encoding="utf-8") == _VAULT_CONFIG


def test_cli_unknown_channel_exits_1_and_names_it(vault: Path) -> None:
    result = runner.invoke(app, ["config", "set-auto-update", "--enabled", "--channel", "nightly"])
    assert result.exit_code == 1
    assert "nightly" in result.stderr
    assert (vault / "scout-config.yaml").read_text(encoding="utf-8") == _VAULT_CONFIG


def test_cli_without_a_config_points_at_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["config", "set-auto-update", "--enabled"])
    assert result.exit_code == 1
    assert "/scout-setup" in result.stderr


# ----- the prompts that drive it ----------------------------------------------

_FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


def test_no_prompt_round_trips_scout_config_through_pyyaml() -> None:
    """A dump of the parsed file is what deleted the comments. Any shell block in
    a command, skill or phase that writes scout-config.yaml must go through the
    engine instead."""
    offenders = []
    for folder in ("commands", "skills", "phases"):
        for md in sorted((REPO_ROOT / folder).rglob("*.md")):
            for block in _FENCE_RE.findall(md.read_text(encoding="utf-8")):
                if "scout-config.yaml" in block and re.search(r"\byaml\.(safe_)?dump\(", block):
                    offenders.append(str(md.relative_to(REPO_ROOT)))
    assert offenders == []


def test_the_update_nudge_uses_the_engine_writer() -> None:
    """/scout-setup records its answer through `bootstrap install --auto-update`;
    /scout-update's nudge is the one place that edits an existing vault's block."""
    text = (REPO_ROOT / "commands" / "scout-update.md").read_text(encoding="utf-8")
    assert '"$SCOUTCTL" config set-auto-update --enabled' in text
