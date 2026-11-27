"""layout_rewrite at its edges (spec 2026-11-26 §4.10): kinds, prefixes, leading slashes, KB-relative schema globs."""

from __future__ import annotations

import pytest

from scout.layout_rewrite import Ref, _Literal, _map_target, _outside, rewrite

KB_PROJECTS = Ref("knowledge-base/projects", "knowledge-base/{space}/projects", True, per_space=True)
SCRIPTS = Ref("scripts", "scout/scripts", True)


def test_kinds() -> None:
    with pytest.raises(ValueError, match="unknown rewrite kind"):
        rewrite("x", [SCRIPTS], kind="nope")
    assert rewrite("scripts/x.sh", [SCRIPTS], kind="plain") == "scripts/x.sh"
    assert rewrite("scripts/x.sh", [], kind="script") == "scripts/x.sh"
    assert _Literal([], (), bare_names=True, space="work").sub("scripts/x") == "scripts/x"


def test_schema_globs_are_kb_relative_per_space() -> None:
    refs = [KB_PROJECTS, Ref("knowledge-base/people", "knowledge-base/people", True)]
    out = rewrite("graph:\n  exclude:\n    - projects/**/archive/**\n", refs, kind="schema", space="work")
    assert "- */projects/**/archive/**" in out


def test_a_prefixed_path_inside_a_word_is_left() -> None:
    out = rewrite("x~/Scout/scripts/run and ~/Scout/scripts/run", [SCRIPTS], kind="script", vault_abs=("~/Scout",))
    assert out == "x~/Scout/scripts/run and ~/Scout/scout/scripts/run"


def test_link_targets_keep_a_leading_slash_and_overlapping_spans_are_skipped() -> None:
    assert _map_target("/scripts/x.sh", [SCRIPTS], "work") == "/scout/scripts/x.sh"
    assert _map_target("https://example.com/scripts", [SCRIPTS], "work") is None
    assert _outside("abcdef", [(0, 3), (1, 2), (4, 5)], str.upper) == "abcDeF"
