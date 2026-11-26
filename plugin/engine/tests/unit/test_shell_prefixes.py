"""Both run gates build their prefix words from scout.kb.shell_prefixes, and keep agreeing (#823's review).

raw_read (the KB read gate's command judge) strips every prefix before the program it judges; live_run (the run
escape walk) strips only the modifiers, since it judges env, nohup and the shells itself and strips words without
their values.
"""

from __future__ import annotations

import pytest

from scout.kb import live_run, raw_read
from scout.kb.shell_prefixes import ALL, LAUNCHERS, MODIFIERS, SELF_JUDGED, SHELLS


def test_the_groups_are_disjoint_and_make_up_the_whole_table() -> None:
    assert not MODIFIERS & LAUNCHERS and not MODIFIERS & SELF_JUDGED and not LAUNCHERS & SELF_JUDGED
    assert ALL == MODIFIERS | LAUNCHERS | SELF_JUDGED
    assert SHELLS <= SELF_JUDGED
    assert {"noglob", "nocorrect", "-", "builtin", "command", "exec"} <= MODIFIERS  # zsh's precommand modifiers


def test_raw_read_strips_every_prefix_and_live_run_only_the_modifiers() -> None:
    assert raw_read._PREFIX_WORDS == set(ALL)
    assert live_run._PREFIX == set(MODIFIERS)
    assert live_run._SHELLS == set(SHELLS)
    assert not live_run._PREFIX & SELF_JUDGED  # env -i, nohup, sh -c stay live_run's to judge
    assert LAUNCHERS <= live_run._GIT_WRAPPERS and MODIFIERS <= live_run._GIT_WRAPPERS


def test_every_prefix_with_options_of_its_own_is_in_the_table() -> None:
    assert set(raw_read._PREFIX_VALUE_FLAGS) <= ALL
    assert set(raw_read._PREFIX_POSITIONAL) <= ALL


@pytest.mark.parametrize("word", sorted(MODIFIERS | LAUNCHERS | {"env", "nohup"}))
def test_raw_read_strips_each_one(word: str) -> None:
    given = ["10"] * raw_read._PREFIX_POSITIONAL.get(word, 0)  # `timeout 10 cat …`: its duration first
    assert raw_read._strip([word, *given, "cat", "knowledge-base/a.md"]).words == ["cat", "knowledge-base/a.md"]


@pytest.mark.parametrize("word", sorted(MODIFIERS))
def test_live_run_strips_each_modifier(word: str) -> None:
    assert live_run._shell_stdin_program([word, "sh"])  # `noglob sh` runs its stdin as commands
