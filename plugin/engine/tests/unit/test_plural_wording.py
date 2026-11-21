"""Text the app shows is written in real singular and plural, never "run(s)" (UI audit T3).

The app's Health page, banners and Settings show run-health findings, doctor
lines and connector alerts as the engine wrote them. This scans those modules'
string literals for a "(s)"-style plural, and pins the helpers they use.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from scout import plural

SCOUT = Path(plural.__file__).resolve().parent

# Modules whose text reaches the app verbatim.
SHOWN_BY_THE_APP = [
    "health.py",
    "scripts/bootstrap_doctor.py",
    "scripts/connector_health_report.py",
]

# "run(s)", "copy file(s)", "cop(ies)", "request(s)".
PAREN_PLURAL = re.compile(r"[A-Za-z]\((?:s|es|ies)\)")

# Python 3.12+ splits an f-string into parts; older versions keep one STRING token.
_STRING_PARTS = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}


def _paren_plurals(source: str) -> list[tuple[int, str]]:
    hits = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in _STRING_PARTS and PAREN_PLURAL.search(tok.string):
            hits.append((tok.start[0], tok.string.strip()))
    return hits


@pytest.mark.parametrize("module", SHOWN_BY_THE_APP)
def test_no_paren_plurals_in_text_the_app_shows(module: str) -> None:
    hits = _paren_plurals((SCOUT / module).read_text(encoding="utf-8"))
    assert not hits, f"use scout.plural (count/noun/were) instead of a '(s)' plural in {module}: {hits}"


def test_the_scan_sees_plain_and_f_strings_but_not_code() -> None:
    src = 'a = f"{n} run(s) failed"\nb = "2 cop(ies)"\nc = ", ".join(repr(s) for s in x)\n'
    assert [line for line, _ in _paren_plurals(src)] == [1, 2]


def test_count_and_noun() -> None:
    assert plural.count(1, "run") == "1 run"
    assert plural.count(0, "run") == "0 runs"
    assert plural.count(3, "run") == "3 runs"
    assert plural.count(2, "copy", "copies") == "2 copies"
    assert plural.noun(1, "slot") == "slot"
    assert plural.noun(2, "slot") == "slots"


def test_verbs_agree() -> None:
    assert (plural.were(1), plural.were(2)) == ("was", "were")
    assert (plural.have(1), plural.have(0)) == ("has", "have")


def test_the_last() -> None:
    assert plural.the_last(1, "scheduled run") == "The last scheduled run"
    assert plural.the_last(4, "scheduled run") == "The last 4 scheduled runs"
    assert plural.last_runs_were_skipped(1) == "The last scheduled run was skipped"
    assert plural.last_runs_were_skipped(2) == "The last 2 scheduled runs were skipped"
