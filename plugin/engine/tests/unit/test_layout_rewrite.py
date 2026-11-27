"""layout_rewrite.rewrite: literal paths, links, scope by kind (spec 2026-11-26 §4.10, R3)."""

from __future__ import annotations

import pytest

from scout.layout_rewrite import Ref, check_refs, rewrite

REFS = [
    Ref("scripts", "scout/scripts", True),
    Ref("hooks", "scout/hooks", True),
    Ref("SKILL.md", "scout/brain/SKILL.md", bare_ok=False),
    Ref("knowledge-base/projects", "knowledge-base/{space}/projects", True, per_space=True),
    Ref("knowledge-base/connector-health.md", "scout/records/connector-health.md"),
    Ref("inbox.md", "inbox/inbox.md"),
    Ref("drop", "inbox/drop", True),
    Ref("meetings", "knowledge-base/{space}/meetings", True),
    Ref("knowledge-base/ontology/entities/acme.md", "knowledge-base/organizations/acme.md"),
]


@pytest.mark.parametrize("kind", ["brain", "script", "note", "config"])
def test_idempotent(kind: str) -> None:
    text = (
        "$SCOUT_DIR/scripts/x ${SCOUT_DIR}/hooks/y {{SCOUT_DIR}}/SKILL.md scripts/z [[projects/a/a]] "
        "[[ontology/entities/acme|Acme]] knowledge-base/connector-health.md inbox.md drop/README.md\n"
    )
    once = rewrite(text, REFS, kind=kind, vault_abs=["/Users/a/Scout"])
    assert rewrite(once, REFS, kind=kind, vault_abs=["/Users/a/Scout"]) == once


def test_boundaries() -> None:
    text = "repo/scripts/x my-scripts/y scripts-old/z scripts and hooks words; ./scripts/a; `scripts/b`"
    out = rewrite(text, REFS, kind="note")
    assert (
        out == "repo/scripts/x my-scripts/y scripts-old/z scripts and hooks words; ./scout/scripts/a; `scout/scripts/b`"
    )


def test_vault_prefixes() -> None:
    out = rewrite(
        'cat "/Users/a/Scout/SKILL.md" ~/Scout/scripts/q', REFS, kind="note", vault_abs=["/Users/a/Scout", "~/Scout"]
    )
    assert out == 'cat "/Users/a/Scout/scout/brain/SKILL.md" ~/Scout/scout/scripts/q'


def test_bare_brain_name_only_in_brain_and_scripts() -> None:
    assert rewrite("read SKILL.md first", REFS, kind="note") == "read SKILL.md first"
    assert rewrite("read SKILL.md first", REFS, kind="brain") == "read scout/brain/SKILL.md first"
    assert rewrite("see other/SKILL.md", REFS, kind="brain") == "see other/SKILL.md"


def test_links_keep_alias_heading_and_style() -> None:
    text = "[[projects/a/a#Goal|A]] [[knowledge-base/projects/b]] [x](projects/c.md#h) [[meetings/s/2026-01-01]]"
    assert rewrite(text, REFS, kind="note", space="work") == (
        "[[work/projects/a/a#Goal|A]] [[knowledge-base/work/projects/b]] [x](work/projects/c.md#h) "
        "[[work/meetings/s/2026-01-01]]"
    )


def test_links_untouched_outside_notes() -> None:
    assert rewrite("[[projects/a/a]]", REFS, kind="brain") == "[[projects/a/a]]"


def test_table_escaped_pipe() -> None:
    assert rewrite("| [[meetings/s/s\\|S]] |", REFS, kind="note") == "| [[work/meetings/s/s\\|S]] |"


def test_config_globs_expand_per_space() -> None:
    text = 'x:\n  - "knowledge-base/projects/**"\n  - glob: knowledge-base/projects/p/**\n    visibility: private\n'
    out = rewrite(text, REFS, kind="config", spaces=["work", "svj"])
    assert out == (
        'x:\n  - "knowledge-base/work/projects/**"\n  - "knowledge-base/svj/projects/**"\n'
        "  - glob: knowledge-base/work/projects/p/**\n    visibility: private\n"
        "  - glob: knowledge-base/svj/projects/p/**\n    visibility: private\n"
    )


def test_check_refs_rejects_a_non_idempotent_list() -> None:
    with pytest.raises(ValueError):
        check_refs([Ref("a/b", "a/b/c", True)])


def test_url_untouched() -> None:
    assert rewrite("[x](https://example.com/scripts/a)", REFS, kind="note") == "[x](https://example.com/scripts/a)"


def test_allowlists_are_not_widened_across_spaces() -> None:
    text = (
        'privacy:\n  sync_only:\n    - "knowledge-base/projects/**"\n'
        "  visibility_defaults:\n    - glob: knowledge-base/projects/x/**\n      visibility: private\n"
    )
    out = rewrite(text, REFS, kind="config", space="work", spaces=["work", "personal"])
    assert '  sync_only:\n    - "knowledge-base/work/projects/**"\n  visibility_defaults:' in out
    assert "glob: knowledge-base/personal/projects/x/**" in out  # a narrowing entry still covers every space
