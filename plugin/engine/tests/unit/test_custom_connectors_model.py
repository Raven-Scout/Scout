"""custom_connectors: parsing and validation of connectors.custom.yaml entries."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout import custom_connectors as cc

PRESETS = {"mail": {"summary": "Mail", "inbound": "Inbox rules for {{USER_NAME}}.", "outbound": "Sent rules."}}
RESERVED = {"slack", "email", "gmail", "github"}


def _parse(key: str, body: object) -> tuple[cc.CustomConnector | None, list[cc.Issue]]:
    return cc.parse_connector(key, body, reserved=RESERVED, presets=PRESETS)


def _messages(issues: list[cc.Issue]) -> str:
    return " | ".join(f"{i.path}: {i.message}" for i in issues)


MAIL = {
    "display_name": "Mail suite",
    "server": "example_suite",
    "probe": "mcp__example_suite__list_folders",
    "preset": "mail",
    "inbound": {"tools": ["mcp__example_suite__search_messages"]},
    "outbound": {"tools": ["mcp__example_suite__search_messages"]},
}


def test_valid_mcp_connector_fills_guidance_from_preset():
    c, issues = _parse("suite_mail", MAIL)
    assert issues == []
    assert c is not None
    assert c.server == "example_suite"
    assert c.health_key == "mcp:example_suite"
    assert c.activities["inbound"].guidance == "Inbox rules for {{USER_NAME}}."
    assert c.activities["inbound"].tools == (cc.ToolRef("mcp", "mcp__example_suite__search_messages"),)
    assert "lookup" not in c.activities


def test_explicit_focus_overrides_preset():
    body = {**MAIL, "inbound": {"tools": MAIL["inbound"]["tools"], "focus": "Only the shared queue."}}
    c, issues = _parse("suite_mail", body)
    assert issues == [] and c is not None
    assert c.activities["inbound"].guidance == "Only the shared queue."


def test_bash_connector_without_server_keys_health_on_its_own_key():
    body = {
        "display_name": "Tickets",
        "probe": {"bash": "tix whoami"},
        "inbound": {"tools": [{"bash": "tix list --assignee me"}], "focus": "Tickets that changed."},
    }
    c, issues = _parse("tickets", body)
    assert issues == [] and c is not None
    assert c.server is None
    assert c.health_key == "tickets"
    assert c.probe.binary == "tix"


@pytest.mark.parametrize(
    ("key", "body", "expected"),
    [
        ("Bad-Key", MAIL, "lowercase"),
        ("slack", MAIL, "built-in"),
        ("gmail", MAIL, "built-in"),
        ("suite_mail", "not a mapping", "must be a mapping"),
        ("suite_mail", {**MAIL, "colour": "blue"}, "unknown field"),
        ("suite_mail", {k: v for k, v in MAIL.items() if k != "display_name"}, "display_name: required"),
        ("suite_mail", {k: v for k, v in MAIL.items() if k != "probe"}, "probe: required"),
        ("suite_mail", {**MAIL, "preset": "fax"}, "unknown preset"),
        ("suite_mail", {**MAIL, "inbound": {"tools": []}}, "non-empty list"),
        ("suite_mail", {**MAIL, "inbound": {"tools": ["search_messages"]}}, "not an MCP tool name"),
        ("suite_mail", {**MAIL, "server": "other_suite"}, "belongs to server"),
        ("suite_mail", {k: v for k, v in MAIL.items() if k != "server"}, "server: required"),
        ("suite_mail", {"display_name": "X", "probe": MAIL["probe"], "server": "example_suite"}, "at least one"),
        (
            "dataplat",
            {
                "display_name": "D",
                "server": "dataplat",
                "probe": "mcp__dataplat__info",
                "lookup": {"tools": ["mcp__dataplat__search"]},
            },
            "when: required",
        ),
        ("suite_mail", {**MAIL, "needs_user_input": ["Bad Name"]}, "needs_user_input"),
        ("suite_mail", {**MAIL, "required_in_types": ["sometimes"]}, "required_in_types"),
        ("suite_mail", {**MAIL, "notes": "token ghp_abcdefghijklmnop"}, "credential"),
        ("suite_mail", {**MAIL, "inbound": {"tools": MAIL["inbound"]["tools"], "when": "x"}}, "unknown field"),
    ],
)
def test_invalid_definitions_are_rejected_with_a_field_path(key, body, expected):
    c, issues = _parse(key, body)
    assert c is None
    assert expected in _messages(issues)


def test_credential_rejection_points_at_the_tools_own_sign_in_not_needs_user_input():
    """F4: inputs are rendered verbatim into SKILL.md, so the message must not steer
    a secret into needs_user_input."""
    _, issues = _parse("suite_mail", {**MAIL, "notes": "token ghp_abcdefghijklmnop"})
    assert [i.message for i in issues] == [
        "looks like it contains a credential; Scout never stores credentials — sign the tool in "
        "through its own MCP connector or CLI instead"
    ]
    assert "needs_user_input" not in _messages(issues)


@pytest.mark.parametrize("text", ["Watch the task-list board.", "Use a risk-based triage.", "Ask about sk-8 sizing."])
def test_credential_guard_ignores_ordinary_words(text):
    c, issues = _parse("suite_mail", {**MAIL, "notes": text})
    assert issues == [], _messages(issues)
    assert c is not None and c.notes == text


def test_parse_file_keeps_valid_entries_when_another_is_broken():
    raw = {"schema_version": 1, "connectors": {"suite_mail": MAIL, "broken": {"display_name": "B"}}}
    loaded = cc.parse_file(raw, reserved=RESERVED, presets=PRESETS)
    assert set(loaded.connectors) == {"suite_mail"}
    assert any(i.path.startswith("connectors.broken") for i in loaded.issues)
    assert set(loaded.raw) == {"suite_mail", "broken"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preset", ["mail"]),
        ("preset", {"a": "b"}),
        ("required_in_types", [["briefing"]]),
        ("required_in_types", 0),
        ("needs_user_input", False),
        ("notes", 0),
    ],
)
def test_wrongly_typed_field_is_an_issue_and_a_valid_sibling_still_parses(field, value):
    """F1: an unhashable `preset` / `required_in_types` item crashed the whole engine with
    TypeError, and falsy non-text values were silently coerced. Each is an issue on that
    entry only; the other entries keep working (spec §2)."""
    raw = {"schema_version": 1, "connectors": {"suite_mail": MAIL, "bad_one": {**MAIL, field: value}}}
    loaded = cc.parse_file(raw, reserved=RESERVED, presets=PRESETS)
    assert set(loaded.connectors) == {"suite_mail"}
    assert any(i.path == f"connectors.bad_one.{field}" for i in loaded.issues), _messages(loaded.issues)


def test_parse_file_turns_an_unexpected_validation_error_into_an_issue(monkeypatch):
    """F1 defense in depth: a bug in parse_connector costs that one entry, not the file."""
    real = cc.parse_connector

    def flaky(key, body, **kw):
        if key == "bad_one":
            raise RuntimeError("boom")
        return real(key, body, **kw)

    monkeypatch.setattr(cc, "parse_connector", flaky)
    raw = {"schema_version": 1, "connectors": {"suite_mail": MAIL, "bad_one": MAIL}}
    loaded = cc.parse_file(raw, reserved=RESERVED, presets=PRESETS)
    assert set(loaded.connectors) == {"suite_mail"}
    assert [(i.path, i.message) for i in loaded.issues] == [("connectors.bad_one", "could not be validated: boom")]


@pytest.mark.parametrize(
    "raw", [["a list"], {"schema_version": 2, "connectors": {}}, {"schema_version": 1, "connectors": []}]
)
def test_parse_file_rejects_bad_top_level(raw):
    loaded = cc.parse_file(raw, reserved=RESERVED, presets=PRESETS)
    assert loaded.connectors == {}
    assert loaded.issues


def test_load_missing_file_is_empty(tmp_path: Path):
    assert cc.load(tmp_path, plugin_root=cc.default_plugin_root()).connectors == {}


def test_load_unparseable_file_reports_one_issue(tmp_path: Path):
    (tmp_path / cc.CUSTOM_FILE).write_text("connectors: [unclosed\n")
    loaded = cc.load(tmp_path, plugin_root=cc.default_plugin_root())
    assert loaded.connectors == {}
    assert loaded.issues and loaded.issues[0].path == cc.CUSTOM_FILE


def test_write_then_load_round_trips(tmp_path: Path):
    body = {
        "display_name": "Tickets",
        "probe": {"bash": "tix whoami"},
        "inbound": {"tools": [{"bash": "tix list"}], "focus": "Changed tickets."},
    }
    cc.write(tmp_path, {"tickets": body})
    text = (tmp_path / cc.CUSTOM_FILE).read_text()
    assert text.startswith("# Custom connectors")
    assert yaml.safe_load(text) == {"schema_version": 1, "connectors": {"tickets": body}}
    assert set(cc.load(tmp_path, plugin_root=cc.default_plugin_root()).connectors) == {"tickets"}


def test_reserved_keys_cover_probes_aliases_and_phase_requires():
    reserved = cc.reserved_keys(cc.default_plugin_root())
    assert {"slack", "email", "gmail", "github", "claude_sessions"} <= reserved


def test_first_binary_skips_env_prefix_and_path():
    assert cc.first_binary("FOO=1 /usr/local/bin/tix list") == "tix"
    assert cc.first_binary("") is None


def test_first_binary_sees_through_wrappers():
    assert cc.first_binary("timeout 10 tixcli list") == "tixcli"
    assert cc.first_binary("env FOO=1 nice -n 5 tix list") == "tix"
    # Known limit (task-4 brief): `-u`'s own value is not skipped.
    assert cc.first_binary("sudo -u alex tix x") == "alex"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("osascript", True),
        ("npm", True),
        ("docker", True),
        ("ssh", True),
        ("open", True),
        ("echo", True),
        ("cat", True),
        ("printf", True),
        ("sh", True),
        ("bash", True),
        ("zsh", True),
        ("curl", True),
        (None, True),
        ("tix", False),
        ("tixcli", False),
    ],
)
def test_is_generic_binary(name, expected):
    assert cc.is_generic_binary(name) is expected


def test_bash_binaries_maps_probe_and_tools_and_skips_generic(tmp_path: Path):
    cc.write(
        tmp_path,
        {
            "tickets": {
                "display_name": "T",
                "probe": {"bash": "tix whoami"},
                "inbound": {"tools": [{"bash": "tix list"}], "focus": "x"},
            },
            "webhook": {
                "display_name": "W",
                "probe": {"bash": "curl -sf https://example.com/health"},
                "inbound": {"tools": [{"bash": "curl -s https://example.com/items"}], "focus": "x"},
            },
        },
    )
    assert cc.bash_binaries(tmp_path) == {"tix": "tickets"}


def test_bash_binaries_tolerates_garbage(tmp_path: Path):
    (tmp_path / cc.CUSTOM_FILE).write_text(":::\n")
    assert cc.bash_binaries(tmp_path) == {}


def test_enabled_keys_reads_connectors_enabled(tmp_path: Path):
    (tmp_path / "scout-config.yaml").write_text(yaml.safe_dump({"connectors": {"enabled": ["tickets", "suite_mail"]}}))
    assert cc.enabled_keys(tmp_path) == {"tickets", "suite_mail"}


@pytest.mark.parametrize(
    "write_config",
    [
        lambda p: None,  # missing file
        lambda p: (p / "scout-config.yaml").write_text("connectors: [unclosed\n"),  # unparseable
        lambda p: (p / "scout-config.yaml").write_text("- a\n- b\n"),  # not a mapping at top level
        lambda p: (p / "scout-config.yaml").write_text(yaml.safe_dump({"connectors": "nope"})),  # connectors not a map
        lambda p: (p / "scout-config.yaml").write_text(yaml.safe_dump({"connectors": {"enabled": "tickets"}})),
        lambda p: (p / "scout-config.yaml").write_text(yaml.safe_dump({"connectors": {}})),  # no 'enabled' key
    ],
)
def test_enabled_keys_fails_closed_on_missing_or_malformed_config(tmp_path: Path, write_config):
    write_config(tmp_path)
    assert cc.enabled_keys(tmp_path) == set()


def test_enabled_keys_ignores_non_string_entries(tmp_path: Path):
    (tmp_path / "scout-config.yaml").write_text(yaml.safe_dump({"connectors": {"enabled": ["tickets", 5, None]}}))
    assert cc.enabled_keys(tmp_path) == {"tickets"}
