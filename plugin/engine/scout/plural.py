"""Counted nouns for text people read: "1 run", "3 runs", never "run(s)".

The app's Health page, banners and Settings show the engine's findings and
doctor lines verbatim, so the engine writes them in real singular and plural
(UI audit T3). `tests/unit/test_plural_wording.py` scans the modules whose text
the app shows and fails on a new "(s)".
"""

from __future__ import annotations


def count(n: int, singular: str, plural: str | None = None) -> str:
    """`n` and the noun that agrees with it.

    ``count(1, "run")`` is "1 run"; ``count(2, "copy", "copies")`` is "2 copies".
    """
    return f"{n} {noun(n, singular, plural)}"


def noun(n: int, singular: str, plural: str | None = None) -> str:
    """The noun alone, singular for exactly one."""
    return singular if n == 1 else (plural or singular + "s")


def were(n: int) -> str:
    """ "was" for one, "were" otherwise."""
    return "was" if n == 1 else "were"


def have(n: int) -> str:
    """ "has" for one, "have" otherwise."""
    return "has" if n == 1 else "have"


def the_last(n: int, singular: str, plural: str | None = None) -> str:
    """ "The last scheduled run" for one, "The last 3 scheduled runs" otherwise."""
    return f"The last {singular}" if n == 1 else f"The last {count(n, singular, plural)}"


def last_runs_were_skipped(n: int) -> str:
    """ "The last scheduled run was skipped" / "The last 3 scheduled runs were skipped"."""
    return f"{the_last(n, 'scheduled run')} {were(n)} skipped"
