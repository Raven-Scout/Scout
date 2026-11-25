"""``PrivateNote.prefixes``: trying a note's pattern only where a prefix starts finds exactly what finditer finds.

Differential: every check compares the terms as built with the same terms with
their prefixes stripped (``prefixes=None``), which is the old search, a plain
``pattern.finditer`` over the whole text. The two must agree hit for hit, and
``redact_lines`` byte for byte, over a hand-written corpus and seeded fuzz with
adversarial spellings.
"""

from __future__ import annotations

import ast
import dataclasses
import random
import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote, quote_plus

import pytest

from scout import delegate_mirror as dm
from scout.kb import outbound
from scout.kb.shared_reads import SharedRecord

PEOPLE = [
    "Ann Lee",
    "Anna",
    "Anna Bell",
    "Bell Rock",
    "Sam O'Brien",
    "Straße Kid",
    "José Lee",
    "Zoë Quinn",
    "Priya Sharma",
    "aaaa",
    "aaaa bbbb",
    "a.b.c Corp",
    "Ŝtefan Novák",
    "Ελένη Παπά",
    "İstanbul Trip",
    "ﬁnn ﬂow",
    "ＦＵＬＬ Width",
    "ǅemal Ok",
    "한국어 사람",
    "カスタム計画",
    "Renée",
    "Mila",
]
ALIASES = {"Anna Bell": ["Belle-Anna", "AB Team"], "Sam O'Brien": ["Sammy"], "Priya Sharma": ["P. Sharma"]}
HIDDEN = ["people/secret-plan.md", "trading/positions.md", "a/b/c.md", "Notes/Über Plan.md", "x/aaaa.md"]
SHARED = [
    SharedRecord(owner="o@example.com", path="projects/widget/roadmap.md", title="Widget Q3 Roadmap Plan"),
    SharedRecord(owner="o@example.com", path="projects/gadget", title=None),
    SharedRecord(owner="p@example.com", path="team/notes/Ann Lee 1-1.md", title="Ann Lee one on one"),
]
FILLER = [
    "same",
    "annal",
    "bellows",
    "anna-belle",
    "rocky",
    "leeway",
    "jose",
    "sharma's",
    "aaaaa",
    "bbbbb",
    "corp",
    "the",
    "plan",
    "widget",
    "roadmap",
    "secret",
    "positions",
    "über",
    "uber",
    "flow",
    "finn",
    "café",
    "naïve",
    "q3",
    "1-1",
    "x_y",
    "'s",
    "shared:",
    "o@example.com",
]
SEPS = [" ", "-", "_", "\n", "\t", " \n ", "--", "__", "\n\n", " - ", "\r\n", " "]
INVISIBLE = ["­", "​", "‍", "﻿", "⁠"]
MARKUP = ["**", "`", "<!-- x -->", "<!--\n-->", "<b>", "</b>", "~~", "==", "*", "![", "<br/>"]


def _note(name: str) -> outbound.PrivateNote:
    slug = re.sub(r"\W+", "-", name.casefold()).strip("-") or "n"
    aliases = ALIASES.get(name, [])
    fm = f"---\nname: {name}\ntype: person\naliases: [{', '.join(aliases)}]\ntitle: {name} notes\n---\n"
    return outbound.note_from_text(f"people/{slug}.md", fm)


def _terms(monkeypatch: pytest.MonkeyPatch) -> outbound.PrivateTerms:
    monkeypatch.setattr(outbound, "load_ledger", lambda vault: list(SHARED))
    monkeypatch.setattr(outbound, "source_note_records", lambda vault, paths: [])
    return outbound._with_shared(outbound.terms_of(_note(n) for n in PEOPLE), Path("/nonexistent"))


def _stripped(terms: outbound.PrivateTerms) -> outbound.PrivateTerms:
    notes = tuple(dataclasses.replace(n, prefixes=None, bare_prefixes=None) for n in terms.notes)
    return dataclasses.replace(terms, notes=notes)


def _scanners(terms: outbound.PrivateTerms) -> tuple[dm.Scanner, dm.Scanner]:
    new = dm.Scanner(terms, HIDDEN)
    old = dm.Scanner(terms, HIDDEN)
    old.terms = _stripped(new.terms)
    return new, old


def _fullwidth(s: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in s)


def _mutate(rng: random.Random, name: str) -> str:
    words = re.split(r"[-_\s]+", name)
    s = rng.choice(SEPS).join(words) if len(words) > 1 else name
    for _ in range(rng.randint(0, 3)):
        op = rng.randrange(12)
        if op == 0:
            s = s.upper()
        elif op == 1:
            s = "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in s)
        elif op == 2:
            s = unicodedata.normalize("NFD", s)
        elif op == 3:
            s = _fullwidth(s)
        elif op == 4 and s:
            i = rng.randrange(len(s) + 1)
            s = s[:i] + rng.choice(INVISIBLE) + s[i:]
        elif op == 5 and s:
            i = rng.randrange(len(s) + 1)
            s = s[:i] + rng.choice(MARKUP) + s[i:]
        elif op == 6:
            s = s.replace("e", "é").replace("a", "á")
        elif op == 7:
            s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode() or s
        elif op == 8:
            s = rng.choice(["x", "_", "9", "é", ""]) + s + rng.choice(["s", "_", "9", "", "'s"])
        elif op == 9:
            s = rng.choice([quote_plus(s), quote(s), s.replace(" ", "%20"), s.replace(" ", "+")])
        elif op == 10:
            s = rng.choice([f"[[{s}]]", f"[[{s}|x]]", f"[x]({s})", f"[{s}](url)", f"[[{s}#h]]", f"[x]: {s}"])
        else:
            s = s.replace("ss", "ß") if "ss" in s else s.replace("fi", "ﬁ")
    return s


def _text(rng: random.Random) -> str:
    out: list[str] = []
    for _ in range(rng.randint(1, 40)):
        r = rng.random()
        if r < 0.45:
            out.append(_mutate(rng, rng.choice(PEOPLE + [a for v in ALIASES.values() for a in v])))
        elif r < 0.55:
            path = rng.choice(HIDDEN + [s.path for s in SHARED])
            out.append(_mutate(rng, rng.choice([path, path[:-3] if path.endswith(".md") else path])))
        elif r < 0.6:
            out.append(rng.choice(["'s shared note", "’s shared note", "shared:o@example.com:x", "---"]))
        else:
            out.append(rng.choice(FILLER))
        out.append(rng.choice(SEPS + [" ", " ", " ", ", ", ". "]))
    return "".join(out)


CORPUS = [
    "Call José\nLee tomorrow",
    "STRASSE KID is here, so is straße-kid",
    "Anna Bell and Bell Rock: Anna Bell Rock",
    "aaaaaaaa aaaa aaaa-bbbb aaaa\nbbbb",
    "xAnna Annax _Anna_ 9Anna Anna9 Anna's",
    "Sa**m O'**Brien, Sa<!-- -->mmy, Sa<!--\n-->mmy",
    "q=Jos%C3%A9+Lee and Ann%20Lee and Ann+Lee",
    "[[Ann Lee]] [[ann lee|Ann]] [x](people/secret-plan.md) [[secret-plan]]",
    "Pri​ya Sharma, Priya­ Sharma, ﻿Priya Sharma",
    "Renée and RENÉE and Renee",
    "ﬁnn ﬂow, finn flow, FINN FLOW, ＦＵＬＬ　Width, full width",
    "İstanbul trip; istanbul trip; i̇stanbul trip",
    "ǅemal ok / DŽemal Ok / džemal ok",
    "한국어 사람 and 한국어사람 and カスタム計画",
    "a.b.c corp, a.b.c-corp, abc corp",
    "the projects/widget/roadmap page, projects/gadget, Widget Q3 Roadmap Plan",
    "o's shared note projects/widget; source: shared:o@example.com:projects/widget/roadmap.md",
    "trading/positions and trading/positions.md and positions",
    "Notes/Über Plan, notes/uber plan, über plan",
    "Ann\nLee\nAnn\n\nLee\n\n\nLee Ann",
    "",
    "\n\n\n",
    "AnnLee AnnaBell",
]


def _check(text: str, terms: outbound.PrivateTerms, new: dm.Scanner, old: dm.Scanner) -> int:
    """Asserts the prefix search and the plain one agree on ``text``; returns how many hits there were."""
    stripped = _stripped(terms)
    hits = outbound._hits(text, terms)
    assert hits == outbound._hits(text, stripped), repr(text)
    for markers in (False, True):
        assert outbound.find_mentions(text, terms, allow_markers=markers) == outbound.find_mentions(
            text, stripped, allow_markers=markers
        ), repr(text)
    assert new.lines(text) == old.lines(text), repr(text)
    for markdown in (False, True):
        assert dm.redact_lines(text, new, markdown=markdown) == dm.redact_lines(text, old, markdown=markdown), repr(
            text
        )
    return len(hits)


def _promise_holds(text: str, terms: outbound.PrivateTerms) -> None:
    """The promise itself: every match of a prefixed pattern starts with one of its prefixes."""
    (folded, _), (bare, _) = outbound._folds(text)
    for note in terms.notes:
        for pattern, prefixes, form in (
            (note.pattern, note.prefixes, folded),
            (note.bare_pattern, note.bare_prefixes, bare),
        ):
            if pattern is None or prefixes is None:
                continue
            for m in pattern.finditer(form):
                assert any(form.startswith(p, m.start()) for p in prefixes), (note.path, m.group(0))


def test_every_note_built_from_terms_has_prefixes(monkeypatch: pytest.MonkeyPatch) -> None:
    terms = _terms(monkeypatch)
    new, _ = _scanners(terms)
    unprefixed = [n.path for n in new.terms.notes if n.pattern is not None and n.prefixes is None]
    assert unprefixed == ["shared-note-citation"]  # hand-written: always searched whole
    assert all(n.prefixes and "" not in n.prefixes for n in new.terms.notes if n.prefixes is not None)
    assert [n for n in new.terms.notes if n.path.startswith("path:")]  # the Scanner's hidden paths are prefixed


@pytest.mark.parametrize("text", CORPUS)
def test_the_corpus_finds_the_same(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    terms = _terms(monkeypatch)
    new, old = _scanners(terms)
    _check(text, terms, new, old)
    _promise_holds(text, new.terms)


@pytest.mark.parametrize("seed", range(8))
def test_fuzzed_text_finds_the_same(monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
    terms = _terms(monkeypatch)
    new, old = _scanners(terms)
    rng = random.Random(seed)
    found = 0
    for _ in range(60):
        text = _text(rng)
        found += _check(text, new.terms, new, old)
        _promise_holds(text, new.terms)
    assert found > 100  # the fuzz hits names, so the comparison isn't vacuous


def test_a_wrong_prefix_would_be_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The differential has teeth: a note promising a prefix its matches don't start with loses hits."""
    terms = _terms(monkeypatch)
    broken = dataclasses.replace(
        terms,
        notes=tuple(
            dataclasses.replace(n, prefixes=("zzzz",)) if n.prefixes and "ann" in n.prefixes else n for n in terms.notes
        ),
    )
    text = "Call Ann Lee\n"
    assert outbound._hits(text, terms) != outbound._hits(text, broken)


def _finditer(pattern: re.Pattern[str], prefixes: tuple[str, ...] | None, form: str) -> Iterator[re.Match[str]]:
    return outbound._matches(pattern, prefixes, form, {})


def test_matching_at_an_index_still_looks_behind_it() -> None:
    """``pattern.match(form, i)`` sees ``form`` before ``i``: a name glued to a letter is no match there."""
    pattern = outbound._term_regex(["Sam Lee"])
    assert pattern is not None
    assert [m.span() for m in _finditer(pattern, ("sam",), "xsam lee sam lee")] == [(9, 16)]
    assert [m.span() for m in pattern.finditer("xsam lee sam lee")] == [(9, 16)]


def test_overlapping_prefixes_resume_where_finditer_does() -> None:
    pattern = outbound._term_regex(["aaaa", "aaaa bbbb"])
    assert pattern is not None
    for form in ("aaaaaaaa aaaa", "aaaa aaaa bbbb aaaa", "aaaa-bbbb aaaa_bbbb", "aaaa\n\nbbbb"):
        expect = [m.span() for m in pattern.finditer(form)]
        assert [m.span() for m in _finditer(pattern, ("aaaa",), form)] == expect, form


def test_no_prefixes_or_an_empty_one_searches_the_whole_text() -> None:
    pattern = re.compile(r"b|a")
    assert [m.span() for m in _finditer(pattern, None, "ab")] == [(0, 1), (1, 2)]
    assert [m.span() for m in _finditer(pattern, ("",), "ab")] == [(0, 1), (1, 2)]


# ----- where prefixes may be set ---------------------------------------------------------------

_BUILDERS = {
    "prefixes": ("pattern", "_term_prefixes", "_term_regex"),
    "bare_prefixes": ("bare_pattern", "_bare_prefixes", "_bare_regex"),
}


def _name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _calls(node: ast.AST | None, fn: str) -> ast.Call | None:
    return node if isinstance(node, ast.Call) and _name(node.func) == fn else None


def _one_literal(prefix: ast.AST, pattern: ast.AST | None) -> bool:
    """``prefixes=(x,)`` beside ``pattern=re.compile(_BEFORE + re.escape(x) + _AFTER)``."""
    if not (isinstance(prefix, ast.Tuple) and len(prefix.elts) == 1):
        return False
    compiled = _calls(pattern, "compile")
    if compiled is None or len(compiled.args) != 1 or compiled.keywords:
        return False
    outer = compiled.args[0]
    if not (isinstance(outer, ast.BinOp) and isinstance(outer.op, ast.Add) and _name(outer.right) == "_AFTER"):
        return False
    inner = outer.left
    if not (isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Add) and _name(inner.left) == "_BEFORE"):
        return False
    escaped = _calls(inner.right, "escape")
    return escaped is not None and len(escaped.args) == 1 and ast.dump(escaped.args[0]) == ast.dump(prefix.elts[0])


def _sites() -> list[tuple[str, bool]]:
    """Every call in the engine passing ``prefixes=`` or ``bare_prefixes=``, and whether it keeps the rule."""
    root = Path(outbound.__file__).resolve().parents[1]
    out: list[tuple[str, bool]] = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg is not None}
            for key, (pattern_key, prefix_fn, regex_fn) in _BUILDERS.items():
                if key not in kw:
                    continue
                value, pattern = kw[key], kw.get(pattern_key)
                built, regex = _calls(value, prefix_fn), _calls(pattern, regex_fn)
                same_words = (
                    built is not None
                    and regex is not None
                    and [ast.dump(a) for a in built.args] == [ast.dump(a) for a in regex.args]
                    and not built.keywords
                    and not regex.keywords
                )
                ok = same_words or (key == "prefixes" and _one_literal(value, pattern))
                out.append((f"{path.relative_to(root.parent)}:{node.lineno}:{key}", ok))
    return out


def test_prefixes_are_set_only_beside_a_pattern_built_from_the_same_words() -> None:
    """A new place that sets prefixes fails here until it builds them with its pattern, from the same words.

    ``_term_prefixes(x)`` and ``_term_regex(x)`` both run :func:`outbound._words_of` over
    ``_term_forms(x)`` (``_bare_*`` over ``_bare_forms``), and the pattern is those
    words escaped between zero-width assertions, so each match starts with a first word.
    """
    sites = _sites()
    assert [where for where, ok in sites if not ok] == []
    # note_from_text (2), the shared-paths note (2), the Scanner's hidden paths (1): the scan sees them all
    assert len(sites) == 5, sites


def test_the_site_check_rejects_a_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The check has teeth: prefixes from other words, a hand-written pattern, or a replace() are refused."""
    kb = tmp_path / "scout" / "kb"
    kb.mkdir(parents=True)
    (kb / "bad.py").write_text(
        "PrivateNote(pattern=_term_regex(a), prefixes=_term_prefixes(b))\n"
        "PrivateNote(pattern=re.compile('x|y'), prefixes=('x',))\n"
        "PrivateNote(pattern=re.compile(_BEFORE + re.escape(a) + _AFTER), prefixes=(b,))\n"
        "dataclasses.replace(note, prefixes=('sam',))\n"
        "PrivateNote(bare_pattern=_term_regex(a), bare_prefixes=_term_prefixes(a))\n"
        "PrivateNote(pattern=_term_regex(a), prefixes=_term_prefixes(a))\n",
        encoding="utf-8",
    )
    (kb / "outbound.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(outbound, "__file__", str(kb / "outbound.py"))
    assert [ok for _, ok in _sites()] == [False, False, False, False, False, True]


# ----- case and Unicode folding -------------------------------------------------------------------


def test_prefixes_are_found_in_the_same_folded_form_the_pattern_matches() -> None:
    """No IGNORECASE: terms (_norm) and text (_folds) are both folded, so str.find sees what the pattern sees.

    The fuzz also spells names upper, mixed case, NFD, fullwidth, with ß and
    ligatures (``_mutate`` ops 0-3 and 11), and CORPUS has İ, ß/SS, ﬁ/ﬂ and
    fullwidth lines; every one is compared with the plain finditer.
    """
    terms = outbound.terms_of(
        [_note(n) for n in ("Straße Kid", "İstanbul Trip", "ﬁnn ﬂow", "ＦＵＬＬ Width", "Ŝtefan Novák")]
    )
    for note in terms.notes:
        for pattern in (note.pattern, note.bare_pattern):
            assert pattern is None or not pattern.flags & re.IGNORECASE
    text = "STRASSE KID, i̇stanbul trip, FINN FLOW, full width, ŜTEFAN NOVÁK, Stefan Novak\n"
    hits = outbound._hits(text, terms)
    assert hits == outbound._hits(text, _stripped(terms))
    assert {path for path, _, _ in hits} == {n.path for n in terms.notes}  # each spelling is found
    assert outbound.find_mentions(text, terms, allow_markers=False) == [1]


def test_an_ignorecase_pattern_is_searched_whole() -> None:
    """A pattern compiled with IGNORECASE could match where a case-sensitive str.find sees no prefix."""
    pattern = re.compile(r"(?<![^\W_])sam lee(?![^\W_])", re.IGNORECASE)
    assert [m.span() for m in _finditer(pattern, ("sam",), "SAM LEE")] == [(0, 7)]
