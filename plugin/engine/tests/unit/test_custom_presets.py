"""Shipped presets: present, parseable, and free of provider-specific tool names."""

from __future__ import annotations

import re

from scout import custom_connectors as cc

PRESETS = cc.load_presets(cc.default_plugin_root())


def test_shipped_presets_exist_with_expected_activities():
    assert set(PRESETS) == {"mail", "chat", "calendar"}
    for name in ("mail", "chat"):
        assert {"summary", "inbound", "outbound", "lookup"} <= set(PRESETS[name])
    assert {"summary", "inbound", "outbound"} <= set(PRESETS["calendar"])


def test_presets_name_no_provider_tools():
    """Presets are provider-neutral: the connector's own tools are listed separately."""
    tool_like = re.compile(r"\b(gmail|gcal|slack)_[a-z_]+|mcp__")
    for name, preset in PRESETS.items():
        for activity, text in preset.items():
            assert not tool_like.search(text), f"{name}.{activity} names a provider tool"


def test_presets_do_not_contain_frontmatter_fences():
    for preset in PRESETS.values():
        for text in preset.values():
            assert not any(line.strip() == "---" for line in text.splitlines())
