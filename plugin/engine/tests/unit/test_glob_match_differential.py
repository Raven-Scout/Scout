"""``glob_match`` answers exactly as the regex it replaced did (#565).

``glob_match`` decides privacy labels, local-only paths, shared files, sync and
read gates, so any difference on any glob is a bug. :func:`_regex_match` is a
frozen copy of the old translation: never change it to suit the matcher. Every
input here is small enough for that regex to finish.
"""

from __future__ import annotations

import json
import random
import re
from functools import cache
from itertools import product
from pathlib import Path

import pytest

from scout.kb import privacy
from scout.kb.lint_config import glob_match, load_lint_config
from scout.kb.ontology import DEFAULT_EXCLUDE

CORPUS = Path(__file__).resolve().parent.parent / "fixtures" / "contract" / "privacy-corpus.json"


@cache
def _regex(glob: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _regex_match(glob: str, rel: str) -> bool:
    return bool(_regex(glob).match(rel))


# Each row is a behaviour of the regex a rewrite could easily lose. Both matchers must agree with it.
PINNED = [
    # `*` and `?` stay within a segment; `**` crosses them.
    ("a/*.md", "a/b.md", True),
    ("a/*.md", "a/b/c.md", False),
    ("a/?.md", "a/b.md", True),
    ("a/?.md", "a//.md", False),
    ("a/**", "a/b/c", True),
    ("a/**", "a/", True),
    ("a/**", "a", False),
    # `**/` is optional as a whole, and otherwise ends on a slash.
    ("**/x", "x", True),
    ("**/x", "a/b/x", True),
    ("**/x", "ax", False),
    ("a**/b", "ab", True),
    ("a**/b", "a/b", True),
    ("a**/b", "azz/b", True),
    ("a**/b", "azzb", False),
    ("**/", "", True),
    ("**/", "a/", True),
    ("**/", "a", False),
    # Three stars read left to right: `**` then `*`, unless a `**/` starts at the second.
    ("***", "a/b", True),
    ("***/x", "a/x", True),
    ("***/x", "x", False),
    ("****/x", "x", True),
    ("*/**/x", "a/x", True),
    # `**`'s dot stops at a newline; `*` and `?` go through one.
    ("**", "a\nb", False),
    ("*", "a\nb", True),
    ("a?b", "a\nb", True),
    ("**/x", "a\n/x", False),
    ("***", "a/b\nc", True),
    ("***", "a\nb/c", False),
    # `$` also matches just before one trailing newline, and only that one.
    ("a", "a\n", True),
    ("a", "a\n\n", False),
    ("a\n", "a\n", True),
    ("a\n", "a\n\n", True),
    ("**", "a\n", True),
    ("*", "\n", True),
    ("", "", True),
    ("", "\n", True),
    ("", "\n\n", False),
    ("a/**", "a/\n", True),
    # No character classes: every other character is itself, regex metacharacters included.
    ("[a]", "a", False),
    ("[a]", "[a]", True),
    ("[!a].md", "b.md", False),
    ("a.md", "axmd", False),
    ("a+(b)|c", "a+(b)|c", True),
    ("^a$", "^a$", True),
    ("^a$", "a", False),
    ("a\\b", "a\\b", True),
    ("{a,b}", "a", False),
    ("a b", "a b", True),
    ("-", "-", True),
    # Dots get no special treatment, and a trailing slash is just a character.
    ("*", ".hidden", True),
    ("**/*.md", ".a/.b.md", True),
    ("a/", "a", False),
    ("a", "a/", False),
    ("a/*", "a/", True),
    ("*/", "a/", True),
    ("../*", "../a", True),
    # Unicode: one code point per `?`, no folding or normalising.
    ("?", "日", True),
    ("?", "é", True),
    ("?", "é", False),
    ("é", "é", False),
    ("Note", "note", False),
    ("?", "🙂", True),
    ("日本/*.md", "日本/語.md", True),
    ("ß", "ss", False),
]


@pytest.mark.parametrize(("glob", "rel", "expected"), PINNED)
def test_a_pinned_behaviour_holds_in_both_matchers(glob: str, rel: str, expected: bool) -> None:
    assert _regex_match(glob, rel) is expected
    assert glob_match(glob, rel) is expected


def test_every_glob_and_path_up_to_a_small_size_agrees() -> None:
    glob_tokens = ("**/", "**", "*", "?", "/", "a", ".", "\n")
    globs = ["".join(t) for n in range(5) for t in product(glob_tokens, repeat=n) if n < 4 or t[0] in ("**/", "**")]
    paths = ["".join(t) for n in range(5) for t in product("a/.\nb", repeat=n)]
    matched = 0
    for glob in globs:
        for rel in paths:
            want = _regex_match(glob, rel)
            matched += want
            assert glob_match(glob, rel) is want, (glob, rel)
    assert matched > 10_000  # not a test of globs that match nothing


GLOB_PIECES = (
    "**/",
    "**",
    "*",
    "?",
    "/",
    "a",
    "b",
    ".",
    "-",
    "_",
    " ",
    "é",
    "日",
    "🙂",
    "é",
    "[",
    "]",
    "!",
    "(",
    ")",
    "+",
    "|",
    "^",
    "$",
    "{",
    "}",
    ",",
    "\\",
    "\n",
    ".md",
    "notes",
)


def _random_glob(rnd: random.Random) -> str:
    pieces = rnd.choices(GLOB_PIECES, k=rnd.randint(0, 9))
    # Few wildcards: the regex under comparison has to finish.
    wild = 0
    kept = []
    for piece in pieces:
        if "*" in piece:
            wild += 1
            if wild > 4:
                continue
        kept.append(piece)
    return "".join(kept)


def _path_for(glob: str, rnd: random.Random) -> str:
    """A path built to match ``glob``, so a wrong "no" shows up as well as a wrong "yes"."""
    out: list[str] = []
    i = 0
    filler = ("a", "b", ".", "é", "notes", "x-y", "")
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append(rnd.choice(("", "a/", "a/b/", "é/", "./")))
            i += 3
        elif glob.startswith("**", i):
            out.append(rnd.choice(("", "a", "a/b", "/", "b/é/")))
            i += 2
        elif glob[i] == "*":
            out.append(rnd.choice(filler))
            i += 1
        elif glob[i] == "?":
            out.append(rnd.choice(("a", "é", "\n", "🙂")))
            i += 1
        else:
            out.append(glob[i])
            i += 1
    return "".join(out)


def _mutated(rel: str, rnd: random.Random) -> str:
    """``rel`` with one small change, for the near misses; sometimes no change."""
    kind = rnd.randrange(7)
    at = rnd.randint(0, len(rel))
    if kind == 0:
        return rel + "\n"
    if kind == 1:
        return rel[:at] + rnd.choice("a/.\né") + rel[at:]
    if kind == 2:
        return rel[:at] + rel[at + 1 :]
    if kind == 3:
        return rel + "/"
    if kind == 4:
        return rel[:at] + "/" + rel[at + 1 :]
    return rel


def test_a_large_generated_set_of_globs_and_paths_agrees() -> None:
    rnd = random.Random(565)
    matched = compared = 0
    for _ in range(40_000):
        glob = _random_glob(rnd)
        built = _path_for(glob, rnd)
        for rel in (built, _mutated(built, rnd), _mutated(built, rnd)):
            want = _regex_match(glob, rel)
            matched += want
            compared += 1
            assert glob_match(glob, rel) is want, (glob, rel)
    assert compared == 120_000
    assert 0.25 < matched / compared < 0.9  # both answers are well represented


def _real_globs(tmp_path: Path) -> list[str]:
    """Every glob Scout ships, plus the ones in the privacy corpus's overlays."""
    policy = privacy.load_policy(None)
    lint = load_lint_config(tmp_path)
    globs = [g for g, _ in policy.visibility_defaults]
    globs += [*policy.local_only, *policy.local_only_always, *policy.sync_allow, *policy.shared_files]
    globs += [d.glob for d in policy.federation_defaults]
    globs += [b.glob for b in lint.budgets]
    globs += [*lint.log_globs, *lint.strict_line_globs, *lint.exclude_globs, *lint.topic_globs]
    globs += [*lint.report_exclude_globs, *DEFAULT_EXCLUDE]
    for case in json.loads(CORPUS.read_text(encoding="utf-8")):
        globs += re.findall(r"\"([^\"]*[*?/][^\"]*)\"", json.dumps(case.get("policy_overlay") or {}))
    return sorted(set(globs))


def test_the_shipped_globs_agree_on_paths_a_vault_has(tmp_path: Path) -> None:
    rnd = random.Random(453)
    globs = _real_globs(tmp_path)
    assert len(globs) > 20
    cases = json.loads(CORPUS.read_text(encoding="utf-8"))
    paths = sorted({c["path"] for c in cases})
    paths += [_path_for(g, rnd) for g in globs for _ in range(5)]
    paths += ["knowledge-base/session-log/2026-10.md", "knowledge-base/projects/widget/archive/2026/old.md"]
    paths += ["action-items/action-items-2026-11-04.md", ".scout-state/id-map.json", "scout-config.yaml", ""]
    paths += [p + "/" for p in paths[:50]] + [p.upper() for p in paths[:50]]
    matched = 0
    for glob in globs:
        for rel in paths:
            want = _regex_match(glob, rel)
            matched += want
            assert glob_match(glob, rel) is want, (glob, rel)
    assert matched > len(globs)


@pytest.mark.parametrize("glob", [5, None, True, 1.5, ["a"], {"a": 1}, b"a"])
def test_a_glob_of_the_wrong_type_is_still_a_type_error(glob: object) -> None:
    """A stray value fails as it always did, and never reads as a rule."""
    with pytest.raises(TypeError):
        glob_match(glob, "a")  # type: ignore[arg-type]
