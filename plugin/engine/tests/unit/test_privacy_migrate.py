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

    def boom(_vault: Path) -> pm.MigrationReport:
        raise OSError("disk on fire")

    monkeypatch.setattr(pm, "report", boom)
    bootstrap._stage_privacy_report(vault)
    assert "privacy" in capsys.readouterr().err
