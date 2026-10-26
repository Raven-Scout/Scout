"""``scoutctl kb classify`` / ``kb policy`` and reader resolution (#453, spec §4.1, §6.1, §6.2)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from scout.cli import app
from scout.kb.privacy import load_policy
from scout.kb.reader import FAIL_CLOSED_READER, resolve_reader

runner = CliRunner()

PRIYA = "agent:person:priya@example.com"


# ----- resolve_reader ----------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "env", "want"),
    [
        (None, {}, "owner"),
        (None, {"SCOUT_MODE": "briefing"}, "autonomous"),
        (None, {"SCOUT_MODE": ""}, "owner"),
        (None, {"SCOUT_READER": "owner", "SCOUT_MODE": "briefing"}, "owner"),
        (None, {"SCOUT_READER": "autonomous"}, "autonomous"),
        (None, {"SCOUT_READER": PRIYA}, PRIYA),
        ("owner", {}, "owner"),
        ("autonomous", {}, "autonomous"),
        ("autonomous", {"SCOUT_MODE": "briefing"}, "autonomous"),
        (PRIYA, {}, PRIYA),
        ("agent:group:team-1", {"SCOUT_MODE": "dream"}, "agent:group:team-1"),
        ("agent:group:team-1", {"SCOUT_READER": "autonomous"}, "agent:group:team-1"),
        (PRIYA, {"SCOUT_READER": PRIYA}, PRIYA),
        # emails compare case-insensitively
        ("agent:person:PRIYA@example.com", {"SCOUT_READER": PRIYA}, PRIYA),
        ("agent:PERSON:Priya@Example.com", {"SCOUT_READER": "AGENT:person:priya@example.com"}, PRIYA),
        ("AGENT:group:team-1", {}, "agent:group:team-1"),
    ],
)
def test_resolve_reader_ok(flag: str | None, env: dict[str, str], want: str) -> None:
    assert resolve_reader(flag, env) == want


@pytest.mark.parametrize(
    ("flag", "env"),
    [
        ("owner", {"SCOUT_READER": "autonomous"}),
        ("owner", {"SCOUT_MODE": "briefing"}),
        ("autonomous", {"SCOUT_READER": PRIYA}),
        ("owner", {"SCOUT_READER": PRIYA}),
        # a different agent principal is lateral, not narrower: refused
        ("agent:person:sam@example.com", {"SCOUT_READER": PRIYA}),
        ("agent:group:team-1", {"SCOUT_READER": PRIYA}),
    ],
)
def test_resolve_reader_cannot_widen(flag: str, env: dict[str, str]) -> None:
    with pytest.raises(typer.BadParameter):
        resolve_reader(flag, env)


@pytest.mark.parametrize(
    "flag",
    [
        "indexer",
        "agent:",
        "agent:org",
        "agent:person:not-an-email",
        "agent:group:",
        "agent:bogus",
        "root",
        "",
        " owner",
    ],
)
def test_resolve_reader_bad_flag(flag: str) -> None:
    with pytest.raises(typer.BadParameter):
        resolve_reader(flag, {})


@pytest.mark.parametrize("value", ["indexer", "root", "", "agent:", "agent:org", "OWNER ", "agent:person:x"])
def test_resolve_reader_bad_env_fails_closed(value: str) -> None:
    got = resolve_reader(None, {"SCOUT_READER": value})
    assert got == FAIL_CLOSED_READER
    assert got.startswith("agent:")
    # and nothing can widen it, owner flag included
    with pytest.raises(typer.BadParameter):
        resolve_reader("owner", {"SCOUT_READER": value})
    assert resolve_reader(None, {"SCOUT_READER": value, "SCOUT_MODE": "x"}) == FAIL_CLOSED_READER


# ----- kb classify ---------------------------------------------------------------


def _vault(tmp_path: Path) -> Path:
    kb = tmp_path / "knowledge-base"
    (kb / "personal").mkdir(parents=True)
    (kb / "people").mkdir()
    (kb / "personal" / "task-renew-permit.md").write_text(
        "---\ntype: task\n---\n# Renew\n\nCANARY-PRIV-1 body.\n", encoding="utf-8"
    )
    (kb / "people" / "sam.md").write_text("---\nvisibility: private\n---\n# Sam\n\nCANARY-PRIV-2\n", encoding="utf-8")
    (kb / "people" / "priya.md").write_text("---\nname: Priya\n---\n# Priya\n", encoding="utf-8")
    (kb / "people" / "broken.md").write_text("---\nname: [unclosed\n---\nCANARY-PRIV-3\n", encoding="utf-8")
    (kb / "people" / "binary.md").write_bytes(b"---\nvisibility: private\n---\n\xff\xfe\xfd bad bytes\n")
    return tmp_path


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("SCOUT_READER", raising=False)
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    return _vault(tmp_path)


def _run(vault: Path, *args: str, env: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["kb", *args, "--repo", str(vault)], env=env)


def test_classify_json_owner(vault: Path) -> None:
    result = _run(
        vault, "classify", "knowledge-base/personal/task-renew-permit.md", "knowledge-base/people/priya.md", "--json"
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [r["path"] for r in rows] == [
        "knowledge-base/personal/task-renew-permit.md",
        "knowledge-base/people/priya.md",
    ]
    assert rows[0]["visibility"] == "private"
    assert rows[0]["visibility_source"] == "path_default"
    assert rows[1]["visibility"] == "normal"
    assert "CANARY-PRIV" not in result.stdout


def test_classify_text_marks_private(vault: Path) -> None:
    result = _run(vault, "classify", "knowledge-base/people/sam.md", "knowledge-base/people/priya.md")
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "[private] knowledge-base/people/sam.md",
        "knowledge-base/people/priya.md",
    ]


def test_classify_autonomous_reads_labels(vault: Path) -> None:
    result = _run(vault, "classify", "knowledge-base/people/sam.md", "--json", env={"SCOUT_MODE": "briefing"})
    assert json.loads(result.stdout)[0]["visibility"] == "private"


def test_classify_normalises_spellings(vault: Path) -> None:
    (vault / "knowledge_base").symlink_to("knowledge-base")
    result = _run(vault, "classify", "./knowledge_base/people/../personal/task-renew-permit.md", "--json")
    row = json.loads(result.stdout)[0]
    assert row["path"] == "knowledge-base/personal/task-renew-permit.md"
    assert row["visibility"] == "private"


def test_classify_absolute_path_inside_vault(vault: Path) -> None:
    result = _run(vault, "classify", str(vault / "knowledge-base" / "people" / "sam.md"), "--json")
    assert json.loads(result.stdout)[0]["path"] == "knowledge-base/people/sam.md"


def test_classify_unparseable_frontmatter_is_forced_private(vault: Path) -> None:
    result = _run(vault, "classify", "knowledge-base/people/broken.md", "--json")
    row = json.loads(result.stdout)[0]
    assert (row["visibility"], row["visibility_source"]) == ("private", "forced")
    assert "CANARY-PRIV" not in result.output


def test_classify_md_with_invalid_byte_in_body_stays_private(vault: Path) -> None:
    result = _run(vault, "classify", "knowledge-base/people/binary.md", "--json")
    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)[0]
    assert (row["visibility"], row["visibility_source"]) == ("private", "frontmatter")


def test_classify_invalid_byte_inside_frontmatter_value_stays_private(vault: Path) -> None:
    (vault / "knowledge-base" / "people" / "bad-fm.md").write_bytes(b"---\nname: Sam\xff\nvisibility: private\n---\n")
    row = json.loads(_run(vault, "classify", "knowledge-base/people/bad-fm.md", "--json").stdout)[0]
    assert row["visibility"] == "private"


def test_classify_non_markdown_gets_no_text(vault: Path) -> None:
    f = vault / "knowledge-base" / "personal" / "x.txt"
    f.write_text("---\nvisibility: normal\n---\n", encoding="utf-8")
    row = json.loads(_run(vault, "classify", "knowledge-base/personal/x.txt", "--json").stdout)[0]
    assert (row["visibility"], row["visibility_source"]) == ("private", "path_default")


def test_classify_large_file_closing_fence_inside_head(vault: Path) -> None:
    big = vault / "knowledge-base" / "people" / "big.md"
    big.write_text("---\nvisibility: private\n---\n" + ("é" * 100_000), encoding="utf-8")
    row = json.loads(_run(vault, "classify", "knowledge-base/people/big.md", "--json").stdout)[0]
    assert (row["visibility"], row["visibility_source"]) == ("private", "frontmatter")


def test_classify_fence_not_closing_in_head_is_private(vault: Path) -> None:
    long_fm = vault / "knowledge-base" / "people" / "long.md"
    long_fm.write_text("---\nname: Long\n" + ("# pad\n" * 20_000) + "---\nbody\n", encoding="utf-8")
    row = json.loads(_run(vault, "classify", "knowledge-base/people/long.md", "--json").stdout)[0]
    assert (row["visibility"], row["visibility_source"]) == ("private", "forced")


def test_classify_missing_file_uses_path_defaults(vault: Path) -> None:
    row = json.loads(_run(vault, "classify", "knowledge-base/personal/not-yet.md", "--json").stdout)[0]
    assert (row["visibility"], row["visibility_source"]) == ("private", "path_default")


def test_classify_uses_vault_overlay(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text('privacy:\n  local_only_add: ["trading/**"]\n', encoding="utf-8")
    row = json.loads(_run(vault, "classify", "trading/p.md", "--json").stdout)[0]
    assert row["local_only"] is True


def test_classify_outside_vault_is_error_exit_2(vault: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("elsewhere") / "secret.md"
    outside.write_text("---\nvisibility: private\n---\nCANARY-PRIV-9\n", encoding="utf-8")
    for target in (str(outside), "../secret.md"):
        result = _run(vault, "classify", "knowledge-base/people/priya.md", target)
        assert result.exit_code == 2, result.output
        assert "CANARY-PRIV" not in result.output
        assert "outside the vault" in result.output


@pytest.mark.parametrize("reader", [PRIYA, "agent:group:team-1"])
def test_classify_agent_gets_denied_and_no_label(vault: Path, reader: str) -> None:
    paths = ["knowledge-base/people/sam.md", "knowledge-base/people/priya.md", "knowledge-base/people/nope.md"]
    result = _run(vault, "classify", *paths, "--reader", reader, "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"path": p, "denied": True} for p in paths]
    for token in ("visibility", "federation", "local_only", "shared_file", "warnings", "CANARY-PRIV"):
        assert token not in result.output


def test_classify_agent_text_form_and_env_reader(vault: Path) -> None:
    result = _run(vault, "classify", "knowledge-base/people/sam.md", env={"SCOUT_READER": PRIYA})
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["knowledge-base/people/sam.md: denied"]


def test_classify_agent_gets_no_existence_oracle(vault: Path) -> None:
    # Outside-vault and missing paths look exactly like real private ones to an agent.
    result = _run(vault, "classify", "../x.md", "knowledge-base/people/nope.md", "--reader", PRIYA, "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {"path": "../x.md", "denied": True},
        {"path": "knowledge-base/people/nope.md", "denied": True},
    ]


def test_classify_bad_env_reader_fails_closed(vault: Path) -> None:
    result = _run(vault, "classify", "knowledge-base/people/sam.md", "--json", env={"SCOUT_READER": "indexer"})
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"path": "knowledge-base/people/sam.md", "denied": True}]


@pytest.mark.parametrize(
    ("flag", "env"),
    [("owner", {"SCOUT_READER": "autonomous"}), ("indexer", {}), ("agent:org", {}), ("owner", {"SCOUT_MODE": "x"})],
)
def test_classify_reader_errors_exit_2(vault: Path, flag: str, env: dict[str, str]) -> None:
    result = _run(vault, "classify", "knowledge-base/people/sam.md", "--reader", flag, env=env)
    assert result.exit_code == 2
    assert "CANARY-PRIV" not in result.output


def test_classify_requires_a_path(vault: Path) -> None:
    result = _run(vault, "classify")
    assert result.exit_code == 2


# ----- kb policy -------------------------------------------------------------------


def test_policy_json_is_effective_policy(vault: Path) -> None:
    result = _run(vault, "policy", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == load_policy(None).to_json()


def test_policy_json_includes_overlay_and_warnings(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text(
        'privacy:\n  local_only_add: ["trading/**"]\n  federation: {enabled: true}\n', encoding="utf-8"
    )
    data = json.loads(_run(vault, "policy", "--json").stdout)
    assert "trading/**" in data["local_only"]["default"]
    assert data["warnings"] == ["federation-reserved"]


def test_policy_text(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text('privacy:\n  local_only_add: ["trading/**"]\n', encoding="utf-8")
    result = _run(vault, "policy")
    assert result.exit_code == 0, result.output
    assert "trading/**" in result.stdout
    assert "knowledge-base/personal/**" in result.stdout
    assert "owner_only" in result.stdout
    assert "{" not in result.stdout.splitlines()[0]


def test_policy_allowed_for_agent_readers(vault: Path) -> None:
    result = _run(vault, "policy", "--json", env={"SCOUT_READER": PRIYA})
    assert result.exit_code == 0
    assert json.loads(result.stdout) == load_policy(None).to_json()
    # a bad reader env doesn't break it either: policy is configuration, not content
    assert _run(vault, "policy", "--json", env={"SCOUT_READER": "bogus"}).exit_code == 0


def test_policy_reader_flag(vault: Path) -> None:
    ok = _run(vault, "policy", "--json", "--reader", PRIYA)
    assert ok.exit_code == 0
    assert json.loads(ok.stdout) == load_policy(None).to_json()
    assert _run(vault, "policy", "--reader", "owner", env={"SCOUT_READER": PRIYA}).exit_code == 2
    assert _run(vault, "policy", "--reader", "indexer").exit_code == 2


def test_classify_unknown_reader_object_is_denied_not_labelled(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scout.kb.reader as reader_mod

    monkeypatch.setattr(reader_mod, "resolve_reader", lambda flag, env: "somebody-new")
    result = _run(vault, "classify", "knowledge-base/people/sam.md", "--json")
    assert json.loads(result.stdout) == [{"path": "knowledge-base/people/sam.md", "denied": True}]


def test_classify_md_directory_is_private_forced(vault: Path) -> None:
    (vault / "knowledge-base" / "people" / "dir.md").mkdir()
    row = json.loads(_run(vault, "classify", "knowledge-base/people/dir.md", "--json").stdout)[0]
    assert (row["visibility"], row["visibility_source"], row["warnings"]) == ("private", "forced", ["file-unreadable"])


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores file modes")
def test_classify_permission_denied_md_is_private_forced(vault: Path) -> None:
    note = vault / "knowledge-base" / "people" / "locked.md"
    note.write_text("---\nvisibility: normal\n---\n", encoding="utf-8")
    note.chmod(0)
    try:
        row = json.loads(_run(vault, "classify", "knowledge-base/people/locked.md", "--json").stdout)[0]
    finally:
        note.chmod(0o600)
    assert (row["visibility"], row["visibility_source"], row["warnings"]) == ("private", "forced", ["file-unreadable"])
