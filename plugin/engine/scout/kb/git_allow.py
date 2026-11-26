"""The git a scheduled run may use: an allowlist of subcommands and options (review of #817, round 6).

Fifty scheduled runs used git 148 times, only as ``log``, ``config`` (reads), ``show``, ``status``, ``diff``, ``add``
and ``remote``, with display options and at most a ``-C <dir>`` before the subcommand. Commits go through
``scripts/git-safe-commit.sh``. So in a run a git call is judged by what it may be, not by what it may do: any
subcommand or option not listed here is refused, and the listed ones stay judged by what they read
(:mod:`scout.kb.git_read`), as defence in depth.

The lists are exact. A long option is spelled out in full (git takes any unique prefix, so ``--comm`` could be
``--comment``, and no prefix is allowed), as ``--opt=value`` or ``--opt value`` where it takes a value. A short
option is one this module lists, alone or in a cluster of listed ones (``-sb``). A word the shell fills in before
``--`` (``$X``, ``$(…)``) must be one the command set, so the gate can read it: a word that starts with an
expansion may expand to an option. A glob there may match a file named like one (``-p``), so it is refused too.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

FLAG = "flag"  # no value
VALUE = "value"  # a value: `--opt=v` or `--opt v` (`-n 5` or `-n5` for a short one)
GLUED = "glued"  # an optional value, glued only (`--stat=80`, `--porcelain=v2`)
NEXT = "next"  # an optional value that takes the next word when it is no option (`--contains [<commit>]`)


@dataclass(frozen=True)
class Spec:
    """What one subcommand may take: its long and short options (any other word is a rev, pathspec or key),
    whether it takes ``-<N>`` counts, and a last check on what was given (``check(positionals, options)`` returns
    the reason a form is refused, else None)."""

    longs: dict[str, str]
    shorts: dict[str, str] = field(default_factory=dict)
    numbers: bool = False
    check: Callable[[list[str], set[str]], str | None] | None = None


def _longs(spec: str) -> dict[str, str]:
    """``"--a --b= --c=? --d=>"``: a flag, one with a value, an optional glued value, an optional next word."""
    out = {}
    for word in spec.split():
        if word.endswith("=?"):
            out[word[:-2]] = GLUED
        elif word.endswith("=>"):
            out[word[:-2]] = NEXT
        elif word.endswith("="):
            out[word[:-1]] = VALUE
        else:
            out[word] = FLAG
    return out


def _shorts(flags: str, values: str = "") -> dict[str, str]:
    return {**dict.fromkeys(flags, FLAG), **dict.fromkeys(values, VALUE)}


def _list_only(*listing: str) -> Callable[[list[str], set[str]], str | None]:
    """``branch``/``tag``: positional words only in list mode (they are patterns there), since without it they
    name a branch or tag to create."""

    def check(positionals: list[str], options: set[str]) -> str | None:
        return "a name outside list mode" if positionals and not options & set(listing) else None

    return check


_LISTS = "--list -l --contains --no-contains --merged --no-merged --points-at"

SPECS: dict[str, Spec] = {
    "log": Spec(
        _longs(
            "--since= --until= --date= --format= --pretty=? --oneline --no-merges --grep= --name-only "
            "--name-status --stat=? --shortstat --all --diff-filter= --max-count= --author= --reverse "
            "--first-parent --decorate=? --graph"
        ),
        _shorts("E", "n"),
        numbers=True,
    ),
    "show": Spec(
        _longs("--stat=? --format= --pretty=? --name-only --name-status --oneline --no-patch"),
        _shorts("s"),
    ),
    "status": Spec(_longs("--short --porcelain=? --branch --untracked-files=?"), _shorts("sb")),
    "diff": Spec(_longs("--stat=? --shortstat --name-only --name-status --numstat --check --cached --staged")),
    "add": Spec(_longs("--all --update"), _shorts("Au")),
    "rev-parse": Spec(
        _longs(
            "--show-toplevel --show-prefix --show-cdup --git-dir --git-common-dir --absolute-git-dir "
            "--is-inside-work-tree --is-inside-git-dir --is-bare-repository --is-shallow-repository "
            "--abbrev-ref=? --short=? --verify --quiet --symbolic --symbolic-full-name --all"
        ),
        _shorts("q"),
    ),
    "ls-files": Spec(
        _longs(
            "--cached --others --modified --deleted --ignored --exclude-standard --full-name --error-unmatch "
            "--stage --unmerged --directory --no-empty-directory --deduplicate"
        ),
        _shorts("comdiszut"),
    ),
    "branch": Spec(
        _longs(
            "--all --remotes --verbose --show-current --list --sort= --format= --merged=> --no-merged=> "
            "--contains=> --no-contains=> --points-at= --no-color --no-column --ignore-case"
        ),
        _shorts("arvli"),
        check=_list_only(*_LISTS.split()),
    ),
    "tag": Spec(
        _longs(
            "--list --sort= --format= --contains=> --no-contains=> --merged=> --no-merged=> --points-at= "
            "--no-column --ignore-case"
        ),
        _shorts("li"),
        check=_list_only(*_LISTS.split(), "-n"),
    ),
    "describe": Spec(
        _longs("--tags --all --always --abbrev=? --long --dirty=? --exact-match --match= --exclude= --first-parent")
    ),
    "merge-base": Spec(_longs("--is-ancestor --all --fork-point --octopus --independent")),
    "rev-list": Spec(
        _longs(
            "--count --all --since= --until= --max-count= --no-merges --merges --first-parent --left-right "
            "--reverse --ancestry-path"
        ),
        _shorts("", "n"),
        numbers=True,
    ),
    "shortlog": Spec(_longs("--summary --numbered --email --since= --until= --all --no-merges"), _shorts("sne")),
}
# `tag -n<N>`: a count glued to it, or none.
_TAG_LINES = re.compile(r"^-n[0-9]*$")
ALLOWED = (*SPECS, "config", "remote")
# The keys a run's Step 0 sets in the vault outside the write sandbox (phases/core/git-setup.md).
WRITE_KEYS = ("user.email", "user.name")
_CONFIG_READS = {"--get": (1, 2), "--get-regexp": (1, 2), "--list": (0, 0), "-l": (0, 0)}


def judge(
    sub: str,
    args: list[str],
    *,
    dynamic: Callable[[str], bool],
    expand: Callable[[str], list[str] | None],
    plain: Callable[[str], str],
) -> str | None:
    """Why ``git <sub> <args>`` is no form a run may use, else None. ``args`` are the shell's words as the gate
    reads them: ``dynamic(word)`` says whether the shell fills part of one in (and so may glob or expand it),
    ``expand(word)`` gives the words it can become (None when the gate can't know), and ``plain(word)`` the word
    as git gets it when it is not dynamic."""
    if sub in ("config", "remote"):
        if any(dynamic(a) for a in args):
            return "a word the gate can't read"
        return (_config if sub == "config" else _remote)([plain(a) for a in args])
    spec = SPECS.get(sub)
    if spec is None:
        return sub
    positionals: list[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if word == "--":
            positionals += args[i:]  # pathspecs from here on: no word can be an option
            break
        expanded = dynamic(word)
        words = expand(word) if expanded else [plain(word)]
        if words is None:
            return "a word the gate can't read"
        for one in words:
            reason, takes = _option(sub, spec, one, seen, positionals, expanded=expanded)
            if reason is not None:
                return reason
            if takes and i < len(args) and (takes == VALUE or not args[i].startswith("-")):
                i += 1  # the option's value, whatever it is
    return spec.check(positionals, seen) if spec.check else None


def _option(
    sub: str, spec: Spec, word: str, seen: set[str], positionals: list[str], *, expanded: bool
) -> tuple[str | None, str | None]:
    """Judge one word: (why it is refused, else None; VALUE/NEXT when the next word is its value)."""
    if word.startswith("--"):
        name, eq, _ = word.partition("=")
        kind = spec.longs.get(name)
        if kind is None or (eq and kind == FLAG) or (expanded and kind in (VALUE, NEXT) and not eq):
            return name, None
        seen.add(name)
        if not eq and kind in (VALUE, NEXT):
            return None, kind
        return None, None
    if word.startswith("-") and len(word) > 1:
        if spec.numbers and word[1:].isdigit():
            return None, None
        if sub == "tag" and _TAG_LINES.match(word):
            seen.add("-n")
            return None, None
        for j, letter in enumerate(word[1:], 1):
            kind = spec.shorts.get(letter)
            if kind is None:
                return f"-{letter}", None
            seen.add(f"-{letter}")
            if kind == VALUE:
                if j + 1 < len(word):
                    return None, None  # glued: `-n5`
                return (f"-{letter}", None) if expanded else (None, VALUE)
        return None, None
    positionals.append(word)
    return None, None


def _config(args: list[str]) -> str | None:
    """``git config`` reads only: ``<key>``, ``--get <key> [<pattern>]``, ``--get-regexp <re> [<pattern>]``,
    ``--list``/``-l``, ``get <key>``, ``list``, each with ``--show-origin`` if wanted; and Step 0's two writes,
    ``git config user.email <v>`` / ``user.name <v>`` with a literal one-line value and nothing else."""
    if len(args) == 2 and args[0].casefold() in WRITE_KEYS:
        value = args[1]
        return "write" if any(c in value for c in "\n\r\0") or value.startswith("-") else None
    words = [a for a in args if a != "--show-origin"]
    if words[:1] in (["get"], ["list"]):
        rest = words[1:]
        if any(w.startswith("-") for w in rest) or len(rest) != (1 if words[0] == "get" else 0):
            return words[0]
        return None
    options = [w for w in words if w.startswith("-")]
    plain = [w for w in words if not w.startswith("-")]
    if not options:
        # One key (`section.key`): a read. One word with no dot is a subcommand (`git config edit` opens an editor).
        return None if len(plain) == 1 and "." in plain[0].strip(".") else "write"
    if len(options) != 1 or options[0] not in _CONFIG_READS:
        return options[0] if options[0] not in _CONFIG_READS else " ".join(options)
    low, high = _CONFIG_READS[options[0]]
    return None if low <= len(plain) <= high and words[0] == options[0] else options[0]


def _remote(args: list[str]) -> str | None:
    """``git remote``, ``-v``, ``get-url [--push|--all] <name>``, ``show [-n] [<name>…]``."""
    if args in ([], ["-v"], ["--verbose"]):
        return None
    if args[:1] == ["get-url"]:
        rest = [a for a in args[1:] if a not in ("--push", "--all")]
        return None if len(rest) == 1 and not rest[0].startswith("-") else "get-url"
    if args[:1] == ["show"]:
        rest = [a for a in args[1:] if a != "-n"]
        return None if not any(a.startswith("-") for a in rest) else "show"
    return args[0]


def forms() -> str:
    """The forms a run may use, for the deny message."""
    return (
        "`git log` (--oneline, --format, --since, --stat, --name-only, -n …), `git show` (--stat, --format, "
        "--no-patch), `git status` (--short, --porcelain), `git diff` (--stat, --name-only, --cached), "
        "`git add <paths>`, `git config <key>` (reads), `git remote -v|get-url|show`, `git rev-parse`, "
        "`git ls-files`, `git branch`/`git tag` (list), `git describe`, `git merge-base`, `git rev-list --count`, "
        "`git shortlog -sn`, with at most `-C <dir>` before the subcommand and no GIT_* or PATH set"
    )
