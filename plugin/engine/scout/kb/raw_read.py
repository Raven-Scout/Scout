"""What counts as reading the KB around ``scoutctl kb`` (#453; hard-rule audit 2026-10, row 2).

One definition for three users: the ``kb-read-gate`` PreToolUse hook
(:mod:`scout.hooks.kb_read_gate`), which denies these calls in scheduled runs,
the ``kb-read-label`` PostToolUse hook (:mod:`scout.hooks.kb_read_label`),
which labels a Read of any path :func:`is_kb_note_path` accepts, and the eval
harness's ``privacy_hygiene`` check, which counts them. All three judge a path
with :data:`KB_PATH`, so ``knowledge-base.md`` (the KB's index) is KB for each.
Pure: it judges strings and does no I/O, so the eval's scoring layer can import
it.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from typing import Any

KB_PATH = re.compile(r"(?:^|[\s'\"=:/<{,])(?:knowledge-base|knowledge_base|action-items/context)(?:/|\b|$)")
KB_DIRS = ("knowledge-base", "knowledge_base", "action-items/context")
_READ_COMMANDS = {
    "cat", "rg", "grep", "egrep", "fgrep", "ls", "find", "head", "tail", "less", "more", "sed", "awk", "gawk",
    "mawk", "wc", "tree", "bat", "nl", "strings", "stat", "du", "file", "cut", "sort", "uniq", "diff", "cmp",
    "comm", "od", "xxd", "hexdump", "tac", "rev", "paste", "column", "fold", "fmt", "pr", "zcat", "base64",
    "iconv", "jq", "yq", "view", "vi", "vim", "nano", "most", "pg",
}  # fmt: skip
_PREFIX_WORDS = {
    "xargs", "sudo", "command", "builtin", "env", "nice", "time", "exec", "timeout", "gtimeout", "stdbuf",
    "caffeinate", "unbuffer", "parallel", "sh", "bash", "zsh", "dash", "ksh", "eval",
}  # fmt: skip
# Prefix flags that take a separate value (`xargs -n 1 wc`, `nice -n 10 cat`): the value is not the command.
_PREFIX_VALUE_FLAGS = {
    "xargs": {"-n", "-I", "-L", "-P", "-s", "-d", "-E", "-a", "--max-args", "--max-procs", "--delimiter"},
    "parallel": {"-j", "-n", "-N", "-I", "--jobs"},
    "nice": {"-n"},
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-U"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "gtimeout": {"-s", "-k", "--signal", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
    **dict.fromkeys(("sh", "bash", "zsh", "dash", "ksh"), {"-o", "-O", "+o", "+O"}),
}
# A positional the prefix takes before the command (`timeout 10 cat …`).
_PREFIX_POSITIONAL = {"timeout": 1, "gtimeout": 1}
# Shell keywords a command can follow inside a compound (`for …; do wc -c "$f"; done`, `if …; then cat x; fi`).
_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "}", "done", "fi"}
_SEGMENTS = re.compile(r"&&|\|\||[;&|\n`]|\$\(|\(|\)")
_MESSAGE_ARG = re.compile(r"(\s(?:-m|--message|-F|--body|--title))(?:\s+|=)(\"(?:[^\"\\]|\\.)*\"|'[^']*')")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GIT_READ = re.compile(r"^(?:-C\s+\S+\s+)?(?:show|diff|grep|blame|annotate|cat-file|log\b.*\s(?:-p|--patch|-u)\b)")
# An input redirection from a KB path (`wc -c <knowledge-base/x.md`, `done < knowledge-base/list`,
# `$(<knowledge-base/x.md)`), not a here-document (`<<`), a here-string (`<<<`) or `<(…)`.
_KB_REDIRECT = re.compile(
    r"(?<![<\d])\d?<(?![<(&])\s*['\"]?[^\s;&|'\"]*(?:knowledge-base|knowledge_base|action-items/context)(?:/|\b|$)"
)
# A variable a command sets: `f=…`, `export f=…`, `local f=…`, `declare -x f=…`, `readonly f=…`.
_SETS = re.compile(
    r"(?:^|[\s;&|(`{])(?:(?:export|local|declare|typeset|readonly)\s+(?:-\w+\s+)*)?([A-Za-z_][A-Za-z0-9_]*)=([^;\n]*)"
)
_FOR = re.compile(r"(?:^|[\s;&|(`{])(?:for|select)\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+([^;\n]*)")
_READ_VAR = re.compile(r"(?:^|[\s;&|(`{])(read|mapfile|readarray)\b([^;&|\n)]*)")
# The flags of `read` and `mapfile` that take a value: the value is not a variable name.
_READ_VALUE_FLAGS = {
    "read": {"-d", "-i", "-n", "-N", "-p", "-t", "-u"},
    "mapfile": {"-d", "-n", "-O", "-s", "-u", "-C", "-c"},
    "readarray": {"-d", "-n", "-O", "-s", "-u", "-C", "-c"},
}
# What puts KB paths in front of a later command: a KB path, or a `scoutctl kb` command that prints paths.
# (`kb read` prints a note's labelled text: reading that through a pipe or a variable is fine.)
_KB_SOURCE = re.compile(
    r"(?:knowledge-base|knowledge_base|action-items/context)"
    r"|\bscoutctl\s+kb\s+(?:files|search|resolve|name-lookup|delta|structure|query|related|traverse|path|stat)\b"
)
# A recursive read: what it reads under a folder it doesn't name.
_RECURSIVE_FLAG = re.compile(r"^-(?:[A-Za-z]*[rR][A-Za-z]*|-recursive|-dereference-recursive|d=?recurse)$")
# knowledge-base/ontology/schema.yaml and parser.py are tooling, not notes: reading them is fine.
ONTOLOGY_TOOLING = re.compile(r"(?:^|/)(?:knowledge-base|knowledge_base)/ontology/(?:schema\.ya?ml|parser\.py)$")
WHOLE_VAULT_GLOBS = {"*", "**", "**/*", "*.md", "**/*.md", "**/*.*"}


def _words(segment: str) -> list[str]:
    return _strip(segment.split())[0]


def _strip(words: list[str]) -> tuple[list[str], bool]:
    """``words`` without what runs before the command: prefix programs (``xargs``, ``sudo``,
    ``env X=1``, ``timeout 10``) with their flags and values, assignments and shell keywords
    (``do``, ``then``, ``!``). Also whether a prefix that feeds the command its arguments from
    stdin (``xargs``, ``parallel``) was among them."""
    fed = False
    words = list(words)
    prefix: str | None = None
    while words:
        word = words[0]
        if word in _PREFIX_WORDS:
            prefix = word
            fed = fed or word in ("xargs", "parallel")
            words.pop(0)
            for _ in range(_PREFIX_POSITIONAL.get(word, 0)):
                if words and not words[0].startswith("-"):
                    words.pop(0)
        elif word.startswith("-") and prefix is not None:
            words.pop(0)
            if word in _PREFIX_VALUE_FLAGS.get(prefix, ()) and words:
                words.pop(0)
        elif word.startswith("-"):
            words.pop(0)  # a stray flag with no prefix before it
        elif _ASSIGNMENT.match(word) or word in _KEYWORDS:
            words.pop(0)
        else:
            break
    return words, fed


def _unquoted(text: str) -> str:
    """``text`` as the shell would join it: quotes and backslashes gone (``know"ledge-base"`` → ``knowledge-base``)."""
    return re.sub(r"[\"'\\]", "", text)


def _names_kb(text: str) -> bool:
    return bool(KB_PATH.search(" " + text) or KB_PATH.search(" " + _unquoted(text)))


def _kb_variables(command: str) -> set[str]:
    """The shell variables this command fills with a KB path or with what ``scoutctl kb`` prints:
    ``f=knowledge-base/x.md``, ``for f in knowledge-base/*``, ``for f in $(scoutctl kb files)``,
    ``… | while read f``, and any variable set from one of those (``D=$P/knowledge``)."""
    tainted: set[str] = set()
    sets = [(m.group(1), m.group(2)) for m in _SETS.finditer(command)]
    sets += [(m.group(1), m.group(2)) for m in _FOR.finditer(command)]
    if _KB_SOURCE.search(command):
        for m in _READ_VAR.finditer(command):
            tainted.update(_read_names(m.group(1), m.group(2).split()))
    while True:
        grown = {
            name
            for name, value in sets
            if name not in tainted
            and (_KB_SOURCE.search(value) or any(re.search(rf"\$\{{?{re.escape(t)}\b", value) for t in tainted))
        }
        if not grown:
            return tainted
        tainted |= grown


def _read_names(builtin: str, words: list[str]) -> set[str]:
    """The variables ``read -r f`` / ``mapfile -t lines`` fill: words that aren't flags or a flag's value."""
    names: set[str] = set()
    skip = False
    for word in words:
        if skip:
            skip = False
        elif word in _READ_VALUE_FLAGS[builtin]:
            skip = True
        elif not word.startswith("-") and re.fullmatch(r"[A-Za-z_]\w*", word):
            names.add(word)
    return names


def _uses(text: str, names: set[str]) -> bool:
    return any(re.search(rf"\$\{{?{re.escape(n)}\b", text) for n in names)


def _recursive_roots(name: str, args: list[str]) -> list[str] | None:
    """Where a recursive read starts: ``rg x``, ``grep -r x .``, ``find . -name …``, ``ls -R``,
    ``tree``, ``du -a``. ``[]`` is the shell's folder; None is not a recursive read, or one whose
    file filter stays out of the KB (``--glob 'action-items/*.md'``, ``--include=*.py``), as for
    the Grep tool."""
    flags, plain = _split_args(args)
    filters: list[str] = []
    if name in ("grep", "egrep", "fgrep"):
        if not any(_RECURSIVE_FLAG.match(f) for f in flags):
            return None
        has_pattern_flag = any(f in ("-e", "-f") or f.startswith(("--regexp", "--file")) for f in flags)
        values = _flag_values(args, {"-e", "-f", "-A", "-B", "-C", "-m", "--include", "--exclude"})
        plain = [a for a in plain if a not in values]
        roots = plain if has_pattern_flag else plain[1:]
        filters = [f.split("=", 1)[1] for f in flags if f.startswith("--include=")]
        filters += _flag_values(args, {"--include"})
    elif name == "rg":
        has_pattern_flag = any(f in ("-e", "-f") or f.startswith(("--regexp", "--file")) for f in flags)
        values = _flag_values(
            args, {"-g", "--glob", "--iglob", "-t", "--type", "-T", "-e", "--regexp", "-f", "--file", "-A", "-B", "-C"}
        )
        plain = [a for a in plain if a not in values]
        roots = plain if has_pattern_flag else plain[1:]
        filters = _flag_values(args, {"-g", "--glob", "--iglob"})
        filters += [f.split("=", 1)[1] for f in flags if f.startswith(("--glob=", "--iglob="))]
    elif name == "find":
        roots = []
        for a in args:
            if a.startswith(("-", "(", "!")):
                break
            roots.append(a)
    elif name == "ls":
        if not any(re.match(r"^-[A-Za-z]*R", f) or f == "--recursive" for f in flags):
            return None
        roots = plain
    elif name == "du":
        if any(re.match(r"^-[A-Za-z]*s", f) or f == "--summarize" for f in flags):
            return None
        roots = plain
    elif name == "tree":
        roots = plain
    else:
        return None
    if not roots and filters and not any(_names_kb(f) or f.strip("\"'") in WHOLE_VAULT_GLOBS for f in filters):
        return None
    return roots


def _split_args(args: list[str]) -> tuple[list[str], list[str]]:
    """``(flags, positionals)``: after ``--`` every word is a positional (``grep -r -- --no-verify x``)."""
    flags: list[str] = []
    plain: list[str] = []
    ended = False
    for a in args:
        if ended or not a.startswith("-"):
            plain.append(a)
        elif a == "--":
            ended = True
        else:
            flags.append(a)
    return flags, plain


def _root_folder(word: str) -> str:
    """A root argument as a folder: a glob stands for the folder it expands in (``*`` → ``.``)."""
    word = word.strip("\"'")
    if word in ("$PWD", "${PWD}"):
        return "."
    parts = word.split("/")
    for i, part in enumerate(parts):
        if any(c in part for c in "*?["):
            return "/".join(parts[:i]) or ("/" if word.startswith("/") else ".")
    return word


def _flag_values(args: list[str], takes_value: set[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a in takes_value]


def _cd(cwd: str | None, target: str) -> str | None:
    """The shell's folder after ``cd target`` from ``cwd``; None when the gate can't tell."""
    target = target.strip("\"'")
    if not target or target.startswith(("~", "$", "-")):
        return None
    if target.startswith("/"):
        return posixpath.normpath(target)
    return None if cwd is None else posixpath.normpath(posixpath.join(cwd, target))


def _reaches_vault(here: str | None, root: str, vault: str | None) -> bool:
    """Whether a recursive read rooted at ``root`` (from the shell's folder ``here``) takes in the vault.
    Home (``~``, ``$HOME``) holds the vault, as far as the gate can tell."""
    folder = _root_folder(root)
    if folder.startswith(("~", "$HOME", "${HOME}")):
        return vault is not None and folder.rstrip("/") in ("~", "$HOME", "${HOME}")
    return _above_or_at(_cd(here, folder), vault)


def _above_or_at(folder: str | None, vault: str | None) -> bool:
    """Whether a recursive read rooted at ``folder`` takes in the vault's KB."""
    if folder is None or vault is None:
        return False
    folder = folder.rstrip("/") or "/"
    return vault == folder or vault.startswith(folder.rstrip("/") + "/")


def raw_kb_read(command: str, *, in_kb: bool = False, cwd: str | None = None, vault: str | None = None) -> bool:
    """Does this shell command read ``knowledge-base/`` or ``action-items/context/`` around ``scoutctl kb``?

    Each shell segment is judged by its command word, after prefixes (``xargs``, ``sudo``,
    ``timeout 10``), assignments and shell keywords (``do``, ``then``): a reading command
    (``/bin/cat`` too) with a KB path among its arguments, quoted or not, or run after ``cd``
    or ``pushd`` into the KB (``in_kb``: the shell starts there); one that reads a variable the
    command filled with a KB path or with ``scoutctl kb``'s output (``f=knowledge-base/x; wc -c
    $f``, ``for f in $(scoutctl kb files); do wc -c "$f"``); one ``xargs`` feeds in a command
    that prints KB paths; ``git show|diff|grep|blame|log -p`` with a KB path; an input
    redirection from a KB path (``wc -c <knowledge-base/x``); ``sh -c``/``eval`` of a program
    that does any of this. With ``cwd`` and ``vault`` (absolute, as the gate knows them), a
    recursive read rooted at the vault or above (``rg x``, ``grep -r x .``, ``find .``) counts
    too. ``scoutctl …`` segments and quoted commit messages never count.
    """
    command = _MESSAGE_ARG.sub(r"\1 ''", command)
    tainted = _kb_variables(command)
    feeds_kb = bool(_KB_SOURCE.search(command))
    here = posixpath.normpath(cwd) if cwd else None
    for segment in _SEGMENTS.split(command):
        raw = segment.split()
        if raw and raw[0].rsplit("/", 1)[-1] == "scoutctl":
            continue
        if _KB_REDIRECT.search(segment):
            return True
        words, fed = _strip(raw)
        if not words:
            continue
        # A quoted program (`bash -c 'cat …'`, `eval "cat …"`) starts with a quote the shell removes.
        name, args = words[0].strip("\"'").rsplit("/", 1)[-1], words[1:]
        rest = " ".join(args)
        if name == "scoutctl":
            continue
        if name in ("cd", "pushd"):
            in_kb = _names_kb(rest)
            here = _cd(here, args[0]) if args else None
        elif name in _READ_COMMANDS:
            if in_kb or _names_kb(rest) or _uses(rest, tainted) or (fed and feeds_kb):
                return True
            roots = _recursive_roots(name, args) if vault is not None else None
            if roots is not None and any(_reaches_vault(here, r, vault) for r in roots or ["."]):
                return True
        elif name == "git" and _GIT_READ.search(_unquoted(rest)) and (_names_kb(rest) or _uses(rest, tainted)):
            return True
    return False


def filter_reaches_kb(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    """A Grep/Glob rooted at the vault reaches the KB: Glob's pattern or Grep's ``glob`` names the KB
    or is a whole-vault pattern (``**``, ``*.md``); Grep with no ``glob`` searches everything."""
    target = str(tool_input.get("pattern" if tool_name == "Glob" else "glob") or "").strip()
    if not target:
        return tool_name == "Grep"
    return bool(KB_PATH.search(" " + target)) or target in WHOLE_VAULT_GLOBS


def search_reads_kb(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    """A Grep/Glob call aimed at the KB, judged on its arguments alone: its path is in it, or it has
    no path (the vault) and :func:`filter_reaches_kb`."""
    path = str(tool_input.get("path") or "")
    if path:
        return bool(KB_PATH.search(" " + path))
    return filter_reaches_kb(tool_name, tool_input)


def is_kb_path(path: str) -> bool:
    """A path string (absolute, or relative to the vault) in the KB: under one of :data:`KB_DIRS`, or
    the index ``knowledge-base.md``. The one path rule the gate, the label hook and the eval share."""
    return bool(KB_PATH.search(" " + path))


def is_kb_note_path(path: str) -> bool:
    """A path string in the KB that isn't ontology tooling (what a Read of the KB means)."""
    return is_kb_path(path) and not ONTOLOGY_TOOLING.search(posixpath.normpath(path))
