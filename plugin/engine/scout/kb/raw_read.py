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
import posixpath
import re
import shlex
from collections.abc import Mapping
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
# Readers of what a compressed file holds, readers too. (`zgrep`, `xzegrep` and the like are grep with
# decompression on: _command_name reads them as grep, see _DECOMPRESSING.)
_DECOMPRESSING_READERS = {
    "zcat", "gzcat", "bzcat", "xzcat", "lzcat", "lz4cat", "zstdcat", "zless", "zmore", "bzless", "bzmore", "xzless",
    "xzmore", "zdiff", "zcmp", "bzdiff", "bzcmp", "xzdiff", "xzcmp",
}  # fmt: skip
_READ_COMMANDS |= _DECOMPRESSING_READERS
# What runs the word after it: launchers, and zsh's precommand modifiers (`noglob`, `nocorrect`, `-`, `builtin`,
# `command`, `exec`; `-` is a stray flag to _strip). `noglob grep -r TODO .` is `grep -r TODO .` to the shell.
_PREFIX_WORDS = {
    "xargs", "sudo", "command", "builtin", "env", "nice", "time", "exec", "timeout", "gtimeout", "stdbuf",
    "caffeinate", "unbuffer", "parallel", "sh", "bash", "zsh", "dash", "ksh", "eval", "noglob", "nocorrect",
    "nohup",
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
    "exec": {"-a"},  # `exec -a NAME cmd`: NAME is the command's argv[0], not the command
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
        if base in _PREFIX_WORDS and (_unquoted(word) == base or "/" in word):  # `no''glob`, `\exec` too
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


_SCOUTCTL_KB = re.compile(r"\bscoutctl\s+kb\b")


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
                                 "--depth", "--jobs", "--range"}  # fmt: skip
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
# The greps that decompress what they read (macOS's zgrep is BSD grep with -Z), read as the grep they wrap.
_DECOMPRESSING = {f"{z}{g}": g for z in ("z", "bz", "xz", "lz", "lz4", "zstd") for g in ("grep", "egrep", "fgrep")}
_ALIASES.update(_DECOMPRESSING)
# What a decompressing read opens (BSD grep's -Z and ugrep's -z): compressed files, whose member is the name
# without the suffix (`x.md.gz` holds `x.md`), and archives, whose members the gate can't name.
_COMPRESSED_SUFFIXES = ("gz", "z", "bz2", "bz", "xz", "lzma", "lz", "lz4", "zst", "zstd", "br")
_ARCHIVE_SUFFIXES = ("tar", "tgz", "taz", "tbz", "tbz2", "tb2", "txz", "tlz", "tzst", "zip", "jar", "war", "7z",
                     "cpio", "pax", "zipx", "apk", "ipa", "xpi", "epub", "docx", "xlsx", "pptx", "odt")  # fmt: skip
# The type names for the formats the KB keeps, in ack's and ugrep's `-t` (ugrep's `+` adds shebangs).
_KB_TOOL_TYPES = {"markdown", "md", "yaml", "yml", "json", "csv", "tsv", "html", "htm", "xml", "text", "txt"}
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
_FOLDER_READERS = {"tree", "du", "find", "rg", "ag", "ack", "fd", "ugrep", "diff"}  # `diff -N dir empty/` prints it


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
    **dict.fromkeys(("grep", "egrep", "fgrep", "ugrep", "rg"), {"-f", "--file"}),
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
    if name == "find":
        roots: list[str] = []
        for a in args:
            if a.startswith(("-", "(", "!")):
                break
            roots.append(a)
        return roots
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


def _exclusions(args: list[str]) -> tuple[str, ...]:
    """What ``--exclude-dir=X`` / ``-g '!X/**'`` keep out, as the text of each exclusion."""
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
        elif value.startswith("!"):
            out.append(value[1:])
    return tuple(out)


def _recursive(name: str, args: list[str], word: str | None = None) -> _Recursive | None:
    """Where a recursive read starts, or None for a command that isn't one, or one whose file
    filter stays out of the KB (``--glob 'action-items/*.md'``, ``--include=*.py``), as for the
    Grep tool. ``word`` is the command word as written (``zgrep`` is grep that decompresses)."""
    if _not_a_read(name, args):
        return None
    runs = False  # fd -x/-X: it runs a command on each file it finds
    if name == "fd":
        args, runs = _fd_without_exec(args)
    flags, plain = _split_args(_operands(name, args) if name != "find" else args)
    filters: list[str] = []
    if name in ("grep", "egrep", "fgrep", "ugrep"):
        if not _grep_recursive(name, args):
            return None
        pattern_given = _program_given(name, args)
        roots = plain if pattern_given else plain[1:]
        filters = [a.split("=", 1)[1] for a in args if a.startswith("--include=")] + _flag_values(args, {"--include"})
        types: list[str] = []
        if name == "ugrep":  # ugrep also filters with `-g GLOBS`, `-O EXTS` and `-t TYPES`
            filters += _ugrep_includes(name, args, {"-g", "--glob"})
            filters += ["*." + e.lstrip(".") for e in _ugrep_includes(name, args, {"-O"})]
            types = _ugrep_includes(name, args, {"-t"})
        rec = _Recursive(roots, True, None, _exclusions(args))
        if _decompresses(word or name, name, args) and any(_may_match_compressed(f) for f in filters):
            return rec  # `--include='*.gz'` reads what each archive holds: notes, perhaps
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
    if name == "find":
        roots = []
        for a in args:
            if a.startswith(("-", "(", "!")):
                break
            roots.append(a)
        content = any(a in ("-exec", "-execdir", "-ok", "-okdir") for a in args)
        return _Recursive(roots, content, _int(_flag_value(args, {"-maxdepth"})))
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


def _grep_recursive(name: str, args: list[str]) -> bool:
    """grep's recursion, read with :func:`_scan` so a cluster counts (``-rnA3``, ``-rC2``, ``-r3``):
    ``-r``/``-R``, ``-d recurse``; for ugrep also ``-NUM`` and ``--depth``."""
    for flag, value in _options(name, args):
        if flag in _GREP_RECURSE or (flag in ("-d", "--directories") and value == "recurse"):
            return True
        if name == "ugrep" and (flag == "--depth" or re.fullmatch(r"-\d", flag)):
            return True
    return False


_GREPS = ("grep", "egrep", "fgrep", "ugrep")


def _decompresses(word: str, name: str, args: list[str]) -> bool:
    """A reader of what compressed files and archives hold: ``zcat``, ``zgrep`` and their kin; a grep with ``-Z``
    (BSD grep's ``--decompress``; GNU's ``--null``, judged the BSD way), ``-z`` (ugrep's ``--decompress``, which a
    run's bare ``grep`` is; GNU's and BSD's ``--null-data``) or ``--decompress``."""
    if posixpath.basename(_unquoted(word)) in _DECOMPRESSING or name in _DECOMPRESSING_READERS:
        return True
    return name in _GREPS and any(f in ("-z", "-Z", "--decompress") for f, _v in _options(name, args))


def _archive(path: str) -> bool:
    """A file whose members a decompressing read reads and the gate can't name: an archive (``kb.tgz``,
    ``notes.zip``), or a compressed one (``kb.tar.gz``). ``x.md.gz`` holds one file, ``x.md``, which its own
    name tells."""
    name = posixpath.basename(_unquoted(path)).lower()
    for suffix in _COMPRESSED_SUFFIXES:
        if name.endswith("." + suffix):
            name = name[: -len(suffix) - 1]
            break
    return name.rsplit(".", 1)[-1] in _ARCHIVE_SUFFIXES if "." in name else False


def _may_match_compressed(glob: str) -> bool:
    """Whether a file filter could let through a compressed file or an archive (``*.gz``, ``*.tgz``, ``*.md.*``):
    under decompression its members are read, and they may be notes. Tried on the glob's last part, since an
    archive can sit in any folder."""
    g = glob.strip("\"'").removeprefix("!").rsplit("/", 1)[-1]
    words = {w for w in re.findall(r"[A-Za-z0-9_-]+", g)}
    stems = {"x", *(s for w in words for s in (w, f"{w}x", f"x{w}", f"x{w}x"))}
    exts = [*_ARCHIVE_SUFFIXES, *(f"{k}.{c}" for k in (*KB_FILE_EXTENSIONS, "tar") for c in _COMPRESSED_SUFFIXES)]
    rx = _glob_regex(g)
    return any(rx.fullmatch(f"{stem}.{ext}") for stem in stems for ext in exts)


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
    (``--no-pager``, ``-P``, ``-c k=v``, ``-C dir``, ``--git-dir=…``)."""
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
            return a, args[i + 1 :], chdirs
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
                    if seg_in_kb or _names_kb(rest) or _uses_operand(name, args, tainted):
                        return True
                    if any(_glob_names_kb(a) for a in _operands(name, args)):
                        return True
                    if _decompresses(words[0], name, args) and any(map(_archive, read_operands(name, args) or [])):
                        return True  # an archive's members, which the gate can't name (`grep -Z x kb.tgz`)
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
    """A command word's program: every quote and backslash a shell removes (`bash -c 'cat …'`, `\rg`, `gr''ep`,
    `"gr"ep`, `g\rep`) gone, and its folder (`/usr/bin/grep`) dropped."""
    name = _unquoted(word).rsplit("/", 1)[-1]
    return _ALIASES.get(name, name)


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
# A substitution that prints where the vault is, or names a KB folder: `$(pwd)`, `$(git rev-parse --show-toplevel)`.
_RISKY_SUBSTITUTION = re.compile(
    r"(?:^|[\s;&|(])(?:pwd|dirname|realpath|readlink|basename)\b|rev-parse\s+--show-(?:toplevel|prefix|cdup)"
    r"|\$\{?(?:PWD|HOME|OLDPWD|SCOUT_DATA_DIR)\b|~|kno|ledge|context|action-items",
    re.IGNORECASE,
)


def _risky_substitution(inner: str) -> bool:
    return bool(_names_kb(inner) or _RISKY_SUBSTITUTION.search(inner))


def _shell_split(command: str) -> tuple[list[list[str]], list[str]]:
    """``(statements, substitutions)``: each statement a list of pipe pieces, split only where the
    shell splits (``rg -n "TODO|FIXME" scripts/`` is one piece). Each outermost ``$(…)`` or
    backtick, outside single quotes, is cut out and stands as :data:`_SUBST` in its piece."""
    statements: list[list[str]] = [[]]
    substitutions: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(command)

    def piece() -> None:
        statements[-1].append("".join(buf))
        buf.clear()

    def statement() -> None:
        piece()
        statements.append([])

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
            statement()
            i += 1
        elif c in ";\n()":
            statement()
        elif c == "&" and not (buf and buf[-1].endswith((">", "<"))) and not command.startswith((">", "&"), i + 1):
            statement()
        elif c == "|":
            piece()
        else:
            buf.append(c)
        i += 1
    piece()
    return [[p for p in s if p.strip()] for s in statements if any(p.strip() for p in s)], substitutions


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


def _judge_recursive(
    command: str, in_kb: bool, cwd: str | None, vault: str | None, env: Mapping[str, str], depth: int = 0
) -> bool:
    """The recursive-read rule: a search that gets to a KB note from the folder it starts in."""
    if vault is None or depth > 4:
        return False
    statements, substitutions = _shell_split(command)
    local = _local_values(command)
    loops = _loop_words(command)
    here = posixpath.normpath(cwd) if cwd else None
    if any(_judge_recursive(s, in_kb, here, vault, env, depth + 1) for s in substitutions):
        return True

    def reaches(rec: _Recursive, where: str | None) -> bool:
        roots = rec.roots or ["."]
        for root in roots:
            loop = _LONE_VAR.match(root)
            if loop and loop.group(1) in loops:
                if reaches(_Recursive(loops[loop.group(1)] or ["."], rec.content, rec.depth, rec.excluded), where):
                    return True
                continue
            if _SUBST in root:
                # Files a command prints are not a folder to walk, unless it prints where the vault is.
                if rec.content and any(_risky_substitution(substitutions[int(i)]) for i in _SUBST_INDEX.findall(root)):
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
            if _reaches_kb(folder, vault, rec):
                return True
        return False

    for pieces in statements:
        for piece in pieces:
            stripped = _strip(_shell_words(piece))
            words = stripped.words
            if not words:
                continue
            if any(c.isspace() for c in words[0]):  # a program a shell prefix runs: `bash -c 'rg x'`
                if _judge_recursive(" ".join(words), in_kb, here, vault, env, depth + 1):
                    return True
                continue
            name, args = _command_name(words[0]), words[1:]
            where = here if stripped.chdir is None else _cd(here, stripped.chdir, env, local)
            if name in ("cd", "pushd"):
                here = _cd(here, args[0] if args else "~", env, local)  # a bare `cd` goes home
                continue
            if name in _READ_COMMANDS:
                rec = _recursive(name, args, words[0])
                if rec is None or (stripped.fed and not rec.roots):
                    continue  # not recursive, or xargs hands it the files to read
                if reaches(rec, where):
                    return True
            elif name == "git":
                sub, sub_args, chdirs = _git(args)
                if sub != "grep":
                    continue
                for c in chdirs:
                    where = _cd(where, c, env, local)
                _flags, plain = _split_args(_operands("grep", sub_args))
                paths = plain if any(a in ("-e", "-f") for a in sub_args) else plain[1:]
                if not (stripped.fed and not paths) and reaches(_Recursive(paths, True), where):
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
