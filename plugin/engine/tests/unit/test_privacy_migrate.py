"""Vault migration for per-note privacy (#453, spec §3.1, §4.3, §5.1, §10 P5, D6, D10).

``scoutctl bootstrap privacy-migrate`` reports, read-only:

(a) notes whose ``visibility:`` value is present but isn't ``normal``/``private``
    (they already count as private); ``--set-private PATH`` rewrites that one
    value with the owner's OK;
(b) lines of shared files that link or name a private note (file + line only);
(c) trading/brokerage folders to make local-only; ``--local-only GLOB`` writes
    them to ``scout-config.yaml → privacy.local_only_add`` (the link step).

The writes refuse a headless caller (``SCOUT_MODE`` / a non-owner reader). The
upgrade pipeline only prints the report and never edits.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner, Result

from scout.cli import app
from scout.kb import outbound
from scout.kb.ontology import clear_frontmatter_cache
from scout.kb.privacy import label_file, load_policy
from scout.scripts import privacy_migrate as pm
from tests.unit.privacy_vault_helpers import CANARIES, FIXTURE

runner = CliRunner()

DRAFT = "knowledge-base/topics/misc/draft.md"
SHARED_DRAFT = "drafts/reply-to-priya.md"
CONFIG_NO_PRIVACY = """\
# Vault config (stand-in values only).
user:
  name: Alex  # the owner

# Budgets
budget:
  daily_usd: 5
"""


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the privacy fixture vault (no git needed), as the data dir."""
    clear_frontmatter_cache()
    outbound.clear_cache()
    v = tmp_path / "vault"
    shutil.copytree(FIXTURE, v)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".scout-cache" not in p.parts
    }


def cli(*args: str, env: dict[str, str] | None = None) -> Result:
    return runner.invoke(app, ["bootstrap", "privacy-migrate", *args], env=env or {})


def _no_private_content(text: str) -> None:
    for token in CANARIES.values():
        assert token not in text
    assert "PRIVATE while drafting" not in text
    assert "Sammy" not in text and "[[sam]]" not in text


# ----- (a) legacy visibility values ---------------------------------------------------


def test_lists_only_notes_with_a_nonstandard_value(vault: Path) -> None:
    # sam/billing say `private`, task-buy-gift says `normal`; broken.md doesn't parse
    # (kb-lint's job) and notes without the key are never listed.
    assert pm.odd_visibility_notes(vault) == [DRAFT]


def test_report_lists_paths_only_and_says_they_count_as_private(vault: Path) -> None:
    result = cli()
    assert result.exit_code == 0, result.output
    assert DRAFT in result.output
    assert "already count as private" in result.output
    _no_private_content(result.output)

    data = json.loads(cli("--json").output)
    assert data["visibility_values"] == [DRAFT]
    _no_private_content(json.dumps(data))


def test_report_never_writes(vault: Path) -> None:
    before = _snapshot(vault)
    cli()
    cli("--json")
    pm.report(vault)
    assert _snapshot(vault) == before


def test_set_private_changes_only_that_value(vault: Path) -> None:
    path = vault / DRAFT
    old = path.read_text(encoding="utf-8").splitlines(keepends=True)
    result = cli("--set-private", DRAFT)
    assert result.exit_code == 0, result.output
    new = path.read_text(encoding="utf-8").splitlines(keepends=True)
    assert len(new) == len(old)
    changed = [i for i, (a, b) in enumerate(zip(old, new, strict=True)) if a != b]
    assert changed == [2]
    assert new[2] == "visibility: private\n"
    label = label_file(vault, DRAFT, load_policy(vault))
    assert label.visibility == "private" and label.warnings == ()
    assert pm.odd_visibility_notes(vault) == []
    _no_private_content(result.output)


def test_set_private_is_idempotent(vault: Path) -> None:
    assert cli("--set-private", DRAFT).exit_code == 0
    before = _snapshot(vault)
    again = cli("--set-private", DRAFT)
    assert again.exit_code == 0, again.output
    assert _snapshot(vault) == before


def test_set_private_keeps_a_trailing_comment_and_crlf(vault: Path) -> None:
    rel = "knowledge-base/topics/misc/odd.md"
    (vault / rel).write_bytes(b"---\r\ntitle: Odd\r\nvisibility: secret  # owner note\r\n---\r\n\r\nBody.\r\n")
    assert pm.set_private(vault, rel) == ("rewritten", "")
    assert (vault / rel).read_bytes() == (
        b"---\r\ntitle: Odd\r\nvisibility: private  # owner note\r\n---\r\n\r\nBody.\r\n"
    )


@pytest.mark.parametrize(
    "rel",
    [
        "knowledge-base/topics/misc/plain.md",  # no visibility key: never add one
        "knowledge-base/people/sam.md",  # already `private`
        "knowledge-base/personal/task-buy-gift.md",  # already `normal`: never narrowed here
    ],
)
def test_set_private_never_touches_notes_without_a_legacy_value(vault: Path, rel: str) -> None:
    before = _snapshot(vault)
    outcome, _ = pm.set_private(vault, rel)
    assert outcome in ("unchanged", "refused")
    assert _snapshot(vault) == before


def test_set_private_refuses_a_multiline_value(vault: Path) -> None:
    rel = "knowledge-base/topics/misc/listy.md"
    text = "---\ntitle: Listy\nvisibility:\n  - team\n  - me\n---\n\nBody.\n"
    (vault / rel).write_text(text, encoding="utf-8")
    assert rel in pm.odd_visibility_notes(vault)
    outcome, reason = pm.set_private(vault, rel)
    assert outcome == "refused" and reason
    assert (vault / rel).read_text(encoding="utf-8") == text


def test_set_private_refuses_a_path_outside_the_vault(vault: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("---\nvisibility: secret\n---\n", encoding="utf-8")
    result = cli("--set-private", str(outside))
    assert result.exit_code == 2
    assert outside.read_text(encoding="utf-8") == "---\nvisibility: secret\n---\n"


@pytest.mark.parametrize("env", [{"SCOUT_MODE": "dreaming"}, {"SCOUT_READER": "autonomous"}])
def test_writes_refuse_a_headless_caller(vault: Path, env: dict[str, str]) -> None:
    (vault / "scout-config.yaml").write_text(CONFIG_NO_PRIVACY, encoding="utf-8")
    before = _snapshot(vault)
    for args in (("--set-private", DRAFT), ("--local-only", "trading/**")):
        result = cli(*args, env=env)
        assert result.exit_code == 2, result.output
        assert "owner" in result.output
    assert _snapshot(vault) == before
    # The read-only report still works headless.
    assert cli(env=env).exit_code == 0


# ----- (b) private links in shared files ---------------------------------------------


def test_reports_private_links_in_shared_files_by_line(vault: Path) -> None:
    path = vault / SHARED_DRAFT
    lines = path.read_text(encoding="utf-8").splitlines()
    lines += ["", "Loop in [[sam]] first.", "Sammy agreed. %%privacy:allow sam%%"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    hit = len(lines) - 1  # 1-based number of the [[sam]] line

    assert pm.private_links_in_shared(vault) == [(SHARED_DRAFT, [hit])]
    result = cli()
    assert f"{SHARED_DRAFT}: line {hit}" in result.output
    _no_private_content(result.output)
    data = json.loads(cli("--json").output)
    assert data["private_links"] == [{"path": SHARED_DRAFT, "lines": [hit]}]


def test_no_private_links_in_the_clean_fixture(vault: Path) -> None:
    assert pm.private_links_in_shared(vault) == []


# ----- (c) the link step: local-only trading/brokerage paths --------------------------


def _no_overlay(vault: Path) -> Path:
    config = vault / "scout-config.yaml"
    config.write_text(CONFIG_NO_PRIVACY, encoding="utf-8")
    return config


def test_proposes_trading_and_brokerage_folders(vault: Path) -> None:
    _no_overlay(vault)
    (vault / "knowledge-base/projects/agentic-trading").mkdir(parents=True)
    (vault / "knowledge-base/projects/agentic-trading/config.yaml").write_text("enabled: false\n")
    (vault / "Brokerage Statements").mkdir()
    (vault / "Brokerage Statements/2026-09.md").write_text("Stand-in.\n")
    (vault / "knowledge-base/projects/widget/trade-show").mkdir()  # `trade` is not a match
    assert pm.local_only_proposals(vault) == [
        "Brokerage Statements/**",
        "knowledge-base/projects/agentic-trading/**",
        "trading/**",
    ]


def test_already_local_only_paths_are_not_proposed(vault: Path) -> None:
    # The fixture's overlay already has `trading/**`.
    assert pm.local_only_proposals(vault) == []


def test_link_step_writes_local_only_add_and_keeps_comments(vault: Path) -> None:
    config = _no_overlay(vault)
    assert label_file(vault, "trading/positions.md", load_policy(vault)).local_only is False
    result = cli("--local-only", "trading/**")
    assert result.exit_code == 0, result.output
    text = config.read_text(encoding="utf-8")
    assert text.startswith(CONFIG_NO_PRIVACY)  # every comment and line before it intact
    assert yaml.safe_load(text)["privacy"] == {"local_only_add": ["trading/**"]}
    assert label_file(vault, "trading/positions.md", load_policy(vault)).local_only is True
    assert pm.local_only_proposals(vault) == []


def test_link_step_is_idempotent_and_never_removes(vault: Path) -> None:
    config = vault / "scout-config.yaml"
    config.write_text(
        CONFIG_NO_PRIVACY + '\nprivacy:\n  # the owner\'s own list\n  local_only_add:\n    - "taxes/**"  # mine\n',
        encoding="utf-8",
    )
    assert cli("--local-only", "trading/**").exit_code == 0
    once = config.read_text(encoding="utf-8")
    assert "# the owner's own list" in once and "# mine" in once
    assert yaml.safe_load(once)["privacy"]["local_only_add"] == ["taxes/**", "trading/**"]
    assert cli("--local-only", "trading/**").exit_code == 0
    assert config.read_text(encoding="utf-8") == once


def test_link_step_adds_the_key_under_an_existing_privacy_block(vault: Path) -> None:
    config = vault / "scout-config.yaml"
    config.write_text(
        CONFIG_NO_PRIVACY + 'privacy:\n  # path defaults\n  shared_files_add:\n    - "public/**"\n',
        encoding="utf-8",
    )
    assert pm.add_local_only(vault, ["trading/**"]) == ["trading/**"]
    text = config.read_text(encoding="utf-8")
    assert "# path defaults" in text
    assert yaml.safe_load(text)["privacy"] == {
        "shared_files_add": ["public/**"],
        "local_only_add": ["trading/**"],
    }


@pytest.mark.parametrize("inline", ["[]", "~", '["taxes/**"]'])
def test_link_step_never_duplicates_an_inline_key(vault: Path, inline: str, capsys: pytest.CaptureFixture[str]) -> None:
    config = vault / "scout-config.yaml"
    config.write_text(CONFIG_NO_PRIVACY + f"privacy:\n  local_only_add: {inline}\n", encoding="utf-8")
    existing = ["taxes/**"] if "taxes" in inline else []
    assert pm.add_local_only(vault, ["trading/**"]) == ["trading/**"]
    text = config.read_text(encoding="utf-8")
    assert text.count("local_only_add") == 1
    assert pm._load(text)["privacy"] == {"local_only_add": [*existing, "trading/**"]}
    # The in-place edit couldn't be used: the block re-dump says so.
    assert "comments inside it may be lost" in capsys.readouterr().err


def test_strict_load_rejects_duplicate_keys() -> None:
    dup = "privacy:\n  local_only_add: []\n  local_only_add:\n    - a\n"
    assert yaml.safe_load(dup) == {"privacy": {"local_only_add": ["a"]}}  # PyYAML keeps the last
    assert pm._load(dup) is None
    assert pm._load("a: 1\nb: {c: 2}\n") == {"a": 1, "b": {"c": 2}}


def test_in_place_edit_does_not_warn(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _no_overlay(vault)
    pm.add_local_only(vault, ["trading/**"])
    assert "warning" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("value_lines", "reason"),
    [
        ("visibility:\n  - team\n", "next line"),
        ("visibility: |\n  team only\n", "block scalar"),
        ("visibility: secret\n  while drafting\n", "parse back"),
    ],
)
def test_set_private_refusal_reasons(vault: Path, value_lines: str, reason: str) -> None:
    rel = "knowledge-base/topics/misc/odd2.md"
    text = f"---\ntitle: Odd\n{value_lines}---\n\nBody.\n"
    (vault / rel).write_text(text, encoding="utf-8")
    outcome, why = pm.set_private(vault, rel)
    assert outcome == "refused" and reason in why
    assert (vault / rel).read_text(encoding="utf-8") == text


def test_an_agent_reader_gets_nothing(vault: Path) -> None:
    before = _snapshot(vault)
    for args in ((), ("--json",), ("--set-private", DRAFT)):
        result = cli(*args, env={"SCOUT_READER": "agent:person:priya@example.com"})
        assert result.exit_code == 2
        assert result.output == ""
    assert _snapshot(vault) == before


@pytest.mark.parametrize("glob", ["", "../outside/**", "/abs/**"])
def test_link_step_refuses_a_bad_glob(vault: Path, glob: str) -> None:
    config = _no_overlay(vault)
    result = cli("--local-only", glob)
    assert result.exit_code == 2
    assert config.read_text(encoding="utf-8") == CONFIG_NO_PRIVACY


# ----- the upgrade pipeline only reports ---------------------------------------------


def test_upgrade_stage_prints_the_report_and_writes_nothing(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scout.scripts.bootstrap import _stage_privacy_report

    _no_overlay(vault)
    before = _snapshot(vault)
    _stage_privacy_report(vault)
    err = capsys.readouterr().err
    assert DRAFT in err
    assert "trading/**" in err
    assert "/scout-update" in err
    _no_private_content(err)
    assert _snapshot(vault) == before


def test_upgrade_stage_never_fails_the_upgrade(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scout.scripts import bootstrap

    def boom(_vault: Path, *, persist: bool = True) -> pm.MigrationReport:
        raise OSError("disk on fire")

    monkeypatch.setattr(pm, "report", boom)
    bootstrap._stage_privacy_report(vault)
    assert "privacy" in capsys.readouterr().err


# ----- edge and refusal paths -----------------------------------------------------


def _write(vault: Path, rel: str, data: str | bytes) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


def test_frontmatter_that_is_not_a_mapping_or_not_yaml_is_none() -> None:
    assert pm._frontmatter("---\n- a\n- b\n---\n") is None
    assert pm._frontmatter("---\nkey: [unclosed\n---\n") is None
    assert pm._frontmatter("no fence\n") is None


def test_a_bad_federation_scope_alone_is_not_a_legacy_visibility(vault: Path) -> None:
    # `visibility-value` also fires for an unparseable federation_scope; with no
    # odd visibility: value the note isn't listed.
    _write(vault, "knowledge-base/topics/misc/scoped.md", "---\nfederation_scope: everyone\n---\n")
    assert pm.odd_visibility_notes(vault) == [DRAFT]


def test_a_note_that_vanishes_mid_scan_is_skipped(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = Path.read_bytes

    def flaky(self: Path) -> bytes:
        if self.name == "draft.md":
            raise OSError("gone")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    assert pm.odd_visibility_notes(vault) == []


@pytest.mark.parametrize(
    ("rel", "data", "reason"),
    [
        ("knowledge-base/topics/misc/notes.txt", "visibility: secret\n", "not a markdown note"),
        ("knowledge-base/topics/misc/missing.md", None, "not a markdown note"),
        ("knowledge-base/topics/misc/latin1.md", b"---\nvisibility: s\xe9cret\n---\n", "not UTF-8"),
        ("knowledge-base/topics/misc/nokey.md", "---\ntitle: No key\n---\n", "never adds one"),
        (
            "knowledge-base/topics/misc/twice.md",
            "---\nvisibility: secret\nvisibility: hidden\n---\n",
            "unambiguously",
        ),
    ],
)
def test_set_private_refuses(vault: Path, rel: str, data: str | bytes | None, reason: str) -> None:
    if data is not None:
        _write(vault, rel, data)
    before = _snapshot(vault)
    outcome, why = pm.set_private(vault, rel)
    assert outcome == "refused" and reason in why
    assert _snapshot(vault) == before


def test_set_private_rewrites_a_quoted_value_without_keeping_its_text(vault: Path) -> None:
    rel = "knowledge-base/topics/misc/quoted.md"
    _write(vault, rel, '---\nvisibility: "secret # not a comment"\ntitle: Q\n---\n')
    assert pm.set_private(vault, rel) == ("rewritten", "")
    assert (vault / rel).read_text(encoding="utf-8") == "---\nvisibility: private\ntitle: Q\n---\n"


def test_a_failed_write_leaves_the_note_and_no_temp_file(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(src: str, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(pm.os, "replace", boom)
    before = (vault / DRAFT).read_bytes()
    with pytest.raises(OSError, match="disk full"):
        pm.set_private(vault, DRAFT)
    assert (vault / DRAFT).read_bytes() == before
    assert not [p for p in (vault / DRAFT).parent.iterdir() if p.name.endswith(".tmp")]


def test_no_shared_files_means_no_links(tmp_path: Path) -> None:
    # The fixture's widget notes declare a federation_scope (shared); a bare vault has none.
    bare = tmp_path / "bare"
    _write(bare, "knowledge-base/people/sam.md", "---\nvisibility: private\n---\nSam.\n")
    _write(bare, "knowledge-base/topics/plain.md", "Mentions Sam's [[sam]] note.\n")
    assert pm.private_links_in_shared(bare) == []


def test_shared_files_that_are_binary_or_unreadable_are_skipped(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(vault, "drafts/scan.bin", b"\x00\x01 [[sam]] Sammy")
    _write(vault, "drafts/gone.md", "Loop in [[sam]].\n")
    real = Path.read_bytes

    def flaky(self: Path) -> bytes:
        if self.name == "gone.md":
            raise OSError("gone")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    assert pm.private_links_in_shared(vault) == []


def test_a_private_shared_file_is_not_matched_against_its_own_name(vault: Path) -> None:
    _write(vault, "drafts/secret-plan.md", "---\nvisibility: private\n---\n\nThe secret-plan.\nAsk [[sam]].\n")
    assert pm.private_links_in_shared(vault) == [("drafts/secret-plan.md", [6])]


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        # A flow-style privacy block can't be edited in place.
        ("user:\n  name: Alex\nprivacy: {shared_files_add: [public/**]}\n", {"shared_files_add": ["public/**"]}),
        # A privacy key holding only comments.
        ("user:\n  name: Alex\nprivacy:\n  # nothing yet\n", {}),
    ],
)
def test_link_step_falls_back_for_layouts_it_cannot_edit(
    vault: Path, config: str, expected: dict[str, list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    _write(vault, "scout-config.yaml", config)
    assert pm.add_local_only(vault, ["trading/**"]) == ["trading/**"]
    text = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    assert pm._load(text)["privacy"] == {**expected, "local_only_add": ["trading/**"]}
    assert pm._load(text)["user"] == {"name": "Alex"}
    assert "comments inside it may be lost" in capsys.readouterr().err


def test_link_step_edits_a_file_without_a_final_newline(vault: Path) -> None:
    _write(vault, "scout-config.yaml", "privacy:\n  shared_files_add:\n    - public/**")
    pm.add_local_only(vault, ["trading/**"])
    assert pm._load((vault / "scout-config.yaml").read_text(encoding="utf-8"))["privacy"] == {
        "shared_files_add": ["public/**"],
        "local_only_add": ["trading/**"],
    }
    _write(vault, "scout-config.yaml", "privacy:\n  local_only_add:\n    - taxes/**")
    pm.add_local_only(vault, ["trading/**"])
    assert pm._load((vault / "scout-config.yaml").read_text(encoding="utf-8"))["privacy"] == {
        "local_only_add": ["taxes/**", "trading/**"]
    }


def test_link_step_appends_to_a_list_followed_by_another_key(vault: Path) -> None:
    _write(
        vault,
        "scout-config.yaml",
        "privacy:\n  local_only_add:\n    - taxes/**  # mine\n  shared_files_add:\n    - public/**\n",
    )
    pm.add_local_only(vault, ["trading/**"])
    text = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    assert "# mine" in text
    assert pm._load(text)["privacy"] == {
        "local_only_add": ["taxes/**", "trading/**"],
        "shared_files_add": ["public/**"],
    }


def test_link_step_fills_an_empty_block_key(vault: Path) -> None:
    _write(vault, "scout-config.yaml", "privacy:\n  # trading data stays here\n  local_only_add:\n")
    pm.add_local_only(vault, ["trading/**"])
    text = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    assert "# trading data stays here" in text
    assert pm._load(text) == {"privacy": {"local_only_add": ["trading/**"]}}


def test_link_step_writes_a_config_that_holds_only_comments_or_is_missing(vault: Path) -> None:
    _write(vault, "scout-config.yaml", "# empty for now\n")
    pm.add_local_only(vault, ["trading/**"])
    text = (vault / "scout-config.yaml").read_text(encoding="utf-8")
    assert text.startswith("# empty for now\n")
    assert pm._load(text) == {"privacy": {"local_only_add": ["trading/**"]}}

    (vault / "scout-config.yaml").unlink()
    pm.add_local_only(vault, ["trading/**"])
    assert pm._load((vault / "scout-config.yaml").read_text(encoding="utf-8")) == {
        "privacy": {"local_only_add": ["trading/**"]}
    }


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ("- a\n- b\n", "isn't a mapping"),
        ("privacy:\n  - trading/**\n", "privacy isn't a mapping"),
        ("privacy:\n  local_only_add: trading/**\n", "isn't a list of globs"),
        ("privacy:\n  local_only_add:\n    - 1\n", "isn't a list of globs"),
    ],
)
def test_link_step_refuses_a_config_it_cannot_read(vault: Path, config: str, message: str) -> None:
    _write(vault, "scout-config.yaml", config)
    with pytest.raises(ValueError, match=message):
        pm.add_local_only(vault, ["trading/**"])
    assert (vault / "scout-config.yaml").read_text(encoding="utf-8") == config
    result = cli("--local-only", "trading/**")
    assert result.exit_code == 2 and message in result.output


def test_link_step_raises_when_no_rewrite_loads_back(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import bootstrap

    config = _no_overlay(vault)
    monkeypatch.setattr(pm, "_insert_globs", lambda _text, _new: None)
    monkeypatch.setattr(bootstrap, "_dump_keeping_comments", lambda _text, _data: "privacy: [broken\n")
    with pytest.raises(RuntimeError, match="could not write"):
        pm.add_local_only(vault, ["trading/**"])
    assert config.read_text(encoding="utf-8") == CONFIG_NO_PRIVACY


def test_strict_load_handles_an_unhashable_key() -> None:
    assert pm._load("? [a, b]\n: 1\n") is None  # PyYAML can't hash a list key: unparseable


def test_report_lines_for_each_list_alone() -> None:
    assert pm.MigrationReport().lines() == ["privacy migration: nothing to do"]
    links = pm.MigrationReport(private_links=[("drafts/a.md", [2, 5])]).lines()
    assert links[0].startswith("private links: 1 shared file(s)")
    assert "  drafts/a.md: lines 2, 5" in links
    single = pm.MigrationReport(private_links=[("drafts/a.md", [2])]).lines()
    assert "  drafts/a.md: line 2" in single
    proposals = pm.MigrationReport(local_only_proposals=["trading/**"]).lines()
    assert proposals[0].startswith("local-only:") and "  trading/**" in proposals


def test_cli_write_json_and_a_refused_note(vault: Path) -> None:
    _no_overlay(vault)
    result = cli("--set-private", DRAFT, "--local-only", "trading/**", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == {
        "set_private": [{"path": DRAFT, "outcome": "rewritten", "reason": ""}],
        "local_only_added": ["trading/**"],
    }
    again = cli("--set-private", "knowledge-base/topics/misc/plain.md", "--local-only", "trading/**")
    assert again.exit_code == 1
    assert "refused: knowledge-base/topics/misc/plain.md" in again.output
    assert "already local-only: trading/**" in again.output


def test_upgrade_stage_is_silent_when_there_is_nothing_to_do(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scout.scripts.bootstrap import _stage_privacy_report

    pm.set_private(vault, DRAFT)  # the fixture's only legacy marker; trading/** is already local-only
    _stage_privacy_report(vault)
    assert capsys.readouterr().err == ""


def _cache_snapshot(vault: Path) -> dict[str, bytes]:
    cache = vault / ".scout-cache"
    return {str(p.relative_to(vault)): p.read_bytes() for p in sorted(cache.rglob("*")) if p.is_file()}


def test_upgrade_stage_never_writes_the_privacy_index(vault: Path) -> None:
    """Repeated upgrades must be byte-identical: the report reads labels without persisting them."""
    from scout.scripts.bootstrap import _stage_privacy_report

    _no_overlay(vault)
    _stage_privacy_report(vault)
    assert not (vault / ".scout-cache").exists()  # a cold vault: nothing created
    pm.report(vault)  # the CLI report does persist the index
    warm = _cache_snapshot(vault)
    assert warm
    (vault / "knowledge-base/topics/misc/plain.md").write_text("Edited.\n", encoding="utf-8")
    _stage_privacy_report(vault)
    _stage_privacy_report(vault)
    assert _cache_snapshot(vault) == warm  # a stale index is read, never rewritten


# ----- --accept: the owner's P5 consent marker ----------------------------------------

MARKER = ".scout-state/privacy-migrate.json"


def _accepted() -> dict[str, object] | None:
    accepted = json.loads(cli("--json").output)["accepted"]
    assert accepted is None or isinstance(accepted, dict)
    return accepted


def test_accept_writes_the_marker_with_its_fields_and_mode(vault: Path) -> None:
    import scout

    result = cli("--accept")
    assert result.exit_code == 0, result.output
    marker = vault / MARKER
    assert marker.stat().st_mode & 0o777 == 0o600
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert set(data) == {"schema", "engine_version", "accepted_at", "report_sha256"}
    assert data["schema"] == 1
    assert data["engine_version"] == scout.__version__
    assert data["accepted_at"].endswith("Z") and "T" in data["accepted_at"]
    assert data["report_sha256"] == pm.decision_sha256(pm.report(vault))
    assert len(data["report_sha256"]) == 64
    assert [p.name for p in (vault / ".scout-state").iterdir()] == ["privacy-migrate.json"]
    assert data["accepted_at"] in result.output
    _no_private_content(result.output)
    # Re-accepting replaces the marker and keeps its mode.
    assert cli("--accept", "--json").exit_code == 0
    assert marker.stat().st_mode & 0o777 == 0o600


def test_decision_sha_covers_only_the_outstanding_decisions() -> None:
    a = pm.MigrationReport(visibility_values=["b.md", "a.md"], local_only_proposals=["trading/**"])
    b = pm.MigrationReport(
        visibility_values=["a.md", "b.md"],
        private_links=[("drafts/x.md", [3])],
        local_only_proposals=["trading/**"],
    )
    assert pm.decision_sha256(a) == pm.decision_sha256(b)
    canonical = json.dumps(
        {"local_only_proposals": ["trading/**"], "visibility_values": ["a.md", "b.md"]},
        sort_keys=True,
        separators=(",", ":"),
    )
    assert pm.decision_sha256(a) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert pm.decision_sha256(pm.MigrationReport()) != pm.decision_sha256(a)


@pytest.mark.parametrize(
    "env",
    [{"SCOUT_MODE": "dreaming"}, {"SCOUT_READER": "autonomous"}, {"SCOUT_MODE": "x", "SCOUT_READER": "owner"}],
)
def test_accept_refuses_a_headless_caller(vault: Path, env: dict[str, str]) -> None:
    before = _snapshot(vault)
    result = cli("--accept", env=env)
    assert result.exit_code == 2, result.output
    assert "owner" in result.output
    assert not (vault / MARKER).exists()
    assert _snapshot(vault) == before


def test_accept_gives_an_agent_reader_nothing(vault: Path) -> None:
    result = cli("--accept", env={"SCOUT_READER": "agent:person:priya@example.com"})
    assert result.exit_code == 2
    assert result.output == ""
    assert not (vault / MARKER).exists()


def test_accept_is_its_own_action(vault: Path) -> None:
    before = _snapshot(vault)
    result = cli("--accept", "--set-private", DRAFT)
    assert result.exit_code == 2
    assert _snapshot(vault) == before


def test_accept_writes_nothing_when_the_report_fails(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> list[str]:
        raise OSError("disk went away")

    monkeypatch.setattr(pm, "local_only_proposals", boom)
    result = cli("--accept")
    assert result.exit_code == 1
    assert "nothing was recorded" in result.output
    assert not (vault / MARKER).exists()


def test_accept_never_writes_the_privacy_index(vault: Path) -> None:
    _no_overlay(vault)
    assert cli("--accept").exit_code == 0
    assert not (vault / ".scout-cache").exists()


def test_a_failed_marker_write_leaves_no_file(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_a: object, **_k: object) -> None:
        raise OSError("no space")

    monkeypatch.setattr(pm.os, "replace", fail)
    with pytest.raises(OSError):
        pm.accept(vault)
    assert list((vault / ".scout-state").iterdir()) == []


def test_accepted_is_current_right_after_and_lapses_on_a_new_legacy_note(vault: Path) -> None:
    assert _accepted() is None
    text = cli().output
    assert "not accepted" in text and "/scout-update" in text
    assert cli("--accept").exit_code == 0
    acc = _accepted()
    assert acc is not None and acc["current"] is True
    marker = json.loads((vault / MARKER).read_text(encoding="utf-8"))
    assert {k: acc[k] for k in ("accepted_at", "engine_version", "report_sha256")} == {
        k: marker[k] for k in ("accepted_at", "engine_version", "report_sha256")
    }
    assert f"accepted at {marker['accepted_at']}" in cli().output

    _write(vault, "knowledge-base/topics/misc/new-legacy.md", "---\nvisibility: secret\n---\nBody.\n")
    clear_frontmatter_cache()
    outbound.clear_cache()
    acc = _accepted()
    assert acc is not None and acc["current"] is False
    text = cli().output
    assert "out of date" in text and "asked again" in text and "/scout-update" in text


def test_accepted_lapses_when_a_trading_folder_appears(vault: Path) -> None:
    assert cli("--accept").exit_code == 0
    (vault / "Brokerage Statements").mkdir()
    (vault / "Brokerage Statements/2026-09.md").write_text("Stand-in.\n", encoding="utf-8")
    acc = _accepted()
    assert acc is not None and acc["current"] is False


def test_editing_a_shared_draft_keeps_acceptance_current(vault: Path) -> None:
    assert cli("--accept").exit_code == 0
    path = vault / SHARED_DRAFT
    path.write_text(path.read_text(encoding="utf-8") + "\nLoop in [[sam]] first.\n", encoding="utf-8")
    outbound.clear_cache()
    data = json.loads(cli("--json").output)
    assert data["private_links"]  # the informational list changed...
    assert data["accepted"]["current"] is True  # ...and consent still holds


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b"[]",
        b'{"schema": 2, "engine_version": "1", "accepted_at": "x", "report_sha256": "y"}',
        b'{"schema": 1, "engine_version": "1", "accepted_at": "x"}',
        b'{"schema": 1, "engine_version": 1, "accepted_at": "x", "report_sha256": "y"}',
        b"\xff\xfe",
        None,  # a directory where the file should be
    ],
)
def test_an_unreadable_or_garbage_marker_reads_as_not_accepted(vault: Path, content: bytes | None) -> None:
    marker = vault / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        marker.mkdir()
    else:
        marker.write_bytes(content)
    assert pm.read_marker(vault) is None
    assert _accepted() is None


def test_acceptance_lines() -> None:
    assert "not accepted" in pm.acceptance_line(None)
    ok = {"accepted_at": "2026-10-21T09:00:00Z", "engine_version": "0", "report_sha256": "a", "current": True}
    assert pm.acceptance_line(ok) == "privacy consent: accepted at 2026-10-21T09:00:00Z"
    stale = pm.acceptance_line({**ok, "current": False})
    assert "out of date" in stale and "asked again" in stale and "/scout-update" in stale


def test_the_marker_is_local_only_under_the_engine_policy() -> None:
    from scout.kb.privacy import classify

    assert classify(MARKER, None, load_policy(None)).local_only is True
