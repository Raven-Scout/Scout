"""Text people read is written in real singular and plural, never "run(s)" (UI audit T3).

The app's Health page, banners and Settings show run-health findings, doctor
lines and connector alerts as the engine wrote them, and the CLI prints the
rest. This scans the string literals of every module under ``scout/`` for a
"(s)"-style plural, and pins the helpers they use instead. A "(s)" that isn't a
counted noun goes in ``NOT_A_PLURAL`` with its reason.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from scout import plural

SCOUT = Path(plural.__file__).resolve().parent

MODULES = sorted(p.relative_to(SCOUT).as_posix() for p in SCOUT.rglob("*.py"))

# "run(s)", "copy file(s)", "cop(ies)", "prefix(es)": the word and its parenthesised ending.
PAREN_PLURAL = re.compile(r"[A-Za-z]+\((?:s|es|ies)\)")

# (module, word) pairs that look like a "(s)" plural but count nothing.
NOT_A_PLURAL: dict[tuple[str, str], str] = {
    ("plural.py", "run(s)"): "the module docstring names the wording it replaces",
    ("kb/live_run.py", "len(s)"): "a docstring quoting code",
    ("scripts/notify_telegram.py", "http(s)"): "the http and https URL schemes",
}

# Python 3.12+ splits an f-string into parts; older versions keep one STRING token.
_STRING_PARTS = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}


def _literal_text(token: str) -> str:
    """``token`` without its f-string replacement fields, so ``{title(s)}`` is code on every Python.

    3.12+ hands the fields over as code tokens already; 3.11 keeps them inside the one STRING token."""
    quotes = [i for i in (token.find('"'), token.find("'")) if i >= 0]
    if not quotes or "f" not in token[: min(quotes)].lower():  # a 3.12+ FSTRING_MIDDLE is literal text already
        return token
    out, depth, i = [], 0, 0
    while i < len(token):
        pair = token[i : i + 2]
        if depth == 0 and pair in ("{{", "}}"):
            out.append(pair[0])
            i += 2
            continue
        ch = token[i]
        if ch == "{":
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
        elif depth == 0:
            out.append(ch)
        i += 1
    return "".join(out)


def _paren_plurals(source: str) -> list[tuple[int, str]]:
    """(line, word) for each "(s)"-style plural in a string literal of ``source``."""
    hits = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in _STRING_PARTS:
            hits += [(tok.start[0], m.group()) for m in PAREN_PLURAL.finditer(_literal_text(tok.string))]
    return hits


def _unexplained(module: str, source: str) -> list[tuple[int, str]]:
    return [(line, word) for line, word in _paren_plurals(source) if (module, word) not in NOT_A_PLURAL]


def test_the_scan_covers_every_module() -> None:
    assert len(MODULES) > 100
    assert {"health.py", "cli.py", "repo_sync.py", "kb/read.py", "scripts/bootstrap_doctor.py"} <= set(MODULES)


@pytest.mark.parametrize("module", MODULES)
def test_no_paren_plurals_in_engine_text(module: str) -> None:
    hits = _unexplained(module, (SCOUT / module).read_text(encoding="utf-8"))
    assert not hits, f"use scout.plural (count/noun/agree/were) instead of a '(s)' plural in {module}: {hits}"


@pytest.mark.parametrize(("module", "word"), sorted(NOT_A_PLURAL))
def test_every_allowed_word_is_still_there(module: str, word: str) -> None:
    """An entry whose text is gone comes out of the list, so it can't excuse a new one."""
    words = {w for _, w in _paren_plurals((SCOUT / module).read_text(encoding="utf-8"))}
    assert word in words, f"{module} no longer has {word!r}; drop it from NOT_A_PLURAL"


def test_the_scan_sees_plain_and_f_strings_but_not_code() -> None:
    src = (
        'a = f"{n} run(s) failed"\nb = "2 cop(ies)"\nc = ", ".join(repr(s) for s in x)\nd = "3 prefix(es)"\n'
        'e = f"**{title(s)}** {{literal}} x"\ng = F"{n:{w}} {f(s)!r} file(s)"\nh = f"{_quoted(s)} now has"\n'
    )
    assert _paren_plurals(src) == [(1, "run(s)"), (2, "cop(ies)"), (4, "prefix(es)"), (6, "file(s)")]


def test_literal_text_drops_only_replacement_fields() -> None:
    assert _literal_text('f"{title(s)} and {{x}}"') == 'f" and {x}"'
    assert _literal_text('rb"{len(s)}"') == 'rb"{len(s)}"'


def test_the_allow_list_is_per_module_and_per_word() -> None:
    src = 'a = "http(s) only"\nb = "dropped 2 link(s)"\n'
    assert _unexplained("scripts/notify_telegram.py", src) == [(2, "link(s)")]
    assert _unexplained("cli.py", src) == [(1, "http(s)"), (2, "link(s)")]


def test_count_and_noun() -> None:
    assert plural.count(1, "run") == "1 run"
    assert plural.count(0, "run") == "0 runs"
    assert plural.count(3, "run") == "3 runs"
    assert plural.count(2, "copy", "copies") == "2 copies"
    assert plural.count(1, "entry", "entries") == "1 entry"
    assert plural.noun(1, "slot") == "slot"
    assert plural.noun(2, "slot") == "slots"
    assert plural.noun(0, "slot") == "slots"


def test_verbs_agree() -> None:
    assert (plural.were(0), plural.were(1), plural.were(2)) == ("were", "was", "were")
    assert (plural.have(0), plural.have(1), plural.have(2)) == ("have", "has", "have")
    assert (plural.are(0), plural.are(1), plural.are(2)) == ("are", "is", "are")
    assert [plural.agree(n, "waits", "wait") for n in (0, 1, 2)] == ["wait", "waits", "wait"]


def test_the_last() -> None:
    assert plural.the_last(1, "scheduled run") == "The last scheduled run"
    assert plural.the_last(4, "scheduled run") == "The last 4 scheduled runs"
    assert plural.last_runs_were_skipped(1) == "The last scheduled run was skipped"
    assert plural.last_runs_were_skipped(2) == "The last 2 scheduled runs were skipped"


def _files(n: int) -> list[dict[str, str]]:
    return [{"path": f"notes/n{i}.md", "mirror_commit": "a" * 40} for i in range(n)]


def test_reworded_notes_agree_for_one_and_many() -> None:
    from scout import repo_sync
    from scout.kb.share_stops import pending_pull_note

    assert pending_pull_note(0).startswith("Agnes has 0 commits on top of your last push")
    assert pending_pull_note(1).startswith("Agnes has 1 commit on top of your last push")
    assert pending_pull_note(3).startswith("Agnes has 3 commits on top of your last push")

    one, two = repo_sync._replay_skipped_note(_files(1)), repo_sync._replay_skipped_note(_files(2))
    assert one.startswith("warning: 1 file written in Agnes is outside privacy.sync_only, so it wasn't copied")
    assert "this sync removes it from" in one
    assert two.startswith("warning: 2 files written in Agnes are outside privacy.sync_only, so they weren't copied")
    assert "this sync removes them from" in two

    one, two = repo_sync._dropped_note(_files(1)), repo_sync._dropped_note(_files(2))
    assert "1 file came from" in one and "no longer has this version" in one and "replaces it in" in one
    assert "2 files came from" in two and "no longer has these versions" in two and "replaces them in" in two


def test_token_expiry_counts_days() -> None:
    from datetime import UTC, datetime

    from scout.claude_auth import AuthStatus

    def status(days: int) -> AuthStatus:
        at = datetime(2026, 12, 1, tzinfo=UTC)
        return AuthStatus("token", "keychain", "-", None, at, days, ())

    assert status(1).expiry_text() == "expires in 1 day (about 2026-12-01)"
    assert status(0).expiry_text() == "expires in 0 days (about 2026-12-01)"
    assert status(-1).expiry_text() == "expired about 1 day ago (2026-12-01)"
    assert status(-3).expiry_text() == "expired about 3 days ago (2026-12-01)"
