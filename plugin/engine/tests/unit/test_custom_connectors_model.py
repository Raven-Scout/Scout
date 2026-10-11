"""custom_connectors: parsing and validation of connectors.custom.yaml entries."""

from __future__ import annotations

import math
import time
from pathlib import Path

import pytest
import yaml

from scout import custom_connectors as cc
from scout import yaml_blocks

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


@pytest.mark.parametrize(
    "tool",
    [
        "mcp__example_suite__*",
        "mcp__example_suite__*,list",
        "mcp__example_suite__* list",
        "mcp__example_suite__list_x,Bash(curl:*)",
        "mcp__example_suite__list_x\n",
        "mcp__example suite__list_x",
    ],
)
def test_a_tool_name_outside_the_strict_pattern_is_rejected(tool):
    """One validator: `custom add` and the draft gate both refuse a name that
    `--allowedTools` would split into more than one rule."""
    c, issues = _parse("suite_mail", {**MAIL, "inbound": {"tools": [tool]}})
    assert c is None
    assert f"connectors.suite_mail.inbound.tools[0]: {tool!r} is not an MCP tool name" in _messages(issues)
    c, issues = _parse("suite_mail", {**MAIL, "probe": tool})
    assert c is None and "connectors.suite_mail.probe" in _messages(issues)


@pytest.mark.parametrize(
    "tool", ["mcp__example_suite__search_messages", "mcp__plugin_example-kit_search-tool__find", "mcp__a__b"]
)
def test_tool_name_pattern_accepts_real_shapes(tool):
    assert cc.MCP_TOOL_RE.match(tool)


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


def test_bash_binaries_skips_an_entry_that_is_not_a_mapping(tmp_path: Path):
    tickets = {"display_name": "T", "probe": {"bash": "tix whoami"}, "inbound": {"tools": [{"bash": "tix list"}]}}
    cc.write(tmp_path, {"half_written": "TODO", "tickets": tickets})
    assert cc.bash_binaries(tmp_path) == {"tix": "tickets"}


@pytest.mark.parametrize("probe", [{"bash": "   "}, {"bash": "tix whoami", "shell": "zsh"}, 7, ["tix", "whoami"]])
def test_a_probe_that_is_neither_an_mcp_name_nor_a_bash_command_is_rejected(probe: object):
    c, issues = _parse("suite_mail", {**MAIL, "probe": probe})
    assert c is None
    assert ("connectors.suite_mail.probe", 'must be an MCP tool name or {bash: "<command>"}') in [
        (i.path, i.message) for i in issues
    ]


@pytest.mark.parametrize("block", ["search everything", ["mcp__example_suite__search_messages"], None])
def test_an_activity_that_is_not_a_mapping_is_rejected(block: object):
    c, issues = _parse("suite_mail", {**MAIL, "lookup": block})
    assert c is None
    assert [(i.path, i.message) for i in issues] == [("connectors.suite_mail.lookup", "must be a mapping with `tools`")]


def test_an_empty_file_or_a_null_connectors_block_is_empty_and_quiet():
    assert cc.parse_file(None, reserved=RESERVED, presets=PRESETS) == cc.CustomLoad()
    loaded = cc.parse_file({"schema_version": 1, "connectors": None}, reserved=RESERVED, presets=PRESETS)
    assert loaded.connectors == {} and loaded.issues == [] and loaded.raw == {}


def test_load_presets_skips_unreadable_and_non_mapping_files(tmp_path: Path):
    presets = tmp_path / "phases" / "presets"
    presets.mkdir(parents=True)
    (presets / "mail.yaml").write_text("summary: Mail\ninbound: |\n  Inbox rules.\nlimit: 5\n", encoding="utf-8")
    (presets / "broken.yaml").write_text("summary: [unclosed\n", encoding="utf-8")
    (presets / "listy.yaml").write_text("- summary\n- inbound\n", encoding="utf-8")
    (presets / "binary.yaml").write_bytes(b"\xff\xfe\x00")
    # Non-text values (limit: 5) are dropped; the file itself still loads.
    assert cc.load_presets(tmp_path) == {"mail": {"summary": "Mail", "inbound": "Inbox rules."}}


def test_reserved_keys_survive_a_missing_registry_and_a_malformed_phase_file(tmp_path: Path):
    from scout.scripts.connector_probes import CONNECTOR_KEY_ALIASES

    phases = tmp_path / "phases" / "core"
    phases.mkdir(parents=True)
    (phases / "no-frontmatter.md").write_text("# just prose, no --- fence\n", encoding="utf-8")
    # No templates/connector-probes.yaml at all: the registry read fails and is skipped.
    reserved = cc.reserved_keys(tmp_path)
    assert reserved == set(CONNECTOR_KEY_ALIASES) | set(CONNECTOR_KEY_ALIASES.values())


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


# --- Task 5: sanitize free text and undeclared inputs -----------------------------------


def test_display_name_must_be_a_single_line():
    c, issues = _parse("suite_mail", {**MAIL, "display_name": "Mail suite\nSecond line"})
    assert c is None
    assert "display_name: must be a single line" in _messages(issues)


def test_display_name_with_carriage_return_is_rejected():
    c, issues = _parse("suite_mail", {**MAIL, "display_name": "Mail suite\rSecond line"})
    assert c is None
    assert "display_name: must be a single line" in _messages(issues)


@pytest.mark.parametrize(
    "notes",
    [
        "# Heading",
        "## Heading",
        "Some text\n# Heading\nmore text",
        "Some text\n   # Heading indented",
    ],
)
def test_notes_rejects_markdown_headings(notes):
    c, issues = _parse("suite_mail", {**MAIL, "notes": notes})
    assert c is None
    assert "notes: must not contain markdown headings; they would break the brain file's structure" in _messages(issues)


@pytest.mark.parametrize(
    "notes",
    [
        "see PROJ-1234 #2",
        "Line one.\nLine two mentions PROJ-1234 #2 mid-line.\nLine three.",
        "#not-a-heading-because-no-space-or-hash",
        "#",
    ],
)
def test_notes_accepts_mid_line_hash_and_multiline_text_without_headings(notes):
    c, issues = _parse("suite_mail", {**MAIL, "notes": notes})
    assert issues == [], _messages(issues)
    assert c is not None and c.notes == notes.strip()


def test_explicit_focus_with_heading_is_rejected():
    body = {**MAIL, "inbound": {"tools": MAIL["inbound"]["tools"], "focus": "# Heading\nrest of guidance"}}
    c, issues = _parse("suite_mail", body)
    assert c is None
    assert "inbound.focus: must not contain markdown headings; they would break the brain file's structure" in (
        _messages(issues)
    )


def test_explicit_when_with_heading_is_rejected():
    body = {
        "display_name": "D",
        "server": "dataplat",
        "probe": "mcp__dataplat__info",
        "lookup": {"tools": ["mcp__dataplat__search"], "when": "## Heading"},
    }
    c, issues = _parse("dataplat", body)
    assert c is None
    assert "lookup.when: must not contain markdown headings; they would break the brain file's structure" in (
        _messages(issues)
    )


def test_preset_guidance_with_heading_shaped_text_is_trusted_not_rejected():
    """The heading rule only applies to explicit focus/when text; preset text is shipped and trusted."""
    presets = {**PRESETS, "heady": {"summary": "H", "inbound": "# Not actually checked"}}
    body = {
        "display_name": "Mail suite",
        "server": "example_suite",
        "probe": "mcp__example_suite__list_folders",
        "preset": "heady",
        "inbound": {"tools": ["mcp__example_suite__search_messages"]},
    }
    c, issues = cc.parse_connector("suite_mail", body, reserved=RESERVED, presets=presets)
    assert issues == [], _messages(issues)
    assert c is not None
    assert c.activities["inbound"].guidance == "# Not actually checked"


def test_undeclared_input_placeholder_is_rejected():
    body = {
        **MAIL,
        "inbound": {"tools": [{"bash": "suite search --workspace {{INPUT_WORKSPACE_ID}}"}]},
    }
    c, issues = _parse("suite_mail", body)
    assert c is None
    assert (
        "needs_user_input: uses {{INPUT_WORKSPACE_ID}} but does not declare `workspace_id` in needs_user_input"
        in _messages(issues)
    )


def test_declared_input_placeholder_is_accepted():
    body = {
        **MAIL,
        "needs_user_input": ["workspace_id"],
        "inbound": {"tools": [{"bash": "suite search --workspace {{INPUT_WORKSPACE_ID}}"}]},
    }
    c, issues = _parse("suite_mail", body)
    assert issues == [], _messages(issues)
    assert c is not None
    assert c.needs_user_input == ("workspace_id",)


# --- Task 6: comment-preserving write ----------------------------------------------

_TICKETS = {
    "display_name": "Tickets",
    "probe": {"bash": "tix whoami"},
    "inbound": {"tools": [{"bash": "tix list"}], "focus": "Changed tickets."},
}
_ALPHA = {
    "display_name": "Alpha",
    "probe": "mcp__dataplat__info",
    "lookup": {"tools": ["mcp__dataplat__search"], "when": "x"},
}
_ZETA = {
    "display_name": "Zeta",
    "probe": "mcp__dataplat__info",
    "lookup": {"tools": ["mcp__dataplat__search"], "when": "y"},
}


def test_header_and_untouched_entry_comments_survive_add_and_remove(tmp_path: Path):
    text = (
        "# Custom connectors file.\n"
        "# Keep hands off the schema_version line.\n"
        "schema_version: 1\n"
        "connectors:\n"
        "  # The ticket tracker.\n"
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS, "alpha": _ALPHA})
    after_add = (tmp_path / cc.CUSTOM_FILE).read_text()
    assert "# Custom connectors file." in after_add
    assert "# Keep hands off the schema_version line." in after_add
    assert "# The ticket tracker." in after_add
    assert yaml.safe_load(after_add) == {"schema_version": 1, "connectors": {"tickets": _TICKETS, "alpha": _ALPHA}}

    cc.write(tmp_path, {"tickets": _TICKETS})
    after_remove = (tmp_path / cc.CUSTOM_FILE).read_text()
    assert "# Custom connectors file." in after_remove
    assert "# The ticket tracker." in after_remove
    assert yaml.safe_load(after_remove) == {"schema_version": 1, "connectors": {"tickets": _TICKETS}}


def test_comments_survive_add_and_remove_after_a_dump_written_file(tmp_path: Path):
    """dump()'s own block-style output is itself a valid target for the per-entry
    rewriter, so after the very first write, every later add/remove takes the
    comment-preserving path rather than the full-dump path."""
    cc.write(tmp_path, {"tickets": _TICKETS})
    path = tmp_path / cc.CUSTOM_FILE
    text = path.read_text()
    assert yaml.safe_load(text) == {"schema_version": 1, "connectors": {"tickets": _TICKETS}}
    text = text.replace("  tickets:\n", "  # Jira-ish ticket tracker\n  tickets:\n")
    path.write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS, "alpha": _ALPHA})
    after_add = path.read_text()
    assert "# Jira-ish ticket tracker" in after_add
    assert yaml.safe_load(after_add) == {"schema_version": 1, "connectors": {"tickets": _TICKETS, "alpha": _ALPHA}}

    cc.write(tmp_path, {"tickets": _TICKETS})
    after_remove = path.read_text()
    assert "# Jira-ish ticket tracker" in after_remove
    assert yaml.safe_load(after_remove) == {"schema_version": 1, "connectors": {"tickets": _TICKETS}}


def test_changed_entry_keeps_its_position_and_neighbours_comments_survive(tmp_path: Path):
    """A changed entry is replaced in place, not moved to the end; its own inner
    comment may be lost, but comments on its neighbours are untouched."""
    text = (
        "# header comment\n"
        "schema_version: 1\n"
        "connectors:\n"
        "  # alpha note\n"
        "  alpha:\n"
        "    display_name: Alpha\n"
        "    probe: mcp__dataplat__info\n"
        "    lookup:\n"
        "      tools:\n"
        "      - mcp__dataplat__search\n"
        "      when: x\n"
        "  tickets:\n"
        "    # inner note, lost on change\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "  # zeta note\n"
        "  zeta:\n"
        "    display_name: Zeta\n"
        "    probe: mcp__dataplat__info\n"
        "    lookup:\n"
        "      tools:\n"
        "      - mcp__dataplat__search\n"
        "      when: y\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)
    new_tickets = {**_TICKETS, "inbound": {**_TICKETS["inbound"], "focus": "Updated focus."}}

    cc.write(tmp_path, {"alpha": _ALPHA, "tickets": new_tickets, "zeta": _ZETA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    loaded = yaml.safe_load(result)
    assert loaded == {"schema_version": 1, "connectors": {"alpha": _ALPHA, "tickets": new_tickets, "zeta": _ZETA}}
    assert list(loaded["connectors"]) == ["alpha", "tickets", "zeta"]
    assert "# alpha note" in result
    assert "# zeta note" in result
    assert "# inner note, lost on change" not in result


def test_trailing_comment_block_survives(tmp_path: Path):
    text = (
        "schema_version: 1\n"
        "connectors:\n"
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "\n"
        "# Keep this note at the end of the file.\n"
        "# Second line of the note.\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)
    new_tickets = {**_TICKETS, "inbound": {**_TICKETS["inbound"], "focus": "Updated focus."}}

    cc.write(tmp_path, {"tickets": new_tickets})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": new_tickets}}
    assert result.endswith("# Keep this note at the end of the file.\n# Second line of the note.\n")


def test_flow_style_file_falls_back_to_a_plain_dump(tmp_path: Path):
    text = (
        "schema_version: 1\n"
        "connectors: {tickets: {display_name: Tickets, probe: {bash: 'tix whoami'}, "
        "inbound: {tools: [{bash: 'tix list'}], focus: 'Changed tickets.'}}}\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS, "alpha": _ALPHA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# Custom connectors")
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": _TICKETS, "alpha": _ALPHA}}


def test_empty_connectors_file_then_add_works(tmp_path: Path):
    """dump() writes an empty mapping as flow-style `connectors: {}`. The splitter
    recognizes that exact spelling as an empty block (fix round 1, item 6), so the
    add takes the fast per-entry path rather than falling all the way back."""
    cc.write(tmp_path, {})
    text = (tmp_path / cc.CUSTOM_FILE).read_text()
    assert "connectors: {}" in text

    cc.write(tmp_path, {"tickets": _TICKETS})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": _TICKETS}}


@pytest.mark.parametrize(
    "text",
    [
        (
            "---\n"
            "schema_version: 1\n"
            "connectors:\n"
            "  tickets:\n"
            "    display_name: Tickets\n"
            "    probe:\n"
            "      bash: tix whoami\n"
            "    inbound:\n"
            "      tools:\n"
            "      - bash: tix list\n"
            "      focus: Changed tickets.\n"
        ),
        ("schema_version: 1\nconnectors:\n\ttickets:\n\t\tdisplay_name: Tickets\n\t\tprobe:\n\t\t\tbash: tix whoami\n"),
    ],
    ids=["document-marker", "tabs"],
)
def test_document_marker_or_tabs_fall_back_to_a_plain_dump(tmp_path: Path, text: str):
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS, "alpha": _ALPHA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# Custom connectors")
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": _TICKETS, "alpha": _ALPHA}}


def test_bool_resolving_key_does_not_lose_or_duplicate_the_entry(tmp_path: Path):
    """A hand-edited file with an unquoted `no:` key parses, line by line, as the
    boolean False rather than the string "no" -- naive key matching would treat the
    entry as removed and silently drop it. The result must still land exactly one
    `no` entry, correctly quoted so it round-trips as a string."""
    no_body = {
        "display_name": "Negative",
        "probe": "mcp__dataplat__info",
        "lookup": {"tools": ["mcp__dataplat__search"], "when": "x"},
    }
    text = (
        "schema_version: 1\n"
        "connectors:\n"
        "  no:\n"
        "    display_name: Negative\n"
        "    probe: mcp__dataplat__info\n"
        "    lookup:\n"
        "      tools:\n"
        "      - mcp__dataplat__search\n"
        "      when: x\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"no": no_body})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()
    loaded = yaml.safe_load(result)

    assert loaded == {"schema_version": 1, "connectors": {"no": no_body}}
    assert list(loaded["connectors"]) == ["no"]


def test_unexpected_content_after_the_connectors_block_falls_back(tmp_path: Path):
    """A column-0 line after the last entry (not a comment, not a document marker)
    is content the splitter doesn't recognize; it must fall back rather than guess."""
    text = (
        "schema_version: 1\n"
        "connectors:\n"
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "extra: 1\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS, "alpha": _ALPHA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# Custom connectors")
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": _TICKETS, "alpha": _ALPHA}}


def test_unparseable_entry_start_line_falls_back(tmp_path: Path):
    """An entry line that is itself invalid YAML on its own (an unterminated flow
    collection) can't be read as a candidate key; the splitter must bail cleanly."""
    text = "schema_version: 1\nconnectors:\n  tickets: [1, 2,\n    3]\n"
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS, "alpha": _ALPHA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# Custom connectors")
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": _TICKETS, "alpha": _ALPHA}}


def test_non_mapping_entry_line_falls_back(tmp_path: Path):
    """A two-space-indented line that parses to something other than a single-key
    mapping (here, a flow mapping with two keys) can't be a connector entry."""
    text = (
        "schema_version: 1\n"
        "connectors:\n"
        "  {a: 1, b: 2}\n"
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS, "alpha": _ALPHA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# Custom connectors")
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": _TICKETS, "alpha": _ALPHA}}


def test_stray_sequence_item_at_entry_level_is_tolerated_like_bootstrap(tmp_path: Path):
    """A `- item` line at entry level is treated as part of the previous key's
    (sequence-valued) content, not an error -- matching
    `yaml_blocks.split_blocks`'s shared behavior with bootstrap's top-level
    splitter (which must tolerate this for a list-valued top-level key)."""
    text = (
        "schema_version: 1\n"
        "connectors:\n"
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "  - stray\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS, "alpha": _ALPHA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": _TICKETS, "alpha": _ALPHA}}


def test_duplicate_entry_keys_fall_back(tmp_path: Path):
    text = "schema_version: 1\nconnectors:\n  tickets:\n    display_name: A\n  tickets:\n    display_name: B\n"
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS, "alpha": _ALPHA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# Custom connectors")
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": _TICKETS, "alpha": _ALPHA}}


def test_entry_with_unparseable_body_is_redumped_not_crashed(tmp_path: Path):
    """The entry's first line parses fine on its own (so the split succeeds), but
    its nested content is inconsistently indented, so parsing the whole block
    raises. That entry must be treated as changed (re-dumped), not crash."""
    text = "schema_version: 1\nconnectors:\n  tickets:\n    display_name: Tickets\n   probe: bad\n"
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": _TICKETS}}


def test_new_entry_appended_after_a_file_missing_its_final_newline(tmp_path: Path):
    text = (
        "schema_version: 1\n"
        "connectors:\n"
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets."
    )
    assert not text.endswith("\n")
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS, "alpha": _ALPHA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": _TICKETS, "alpha": _ALPHA}}


def test_rebuild_entries_tolerates_an_empty_header():
    """Defense in depth: `_rebuild_entries` is only ever called by
    `_write_keeping_comments` with a non-empty header (the splitter always
    includes at least the `connectors:` line), but it must not assume that --
    an empty header must not crash the "keep it in sync" patch-up."""
    assert cc._rebuild_entries([], [], [], {}) == ""


def test_a_rebuild_that_does_not_parse_falls_back_to_a_plain_dump(tmp_path: Path, monkeypatch):
    """Defense in depth: even if `_rebuild_entries` ever produced text that isn't
    valid YAML at all, `write` must not raise or write garbage -- it falls back."""
    cc.write(tmp_path, {"tickets": _TICKETS})
    monkeypatch.setattr(cc, "_rebuild_entries", lambda *a, **k: "connectors: [unterminated\n")

    cc.write(tmp_path, {"tickets": _TICKETS, "alpha": _ALPHA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# Custom connectors")
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": _TICKETS, "alpha": _ALPHA}}


# --- Fix round 1 --------------------------------------------------------------------


def test_block_scalar_hash_line_in_an_unchanged_entry_survives(tmp_path: Path):
    """Item 1(a): a literal `|` block-scalar line that happens to start with `#`
    (not a real YAML comment -- it's scalar content) must not be mistaken by the
    tail-trim for a gap comment belonging to the next entry. An unchanged entry
    must come back verbatim, with that line intact and not duplicated."""
    body = {
        "display_name": "Tickets",
        "probe": {"bash": "tix whoami"},
        "inbound": {"tools": [{"bash": "tix list"}], "focus": "Changed tickets."},
        "notes": "Steps for escalation:\n#2 is the escalation path\n",
    }
    text = (
        "schema_version: 1\n"
        "connectors:\n"
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "    notes: |\n"
        "      Steps for escalation:\n"
        "      #2 is the escalation path\n"
        "  alpha:\n"
        "    display_name: Alpha\n"
        "    probe: mcp__dataplat__info\n"
        "    lookup:\n"
        "      tools:\n"
        "      - mcp__dataplat__search\n"
        "      when: x\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)
    # Confirm "tickets" really is unchanged relative to `body`, not just close.
    assert yaml.safe_load(text)["connectors"]["tickets"] == body

    cc.write(tmp_path, {"tickets": body, "alpha": _ALPHA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.count("#2 is the escalation path") == 1
    assert "notes: |\n      Steps for escalation:\n      #2 is the escalation path\n" in result
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": body, "alpha": _ALPHA}}


def test_block_scalar_hash_line_does_not_linger_after_removal(tmp_path: Path):
    """Item 1(b): removing the entry whose `|` block scalar contains a `#`-shaped
    line must not leave a fragment of it behind as an orphan comment."""
    text = (
        "schema_version: 1\n"
        "connectors:\n"
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "    notes: |\n"
        "      Steps for escalation:\n"
        "      #2 is the escalation path\n"
        "  alpha:\n"
        "    display_name: Alpha\n"
        "    probe: mcp__dataplat__info\n"
        "    lookup:\n"
        "      tools:\n"
        "      - mcp__dataplat__search\n"
        "      when: x\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"alpha": _ALPHA})  # drop tickets entirely
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert "escalation path" not in result
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"alpha": _ALPHA}}


def test_add_then_remove_keeps_the_closing_comment_at_the_end(tmp_path: Path):
    """Item 2: a new entry must land before the trailing comment block, not after
    it -- otherwise the trailer becomes the new entry's own leading comment, and
    removing that entry again deletes it."""
    text = (
        "schema_version: 1\n"
        "connectors:\n"
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "\n"
        "# Closing note.\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS, "alpha": _ALPHA})
    after_add = (tmp_path / cc.CUSTOM_FILE).read_text()
    assert after_add.rstrip("\n").endswith("# Closing note.")
    assert yaml.safe_load(after_add) == {"schema_version": 1, "connectors": {"tickets": _TICKETS, "alpha": _ALPHA}}

    cc.write(tmp_path, {"tickets": _TICKETS})  # remove alpha again
    after_remove = (tmp_path / cc.CUSTOM_FILE).read_text()
    assert "# Closing note." in after_remove
    assert yaml.safe_load(after_remove) == {"schema_version": 1, "connectors": {"tickets": _TICKETS}}


def test_a_value_containing_nan_forces_the_full_dump_fallback(tmp_path: Path):
    """Item 3, reached honestly (no monkeypatch): `nan != nan` always, so a value
    containing one makes the round-trip safety-net comparison see a mismatch no
    matter how faithfully the text was actually reproduced."""
    weird = {"display_name": "Weird", "probe": "mcp__dataplat__info", "score": float("nan")}
    cc.write(tmp_path, {"weird": weird})  # first write: no existing file, plain dump()
    cc.write(tmp_path, {"weird": weird})  # second write: existing file, exercises the rebuild path
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# Custom connectors")
    loaded = yaml.safe_load(result)
    assert loaded["connectors"]["weird"]["display_name"] == "Weird"
    assert math.isnan(loaded["connectors"]["weird"]["score"])


def test_recursive_alias_comparison_falls_back_instead_of_crashing(tmp_path: Path):
    """Item 5: a cyclic alias makes `==` raise RecursionError rather than return
    False. Both comparisons (`_rebuild_entries`'s per-entry check and the final
    round-trip check) must treat that as "not equal" and fall back, not crash."""
    text = (
        "schema_version: 1\n"
        "connectors:\n"
        "  loopy:\n"
        "    display_name: Loopy\n"
        "    probe: mcp__dataplat__info\n"
        "    lookup:\n"
        "      tools:\n"
        "      - mcp__dataplat__search\n"
        "      when: x\n"
        "    loop: &l\n"
        "      self: *l\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cyclic: dict = {}
    cyclic["self"] = cyclic
    new_body = {
        "display_name": "Loopy",
        "probe": "mcp__dataplat__info",
        "lookup": {"tools": ["mcp__dataplat__search"], "when": "x"},
        "loop": cyclic,
    }

    cc.write(tmp_path, {"loopy": new_body})  # must not raise RecursionError
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# Custom connectors")
    loaded = yaml.safe_load(result)
    assert loaded["connectors"]["loopy"]["display_name"] == "Loopy"
    assert loaded["connectors"]["loopy"]["loop"] is loaded["connectors"]["loopy"]["loop"]["self"]


def test_header_and_trailer_survive_remove_last_then_add(tmp_path: Path):
    """Item 6: removing the only entry must not force a full re-dump (which would
    lose the header and trailer forever) -- `connectors:` becomes `connectors: {}`,
    which the splitter also recognizes, so a later add still takes the fast path."""
    text = (
        "# Custom connectors file.\n"
        "schema_version: 1\n"
        "connectors:\n"
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "\n"
        "# Trailing note.\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {})  # remove the only entry
    after_remove = (tmp_path / cc.CUSTOM_FILE).read_text()
    assert "# Custom connectors file." in after_remove
    assert "# Trailing note." in after_remove
    assert "connectors: {}" in after_remove
    assert yaml.safe_load(after_remove) == {"schema_version": 1, "connectors": {}}

    cc.write(tmp_path, {"alpha": _ALPHA})  # add again
    after_add = (tmp_path / cc.CUSTOM_FILE).read_text()
    assert "# Custom connectors file." in after_add
    assert "# Trailing note." in after_add
    assert yaml.safe_load(after_add) == {"schema_version": 1, "connectors": {"alpha": _ALPHA}}


# --- Fix round 2 --------------------------------------------------------------------

_HDR = "# my header\nschema_version: 1\nconnectors:\n"


def test_trailer_with_trailing_blank_line_survives_changing_the_last_entry(tmp_path: Path):
    """Item A, reviewer case 2e: round 1's "never trim a blank line" rule made
    the tail-walk stop at the first blank line, so a trailer followed by a
    trailing blank line belonged to the entry above it -- and was deleted when
    that entry changed. The closing comment must survive a change to tickets."""
    text = _HDR + (
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "\n"
        "# Closing note.\n"
        "\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": dict(_TICKETS, display_name="Tix")})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert "# Closing note." in result
    assert yaml.safe_load(result) == {
        "schema_version": 1,
        "connectors": {"tickets": dict(_TICKETS, display_name="Tix")},
    }


def test_trailer_with_trailing_blank_line_survives_removing_the_last_entry(tmp_path: Path):
    """Item A, reviewer case 2f: same hazard as above, but removing (rather than
    changing) the last of two entries."""
    text = _HDR + (
        "  alpha:\n"
        "    display_name: Alpha\n"
        "    probe: mcp__dataplat__info\n"
        "    lookup:\n"
        "      tools:\n"
        "      - mcp__dataplat__search\n"
        "      when: x\n"
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "\n"
        "# Closing note.\n"
        "\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"alpha": _ALPHA})  # drop tickets
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert "# Closing note." in result
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"alpha": _ALPHA}}


def test_gap_comment_with_blank_lines_both_sides_survives_removing_previous_entry(tmp_path: Path):
    """Item A, reviewer case 3a: a gap comment documenting the next entry, with a
    blank line on both sides, must survive even when the entry *before* it is
    removed (not just when the entry it documents changes)."""
    text = _HDR + (
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "\n"
        "# About alpha\n"
        "\n"
        "  alpha:\n"
        "    display_name: Alpha\n"
        "    probe: mcp__dataplat__info\n"
        "    lookup:\n"
        "      tools:\n"
        "      - mcp__dataplat__search\n"
        "      when: x\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"alpha": _ALPHA})  # drop tickets, the entry *before* the gap comment
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert "# About alpha" in result
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"alpha": _ALPHA}}


def test_gap_comment_with_blank_lines_both_sides_survives_changing_previous_entry(tmp_path: Path):
    """Item A, reviewer case 3b: same gap comment, but the entry before it
    changes instead of being removed."""
    text = _HDR + (
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "\n"
        "# About alpha\n"
        "\n"
        "  alpha:\n"
        "    display_name: Alpha\n"
        "    probe: mcp__dataplat__info\n"
        "    lookup:\n"
        "      tools:\n"
        "      - mcp__dataplat__search\n"
        "      when: x\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": dict(_TICKETS, display_name="Tix"), "alpha": _ALPHA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert "# About alpha" in result
    assert yaml.safe_load(result) == {
        "schema_version": 1,
        "connectors": {"tickets": dict(_TICKETS, display_name="Tix"), "alpha": _ALPHA},
    }


def test_indented_gap_comment_then_blank_survives_removing_previous_entry(tmp_path: Path):
    """Item A, reviewer case 3e: an indented gap comment (not at entry level),
    followed by a blank line, must also survive -- the old "never trim a blank
    line" rule stopped the tail-walk right after the indented comment, so both
    it and the comment were attributed to (and deleted with) the previous
    entry."""
    text = _HDR + (
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "  # About alpha (indented)\n"
        "\n"
        "  alpha:\n"
        "    display_name: Alpha\n"
        "    probe: mcp__dataplat__info\n"
        "    lookup:\n"
        "      tools:\n"
        "      - mcp__dataplat__search\n"
        "      when: x\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"alpha": _ALPHA})  # drop tickets
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert "# About alpha" in result
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"alpha": _ALPHA}}


def test_tab_inside_a_comment_keeps_the_fast_path(tmp_path: Path):
    """Item B: a tab anywhere forced the full-dump fallback (losing every
    comment) even when the tab was just part of a comment's text, not doing
    the job of indentation. Only a tab in a line's own leading whitespace
    should trigger the fallback."""
    text = _HDR + (
        "  # a tab\tin this comment, not as indentation\n"
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS, "alpha": _ALPHA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert not result.startswith("# Custom connectors")  # took the fast path, not the fallback
    assert "a tab\tin this comment" in result
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": _TICKETS, "alpha": _ALPHA}}


@pytest.mark.parametrize(
    "header",
    [
        "extra: 1\nschema_version: 1\nconnectors:\n",
        "schema_version: 2\nconnectors:\n",
        "connectors:\n",
    ],
    ids=["extra-top-level-key", "stale-schema-version", "missing-schema-version"],
)
def test_header_mismatch_forces_the_full_dump_fallback(tmp_path: Path, header: str):
    """Item 3, the honest (non-NaN) trigger the reviewer verified: the header is
    kept verbatim no matter what, so if it doesn't parse back to exactly
    `{"schema_version": 1, ...}` the final round-trip check must see a mismatch
    and fall back to a plain dump -- regardless of how faithfully the entries
    themselves were rewritten."""
    text = header + (
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, {"tickets": _TICKETS})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# Custom connectors")
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": _TICKETS}}


def test_recursive_alias_in_a_trailing_comment_scan_does_not_crash(tmp_path: Path):
    """Exercises `yaml_blocks.split_blocks`'s own trim-safety comparison (not
    `_rebuild_entries`'s separate per-entry check, already covered): a cyclic
    alias entry followed by a gap comment means the splitter compares two
    separately-parsed copies of the same cyclic structure (with and without the
    trailing comment) while deciding whether the comment is safe to trim. That
    comparison must be treated as "not safe", not crash with RecursionError."""
    text = (
        "schema_version: 1\n"
        "connectors:\n"
        "  loopy:\n"
        "    loop: &l\n"
        "      self: *l\n"
        "  # trailing comment\n"
        "  alpha:\n"
        "    display_name: Alpha\n"
        "    probe: mcp__dataplat__info\n"
        "    lookup:\n"
        "      tools:\n"
        "      - mcp__dataplat__search\n"
        "      when: x\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)
    loaded_loopy = yaml.safe_load(text)["connectors"]["loopy"]

    cc.write(tmp_path, {"loopy": loaded_loopy, "alpha": _ALPHA})  # must not raise RecursionError
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    loaded = yaml.safe_load(result)
    assert loaded["connectors"]["alpha"] == _ALPHA


# --- Fix round 3 --------------------------------------------------------------------


def test_shallow_comment_after_deep_scalar_lines_is_recovered(tmp_path: Path):
    """Exercises candidate (b) of the capped trim specifically: the whole-run
    check (a) fails here (the deep `#`-led scalar lines are significant), but
    the trailing shallow comment with no blank line before it is still safely
    recovered as ALPHA's leading run rather than tickets' own trailing content
    -- made observable by changing tickets (discarding its verbatim text): if
    the comment had been misattributed to tickets' own block (candidate (b)
    not recovering it, falling through to "trim nothing"), it would be lost
    along with the rest of tickets' old text when tickets is redumped."""
    body = {
        "display_name": "Tickets",
        "probe": {"bash": "tix whoami"},
        "inbound": {"tools": [{"bash": "tix list"}], "focus": "Changed tickets."},
        "notes": "text\n" + "".join(f"#{i}\n" for i in range(5)),
    }
    text = (
        "schema_version: 1\n"
        "connectors:\n"
        "  tickets:\n"
        "    display_name: Tickets\n"
        "    probe:\n"
        "      bash: tix whoami\n"
        "    inbound:\n"
        "      tools:\n"
        "      - bash: tix list\n"
        "      focus: Changed tickets.\n"
        "    notes: |\n"
        "      text\n" + "".join(f"      #{i}\n" for i in range(5)) + "  # about alpha\n"
        "  alpha:\n"
        "    display_name: Alpha\n"
        "    probe: mcp__dataplat__info\n"
        "    lookup:\n"
        "      tools:\n"
        "      - mcp__dataplat__search\n"
        "      when: x\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)
    assert yaml.safe_load(text)["connectors"]["tickets"] == body

    changed = dict(body, display_name="Tix")
    cc.write(tmp_path, {"tickets": changed, "alpha": _ALPHA})
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert "# about alpha" in result
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": {"tickets": changed, "alpha": _ALPHA}}


def test_split_blocks_cost_is_bounded_not_quadratic(monkeypatch: pytest.MonkeyPatch):
    """Item 1: the trim must cost a bounded number of `safe_load` calls per
    segment (at most 3 -- the whole run, one shallow-suffix candidate, and the
    initial full-block parse), never one call per line of the trailing run.
    Without this, a `|` scalar with thousands of significant `#`-led content
    lines took ~40s to split; a block that can't load alone followed by
    thousands of ordinary comments took ~4.5s. Both must now be near-instant,
    checked deterministically via a counting monkeypatch rather than a wall-
    clock budget."""
    calls = [0]
    real_safe_load = yaml.safe_load

    def counting_safe_load(stream):
        calls[0] += 1
        return real_safe_load(stream)

    monkeypatch.setattr(yaml, "safe_load", counting_safe_load)
    monkeypatch.setattr(yaml_blocks.yaml, "safe_load", counting_safe_load)

    # A `|` scalar whose content is 4,000 significant `#`-led lines: every
    # candidate trim point changes the value, so the old per-line search tried
    # (and re-parsed) every single one.
    text = (
        "schema_version: 1\nconnectors:\n  tickets:\n    display_name: T\n    notes: |\n"
        + "".join(f"      # step {i}\n" for i in range(4000))
        + "  alpha:\n    display_name: A\n    probe: p\n"
    )
    calls[0] = 0
    start = time.perf_counter()
    result = yaml_blocks.split_blocks(text, indent=2, after_key="connectors")
    elapsed = time.perf_counter() - start
    assert result is not None
    assert calls[0] <= 10, f"expected a bounded call count, got {calls[0]}"
    assert elapsed < 1.0, f"expected well under 1s, took {elapsed}s"

    # A block that can't load alone (an alias to a sibling's anchor), followed
    # by 10,000 ordinary comments: every parse attempt in the old algorithm
    # failed (so the loop never broke early), re-parsing the whole block once
    # per candidate line.
    text2 = (
        "schema_version: 1\nconnectors:\n"
        "  alpha:\n    display_name: &dn Alpha\n    probe: p\n"
        "  beta:\n    display_name: *dn\n    probe: b\n"
        + "".join(f"  # note {i}\n" for i in range(10000))
        + "  gamma:\n    display_name: G\n    probe: g\n"
    )
    calls[0] = 0
    start = time.perf_counter()
    result2 = yaml_blocks.split_blocks(text2, indent=2, after_key="connectors")
    elapsed2 = time.perf_counter() - start
    assert result2 is not None
    assert calls[0] <= 15, f"expected a bounded call count, got {calls[0]}"
    assert elapsed2 < 1.0, f"expected well under 1s, took {elapsed2}s"

    # Per segment, the trim itself (the full-block parse plus the candidate
    # cuts, all through `_safe_load_or_sentinel`) costs at most 3 loads, and
    # the same number at two very different run lengths.
    trim_loads = [0]
    real_load_or_sentinel = yaml_blocks._safe_load_or_sentinel

    def counting_load_or_sentinel(text: str):
        trim_loads[0] += 1
        return real_load_or_sentinel(text)

    monkeypatch.setattr(yaml_blocks, "_safe_load_or_sentinel", counting_load_or_sentinel)
    alpha = "  # About alpha\n\n  alpha:\n    display_name: A\n    probe: p\n"
    shapes = {
        "hash-tail scalar": lambda n: (
            "    notes: |\n" + "".join(f"      # step {i}\n" for i in range(n)) + "\n" + alpha
        ),
        "keep-tail scalar": lambda n: "    notes: |+\n      Watch.\n" + "\n" * n + alpha,
        "unloadable block": lambda n: "    probe: *dn\n" + "".join(f"  # note {i}\n" for i in range(n)) + alpha,
    }
    for name, tail in shapes.items():
        per_n = {}
        for n in (500, 4000):
            text3 = (
                "schema_version: 1\nconnectors:\n  base:\n    display_name: &dn B\n"
                "  tickets:\n    display_name: T\n" + tail(n)
            )
            trim_loads[0] = 0
            split = yaml_blocks.split_blocks(text3, indent=2, after_key="connectors")
            assert split is not None
            segments = len(split[1])
            assert trim_loads[0] <= 3 * segments, f"{name}, n={n}: {trim_loads[0]} loads for {segments} segments"
            per_n[n] = trim_loads[0]
        assert per_n[500] == per_n[4000], f"{name}: load count grew with the run length: {per_n}"


def test_boundary_absorption_deep_comment_after_removed_entry(tmp_path: Path):
    """Item 2 (reviewer's `exp_absorb.py` case): beta's own trailing comment
    (indented past entry level, but still a real comment) moves into gamma's
    leading run when beta is removed. Gamma's new predecessor is now alpha
    (unchanged, ending in a `|` scalar) instead of beta -- a boundary that
    didn't exist in the original file. Emitting that leading run verbatim
    right after alpha's scalar would silently absorb it as scalar content
    instead of an inert comment. Must stay on the fast path, header and all
    comments intact."""
    text = (
        "# my header\nschema_version: 1\nconnectors:\n"
        "  alpha:\n    display_name: Alpha\n    focus: |\n      Watch for changes.\n"
        "  beta:\n    display_name: Beta\n    probe: b\n      # old probe: b-legacy\n"
        "  # About gamma\n  gamma:\n    display_name: Gamma\n    probe: g\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)
    loaded = yaml.safe_load(text)["connectors"]

    cc.write(tmp_path, {"alpha": loaded["alpha"], "gamma": loaded["gamma"]})  # remove beta
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# my header")
    assert "# About gamma" in result
    assert yaml.safe_load(result) == {
        "schema_version": 1,
        "connectors": {"alpha": loaded["alpha"], "gamma": loaded["gamma"]},
    }


def test_boundary_absorption_keep_tail_after_removed_entry(tmp_path: Path):
    """Item 2, the `|+` variant: a significant blank-led gap after beta's
    `probe:` field (not actually part of any scalar -- beta has none here --
    but positioned right where it would be swallowed if moved after alpha's
    own `|+` keep-tail scalar) must not corrupt alpha's value when beta is
    removed and gamma's leading run ends up sitting right after alpha."""
    text = (
        "# my header\nschema_version: 1\nconnectors:\n"
        "  alpha:\n    display_name: Alpha\n    focus: |+\n      Watch.\n\n"
        "  beta:\n    display_name: Beta\n    probe: b\n\n"
        "  # About gamma\n  gamma:\n    display_name: Gamma\n    probe: g\n"
    )
    (tmp_path / cc.CUSTOM_FILE).write_text(text)
    loaded = yaml.safe_load(text)["connectors"]

    cc.write(tmp_path, {"alpha": loaded["alpha"], "gamma": loaded["gamma"]})  # remove beta
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# my header")
    assert "# About gamma" in result
    assert yaml.safe_load(result) == {
        "schema_version": 1,
        "connectors": {"alpha": loaded["alpha"], "gamma": loaded["gamma"]},
    }


# --- Fix round 4 --------------------------------------------------------------------

# Two entry tails that the whole-run trim can't move past, because dropping the
# run changes the value: a `|+` scalar whose trailing blank line belongs to it,
# and a `|` scalar whose last content line starts with `#`.
_SIGNIFICANT_TAILS = {
    "keep-tail": "  tickets:\n    display_name: Tickets\n    notes: |+\n      Watch the queue.\n\n",
    "hash-tail": "  tickets:\n    display_name: Tickets\n    notes: |\n      Steps:\n      # step 3 is manual\n",
}
_ALPHA_TEXT = (
    "  alpha:\n"
    "    display_name: Alpha\n"
    "    probe: mcp__dataplat__info\n"
    "    lookup:\n"
    "      tools:\n"
    "      - mcp__dataplat__search\n"
    "      when: x\n"
)


def _change_tickets(conns: dict) -> dict:
    return {"tickets": dict(conns["tickets"], display_name="Tix"), "alpha": conns["alpha"]}


def _remove_tickets(conns: dict) -> dict:
    return {"alpha": conns["alpha"]}


@pytest.mark.parametrize("tail", sorted(_SIGNIFICANT_TAILS))
@pytest.mark.parametrize(
    "gap",
    [
        "  # About alpha\n\n",
        "\n  # About alpha\n\n",
        "  # About alpha\n\n      # (see the runbook)\n\n",
    ],
    ids=["comment+blank", "blank+comment+blank", "comment+blank+deep-comment+blank"],
)
@pytest.mark.parametrize("op", [_change_tickets, _remove_tickets], ids=["change-prev", "remove-prev"])
def test_gap_comment_after_a_significant_tail_survives(tmp_path: Path, tail: str, gap: str, op) -> None:
    """N1: when the entry above ends in a significant tail, the whole-run trim
    fails, and the fallback candidate used to be only the run's *trailing*
    shallow comments -- so one blank line after the comment left nothing to
    recover, the comment stayed in tickets' own block, and it was deleted when
    tickets changed or was removed. A comment at entry level ends any scalar
    above it, so it and everything after it can move to alpha's leading run."""
    text = _HDR + _SIGNIFICANT_TAILS[tail] + gap + _ALPHA_TEXT
    conns = yaml.safe_load(text)["connectors"]
    new = op(conns)
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, new)
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# my header")  # the fast path, not the plain-dump fallback
    for comment in ("# About alpha", "# (see the runbook)"):
        assert result.count(comment) == gap.count(comment)
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": new}


@pytest.mark.parametrize("tail", sorted(_SIGNIFICANT_TAILS))
@pytest.mark.parametrize("op", [_change_tickets, _remove_tickets], ids=["change-last", "remove-last"])
def test_trailer_after_a_significant_tail_survives(tmp_path: Path, tail: str, op) -> None:
    """N1, trailer form: a closing comment followed by a blank line at EOF, after
    a last entry with a significant tail, must survive that entry changing or
    being removed."""
    text = _HDR + _ALPHA_TEXT + _SIGNIFICANT_TAILS[tail] + "\n# Closing note.\n\n"
    conns = yaml.safe_load(text)["connectors"]
    new = op(conns)
    (tmp_path / cc.CUSTOM_FILE).write_text(text)

    cc.write(tmp_path, new)
    result = (tmp_path / cc.CUSTOM_FILE).read_text()

    assert result.startswith("# my header")
    assert result.count("# Closing note.") == 1
    assert yaml.safe_load(result) == {"schema_version": 1, "connectors": new}


def test_input_placeholder_must_match_the_renderers_case_exactly():
    # custom_assembly substitutes only INPUT_<NAME.upper()>, so a mixed-case
    # placeholder would render literally and must be rejected.
    body = {
        **MAIL,
        "needs_user_input": ["foo"],
        "inbound": {"tools": [{"bash": "suite search {{INPUT_Foo}}"}]},
    }
    c, issues = _parse("suite_mail", body)
    assert c is None
    assert "uses {{INPUT_Foo}}; write it as {{INPUT_FOO}} (placeholders are upper case)" in _messages(issues)


def test_upper_case_placeholder_for_lower_case_name_is_accepted():
    body = {**MAIL, "needs_user_input": ["foo"], "inbound": {"tools": [{"bash": "suite search {{INPUT_FOO}}"}]}}
    c, issues = _parse("suite_mail", body)
    assert issues == [], _messages(issues)


def test_undeclared_placeholder_is_reported_once():
    body = {
        **MAIL,
        "inbound": {"tools": [{"bash": "suite {{INPUT_X}} {{INPUT_X}}"}, {"bash": "suite two {{INPUT_X}}"}]},
    }
    _, issues = _parse("suite_mail", body)
    assert sum("INPUT_X" in i.message for i in issues) == 1


def test_first_binary_command_presence_check_is_not_a_call():
    assert cc.first_binary("command -v gh") is None
    assert cc.first_binary("command -V gh") is None
    assert cc.first_binary("command gh pr list") == "gh"


def test_first_binary_skips_shell_comment_lines():
    """#344: a model-drafted multi-line command often opens with a comment line."""
    assert cc.first_binary("# fetch open tickets\ntix list --open") == "tix"
    assert cc.first_binary("  # indented comment\n\n  tix list") == "tix"
    assert cc.first_binary("# only a comment") is None


def test_multi_line_bash_tool_and_probe_with_shell_comments_stay_valid():
    """#344: 0.15.0 accepted multi-line `bash:` values; they are rendered fenced, not rejected."""
    body = {
        "display_name": "Tickets",
        "probe": {"bash": "# is the CLI signed in?\ntix whoami"},
        "inbound": {"tools": [{"bash": "# fetch open tickets\ntix list --open\n"}], "focus": "Open tickets."},
    }
    c, issues = _parse("tix", body)
    assert issues == [] and c is not None
    assert c.activities["inbound"].tools[0].value == "# fetch open tickets\ntix list --open"
    assert c.probe.binary == "tix"


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ("ops\nmore", "must be a single line"),
        ("ops\r", "must be a single line"),
        ("# Ops team", "must not contain markdown headings; they would break the brain file's structure"),
        ("  ## Ops", "must not contain markdown headings; they would break the brain file's structure"),
        ("ghp_abcdefghijklmnop", "looks like a credential"),
    ],
)
def test_input_issues_reject_multi_line_heading_and_credential_values_without_echoing_them(value: str, problem: str):
    issues = cc.input_issues("tix", {"team": value})
    assert [i.path for i in issues] == ["connectors.tix.needs_user_input"]
    assert issues[0].message.startswith("the value for 'team' ")
    assert problem in issues[0].message
    assert value.strip() not in issues[0].message


@pytest.mark.parametrize("value", ["ops", "team #2", "#ops", "", None, 12345])
def test_input_issues_accept_plain_single_line_values(value: object):
    assert cc.input_issues("tix", {"team": value}) == []


def test_stored_input_issues_name_the_config_entry_and_skip_undeclared_or_unset_inputs():
    c, _ = _parse(
        "tix",
        {
            "display_name": "Tickets",
            "probe": {"bash": "tix whoami"},
            "needs_user_input": ["team", "board"],
            "inbound": {"tools": [{"bash": "tix list --team {{INPUT_TEAM}}"}], "focus": "Open tickets."},
        },
    )
    assert c is not None
    stored = {"tix__team": "ops\n# Injected", "other__team": "x\ny", "unrelated": "a\nb"}
    issues = cc.stored_input_issues({"tix": c}, stored)
    assert [i.path for i in issues] == ["connectors.tix.needs_user_input"] * 2
    assert all("scout-config.yaml (connectors.inputs.tix__team)" in i.message for i in issues)
    assert "Injected" not in _messages(issues)
