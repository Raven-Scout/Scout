"""Readers whose files aren't simply their operands: what else counts as reading a file around ``scoutctl kb``.

:mod:`scout.kb.raw_read` judges a reader's operands (``cat x``, ``grep -r y dir``). The commands here read
files another way, and both of the gate's judges ask this module about them (the shell's spelling in
:func:`scout.kb.raw_read.raw_kb_read`, the path the shell hands over in :mod:`scout.kb.live_run`):

- a copy or an archive reads its sources, all of them below a folder when it is recursive (``cp -r``,
  ``rsync -a``, ``ditto``, ``tar c``, ``zip -r``, ``pax -w``, ``cpio -o``, ``split``, ``gzip -c``); a copy
  into the KB is a write there, not a read out of it;
- ``diff -r`` reads every file below each folder it compares;
- ``dd if=FILE``, ``source FILE`` and ``. FILE``;
- a program names files in its own text: sed's ``r FILE`` / ``R FILE``, awk's ``getline < "FILE"`` (and
  ``getline < f`` with ``f`` set in the program or by ``-v``), perl's ``open(F, "<", "FILE")`` and the files
  ``-n``/``-p``/``<>`` read. Such a path is opened as written: no glob, ``~`` or ``$``. One the program builds
  at run time (``"kn" "owledge-base"``, ``open(F, $x)``) can't be read here: :attr:`OtherReads.built`. And the
  shell commands a program runs (awk's ``system("…")`` and ``"cmd" | getline``, perl's ``system``, backticks
  and piped ``open``, sed's ``e``) are commands of their own: :attr:`OtherReads.programs`.

Pure: it judges words and does no I/O.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from dataclasses import dataclass, field

# How a path is read (scout.kb.live_run.KB_READ_HOW): "file" opens it, "covers" reads every file below it.
FILE, COVERS = "file", "covers"


@dataclass
class OtherReads:
    """What one command reads beyond its operands."""

    reads: list[tuple[str, str]] = field(default_factory=list)  # (word as the shell hands it, FILE | COVERS)
    literal: list[str] = field(default_factory=list)  # paths a program opens exactly as written
    dests: list[str] = field(default_factory=list)  # where a copy writes: one in the KB makes ``reads`` none
    built: bool = False  # a program opens a path it builds at run time
    stdin_list: bool = False  # it reads the files named on its stdin (``find . | cpio -o``)
    programs: list[str] = field(default_factory=list)  # shell commands a program runs
    # Its data passes through a program it names (tar's `-I`, `--to-command`): a copy into the KB still reads
    # what it copies, since that program sees it on the way.
    piped: bool = False
    # Paths a program takes from the environment (awk's `ENVIRON["F"]`): the variable's name, judged as `$F`.
    environ: list[str] = field(default_factory=list)


def into_kb_counts(other: OtherReads) -> bool:
    """Whether a copy whose target is in the KB reads nothing out of it (true unless its data is piped)."""
    return not other.piped


# ----- options ---------------------------------------------------------------------------------------------


_Event = tuple[str | None, str | None]  # (option, its value) or (None, an operand), in the order given


def _events(args: list[str], takes: set[str]) -> list[_Event]:
    """``args`` read getopt-style, in order: ``-xVALUE``, ``-x VALUE``, ``--long=VALUE`` and ``--long VALUE``
    for an option in ``takes``, bundles (``-avz``), and ``--`` ending the options. Options and operands mix
    (GNU)."""
    out: list[_Event] = []
    i, ended = 0, False
    while i < len(args):
        a = args[i]
        i += 1
        if ended or a == "-" or not a.startswith("-"):
            out.append((None, a))
        elif a == "--":
            ended = True
        elif a.startswith("--"):
            flag, eq, value = a.partition("=")
            if eq:
                out.append((flag, value))
            elif flag in takes and i < len(args):
                out.append((flag, args[i]))
                i += 1
            else:
                out.append((flag, None))
        else:
            for j, ch in enumerate(a[1:], 1):
                flag = "-" + ch
                if flag not in takes:
                    out.append((flag, None))
                    continue
                if j < len(a) - 1:
                    out.append((flag, a[j + 1 :]))
                elif i < len(args):
                    out.append((flag, args[i]))
                    i += 1
                else:
                    out.append((flag, None))
                break
    return out


def _getopt(args: list[str], takes: set[str]) -> tuple[list[tuple[str, str | None]], list[str]]:
    """``(options, operands)`` of :func:`_events`."""
    events = _events(args, takes)
    return [(f, v) for f, v in events if f is not None], [v for f, v in events if f is None and v is not None]


def _has(opts: list[tuple[str, str | None]], *flags: str) -> bool:
    return any(f in flags for f, _ in opts)


def _values(opts: list[tuple[str, str | None]], *flags: str) -> list[str]:
    return [v for f, v in opts if f in flags and v is not None]


# ----- copies and archives ---------------------------------------------------------------------------------

_CP_VALUES = {"-t", "--target-directory", "-S", "--suffix"}
_INSTALL_VALUES = {"-m", "-o", "-g", "-t", "-S", "-B", "-f", "--mode", "--owner", "--group", "--target-directory",
                   "--suffix"}  # fmt: skip
_RSYNC_VALUES = {"-e", "--rsh", "-f", "--filter", "--exclude", "--include", "--files-from", "--exclude-from",
                 "--include-from", "--link-dest", "--compare-dest", "--copy-dest", "--backup-dir", "--suffix",
                 "--chmod", "--log-file", "--partial-dir", "-T", "--temp-dir", "-B", "--block-size", "--max-size",
                 "--min-size", "--bwlimit", "--port", "--password-file", "--out-format", "--timeout",
                 "--modify-window", "-M", "--remote-option", "--usermap", "--groupmap", "--chown", "--iconv",
                 "--rsync-path"}  # fmt: skip
_SCP_VALUES = {"-P", "-i", "-o", "-F", "-c", "-l", "-S", "-J", "-D", "-X"}
_DITTO_VALUES = {"--arch", "--bom"}
_SPLIT_VALUES = {"-l", "-b", "-n", "-a", "-C", "-p", "-t", "--lines", "--bytes", "--number", "--suffix-length",
                 "--additional-suffix", "--filter", "--line-bytes", "--separator"}  # fmt: skip
_CSPLIT_VALUES = {"-f", "-n", "-b", "--prefix", "--digits", "--suffix-format"}
_COMPRESS_VALUES = {"-S", "--suffix", "-o", "-D", "-T", "-M", "--memlimit", "--threads", "--format", "-F"}
_COMPRESSORS = {"gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz", "lzma", "unlzma", "zstd", "unzstd", "lz4",
                "unlz4", "compress", "uncompress", "pigz", "unpigz", "pbzip2", "brotli"}  # fmt: skip
_TAR_VALUES = {"-f", "--file", "-C", "--directory", "-T", "--files-from", "-X", "--exclude-from", "--exclude",
               "-b", "--blocking-factor", "-H", "--format", "-I", "--use-compress-program", "-K", "-N", "--newer",
               "-L", "-V", "--label", "-F", "-g", "--listed-incremental", "--transform", "--owner", "--group",
               "--mode", "--mtime", "--strip-components", "--options", "--include", "--to-command",
               "--checkpoint-action"}  # fmt: skip
# Old-style tar (`tar cvf out.tar dir`): the bundle's letters that take the next words, in order.
_TAR_LETTER_VALUES = set("fCTXbHIKNLVFg")
_ZIP_VALUES = {"-b", "-n", "-t", "-tt", "-P", "-O", "--out", "-Z", "-s", "-sp", "-ds", "-dd"}
_PAX_VALUES = {"-f", "-s", "-x", "-o", "-b", "-B", "-G", "-T", "-U"}
_CPIO_VALUES = {"-F", "-O", "-I", "-H", "-C", "-E", "-M", "-R", "--file", "--format", "--pattern-file"}


def _recursive(opts: list[tuple[str, str | None]], letters: str, longs: tuple[str, ...]) -> bool:
    return any((f[1:] in letters and len(f) == 2) or f in longs for f, _ in opts)


def _copy(name: str, args: list[str]) -> OtherReads:
    """cp, install, rsync, scp, ditto: sources, then the target (or ``-t DIR``)."""
    takes = {"cp": _CP_VALUES, "install": _INSTALL_VALUES, "rsync": _RSYNC_VALUES, "scp": _SCP_VALUES}.get(
        name, _DITTO_VALUES
    )
    opts, plain = _getopt(args, takes)
    if name == "ditto":
        how = COVERS
    elif name == "rsync":
        how = COVERS if _recursive(opts, "ra", ("--recursive", "--archive")) else FILE
    elif name in ("cp", "scp"):
        how = COVERS if _recursive(opts, "rRa", ("--recursive", "--archive")) else FILE
    else:
        how = FILE
    target = _values(opts, "-t", "--target-directory")
    if target:
        sources, dests = plain, target
    elif len(plain) >= 2:
        sources, dests = plain[:-1], plain[-1:]
    else:
        sources, dests = (plain if name == "rsync" else []), []  # `rsync -r src` lists what it would copy
    return OtherReads(reads=[(s, how) for s in sources], dests=dests)


def _split(name: str, args: list[str]) -> OtherReads:
    """split / csplit: the file it cuts up (``-`` or none is stdin), and the prefix it writes."""
    opts, plain = _getopt(args, _SPLIT_VALUES if name in ("split", "gsplit") else _CSPLIT_VALUES)
    source = [p for p in plain[:1] if p != "-"]
    dests = plain[1:2] if name in ("split", "gsplit") else _values(opts, "-f", "--prefix")
    return OtherReads(reads=[(s, FILE) for s in source], dests=dests)


def _compressor(args: list[str]) -> OtherReads:
    """gzip and kin: each operand, every file below it with ``-r``; ``-c`` prints it, otherwise it is rewritten."""
    opts, plain = _getopt(args, _COMPRESS_VALUES)
    how = COVERS if _recursive(opts, "r", ("--recursive",)) else FILE
    return OtherReads(reads=[(p, how) for p in plain if p != "-"])


def _tar(args: list[str]) -> OtherReads:
    """tar: what ``c``/``r``/``u`` put in the archive (each from the folder ``-C`` names before it), the archive
    ``x``/``t`` read, and the archive written (its dest)."""
    words = list(args)
    events: list[_Event] = []
    if words and not words[0].startswith("-"):  # old style: `tar cvf out.tar dir`
        bundle, words = words[0], words[1:]
        for ch in bundle:
            events.append(("-" + ch, words.pop(0) if ch in _TAR_LETTER_VALUES and words else None))
    events += _events(words, _TAR_VALUES)
    opts = [(f, v) for f, v in events if f is not None]
    # In order, so `-C dir` moves the sources after it.
    base = ""
    sources: list[str] = []
    for flag, value in events:
        if flag in ("-C", "--directory") and value is not None:
            base = value if value.startswith(("/", "~")) else posixpath.join(base, value)
        elif flag is None and value is not None:
            sources.append(posixpath.join(base, value) if base and not value.startswith(("/", "~", "@")) else value)
    creates = _has(opts, "-c", "-r", "-u", "--create", "--append", "--update")
    archive = _values(opts, "-f", "--file")
    out = OtherReads()
    # A program the archive's bytes go through: a command of its own, and one that sees every file tar reads.
    filters = _values(opts, "-I", "--use-compress-program", "--to-command")
    filters += [v.split("=", 1)[1] for v in _values(opts, "--checkpoint-action") if v.startswith("exec=")]
    if filters:
        out.programs += filters
        out.piped = True
    for listed in _values(opts, "-T", "--files-from"):
        if listed == "-":
            out.stdin_list = True
        else:
            out.reads.append((listed, FILE))
            out.built = True  # the names in a list file aren't in the command
    if creates:
        out.reads += [(s.lstrip("@"), FILE if s.startswith("@") else COVERS) for s in sources]
        out.dests = [a for a in archive if a != "-"]
    else:  # extract or list: it reads the archive
        out.reads += [(a, FILE) for a in archive if a != "-"]
    return out


def _zip(args: list[str]) -> OtherReads:
    """zip ARCHIVE FILES…: the files (every file below each with ``-r``), ``-@`` names from stdin."""
    words: list[str] = []
    skipping = False
    for a in args:  # `-x`/`-i` take patterns up to the next option
        if a in ("-x", "-i", "--exclude", "--include"):
            skipping = True
        elif skipping and not a.startswith("-"):
            continue
        else:
            skipping = False
            words.append(a)
    opts, plain = _getopt(words, _ZIP_VALUES)
    how = COVERS if _recursive(opts, "rR", ("--recurse-paths", "--recurse-patterns")) else FILE
    archive, sources = plain[:1], plain[1:]
    return OtherReads(
        reads=[(s, how) for s in sources if s != "-"],
        dests=[a for a in archive if a != "-"],
        stdin_list=_has(opts, "-@", "--names-stdin"),
    )


def _pax(args: list[str]) -> OtherReads:
    """pax -w [FILE…] archives them (stdin's names when none), -rw copies them into the last; -r reads -f."""
    opts, plain = _getopt(args, _PAX_VALUES)
    writes, reads = _has(opts, "-w"), _has(opts, "-r")
    how = FILE if _has(opts, "-d") else COVERS
    archive = _values(opts, "-f")
    if writes:
        sources, dests = (plain[:-1], plain[-1:]) if reads else (plain, archive)
        return OtherReads(reads=[(s, how) for s in sources], dests=dests, stdin_list=not sources)
    return OtherReads(reads=[(a, FILE) for a in archive if a != "-"])


def _cpio(args: list[str]) -> OtherReads:
    """cpio -o / -p: the files named on stdin; -i reads the archive (-I / -F)."""
    opts, plain = _getopt(args, _CPIO_VALUES)
    if _has(opts, "-o", "-p", "--create", "--pass-through"):
        dests = plain[:1] if _has(opts, "-p", "--pass-through") else _values(opts, "-O", "-F", "--file")
        return OtherReads(dests=dests, stdin_list=True)
    return OtherReads(reads=[(a, FILE) for a in _values(opts, "-I", "-F", "--file") if a != "-"])


# ----- diff -r, dd, source ---------------------------------------------------------------------------------

DIFF_VALUES = {"-x", "--exclude", "-X", "--exclude-from", "-I", "--ignore-matching-lines", "-S", "--starting-file",
               "-L", "--label", "-U", "-C", "-W", "--width", "-F", "--show-function-line", "-D", "--ifdef",
               "--horizon-lines", "--line-format", "--old-line-format", "--new-line-format",
               "--unchanged-line-format", "--old-group-format", "--new-group-format", "--changed-group-format",
               "--unchanged-group-format", "--tabsize", "--palette"}  # fmt: skip


def _diff(args: list[str]) -> OtherReads | None:
    """diff -r compares every file below each folder: each operand covers what is below it."""
    opts, plain = _getopt(args, DIFF_VALUES)
    if not _recursive(opts, "r", ("--recursive",)):
        return None
    return OtherReads(reads=[(p, COVERS) for p in plain if p != "-"])


# ----- programs: sed, awk, perl ----------------------------------------------------------------------------


def _scripts(args: list[str], script_flags: set[str], file_flags: set[str]) -> tuple[list[str], list[str], list[str]]:
    """``(inline programs, program files, operands)`` of sed/awk-style ``args``: ``-e``/``--expression`` values,
    else the first operand."""
    takes = script_flags | file_flags | {"-v", "--assign", "-F", "--field-separator", "-l", "--line-length"}
    opts, plain = _getopt(args, takes)
    programs = _values(opts, *script_flags)
    files = _values(opts, *file_flags)
    if not programs and not files and plain:
        programs, plain = plain[:1], plain[1:]
    return programs, files, plain


def _sed(args: list[str]) -> OtherReads:
    """sed's ``r FILE`` and ``R FILE`` read a file named in its script (to the end of the line, as written);
    ``e COMMAND`` runs one; ``-f FILE`` reads its script from a file."""
    args = [a for i, a in enumerate(args) if not (i and args[i - 1] == "-i" and (not a or a.startswith(".")))]
    programs, files, _plain = _scripts(args, {"-e", "--expression"}, {"-f", "--file"})
    # A script file's text isn't in the command: what its `r` commands read is unknown (as tar's -T list is).
    out = OtherReads(reads=[(f, FILE) for f in files if f != "-"], built=bool(files))
    for program in programs:
        for line in program.split("\n"):
            _sed_line(line, out)
    return out


_SED_ADDR = r"(?:\d+(?:~\d+)?|\$|/(?:[^/\\\n]|\\.)*/I?M?|\\(.)(?:(?!\1).)*\1)"
_SED_HEAD = re.compile(rf"\s*(?:{_SED_ADDR}(?:\s*,\s*(?:{_SED_ADDR}|[+~]\d+))?)?\s*!*\s*")


def _sed_line(line: str, out: OtherReads) -> None:
    i = 0
    while i < len(line):
        m = _SED_HEAD.match(line, i)
        i = m.end() if m else i
        if i >= len(line):
            return
        c = line[i]
        if c in ";{}":
            i += 1
        elif c in "rR":
            path = line[i + 1 :].strip()
            if path and path not in ("/dev/stdin", "-"):
                out.literal.append(path)
            return
        elif c == "e":
            command = line[i + 1 :].strip()
            if command:
                out.programs.append(command)
            return
        elif c in "wWaic":  # to the end of the line: a file it writes, or text
            return
        elif c in "sy" and i + 1 < len(line):
            delim, j, parts = line[i + 1], i + 2, 0
            while j < len(line) and parts < 2:  # the opening delimiter's two partners
                if line[j] == "\\":
                    j += 2
                    continue
                if line[j] == delim:
                    parts += 1
                j += 1
            flags = re.match(r"[gpIiMme0-9]*", line[j:])
            j += flags.end() if flags else 0
            if j < len(line) and line[j] == "w":  # `s/a/b/w file`: writes, to the end of the line
                return
            i = j
        elif c in "btT:":  # a label, to the next `;`
            end = line.find(";", i)
            i = len(line) if end < 0 else end + 1
        else:
            i += 1


_AWK_STRING = r'"(?:[^"\\\n]|\\.)*"'
_GETLINE_FROM = re.compile(r"\bgetline\b\s*(?:\$?[A-Za-z_][A-Za-z0-9_]*(?:\s*\[[^\]]*\])?)?\s*<(?!=)\s*")
_AWK_PIPE_GETLINE = re.compile(r"\|&?\s*getline\b")
_AWK_SYSTEM = re.compile(r"\bsystem\s*\(")
_AWK_PRINT_PIPE = re.compile(r"\bprintf?\b")
# One piece of an awk string expression: a literal, ENVIRON["NAME"], or a variable.
_AWK_TOKEN = re.compile(rf'\s*(?:({_AWK_STRING})|ENVIRON\s*\[\s*"([A-Za-z_]\w*)"\s*\]|([A-Za-z_][A-Za-z0-9_]*))')
# What ENVIRON["NAME"] stands for in a value: the shell's `$NAME` (see OtherReads.environ).
_ENV_MARK = re.compile("\0env:([A-Za-z_][A-Za-z0-9_]*)\0")
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
_AWK_LIMIT = 16  # values an expression may take before it counts as built


def _awk_unescape(literal: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), literal[1:-1])


def _awk(args: list[str]) -> OtherReads:
    """awk's ``getline < FILE`` reads that file; a command it pipes into ``getline``, out of ``print``, or runs
    with ``system()`` is a command of its own (and what it prints into a shell is too). A file or a command is
    worked out from string literals, the variables the program, ``-v`` or an ``x=y`` operand set to them, and
    ``ENVIRON["NAME"]``, joined as awk joins them; anything else is built at run time. ``-f FILE`` reads its
    program from a file."""
    opts, _ = _getopt(args, {"-f", "--file", "-v", "--assign", "-F", "--field-separator", "-e", "--source"})
    programs, files, plain = _scripts(args, {"-e", "--source"}, {"-f", "--file"})
    out = OtherReads(reads=[(f, FILE) for f in files if f != "-"], built=bool(files))  # its program isn't here
    assigned = [v for v in _values(opts, "-v", "--assign") if "=" in v]
    assigned += [p for p in plain if re.match(r"^[A-Za-z_]\w*=", p)]  # `awk '…' f=x file`: an assignment
    given = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in assigned}
    for program in programs:
        _awk_program(program, given, out)
    return out


def _awk_layout(program: str) -> tuple[list[bool], list[int]]:
    """For each character of ``program``: whether it is inside a string literal, and its paren depth."""
    in_string, depth = [False] * len(program), [0] * len(program)
    level, quoted, i = 0, False, 0
    while i < len(program):
        c = program[i]
        in_string[i], depth[i] = quoted, level
        if quoted and c == "\\" and i + 1 < len(program):
            in_string[i + 1], depth[i + 1] = True, level
            i += 2
            continue
        if c == '"':
            quoted = not quoted
            in_string[i] = True
        elif not quoted and c == "(":
            level += 1
        elif not quoted and c == ")":
            level -= 1
            depth[i] = level
        i += 1
    return in_string, depth


def _awk_forward(program: str, at: int, layout: tuple[list[bool], list[int]], *, commas: bool = False) -> int:
    """Where the expression that starts at ``at`` ends: at a top-level `;`, `}`, newline, `|`, `>`, `&&`, `,`
    (unless ``commas``: a print's arguments), or the `)` that closes the parens it sits in."""
    in_string, depth = layout
    base = depth[at] if at < len(program) else 0
    stops = ";}\n|>" if commas else ";}\n,|>"
    for i in range(at, len(program)):
        if in_string[i]:
            continue
        if depth[i] < base or (depth[i] == base and (program[i] in stops or program.startswith("&&", i))):
            return i
    return len(program)


def _awk_backward(program: str, at: int, layout: tuple[list[bool], list[int]]) -> int:
    """Where the expression that ends just before ``at`` (a `|`) starts: after a top-level `;`, `{`, `}`,
    newline, `,`, `=` or `(` of its own level, or the start."""
    in_string, depth = layout
    base = depth[at - 1] if at else 0
    i = at - 1
    while i >= 0:
        if not in_string[i]:
            c = program[i]
            if c == "(" and depth[i] < base or (depth[i] == base and c in ";{}\n,"):
                return i + 1
            if depth[i] == base and c == "=" and (i == 0 or program[i - 1] not in "=!<>") and program[i + 1] != "=":
                return i + 1
            if depth[i] < base:
                return i + 1
        i -= 1
    return 0


def _awk_eval(expr: str, program: str, given: dict[str, str], depth: int = 0) -> list[str] | None:
    """The strings an awk expression can be (see :func:`_awk`), or None when any part is something else."""
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")") and _awk_layout(expr)[1][-1] == 0:
        expr = expr[1:-1].strip()
    if not expr or depth > 3:
        return None
    values, i = [""], 0
    while i < len(expr):
        m = _AWK_TOKEN.match(expr, i)
        if m is None or m.end() == i:
            return None
        if m[1] is not None:
            part: list[str] | None = [_awk_unescape(m[1])]
        elif m[2] is not None:
            part = [f"\0env:{m[2]}\0"]
        else:
            part = _awk_values(program, m[3], given, depth + 1)
        if part is None:
            return None
        values = [v + p for v in values for p in part]
        if len(values) > _AWK_LIMIT:
            return None
        i = m.end()
        while i < len(expr) and expr[i].isspace():
            i += 1
    return values


def _awk_values(program: str, name: str, given: dict[str, str], depth: int = 0) -> list[str] | None:
    """The values variable ``name`` takes in ``program`` (and from ``-v``); None when one is built, or when it
    is never set (an awk builtin such as ``ARGV``, or a name the gate can't see filled)."""
    values = [given[name]] if name in given else []
    layout = _awk_layout(program)
    for m in re.finditer(rf"(?<![\w$.\"]){re.escape(name)}\s*=(?!=)", program):
        if layout[0][m.start()]:
            continue  # inside a string
        found = _awk_eval(program[m.end() : _awk_forward(program, m.end(), layout)], program, given, depth)
        if found is None:
            return None
        values += found
    return values or None


def _awk_program(program: str, given: dict[str, str], out: OtherReads) -> None:
    layout = _awk_layout(program)

    def paths(expr: str) -> None:
        """A file it reads: judged by each value, ENVIRON's as the shell's `$NAME`."""
        found = _awk_eval(expr, program, given)
        if found is None:
            out.built = True
            return
        for value in found:
            names = _ENV_MARK.findall(value)
            if names and _ENV_MARK.fullmatch(value):
                out.environ.append(names[0])
            elif names:
                out.built = True
            else:
                out.literal.append(value)

    def commands(expr: str) -> None:
        """A command it runs: each value, judged as a command; one it builds (or takes from ENVIRON) is built."""
        found = _awk_eval(expr, program, given)
        if found is None or any(_ENV_MARK.search(v) for v in found):
            out.built = True
        else:
            out.programs += found

    for m in _GETLINE_FROM.finditer(program):
        if layout[0][m.start()]:
            continue
        at = m.end()
        primary = re.match(rf"\s*(?:{_AWK_STRING}|ENVIRON\s*\[[^\]]*\]|[A-Za-z_]\w*)", program[at:])
        if program[at : at + 1] == "(":  # a parenthesised file name
            end = at + 1
            while end < len(program) and (layout[1][end] > layout[1][at] or layout[0][end]):
                end += 1
            paths(program[at : end + 1])
        elif primary is not None and primary[0].strip() in ("ARGV", "FILENAME"):
            continue  # the operands, which are judged as files already
        elif primary is not None:
            paths(primary[0])
            rest = program[at + primary.end() :]
            if re.match(r"\s*(?:\[|" + _AWK_STRING + r"|[A-Za-z_(])", rest):
                out.built = True  # `getline < "a" x`: what follows may make the name longer
        else:
            out.built = True
    for m in _AWK_PIPE_GETLINE.finditer(program):
        if not layout[0][m.start()]:
            commands(program[_awk_backward(program, m.start(), layout) : m.start()])
    for m in _AWK_SYSTEM.finditer(program):
        if layout[0][m.start()]:
            continue
        end = m.end()
        while end < len(program) and (layout[1][end] >= layout[1][m.end() - 1] + 1 or layout[0][end]):
            end += 1
        commands(program[m.end() : end])
    for m in _AWK_PRINT_PIPE.finditer(program):
        if layout[0][m.start()]:
            continue
        printed_end = _awk_forward(program, m.end(), layout, commas=True)
        if not program.startswith("|", printed_end) or program.startswith("||", printed_end):
            continue
        target_at = printed_end + 1 + (program[printed_end + 1 : printed_end + 2] == "&")
        target = program[target_at : _awk_forward(program, target_at, layout)]
        commands(target)
        runs = _awk_eval(target, program, given)
        if runs is not None and any(r.split()[:1] and posixpath.basename(r.split()[0]) in _SHELLS for r in runs):
            if not any("-c" in r.split() for r in runs):  # `print "cat x" | "sh"`: what it prints is a command
                commands(program[m.end() : printed_end].replace(",", " "))


_PERL_OPEN = re.compile(r"\bopen\s*\(?\s*(?:(?:my|our|local)\s+)?(?:\$\w+|\*?\w+)\s*,\s*")
_PERL_READS_ARGV = re.compile(r"<<?>>?|<ARGV>|\bARGV\b|\$ARGV\b|\breadline\s*\(?\s*\)?\s*;|\beof\s*\(\s*\)")
_PERL_SYSTEM = re.compile(r"\b(?:system|exec)\b\s*")
_PERL_BACKTICK = re.compile(r"`([^`]*)`")
_PERL_QX = re.compile(r"\bqx\s*(?=[^\w\s])")
# `@ARGV = (…)`, `local @ARGV = …`, `push @ARGV, …`, `unshift(@ARGV, …)`: the files `<>` will read.
_PERL_SETS_ARGV = re.compile(r"@ARGV\s*=(?!=)\s*|\b(?:push|unshift)\s*\(?\s*@ARGV\s*,\s*")
_PERL_ARGV_WORDS = re.compile(r"\$ARGV(?:\[\d+\])?|shift(?:\s*\(?\s*@ARGV\s*\)?)?|\$_")
_PAIRS = {"(": ")", "{": "}", "[": "]", "<": ">"}


def _perl_quoted(text: str, at: int, interpolates: bool) -> tuple[int, str, bool] | None:
    """The quote-like string whose delimiter sits at ``text[at]`` (``q{…}``, ``qq(…)``, ``qx/…/``): where it
    ends, its text, and whether it interpolates."""
    if at >= len(text):
        return None
    opener = text[at]
    closer = _PAIRS.get(opener, opener)
    depth, i, out = 1, at + 1, []
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            out.append(text[i + 1] if text[i + 1] in (opener, closer, "\\") else text[i : i + 2])
            i += 2
            continue
        if c == closer and opener != closer:
            depth -= 1
        elif c == opener and opener != closer:
            depth += 1
        elif c == closer:
            depth = 0
        if depth == 0:
            body = "".join(out)
            return i + 1, body, interpolates and bool(re.search(r"(?<!\\)[$@]", body))
        out.append(c)
        i += 1
    return None


def _perl_literal(token: str) -> tuple[str | None, bool]:
    """A Perl string literal's text (``'…'``, ``"…"``, ``q{…}``, ``qq{…}``), and whether it interpolates (``$``
    or ``@`` in a double-quoted one). ``(None, False)`` for anything else."""
    token = token.strip()
    if token.startswith("'") and len(token) > 1 and token.endswith("'"):
        return re.sub(r"\\([\\'])", r"\1", token[1:-1]), False
    if token.startswith('"') and len(token) > 1 and token.endswith('"'):
        text = token[1:-1]
        return re.sub(r"\\(.)", r"\1", text), bool(re.search(r"(?<!\\)[$@]", text))
    m = re.match(r"(qq|q)\s*(?=[^\w\s,;])", token)
    if m is not None:
        found = _perl_quoted(token, m.end(), m[1] == "qq")
        if found is not None and found[0] == len(token):
            return found[1], found[2]
    return None, False


def _perl_args(text: str) -> list[str]:
    """The comma-separated arguments of a call, up to where it ends (a `;`, an unmatched `)`, `or`/`and`)."""
    args, depth, buf, quote, i = [], 0, [], None, 0
    while i < len(text):
        c = text[i]
        if quote:
            buf.append(c)
            if c == "\\" and i + 1 < len(text):
                buf.append(text[i + 1])
                i += 2
                continue
            quote = None if c == quote else quote
        elif c == "\\" and i + 1 < len(text):  # `q{a\}b}`: an escaped bracket opens or closes nothing
            buf.append(text[i : i + 2])
            i += 2
            continue
        elif c in "'\"":
            quote = c
            buf.append(c)
        elif c in "([{":
            depth += 1
            buf.append(c)
        elif c in ")]}":
            if depth == 0:
                break
            depth -= 1
            buf.append(c)
        elif c == ";" or (depth == 0 and re.match(r"\s+(?:or|and|\|\||&&)\b", text[i:])):
            break
        elif c == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    args.append("".join(buf).strip())
    return [a for a in args if a]


def _perl_values(arg: str, text: str, depth: int = 0) -> list[str] | None:
    """The strings an argument can be: a literal that doesn't interpolate, or a scalar the program sets to
    one (``$f = q{…}; open(F, $f)``); None for anything else."""
    value, live = _perl_literal(arg)
    if value is not None:
        return None if live else [value]
    name = re.fullmatch(r"\$([A-Za-z_]\w*)", arg.strip())
    if name is None or name[1] in ("ARGV", "_") or depth > 3:
        return None
    values: list[str] = []
    for m in re.finditer(rf"(?:\b(?:my|our|local)\s+)?\${re.escape(name[1])}\s*=(?![=~])\s*", text):
        rhs = _perl_args(text[m.end() :])
        found = _perl_values(rhs[0], text, depth + 1) if rhs else None
        if found is None:
            return None
        values += found
    return values or None


def _perl_open(args: list[str], text: str, out: OtherReads) -> None:
    if not args:
        return
    if len(args) >= 2:
        mode, live = _perl_literal(args[0])
        if mode is None or live:
            out.built = True
            return
        mode = mode.strip()
        if "|" in mode:  # `open(F, "-|", "cat", "x")`: runs a command
            _perl_command(args[1:], text, out)
            return
        if not mode.startswith(("<", "+<")) and mode:
            return  # it writes
        _perl_path(args[1], text, out)
        return
    if _PERL_ARGV_WORDS.fullmatch(args[0].strip()):
        return  # the files ARGV names are judged as operands
    found = _perl_values(args[0], text)
    if found is None:
        out.built = True
        return
    for value in (v.strip() for v in found):
        if value.startswith("|") or value.endswith("|"):
            out.programs.append(value.strip("|").strip())
        elif not value.startswith((">", "+>")):
            path = re.sub(r"^\+?<\s*", "", value)
            if path and path != "-":
                out.literal.append(path)


def _perl_path(arg: str, text: str, out: OtherReads) -> None:
    if _PERL_ARGV_WORDS.fullmatch(arg.strip()):
        return  # the files ARGV names are judged as operands
    found = _perl_values(arg, text)
    if found is None:
        out.built = True
    else:
        out.literal += [v for v in found if v and v != "-"]


def _perl_command(args: list[str], text: str, out: OtherReads) -> None:
    """``system LIST``, ``exec LIST``, a piped ``open``'s command: one string is a shell command, a list is the
    program and its arguments (judged as that command). Anything it builds at run time is built."""
    words: list[str] = []
    for arg in args:
        found = _perl_values(arg, text)
        if found is None or len(found) != 1:
            out.built = True
            return
        words.append(found[0])
    if words:
        out.programs.append(words[0] if len(words) == 1 else shlex.join(words))


def _perl(args: list[str]) -> OtherReads | None:
    """perl -e/-E: the files ``open`` names and those ``@ARGV`` is set to, its operands when it reads ``ARGV``
    (``-n``, ``-p``, ``<>``); the commands ``system``, ``exec``, backticks, ``qx`` and a piped ``open`` run. A
    path or a command built at run time is built. A script file is its own business."""
    programs: list[str] = []
    operands: list[str] = []
    loops = False
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if programs and (a == "--" or not a.startswith("-")):
            operands += args[i:] if a == "--" else [a, *args[i:]]
            break
        if a == "--" or not a.startswith("-"):
            return None  # a script file: what it reads isn't in the command
        if a.startswith(("-i", "-M", "-m", "-I", "-0", "-l", "-x", "-C", "-d", "-D")) and not re.fullmatch(
            r"-[a-zA-Z]*[eE]", a
        ):
            continue  # attached values: `-i.bak`, `-MJSON`, `-0777`
        letters = a[1:]
        loops = loops or "n" in letters or "p" in letters
        if letters.endswith(("e", "E")) and i < len(args):
            programs.append(args[i])
            i += 1
    if not programs:
        return None
    out = OtherReads()
    text = "\n".join(programs)
    if loops or _PERL_READS_ARGV.search(text):
        out.reads += [(o, FILE) for o in operands if o != "-"]
    for m in _PERL_OPEN.finditer(text):
        _perl_open(_perl_args(text[m.end() :]), text, out)
    for m in _PERL_SETS_ARGV.finditer(text):
        rest = text[m.end() :]
        qw = re.match(r"\s*qw\s*(?=[^\w\s])", rest)
        if qw is not None:  # `@ARGV = qw(a b)`
            words = _perl_quoted(rest, qw.end(), False)
            if words is None:
                out.built = True
            else:
                out.literal += words[1].split()
            continue
        listed = _perl_args(rest[1:] if rest.startswith("(") else rest)
        for arg in listed:
            _perl_path(arg, text, out)
    for m in _PERL_SYSTEM.finditer(text):
        rest = text[m.end() :]
        _perl_command(_perl_args(rest[1:] if rest.startswith("(") else rest), text, out)
    for m in _PERL_BACKTICK.finditer(text):
        if re.search(r"(?<!\\)[$@]", m[1]):
            out.built = True
        else:
            out.programs.append(m[1])
    for m in _PERL_QX.finditer(text):
        found = _perl_quoted(text, m.end(), text[m.end()] != "'")
        if found is None or found[2]:
            out.built = True
        else:
            out.programs.append(found[1])
    return out


# ----- the table -------------------------------------------------------------------------------------------

_COPIES = {"cp", "gcp", "install", "ginstall", "rsync", "scp", "ditto"}
_TARS = {"tar", "gtar", "bsdtar"}
NAMES = _COPIES | _TARS | _COMPRESSORS | {"zip", "pax", "cpio", "split", "gsplit", "csplit", "gcsplit", "diff",
                                          "gdiff", "dd", "gdd", "source", ".", "sed", "gsed", "awk", "gawk", "mawk",
                                          "nawk", "perl", "look"}  # fmt: skip


def other_reads(name: str, args: list[str]) -> OtherReads | None:
    """What ``name args`` reads beyond its operands (see the module's docstring), or None for a command that
    reads nothing more."""
    if name not in NAMES and not re.fullmatch(r"perl[0-9.]*", name):
        return None
    if any(a in ("--help", "--version") for a in args):
        return None
    if name in _COPIES:
        return _copy(name.removeprefix("g"), args)
    if name in _TARS:
        return _tar(args)
    if name in _COMPRESSORS:
        return _compressor(args)
    if name == "zip":
        return _zip(args)
    if name == "pax":
        return _pax(args)
    if name == "cpio":
        return _cpio(args)
    if name in ("split", "gsplit", "csplit", "gcsplit"):
        return _split(name.removeprefix("g"), args)
    if name in ("diff", "gdiff"):
        return _diff(args)
    if name in ("dd", "gdd"):
        files = [a[3:] for a in args if a.startswith("if=") and a[3:] not in ("", "-", "/dev/stdin")]
        return OtherReads(reads=[(f, FILE) for f in files]) if files else None
    if name == "look":  # look [-df] [-t c] STRING [FILE]: the string first
        _opts, plain = _getopt(args, {"-t", "--terminate"})
        return OtherReads(reads=[(f, FILE) for f in plain[1:2]])
    if name in ("source", "."):
        plain = [a for a in args if a != "--"]
        return OtherReads(reads=[(plain[0], FILE)]) if plain else None
    if name in ("sed", "gsed"):
        return _sed(args)
    if name in ("awk", "gawk", "mawk", "nawk"):
        return _awk(args)
    return _perl(args)
