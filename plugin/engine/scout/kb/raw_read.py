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

import fnmatch
import os
import posixpath
import re
import shlex
import unicodedata
from collections.abc import Callable, Mapping
from typing import Any

KB_PATH = re.compile(r"(?:^|[\s'\"=:/<{,])(?:knowledge-base|knowledge_base|action-items/context)(?:/|\b|$)")
KB_DIRS = ("knowledge-base", "knowledge_base", "action-items/context")
_READ_COMMANDS = {
    "cat", "rg", "grep", "egrep", "fgrep", "ls", "find", "head", "tail", "less", "more", "sed", "awk", "gawk",
    "mawk", "wc", "tree", "bat", "nl", "strings", "stat", "du", "file", "cut", "sort", "uniq", "diff", "cmp",
    "comm", "od", "xxd", "hexdump", "tac", "rev", "paste", "column", "fold", "fmt", "pr", "zcat", "base64",
    "iconv", "jq", "yq", "view", "vi", "vim", "nano", "most", "pg", "ag", "ack", "fd", "ugrep",
    "ug", "ug+", "ugrep+", "fdfind", "ack-grep",  # aliases: _command_name reads them as ugrep, fd, ack
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
_GIT_READ = re.compile(
    r"^(?:-C\s+\S+\s+)?(?:(?i:show|diff|grep|blame|annotate|cat-file)|(?i:log)\b.*\s(?:-p|--patch|-u)\b)"
)
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
    r"|\b(?i:scoutctl)\s+kb\s+(?:files|search|resolve|name-lookup|delta|structure|query|related|traverse|path|stat)\b"
)
# knowledge-base/ontology/schema.yaml and parser.py are tooling, not notes: reading them is fine.
ONTOLOGY_TOOLING = re.compile(r"(?:^|/)(?:knowledge-base|knowledge_base)/ontology/(?:schema\.ya?ml|parser\.py)$")
WHOLE_VAULT_GLOBS = {"*", "**", "**/*", "*.md", "**/*.md", "**/*.*"}
# The file formats the KB keeps facts in: notes, and the data files beside them (ontology entities,
# the CSV and JSON tables an ingestion writes). A file filter naming one of these does not keep a
# search rooted at the vault out of the KB (#541). Code (``*.py``, ``*.sh``) and logs do.
KB_FILE_EXTENSIONS = ("md", "markdown", "yaml", "yml", "csv", "tsv", "json", "txt", "html", "htm")
# ripgrep's ``-t`` names for those formats, and ``all``. ripgrep has no ``tsv`` type, and its ``csv``
# type is ``*.csv`` only, so ``rg -t tsv`` is an error and ``*.tsv`` is caught as a glob only.
_KB_RG_TYPES = {"all", "md", "markdown", "yaml", "csv", "json", "txt", "html"}


class _Stripped:
    """A segment's command words after its prefixes, and what the prefixes said."""

    def __init__(self, words: list[str], fed: bool, chdir: str | None) -> None:
        self.words = words
        self.fed = fed  # `xargs`/`parallel` gives the command its arguments from stdin
        self.chdir = chdir  # `env -C <dir>` runs it in another folder


def _strip(words: list[str]) -> _Stripped:
    """``words`` without what runs before the command: prefix programs (``xargs``, ``sudo``,
    ``env X=1``, ``timeout -s KILL 10``) with their flags and values, assignments and shell
    keywords (``do``, ``then``, ``!``). ``command -v x`` runs nothing."""
    fed = False
    chdir: str | None = None
    words = list(words)
    prefix: str | None = None
    pending = 0  # positionals the prefix still takes before the command (`timeout 10 …`)
    while words:
        word = words[0]
        base = _command_name(word)  # `/usr/bin/env`, `/usr/bin/xargs` are prefixes too
        if base in _PREFIX_WORDS and (fold(_unquoted(word)) == base or "/" in word):
            if base == "command" and len(words) > 1 and words[1] in ("-v", "-V") and not _names_kb(" ".join(words[2:])):
                return _Stripped([], fed, chdir)  # a lookup: nothing runs (main denied one naming a KB path)
            word = base
            prefix = word
            fed = fed or word in ("xargs", "parallel")
            pending = _PREFIX_POSITIONAL.get(word, 0)
            words.pop(0)
        elif word.startswith("-") and prefix is not None:
            words.pop(0)
            if prefix == "env" and word.startswith("--chdir="):
                chdir = word.split("=", 1)[1]
            elif word in _PREFIX_VALUE_FLAGS.get(prefix, ()) and words:
                value = words.pop(0)
                if prefix == "env" and word in ("-C", "--chdir"):
                    chdir = value
        elif word.startswith("-"):
            words.pop(0)  # a stray flag with no prefix before it
        elif pending and prefix is not None:
            pending -= 1
            words.pop(0)
        elif _ASSIGNMENT.match(word) or word in _KEYWORDS:
            words.pop(0)
        else:
            break
    return _Stripped(words, fed, chdir)


def _unquoted(text: str) -> str:
    """``text`` as the shell would join it: quotes and backslashes gone (``know"ledge-base"`` → ``knowledge-base``)."""
    return re.sub(r"[\"'\\]", "", text)


def _names_kb(text: str) -> bool:
    return bool(KB_PATH.search(" " + text) or KB_PATH.search(" " + _unquoted(text)))


# What a `$(…)` or backtick that runs a path-printing `scoutctl kb` command stands for: KB paths.
_KB_PATHS = "knowledge-base/__scoutctl_kb_paths__"


def _substitutions(command: str) -> list[tuple[int, int]]:
    """The ``(start, end)`` spans of each outermost ``$(…)`` and backtick substitution."""
    spans: list[tuple[int, int]] = []
    i, n = 0, len(command)
    while i < n:
        if command.startswith("$(", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if command.startswith("$(", j):
                    depth, j = depth + 1, j + 2
                    continue
                depth += {"(": 1, ")": -1}.get(command[j], 0)
                j += 1
            spans.append((i, j))
            i = j
        elif command[i] == "`":
            j = command.find("`", i + 1)
            j = n if j < 0 else j + 1
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def _with_kb_paths(command: str) -> str:
    """``command`` with each substitution that runs a path-printing ``scoutctl kb`` replaced by
    :data:`_KB_PATHS`, so ``cat $(scoutctl kb files)`` reads as ``cat <KB paths>``."""
    out, last = [], 0
    for start, end in _substitutions(command):
        if _KB_SOURCE.search(command[start:end]) and _SCOUTCTL_KB.search(command[start:end]):
            out += [command[last:start], " ", _KB_PATHS, " "]
            last = end
    return "".join(out) + command[last:] if out else command


_SCOUTCTL_KB = re.compile(r"\b(?i:scoutctl)\s+kb\b")


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


def _local_values(command: str) -> dict[str, str]:
    """Plain values the command itself assigns (``d=scripts; rg x $d``), for resolving a root."""
    out: dict[str, str] = {}
    for m in _SETS.finditer(command):
        value = m.group(2).split()[0] if m.group(2).split() else ""
        if value and not any(c in value for c in "$`();&|<>"):
            out[m.group(1)] = value.strip("\"'")
    return out


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


# Reader flags that take a separate value: `head -n $n file` reads `file`, not `$n`.
_GREP_VALUES = {"-e", "-f", "-m", "-A", "-B", "-C", "-d", "-D", "--max-count", "--after-context",
                "--before-context", "--context", "--include", "--exclude", "--exclude-dir", "--exclude-from",
                "--label", "--binary-files", "--devices", "--directories", "--regexp", "--file"}  # fmt: skip
_RG_VALUES = {"-A", "-B", "-C", "-e", "-f", "-g", "-j", "-m", "-M", "-t", "-T", "-E", "-d", "-r",
              "--after-context", "--before-context", "--context", "--regexp", "--file", "--glob", "--iglob",
              "--threads", "--max-count", "--max-columns", "--type", "--type-not", "--encoding", "--max-depth",
              "--maxdepth", "--max-filesize", "--path-separator", "--replace", "--sort", "--sortr", "--colors",
              "--context-separator", "--field-context-separator", "--field-match-separator", "--ignore-file",
              "--pre", "--pre-glob", "--type-add", "--type-clear", "--dfa-size-limit", "--regex-size-limit",
              "--engine", "--hostname-bin", "--hyperlink-format", "--generate"}  # fmt: skip
_FIND_VALUES = {"-name", "-iname", "-path", "-ipath", "-wholename", "-regex", "-iregex", "-type", "-size",
                "-mtime", "-mmin", "-atime", "-amin", "-ctime", "-cmin", "-newer", "-user", "-group", "-perm",
                "-maxdepth", "-mindepth", "-printf", "-fprint", "-links", "-inum", "-samefile"}  # fmt: skip
# ag's `-A`/`-B`/`-C` take a count only when the next word is a number (`ag -A x` searches for `x`),
# and its `--after`/`--before`/`--context` take one only attached (`--context=3`).
_AG_VALUES = {"-A", "-B", "-C", "-G", "-g", "-m", "-p", "-W", "--depth", "--file-search-regex", "--ignore",
              "--ignore-dir", "--max-count", "--pager", "--path-to-ignore", "--workers", "--width",
              "--color-path", "--color-match", "--color-line-number"}  # fmt: skip
_AG_COUNTS = {"-A", "-B", "-C"}
_ACK_VALUES = {"-A", "-B", "-C", "-g", "-m", "-t", "-T", "--after-context", "--before-context", "--context",
               "--ignore-dir", "--ignore-file", "--match", "--max-count", "--output", "--pager", "--type",
               "--type-add", "--type-del", "--type-set", "--and", "--or", "--not", "--range-start", "--range-end",
               "--files-from"}  # fmt: skip
_UGREP_VALUES = _GREP_VALUES | {"-g", "--glob", "-t", "-O", "-M", "-N", "-J", "-K", "--and", "--andnot", "--not",
                                 "--depth", "--jobs", "--range", "--iglob", "--include-dir", "--include-from",
                                 "--from", "--file-extension", "--file-type", "--file-magic", "--exclude-fs",
                                 "--include-fs"}  # fmt: skip
_FD_VALUES = {"-d", "-e", "-E", "-t", "-S", "-o", "-j", "-c", "-C", "--max-depth", "--min-depth",
              "--exact-depth", "--extension", "--exclude", "--type", "--size", "--changed-within",
              "--changed-before", "--newer", "--older", "--changed-after", "--change-newer-than",
              "--change-older-than", "--owner", "--base-directory", "--search-path", "--path-separator",
              "--threads", "--max-results", "--color", "--ignore-file", "--ignore-contain", "--format", "--and",
              "--batch-size"}  # fmt: skip
# fd's flags that run a command on what it finds: the words after them are that command, up to a `;`.
_FD_EXEC = {"-x", "--exec", "-X", "--exec-batch"}
# Other names the search tools are installed under (Debian's `fdfind`, `ack-grep`; ugrep's `ug`).
_ALIASES = {"ug": "ugrep", "ug+": "ugrep", "ugrep+": "ugrep", "fdfind": "fd", "ack-grep": "ack"}
# The type names for the formats the KB keeps, in ack's and ugrep's `-t` (ugrep's `+` adds shebangs).
_KB_TOOL_TYPES = {"markdown", "md", "yaml", "yml", "json", "csv", "tsv", "html", "htm", "xml", "text", "txt"}
# Claude Code's shell snapshot (`~/.claude/shell-snapshots/snapshot-zsh-*.sh`), which every Bash call of a run
# sources, defines `grep` and `find` as functions: `grep …` runs the Claude binary's embedded ugrep with these flags
# before the caller's, `find …` its embedded bfs. A program called by its path (`/usr/bin/grep`) is the real one.
# The snapshot is Claude Code's and may change between versions, so a bare `grep`/`egrep`/`fgrep` is judged both
# as itself and as this ugrep, and a bare `find` as itself and as bfs: a read either one makes counts.
# `scoutctl bootstrap doctor` says when the newest snapshot shadows anything else (scout.kb.shell_shadows).
SNAPSHOT_UGREP_FLAGS = (
    "-G", "--ignore-files", "--hidden", "-I",
    *(f"--exclude-dir=.{vcs}" for vcs in ("git", "svn", "hg", "bzr", "jj", "sl")),
)  # fmt: skip
SNAPSHOT_BFS_FLAGS = ("-S", "dfs", "-regextype", "findutils-default")
# The names a run's shell may run as another program, and that program's model here.
SHADOWED = {"grep": "ugrep", "egrep": "ugrep", "fgrep": "ugrep", "find": "bfs"}


def run_models(word: str, name: str, args: list[str]) -> list[tuple[str, list[str]]]:
    """The ways a run's shell may run ``word args`` (``name`` is :func:`_command_name` of ``word``), each as
    ``(model, args)``: the program itself, and for a bare ``grep``/``egrep``/``fgrep``/``find`` (``\\grep``
    and a quoted name too: only a path skips a shell function) also the snapshot's ugrep or bfs. A function is
    found by its exact spelling, so ``GREP`` is the program a case-insensitive disk finds, BSD grep, and
    ``name`` (folded) is that program."""
    alt = SHADOWED.get(_unquoted(word))
    if alt is None or "/" in word:
        return [(name, args)]
    if alt == "ugrep":
        return [(name, args), ("ugrep", [*SNAPSHOT_UGREP_FLAGS, *args])]
    return [(name, args), ("bfs", args)]


_VALUE_FLAGS: dict[str, set[str]] = {
    "head": {"-n", "-c", "--lines", "--bytes"},
    "tail": {"-n", "-c", "--lines", "--bytes"},
    "grep": _GREP_VALUES, "egrep": _GREP_VALUES, "fgrep": _GREP_VALUES,
    "ugrep": _UGREP_VALUES,
    "rg": _RG_VALUES,
    "ag": _AG_VALUES, "ack": _ACK_VALUES, "fd": _FD_VALUES,
    "sed": {"-e", "-f", "-l", "--expression", "--file"},
    "awk": {"-v", "-f", "-F", "--file"}, "gawk": {"-v", "-f", "-F", "--file"}, "mawk": {"-v", "-f", "-F", "--file"},
    "cut": {"-d", "-f", "-c", "-b"},
    "sort": {"-k", "-t", "-o", "-S", "-T"},
    "uniq": {"-f", "-s", "-w"},
    "find": _FIND_VALUES,
    "tree": {"-L", "-P", "-I", "-o"},
    "du": {"-d", "--max-depth", "-B", "--block-size"},
    "jq": {"--arg", "--argjson", "-f", "--from-file"},
}  # fmt: skip


def _scan(name: str, args: list[str]) -> tuple[list[tuple[str, str | None]], list[str]]:
    """``args`` read getopt-style: each option given with its value (``-t md``, ``-tmd``, ``-rt md``,
    ``--type md``, ``--type=md``), and the words that are not a value (flags included)."""
    takes = _VALUE_FLAGS.get(name, set())
    opts: list[tuple[str, str | None]] = []
    words: list[str] = []
    i, ended = 0, False
    while i < len(args):
        a = args[i]
        i += 1
        if ended or a == "-" or not a.startswith("-"):
            words.append(a)
            continue
        words.append(a)
        if a == "--":
            ended = True  # kept in `words`, so _split_args still reads what follows as positionals
        elif a.startswith("--"):
            flag, eq, value = a.partition("=")
            if eq:
                opts.append((flag, value))
            elif flag in takes and i < len(args):
                opts.append((flag, args[i]))
                i += 1
            else:
                opts.append((flag, None))
        else:
            for j, ch in enumerate(a[1:], 1):
                if not ch.isalnum():
                    break  # not a flag letter: the rest is a value we don't know (`sed -i.bak`)
                flag = "-" + ch
                if flag not in takes:
                    opts.append((flag, None))
                    continue
                if j < len(a) - 1:
                    opts.append((flag, a[j + 1 :]))  # attached: `-tpy`, `-rO md` → `-O` takes `md`
                elif i < len(args) and not (name == "ag" and flag in _AG_COUNTS and not args[i].isdigit()):
                    opts.append((flag, args[i]))
                    i += 1
                else:
                    opts.append((flag, None))
                break
    return opts, words


def _options(name: str, args: list[str]) -> list[tuple[str, str | None]]:
    return _scan(name, args)[0]


def _operands(name: str, args: list[str]) -> list[str]:
    """``args`` without the values of the reader's value-taking flags."""
    return _scan(name, args)[1]


def _values(name: str, args: list[str], flags: set[str]) -> list[str]:
    """The values given to any of ``flags``, quotes gone."""
    return [v.strip("\"'") for f, v in _options(name, args) if f in flags and v is not None]


def _split_list(value: str) -> list[str]:
    """A comma-separated list of globs or names, keeping a ``{a,b}`` together."""
    out, depth, start = [], 0, 0
    for i, c in enumerate(value):
        depth += {"{": 1, "}": -1}.get(c, 0)
        if c == "," and depth <= 0:
            out.append(value[start:i])
            start = i + 1
    return [p for p in [*out, value[start:]] if p]


def _uses_operand(name: str, args: list[str], names: set[str]) -> bool:
    return bool(names) and _uses(" ".join(_operands(name, args)), names)


_NOT_A_READ = {"--version", "--help", "-version", "-help", "--type-list"}
# Per tool: the flags that print a version, help or a type list and read no file.
_TOOL_NOT_A_READ = {
    "rg": {"-V"}, "grep": {"-V"}, "egrep": {"-V"}, "fgrep": {"-V"}, "ugrep": {"-V"},
    "ag": {"-V", "--list-file-types"}, "fd": {"-V", "-h"}, "ack": {"--help-types", "--dump"},
}  # fmt: skip


def _not_a_read(name: str, args: list[str]) -> bool:
    return any(a in _NOT_A_READ or a in _TOOL_NOT_A_READ.get(name, ()) for a in args)


# Readers whose first operand is a pattern or a program, not a file, unless a flag gives it (`grep -e x f`).
_PROGRAM_FIRST: dict[str, set[str]] = {
    **dict.fromkeys(("grep", "egrep", "fgrep"), {"-e", "-f", "--regexp", "--file"}),
    "rg": {"-e", "-f", "--regexp", "--file", "--files"},
    "sed": {"-e", "-f", "--expression", "--file"},
    **dict.fromkeys(("awk", "gawk", "mawk"), {"-f", "--file"}),
    "jq": {"-f", "--from-file"},
    "ugrep": {"-e", "-f", "--regexp", "--file"},
    "ag": {"-g"},  # `ag -g PATTERN dir`: -g's value is the pattern
    "ack": {"--match", "-f", "-g"},  # `ack -f dir` lists files: no pattern
    "fd": set(),  # `fd [pattern] [path...]`
}
# Readers that read a folder they're given (its names, or the files below it); the rest only open files.
# ugrep reads a folder it's given one level deep even without -r (`grep TODO context` from `action-items/`).
_FOLDER_READERS = {"tree", "du", "find", "bfs", "rg", "ag", "ack", "fd", "ugrep", "diff"}  # `diff -N dir empty/` too

# find's flags before its paths (BSD and bfs); bfs's flags that take the next word, or attach a number (`-O3`).
_FIND_FLAGS = {"-H", "-L", "-P", "-E", "-X", "-d", "-s", "-x"}
_BFS_FLAG_VALUES = {"-D", "-S"}
_BFS_ATTACHED = re.compile(r"^-[Oj]\d+$")
# bfs's expression words that take the next word (`-fprintf` takes two), and the two that take an optional count.
_BFS_VALUES = {"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex", "-lname",
               "-ilname", "-type", "-xtype", "-size", "-perm", "-user", "-group", "-uid", "-gid", "-inum", "-links",
               "-samefile", "-newer", "-anewer", "-Bnewer", "-cnewer", "-mnewer", "-amin", "-Bmin", "-cmin", "-mmin",
               "-atime", "-Btime", "-ctime", "-mtime", "-asince", "-Bsince", "-csince", "-msince", "-since", "-used",
               "-maxdepth", "-mindepth", "-fstype", "-regextype", "-xattrname", "-limit", "-fls", "-fprint",
               "-fprint0", "-printf", "-files0-from", "-context"}  # fmt: skip
_BFS_NEWER_XY = re.compile(r"^-newer[aBcm][aBcmt]$")
_VALUE_FLAGS["bfs"] = _BFS_VALUES | _BFS_FLAG_VALUES  # not -f: its value is a path bfs searches
_FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
_FILES0 = "-files0-from"  # bfs: the paths to search, NUL-separated, in a file (`-` for stdin)


def _find_roots(args: list[str], bfs: bool) -> list[str]:
    """The paths ``find args`` searches, as written (none: bfs searches ``.``, BSD find stops). BSD find takes them
    after its flags (``-L``, ``-s``, ``-f PATH``) and before the expression. bfs takes them anywhere: ``find docs
    -name '*.md' .`` searches ``.`` too (words its expression takes are not paths: ``-name x``, ``-exec … ;``)."""
    roots: list[str] = []
    i = 0
    while i < len(args):  # the leading flags, for both
        a = args[i].strip("\"'")
        if a == "-f" and i + 1 < len(args):
            roots.append(args[i + 1])
            i += 2
        elif a in _FIND_FLAGS or (bfs and _BFS_ATTACHED.match(a)):
            i += 1
        elif bfs and a in _BFS_FLAG_VALUES:
            i += 2
        else:
            break
    if not bfs:
        while i < len(args) and not args[i].strip("\"'").startswith(("-", "(", "!")):
            roots.append(args[i])
            i += 1
        return roots
    while i < len(args):
        a = args[i].strip("\"'")
        i += 1
        if a in _FIND_EXEC:  # the command runs up to `;`, or a `+` right after `{}`
            while (
                i < len(args)
                and args[i].strip("\"'\\") != ";"
                and not (args[i].strip("\"'") == "+" and args[i - 1].strip("\"'") == "{}")
            ):
                i += 1
            i += 1
        elif a == "-f" and i < len(args):
            roots.append(args[i])
            i += 1
        elif a in _BFS_VALUES or a in _BFS_FLAG_VALUES or _BFS_NEWER_XY.match(a):
            i += 1
        elif a == "-fprintf":
            i += 2
        elif a in ("-depth", "-exit") and i < len(args) and re.fullmatch(r"[-+]?\d+", args[i].strip("\"'")):
            i += 1
        elif not a.startswith("-") and a not in ("(", ")", "!", ",", "\\(", "\\)", "\\!"):
            roots.append(args[i - 1])
    return roots


def reads_folders(name: str, args: list[str]) -> bool:
    """Whether ``name args`` reads a folder among its operands: ``ls`` without ``-d``, ``tree``, ``find``, ``rg``…,
    ``grep -r``. ``cat dir``, ``head dir``, ``grep x dir`` only print an error."""
    if name == "ls":
        return not any(a == "--directory" or re.fullmatch(r"-[A-Za-z]*d[A-Za-z]*", a) for a in args)
    if name in _FOLDER_READERS:
        return True
    if name in ("grep", "egrep", "fgrep"):
        return _grep_recursive(name, args)
    return False


# The flags whose value is a file the reader opens: a pattern file, a script, a file list.
_FILE_FLAGS: dict[str, set[str]] = {
    **dict.fromkeys(("grep", "egrep", "fgrep", "rg"), {"-f", "--file"}),
    "ugrep": {"-f", "--file", "--from"},  # `--from=list`: the files to search, one per line
    "sed": {"-f", "--file"},
    **dict.fromkeys(("awk", "gawk", "mawk"), {"-f", "--file"}),
    "jq": {"-f", "--from-file"},
    "ack": {"--files-from"},
}


def _program_given(name: str, args: list[str]) -> bool:
    """A flag gives the pattern or program (``grep -e x``, ``sed -ne p``, ``rg --regexp=x``), so every
    positional is a file. Read with :func:`_scan`, so a cluster ending in ``-e`` counts too."""
    given = _PROGRAM_FIRST.get(name, set())
    return any(flag in given for flag, _value in _options(name, args))


def read_operands(name: str, args: list[str]) -> list[str] | None:
    """The files a reading command (:data:`_READ_COMMANDS`) reads, as written: its positionals without its
    flags' values, a pattern or program it takes first (``grep x f``, ``sed 1p f``, ``awk '{…}' f``, ``jq . f``),
    or ``find``'s expression (its roots only). None for a call that reads no file (``--version``, ``du -s``)."""
    if _not_a_read(name, args) or (name == "du" and _du_summary(args)):
        return None
    if name in ("find", "bfs"):
        files0 = [args[i + 1] for i, a in enumerate(args[:-1]) if name == "bfs" and a.strip("\"'") == _FILES0]
        return [*_find_roots(args, name == "bfs"), *files0]
    if name == "sed":  # BSD `sed -i '' …` / `sed -i .bak …`: the backup suffix is -i's own word
        args = [a for i, a in enumerate(args) if not (i and args[i - 1] == "-i" and (not a or a.startswith(".")))]
    _flags, plain = _split_args(_operands(name, args))
    if name in _PROGRAM_FIRST and plain and not _program_given(name, args):
        plain = plain[1:]
    given = [v for f, v in _options(name, args) if f in _FILE_FLAGS.get(name, ()) and v]  # `grep -f pats f`
    return [p for p in [*given, *plain] if p and p != "-"]


# The search tools' flags that make them print file paths: names that match, or that hold a match.
_LISTS_FILES = {
    "ag": {"-g", "-l", "-L", "--files-with-matches", "--files-without-matches"},
    "ack": {"-f", "-g", "-l", "-L", "--files-with-matches", "--files-without-matches"},
    "ugrep": {"-l", "-L", "--files-with-matches", "--files-without-match"},
}


def listed_roots(name: str, args: list[str]) -> list[str] | None:
    """For ``fd``, and ``ag``/``ack``/``ugrep`` when they print file paths (``ag -g``, ``ack -f``,
    ``ugrep -l``): the folders whose file paths they print, for ``… | xargs cat`` to read. None for any other
    call. Their other names (``fdfind``, ``ack-grep``, ``ug``) count too. [] when a file filter keeps every
    KB note out of the listing (``fd -e py``)."""
    name = _ALIASES.get(name, name)
    if name == "fd":
        if _fd_without_exec(args)[1] or _not_a_read(name, args):
            return None  # it prints what the command it runs prints
        rec = _recursive("fd", args)
        return [] if rec is None else rec.roots or ["."]
    if name not in _LISTS_FILES or not any(f in _LISTS_FILES[name] for f, _v in _options(name, args)):
        return None
    return read_operands(name, args) or ["."]


_BRACE_SEQUENCE = re.compile(r"^(-?\d+|[A-Za-z])\.\.(-?\d+|[A-Za-z])(?:\.\.(-?\d+))?$")


def brace_expand(word: str, limit: int = 64) -> list[str] | None:
    """``word`` after the shell's brace expansion: ``a{b,c}d`` is ``abd acd``, nested lists and sequences
    (``{1..3}``, ``{a..c}``, ``{1..9..4}``) included. ``${…}``, ``{}``, ``{a}`` and an unclosed or
    backslashed ``{`` stay as written. None when it makes more than ``limit`` words."""
    start = _brace_open(word)
    if start is None:
        return [word]
    end, commas = _brace_close(word, start)
    if end is None:
        return [word]
    body = word[start + 1 : end]
    alternatives: list[str] | None
    if commas:
        cuts = [start, *commas, end]
        alternatives = [word[a + 1 : b] for a, b in zip(cuts, cuts[1:], strict=False)]
    else:
        alternatives = _sequence(body, limit)
        if alternatives is None:
            return None
        if not alternatives:  # not a list: the brace is a plain character, look past it
            rest = brace_expand(word[start + 1 :], limit)
            return None if rest is None else [word[: start + 1] + r for r in rest]
    out: list[str] = []
    for alt in alternatives:
        more = brace_expand(word[:start] + alt + word[end + 1 :], limit)
        if more is None:
            return None
        out += more
        if len(out) > limit:
            return None
    return out


def _brace_open(word: str) -> int | None:
    i = 0
    while i < len(word):
        if word[i] == "\\":
            i += 2
            continue
        if word[i] == "{" and not (i and word[i - 1] == "$"):
            return i
        i += 1
    return None


def _brace_close(word: str, start: int) -> tuple[int | None, list[int]]:
    """Where the brace opened at ``start`` closes, and its top-level commas."""
    commas: list[int] = []
    depth, i = 0, start
    while i < len(word):
        c = word[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i, commas
        elif c == "," and depth == 1:
            commas.append(i)
        i += 1
    return None, []


def _sequence(body: str, limit: int) -> list[str] | None:
    """``1..3`` / ``a..c`` / ``1..9..4`` as its words; [] when ``body`` is no sequence; None above ``limit``."""
    m = _BRACE_SEQUENCE.match(body)
    if m is None:
        return []
    a, b, step = m[1], m[2], abs(int(m[3] or 1)) or 1
    if a.lstrip("-").isdigit() != b.lstrip("-").isdigit():
        return []
    lo, hi = (int(a), int(b)) if a.lstrip("-").isdigit() else (ord(a), ord(b))
    if abs(hi - lo) // step + 1 > limit:
        return None
    values = range(lo, hi + 1, step) if lo <= hi else range(lo, hi - 1, -step)
    return [str(v) if a.lstrip("-").isdigit() else chr(v) for v in values]


class _Recursive:
    """A recursive read: where it starts (``roots``; empty for the shell's folder), whether it
    reads file contents (``grep -r``, ``rg``, ``git grep``, ``find -exec``) or only names
    (``find``, ``ls -R``, ``tree``, ``du``), how deep it goes, and the folder names it excludes."""

    def __init__(self, roots: list[str], content: bool, depth: int | None = None, excluded: tuple[str, ...] = ()):
        self.roots, self.content, self.depth, self.excluded = roots, content, depth, excluded
        self.follows = False  # it follows symlinks below its roots (`find -L`, `grep -R`, `rg -L`)


# The flags that make a recursive read follow symlinks below its roots, per tool (`-H`-style "the roots only"
# needs none: a root is judged with its links resolved anyway).
_FOLLOWS = {
    **dict.fromkeys(("grep", "egrep", "fgrep"), {"-R", "-S", "--dereference-recursive"}),
    "ugrep": {"-R", "-S", "--dereference-recursive", "--dereference-files"},
    "rg": {"-L", "--follow"},
    "fd": {"-L", "--follow"},
    "ag": {"-f", "--follow"},
    "ack": {"--follow"},
    "tree": {"-l"},
    "du": {"-L"},
    "ls": {"-L"},
}


def _follows_links(name: str, args: list[str]) -> bool:
    if name in ("find", "bfs"):
        return any(a.strip("\"'") in ("-L", "-follow") for a in args)
    flags = _FOLLOWS.get(name, set())
    return any(
        f in flags or (f in ("-d", "--directories") and v == "dereference-recurse") for f, v in _options(name, args)
    )


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _flag_value(args: list[str], names: set[str]) -> str | None:
    """The value of the last of ``names`` given, separate (``-L 2``) or attached (``--max-depth=2``)."""
    found = None
    for i, a in enumerate(args):
        if a in names and i + 1 < len(args):
            found = args[i + 1]
        elif "=" in a and a.split("=", 1)[0] in names:
            found = a.split("=", 1)[1]
    return found


def _exclusions(args: list[str], name: str = "rg") -> tuple[str, ...]:
    """What ``--exclude-dir=X`` / ``-g '!X/**'`` keep out, as the text of each exclusion. grep has no ``-g``.
    ugrep's ``-g`` is a list, negated by ``!`` or ``^``, and keeps a folder out only when the entry ends in ``/``
    (``-g '!context/'``; ``-g '!context'`` skips files named so and still searches the folder)."""
    out = []
    for i, a in enumerate(args):
        value = None
        if a in ("--exclude-dir", "-g", "--glob", "--iglob") and i + 1 < len(args):
            value = args[i + 1]
        elif a.startswith(("--exclude-dir=", "--glob=", "--iglob=")):
            value = a.split("=", 1)[1]
        if value is None:
            continue
        value = value.strip("\"'")
        if a.startswith("--exclude-dir"):
            out.append(value)
        elif name == "ugrep":
            out += [e[1:-1] for e in _split_list(value) if e.startswith(("!", "^")) and e.endswith("/")]
        elif name not in ("grep", "egrep", "fgrep") and value.startswith("!"):
            out.append(value[1:])
    return tuple(out)


def _recursive(name: str, args: list[str]) -> _Recursive | None:
    """Where a recursive read starts, or None for a command that isn't one, or one whose file
    filter stays out of the KB (``--glob 'action-items/*.md'``, ``--include=*.py``), as for the
    Grep tool. ``follows`` says whether it follows the symlinks below its roots."""
    rec = _recursive_read(name, args)
    if rec is not None:
        rec.follows = _follows_links(name, args)
    return rec


def _recursive_read(name: str, args: list[str]) -> _Recursive | None:
    if _not_a_read(name, args):
        return None
    runs = False  # fd -x/-X: it runs a command on each file it finds
    if name == "fd":
        args, runs = _fd_without_exec(args)
    flags, plain = _split_args(_operands(name, args) if name not in ("find", "bfs") else args)
    filters: list[str] = []
    if name in ("grep", "egrep", "fgrep", "ugrep"):
        if name == "ugrep" and any(f == "--from" for f, _v in _options(name, args)):
            return _Recursive([_UNKNOWN], True)  # `--from=list`: the files to search are in a file, or stdin
        if not _grep_recursive(name, args) or (name == "ugrep" and _ugrep_dir_bound(name, args)):
            return None
        pattern_given = _program_given(name, args)
        roots = plain if pattern_given else plain[1:]
        filters = [a.split("=", 1)[1] for a in args if a.startswith("--include=")] + _flag_values(args, {"--include"})
        types: list[str] = []
        if name == "ugrep":  # ugrep also filters with `-g GLOBS`, `-O EXTS` and `-t TYPES`
            filters = [e for e in _ugrep_includes(name, args, _UGREP_GLOBS) if not e.endswith("/")]  # `x/`: folders
            filters += ["*." + e.lstrip(".") for e in _ugrep_includes(name, args, {"-O", "--file-extension"})]
            types = _ugrep_includes(name, args, {"-t", "--file-type"})
        rec = _Recursive(roots, True, None, _exclusions(args, name))
        return _either_filter(rec, filters, types, prefix=name == "ugrep")
    if name in ("ag", "ack"):  # recursive by default; the first positional is the pattern
        lists = "-g" in args or (name == "ack" and "-f" in args)  # file names only, no pattern
        pattern_given = name == "ack" and any(a == "--match" or a.startswith("--match=") for a in args)
        rec = _Recursive(plain if lists or pattern_given else plain[1:], not lists)
        if name == "ack" and not any(f in ("--type-set", "--type-add") for f, _v in _options(name, args)):
            # `-T TYPE` and `--type=noTYPE` exclude a type: they widen the search, never bound it.
            types = [t for t in _values(name, args, {"-t", "--type"}) if not t.lower().startswith("no")]
            return _either_filter(rec, [], types)
        return rec
    if name == "fd":  # `fd [pattern] [path...]`: names, or contents when it runs a command on them
        paths = plain[1:] + _values(name, args, {"--search-path"})
        base = _values(name, args, {"-C", "--base-directory"})
        if base:  # the paths are under the base directory, which is the search itself when none is given
            paths = [p if p.startswith(("/", "~", "$")) else posixpath.join(base[-1], p) for p in paths] or base[-1:]
        globs = ["*." + e.lstrip(".") for e in _values(name, args, {"-e", "--extension"})]
        depth = _int(next(iter(_values(name, args, {"-d", "--max-depth"})[-1:]), None))
        return _filtered(_Recursive(paths, runs, depth), globs)
    if name == "rg":
        if "--files" in args:  # lists the files it would search: names, no pattern
            return _Recursive(plain, False, _int(_flag_value(args, {"--max-depth", "--maxdepth", "-d"})))
        pattern_given = _program_given(name, args)
        roots = plain if pattern_given else plain[1:]
        globs = _flag_values(args, {"-g", "--glob", "--iglob"})
        globs += [a.split("=", 1)[1] for a in args if a.startswith(("--glob=", "--iglob="))]
        filters = [g for g in globs if not g.strip("\"'").startswith("!")]
        depth = _int(_flag_value(args, {"--max-depth", "--maxdepth", "-d"}))
        types = _flag_values(args, {"-t", "--type"}) + [a.split("=", 1)[1] for a in args if a.startswith("--type=")]
        types += [a[2:] for a in args if re.match(r"^-t[A-Za-z0-9]", a)]  # attached: `-tpy`
        if any(a == "--type-add" or a.startswith("--type-add=") for a in args):
            types = []  # a type defined in the command itself: it can name anything
        return _filtered(_Recursive(roots, True, depth, _exclusions(args)), filters, types)
    if name in ("find", "bfs"):
        content = any(a.strip("\"'") in _FIND_EXEC for a in args)
        if name == "bfs" and any(a.strip("\"'") == _FILES0 for a in args):
            return _Recursive([_UNKNOWN], True)  # the paths are in a file, or stdin: the gate can't place them
        return _Recursive(_find_roots(args, name == "bfs"), content, _int(_flag_value(args, {"-maxdepth"})))
    if name == "ls":
        if not any(re.match(r"^-[A-Za-z]*R", f) or f == "--recursive" for f in flags):
            return None
        return _Recursive(plain, False)
    if name == "du":
        if any(re.match(r"^-[A-Za-z]*s", f) or f == "--summarize" for f in flags):
            return None
        return _Recursive(plain, False, _int(_flag_value(args, {"-d", "--max-depth"})))
    if name == "tree":
        return _Recursive(plain, False, _int(_flag_value(args, {"-L"})))
    return None


def _fd_without_exec(args: list[str]) -> tuple[list[str], bool]:
    """fd's own words, and whether it runs a command: ``-x``/``-X`` take the words after them up to a
    ``;``, and fd reads options and paths again after it (``fd x scripts -X cat ';' .``)."""
    out: list[str] = []
    runs = skipping = False
    for a in args:
        if skipping:
            skipping = a not in (";", "\\;")
        elif _fd_exec_starts(a):
            runs = skipping = True
        else:
            out.append(a)
    return out, runs


def _fd_exec_starts(word: str) -> bool:
    """``word`` starts fd's exec command: ``-x``, ``--exec=cat``, or a cluster reaching ``x``/``X`` before
    any flag that takes a value (``-Hx``, ``-xcat``, ``-x=cat``, ``-HX``; ``-ex`` is ``-e x``)."""
    if word in _FD_EXEC or word.startswith(("--exec=", "--exec-batch=")):
        return True
    if not word.startswith("-") or word.startswith("--"):
        return False
    for ch in word[1:]:
        if ch in "xX":
            return True
        if not ch.isalnum() or "-" + ch in _FD_VALUES:
            return False
    return False


def _kb_filter(glob: str) -> bool:
    """A file filter that may let a KB file through."""
    return _names_kb(glob) or glob.strip("\"'") in WHOLE_VAULT_GLOBS or _may_match_kb(glob)


def _either_filter(rec: _Recursive, filters: list[str], types: list[str], prefix: bool = False) -> _Recursive | None:
    """For ugrep and ack, where a file passes if it matches any one of the filters (a glob, an
    extension or a ``-t`` type): None when there are filters and none of them can match a KB file.
    ugrep takes a type by its first letters (``-t mark`` is markdown): ``prefix`` matches that way."""
    if not filters and not types:
        return rec

    def kb_type(t: str) -> bool:
        t = t.lower().rstrip("+")
        return any(k.startswith(t) for k in _KB_TOOL_TYPES) if prefix else t in _KB_TOOL_TYPES

    if any(_kb_filter(f) for f in filters) or any(kb_type(t) for t in types):
        return rec
    return None


def _ugrep_includes(name: str, args: list[str], flags: set[str]) -> list[str]:
    """The entries of ugrep's comma-separated ``flags`` lists that include files. An entry that starts
    with ``!`` or ``^`` excludes instead (``-O '^py'``, ``-g '^*.py'``), so it never bounds a search."""
    entries = [e for v in _values(name, args, flags) for e in _split_list(v)]
    return [e for e in entries if not e.startswith(("!", "^"))]


_GREP_RECURSE = {"-r", "-R", "--recursive", "--dereference-recursive"}


_UGREP_GLOBS = {"-g", "--glob", "--iglob", "--include"}


def _grep_recursive(name: str, args: list[str]) -> bool:
    """grep's recursion, read with :func:`_scan` so a cluster counts (``-rnA3``, ``-rC2``, ``-r3``):
    ``-r``/``-R``, ``-d recurse``. For ugrep also ``-NUM`` (``-3-5``, ``-1,3``), ``--depth``, ``--index``,
    ``--include-dir``, and an include glob holding a ``/`` (``-g '**/*.md'``; one ending in ``/`` names folders)."""
    for flag, value in _options(name, args):
        if flag in _GREP_RECURSE or (flag in ("-d", "--directories") and value == "recurse"):
            return True
        if name != "ugrep":
            continue
        if flag in ("--depth", "--index", "--include-dir") or re.fullmatch(r"-\d", flag):
            return True
        if flag in _UGREP_GLOBS and value is not None:
            entries = _split_list(value.strip("\"'"))
            if any("/" in e and not e.startswith(("!", "^")) for e in entries):
                return True
    return False


def _ugrep_dir_bound(name: str, args: list[str]) -> bool:
    """ugrep's ``--include-dir`` (and an include glob ending in ``/``) enters only the folders it names: True
    when none it names can be a KB folder, so the search never gets into the KB. A glob that names a path
    (``docs/sub/``) bounds nothing here."""
    globs = [v.strip("\"'") for v in _values(name, args, {"--include-dir"})]
    globs += [e[:-1] for e in _ugrep_includes(name, args, _UGREP_GLOBS) if e.endswith("/")]
    if not globs or any("/" in g for g in globs):
        return False

    def can(folder: str) -> bool:  # folded, as --iglob and a case-insensitive disk match: more matches bound less
        return any(fnmatch.fnmatchcase(fold(folder), fold(g)) for g in globs)

    return not (can("knowledge-base") or can("knowledge_base") or (can("action-items") and can("context")))


def _filtered(rec: _Recursive, filters: list[str], types: list[str] | None = None) -> _Recursive | None:
    """None when a file filter keeps the search out of the KB, root or no root: ``--include=*.py``,
    ``-g '*.py'``, ``-g 'docs/*.md'``, ``rg -t py``. A filter bounds the search only if it can't
    match a KB file (``*people*``, ``*.m?``, ``*.{md,txt}``, ``*.MD`` and ``*.yaml`` all can).
    ripgrep ignores ``-t`` when an include glob is given (``rg -t py -g '*.md'`` searches notes), so
    the types bound the search only when there is no include glob."""
    if filters and not any(_kb_filter(f) for f in filters):
        return None
    if types and not filters and not any(t.strip("\"'") in _KB_RG_TYPES for t in types):
        return None
    return rec


def _glob_regex(glob: str) -> re.Pattern[str]:
    """A ripgrep-style glob as a regex, ignoring case: ``**`` crosses folders, ``*`` and ``?`` don't,
    ``{a,b}`` is either."""
    out, i, depth = [], 0, 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
            if glob.startswith("/", i):
                i += 1
                out.append("/?")
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            end = glob.find("]", i + 1)
            if end < 0:
                out.append(re.escape(c))
            else:
                body = glob[i + 1 : end].replace("\\", "\\\\")
                out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
                i = end
        elif c == "{":
            depth += 1
            out.append("(?:")
        elif c == "}" and depth:
            depth -= 1
            out.append(")")
        elif c == "," and depth:
            out.append("|")
        else:
            out.append(re.escape(c))
        i += 1
    try:
        return re.compile("".join(out) + ")" * depth, re.IGNORECASE)
    except re.error:
        return re.compile(".*")  # a glob the gate can't read bounds nothing


def _may_match_kb(glob: str) -> bool:
    """Whether a file filter could match a KB file: tried on file names built from its own words
    (``*people*`` → ``people.md``), in each format the KB keeps (:data:`KB_FILE_EXTENSIONS`), under
    each KB folder; by file name for a glob with no ``/``."""
    g = glob.strip("\"'").removeprefix("!")
    words = [w for w in re.findall(r"[A-Za-z0-9_-]+", g) if w.lower() not in KB_FILE_EXTENSIONS]
    stems = {"x", *(s for w in words for s in (w, f"{w}x", f"x{w}", f"x{w}x"))}
    names = {f"{stem}.{ext}" for stem in stems for ext in KB_FILE_EXTENSIONS}
    rx = _glob_regex(g)
    if "/" not in g:
        return any(rx.fullmatch(n) for n in names)
    paths = (f"{kb}/{sub}{n}" for kb in KB_DIRS for sub in ("", "x/", "projects/x/") for n in names)
    return any(rx.fullmatch(p) for p in paths)


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


def _flag_values(args: list[str], takes_value: set[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a in takes_value]


_UNKNOWN = "\0unknown"


def _expand(word: str, env: Mapping[str, str], local: Mapping[str, str]) -> str:
    """``word`` with quotes gone and ``~``, ``$HOME``, ``$VAR`` put in from the command's own
    assignments or the run's environment; :data:`_UNKNOWN` when part of it can't be known."""
    word = word.strip("\"'")
    if word == "~" or word.startswith("~/"):
        home = env.get("HOME")
        if not home:
            return _UNKNOWN
        word = home + word[1:]

    def put(m: re.Match[str]) -> str:
        name = m.group(1) or m.group(2)
        return local.get(name) or env.get(name) or _UNKNOWN

    word = re.sub(r"\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)", put, word)
    return _UNKNOWN if _UNKNOWN in word or "$" in word or "`" in word else word


def _root_folder(word: str) -> str:
    """A root argument as a folder: a glob stands for the folder it expands in (``*`` → ``.``)."""
    parts = word.split("/")
    for i, part in enumerate(parts):
        if any(c in part for c in "*?["):
            return "/".join(parts[:i]) or ("/" if word.startswith("/") else ".")
    return word


def _cd(
    cwd: str | None, target: str, env: Mapping[str, str] | None = None, local: Mapping[str, str] | None = None
) -> str | None:
    """The shell's folder after ``cd target`` from ``cwd``; None when the gate can't tell."""
    target = _expand(target, env or {}, local or {})
    if target == _UNKNOWN or not target or target == "-" or "\x01" in target:  # \x01: a substitution's output
        return None
    if target.startswith("/"):
        return posixpath.normpath(target)
    return None if cwd is None else posixpath.normpath(posixpath.join(cwd, target))


def _spelling(name: str) -> str:
    return name.replace("_", "-")


def _reaches_kb(folder: str, vault: str, rec: _Recursive) -> bool:
    """Whether a recursive read rooted at ``folder`` (absolute) gets to a KB note, as the Grep
    tool's rule has it (``_covers_kb``): rooted at the vault or above, at a KB folder's parent
    (``action-items`` holds ``context/``), or inside the KB. A read of names only counts from
    the vault or above, or inside the KB."""
    folder = posixpath.normpath(folder)
    for kb_dir in KB_DIRS:
        target = posixpath.join(vault, kb_dir)
        # `knowledge-base` and `knowledge_base` are two spellings of one folder: excluding either excludes both.
        names = {_spelling(kb_dir), _spelling(posixpath.basename(kb_dir))}
        if any(ex and _spelling(ex.rstrip("/*")) in names for ex in rec.excluded):
            continue
        if folder == target or folder.startswith(target + "/"):
            return rec.depth is None or rec.depth >= 1
        if not (target.startswith(folder.rstrip("/") + "/")):
            continue
        at_or_above_vault = vault == folder or vault.startswith(folder.rstrip("/") + "/")
        if not rec.content and not at_or_above_vault:
            continue  # a name listing from a KB folder's parent (`find action-items`)
        # The note files sit one level below the KB folder.
        below = len(posixpath.relpath(target, folder).split("/")) + 1
        if rec.depth is None or rec.depth >= below:
            return True
    return False


_STATEMENTS = re.compile(r"&&|\|\||;|\n|(?<![<>&|])&(?![>&])")
_PIPE = re.compile(r"(?<!\|)\|(?!\|)")
_GIT_GLOBAL_VALUES = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}
_GIT_READS = {"show", "diff", "grep", "blame", "annotate", "cat-file", "whatchanged", "difftool"}


def _git(args: list[str]) -> tuple[str | None, list[str], list[str]]:
    """``(subcommand, its args, -C folders)`` of a ``git`` call, past git's own options
    (``--no-pager``, ``-P``, ``-c k=v``, ``-C dir``, ``--git-dir=…``). The subcommand is folded to lower case:
    git itself refuses ``git SHOW``, so a gate that reads it as ``show`` only ever over-denies."""
    chdirs: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in _GIT_GLOBAL_VALUES and i + 1 < len(args):
            if a == "-C":
                chdirs.append(args[i + 1])
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            return fold(a), args[i + 1 :], chdirs
    return None, [], chdirs


def _git_reads(sub: str | None, args: list[str]) -> bool:
    if sub in _GIT_READS:
        return True
    return sub == "log" and any(a in ("-p", "-u", "--patch") or a.startswith("--patch") for a in args)


def raw_kb_read(
    command: str,
    *,
    in_kb: bool = False,
    cwd: str | None = None,
    vault: str | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Does this shell command read ``knowledge-base/`` or ``action-items/context/`` around ``scoutctl kb``?

    Each shell segment is judged by its command word, after prefixes (``xargs``, ``sudo``,
    ``timeout -s KILL 10``), assignments and shell keywords (``do``, ``then``): a reading command
    (``/bin/cat`` too) with a KB path among its operands, quoted or not, or run after ``cd``,
    ``pushd`` or ``env -C`` into the KB (``in_kb``: the shell starts there); one that reads a
    variable the command filled with a KB path or with ``scoutctl kb``'s output (``f=knowledge-base/x;
    wc -c $f``, ``for f in $(scoutctl kb files); do wc -c "$f"``), or a command substitution that
    runs it (``cat $(scoutctl kb files)``); one ``xargs`` feeds from a pipe that carries KB paths;
    ``git show|diff|grep|blame|log -p`` with a KB path, past git's own options; an input
    redirection from a KB path (``wc -c <knowledge-base/x``); ``sh -c``/``eval`` of a program
    that does any of this. With ``cwd`` and ``vault`` (absolute, as the gate knows them), a
    recursive read that gets to a KB note (``rg x``, ``grep -r x action-items``, ``git grep x``,
    ``find .``) counts too; ``env`` puts in ``~`` and ``$VAR``. ``scoutctl …`` segments and
    quoted commit messages never count.

    Known gaps, none of which main denied: ``xargs`` fed by a command that prints no KB path
    (``ls | xargs rg x`` from the vault); ``cd action-items && cat context/x.md`` (the shell
    pre-filter sees no KB word); and a vault reached through a symlink, spelled with ``~``.
    """
    command = _MESSAGE_ARG.sub(r"\1 ''", command)
    env = env or {}
    if _judge(command, in_kb, cwd, vault, env):
        return True
    expanded = _with_kb_paths(command)
    if expanded != command and _judge(expanded, in_kb, cwd, vault, env):
        return True
    return _judge_recursive(command, in_kb, cwd, vault, env)


def _judge(command: str, in_kb: bool, cwd: str | None, vault: str | None, env: Mapping[str, str]) -> bool:
    """Every rule but the recursive-read one, on main's split (which also splits inside quotes, so
    every command main denied is still denied)."""
    tainted = _kb_variables(command)
    local = _local_values(command)
    here = posixpath.normpath(cwd) if cwd else None
    for statement in _STATEMENTS.split(command):
        upstream_kb = False
        for piece in _PIPE.split(statement):
            for segment in _SEGMENTS.split(piece):
                raw = segment.split()
                if raw and raw[0].rsplit("/", 1)[-1] == "scoutctl":
                    continue
                if _KB_REDIRECT.search(segment):
                    return True
                stripped = _strip(raw)
                words = stripped.words
                if not words:
                    continue
                name, args = _command_name(words[0]), words[1:]
                rest = " ".join(args)
                if name == "scoutctl":
                    continue
                seg_in_kb = in_kb if stripped.chdir is None else _names_kb(stripped.chdir)
                if name in ("cd", "pushd"):
                    in_kb = _names_kb(rest)
                    here = _cd(here, args[0], env, local) if args else None
                elif name in _READ_COMMANDS:
                    if name == "du" and _du_summary(args):
                        continue  # one total per folder named: no note's name or text
                    models = run_models(words[0], name, args)
                    if seg_in_kb or _names_kb(rest) or any(_uses_operand(m, a, tainted) for m, a in models):
                        return True
                    if any(_glob_names_kb(a) for m, m_args in models for a in _operands(m, m_args)):
                        return True
                    if stripped.fed and upstream_kb:
                        return True
                elif name == "git":
                    sub, sub_args, _chdirs = _git(args)
                    names_kb = _names_kb(rest) or _uses(rest, tainted)
                    if names_kb and (_GIT_READ.search(rest) or _GIT_READ.search(_unquoted(rest))):
                        return True
                    if names_kb and _git_reads(sub, sub_args):
                        return True
            upstream_kb = upstream_kb or bool(_KB_SOURCE.search(piece))
    return False


def _command_name(word: str) -> str:
    """A command word's program: quotes and backslashes a shell removes (`bash -c 'cat …'`, `\rg`, `NO''HUP`)
    gone, its folder (`/usr/bin/grep`) dropped, its letters folded to lower case (:func:`program_name`), and an
    alias (`ug`, `fdfind`, `ack-grep`) read as the program it names."""
    name = program_name(_unquoted(word).rsplit("/", 1)[-1])
    return _ALIASES.get(name, name)


# Builtins and keywords a shell knows only as spelled. `EXEC rg x` is "command not found", and `CD x` runs
# /usr/bin/cd in a child shell, which leaves this one where it was: neither is the builtin.
SHELL_ONLY = frozenset({
    "cd", "pushd", "popd", "exec", "eval", "builtin", "source", ".", "export", "unset", "local", "declare",
    "typeset", "readonly", "read", "mapfile", "readarray", "set", "shift", "return", "exit", "trap", "alias",
    "unalias", "let", "shopt", "setopt", "unsetopt", "disown", "for", "select", "while", "until", "if", "then",
    "elif", "else", "fi", "do", "done", "case", "esac", "function",
})  # fmt: skip


def program_name(name: str) -> str:
    """The program a command name runs, in lower case. On a case-insensitive disk (macOS's default) the shell
    finds a program in any letter case: `FIND`, `Rg`, `NOHUP` and `XARGS` run find, rg, nohup and xargs. So a
    gate compares program names folded, always (an odd-cased name in a run may be over-denied, never let
    through). A builtin or keyword in :data:`SHELL_ONLY` keeps its spelling. `command` and `time` are not
    there: /usr/bin/command and /usr/bin/time run the word after them, as the builtins do."""
    folded = fold(name)
    return name if folded in SHELL_ONLY else folded


def fold(text: str) -> str:
    """``text`` as a case-insensitive disk compares a name: Unicode-normalised and fully case-folded. APFS folds
    with full Unicode case folding, so ``ﬁnd`` (the ﬁ ligature), ``xargſ`` and ``ſh`` (long s) and ``ßh`` run
    find, xargs, sh and ssh, which ``str.lower()`` leaves alone. NFKC before and after ``casefold()`` also makes
    an NFD spelling and a compatibility form (``ｆｉｎｄ``) read the same; one that folds where the disk wouldn't
    is only over-denied."""
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", text).casefold())


def _du_summary(args: list[str]) -> bool:
    flags = _split_args(args)[0]
    return any(re.match(r"^-[A-Za-z]*s", f) or f == "--summarize" for f in flags) and not any(
        re.match(r"^-[A-Za-z]*a", f) or f == "--all" for f in flags
    )


_GLOB_CHARS = re.compile(r"[*?\[]")


def _glob_names_kb(word: str) -> bool:
    """A glob that spells a KB folder (``know*/people.md``, ``action-items/con*/x``): it matches the
    folder's name, and a run of three or more of its plain letters comes from that name. ``*/x``,
    ``*.md`` and ``action-items/*.md`` spell nothing in particular."""
    parts = _unquoted(word).split("/")
    for kb in KB_DIRS:
        want = kb.split("/")
        for i in range(len(parts) - len(want) + 1):
            got = parts[i : i + len(want)]
            globbed = [(g, w) for g, w in zip(got, want, strict=True) if _GLOB_CHARS.search(g)]
            if not globbed or not all(fnmatch.fnmatchcase(w, g) for g, w in zip(got, want, strict=True)):
                continue
            if any(_stem_letters(g, w) for g, w in globbed):
                return True
    return False


def _stem_letters(glob: str, name: str) -> bool:
    """A run of 3+ plain characters in ``glob`` that is part of ``name`` (``know*`` → ``knowledge-base``)."""
    return any(len(run) >= 3 and run.lower() in name.lower() for run in re.split(r"[*?\[\]]+", glob))


# ----- the recursive-read rule, on a split that keeps quotes whole ---------------------------------

_SUBST = "\x01"  # marks a command substitution's output (`\x01<n>\x01`): files the gate can't see
_SUBST_INDEX = re.compile("\x01(\\d+)\x01")


def _restored(text: str, substitutions: list[str]) -> str:
    """``text`` with each :data:`_SUBST` marker put back as the substitution it stands for, so a program judged
    again on its own (``bash -c "cat $(find .)"``) splits its own substitutions: a marker indexes only the list
    of the split that made it. A marker that list doesn't hold (the command spelled one itself) stays as it is."""

    def put(m: re.Match[str]) -> str:
        i = int(m.group(1))
        return f"$({substitutions[i]})" if i < len(substitutions) else m.group(0)

    return _SUBST_INDEX.sub(put, text)


_SUBSHELL_OPEN, _SUBSHELL_CLOSE = "\x02(", "\x02)"  # a `(` and a `)` of the command, each a statement of its own


def _shell_split(command: str) -> tuple[list[list[str]], list[str]]:
    """``(statements, substitutions)``: each statement a list of pipe pieces, split only where the
    shell splits (``rg -n "TODO|FIXME" scripts/`` is one piece). Each outermost ``$(…)`` or
    backtick, outside single quotes, is cut out and stands as :data:`_SUBST` in its piece. Each ``(``
    and ``)`` is a statement holding only :data:`_SUBSHELL_OPEN` or :data:`_SUBSHELL_CLOSE`."""
    statements, _joins, substitutions = _shell_split_joined(command)
    return statements, substitutions


# How a statement is joined to the one before it. A run of empty statements (`a &&\n b`, `a && (b)`) passes on
# the strongest join in it.
_JOIN_RANK = {"": 0, "(": 1, ")": 1, ";": 2, "&": 3, "&&": 4, "||": 5}


def _shell_split_joined(command: str) -> tuple[list[list[str]], list[str], list[str]]:
    """:func:`_shell_split`'s statements, each with the operator that joins it to the statement before it
    (``""`` first, ``;`` for a newline too, ``&&``, ``||``, ``&``; ``(`` for a subshell's first statement and
    ``)`` for the one after it), and the substitutions."""
    statements: list[list[str]] = [[]]
    joins: list[str] = [""]
    substitutions: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(command)

    def piece() -> None:
        statements[-1].append("".join(buf))
        buf.clear()

    def statement(join: str = ";") -> None:
        piece()
        statements.append([])
        joins.append(join)

    while i < n:
        c = command[i]
        if quote == "'":
            buf.append(c)
            quote = None if c == "'" else quote
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(command[i : i + 2])
            i += 2
            continue
        if command.startswith("$(", i) or c == "`":
            end = _substitution_end(command, i)
            substitutions.append(command[i + (1 if c == "`" else 2) : end - 1])
            buf.append(f"{_SUBST}{len(substitutions) - 1}{_SUBST}")
            i = end
            continue
        if quote == '"':
            buf.append(c)
            quote = None if c == '"' else quote
            i += 1
            continue
        if c in "'\"":
            quote = c
            buf.append(c)
        elif command.startswith(("&&", "||"), i):
            statement(command[i : i + 2])
            i += 1
        elif c in "()":  # a subshell: a statement of its own, between markers
            statement("")
            statements[-1].append(_SUBSHELL_OPEN if c == "(" else _SUBSHELL_CLOSE)
            statements.append([])
            joins.append(c)
        elif c in ";\n":
            statement(";")
        elif c == "&" and not (buf and buf[-1].endswith((">", "<"))) and not command.startswith((">", "&"), i + 1):
            statement("&")
        elif c == "|":
            piece()
        else:
            buf.append(c)
        i += 1
    piece()
    kept: list[list[str]] = []
    kept_joins: list[str] = []
    carried = ""
    for pieces, join in zip(statements, joins, strict=True):
        carried = join if _JOIN_RANK[join] > _JOIN_RANK[carried] else carried
        pieces = [p for p in pieces if p.strip()]
        if pieces:
            kept.append(pieces)
            kept_joins.append(carried)
            carried = ""
    return kept, kept_joins, substitutions


def _substitution_end(command: str, start: int) -> int:
    """The index just past the ``$(…)`` or backtick that starts at ``start``."""
    if command[start] == "`":
        end = command.find("`", start + 1)
        return len(command) if end < 0 else end + 1
    depth, j, quote = 1, start + 2, None
    while j < len(command) and depth:
        c = command[j]
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif command.startswith("$(", j):
            depth += 1
            j += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        j += 1
    return j


_REDIRECT = re.compile(r"^\d*(?:>>?|<|&>>?|>&|<&)")


def _shell_words(piece: str) -> list[str]:
    """A piece's words as the shell reads them, quotes removed, redirections dropped."""
    try:
        words = shlex.split(piece, posix=True)
    except ValueError:
        words = piece.split()
    out: list[str] = []
    skip = False
    for w in words:
        if skip:
            skip = False
        elif _REDIRECT.match(w):
            skip = _REDIRECT.sub("", w) == ""  # a bare `>`: its target is the next word
        else:
            out.append(w)
    return out


def _loop_words(command: str) -> dict[str, list[str]]:
    """Each ``for x in …`` loop variable and the words it takes (``for f in scripts/*.py``)."""
    out: dict[str, list[str]] = {}
    for m in _FOR.finditer(command):
        out[m.group(1)] = _shell_words(m.group(2).split(";")[0])
    return out


_LONE_VAR = re.compile(r"^\$\{?([A-Za-z_]\w*)\}?$")


_BLOCK_OPEN = {"if", "while", "until", "for", "case", "select"}
_BLOCK_CLOSE = {"fi", "done", "esac", "}"}


def _union(*lists: list[str | None]) -> list[str | None]:
    return list(dict.fromkeys(f for one in lists for f in one))


def _judged_piece(
    name: str,
    words: list[str],
    args: list[str],
    stripped: _Stripped,
    where: str | None,
    reaches: Callable[[_Recursive, str | None], bool],
    read: bool,
    env: Mapping[str, str],
    local: Mapping[str, str],
) -> bool:
    """The recursive-read rule for one command run from ``where``: a reading command's recursive read (each way a
    run's shell may run it), a bare grep's substitution operands (ugrep reads a folder it's given one level deep:
    ``grep -l x $(ls)``), or ``git grep``."""
    if name in _READ_COMMANDS:
        for model, model_args in run_models(words[0], name, args):
            rec = _recursive(model, model_args)
            if rec is None and model == "ugrep" and not stripped.fed:
                # ugrep reads a folder it's given one level deep: each operand, judged from every folder the
                # shell may be in (`cd nosuchdir; grep TODO *`), a substitution's output and a glob included.
                # A variable's value is judged file by file where the run's shell fills it in (live_run).
                operands = [o for o in read_operands(model, model_args) or [] if "$" not in o.replace(_SUBST, "")]
                rec = _Recursive(operands, True, 1) if operands else None
            if rec is None or (stripped.fed and not rec.roots):
                continue  # not recursive, or xargs hands it the files to read
            if read:
                rec.content = True
            if reaches(rec, where):
                return True
    elif name == "git":
        sub, sub_args, chdirs = _git(args)
        if sub != "grep":
            return False
        for c in chdirs:
            where = _cd(where, c, env, local)
        _flags, plain = _split_args(_operands("grep", sub_args))
        paths = plain if any(a in ("-e", "-f") for a in sub_args) else plain[1:]
        if not (stripped.fed and not paths) and reaches(_Recursive(paths, True), where):
            return True
    return False


def _listed_by(inner: str) -> list[tuple[str, int | None]] | None:
    """What a command prints when it prints paths the gate can place, each as ``(root, levels)``: the paths are
    ``root`` itself (``levels`` 0) or up to ``levels`` below it (None: any depth); a root may be a glob, which
    stands for what it matches. ``echo``/``printf`` print their words, ``pwd`` the folder, ``ls`` its operands'
    entries (``-d``: the operands; ``-R``: all below), ``find`` the paths under its roots, ``git ls-files`` the
    files under its pathspecs (all of them with none; a glob pathspec that can't match a KB note lists none of
    it). None for anything else: what it prints can't be known here, so it covers anything."""
    statements, _substitutions = _shell_split(inner)
    if len(statements) != 1 or len(statements[0]) != 1 or _SUBST in inner:
        return None
    words = _strip(_shell_words(statements[0][0])).words
    if not words:
        return None
    name, args = _command_name(words[0]), words[1:]
    if any("$" in w or "`" in w for w in args):
        return None  # a word the shell fills in (`$(echo $PWD)`): not one the gate can place
    if name in ("echo", "printf"):
        if name == "echo":
            while args and re.fullmatch(r"-[neE]+", args[0]):
                args = args[1:]
        elif args[:1] == ["--"]:
            args = args[1:]
        if name == "printf":  # `printf FORMAT ARGS`: the args, when the format only places them as words
            if not args:
                return None
            fmt, rest = args[0], args[1:]
            if not rest:
                printed = [fmt] if "%" not in fmt and "\\" not in fmt else None
            else:
                printed = rest if re.fullmatch(r"(?:%s|\\n|\\t| )+", fmt) else None
            if printed is None:
                return None
            args = printed
        # `~+`, `~-`, `~name` and a brace list `{a,b}` are rewritten by the shell: not placed here. A glob stands
        # for what it matches (`echo */*.md`).
        if any(w.startswith("~") or ("{" in w and ("," in w or ".." in w)) for w in args):
            return None
        return [(w, 0) for w in args]
    if name == "pwd":
        return [(".", 0)]
    if name == "ls":
        flags, plain = _split_args(args)
        letters = "".join(f[1:] for f in flags if not f.startswith("--"))
        levels = None if "R" in letters or "--recursive" in flags else 0 if "d" in letters else 1
        return [(w, levels) for w in plain or ["."]]
    if name == "find":
        depth = _int(_flag_value(args, {"-maxdepth"}))
        return [(w, depth) for w in _find_roots(args, bfs=True) or ["."]]
    if name == "git":
        sub, sub_args, chdirs = _git(args)
        if sub != "ls-files" or chdirs:
            return None  # `git -C dir ls-files`: paths relative to a folder this model doesn't follow
        return _git_ls_files(sub_args)
    return None


# git ls-files options that take the next word (an exclude pattern or file, a format): not a pathspec.
_LS_FILES_VALUES = {"-x", "--exclude", "-X", "--exclude-from", "--exclude-per-directory", "--format", "--abbrev"}


def _git_ls_files(args: list[str]) -> list[tuple[str, int | None]]:
    """The roots ``git ls-files args`` lists files under (tracked, untracked with ``-o``: either way the KB may be
    among them). A pathspec glob matches the whole path (git's ``*`` crosses ``/``): one that can't match a KB
    note lists none of it; a literal pathspec is a folder or file it lists under."""
    specs: list[str] = []
    i = 0
    ended = False
    while i < len(args):
        a = args[i].strip("\"'")
        i += 1
        if not ended and a == "--":
            ended = True
        elif not ended and a in _LS_FILES_VALUES:
            i += 1
        elif ended or not a.startswith("-"):
            specs.append(a)
    if not specs:
        return [(".", None)]
    out: list[tuple[str, int | None]] = []
    for spec in specs:
        if spec.startswith(":"):
            out.append((".", None))  # a magic pathspec (`:(glob)…`, `:!x`): not read here
        elif _GLOB_CHARS.search(spec):
            if _may_match_kb(re.sub(r"\*+", "**", spec)) or _names_kb(spec):
                out.append((".", None))
        else:
            out.append((spec, None))
    return out


def _glob_reaches_kb(path: str, vault: str, rec: _Recursive) -> bool:
    """A depth-bounded read of a glob root (``ls -d k*`` handed to a one-level read, ``cat $(echo */*.md)``): the
    glob's components are matched against each KB folder's path, so ``*`` from the vault counts and ``*.yaml``
    doesn't. A glob that goes on below a KB folder it matches (``*/*.md``) matches inside the KB."""
    parts = posixpath.normpath(path).split("/")
    first = next(i for i, part in enumerate(parts) if _GLOB_CHARS.search(part))
    prefix, pattern = _real("/".join(parts[:first]) or "/"), parts[first:]
    for kb_dir in KB_DIRS:
        target = posixpath.join(vault, kb_dir)
        if (prefix + "/").startswith(target + "/"):
            return True  # the glob is inside a KB folder
        if not (target + "/").startswith(prefix.rstrip("/") + "/"):
            continue
        rel = posixpath.relpath(target, prefix).split("/")
        if not all(fnmatch.fnmatchcase(fold(name), fold(glob)) for name, glob in zip(rel, pattern, strict=False)):
            continue
        if len(pattern) > len(rel):
            return True  # its matches are inside the KB folder
        if _reaches_kb(posixpath.join(prefix, *rel[: len(pattern)]), vault, rec):
            return True
    return False


def _real(path: str) -> str:
    """``path`` with its symlinks resolved, as far as it exists (a run's `cd docs/kblink` is in the KB)."""
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return posixpath.normpath(path)


LINK_WALK_CAP = 4000  # entries a link-following read's root is searched for symlinks before the gate gives up


def _links_reach_kb(root: str, vault: str, cap: int = LINK_WALK_CAP) -> bool:
    """Whether a read that follows symlinks (``find -L``, ``grep -R``, ``rg -L``) from ``root`` (resolved) may get
    into the KB through one: a link under it to a KB folder, into one or above one, or to a folder outside that
    leads on to such a link. True when the walk can't finish (over ``cap`` entries, an error): it fails closed."""
    kb = [posixpath.join(vault, d) for d in KB_DIRS]

    def into_kb(target: str) -> bool:
        return any(target == k or target.startswith(k + "/") or k.startswith(target.rstrip("/") + "/") for k in kb)

    seen: set[str] = set()
    todo = [root]
    count = 0
    while todo:
        top = todo.pop()
        if top in seen or not os.path.isdir(top):
            continue
        seen.add(top)
        try:
            for here, dirs, files in os.walk(top, followlinks=False):
                for name in (*dirs, *files):
                    count += 1
                    if count > cap:
                        return True
                    path = posixpath.join(here, name)
                    if not os.path.islink(path):
                        continue
                    target = _real(path)
                    if into_kb(target):
                        return True
                    if os.path.isdir(target) and not (target + "/").startswith(root.rstrip("/") + "/"):
                        todo.append(target)  # a folder outside the root: its links count too
        except OSError:
            return True
    return False


_UNPLACED = "\x00unplaced"  # a cd to a folder the gate can't place
# A function or alias that may move the shell, `cd` redefined, autocd, a global alias (it can rewrite any word).
_MOVES_UNSEEN = re.compile(
    r"(?:^|[\s;&|({])(?:function\s+[\w.-]+|[\w.-]+\s*\(\s*\))\s*(?:\{|\()[^}]*?\b(?:cd|pushd|popd|chdir)\b"
    r"|(?:^|[\s;&|({])(?:function\s+(?:cd|pushd|popd|chdir)\b|(?:cd|pushd|popd|chdir)\s*\(\s*\))"
    r"|\balias\b[^;&|\n]*(?:-g\b|\b(?:cd|pushd|popd|chdir)=)|\bauto_?cd\b",
    re.IGNORECASE,
)


def _cd_target(args: list[str], command: str) -> str:
    """Where ``cd args`` (``pushd``, ``chdir``) goes, as written, or :data:`_UNPLACED` when the gate can't place
    it: a flag it may take as the target (`-P`, `-q`, `-`, `-1`), a `~` form other than `~` and `~/…` (`~+`,
    `~-`, `~0`, a named folder), a relative target while the command sets CDPATH."""
    if args[:1] == ["--"]:
        args = args[1:]
    elif args and args[0].startswith("-"):
        return _UNPLACED
    target = args[0] if args else "~"  # a bare `cd` goes home
    if target.startswith("~") and target != "~" and not target.startswith("~/"):
        return _UNPLACED
    if "CDPATH" in command and not target.startswith(("/", "~", "./", "../")) and target not in (".", ".."):
        return _UNPLACED
    return target


def _cd_real(here: str | None, target: str, env: Mapping[str, str], local: Mapping[str, str]) -> str | None:
    """The folder ``cd target`` from ``here`` lands in, its symlinks resolved; None when it isn't a folder the
    gate can see (a missing one may be made, or linked, by the command itself)."""
    folder = _cd(here, target, env, local)
    if folder is None or not os.path.isdir(folder):
        return None
    return _real(folder)


def _lister_reaches(
    piece: str, name: str, where: str | None, reaches: Callable[[_Recursive, str | None], bool]
) -> bool:
    """A command whose output is a reader's files (``cat $(…)``): what a printer or lister prints counts as read,
    and a printer whose output the gate can't place (``echo ~+``) counts as reading anything."""
    if name not in ("echo", "printf", "ls", "git", "pwd"):
        return False
    listed = _listed_by(piece)
    if listed is None:
        return name != "git" or " ls-files" in piece
    return any(reaches(_Recursive([root], True, levels), where) for root, levels in listed)


def _judge_recursive(
    command: str,
    in_kb: bool,
    cwd: str | None,
    vault: str | None,
    env: Mapping[str, str],
    depth: int = 0,
    read: bool = False,
) -> bool:
    """The recursive-read rule: a search that gets to a KB note from the folder it starts in. ``read``: what
    ``command`` prints is handed to a reader as its files (``cat $(find .)``), so a name listing counts as a read
    of what it lists."""
    if vault is None or depth > 4:
        return False
    statements, joins, substitutions = _shell_split_joined(command)
    local = _local_values(command)
    loops = _loop_words(command)
    vault = _real(vault)  # every folder below is judged with its symlinks resolved
    here = _real(posixpath.normpath(cwd)) if cwd else None
    start = here
    judged: set[int] = set()
    # Something in the command that can move the shell where the gate can't follow: a function or alias that
    # cd's, `cd` itself redefined, autocd, a global alias. After each statement the folder may then be any.
    wobbly = bool(_MOVES_UNSEEN.search(command))

    def substituted(piece: str, folder: str | None) -> bool:
        """The substitutions in ``piece``, each judged from the folder the shell is in when it runs it
        (after the command's own ``cd``: ``cd docs; cat $(find .)`` lists ``docs/``). One a reading command
        takes among its arguments hands it files to read: their names are their contents then."""
        words = _strip(_shell_words(piece)).words
        reader = bool(words) and _command_name(words[0]) in _READ_COMMANDS
        for i in map(int, _SUBST_INDEX.findall(piece)):
            if i >= len(substitutions):
                continue  # a marker the command spelled itself: no substitution of this split
            judged.add(i)
            marker = f"{_SUBST}{i}{_SUBST}"
            fed_words = [w for w in words[1:] if marker in w] if reader else []
            if _judge_recursive(substitutions[i], in_kb, folder, vault, env, depth + 1, bool(fed_words)):
                return True
            listed = _listed_by(substitutions[i]) if fed_words else None
            if listed is None:
                continue
            for word in fed_words:
                # Text around it (`cat $(ls -d k*)/*`) makes the reader's paths: what it lists, joined with it.
                if word != marker and word.count(_SUBST) == 2:
                    joined = [word.replace(marker, root) for root, _levels in listed]
                    if any(_names_kb(j) or reaches(_Recursive([j], True, 0), folder) for j in joined):
                        return True
        return False

    def reaches(rec: _Recursive, where: str | None) -> bool:
        roots = rec.roots or ["."]
        for root in roots:
            loop = _LONE_VAR.match(root)
            if loop and loop.group(1) in loops:
                if reaches(_Recursive(loops[loop.group(1)] or ["."], rec.content, rec.depth, rec.excluded), where):
                    return True
                continue
            if _SUBST in root:
                # A root a command prints: what a printer or lister prints is placed (`$(echo .)`, `$(ls)`,
                # `$(find .. -maxdepth 1)`), anything else is a root the gate can't place, and the search is
                # counted as reaching the KB.
                found = _SUBST_INDEX.findall(root)
                listed = (
                    _listed_by(substitutions[int(found[0])])
                    if len(found) == 1 and int(found[0]) < len(substitutions)
                    else None
                )
                if listed is None:
                    return True
                marker = f"{_SUBST}{found[0]}{_SUBST}"
                for printed, extra in listed:
                    deeper = None if rec.depth is None or extra is None else rec.depth + extra
                    one = _Recursive([root.replace(marker, printed)], rec.content, deeper, rec.excluded)
                    if reaches(one, where):
                        return True
                continue
            word = _expand(root, env, local)
            if word == _UNKNOWN:
                if rec.content:
                    return True  # a content search the gate can't place: it may well be the vault
                continue
            folder = _root_folder(word)
            if not folder.startswith("/"):
                if where is None:
                    # Run from a folder the gate can't tell: the folder itself, its parent, the vault by its
                    # own name or a KB folder's parent may be what it reads.
                    first = posixpath.normpath(folder or ".").split("/")[0]
                    if rec.content and first in (".", "..", "action-items", posixpath.basename(vault.rstrip("/"))):
                        return True
                    continue
                folder = posixpath.join(where, folder)
            folder = _real(folder)
            if rec.follows and _links_reach_kb(folder, vault):
                return True  # it follows a symlink below its root into the KB
            if rec.depth is not None and _GLOB_CHARS.search(word):  # a glob a few levels from the KB: match it
                if _glob_reaches_kb(word if word.startswith("/") else posixpath.join(where or "/", word), vault, rec):
                    return True
                continue
            if _reaches_kb(folder, vault, rec):
                return True
        return False

    # The folders the shell may be in. A `cd` narrows them only when it surely runs and lands (top level, not after
    # `||` or a command that may fail, not in an `if`, a loop, a `{ … }` or a function, not backgrounded or in a
    # pipeline, to a folder that exists): otherwise the old folders stay possible beside the new ones.
    after: list[str | None] = [here]  # where a statement that follows unconditionally runs
    chain: list[str | None] = [here]  # where one joined by `&&` runs: the last statement succeeded
    tried: list[str | None] = [here]  # where the last statement ran: one joined by `||` runs there too
    sure = True  # the last statement surely ran
    landed = False  # ... and surely succeeded (a `cd` that landed)
    block = 0  # open `if`/loop/`case`/`{` blocks: what runs inside may not
    stack: list[tuple[list[str | None], bool]] = []  # each open subshell: `after` and `sure` at its `(`
    for index, (pieces, join) in enumerate(zip(statements, joins, strict=True)):
        if join == "&&":
            folders, certain = chain, sure and landed
        elif join == "||":
            folders, certain = _union(tried, chain), False
        elif join == "(":
            folders, certain = after, sure
        else:
            folders, certain = after, block == 0 and (not stack or stack[-1][1])
        backgrounded = index + 1 < len(joins) and joins[index + 1] == "&"
        if pieces == [_SUBSHELL_OPEN]:
            stack.append((after, certain))
            after, sure, landed = folders, certain, False
            continue
        if pieces == [_SUBSHELL_CLOSE]:  # a `cd` inside `( … )` ends at its `)`
            if stack:
                after, sure = stack.pop()
            chain = tried = after
            landed = False
            continue
        first = (_shell_words(pieces[0]) or [""])[0]
        if first in _BLOCK_CLOSE:
            block = max(0, block - 1)
        if first == "{":
            block += 1
            certain = False
        cd_to: str | None = None
        for piece in pieces:
            for here_now in folders:
                if substituted(piece, here_now):
                    return True
            raw = (_shell_words(piece) or [""])[0]
            if raw in ("eval", "source", "."):
                cd_to = _UNPLACED  # what it runs may cd anywhere (its own reads are judged below)
            stripped = _strip(_shell_words(piece))
            words = stripped.words
            if not words:
                continue
            if any(c.isspace() for c in words[0]):  # a program a shell prefix runs: `bash -c 'rg x'`
                text = _restored(" ".join(words), substitutions)
                if any(_judge_recursive(text, in_kb, f, vault, env, depth + 1) for f in folders):
                    return True
                continue
            name, args = _command_name(words[0]), words[1:]
            if name in ("cd", "pushd", "chdir", "popd"):  # `popd`: a folder the gate can't place
                cd_to = _cd_target(args, command) if name != "popd" and cd_to != _UNPLACED else _UNPLACED
                continue
            for here_now in folders:
                where = here_now if stripped.chdir is None else _cd(here_now, stripped.chdir, env, local)
                if read and _lister_reaches(piece, name, where, reaches):
                    return True  # what it prints is handed to a reader as its files (`cat $(echo */*.md)`)
                if _judged_piece(name, words, args, stripped, where, reaches, read, env, local):
                    return True
        if first in _BLOCK_OPEN:
            block += 1
        tried = folders
        if cd_to is not None and not backgrounded:
            moved = _union([None if cd_to == _UNPLACED else _cd_real(f, cd_to, env, local) for f in folders])
            lands = len(pieces) == 1 and all(m is not None for m in moved)
            chain = moved
            after = moved if certain and lands else _union(after, folders, moved)
            sure, landed = certain, lands
        else:
            chain = folders
            sure, landed = certain, False
        if wobbly:
            after, chain = _union(after, [None]), _union(chain, [None])
    # A substitution no piece holds (none should be): judged from where the command starts, as before.
    rest = [s for i, s in enumerate(substitutions) if i not in judged]
    return any(_judge_recursive(s, in_kb, start, vault, env, depth + 1) for s in rest)


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
