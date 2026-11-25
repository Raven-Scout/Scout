"""The files an interpreter's inline program reads: Python, Ruby, Node (and Bun, Deno), PHP, Lua and R.

:mod:`scout.kb.other_reads` asks this module about ``python3 -c``, ``ruby -e``, ``node -e``, ``php -r``,
``lua -e``, ``Rscript -e`` (and a program read from stdin: ``python3 - <<EOF``), so both of the gate's judges see
what such a program opens. Perl, sed and awk are other_reads' own.

A program's text is read for the calls that open a file (``open()``, ``Path(…).read_text()``, ``File.read``,
``fs.readFileSync``, ``file_get_contents``, ``io.open``, ``readLines`` and their kin), each judged by the path
its arguments spell:

- a string literal, or literals joined by ``+``, ``..``, ``.``, ``os.path.join``, ``Path(a) / b``,
  ``File.join``, ``path.join``, ``file.path``, ``paste0``, or a variable the program sets to one of those, is a
  path opened as written (:attr:`OtherReads.literal`);
- a path that starts with a literal and goes on with something built at run time (``"knowledge-base/" + n``,
  ``f"knowledge-base/{n}"``) reads in the folder the literal names (:data:`FOLDER`), and is built too;
- anything else is built at run time (:attr:`OtherReads.built`), which the gate records as ``program_path``;
- ``sys.argv``, ``ARGV``, ``ARGF``, ``process.argv``, ``$argv``, ``arg[n]`` and ``commandArgs()`` read the
  program's operands, as ``ruby -n``/``-p`` loops do: they are judged as the files the shell hands over.

A program that lists a folder (``os.walk``, ``glob.glob``, ``Dir.glob``, ``fs.readdirSync``, ``list.files``…)
and opens a path it builds is taken to read what it listed: the listed root, below it all when the listing is
recursive (:data:`~scout.kb.other_reads.COVERS`). A write (``open(p, "w")``, ``fs.writeFileSync``) reads
nothing. A copy or a move (``shutil.copy``, ``os.rename``, ``FileUtils.cp``, ``copy()``, ``file.copy``) reads its
source unless its target names the KB. The shell commands a program runs (``subprocess.run``, ``os.system``,
backticks, ``execSync``, ``shell_exec``, ``os.execute``, ``system()``) are commands of their own
(:attr:`OtherReads.programs`).

Pure: it judges text and does no I/O. It over-reads where a program is ambiguous; the gate only denies a path
that resolves into the KB.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field

# How a path is read (scout.kb.live_run.KB_READ_HOW): "folder" reads in a folder (a KB folder counts, a folder
# holding one doesn't). other_reads has FILE and COVERS.
FILE, FOLDER, COVERS = "file", "folder", "covers"
# What of a spelled path is live (scout.kb.live_run shields the rest): nothing, a glob's `*?[`, a leading `~`.
AS_IS, GLOB, TILDE = "as-is", "glob", "tilde"
_EXPANDS_HOME = re.compile(r"expanduser|expand_path|path\.expand|homedir|os\.getenv\(\s*['\"]HOME")
_KB_NAME = re.compile(r"knowledge[-_]base|action-items/context")
_GLOB = re.compile(r"[*?\[]")
_MAX_COMBOS = 16
_MAX_DEPTH = 3


@dataclass
class ProgramReads:
    """What one interpreter's programs read (scout.kb.other_reads turns it into an OtherReads)."""

    reads: list[tuple[str, str]] = field(default_factory=list)  # operands, (word as the shell hands it, FILE)
    literal: list[str] = field(default_factory=list)  # paths a program opens exactly as written
    # Paths the program's text reads another way: (path as the program spells it, FILE | FOLDER | COVERS, what of
    # it is live: AS_IS, GLOB, TILDE): a built path's folder, a copy's source, what a listing lists, a `~` path the
    # program expands.
    spelled: list[tuple[str, str, str]] = field(default_factory=list)
    built: bool = False
    programs: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.reads or self.literal or self.spelled or self.built or self.programs)


# ----- the interpreters' command lines ---------------------------------------------------------------------


@dataclass
class _Call:
    lang: str
    programs: list[str]
    operands: list[str]  # the program's argv, as the shell hands it
    loops: bool = False  # ruby -n/-p: the operands are read line by line
    chdir: str | None = None  # ruby -C DIR


_PYTHON_VALUES = {"-W", "-X", "--check-hash-based-pycs"}
_RUBY_VALUES = {"-r", "-I", "-C", "-F", "-E", "-x", "--encoding", "--external-encoding", "--internal-encoding"}
_NODE_VALUES = {"-r", "--require", "--import", "--loader", "--experimental-loader", "--input-type", "-C",
                "--conditions", "--env-file", "--title"}  # fmt: skip
_PHP_VALUES = {"-d", "-c", "-z", "--define", "-B", "-R", "-E", "-F", "-f", "--file"}
_LUA_VALUES = {"-l"}
_R_VALUES = {"--encoding", "--file", "-f", "--default-packages", "--args"}

# Matched against a name raw_read.program_name has folded: `PYTHON3`, `Rscript` and `ｒｕｂｙ` run them too.
INTERPRETER = re.compile(
    r"(?:python[0-9.]*|pypy[0-9.]*|ruby[0-9.]*|node|nodejs|bun|deno|php[0-9.]*|lua[0-9.]*|luajit|rscript|r)"
)


def _lang(name: str) -> str:
    if name.startswith(("python", "pypy")):
        return "python"
    if name.startswith("ruby"):
        return "ruby"
    if name in ("node", "nodejs", "bun", "deno"):
        return "js"
    if name.startswith("php"):
        return "php"
    if name.startswith("lua"):
        return "lua"
    return "r"


def _call(name: str, args: list[str], stdin: Sequence[str]) -> _Call | None:
    """The programs ``name args`` runs from the command itself (its ``-c``/``-e`` values, or its stdin when it
    reads its program there), and their operands; None for a script file or a module."""
    lang = _lang(name)
    if lang == "js" and name == "deno":
        if args[:1] == ["eval"]:
            rest = [a for a in args[1:] if not a.startswith("-")]
            return _Call(lang, rest[:1], rest[1:]) if rest else None
        return None  # deno run FILE, deno task…: a file
    if lang == "js" and name == "bun" and args[:1] and not args[0].startswith("-"):
        return None  # bun run FILE, bun install…
    letters = {"python": "c", "ruby": "e", "js": "eEp", "php": "r", "lua": "e", "r": "e"}[lang]
    takes = {"python": _PYTHON_VALUES, "ruby": _RUBY_VALUES, "js": _NODE_VALUES, "php": _PHP_VALUES,
             "lua": _LUA_VALUES, "r": _R_VALUES}[lang]  # fmt: skip
    programs: list[str] = []
    loops, chdir, from_stdin = False, None, False
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if lang == "python" and programs:
            return _Call(lang, programs, args[i - 1 :])  # python -c PROG: the rest is its argv
        if a == "--":
            break
        if a == "-":
            from_stdin = True
            break
        if lang == "js" and a in ("--eval", "--print"):
            programs += args[i : i + 1]
            i += 1
            continue
        if lang == "js" and a.startswith(("--eval=", "--print=")):
            programs.append(a.split("=", 1)[1])
            continue
        if lang == "r" and a.startswith("--file="):
            return None
        if lang == "php" and a in ("-f", "--file"):
            return None
        if not a.startswith("-"):
            if not programs:
                return None  # a script file, or a subcommand (`R CMD BATCH x.R`)
            i -= 1
            break
        if a.startswith("--"):
            flag = a.split("=", 1)[0]
            if flag in takes and "=" not in a:
                if lang == "r" and flag == "--args":
                    break
                i += 1
            if lang == "r" and flag == "--file":
                return None
            continue
        flag = a[:2]
        if lang == "ruby":
            for j, ch in enumerate(a[1:], 1):
                if ch in "np":
                    loops = True
                if "-" + ch in _RUBY_VALUES:
                    value = a[j + 1 :] or (args[i] if i < len(args) else "")
                    if not a[j + 1 :]:
                        i += 1
                    if ch == "C":
                        chdir = value
                    break
                if ch == "e":
                    if a[j + 1 :]:
                        programs.append(a[j + 1 :])
                    elif i < len(args):
                        programs.append(args[i])
                        i += 1
                    break
            continue
        last = a[-1]
        if len(a) >= 2 and last in letters and all(c.isalpha() for c in a[1:]) and flag not in takes:
            if i < len(args):
                programs.append(args[i])
                i += 1
            continue
        if flag[1:] in letters and len(a) > 2 and lang != "js":
            programs.append(a[2:])  # -cPROG, -ePROG
            continue
        if flag in takes and len(a) == 2:
            i += 1
        if lang == "r" and flag == "-f":
            return None
    operands = args[i:]
    if lang == "python" and programs:
        return _Call(lang, programs, operands)
    if not programs:
        if not (from_stdin or not operands):
            return None  # a script file
        if not stdin:
            return None
        return _Call(lang, [_printed(t) for t in stdin], operands, loops, chdir)
    return _Call(lang, programs, operands, loops, chdir)


def program_reads(name: str, args: list[str], stdin: Sequence[str] = ()) -> ProgramReads | None:
    """What ``name args`` reads when ``name`` is one of this module's interpreters (see the module's docstring),
    with ``stdin`` the programs it would get there (here-documents, here-strings, a pipe's text); None for
    another command, or a program that reads nothing."""
    from scout.kb.raw_read import program_name

    name = program_name(name)  # on a case-insensitive disk `PYTHON3` runs python3
    if not INTERPRETER.fullmatch(name):
        return None
    call = _call(name, args, stdin)
    if call is None:
        return None
    out = ProgramReads()
    text = "\n".join(call.programs)
    scan = _Scan(text, call.lang)
    reads_operands = call.loops or scan.reads_operands()
    scan.judge(out)
    if reads_operands or scan.argv_read:
        out.reads += [(o, FILE) for o in call.operands if o not in ("-", "--")]
    if call.chdir:
        out.literal = [p if p.startswith(("/", "~")) else posixpath.join(call.chdir, p) for p in out.literal]
    return out or None


def _printed(text: str) -> str:
    """A program piped in by ``echo`` or ``printf`` (``echo 'print(1)' | python3``): what it prints."""
    if not re.match(r"\s*(?:echo|printf)\s", text):
        return text
    try:
        words = shlex.split(text)
    except ValueError:
        return text
    return " ".join(w for w in words[1:] if not re.fullmatch(r"-[neE]+", w))


# ----- reading a program's text ----------------------------------------------------------------------------

_COMMENT = {"python": "#", "ruby": "#", "r": "#", "php": "#|//", "js": "//", "lua": "--"}


def _spans(text: str, lang: str) -> tuple[list[tuple[int, int]], str]:
    """The string literals in ``text`` (start, end) and ``text`` with their insides and its comments blanked."""
    spans: list[tuple[int, int]] = []
    masked = list(text)
    comment = re.compile(_COMMENT[lang])
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if lang == "python" and text.startswith(('"""', "'''"), i):
            end = text.find(text[i : i + 3], i + 3)
            end = n if end < 0 else end + 3
        elif lang == "lua" and (m := re.match(r"\[(=*)\[", text[i:])):
            close = "]" + m[1] + "]"
            end = text.find(close, i + len(m[0]))
            end = n if end < 0 else end + len(close)
        elif c in "'\"" or (c == "`" and lang in ("js", "ruby", "php")):
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            end = min(j + 1, n)
        elif comment.match(text, i) and not (lang == "ruby" and text.startswith("#{", i)):
            end = text.find("\n", i)
            end = n if end < 0 else end
            masked[i:end] = " " * (end - i)
            i = end
            continue
        else:
            i += 1
            continue
        spans.append((i, end))
        masked[i + 1 : end - 1] = " " * max(0, end - i - 2)
        i = end
    return spans, "".join(masked)


_PAIRS = {"(": ")", "[": "]", "{": "}"}


def _close(masked: str, at: int) -> int:
    """The index just past the bracket that closes the one at ``at``."""
    want: list[str] = []
    for j in range(at, len(masked)):
        c = masked[j]
        if c in _PAIRS:
            want.append(_PAIRS[c])
        elif want and c == want[-1]:
            want.pop()
            if not want:
                return j + 1
    return len(masked)


def _split_top(masked: str, start: int, end: int, seps: str) -> list[tuple[int, int]]:
    """``masked[start:end]`` split at top-level separators (any of ``seps``)."""
    parts, depth, at = [], 0, start
    for j in range(start, end):
        c = masked[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c in seps:
            parts.append((at, j))
            at = j + 1
    parts.append((at, end))
    return [(a, b) for a, b in parts if masked[a:b].strip()]


def _statement_end(masked: str, at: int, lang: str) -> int:
    """Where an expression that starts at ``at`` ends: a top-level newline, ``;``, a closing bracket or ``,``, or
    (Lua, Ruby, R without parentheses) a word that follows a complete operand."""
    depth, j, n = 0, at, len(masked)
    while j < n:
        c = masked[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return j
            depth -= 1
        elif depth == 0 and c in "\n;,":
            return j
        elif depth == 0 and c.isspace() and lang in ("lua", "ruby", "python", "r"):
            before = masked[at:j].rstrip()
            after = masked[j:].lstrip()
            ends_operand = bool(before) and (before[-1].isalnum() or before[-1] in "_'\")]`")
            if ends_operand and after and (after[0].isalpha() or after[0] == "_"):
                word = re.match(r"\w+", after)
                if word is None or word[0] not in ("and", "or", "not", "in"):
                    return j
        j += 1
    return n


@dataclass
class _Value:
    kind: str  # "lit": a whole path; "pre": a path that starts with ``text``; "built"; "argv": the operands
    text: str = ""


_BUILT = _Value("built")


def _join(parts: list[_Value], sep: str) -> _Value:
    """Parts joined as a path (``sep`` "/") or a string (``sep`` "")."""
    text = ""
    for k, part in enumerate(parts):
        if part.kind == "argv" and k == 0:
            return part  # the operand itself
        if part.kind not in ("lit", "pre"):
            return _Value("pre", text) if text else _BUILT
        if sep and k and part.text.startswith("/"):
            text = part.text
        elif sep and k:
            text = posixpath.join(text, part.text) if text else part.text
        else:
            text += part.text
        if part.kind == "pre":
            return _Value("pre", text)
    return _Value("lit", text)


# Calls whose result is a path made of their arguments: joined as path parts, or passed through (the first).
_JOINS = {
    "python": r"(?:os\.path\.join|posixpath\.join|(?:pathlib\.)?(?:Pure(?:Posix)?)?Path|PosixPath)",
    "ruby": r"(?:File\.join|Pathname(?:\.new)?)",
    "js": r"(?:[\w$]+\.(?:posix\.)?)?(?:join|resolve)",
    "php": r"(?!)",
    "lua": r"(?!)",
    "r": r"(?:file\.path)",
}
_PASS = {
    "python": r"(?:os\.path\.(?:expanduser|abspath|realpath|normpath|expandvars)|os\.fspath|str)",
    "ruby": r"(?:File\.(?:expand_path|absolute_path|realpath))",
    "js": r"(?:path\.normalize|String)",
    "php": r"(?:realpath)",
    "lua": r"(?!)",
    "r": r"(?:normalizePath|path\.expand)",
}
_CONCAT = {"python": "+", "ruby": "+", "js": "+", "php": ".", "lua": "..", "r": "\0"}
_ARGV = {
    "python": r"\bsys\.argv\b",
    "ruby": r"\bARGV\b|\$\*",
    "js": r"\bprocess\.argv\b|\bDeno\.args\b|\bBun\.argv\b",
    "php": r"\$argv\b|\$_SERVER\s*\[\s*['\"]argv",
    "lua": r"\barg\s*\[|\.\.\.",
    "r": r"\bcommandArgs\s*\(",
}
# A program that reads the files its operands name without naming them: fileinput, ARGF and kin.
_READS_OPERANDS = {
    "python": r"\bfileinput\.input\s*\(\s*\)",
    "ruby": r"\bARGF\b|\$<|(?<![\w.:])(?:gets|readlines|readline)\b",
    "js": r"(?!)",
    "php": r"(?!)",
    "lua": r"(?!)",
    "r": r"(?!)",
}
# Assignments a variable's value can come from.
_ASSIGN = {
    "python": r"(?<![\w.]){name}\s*(?::\s*[\w.\[\]]+\s*)?=(?!=)\s*",
    "ruby": r"(?<![\w.@$]){name}\s*=(?![=~])\s*",
    "js": r"(?<![\w.$]){name}\s*=(?![=>])\s*",
    "php": r"\${name}\s*=(?![=>])\s*",
    "lua": r"(?<![\w.]){name}\s*=(?!=)\s*",
    "r": r"(?<![\w.]){name}\s*(?:<<?-|=(?!=))\s*",
}
_IDENT = {"python": r"[A-Za-z_]\w*", "ruby": r"[a-z_]\w*", "js": r"[A-Za-z_$][\w$]*", "php": r"\$[A-Za-z_]\w*",
          "lua": r"[A-Za-z_]\w*", "r": r"[A-Za-z.][\w.]*"}  # fmt: skip


# (call head, path argument, mode argument or None, mode keyword or None, how): each call reads a path.
_Read = tuple[str, int, int | None, str | None]
_READS: dict[str, list[_Read]] = {
    "python": [
        (r"(?<![\w.])(?:io\.|codecs\.|builtins\.|gzip\.|bz2\.|lzma\.|tokenize\.)?open", 0, 1, "mode"),
        (r"\bos\.open", 0, None, None),  # flags: judged below
        (
            r"\b(?:linecache\.getlines?|np\.(?:loadtxt|genfromtxt|load|fromfile)|numpy\.(?:loadtxt|genfromtxt|load)"
            r"|(?:pd|pandas)\.read_\w+|runpy\.run_path|imp\.load_source)",
            0,
            None,
            None,
        ),
        (r"\bfileinput\.input", 0, None, None),
    ],
    "ruby": [
        (r"\b(?:File|IO)\.(?:open|new)", 0, 1, "mode"),
        (
            r"\b(?:File|IO)\.(?:read|readlines|foreach|binread|readlink)|\bCSV\.(?:read|foreach|open|table)"
            r"|\bYAML\.(?:load_file|safe_load_file|unsafe_load_file)|\bJSON\.load_file"
            r"|(?<![\w.:])(?:load|require_relative)",
            0,
            None,
            None,
        ),
        (r"(?<![\w.:])open", 0, 1, "mode"),
    ],
    "js": [
        (
            r"(?<![\w$])(?:[\w$]+\.)*(?:readFileSync|readFile|createReadStream|readTextFile|readTextFileSync)",
            0,
            None,
            None,
        ),
        (
            r"(?<![\w$])(?:[\w$]+\.)*openSync|(?<![\w$])(?:fs|fsp|promises)(?:\.promises)?\.open|\bDeno\.open",
            0,
            1,
            "flag",
        ),
        (r"\bBun\.file", 0, None, None),
        (r"(?<![\w$.])require|(?<![\w$.])import", 0, None, None),
    ],
    "php": [
        (r"(?<![\w$>:])(?:fopen|gzopen|SplFileObject)", 0, 1, None),
        (
            r"(?<![\w$>:])(?:file_get_contents|file|readfile|highlight_file|show_source|php_strip_whitespace"
            r"|parse_ini_file|simplexml_load_file|md5_file|sha1_file|gzfile|readgzfile|include|include_once|require"
            r"|require_once)",
            0,
            None,
            None,
        ),
        (r"(?<![\w$>:])hash_file", 1, None, None),
    ],
    "lua": [
        (r"\bio\.open", 0, 1, None),
        (r"\bio\.(?:lines|input)|(?<![\w.])(?:dofile|loadfile)", 0, None, None),
    ],
    "r": [
        (r"(?<![\w.])file", 0, 1, "open"),
        (
            r"(?<![\w.:])(?:(?:readr|vroom|data\.table|jsonlite|yaml|readxl)::)?(?:readLines|readRDS|readChar|readBin"
            r"|scan|source|sys\.source|load|fread|read_(?:csv2?|tsv|file|lines|delim|rds|yaml|json|excel|table)"
            r"|read\.(?:csv2?|table|delim2?|fwf|dcf)|fromJSON|vroom)",
            0,
            None,
            None,
        ),
    ],
}
# Copies and moves: (call head, source argument, target argument).
_COPIES: dict[str, list[tuple[str, int, int]]] = {
    "python": [(r"\bshutil\.(?:copy\w*|move)|\bos\.(?:rename|replace|renames|link)", 0, 1)],
    "ruby": [(r"\b(?:FileUtils\.(?:cp\w*|copy\w*|mv|move|ln\w*|install)|File\.(?:rename|link))", 0, 1)],
    "js": [(r"(?<![\w$])(?:[\w$]+\.)*(?:copyFileSync|copyFile|cpSync|cp|renameSync|rename|linkSync|link)", 0, 1)],
    "php": [(r"(?<![\w$>:])(?:copy|rename|link)", 0, 1)],
    "lua": [(r"\bos\.rename", 0, 1)],
    "r": [(r"(?<![\w.])file\.(?:copy|rename|link|append)", 0, 1)],
}
# Listings: (call head, root argument, recursive?) — None for "recursive when its pattern has `**`".
_LISTS: dict[str, list[tuple[str, int, bool | None]]] = {
    "python": [
        (r"\bos\.(?:listdir|scandir)|\.iterdir", 0, False),
        (r"\bos\.walk|\.rglob", 0, True),
        (r"\bglob\.i?glob|(?<![\w.])i?glob|\.glob", 0, None),
    ],
    "ruby": [
        (r"\bDir\.(?:entries|children|each_child|foreach)", 0, False),
        (r"\bDir\.glob", 0, None),
        (r"\bDir\[", 0, None),
    ],
    "js": [
        (r"(?<![\w$])(?:[\w$]+\.)*(?:readdirSync|readdir|opendirSync|opendir)", 0, False),
        (r"(?<![\w$])(?:[\w$]+\.)*(?:globSync|glob)", 0, None),
    ],  # fmt: skip
    "php": [
        (r"(?<![\w$>:])(?:scandir|opendir)", 0, False),
        (r"(?<![\w$>:])glob", 0, None),
        (r"\bRecursiveDirectoryIterator", 0, True),
    ],  # fmt: skip
    "lua": [(r"\blfs\.dir", 0, False)],
    "r": [(r"(?<![\w.])(?:list\.files|dir|list\.dirs)", 0, False), (r"(?<![\w.])Sys\.glob", 0, None)],
}
# Shell commands: (call head, kind) — "shell": its first argument is a command line; "argv": a program and its
# arguments (a list, or the rest of the call's arguments).
_RUNS: dict[str, list[tuple[str, str]]] = {
    "python": [(r"\bos\.(?:system|popen)|\bsubprocess\.(?:getoutput|getstatusoutput)|\bcommands\.getoutput", "shell"),
               (r"\bsubprocess\.(?:run|call|check_call|check_output|Popen)|\bos\.exec\w+|\bpty\.spawn", "argv")],
    "ruby": [(r"(?<![\w.:])(?:system|exec|spawn)|\bIO\.popen|\bOpen3\.(?:capture\w*|popen\w*)", "argv")],
    "js": [(r"(?<![\w$])(?:[\w$]+\.)*(?:execSync|exec)", "shell"),
           (r"(?<![\w$])(?:[\w$]+\.)*(?:execFileSync|execFile|spawnSync|spawn)|\bBun\.spawn(?:Sync)?", "argv")],
    "php": [(r"(?<![\w$>:])(?:shell_exec|system|exec|passthru|popen|proc_open)", "shell")],
    "lua": [(r"\bos\.execute|\bio\.popen", "shell")],
    "r": [(r"(?<![\w.])(?:system|shell|pipe)", "shell"), (r"(?<![\w.])system2", "argv")],
}  # fmt: skip
_CHDIR = {
    "python": r"\bos\.chdir",
    "ruby": r"\bDir\.chdir",
    "js": r"\bprocess\.chdir|\bDeno\.chdir",
    "php": r"(?<![\w$>:])chdir",
    "lua": r"\blfs\.chdir",
    "r": r"(?<![\w.])setwd",
}


class _Scan:
    """One program's text in one language."""

    def __init__(self, text: str, lang: str) -> None:
        self.text, self.lang = text, lang
        self.spans, self.masked = _spans(text, lang)
        self.starts = {a: b for a, b in self.spans}
        self.argv_read = False

    # -- what an expression spells ------------------------------------------------------------------------

    def _literal(self, a: int, b: int) -> _Value:
        """The string literal at ``text[a:b]``: its text, or the part before an interpolation."""
        token = self.text[a:b]
        prefix = ""
        if self.lang == "python":
            m = re.match(r"[rRbBuUfF]{0,2}", self.text[max(0, a - 2) : a][::-1])
            prefix = self.text[a - len(m[0]) : a].lower() if m else ""
        quote = token[:3] if token[:3] in ('"""', "'''") else token[:1]
        if token.startswith("["):  # Lua's long string
            return _Value("lit", re.sub(r"^\[=*\[\n?|\]=*\]$", "", token))
        body = token[len(quote) : len(token) - len(quote)] if len(token) >= 2 * len(quote) else token[len(quote) :]
        live = {
            "python": r"(?<!\{)\{(?!\{)" if "f" in prefix else None,
            "js": r"\$\{" if quote == "`" else None,
            "ruby": r"#\{|#[@$]" if quote == '"' else None,
            "php": r"\$[A-Za-z_{]|\{\$" if quote == '"' else None,
        }.get(self.lang)
        if (self.lang == "ruby" and quote == "`") or (self.lang == "php" and quote == "`"):
            return _BUILT  # a command's output
        cut = re.search(live, body) if live else None
        text = body[: cut.start()] if cut else body
        if self.lang in ("ruby", "php") and quote == "'":
            text = re.sub(r"\\([\\'])", r"\1", text)  # only \\ and \' escape there
        elif "r" not in prefix:
            text = re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), text)
        if cut:
            return _Value("pre", text) if text else _BUILT
        return _Value("lit", text)

    def values(self, a: int, b: int, depth: int = 0) -> list[_Value]:
        """What ``text[a:b]`` can spell as a path: each value it can take (a variable set more than once has
        more than one)."""
        while a < b and self.masked[a].isspace():
            a += 1
        while b > a and self.masked[b - 1].isspace():
            b -= 1
        if a >= b or depth > _MAX_DEPTH:
            return [_BUILT]
        expr = self.masked[a:b]
        if re.search(_ARGV[self.lang], self.text[a:b]):
            return [_Value("argv")]
        if expr[0] == "(" and _close(self.masked, a) == b:
            return self.values(a + 1, b - 1, depth)
        if a in self.starts and self.starts[a] == b:
            return [self._literal(a, b)]
        if self.lang == "python" and re.match(r"[rRbBuUfF]{1,2}['\"]", self.text[a:b]):
            q = a + len(re.match(r"[rRbBuUfF]{1,2}", self.text[a:b])[0])  # type: ignore[index]
            if q in self.starts and self.starts[q] == b:
                return [self._literal(q, b)]
        # Concatenation: `a + b`, `a .. b`, `a . b`.
        op = _CONCAT[self.lang]
        parts = self._split_op(a, b, op)
        if len(parts) > 1:
            return self._combine([self.values(x, y, depth + 1) for x, y in parts], "")
        if self.lang == "python":
            slash = self._split_op(a, b, "/")
            if len(slash) > 1:
                return self._combine([self.values(x, y, depth + 1) for x, y in slash], "/")
        # A call: a join, a pass-through, R's paste0/paste/sprintf.
        call = re.match(r"([\w.$:]+)\s*\(", expr)
        if call is not None and _close(self.masked, a + call.end() - 1) == b:
            name = call[1]
            args = self.args(a + call.end() - 1)
            if re.fullmatch(_JOINS[self.lang], name):
                return self._combine([self.values(x, y, depth + 1) for x, y in args], "/") if args else [_BUILT]
            if re.fullmatch(_PASS[self.lang], name) and args:
                return self.values(*args[0], depth + 1)
            if self.lang == "r" and name in ("paste0", "paste", "sprintf"):
                plain = [(x, y) for x, y in args if not re.match(r"\s*(?:sep|collapse)\s*=", self.masked[x:y])]
                if name == "sprintf" and plain:
                    first = self.values(*plain[0], depth + 1)
                    return [_Value("pre", v.text.split("%", 1)[0]) if v.kind == "lit" and "%" in v.text else v
                            for v in first]  # fmt: skip
                seps = [_Value("lit", "" if name == "paste0" else " ")]
                for x, y in args:
                    given = re.match(r"\s*sep\s*=\s*", self.masked[x:y])
                    if given is not None:
                        seps = self.values(x + given.end(), y, depth + 1)
                pasted: list[list[_Value]] = []
                for k, (x, y) in enumerate(plain):
                    pasted += [seps] if k else []
                    pasted.append(self.values(x, y, depth + 1))
                return self._combine(pasted, "")
            return [_BUILT]
        tail = re.search(r"\.(?:expanduser|absolute|resolve|as_posix|__str__)\(\)$", expr)
        if self.lang == "python" and tail is not None:  # `Path("x").expanduser()`, `p.resolve()`
            return self.values(a, a + tail.start(), depth + 1)
        # A variable: the values the program assigns it.
        ident = re.fullmatch(_IDENT[self.lang], expr)
        if ident is not None:
            name = expr
            if self.lang == "js" and name in ("__dirname", "__filename"):
                return [_BUILT]
            found: list[_Value] = []
            pattern = _ASSIGN[self.lang].format(name=re.escape(name.lstrip("$")))
            for m in re.finditer(pattern, self.masked):
                end = _statement_end(self.masked, m.end(), self.lang)
                found += self.values(m.end(), end, depth + 1)
            if re.search(rf"\bfor\s+{re.escape(name)}\s+in\b", self.masked):  # a loop variable: built
                found.append(_BUILT)
            return found or [_BUILT]
        return [_BUILT]

    def _split_op(self, a: int, b: int, op: str) -> list[tuple[int, int]]:
        """``text[a:b]`` split at a top-level binary ``op`` (``.`` in PHP only between spaces or quotes)."""
        if op == "\0":
            return [(a, b)]
        out, depth, at, j = [], 0, a, a
        while j < b:
            c = self.masked[j]
            if j in self.starts:
                j = self.starts[j]
                continue
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif depth == 0 and self.masked.startswith(op, j):
                if op == "." and not (self.masked[j - 1 : j].isspace() or self.masked[j - 1 : j] in "'\")"):
                    j += 1
                    continue
                if op == "/" and self.masked.startswith("//", j):
                    return [(a, b)]  # floor division: no path
                out.append((at, j))
                at = j + len(op)
                j = at
                continue
            j += 1
        out.append((at, b))
        return out

    def _combine(self, parts: list[list[_Value]], sep: str) -> list[_Value]:
        combos: list[list[_Value]] = [[]]
        for options in parts:
            combos = [c + [o] for c in combos for o in options][: _MAX_COMBOS + 1]
        if len(combos) > _MAX_COMBOS:
            return [_BUILT]
        return [_join(c, sep) for c in combos]

    def args(self, paren: int) -> list[tuple[int, int]]:
        """The top-level arguments of the call whose ``(`` is at ``paren``."""
        end = _close(self.masked, paren)
        return _split_top(self.masked, paren + 1, end - 1, ",")

    def _call_args(self, head_end: int) -> list[tuple[int, int]] | None:
        """The arguments of a call whose head ends at ``head_end``: in parentheses, or (Ruby, Lua, PHP's
        include) a bare string or a list up to the end of the statement."""
        j = head_end
        while j < len(self.masked) and self.masked[j] in " \t":
            j += 1
        if j < len(self.masked) and self.masked[j] == "(":
            return self.args(j)
        if self.lang in ("ruby", "lua", "php") and j > head_end or (j < len(self.text) and self.text[j] in "'\"["):
            if self.lang in ("ruby", "lua", "php"):
                end = _statement_end(self.masked, j, self.lang)
                if self.lang == "ruby":  # `File.read "x" do |l|`, `File.read "x" if y`
                    m = re.search(r"\s(?:do|if|unless|while|until|and|or)\b|\{", self.masked[j:end])
                    end = j + m.start() if m else end
                return _split_top(self.masked, j, end, ",") or None
        return None

    def _heads(self, pattern: str) -> list[tuple[int, int]]:
        """``(start, end)`` of each call head ``pattern`` matches outside strings and comments."""
        return [(m.start(), m.end()) for m in re.finditer(rf"(?:{pattern})(?=\s*[(\s'\"`\[])", self.masked)]

    # -- what the program does ----------------------------------------------------------------------------

    def reads_operands(self) -> bool:
        return bool(re.search(_READS_OPERANDS[self.lang], self.masked))

    def _mode_writes(self, args: list[tuple[int, int]], index: int | None, keyword: str | None) -> bool:
        """Whether a call's mode makes it a write that reads nothing (``"w"``, ``"a"``, ``"x"``, not ``"r+"``
        or ``"a+"``). A mode the program builds counts as a read."""
        mode: tuple[int, int] | None = None
        if keyword is not None:
            for x, y in args:
                m = re.match(rf"\s*:?{keyword}\s*(?:=|:)\s*", self.masked[x:y])
                if m is not None:
                    mode = (x + m.end(), y)
        positional = [(x, y) for x, y in args if not re.match(r"\s*:?\w+\s*(?:=(?!=)|:(?!:))", self.masked[x:y])]
        if mode is None and index is not None and index < len(positional):
            mode = positional[index]
        if mode is None:
            return False
        values = self.values(*mode)
        if self.lang == "js" and re.match(r"\s*\{", self.masked[mode[0] : mode[1]]):
            flag = re.search(r"\bflags?\s*:\s*(['\"])([^'\"]*)\1", self.text[mode[0] : mode[1]])
            values = [_Value("lit", flag[2])] if flag else [_Value("lit", "r")]
        if not all(v.kind == "lit" for v in values):
            return False
        return all(("r" not in v.text and not ("a" in v.text and "+" in v.text)) for v in values)

    def _path_args(self, args: list[tuple[int, int]], index: int) -> list[_Value]:
        positional = [(x, y) for x, y in args if not re.match(r"\s*:?\w+\s*(?:=(?!=)|:(?!:))", self.masked[x:y])]
        if index >= len(positional):
            named = [
                (x, y) for x, y in args if re.match(r"\s*(?:file|path|filename|description|con)\s*=", self.masked[x:y])
            ]
            if not named:
                return []
            x, y = named[0]
            return self.values(x + self.masked[x:y].index("=") + 1, y)
        x, y = positional[index]
        expr = self.masked[x:y].strip()
        if expr.startswith("[") and self.lang in ("python", "ruby", "js"):  # fileinput.input(["a", "b"])
            inner = x + self.masked[x:y].index("[")
            return [v for p in _split_top(self.masked, inner + 1, _close(self.masked, inner) - 1, ",")
                    for v in self.values(*p)]  # fmt: skip
        return self.values(x, y)

    def judge(self, out: ProgramReads) -> None:
        """Every read, copy, listing and command in the program, into ``out``."""
        paths: list[_Value] = []
        for head, index, mode_index, keyword in _READS[self.lang]:
            for _start, end in self._heads(head):
                args = self._call_args(end)
                if args is None:
                    continue
                if "os\\.open" in head:
                    flags = self.masked[args[1][0] : args[1][1]] if len(args) > 1 else ""
                    if "O_WRONLY" in flags:
                        continue
                elif mode_index is not None and self._mode_writes(args, mode_index, keyword):
                    continue
                found = self._path_args(args, index)
                if "require|" in head and self.lang == "js":  # a module by name is no file
                    found = [v for v in found if v.kind != "lit" or v.text.startswith((".", "/"))]
                if "fileinput" in head and not args:
                    self.argv_read = True
                paths += found
        # Path(…).read_text() and kin: the receiver is the path.
        if self.lang in ("python", "ruby"):
            paren = r"\s*\(" if self.lang == "python" else r"\b\s*\(?"
            method = rf"\.(?:read_text|read_bytes|open|read|readlines|each_line|binread){paren}"
            for m in re.finditer(method, self.masked):
                receiver = self._receiver(m.start())
                if receiver is None:
                    continue
                called = m[0].endswith("(")
                if m[0].startswith(".open") and called and self._mode_writes(self.args(m.end() - 1), 0, "mode"):
                    continue
                values = self.values(*receiver)
                if not m[0].startswith((".read_text", ".read_bytes")):
                    values = [v for v in values if v.kind != "built"]  # `f.read()` on a file object: no new path
                paths += values
        copied: list[_Value] = []
        for head, src, dst in _COPIES[self.lang]:
            for _start, end in self._heads(head):
                args = self._call_args(end)
                if args is None:
                    continue
                target = self._path_args(args, dst)
                if any(v.kind in ("lit", "pre") and _KB_NAME.search(v.text) for v in target):
                    continue  # into the KB: a write there
                copied += self._path_args(args, src)
        listed: list[tuple[str, str]] = []
        for head, index, recursive in _LISTS[self.lang]:
            for start, end in self._heads(head):
                if head.endswith("\\["):  # Ruby's Dir["pattern"]
                    args = _split_top(self.masked, end, _close(self.masked, end - 1) - 1, ",")
                else:
                    args = self._call_args(end)
                if args is None:
                    continue
                values = self._path_args(args, index)
                if self.masked[start] == ".":  # Path("x").glob("*.md"): below the receiver
                    receiver = self._receiver(start)
                    bases = self.values(*receiver) if receiver is not None else [_BUILT]
                    values = [_join([b, v], "/") for b in bases for v in values or [_Value("lit", "")]]
                for value in values or [_Value("lit", ".")]:
                    if value.kind in ("lit", "pre"):
                        deep = recursive if recursive is not None else "**" in value.text
                        after = self.text[end : end + 200]
                        deep = deep or bool(re.search(r"recursive\s*=\s*(?:True|TRUE|T)\b", after))
                        root = value.text if value.kind == "lit" else posixpath.dirname(value.text)
                        listed.append((root.rstrip("/") or ".", COVERS if deep else FOLDER))
        chdirs = []
        for _start, end in self._heads(_CHDIR[self.lang]):
            args = self._call_args(end)
            chdirs += self._path_args(args, 0) if args else [_BUILT]
        self._runs(out)
        for value in copied:  # a copy reads all of a folder it's given
            if value.kind == "argv":
                self.argv_read = True
            elif value.kind == "lit":
                out.spelled.append((value.text, COVERS, AS_IS))
            else:
                out.built = True
        for value in paths:
            if value.kind == "argv":
                self.argv_read = True
            elif value.kind == "lit" and self.lang == "ruby" and value.text.startswith("|"):
                out.programs.append(value.text[1:])  # open("|cmd"), IO.read("|cmd"): runs it
            elif value.kind == "lit":
                out.literal.append(value.text)
            elif value.kind == "pre":
                out.built = True
                folder = value.text if value.text.endswith("/") else posixpath.dirname(value.text)
                out.spelled.append(((folder.rstrip("/") or "."), FOLDER, AS_IS))
            else:
                out.built = True
        if out.built:  # it opens what it builds: what it listed, it reads
            for root, how in listed:
                pattern = bool(_GLOB.search(root))
                out.spelled.append((root, FILE if pattern and how == FOLDER else how, GLOB if pattern else AS_IS))
        self._chdirs(chdirs, out)
        if _EXPANDS_HOME.search(self.text):  # Path("~/x").expanduser(): the `~` is the home folder
            out.spelled += [(p, FILE, TILDE) for p in out.literal if p.startswith("~")]

    @staticmethod
    def _chdirs(chdirs: list[_Value], out: ProgramReads) -> None:
        """A program that changes its folder (``os.chdir``, ``Dir.chdir``, ``setwd``) is judged as perl's ``chdir``
        is (scout.kb.other_reads): a literal folder is a folder read (one in the KB is denied, read or not), and
        each relative path the program names is judged from it as well as from where the command runs; a folder the
        program builds is a path built at run time."""
        for chdir in chdirs:
            if chdir.kind != "lit":
                out.built = True
                continue
            here = chdir.text.rstrip("/") or "/"
            relative = [p for p in out.literal if not p.startswith(("/", "~"))]
            out.literal += [posixpath.join(here, p) for p in relative]
            out.spelled += [
                (posixpath.join(here, p), how, live)
                for p, how, live in list(out.spelled)
                if live != TILDE and not p.startswith(("/", "~"))
            ]
            out.spelled.append((here, FOLDER, AS_IS))

    def _receiver(self, dot: int) -> tuple[int, int] | None:
        """The expression a method call at ``dot`` (``.read_text``) is called on: a call (``Path("x")``), a
        parenthesised expression or a variable; None for anything else."""
        j = dot
        if j and self.masked[j - 1] == ")":
            start = j
            while start and self.masked[start - 1] == ")":  # `Path("x").expanduser()`: back over each call
                depth, k = 0, start - 1
                while k >= 0:
                    if self.masked[k] == ")":
                        depth += 1
                    elif self.masked[k] == "(":
                        depth -= 1
                        if depth == 0:
                            break
                    k -= 1
                if k < 0:
                    return None
                head = re.search(r"[\w.]*$", self.masked[:k])
                start = head.start() if head else k
                if not self.masked.startswith(".", start):
                    break
            return (start, j)
        word = re.search(r"[A-Za-z_]\w*$", self.masked[:j])
        if word is None or word[0] in ("self", "sys", "os", "io", "f", "fh", "fp", "file"):
            return None
        return (word.start(), j)

    def _runs(self, out: ProgramReads) -> None:
        for head, kind in _RUNS[self.lang]:
            for _start, end in self._heads(head):
                args = self._call_args(end)
                if not args:
                    continue
                positional = [(x, y) for x, y in args if not re.match(r"\s*(?::?\w+\s*[=:](?!:)|\{)", self.masked[x:y])]
                listed = bool(positional) and self.masked[positional[0][0] : positional[0][1]].strip()[:1] == "["
                if kind == "argv" and (listed or len(positional) > 1):
                    items: list[tuple[int, int]] = []
                    for x, y in positional:  # a list's items, flattened: ["cat", "x"], spawn("cat", ["x"]), c("x")
                        inner = re.match(r"\s*(?:c\s*\(|\[)", self.masked[x:y])
                        if inner is not None:
                            at = x + inner.end() - 1
                            items += _split_top(self.masked, at + 1, _close(self.masked, at) - 1, ",")
                        else:
                            items.append((x, y))
                    parts = [self.values(x, y) for x, y in items]
                    words = [p[0].text if len(p) == 1 and p[0].kind == "lit" else None for p in parts]
                    if any(w is None for w in words):
                        out.built = True
                    elif words:
                        out.programs.append(" ".join(shlex.quote(w) for w in words if w is not None))
                    continue
                values = self.values(*positional[0]) if positional else [_BUILT]
                for value in values:
                    if value.kind == "lit":
                        out.programs.append(value.text)
                    else:
                        out.built = True
        if self.lang in ("ruby", "php"):  # backticks and %x(…): commands
            for a, b in self.spans:
                if self.text[a] == "`":
                    command = self._literal_command(a, b)
                    out.programs += [command] if command is not None else []
                    out.built = out.built or command is None
            if self.lang == "ruby":
                for m in re.finditer(r"%x([({\[])", self.masked):
                    close = _close(self.text, m.end() - 1)
                    body = self.text[m.end() : close - 1]
                    if "#{" in body:
                        out.built = True
                    else:
                        out.programs.append(body)
        if self.lang == "js":  # zx / Bun shell: $`cmd`
            for a, b in self.spans:
                if self.text[a] == "`" and re.search(r"\$\s*$", self.masked[:a]):
                    command = self._literal_command(a, b)
                    out.programs += [command] if command is not None else []
                    out.built = out.built or command is None

    def _literal_command(self, a: int, b: int) -> str | None:
        body = self.text[a + 1 : b - 1]
        live = {"ruby": r"#\{", "php": r"\$[A-Za-z_{]", "js": r"\$\{"}[self.lang]
        return None if re.search(live, body) else body
