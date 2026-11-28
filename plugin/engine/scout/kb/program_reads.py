"""What an interpreter's inline program reads and writes: Python, Ruby, Node (and Bun, Deno), PHP, Lua and R, with
a lexer near enough for ipython, groovy, jshell, swift, tclsh and expect.

:mod:`scout.kb.other_reads` asks this module about ``python3 -c``, ``ruby -e``, ``node -e``, ``php -r``,
``lua -e``, ``Rscript -e``, a program read from stdin (``python3 - <<EOF``), and a script file or a module, so both
of the gate's judges see what such a program opens. Perl, sed and awk are other_reads' own.

A program's text is read for the calls that open a file (``open()``, ``Path(…).read_text()``, ``File.read``,
``fs.readFileSync``, ``file_get_contents``, ``io.open``, ``readLines`` and their kin), each judged by the path
its arguments spell:

- a string literal (its escapes decoded), or literals joined by ``+``, ``..``, ``.``, ``os.path.join``,
  ``Path(a) / b``, ``File.join``, ``path.join``, ``file.path``, ``paste0``, or a variable the program sets to one
  of those, is a path opened as written (:attr:`ProgramReads.literal`);
- a path that starts with a literal and goes on with something built at run time (``"knowledge-base/" + n``,
  ``f"knowledge-base/{n}"``) reads in the folder the literal names (:data:`FOLDER`), and is built too;
- anything else is built at run time (:attr:`ProgramReads.built`), which the gate records as ``program_path``;
- ``sys.argv``, ``ARGV``, ``ARGF``, ``process.argv``, ``$argv``, ``arg[n]`` and ``commandArgs()`` read the
  program's operands, as ``ruby -n``/``-p`` loops and a script's or a module's operands do.

The files it writes (an open with a mode that writes, ``write_text``, ``File.write``, ``fs.writeFileSync``,
``file_put_contents``, a copy's target, ``ruby -i``'s operands) are :attr:`ProgramReads.writes`: a program that
reads a KB note and writes only into the KB is an edit, which the gate records rather than denies. A listing
(``os.listdir``, ``os.walk``, ``glob.glob``, ``fs.readdirSync``…) reads the folder it lists, as ``ls`` does, so
listing a KB folder is a KB read. A test (``os.path.exists``) gives a name only, as ``test -e`` does, and is no
read. The shell commands a program runs
(``subprocess.run``, ``os.system``, backticks, ``execSync``, ``os.execute``, ``system()``) are commands of their own
(:attr:`ProgramReads.programs`).

It fails closed: the program's text naming the KB anywhere no judged call spells a path with (an alias, ``exec``,
code in an f-string or a template, a comment, a literal behind an escape, a lexer the program fools) is
:attr:`ProgramReads.unaccounted`, which the gate denies.

Pure: it judges text and does no I/O. The gate only denies a path that resolves into the KB.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scout.kb.python_text import PythonText

# How a path is read (scout.kb.live_run.KB_READ_HOW): "folder" reads in a folder (a KB folder counts, a folder
# holding one doesn't). other_reads has FILE and COVERS.
FILE, FOLDER, COVERS = "file", "folder", "covers"
# What of a spelled path is live (scout.kb.live_run shields the rest): nothing, a glob's `*?[`, a leading `~`.
AS_IS, GLOB, TILDE = "as-is", "glob", "tilde"
# Python that names a module which starts programs or runs code: a text literal it writes may be run.
# A `for` loop's names (one, or a tuple of them), up to its `in`.
_FOR_TARGETS = r"\bfor\s+\(?\s*((?:[A-Za-z_]\w*\s*,\s*)*[A-Za-z_]\w*)\s*,?\s*\)?\s+in\s+"
_SPAWNS = (
    r"\b(?:subprocess|_posixsubprocess|pty|pexpect|multiprocessing|asyncio|concurrent|ctypes|cffi|posix|runpy"
    r"|importlib|webbrowser|code|codeop)\b|\bos\.(?:system|popen|spawn\w*|exec\w*|posix_spawn\w*|fork\w*|startfile)"
)
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
    # The files it writes (a write call's path, a copy's target), exactly as written; ``writes_built`` when one is
    # a path it builds; ``operand_writes`` when it rewrites its operands in place (``ruby -i -pe``). A program that
    # reads a KB note and writes only into the KB is an edit (the gate records ``kb_edit``).
    writes: list[str] = field(default_factory=list)
    writes_built: bool = False
    operand_writes: bool = False
    # Its text names the KB somewhere the scan didn't judge as a path (an alias, code in an f-string, an escape, a
    # lexer the program fooled): the gate denies it, unless it's an edit.
    unaccounted: bool = False
    # Its program is text the shell fills in (``-c "$(…)"``, ``-c "$P"``): the gate denies it when the command
    # names the KB anywhere, and records it otherwise.
    opaque: bool = False
    # The script file it runs (``python3 x.py``), as the shell hands it: what that reads isn't in the command, so
    # the gate judges the file (Scout's own scripts are trusted; any other is recorded). Also in ``reads``.
    script: str | None = None
    # `python3 <script> args` with no interpreter flag: the only shape a script of Scout's own is trusted in (a flag
    # like `-i`, `-m pdb` or `-X` runs other code around it).
    script_bare: bool = False

    def __bool__(self) -> bool:
        return bool(
            self.reads
            or self.literal
            or self.spelled
            or self.built
            or self.script
            or self.programs
            or self.writes
            or self.writes_built
            or self.unaccounted
        )


# ----- the interpreters' command lines ---------------------------------------------------------------------


@dataclass
class _Call:
    lang: str
    programs: list[str]
    operands: list[str]  # the program's argv, as the shell hands it
    loops: bool = False  # ruby -n/-p: the operands are read line by line
    chdir: str | None = None  # ruby -C DIR
    files: list[str] = field(default_factory=list)  # files the command line itself reads (a script, `-r`, `-l`)
    operands_read: bool = False  # a script or a module given operands: they are taken as files it reads
    in_place: bool = False  # ruby -i: the operands are rewritten
    piped: str | None = None  # its program comes down a pipe from a command the gate can't read: that text
    script: str | None = None  # the script file it runs (also in ``files``), as the shell hands it
    bare: bool = False  # `python3 <script> args`: no interpreter flag before the script


_PYTHON_VALUES = {"-W", "-X", "--check-hash-based-pycs"}
_RUBY_VALUES = {"-r", "-I", "-C", "-F", "-E", "-x", "--encoding", "--external-encoding", "--internal-encoding"}
_NODE_VALUES = {"-r", "--require", "--import", "--loader", "--experimental-loader", "--input-type", "-C",
                "--conditions", "--env-file", "--title"}  # fmt: skip
_PHP_VALUES = {"-d", "-c", "-z", "--define", "-B", "-R", "-E", "-F", "-f", "--file"}
_LUA_VALUES = {"-l"}
_R_VALUES = {"--encoding", "--file", "-f", "--default-packages", "--args"}
_OTHER_VALUES = {"-f", "--startup", "-R", "-J", "-cp", "--class-path", "-classpath"}
# Option values that are files the interpreter reads: a library it requires, a script, an ini or env file.
_FILE_VALUES = {"ruby": {"-r"}, "js": {"-r", "--require", "--import", "--loader", "--experimental-loader",
                                       "--env-file"},
                "lua": {"-l"}, "php": {"-c", "-F", "-f", "--file", "-z"}, "r": {"--file", "-f"},
                "other": {"-f", "--startup"}}  # fmt: skip
# php.ini settings that name a file php runs before or after the script.
_PHP_FILE_SETTINGS = re.compile(r"\s*(?:auto_prepend_file|auto_append_file|error_log|include_path)\s*=\s*(.*)")

# Matched against a name raw_read.program_name has folded: `PYTHON3`, `Rscript` and `ｒｕｂｙ` run them too.
INTERPRETER = re.compile(
    r"(?:python[0-9.]*|pypy[0-9.]*|ipython[0-9.]*|ruby[0-9.]*|node|nodejs|bun|deno|php[0-9.]*|lua[0-9.]*|luajit"
    r"|rscript|r|tclsh[0-9.]*|wish|expect|jshell|groovy|swift)"
)
# What a piped-in program came from when it wasn't `echo`/`printf` (live_run marks it): the gate can't read it.
PIPED = "\x00scout-piped\x00"
# Running code the program builds or reads at run time: what that code reads, the gate can't see.
_RUNS_CODE = {
    "python": r"(?<![\w.])(?:exec|eval|compile|__import__)\s*\(|\bimportlib\.|\brunpy\.",
    "ruby": r"(?<![\w.])(?:eval|instance_eval|class_eval|module_eval|send|public_send)\b|\bBinding\b",
    "js": r"(?<![\w$.])(?:eval|Function)\s*\(|\bnew\s+Function\b|\bvm\.run|\bimport\s*\(",
    "php": r"(?<![\w$>:])(?:eval|create_function|assert|call_user_func\w*)\s*\(",
    "lua": r"(?<![\w.])(?:load|loadstring|loadfile|dofile)\s*\(",
    "r": r"(?<![\w.])(?:eval|parse|source|do\.call|get)\s*\(",
}


def _lang(name: str) -> str:
    """The language whose scan reads ``name``'s program. tclsh, expect, jshell, groovy and swift get a lexer
    near enough (their reads aren't modelled: what they name of the KB is judged by the text alone)."""
    if name.startswith(("python", "pypy", "ipython")):
        return "python"
    if name.startswith("ruby"):
        return "ruby"
    if name in ("node", "nodejs", "bun", "deno"):
        return "js"
    if name.startswith("php"):
        return "php"
    if name.startswith("lua"):
        return "lua"
    if name in ("rscript", "r"):
        return "r"
    return "other"


def _lexer(lang: str, name: str) -> str:
    if lang != "other":
        return lang
    return "ruby" if name.startswith(("tclsh", "wish", "expect")) else "js"


def _call(name: str, args: list[str], stdin: Sequence[str]) -> _Call | None:  # noqa: C901 - one flag at a time
    """What ``name args`` runs: its ``-c``/``-e`` programs (or its stdin when it reads its program there), their
    operands, and the files the command line reads itself (a script file, ``-m MOD``'s operands, ``-r LIB``,
    ``-l LIB``, php's ``-d auto_prepend_file=``). None for a command that runs no program (``bun install``)."""
    lang = _lang(name)
    lexer = _lexer(lang, name)
    if name == "deno":
        if args[:1] == ["eval"]:
            rest = [a for a in args[1:] if not a.startswith("-")]
            return _Call(lexer, rest[:1], rest[1:]) if rest else None
        if args[:1] == ["run"]:
            rest = [a for a in args[1:] if not a.startswith("-")]
            return _Call(lexer, [], rest[1:], files=rest[:1], operands_read=True, script=rest[0]) if rest else None
        return None  # deno task, deno fmt…
    if name == "bun" and args[:1] and not args[0].startswith("-"):
        rest = args[1:] if args[0] == "run" else args if re.search(r"\.[cm]?[jt]sx?$", args[0]) else []
        return _Call(lexer, [], rest[1:], files=rest[:1], operands_read=True, script=rest[0]) if rest else None
    letters = {"python": "c", "ruby": "e", "js": "eEp", "php": "rBRE", "lua": "e", "r": "e", "other": "ce"}[lang]
    takes = {"python": _PYTHON_VALUES, "ruby": _RUBY_VALUES, "js": _NODE_VALUES, "php": _PHP_VALUES,
             "lua": _LUA_VALUES, "r": _R_VALUES, "other": _OTHER_VALUES}[lang]  # fmt: skip
    file_values = _FILE_VALUES.get(lang, set())
    if any(a in ("-V", "-v", "-h", "--help", "--version") for a in args[:1]):
        return None  # a version or a help text: no program runs
    programs: list[str] = []
    files: list[str] = []
    loops = in_place = from_stdin = False
    chdir: str | None = None
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if lang == "python" and programs:
            return _Call(lexer, programs, args[i - 1 :], files=files)  # python -c PROG: the rest is its argv
        if a == "--":
            break
        if a == "-":
            from_stdin = True
            break
        if lang == "python" and a.startswith("-m"):  # a module: what it reads isn't here, but its operands are
            module, rest = (a[2:], args[i:]) if a[2:] else (args[i] if i < len(args) else "", args[i + 1 :])
            if module.split(".")[0] == "scout":
                return None  # Scout's own engine, as scoutctl: the gate judges its commands elsewhere
            return _Call(lexer, [], rest, files=files, operands_read=True)
        if lang == "js" and a in ("--eval", "--print"):
            programs += args[i : i + 1]
            i += 1
            continue
        if lang == "js" and a.startswith(("--eval=", "--print=")):
            programs.append(a.split("=", 1)[1])
            continue
        if not a.startswith("-"):
            if programs:
                i -= 1
                break
            if lang == "r" and a == "CMD":
                return _Call(lexer, [], args[i + 1 :], files=args[i : i + 1], operands_read=True)  # R CMD BATCH x.R
            bare = lang == "python" and i == 1 and not files
            if lang == "python" and any(re.fullmatch(r"-[A-Za-z]*i[A-Za-z]*", x) for x in args[: i - 1]):
                # `-i`: after the script, it runs what stdin holds (a here-string, `< file`) as a program too
                texts = [_printed(t) for t in stdin]
                piped = next((t[len(PIPED) :] for t in texts if t.startswith(PIPED)), "" if not texts else None)
                texts = [t for t in texts if not t.startswith(PIPED)]
                return _Call(lexer, texts, args[i:], files=[*files, a], operands_read=True, script=a, piped=piped)
            return _Call(lexer, [], args[i:], files=[*files, a], operands_read=True, script=a, bare=bare)
        if a.startswith("--"):
            flag, eq, value = a.partition("=")
            if flag in takes and not eq and i < len(args):
                if lang == "r" and flag == "--args":
                    from_stdin = True  # the rest is the program's argv; R reads its program from stdin
                    i -= 1
                    break
                value, i = args[i], i + 1
            if flag in file_values and value:
                files.append(value)
            if lang == "php" and flag == "--define" and value and (m := _PHP_FILE_SETTINGS.match(value)):
                files.append(m[1].strip("'\""))
            continue
        flag = a[:2]
        if lang == "ruby":
            for j, ch in enumerate(a[1:], 1):
                if ch in "np":
                    loops = True
                if ch == "i":
                    in_place = True
                    break  # `-i.bak`: the rest is a suffix
                if "-" + ch in _RUBY_VALUES:
                    value = a[j + 1 :] or (args[i] if i < len(args) else "")
                    if not a[j + 1 :]:
                        i += 1
                    if ch == "C":
                        chdir = value
                    if ch == "r" and value:
                        files.append(value)
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
        if (
            len(a) >= 2
            and last in letters
            and all(c.isalpha() for c in a[1:])
            and flag not in takes - {"-B", "-R", "-E"}
        ):
            if i < len(args):
                programs.append(args[i])
                i += 1
            continue
        if lang == "php" and flag in ("-B", "-R", "-E"):  # php code run before, per line, after
            programs.append(a[2:] or (args[i] if i < len(args) else ""))
            i += 0 if a[2:] else 1
            continue
        if flag[1:] in letters and len(a) > 2 and lang != "js":
            programs.append(a[2:])  # -cPROG, -ePROG
            continue
        if flag in takes:
            value = a[2:]
            if not value and i < len(args):
                value, i = args[i], i + 1
            if flag in file_values and value:
                files.append(value)
            if lang == "php" and flag == "-d" and (m := _PHP_FILE_SETTINGS.match(value)):
                files.append(m[1].strip("'\""))
            if lang in ("php", "r", "other") and flag == "-f" and value:
                return _Call(lexer, [], args[i:], files=files, operands_read=True, script=value)  # php -f, R -f
    operands = args[i:]
    if lang == "python" and programs:
        return _Call(lexer, programs, operands, files=files)
    if not programs:
        if not (from_stdin or not operands):
            return _Call(lexer, [], operands[1:], files=[*files, operands[0]], operands_read=True, script=operands[0])
        if not stdin:  # `python3 < file`, a terminal: a program the gate can't see
            return _Call(lexer, [], operands, files=files, piped="")
        texts = [_printed(t) for t in stdin]
        piped = next((t[len(PIPED) :] for t in texts if t.startswith(PIPED)), None)
        texts = [t for t in texts if not t.startswith(PIPED)]
        return _Call(lexer, texts, operands, loops, chdir, files, in_place=in_place, piped=piped)
    return _Call(lexer, programs, operands, loops, chdir, files, in_place=in_place)


def program_reads(name: str, args: list[str], stdin: Sequence[str] = ()) -> ProgramReads | None:
    """What ``name args`` reads and writes when ``name`` is one of this module's interpreters (see the module's
    docstring), with ``stdin`` the programs it would get there (here-documents, here-strings, a pipe's text); None
    for another command, or a program that does neither."""
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
    reads_operands = call.loops or call.operands_read or scan.reads_operands()
    scan.judge(out)
    if reads_operands or scan.argv_read:
        out.reads += [(o, FILE) for o in call.operands if o not in ("-", "--")]
    out.reads += [(f, FILE) for f in call.files]
    out.operand_writes = call.in_place and bool(call.operands)
    if call.piped is not None:  # `cmd | python3`, `python3 < f`: a program the gate can't read
        out.built = True
        out.unaccounted = out.unaccounted or bool(_KB_NAME.search(call.piped))
    if call.script is not None:  # a script: what it reads isn't in the command, so the gate judges the file
        out.script = call.script  # Scout's own, or one it records
        out.script_bare = call.bare
    elif call.operands_read and not call.programs:  # a module: what it reads isn't in the command either
        out.built = True
    if re.search(r"\$__scout_\w+__", text):  # live_run's placeholder for what the shell fills in
        out.built = out.opaque = True
    if re.search(_RUNS_CODE.get(call.lang, r"(?!)"), scan.masked):  # code it builds or reads, then runs
        out.built = True
    if call.chdir:

        def into(p: str) -> str:
            if p.startswith(("/", "~")):
                return p
            return (call.chdir or "") if p == "." else posixpath.join(call.chdir or "", p)

        out.literal = [into(p) for p in out.literal]
        out.writes = [into(p) for p in out.writes]
        out.spelled = [(p if live == TILDE else into(p), how, live) for p, how, live in out.spelled]
    out.literal = list(dict.fromkeys(out.literal))
    out.spelled = list(dict.fromkeys(out.spelled))
    out.writes = list(dict.fromkeys(out.writes))
    return out or None


def _printed(text: str) -> str:
    """A program piped in by ``echo`` or ``printf`` (``echo 'print(1)' | python3``): what it prints. Text from any
    other command is marked :data:`PIPED` (live_run's), and stays so."""
    if text.startswith(PIPED) or not re.match(r"\s*(?:echo|printf)\s", text):
        return text
    try:
        words = shlex.split(text)
    except ValueError:
        return text
    return " ".join(w for w in words[1:] if not re.fullmatch(r"-[neE]+", w))


# ----- reading a program's text ----------------------------------------------------------------------------

_COMMENT = {"python": "#", "ruby": "#", "r": "#", "php": "#|//", "js": "//", "lua": "--"}


_ESCAPE = re.compile(
    r"\\(?:x([0-9A-Fa-f]{2})|u\{([0-9A-Fa-f]{1,6})\}|u([0-9A-Fa-f]{4})|U([0-9A-Fa-f]{8})|([0-9]{1,3})|(.))", re.S
)


def _unescape(text: str, lang: str) -> str | None:
    r"""A string literal's escapes decoded as ``lang`` reads them (``\x6b``, ``\u006b``, ``\u{6b}``, ``\153``;
    Lua's ``\107`` is decimal); None for one it can't decode (Python's ``\N{…}``)."""

    def one(m: re.Match[str]) -> str:
        hex2, brace, u4, u8, digits, other = m.groups()
        code = hex2 or brace or u4 or u8
        if code is not None:
            return chr(int(code, 16))
        if digits is not None:
            if lang == "lua":
                return chr(int(digits) % 256)
            octal = re.match(r"[0-7]+", digits)
            if octal is None:
                return "\\" + digits
            return chr(int(octal[0], 8)) + digits[octal.end() :]
        if other == "N" and lang == "python":
            raise ValueError("a named escape")
        return {"n": "\n", "t": "\t", "r": "\r"}.get(other, other)

    try:
        return _ESCAPE.sub(one, text)
    except (ValueError, OverflowError):
        return None


def _spans(text: str, lang: str) -> tuple[list[tuple[int, int]], str]:
    """The string literals in ``text`` (start, end) and ``text`` with their insides and its comments blanked."""
    spans: list[tuple[int, int]] = []
    masked = list(text)
    comment = re.compile(_COMMENT[lang])
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if lang == "python" and text.startswith(('"""', "'''"), i):
            quote, j, end = text[i : i + 3], i + 3, n
            while j < n:  # a backslash keeps the next character in the string, a raw string's too (`\"""`)
                if text[j] == "\\":
                    j += 2
                elif text.startswith(quote, j):
                    end = j + 3
                    break
                else:
                    j += 1
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
# The read heads of each language, one pattern: a call to one is judged by its path.
_READ_HEADS = {lang: "|".join(head for head, *_ in reads) or r"(?!)" for lang, reads in _READS.items()}
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
# Writes: (call head, path argument). A read call with a mode that writes (``open(p, "w")``) is one too.
_WRITES: dict[str, list[tuple[str, int]]] = {
    "python": [(r"\b(?:json|yaml|pickle|toml|tomli_w)\.(?:safe_)?dump_?file", 0)],
    "ruby": [(r"\b(?:File|IO)\.(?:write|binwrite)|\bFileUtils\.(?:touch|mkdir_p)", 0)],
    "js": [(r"(?<![\w$])(?:[\w$]+\.)*(?:writeFileSync|writeFile|appendFileSync|appendFile|createWriteStream"
            r"|writeTextFileSync|writeTextFile|truncateSync)|\bBun\.write", 0)],
    "php": [(r"(?<![\w$>:])(?:file_put_contents|touch|tempnam)", 0)],
    "lua": [(r"\bio\.output", 0)],
    "r": [(r"(?<![\w.:])(?:writeLines|saveRDS|write\.(?:csv2?|table)|write_(?:csv|tsv|lines|file|rds)|write)", 1),
          (r"(?<![\w.:])sink", 0), (r"(?<![\w.:])(?:cat|save)", 99)],  # cat(…, file=p): only by name
}  # fmt: skip
# Methods whose receiver is the path written: Path(p).write_text(…).
_WRITE_METHODS = r"\.(?:write_text|write_bytes|touch|binwrite)\s*\("
# Calls that use a path's name only (a listing's, a test, a size): judged as names, never as reads.
_NAMES: dict[str, list[tuple[str, int]]] = {
    "python": [(r"\bos\.(?:path\.(?:exists|isfile|isdir|islink|getsize|getmtime|getctime|lexists|basename"
                r"|dirname|split|splitext)|stat|lstat|access|makedirs|mkdir)|\bshutil\.disk_usage", 0)],
    "ruby": [(r"\b(?:File|Dir|FileTest)\.(?:exist\?|exists\?|file\?|directory\?|size\??|mtime|basename|dirname"
              r"|extname|stat|mkdir)|\bFileUtils\.mkdir", 0)],
    "js": [(r"(?<![\w$])(?:[\w$]+\.)*(?:existsSync|statSync|lstatSync|accessSync|mkdirSync|basename|dirname"
            r"|extname)", 0)],
    "php": [(r"(?<![\w$>:])(?:file_exists|is_file|is_dir|filesize|filemtime|basename|dirname|pathinfo|mkdir"
             r"|realpath)", 0)],
    "lua": [],
    "r": [(r"(?<![\w.:])(?:file\.exists|dir\.exists|file\.info|file\.size|basename|dirname|dir\.create)", 0)],
}  # fmt: skip
# Methods whose receiver is a path tested or named only: Path(p).exists().
_NAME_METHODS = r"\.(?:exists|is_file|is_dir|is_symlink|stat|lstat|mkdir|name|stem|suffix|parent)\b"


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
# Python's calls that open, list, copy, change folder or run (the heads above, Path's read methods, exec and kin):
# in an f-string's code they make the program build what it opens (_Scan.judge).
_PY_HEADS = "|".join(
    [
        *(head for head, *_ in _READS["python"]),
        *(head for head, *_ in _LISTS["python"]),
        *(head for head, *_ in _COPIES["python"]),
        *(head for head, _kind in _RUNS["python"]),
        _CHDIR["python"],
        r"\bPath",
    ]
)
_PY_METHODS = r"\.(?:read_text|read_bytes|open|read|readlines|iterdir|glob|rglob)"
# The dotted names of the calls the scan judges itself (reads, lists, copies, runs, folder changes, writes, tests).
_JUDGED = re.compile("|".join([_PY_HEADS, *(h for h, _i in _WRITES["python"]), *(h for h, _i in _NAMES["python"])]))
_ACTS = rf"(?:{_PY_HEADS}|{_PY_METHODS})\s*\(|{_RUNS_CODE['python']}"


class _Scan:
    """One program's text in one language."""

    def __init__(self, text: str, lang: str) -> None:
        self.text, self.lang = text, lang
        self.spans, self.masked = _spans(text, lang)
        # pathlib's Path under another name: `from pathlib import Path as P`, `import pathlib as pl` (`pl.Path`)
        self.path_names: set[str] = set()
        if lang == "python":
            for m in re.finditer(r"\bfrom\s+pathlib\s+import\s+([^\n;]+)", self.masked):
                for part in m[1].strip("() ").split(","):
                    got = re.fullmatch(r"\s*(?:Pure(?:Posix)?)?Path\s+as\s+([A-Za-z_]\w*)\s*", part)
                    if got:
                        self.path_names.add(got[1])
            for m in re.finditer(r"\bimport\s+pathlib\s+as\s+([A-Za-z_]\w*)", self.masked):
                self.path_names |= {f"{m[1]}.Path", f"{m[1]}.PurePath"}
        self.starts = {a: b for a, b in self.spans}
        self.argv_read = False
        self.chdirs: list[_Value] = []
        self.used: set[tuple[int, int]] = set()  # the literals a judged call's path arguments spell

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
            decoded = _unescape(text, self.lang)
            if decoded is None:
                return _BUILT  # an escape the scan can't decode (`\N{…}` in Python): a path it can't read
            text = decoded
        if cut:
            return _Value("pre", text) if text else _BUILT
        return _Value("lit", text)

    def static_end(self, a: int, b: int) -> int:
        """Where literal ``text[a:b]`` stops being text: its first interpolation (``{``, ``${``, ``#{``, ``$x``),
        else its end."""
        token = self.text[a:b]
        prefix = self.text[max(0, a - 2) : a].lower() if self.lang == "python" else ""
        pattern = {
            "python": r"(?<!\{)\{(?!\{)" if "f" in prefix else None,
            "js": r"\$\{" if token[:1] == "`" else None,
            "ruby": r"#\{|#[@$]" if token[:1] in '"`' else None,
            "php": r"\$[A-Za-z_{]|\{\$" if token[:1] in '"`' else None,
        }.get(self.lang)
        m = re.search(pattern, token) if pattern else None
        return a + m.start() if m else b

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
            self.used.add((a, b))
            return [self._literal(a, b)]
        if self.lang == "python" and re.match(r"[rRbBuUfF]{1,2}['\"]", self.text[a:b]):
            q = a + len(re.match(r"[rRbBuUfF]{1,2}", self.text[a:b])[0])  # type: ignore[index]
            if q in self.starts and self.starts[q] == b:
                self.used.add((q, b))
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
            if re.fullmatch(_JOINS[self.lang], name) or name in self.path_names:
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
            for m in re.finditer(_FOR_TARGETS, self.masked):
                targets = [t.strip() for t in m[1].split(",")]
                if name not in targets:
                    continue  # a loop variable: what it takes, from what the loop goes over
                items = self._items(m.end(), self._iter_end(m.end()), depth + 1)
                k = targets.index(name)
                for item in items if items is not None else [None]:
                    if item is None or (len(targets) > 1 and len(item) != len(targets)):
                        found.append(_BUILT)
                    elif len(targets) > 1:
                        found += self.values(*item[k], depth + 1)
                    else:
                        found += self.values(item[0][0], item[-1][1], depth + 1)
            return found or [_BUILT]
        return [_BUILT]

    def _iter_end(self, j: int) -> int:
        """Where the expression a ``for … in`` loop goes over, from ``j``, ends: a ``:`` (a statement), an ``if`` or
        ``for`` (a comprehension), the bracket closing a comprehension, or the end of the line."""
        depth = 0
        while j < len(self.masked):
            if j in self.starts:
                j = self.starts[j]
                continue
            c = self.masked[j]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    return j
                depth -= 1
            elif depth == 0 and (c in ":\n" or re.match(r"\s(?:if|for)\b", self.masked[j:])):
                return j
            j += 1
        return j

    def _items(self, a: int, b: int, depth: int, part: str = "") -> list[list[tuple[int, int]]] | None:
        """What a loop over ``text[a:b]`` takes, each item as its elements (one, or a tuple's): a list, tuple or
        set it spells, a dict's keys (``part`` "values", "items": its values, its pairs), ``.keys()``,
        ``.values()``, ``.items()``, ``sorted(…)``/``list(…)`` of one, or a variable the program sets to one.
        None for anything else."""
        while a < b and self.masked[a].isspace():
            a += 1
        while b > a and self.masked[b - 1].isspace():
            b -= 1
        if a >= b or depth > _MAX_DEPTH:
            return None
        expr = self.masked[a:b]
        method = re.search(r"\.(keys|values|items)\(\s*\)$", expr)
        if method is not None:
            return self._items(a, a + method.start(), depth + 1, "" if method[1] == "keys" else method[1])
        call = re.match(r"(?:sorted|list|tuple|set|reversed|iter)\s*\(", expr)
        if call is not None and _close(self.masked, a + call.end() - 1) == b:
            args = self.args(a + call.end() - 1)
            return self._items(*args[0], depth + 1, part) if len(args) == 1 else None
        if expr[0] in "[({" and _close(self.masked, a) == b:
            out: list[list[tuple[int, int]]] = []
            for x, y in _split_top(self.masked, a + 1, b - 1, ","):
                while x < y and self.masked[x].isspace():
                    x += 1
                y = x + len(self.masked[x:y].rstrip())
                if x >= y:
                    continue
                pair = _split_top(self.masked, x, y, ":") if expr[0] == "{" else [(x, y)]
                if len(pair) == 2:  # a dict's entry
                    out.append({"values": [pair[1]], "items": pair}.get(part, [pair[0]]))
                elif self.masked[x] in "([" and _close(self.masked, x) == y:  # a tuple: its elements
                    out.append(_split_top(self.masked, x + 1, y - 1, ","))
                else:
                    out.append([(x, y)])
            return out
        if re.fullmatch(_IDENT[self.lang], expr):
            found: list[list[tuple[int, int]]] = []
            for m in re.finditer(_ASSIGN[self.lang].format(name=re.escape(expr)), self.masked):
                end = _statement_end(self.masked, m.end(), self.lang)
                items = self._items(m.end(), end, depth + 1, part)
                if items is None:
                    return None
                found += items
            return found or None
        return None

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

    def _mode(self, args: list[tuple[int, int]], index: int | None, keyword: str | None) -> tuple[bool, bool]:
        """``(reads, writes)`` of an open call by its mode: ``"w"``, ``"a"``, ``"x"`` only write; ``"r+"``,
        ``"w+"``, ``"a+"`` do both; ``"r"``, none or one the program builds read."""
        if self._mode_writes(args, index, keyword):
            return False, True
        mode: tuple[int, int] | None = None
        positional = [(x, y) for x, y in args if not re.match(r"\s*:?\w+\s*(?:=(?!=)|:(?!:))", self.masked[x:y])]
        if keyword is not None:
            for x, y in args:
                m = re.match(rf"\s*:?{keyword}\s*(?:=|:)\s*", self.masked[x:y])
                if m is not None:
                    mode = (x + m.end(), y)
        if mode is None and index is not None and index < len(positional):
            mode = positional[index]
        values = self.values(*mode) if mode is not None else []
        return True, any(v.kind == "lit" and "+" in v.text for v in values)

    def _os_open_writes(self, args: list[tuple[int, int]]) -> tuple[bool, bool]:
        """``(reads, writes)`` of ``os.open(p, flags)``: it only writes when its flags are a plain ``|`` join of
        names with ``O_WRONLY`` in it and no ``O_RDWR``; anything else (an expression, ``O_RDWR``) reads."""
        flags = self.masked[args[1][0] : args[1][1]] if len(args) > 1 else ""
        names = re.fullmatch(r"\s*(?:os\.)?O_[A-Z]+(?:\s*\|\s*(?:os\.)?O_[A-Z]+)*\s*", flags)
        if names is not None and "O_WRONLY" in flags and "O_RDWR" not in flags:
            return False, True
        return True, "O_RDWR" in flags

    def judge(self, out: ProgramReads) -> None:  # noqa: C901 - every kind of call a program reads files with
        """Every read, write, copy, listing and command in the program, into ``out``, and whether its text names
        the KB anywhere none of them spells a path (:attr:`ProgramReads.unaccounted`)."""
        paths: list[_Value] = []
        writes: list[_Value] = []
        for head, index, mode_index, keyword in _READS[self.lang]:
            for _start, end in self._heads(head):
                args = self._call_args(end)
                if args is None:
                    continue
                if "os\\.open" in head:
                    reading, writing = self._os_open_writes(args)
                elif mode_index is not None:
                    reading, writing = self._mode(args, mode_index, keyword)
                else:
                    reading, writing = True, False
                found = self._path_args(args, index)
                if "require|" in head and self.lang == "js":  # a module by name is no file
                    found = [v for v in found if v.kind != "lit" or v.text.startswith((".", "/"))]
                if "fileinput" in head and not args:
                    self.argv_read = True
                paths += found if reading else []
                writes += found if writing else []
        for head, index in _WRITES[self.lang]:
            for _start, end in self._heads(head):
                args = self._call_args(end)
                if args is not None:
                    writes += self._path_args(args, index) or ([_BUILT] if index != 99 else [])
        for head, index in _NAMES[self.lang]:  # names only: what they spell is no read
            for _start, end in self._heads(head):
                args = self._call_args(end)
                if args is not None:
                    self._path_args(args, index)
        # Path(…).read_text() and kin: the receiver is the path.
        if self.lang in ("python", "ruby"):
            paren = r"\s*\(" if self.lang == "python" else r"\b\s*\(?"
            method = rf"\.(?:read_text|read_bytes|open|read|readlines|each_line|binread){paren}"
            for m in re.finditer(method, self.masked):
                receiver = self._receiver(m.start())
                if receiver is None:
                    continue
                reading, writing = True, False
                if m[0].startswith(".open") and self._is_module(*receiver):
                    continue  # `gzip.open(p)`, `Kernel.open(p)`: a function, judged by its heads (or the KB rule)
                if m[0].startswith(".open") and m[0].endswith("("):
                    reading, writing = self._mode(self.args(m.end() - 1), 0, "mode")
                values = self.values(*receiver)
                if not m[0].startswith((".read_text", ".read_bytes", ".open")):
                    # `f.read()` on a file object: no new path, unless the receiver is a call naming one that the
                    # heads don't judge (`open("knowledge-base/a.md").read()` is judged as the open it is)
                    judged = re.match(rf"(?:{_READ_HEADS[self.lang]})\s*\(", self.masked[receiver[0] : receiver[1]])
                    values = [v for v in values if v.kind != "built" or (self._names_kb(*receiver) and not judged)]
                paths += values if reading else []
                writes += values if writing else []
            for m in re.finditer(_WRITE_METHODS, self.masked):
                receiver = self._receiver(m.start())
                writes += self.values(*receiver) if receiver is not None else [_BUILT]
            for m in re.finditer(_NAME_METHODS, self.masked):
                receiver = self._receiver(m.start())
                if receiver is not None:
                    self.values(*receiver)
        copied: list[_Value] = []
        for head, src, dst in _COPIES[self.lang]:
            for _start, end in self._heads(head):
                args = self._call_args(end)
                if args is None:
                    continue
                writes += self._path_args(args, dst) or [_BUILT]  # judged where it lands (live_run, raw_read)
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
        self.chdirs = chdirs
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
        for value in writes:
            if value.kind == "lit":
                out.writes.append(value.text)
            else:
                out.writes_built = True
        for root, how in listed:  # a listing reads the names in a folder, as `ls` does
            pattern = bool(_GLOB.search(root))
            out.spelled.append((root, FILE if pattern and how == FOLDER else how, GLOB if pattern else AS_IS))
        self._chdirs(chdirs, out)
        if _EXPANDS_HOME.search(self.text):  # Path("~/x").expanduser(): the `~` is the home folder
            out.spelled += [(p, FILE, TILDE) for p in out.literal if p.startswith("~")]
        if self.lang == "python" and re.search(_ACTS, self._fstring_code()):
            out.built = True  # an f-string's `{…}` opens or runs something: code the scan doesn't follow
        if self.lang == "python" and not out.built:
            from scout.kb.python_text import text_checker

            checker = text_checker(self.text)
            if checker is not None and checker.hides_reads(lambda name: _JUDGED.fullmatch(name) is not None):
                out.built = True  # a call that may open what it's handed, with what the scan can't spell out
        out.unaccounted = self._unaccounted(self._data_literals(out))

    def _is_module(self, a: int, b: int) -> bool:
        """Whether ``text[a:b]`` names a module or a class, not a value: a dotted name the program never assigns
        (``gzip``, ``os.path``), or a constant (``Kernel``, ``Zlib::GzipWriter``)."""
        expr = self.masked[a:b].strip()
        if not re.fullmatch(r"[A-Za-z_][\w.:]*", expr):
            return False
        if expr[0].isupper() and self.lang in ("ruby", "js", "php"):
            return True
        root = re.split(r"[.:]", expr)[0]
        return not re.search(
            _ASSIGN[self.lang].format(name=re.escape(root.lstrip("$"))), self.masked
        ) and not re.search(rf"\bfor\s+{re.escape(root)}\b|\bas\s+{re.escape(root)}\b", self.masked)

    def _data_literals(self, out: ProgramReads) -> PythonText | None:
        """The judge of whether a literal no judged call takes is only text (:mod:`scout.kb.python_text`): Python
        only, and only for a program that opens no path it builds (it could have written the KB path to a file and
        read it back), runs no shell command, and names no module that starts programs or runs code. None when
        none of the program's literals may count as text."""
        if self.lang != "python" or out.built or out.programs:
            return None
        if re.search(_RUNS_CODE["python"], self.masked) or re.search(_SPAWNS, self.masked):
            return None
        from scout.kb.python_text import text_checker

        return text_checker(self.text)

    def _fstring_code(self) -> str:
        """The code in the program's f-strings' ``{…}``, which the scan doesn't read (Python)."""
        return "\n".join(self.text[x:y] for a, b in self.spans for x, y in self._interpolations(a, b))

    def _interpolations(self, a: int, b: int) -> list[tuple[int, int]]:
        """Where a Python f-string token ``text[a:b]`` has code (``{…}``, not ``{{``); none for another literal."""
        prefix = re.search(r"[rRbBuUfF]{0,2}$", self.text[max(0, a - 2) : a])
        if self.lang != "python" or prefix is None or "f" not in prefix[0].lower():
            return []
        quote = 3 if self.text.startswith(('"""', "'''"), a) else 1
        body_start, body_end = a + quote, max(a + quote, b - quote)
        out, i = [], body_start
        while i < body_end:
            if self.text.startswith(("{{", "}}"), i):
                i += 2
            elif self.text[i] == "{":
                depth, j = 1, i + 1
                while j < body_end and depth:
                    depth += {"{": 1, "}": -1}.get(self.text[j], 0)
                    j += 1
                out.append((i, j))
                i = j
            else:
                i += 1
        return out

    def _interpolated(self, a: int, b: int, at: int) -> bool:
        return any(x <= at < y for x, y in self._interpolations(a, b))

    def _names_kb(self, a: int, b: int) -> bool:
        return bool(_KB_NAME.search(self.text[a:b]))

    def _unaccounted(self, data: PythonText | None = None) -> bool:
        """Whether the program names the KB anywhere but in a literal a judged call spells a path with: in code
        (an alias, `getattr`, a name), a comment, a literal no judged call takes (``exec("…")``), the code inside
        an f-string or a template, or behind an escape (``"\x6bnowledge-base"``). That covers what the scan can't
        follow, a lexer the program fools included: fail closed. With ``data`` (:meth:`_data_literals`), a literal
        no judged call takes that ``data`` shows is only ever text names no read (a KB name in an f-string's code
        still does)."""
        inside = sorted(self.spans)
        for m in _KB_NAME.finditer(self.text):
            span = next(((a, b) for a, b in inside if a <= m.start() < b), None)
            if span is None:
                return True
            if span not in self.used:
                if data is not None and not self._interpolated(*span, m.start()) and data.is_text(span[0]):
                    continue
                return True
            if m.start() >= self.static_end(*span):
                return True
        for a, b in self.spans:  # what an escape spells: `"\x6bnowledge-base"`, `"\153nowledge"`
            token = self.text[a:b]
            if (a, b) in self.used or "\\" not in token or (data is not None and data.is_text(a)):
                continue
            decoded = _unescape(token, self.lang)
            if decoded is None or _KB_NAME.search(decoded):
                return True  # one that spells the KB, or one the scan can't decode
        return False

    @staticmethod
    def _chdirs(chdirs: list[_Value], out: ProgramReads) -> None:
        """A program that changes its folder (``os.chdir``, ``Dir.chdir``, ``setwd``) reads its relative paths
        from there too: each is judged from both folders. A literal folder is a folder read when the program reads
        or lists anything after it (``os.chdir("knowledge-base")`` then ``open(…)`` or ``os.listdir(".")``, as
        ``cd knowledge-base && ls`` is); testing a name there (``os.path.exists``) is no read. A folder the program
        builds is a path built at run time."""
        reads = bool(out.literal or out.built or out.spelled)
        for chdir in chdirs:
            if chdir.kind != "lit":
                out.built = True
                continue
            here = chdir.text.rstrip("/") or "/"
            out.literal += [posixpath.join(here, p) for p in out.literal if not p.startswith(("/", "~"))]
            out.writes += [posixpath.join(here, p) for p in out.writes if not p.startswith(("/", "~"))]
            out.spelled += [
                (here if p == "." else posixpath.join(here, p), how, live)
                for p, how, live in list(out.spelled)
                if live != TILDE and not p.startswith(("/", "~"))
            ]
            if reads:
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
