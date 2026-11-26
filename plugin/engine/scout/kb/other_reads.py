"""Readers whose files aren't simply their operands: what else counts as reading a file around ``scoutctl kb``.

:mod:`scout.kb.raw_read` judges a reader's operands (``cat x``, ``grep -r y dir``). The commands here read
files another way, and both of the gate's judges ask this module about them (the shell's spelling in
:func:`scout.kb.raw_read.raw_kb_read`, the path the shell hands over in :mod:`scout.kb.live_run`):

- a copy or an archive reads its sources, all of them below a folder when it is recursive (``cp -r``,
  ``rsync -a``, ``ditto``, ``tar c``, ``zip -r``, ``pax -w``, ``cpio -o``, ``split``, ``gzip -c``); a copy
  into the KB is a write there, not a read out of it;
- a move or a link out of the KB (``mv``, ``ln``, ``ln -s``, ``link``) leaves a note where a later call reads it
  (a hard link is the note's own inode, which no path check sees through); one into the KB reads nothing;
- an interpreter's inline program (``python3 -c``, ``ruby -e``, ``node -e``, ``php -r``, ``lua -e``,
  ``Rscript -e``, or one read from stdin) reads the files its text names: :mod:`scout.kb.program_reads`;
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
from collections.abc import Sequence
from dataclasses import dataclass, field

# How a path is read (scout.kb.live_run.KB_READ_HOW): "file" opens it, "covers" reads every file below it.
FILE, COVERS = "file", "covers"


@dataclass
class OtherReads:
    """What one command reads beyond its operands."""

    reads: list[tuple[str, str]] = field(default_factory=list)  # (word as the shell hands it, FILE | COVERS)
    literal: list[str] = field(default_factory=list)  # paths a program opens exactly as written
    folders: list[str] = field(default_factory=list)  # folders a program lists exactly as written (opendir)
    requires: list[str] = field(default_factory=list)  # relative files `do`/`require` look for in each @INC folder
    dests: list[str] = field(default_factory=list)  # where a copy writes: one in the KB makes ``reads`` none
    # Paths a program's text reads another way (scout.kb.program_reads.ProgramReads.spelled): (path, how, what of
    # it is live).
    spelled: list[tuple[str, str, str]] = field(default_factory=list)
    built: bool = False  # a program opens a path it builds at run time
    stdin_list: bool = False  # it reads the files named on its stdin (``find . | cpio -o``)
    programs: list[str] = field(default_factory=list)  # shell commands a program runs
    # Its data passes through a program it names (tar's `-I`, `--to-command`): a copy into the KB still reads
    # what it copies, since that program sees it on the way.
    piped: bool = False
    # Paths a program takes from the environment (awk's `ENVIRON["F"]`): the variable's name, judged as `$F`.
    environ: list[str] = field(default_factory=list)
    # What a program writes (scout.kb.program_reads.ProgramReads.writes, writes_built, operand_writes), so the gate
    # can tell an edit of KB notes (read there, written only there: recorded as kb_edit) from a copy out of the KB.
    writes: list[str] = field(default_factory=list)
    writes_built: bool = False
    operand_writes: bool = False
    # Its text names the KB where no judged call spells a path (scout.kb.program_reads): denied, unless an edit.
    unaccounted: bool = False
    opaque: bool = False  # its program is text the shell fills in (`-c "$(…)"`): denied when the command names the KB

    @property
    def writes_something(self) -> bool:
        return bool(self.writes or self.writes_built or self.operand_writes)


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
_AWK_SETS_ARGV = re.compile(r"\bARGV\s*\[(?:[^\[\]]|\[[^\]]*\])*\]\s*=(?!=)\s*")
# One piece of an awk string expression: a literal, ENVIRON["NAME"], or a variable.
_AWK_TOKEN = re.compile(rf'\s*(?:({_AWK_STRING})|ENVIRON\s*\[\s*"([A-Za-z_]\w*)"\s*\]|([A-Za-z_][A-Za-z0-9_]*))')
# What ENVIRON["NAME"] stands for in a value: the shell's `$NAME` (see OtherReads.environ).
_ENV_MARK = re.compile("\0env:([A-Za-z_][A-Za-z0-9_]*)\0")
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
_AWK_LIMIT = 16  # values an expression may take before it counts as built


_AWK_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "a": "\a", "\\": "\\", '"': '"',
                "/": "/"}  # fmt: skip


def _awk_unescape(literal: str) -> str | None:
    """A string literal's text as awk decodes it: ``\\n`` and the other POSIX escapes, ``\\/``, ``\\ooo`` octal,
    and ``\\xHH`` hex (gawk, and BSD/macOS awk). None for any other escape: awks differ there, and a name the gate
    can't spell for certain is one it won't guess."""
    text, out, i = literal[1:-1], [], 0
    while i < len(text):
        c = text[i]
        if c != "\\" or i + 1 == len(text):
            out.append(c)
            i += 1
            continue
        after = text[i + 1]
        octal = re.match(r"[0-7]{1,3}", text[i + 1 :])
        hexa = re.match(r"x([0-9A-Fa-f]{1,2})", text[i + 1 :])
        if after in _AWK_ESCAPES:
            out.append(_AWK_ESCAPES[after])
            i += 2
        elif octal is not None:
            out.append(chr(int(octal[0], 8)))
            i += 1 + len(octal[0])
        elif hexa is not None:
            out.append(chr(int(hexa[1], 16)))
            i += 1 + len(hexa[0])
        else:
            return None
    return "".join(out)


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
            text = _awk_unescape(m[1])
            part: list[str] | None = None if text is None else [text]
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
    is never set (an awk builtin such as ``ARGV``, or a name the gate can't see filled), or changed in place
    (``sub(/x/, "k", f)``, ``split(…, f)``, ``getline f``, ``f++``, ``f[…]``)."""
    n = re.escape(name)
    changed = (
        rf"\b(?:g?sub|gensub|split|patsplit|match)\s*\((?:[^()]|\([^()]*\))*\b{n}\b"
        rf"|\bgetline\s+{n}\b|(?<![\w$]){n}\s*(?:\+\+|--|[-+*/%^]=|\[)|(?:\+\+|--)\s*{n}\b"
    )
    if re.search(changed, program):
        return None
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
    for m in _AWK_SETS_ARGV.finditer(program):  # `ARGV[ARGC++] = "x"`: a file it reads as its input
        if not layout[0][m.start()]:
            paths(program[m.end() : _awk_forward(program, m.end(), layout)])
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


_PERL_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "f": "\f", "b": "\b", "a": "\a", "e": "\x1b"}


def _perl_unescape(text: str) -> str | None:
    """A double-quoted Perl string's text as Perl decodes it: ``\\n`` and kin, ``\\e``, ``\\xHH``, ``\\x{…}``,
    ``\\ooo``, ``\\o{…}``, ``\\N{U+…}``, ``\\cX``, and a backslashed punctuation character as itself. None for any
    other escape (``\\L``, ``\\u``, ``\\Q``, ``\\N{NAME}``…): the gate won't guess a name it can't spell."""
    out, i = [], 0
    try:
        while i < len(text):
            c = text[i]
            if c != "\\" or i + 1 == len(text):
                out.append(c)
                i += 1
                continue
            after, rest = text[i + 1], text[i + 2 :]
            braced = re.match(r"\{([^}]*)\}", rest)
            if after in _PERL_ESCAPES:
                out.append(_PERL_ESCAPES[after])
                i += 2
            elif after == "x" and braced is not None:
                out.append(chr(int(braced[1].replace("_", "") or "0", 16)))
                i += 2 + len(braced[0])
            elif after == "x":
                digits = re.match(r"[0-9A-Fa-f]{0,2}", rest)
                assert digits is not None
                out.append(chr(int(digits[0] or "0", 16)))
                i += 2 + len(digits[0])
            elif after == "o" and braced is not None and re.fullmatch(r"[0-7]+", braced[1]):
                out.append(chr(int(braced[1], 8)))
                i += 2 + len(braced[0])
            elif after in "01234567":
                digits = re.match(r"[0-7]{1,3}", text[i + 1 :])
                assert digits is not None
                out.append(chr(int(digits[0], 8)))
                i += 1 + len(digits[0])
            elif after == "N" and braced is not None and re.fullmatch(r"U\+[0-9A-Fa-f]+", braced[1]):
                out.append(chr(int(braced[1][2:], 16)))
                i += 2 + len(braced[0])
            elif after == "c" and rest:
                out.append(chr(ord(rest[0].upper()) ^ 64))
                i += 3
            elif after.isalnum():
                return None
            else:
                out.append(after)
                i += 2
    except (ValueError, OverflowError):  # a code point past Unicode
        return None
    return "".join(out)


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
            keep = not interpolates and text[i + 1] in (opener, closer, "\\")
            out.append(text[i + 1] if keep else text[i : i + 2])
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
            if not interpolates:
                return i + 1, body, False
            decoded = _perl_unescape(body)  # an escape it can't decode: as unknown as an interpolation
            live = decoded is None or bool(re.search(r"(?<!\\)[$@]", body))
            return i + 1, decoded if decoded is not None else body, live
        out.append(c)
        i += 1
    return None


def _perl_string_end(text: str, at: int) -> int:
    """The index just past the ``'…'`` or ``"…"`` string that starts at ``text[at]``, else ``len(text) + 1``."""
    quote, i = text[at], at + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return len(text) + 1


def _perl_concatenation(arg: str) -> list[str]:
    """``arg`` split at its top-level ``.`` operators (outside strings and brackets); one part if it has none."""
    parts, depth, at, i = [], 0, 0, 0
    while i < len(arg):
        c = arg[i]
        if c in "'\"":
            i = _perl_string_end(arg, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "." and depth == 0 and not (arg[i - 1 : i].isdigit() or arg[i + 1 : i + 2].isdigit()):
            parts.append(arg[at:i].strip())
            at = i + 1
        i += 1
    parts.append(arg[at:].strip())
    return [p for p in parts if p] if len(parts) > 1 else [arg]


def _perl_literal(token: str) -> tuple[str | None, bool]:
    """A Perl string literal's text (``'…'``, ``"…"``, ``q{…}``, ``qq{…}``), and whether it interpolates (``$``
    or ``@`` in a double-quoted one). ``(None, False)`` for anything else."""
    token = token.strip()
    if token[:1] in ("'", '"') and _perl_string_end(token, 0) != len(token):
        return None, False  # more after the string (`"kn"."owledge"`): not one literal
    if token.startswith("'") and len(token) > 1 and token.endswith("'"):
        return re.sub(r"\\([\\'])", r"\1", token[1:-1]), False
    if token.startswith('"') and len(token) > 1 and token.endswith('"'):
        text = token[1:-1]
        decoded = _perl_unescape(text)
        if decoded is None:
            return None, False  # an escape the gate doesn't decode: no literal it can judge
        return decoded, bool(re.search(r"(?<!\\)[$@]", text))
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
    parts = _perl_concatenation(arg)
    if len(parts) > 1:  # `"kn" . "owledge-base/a.md"`: each part one value, joined
        joined = [""]
        for part in parts:
            found = _perl_values(part, text, depth + 1)
            if found is None or len(found) != 1:
                return None
            joined = [joined[0] + found[0]]
        return joined
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
        argv = words if len(words) > 1 else words[0].split()
        if argv and posixpath.basename(argv[0]) in _SHELLS and "-c" not in argv:
            out.built = True  # `open(P, "|-", "sh")`: what the program writes into it runs, and that isn't here


def _perl(args: list[str]) -> OtherReads | None:
    """perl -e/-E: the files ``open`` names and those ``@ARGV`` is set to, its operands when it reads ``ARGV``
    (``-n``, ``-p``, ``<>``); the commands ``system``, ``exec``, backticks, ``qx`` and a piped ``open`` run. A
    path or a command built at run time is built. A script file is its own business."""
    programs: list[str] = []
    operands: list[str] = []
    inc: list[str] = []  # folders `-I DIR` and `-Mlib=DIR` put in @INC
    script: str | None = None
    loops = embedded = False
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if programs and (a == "--" or not a.startswith("-")):
            operands += args[i:] if a == "--" else [a, *args[i:]]
            break
        if a == "--":
            script = args[i] if i < len(args) else None
            break
        if not a.startswith("-"):
            script = a
            break
        if a == "-I" and i < len(args):
            inc.append(args[i])
            i += 1
            continue
        lib = re.fullmatch(r"-M-?lib=(.*)", a)
        if a.startswith("-I") or lib is not None:
            inc += [a[2:]] if lib is None else [d for d in lib[1].split(",") if d]
            continue
        embedded = embedded or a.startswith("-x")
        if a.startswith(("-i", "-M", "-m", "-0", "-l", "-x", "-C", "-d", "-D")) and not re.fullmatch(
            r"-[a-zA-Z]*[eE]", a
        ):
            continue  # attached values: `-i.bak`, `-MJSON`, `-0777`
        letters = a[1:]
        loops = loops or "n" in letters or "p" in letters
        if letters.endswith(("e", "E")) and i < len(args):
            programs.append(args[i])
            i += 1
    if not programs:
        if script is None and not embedded and any(re.fullmatch(r"-[vVhc]\S*", a) for a in args):
            return None  # `perl -v`, `perl -V`: no program runs
        # A program from a file (`perl x.pl`, `-x`) or from stdin: what it reads isn't in the command.
        reads = [(script, FILE)] if script not in (None, "-") else []
        return OtherReads(reads=reads, folders=inc, built=True)
    out = OtherReads(folders=list(inc))
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
        command = _perl_unescape(m[1])
        if command is None or re.search(r"(?<!\\)[$@]", m[1]):
            out.built = True
        else:
            out.programs.append(command)
    for m in _PERL_QX.finditer(text):
        found = _perl_quoted(text, m.end(), text[m.end()] != "'")
        if found is None or found[2]:
            out.built = True
        else:
            out.programs.append(found[1])
    _perl_rest(text, any("File::Copy" in a for a in args), inc, out)
    return out


# Every other construct that opens, lists, reads or runs something: each is judged by its argument, which must be
# one literal (or a scalar set to one) to be read; anything else is built. So a form the handlers above don't
# know is recorded, never silent: `open FH => …`, `sysopen`, `opendir`, `glob`, `<*.md>`, `do FILE`,
# `require FILE`, `readpipe`, File::Copy's `copy`/`move`. And what moves where a relative path is looked for:
# `chdir DIR` (every later relative path is under it), and `use lib`, `push`/`unshift @INC`, `@INC = …` (a
# relative `do`/`require` file is looked for in each folder there).
_PERL_TRIGGER = re.compile(
    r"\b(open|sysopen|opendir|glob|do|require|readpipe|copy|move|syscopy|chdir)\b|<(?![\s=<>]|\$?\w+>|>)([^<>\n]+)>"
    r"|\buse\s+(lib)\b\s*|\b(?:unshift|push)\s*\(?\s*(@INC)\s*,\s*|(@INC)\s*=(?!=)\s*"
)


def _perl_string_spans(text: str) -> list[tuple[int, int]]:
    spans, i = [], 0
    while i < len(text):
        if text[i] in "'\"":
            quote, j = text[i], i + 1
            while j < len(text) and text[j] != quote:
                j += 2 if text[j] == "\\" else 1
            spans.append((i, j))
            i = j + 1
        else:
            i += 1
    return spans


def _perl_list(rest: str, text: str) -> list[str] | None:
    """The literal strings a list argument holds (``qw(a b)``, ``("a", "b")``, ``"a", $b``…); None when one isn't."""
    qw = re.match(r"\s*qw\s*(?=[^\w\s])", rest)
    if qw is not None:
        words = _perl_quoted(rest, qw.end(), False)
        return None if words is None else words[1].split()
    values: list[str] = []
    for arg in _perl_args(rest.lstrip()[1:] if rest.lstrip().startswith("(") else rest):
        found = _perl_values(arg, text)
        if found is None:
            return None
        values += found
    return values


def _perl_rest(text: str, file_copy: bool, inc: list[str], out: OtherReads) -> None:
    spans = _perl_string_spans(text)
    chdirs: list[str] = []  # folders `chdir` moves to: every later relative path is under one of them
    required: list[str] = []  # relative `do`/`require` files: looked for in each @INC folder
    inc = list(inc)
    for m in _PERL_TRIGGER.finditer(text):
        if any(a < m.start() <= b for a, b in spans):
            continue  # a word inside a string
        word, rest = m[1], text[m.end() :]
        args = _perl_args(rest.lstrip()[1:] if rest.lstrip().startswith("(") else rest)
        if m[3] or m[4] or m[5]:  # `use lib …`, `push @INC, …`, `@INC = …`: folders a `do`/`require` looks in
            found = _perl_list(rest, text)
            if found is None:
                out.built = True
            else:
                inc += found
            continue
        if word == "chdir":
            found = _perl_values(args[0], text) if args else None  # a bare `chdir` goes home
            if found is None:
                out.built = True
            else:
                chdirs += found
            continue
        if word is None:  # `<*.md>`, `<$dir/*>`: a glob
            if re.search(r"(?<!\\)[$@]", m[2]):
                out.built = True
            else:
                out.reads.append((m[2], FILE))
        elif word == "open":
            if _PERL_OPEN.match(text, m.start()) is None:
                out.built = True  # a form `_PERL_OPEN` doesn't parse (`open FH => …`, `open(FH)`)
        elif word in ("do", "require"):
            head = rest.lstrip()
            if not head or head[0] in "{;" or re.match(r"[A-Za-z_][\w:]*(?:\s|;|$)|v?\d", head):
                continue  # a block, a module name or a version: no file
            found = _perl_values(args[0], text) if args else None
            if found is None:
                out.built = True
            else:
                out.literal += found
                required += [f for f in found if not f.startswith("/")]
        elif word == "glob":
            found = _perl_values(args[0], text) if args else None
            if found is None:
                out.built = True
            else:
                out.reads += [(pattern, FILE) for pattern in found]
        elif word == "sysopen":
            _perl_path(args[1] if len(args) > 1 else "", text, out)
        elif word == "opendir":
            found = _perl_values(args[1], text) if len(args) > 1 else None
            if found is None:
                out.built = True
            else:
                out.folders += found
        elif word == "readpipe":
            _perl_command(args[:1], text, out)
        elif file_copy:  # File::Copy's copy, move, syscopy: the source is read
            _perl_path(args[0] if args else "", text, out)
    # Every folder @INC gains is read for its files; a relative `do`/`require` file is in one of them.
    out.folders += [d for d in inc if d not in out.folders]
    out.literal += [posixpath.join(d, f) for d in inc for f in required]
    out.requires += required  # PERL5LIB's folders, which the caller knows, are looked in too
    # After a `chdir`, a relative path is under the folder it moved to (one it can't work out made `built`).
    out.folders += chdirs
    relative = [p for p in out.literal if not p.startswith("/")]
    out.literal += [posixpath.join(d, p) for d in chdirs for p in relative]
    out.reads += [(posixpath.join(d, w), how) for d in chdirs for w, how in list(out.reads) if not w.startswith("/")]


# ----- moves and links --------------------------------------------------------------------------------------

_MOVE_VALUES = {"-t", "--target-directory", "-S", "--suffix"}


def _move(name: str, args: list[str]) -> OtherReads:
    """mv, ln and link take a file out of where it was: a move or a hard link out of the KB leaves a copy of a
    note for a later read (a hard link is the note's own inode, which no path check sees through), and a symbolic
    link one that a reader following links (``grep -R``) reads. Their sources are reads, as a copy's are, unless
    the target is in the KB. A symbolic link's target is relative to the link's folder, not the command's, so it
    is judged from both (and from the target as a folder)."""
    opts, plain = _getopt(args, _MOVE_VALUES)
    target = _values(opts, "-t", "--target-directory")
    symbolic = name == "ln" and _has(opts, "-s", "--symbolic")
    if target:
        sources, dests = plain, target
    elif len(plain) >= 2:
        sources, dests = plain[:-1], plain[-1:]
    else:  # `ln -s ../x`: a link in this folder
        sources, dests = (plain if name == "ln" else []), []
    how = COVERS if name == "mv" or symbolic else FILE
    reads = [(s, how) for s in sources]
    if symbolic and not _has(opts, "-r", "--relative"):
        for s in sources:
            if s.startswith(("/", "~")):
                continue
            for dest in dests:
                reads += [(posixpath.join(dest, s), how), (posixpath.join(posixpath.dirname(dest), s), how)]
    return OtherReads(reads=list(dict.fromkeys(reads)), dests=dests)


def _with_stdin(name: str, args: list[str], stdin: Sequence[str]) -> OtherReads | None:
    """perl reading its program from stdin (``perl <<EOF``, ``perl - FILE <<EOF``): as ``perl -e``."""
    if not stdin or any(a == "-e" or re.fullmatch(r"-[a-zA-Z]*[eE]", a) for a in args):
        return None
    flags = [a for a in args if a.startswith("-") and a != "-"]
    rest = [a for a in args if not a.startswith("-") or a == "-"]
    if rest and rest[0] != "-":
        return None  # a script file
    return _perl([*flags, "-e", "\n".join(stdin), *rest[1:]])


def _interpreter(name: str, args: list[str], stdin: Sequence[str]) -> OtherReads | None:
    from scout.kb.program_reads import program_reads

    found = program_reads(name, args, stdin)
    if found is None:
        return None
    return OtherReads(
        reads=found.reads,
        literal=found.literal,
        spelled=found.spelled,
        built=found.built,
        programs=found.programs,
        writes=found.writes,
        writes_built=found.writes_built,
        operand_writes=found.operand_writes,
        unaccounted=found.unaccounted,
        opaque=found.opaque,
    )


_SQLITE_VALUES = {"-cmd", "-init", "-separator", "-newline", "-nullvalue", "-vfs", "-mmap", "-pagecache",
                  "-heap", "-lookaside", "-maxsize"}  # fmt: skip


def _sqlite(args: list[str]) -> OtherReads:
    """sqlite3 DB [SQL…]: the database it opens, the files its SQL reads (``readfile('…')``, ``.read F``,
    ``.import F T``, ``.open F``, ``-init F``), and any other KB name in its SQL, which the gate denies."""
    opts, plain = _getopt(args, _SQLITE_VALUES)
    out = OtherReads(reads=[(d, FILE) for d in plain[:1] if d not in ("", ":memory:")])
    out.reads += [(f, FILE) for f in _values(opts, "-init")]
    sql = "\n".join([*plain[1:], *_values(opts, "-cmd")])
    for m in re.finditer(r"\breadfile\s*\(\s*(?:'((?:[^']|'')*)'\s*\)|)", sql, re.I):
        if m[1] is None:
            out.built = True
        else:
            out.literal.append(m[1].replace("''", "'"))
    for m in re.finditer(r"^\s*\.(?:read|import|open|restore|load)\s+(\S+)", sql, re.M | re.I):
        out.literal.append(m[1].strip("'\""))
    named = " ".join([*out.literal, *(w for w, _ in out.reads)])
    out.unaccounted = bool(re.search(r"knowledge[-_]base|action-items/context", sql)) and not re.search(
        r"knowledge[-_]base|action-items/context", named
    )
    out.writes += [f for f in (m[1] for m in re.finditer(r"\bwritefile\s*\(\s*'([^']*)'", sql, re.I))]
    return out


# ----- the table -------------------------------------------------------------------------------------------

_COPIES = {"cp", "gcp", "install", "ginstall", "rsync", "scp", "ditto"}
_TARS = {"tar", "gtar", "bsdtar"}
NAMES = _COPIES | _TARS | _COMPRESSORS | {"zip", "pax", "cpio", "split", "gsplit", "csplit", "gcsplit", "diff",
                                          "gdiff", "dd", "gdd", "source", ".", "sed", "gsed", "awk", "gawk", "mawk",
                                          "nawk", "perl", "look"}  # fmt: skip
_MOVES = {"mv", "gmv", "ln", "gln", "link", "glink"}
_PERL_IN_PLACE = re.compile(r"-[aplnswT0-9]*i")  # -i, -pi, -pi.bak; not -Mlib or -I


def other_reads(name: str, args: list[str], stdin: Sequence[str] = ()) -> OtherReads | None:
    """What ``name args`` reads beyond its operands (see the module's docstring), or None for a command that
    reads nothing more. ``stdin``: the programs an interpreter would read there (its here-documents, a pipe's
    text), for one that takes its program from stdin (:mod:`scout.kb.program_reads`)."""
    from scout.kb.program_reads import INTERPRETER
    from scout.kb.raw_read import program_name

    if any(a in ("--help", "--version") for a in args):
        return None
    folded = program_name(name)  # `MV`, `Ln` and `PYTHON3` run them on a case-insensitive disk
    if folded in _MOVES:
        return _move(folded.removeprefix("g"), args)
    if folded in ("sqlite3", "sqlite"):
        return _sqlite(args)
    if INTERPRETER.fullmatch(folded):
        return _interpreter(folded, args, stdin)
    if name not in NAMES and not re.fullmatch(r"perl[0-9.]*", name):
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
    found = _with_stdin(name, args, stdin) or _perl(args)  # a program on stdin is judged as `perl -e`
    if found is not None and found.reads and any(_PERL_IN_PLACE.match(a) for a in args if not a.startswith("--")):
        found.operand_writes = True  # `perl -pi -e …`: it rewrites its operands, an edit when they're KB notes
    return found
