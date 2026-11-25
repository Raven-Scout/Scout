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
               "--mode", "--mtime", "--strip-components", "--options", "--include"}  # fmt: skip
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
    out = OtherReads(reads=[(f, FILE) for f in files if f != "-"])
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
_AWK_END = re.compile(r"\s*(?:$|[);},\n>|&]|\bthen\b)")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_GETLINE_FROM = re.compile(r"\bgetline\b\s*(?:\$?[A-Za-z_][A-Za-z0-9_]*(?:\s*\[[^\]]*\])?)?\s*<(?!=)\s*")
_AWK_PIPE_GETLINE = re.compile(rf"({_AWK_STRING}|[A-Za-z_][A-Za-z0-9_]*)\s*\|&?\s*getline\b")
_AWK_SYSTEM = re.compile(rf"\bsystem\s*\(\s*({_AWK_STRING})\s*\)")
_AWK_OUT_PIPE = re.compile(rf"\|&?\s*({_AWK_STRING})")


def _awk_unescape(literal: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), literal[1:-1])


def _awk(args: list[str]) -> OtherReads:
    """awk's ``getline < FILE`` (a literal, or a variable the program or ``-v`` sets to one) reads that file; a
    command it pipes into ``getline``, out of ``print``, or runs with ``system()`` is a command of its own;
    ``-f FILE`` reads its program from a file."""
    opts, _ = _getopt(args, {"-f", "--file", "-v", "--assign", "-F", "--field-separator", "-e", "--source"})
    programs, files, plain = _scripts(args, {"-e", "--source"}, {"-f", "--file"})
    out = OtherReads(reads=[(f, FILE) for f in files if f != "-"])
    assigned = [v for v in _values(opts, "-v", "--assign") if "=" in v]
    assigned += [p for p in plain if re.match(r"^[A-Za-z_]\w*=", p)]  # `awk '…' f=x file`: an assignment
    given = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in assigned}
    for program in programs:
        _awk_program(program, given, out)
    return out


def _awk_values(program: str, name: str, given: dict[str, str]) -> list[str] | None:
    """The literal values variable ``name`` takes in ``program`` (and from ``-v``); None when one is built."""
    values = [given[name]] if name in given else []
    for m in re.finditer(rf"(?<![\w$.]){re.escape(name)}\s*=(?!=)\s*", program):
        lit = re.match(_AWK_STRING, program[m.end() :])
        if lit is None or not _AWK_END.match(program, m.end() + lit.end()):
            return None
        values.append(_awk_unescape(lit[0]))
    return values or None


def _awk_program(program: str, given: dict[str, str], out: OtherReads) -> None:
    for m in _GETLINE_FROM.finditer(program):
        at = m.end()
        lit = re.match(_AWK_STRING, program[at:])
        if lit is not None and _AWK_END.match(program, at + lit.end()):
            out.literal.append(_awk_unescape(lit[0]))
            continue
        ident = _IDENT.match(program, at)
        if ident is not None and ident[0] in ("ARGV", "FILENAME"):
            continue  # the operands, which are judged as files already
        if ident is not None and _AWK_END.match(program, ident.end()):
            values = _awk_values(program, ident[0], given)
            if values is not None:
                out.literal += values
                continue
        out.built = True
    for m in _AWK_PIPE_GETLINE.finditer(program):
        word = m[1]
        if word.startswith('"'):
            out.programs.append(_awk_unescape(word))
        else:
            values = _awk_values(program, word, given)
            if values is None:
                out.built = True
            else:
                out.programs += values
    for pattern in (_AWK_SYSTEM, _AWK_OUT_PIPE):
        out.programs += [_awk_unescape(m[1]) for m in pattern.finditer(program)]


_PERL_STRING = r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*\""""
_PERL_OPEN = re.compile(r"\bopen\s*\(?\s*(?:(?:my|our|local)\s+)?(?:\$\w+|\*?\w+)\s*,\s*")
_PERL_READS_ARGV = re.compile(r"<<?>>?|<ARGV>|\bARGV\b|\$ARGV\b|\breadline\s*\(?\s*\)?\s*;|\beof\s*\(\s*\)")
_PERL_SYSTEM = re.compile(rf"\b(?:system|exec)\s*\(?\s*({_PERL_STRING})")
_PERL_BACKTICK = re.compile(r"`([^`]*)`|\bqx\s*([({\[/])")


def _perl_literal(token: str) -> tuple[str | None, bool]:
    """A Perl string literal's text, and whether it interpolates (a double-quoted one with ``$`` or ``@``)."""
    if token.startswith("'"):
        return re.sub(r"\\([\\'])", r"\1", token[1:-1]), False
    if token.startswith('"'):
        text = token[1:-1]
        live = bool(re.search(r"(?<!\\)[$@]", text))
        return re.sub(r"\\(.)", r"\1", text), live
    return None, False


def _perl_args(text: str) -> list[str]:
    """The comma-separated arguments of an ``open`` call, up to where the call ends."""
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


def _perl_open(args: list[str], out: OtherReads) -> None:
    if not args:
        return
    if len(args) >= 2:
        mode, live = _perl_literal(args[0])
        if mode is None or live:
            out.built = True
            return
        mode = mode.strip()
        if "|" in mode:  # `open(F, "-|", "cat", "x")`: runs a command
            words = [_perl_literal(a) for a in args[1:]]
            if any(w is None or live for w, live in words):
                out.built = True
            else:
                out.programs.append(" ".join(w for w, _ in words if w is not None))
            return
        if not mode.startswith(("<", "+<")) and mode:
            return  # it writes
        _perl_path(args[1], out)
        return
    text, live = _perl_literal(args[0])
    if text is None or live:
        if not re.fullmatch(r"\$ARGV(?:\[\d+\])?|shift(?:\s*@ARGV)?|\$_", args[0]):
            out.built = True
        return
    text = text.strip()
    if text.startswith("|") or text.endswith("|"):
        out.programs.append(text.strip("|").strip())
    elif not text.startswith((">", "+>")):
        path = re.sub(r"^\+?<\s*", "", text)
        if path and path != "-":
            out.literal.append(path)


def _perl_path(arg: str, out: OtherReads) -> None:
    text, live = _perl_literal(arg)
    if text is not None and not live:
        if text and text != "-":
            out.literal.append(text)
    elif not re.fullmatch(r"\$ARGV(?:\[\d+\])?|shift(?:\s*@ARGV)?|\$_", arg):
        out.built = True  # the files ARGV names are judged as operands


def _perl(args: list[str]) -> OtherReads | None:
    """perl -e/-E: the files ``open`` names, and its operands when it reads ``ARGV`` (``-n``, ``-p``, ``<>``);
    the commands ``system``, backticks and a piped ``open`` run. A script file is its own business."""
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
        _perl_open(_perl_args(text[m.end() :]), out)
    for m in _PERL_SYSTEM.finditer(text):
        command, live = _perl_literal(m[1])
        if command is None or live:
            out.built = True
        else:
            out.programs.append(command)
    for m in _PERL_BACKTICK.finditer(text):
        if m[1] is not None and not re.search(r"(?<!\\)[$@]", m[1]):
            out.programs.append(m[1])
        else:
            out.built = True
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
