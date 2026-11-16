"""What a delegate mirror can read inside an Office document (#550): the text of a zip of XML parts.

``.docx``/``.xlsx``/``.pptx`` (their macro-enabled, template and add-in forms,
``.xlsb``, Visio, themes and XPS) and OpenDocument files (``.odt``/``.ods``/
``.odp``/``.odg``) are zip containers, mostly of XML. :func:`unpack` opens one
with the standard library alone and returns every piece of text in it as a list
of *readings*, for :class:`scout.delegate_mirror.Scanner` to check one by one:

- each XML part read block by block (paragraphs, table cells, shared strings
  and phonetic runs each on a line, the runs inside them joined), so a name split
  across runs is found; when the part has tracked changes, hidden runs, field
  codes or phonetic runs, again as its *final* text (no deletions, field codes,
  hidden or phonetic runs) and as its *original* text (no insertions), so a
  tracked edit inside a name can't hide it; an Excel header or footer again with
  its ``&L``/``&"font"``/``&12`` codes taken out and spaced out;
- every text node, attribute value, XML comment and processing instruction on a
  line of its own (once each, numbers and cell references left out), so values
  packed side by side in metadata never run together;
- the member names, the zip comments, extra fields and any bytes between or
  around the members, and the strings (ASCII and UTF-16) of every binary part
  that isn't an image, a sound, a video or a font.

That covers the body, headers, footers, footnotes, endnotes, comments and their
authors, tracked changes, text boxes, shared and inline strings, sheet and
defined names, slides, layouts and notes, ``docProps`` (author, title), custom
XML and relationship targets. Excel 4 macro sheets (``xl/macrosheets/``) aren't
flagged as macros; their formulas are XML, and scanned.

A file can't be redacted in place, so the caller keeps it or leaves it out
whole. ``Unpacked.refusal`` says why the file can't be vouched for even when no
reading names a hidden note. Reading goes on past a refusal, so a name anywhere
the reader got to still counts, except for the three that stop it:

- ``too_large``: over :data:`MAX_FILE_BYTES`, over :data:`MAX_MEMBERS` parts (read
  from the end-of-directory record, before the directory is parsed), over
  :data:`MAX_UNPACKED_BYTES` unpacked (declared, then counted as read) or over
  :data:`MAX_TEXT_CHARS` of readings: the zip-bomb guard. A name past the
  point where reading stopped isn't seen, so such a file ships only under an
  ``allow_unscanned`` glob, as one that can't be scanned does;
- ``unreadable``: not a zip, a corrupt one, one whose members overlap, one
  whose member doesn't fill its span exactly (a deflate stream that ends
  early is fine only if the bytes after it are read as stray), or one packed
  other than stored or deflated (any decoder error lands here too, never an
  exception). A password-protected Office file is an OLE compound file, not
  a zip, so it lands here;
- ``encrypted``: a zip-encrypted part, or an OpenDocument manifest that lists
  encryption.

And the ones that don't:

- ``macros``: a ``vbaProject.bin``, or an OpenDocument ``Basic/`` or ``Scripts/``
  folder;
- ``nested_archive``: a part that is itself an archive or an Office file (an
  embedded spreadsheet), by name or by its first bytes. A zip part is read
  too, one level deep and within the same budget, so a name inside it counts;
- ``unscannable_part``: a binary part that isn't a known image, sound, video or
  font, or one whose bytes don't look like one (an embedded OLE object, an
  ActiveX control); printer settings and LibreOffice's layout cache are known
  and fine;
- ``broken_part``: an XML part that doesn't parse (legacy ``.vml`` drawings are
  often not well-formed, and are read with their tags stripped instead);
- ``xml_dtd``: an XML part with a ``<!DOCTYPE`` or ``<!ENTITY``, which Office never
  writes: not parsed, so no entity is ever expanded;
- ``stray_bytes``: bytes the members don't account for, or an extra field of an
  unknown kind (all of them are still read for names).
"""

from __future__ import annotations

import gzip
import posixpath
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO

from scout import delegate_media

# Parts and fields that name the tools and typefaces a document was made with, never its content: not read, so a
# hidden note named after a vendor ("Microsoft", "Adobe") doesn't leave out every Office file.
_TOOL_PART = re.compile(r"(?:^|/)(?:fontTable\.xml|theme/[^/]+\.xml)$", re.IGNORECASE)
_TOOL_FIELDS = {
    # Company stays: it names an organisation, often the client's own (#639 review, round 4).
    "docprops/app.xml": frozenset({"Application", "AppVersion", "Template"}),
    "meta.xml": frozenset({"generator", "template"}),
}
# Elements whose attributes are typeface names (w:rFonts, a:latin, xl's <font><name val>, ODF font-face).
_FONT_ELEMENTS = frozenset({"rFonts", "latin", "ea", "cs", "sym", "font", "name", "rFont", "font-face", "family"})
_FONT_ATTRS = frozenset(
    {"typeface", "panose", "pitchFamily", "charset", "font-family", "font-name", "font-family-generic"}
)

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_MEMBERS = 2000
MAX_DIRECTORY_BYTES = 4 * 1024 * 1024
MAX_UNPACKED_BYTES = 16 * 1024 * 1024
# The scan costs about 4 s per million characters of readings against 300 hidden notes, and runs twice per
# file per sync (the build and check_tree): 2M keeps one Office file under about 10 s a pass.
MAX_TEXT_CHARS = 2 * 1024 * 1024
_CHUNK = 1024 * 1024

OOXML_TYPES = frozenset(
    {
        ".docx", ".docm", ".dotx", ".dotm",
        ".xlsx", ".xlsm", ".xltx", ".xltm", ".xlsb", ".xlam",
        ".pptx", ".pptm", ".potx", ".potm", ".ppsx", ".ppsm", ".ppam",
        ".vsdx", ".vsdm", ".vstx", ".vstm", ".thmx", ".xps", ".oxps",
    }
)  # fmt: skip
ODF_TYPES = frozenset({".odt", ".ott", ".ods", ".ots", ".odp", ".otp", ".odg", ".otg"})
CONTAINER_TYPES = OOXML_TYPES | ODF_TYPES
# Office files that aren't zips: left out unless the owner opts a path in.
LEGACY_OFFICE_TYPES = frozenset({".doc", ".dot", ".xls", ".xlt", ".ppt", ".pot", ".pps", ".vsd"})
ARCHIVE_TYPES = frozenset(
    {
        ".zip", ".gz", ".tgz", ".tar", ".7z", ".rar", ".bz2", ".tbz", ".tbz2", ".xz", ".txz",
        ".zst", ".lz", ".lzma", ".z", ".cab", ".cpio", ".dmg", ".iso", ".jar", ".war", ".apk",
        ".epub", ".deb", ".rpm",
    }
)  # fmt: skip
MEDIA_TYPES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp", ".ico", ".tif", ".tiff",
        ".emf", ".wmf", ".svgz", ".mp3", ".m4a", ".wav", ".mp4", ".mov",
        ".ttf", ".otf", ".odttf", ".fntdata", ".woff", ".woff2",
    }
)  # fmt: skip
# The first bytes of an archive, an Office zip or an OLE compound file (a legacy Office file, an embedded object).
ARCHIVE_MAGIC = (
    b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"Rar!", b"\xfd7zXZ",
    b"BZh", b"\x28\xb5\x2f\xfd", b"MSCF", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
)  # fmt: skip
# Binary parts Office writes that hold no document text of their own: read for strings, never refused.
_KNOWN_BINARY = re.compile(r"^(?:(?:xl|word|ppt)/printerSettings/[^/]+\.bin|layout-cache)$", re.IGNORECASE)
_DTD = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_PROLOG_ENCODING = re.compile(r"""^(\s*<\?xml[^>]*?)\s+encoding\s*=\s*["'][^"']*["']""")
_TAG = re.compile(r"<[^>]*>")
_ASCII_STRINGS = re.compile(rb"[\x20-\x7e\t]{4,}")
_UTF16_STRINGS = re.compile(rb"(?:[\x20-\xff]\x00){4,}")
# Values that are never a name: numbers, cell references, ids.
_TRIVIAL = re.compile(
    r"^(?:[-+]?[\d.,eE:%\s]*|\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?|[A-Za-z]?\d*"
    r"|\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?|(?=[^\d]*\d)[0-9A-Fa-f]{8,})$"
)
# Excel header/footer codes: sections (&L &C &R), fields (&P &N &D &T &A &F &Z &G), styles (&B &I &U &E &S &X &Y
# &H &O), a font (&"name,style"), a size (&12), a colour (&K…).
_HF_CODE = re.compile(r'&"[^"]*"|&\d+|&K(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{2}[+-]\d{3})|&[LCRPNDTAFZGBIUESXYHO]')
_HEADER_FOOTER = frozenset({"oddHeader", "oddFooter", "evenHeader", "evenFooter", "firstHeader", "firstFooter"})
# Elements whose text is a unit a reader sees on its own.
_BLOCKS = frozenset(
    {"p", "h", "si", "is", "c", "tc", "tr", "row", "br", "cr", "rPh", "line-break", "list-item", "table-cell",
     "table-row", "tab"}
)  # fmt: skip
_NOT_FINAL = frozenset({"del", "delText", "delInstrText", "instrText", "moveFrom", "rPh"})
_NOT_ORIGINAL = frozenset({"ins", "moveTo", "rPh"})
_HIDDEN_RUN = frozenset({"vanish", "webHidden", "specVanish"})
_KNOWN_EXTRA = frozenset({0x0001, 0x000A, 0x5455, 0x7875, 0x7855, 0xA220, 0xCAFE, 0x6375, 0x7075, 0x9901})
_SIGNATURES: tuple[tuple[int, bytes], ...] = (
    (0, b"\x89PNG\r\n\x1a\n"), (0, b"\xff\xd8\xff"), (0, b"GIF87a"), (0, b"GIF89a"), (0, b"BM"),
    (0, b"II*\x00"), (0, b"MM\x00*"), (0, b"\x00\x00\x01\x00"), (8, b"WEBP"), (8, b"WAVE"), (4, b"ftyp"),
    (4, b"moov"), (4, b"mdat"), (4, b"wide"), (4, b"free"), (0, b"ID3"), (0, b"\xff\xfb"), (0, b"\xff\xf3"),
    (0, b"\xff\xf2"), (40, b" EMF"), (0, b"\xd7\xcd\xc6\x9a"), (0, b"\x01\x00\x09\x00"), (0, b"\x02\x00\x09\x00"),
    (0, b"\x00\x01\x00\x00"), (0, b"true"), (0, b"OTTO"), (0, b"wOFF"), (0, b"wOF2"), (34, b"LP"),
)  # fmt: skip
_GUID = re.compile(r"^\{?([0-9A-Fa-f]{8})-?([0-9A-Fa-f]{4})-?([0-9A-Fa-f]{4})-?([0-9A-Fa-f]{4})-?([0-9A-Fa-f]{12})\}?$")

REASONS = {
    "too_large": "too large to scan",
    "unreadable": "can't be read",
    "encrypted": "encrypted",
    "macros": "holds macros",
    "nested_archive": "holds another archive or Office file",
    "unscannable_part": "holds a part that can't be scanned",
    "broken_part": "holds a part that doesn't parse",
    "xml_dtd": "holds XML with a document type declaration",
    "stray_bytes": "holds bytes outside its parts",
}
HARD = frozenset({"too_large", "unreadable", "encrypted"})  # these stop the reading


class _Stop(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


@dataclass
class Unpacked:
    """The readings of a container, and why it can't be vouched for (one of :data:`REASONS`), if it can't."""

    parts: list[str] = field(default_factory=list)
    refusal: str | None = None
    chars: int = 0
    budget: int = 0  # unpacked bytes left to read, shared with a nested document

    @property
    def text(self) -> str:
        return "\n".join(self.parts)

    def add(self, text: str) -> None:
        if not text:
            return
        self.chars += len(text)
        if self.chars > MAX_TEXT_CHARS:
            raise _Stop("too_large")
        self.parts.append(text)

    def refuse(self, reason: str) -> None:
        if self.refusal is None or (reason in HARD and self.refusal not in HARD):
            self.refusal = reason


def _ext(name: str) -> str:
    return posixpath.splitext(name.lower())[1]


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def strings(raw: bytes) -> str:
    """The printable runs of a binary: ASCII, and UTF-16LE of Latin-1 letters (OLE and EMF text)."""
    out = [m.group().decode("ascii") for m in _ASCII_STRINGS.finditer(raw)]
    out += [m.group().decode("utf-16-le") for m in _UTF16_STRINGS.finditer(raw)]
    return "\n".join(out)


def _hidden_run(el: ET.Element) -> bool:
    if _local(el.tag) != "r":
        return False
    return any(_local(p.tag) == "rPr" and any(_local(x.tag) in _HIDDEN_RUN for x in p) for p in el)


def _walk(
    root: ET.Element, drop: Callable[[ET.Element], bool] | None, leaves: list[str] | None
) -> tuple[str, set[str]]:
    """One block-by-block reading of ``root`` (subtrees ``drop`` names left out), and the local names seen."""
    joined: list[str] = []
    seen: set[str] = set()
    stack: list[tuple[ET.Element, bool]] = [(root, False)]
    while stack:  # no recursion: a deep document can't overflow the stack
        el, closing = stack.pop()
        local = _local(el.tag)
        block = local in _BLOCKS
        if closing:
            if block:
                joined.append("\n")
            if el.tail:
                joined.append(el.tail)
                if leaves is not None:
                    leaves.append(el.tail)
            continue
        if drop is not None and drop(el):
            if el.tail:
                joined.append(el.tail)
            continue
        seen.add(local)
        if block:
            joined.append("\n")
        if leaves is not None:
            font = local in _FONT_ELEMENTS
            leaves.extend(v for k, v in el.attrib.items() if not font and _local(k) not in _FONT_ATTRS)
        if el.text:
            joined.append(el.text)
            if leaves is not None:
                leaves.append(el.text)
        if local in _HEADER_FOOTER and el.text:
            joined.append("\n" + _HF_CODE.sub(" ", el.text) + "\n" + _HF_CODE.sub("", el.text) + "\n")
        stack.append((el, True))
        stack.extend((child, False) for child in reversed(el))
    return "".join(joined), seen


def xml_readings(root: ET.Element) -> list[str]:
    """Every reading of one parsed XML part (module docstring)."""
    leaves: list[str] = []
    joined, seen = _walk(root, None, leaves)
    out = [joined]
    hidden = bool(seen & _HIDDEN_RUN)
    if hidden or seen & _NOT_FINAL:
        out.append(_walk(root, lambda el: _local(el.tag) in _NOT_FINAL or _hidden_run(el), None)[0])
    if seen & _NOT_ORIGINAL:
        out.append(_walk(root, lambda el: _local(el.tag) in _NOT_ORIGINAL, None)[0])
    lines = set(joined.split("\n"))
    unique = dict.fromkeys(v.strip() for v in leaves)
    out.append("\n".join(v for v in unique if v and v not in lines and not _TRIVIAL.match(v)))
    return out


def _decode(raw: bytes) -> str | None:
    """Text by its byte-order mark (UTF-16 or UTF-8), else UTF-8 with no NUL; None when it isn't text."""
    try:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw.decode("utf-16")
        if b"\0" in raw:
            return None
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _parse(text: str) -> ET.Element:
    body = _PROLOG_ENCODING.sub(r"\1", text, count=1)  # it is a str now: the declared encoding no longer applies
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    parser.feed(body)
    return parser.close()


# ----- the zip's layout ------------------------------------------------------------------------


def _extras(blob: bytes, out: Unpacked) -> dict[int, bytes]:
    """An extra-field block as ``{id: data}``; its bytes go to the readings; an unknown or broken one is stray."""
    fields: dict[int, bytes] = {}
    i = 0
    while i + 4 <= len(blob):
        tag, size = struct.unpack_from("<HH", blob, i)
        data = blob[i + 4 : i + 4 + size]
        if len(data) != size:
            out.refuse("stray_bytes")
            break
        if tag == 0x0017:  # strong encryption
            raise _Stop("encrypted")
        if tag not in _KNOWN_EXTRA:
            out.refuse("stray_bytes")
        if tag in (0x6375, 0x7075) and len(data) > 5:  # Info-ZIP Unicode comment / path: version, crc, UTF-8
            out.add(data[5:].decode("utf-8", "replace"))
        fields[tag] = data
        i += 4 + size
    if i != len(blob):
        out.refuse("stray_bytes")
    if blob:
        out.add(strings(blob))
    return fields


def _zip64(fields: dict[int, bytes], usize: int, csize: int, offset: int) -> tuple[int, int, int]:
    """The sizes and offset a zip64 extra field holds for the ones the header marks 0xFFFFFFFF."""
    data = fields.get(0x0001, b"")
    values = [usize, csize, offset]
    at = 0
    for k, v in enumerate(values):
        if v == 0xFFFFFFFF and at + 8 <= len(data):
            values[k] = struct.unpack_from("<Q", data, at)[0]
            at += 8
    return values[0], values[1], values[2]


def _stray(out: Unpacked, data: bytes) -> None:
    if data:
        out.refuse("stray_bytes")
        out.add(data.decode("utf-8", "replace"))
        out.add(strings(data))


def check_layout(data: bytes, out: Unpacked) -> None:
    """Refuse a zip whose bytes its members don't tile exactly, before :class:`zipfile.ZipFile` parses it.

    The end record's member count is checked against :data:`MAX_MEMBERS` first. Every byte must belong to a
    local header, a member's data, a data descriptor, the central directory, the zip64 records or the end
    record; any other byte is stray, and read. Overlapping members (a zip-bomb trick) are ``unreadable``.
    """
    eocd = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if eocd < 0 or eocd + 22 > len(data):
        raise _Stop("unreadable")
    _sig, _d, _cd, _n_disk, entries, cd_size, cd_offset, comment_len = struct.unpack_from("<4s4H2LH", data, eocd)
    _stray(out, data[eocd + 22 + comment_len :])
    tail = eocd
    if entries == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        loc = eocd - 20
        if loc < 0 or data[loc : loc + 4] != b"PK\x06\x07":
            raise _Stop("unreadable")
        (z64,) = struct.unpack_from("<Q", data, loc + 8)
        if data[z64 : z64 + 4] != b"PK\x06\x06":
            raise _Stop("unreadable")
        size, _v1, _v2, _dk, _cdk, _nd, entries, cd_size, cd_offset = struct.unpack_from("<Q2H2L4Q", data, z64 + 4)
        _stray(out, data[z64 + 12 + size : loc])
        tail = z64
    if entries > MAX_MEMBERS or cd_size > MAX_DIRECTORY_BYTES:
        raise _Stop("too_large")
    # Offsets count from the zip's own start; bytes before it (a prefix, as zipfile allows) are stray.
    base = tail - cd_size - cd_offset
    if base < 0:
        raise _Stop("unreadable")
    _stray(out, data[:base])
    cd_start, cd_end = base + cd_offset, tail
    members: list[tuple[int, int, int, int, int]] = []  # local header offset, packed and unpacked size, flags, method
    at = cd_start
    for _ in range(entries):
        if data[at : at + 4] != b"PK\x01\x02":
            raise _Stop("unreadable")
        f = struct.unpack_from("<4s6H3L5H2L", data, at)
        flags, method, csize, usize, nlen, xlen, clen, lho = f[3], f[4], f[8], f[9], f[10], f[11], f[12], f[16]
        extra = _extras(data[at + 46 + nlen : at + 46 + nlen + xlen], out)
        usize, csize, lho = _zip64(extra, usize, csize, lho)
        members.append((base + lho, csize, usize, flags, method))
        at += 46 + nlen + xlen + clen
    if at > cd_end:
        raise _Stop("unreadable")
    _stray(out, data[at:cd_end])
    pos = base
    unpacked = 0
    for lho, csize, usize, flags, method in sorted(members):
        if lho < pos:
            raise _Stop("unreadable")  # overlapping members
        _stray(out, data[pos:lho])
        if data[lho : lho + 4] != b"PK\x03\x04":
            raise _Stop("unreadable")
        nlen, xlen = struct.unpack_from("<HH", data, lho + 26)
        _extras(data[lho + 30 + nlen : lho + 30 + nlen + xlen], out)
        start = lho + 30 + nlen + xlen
        unpacked += _span(data[start : start + csize], usize, flags, method, out, MAX_UNPACKED_BYTES - unpacked)
        pos = start + csize
        if flags & 0x8:  # a data descriptor follows: signed or not, 32- or 64-bit sizes
            if data[pos : pos + 4] == b"PK\x07\x08":
                pos += 4
            fits = [n for n in (12, 20) if pos + n == cd_start or data[pos + n : pos + n + 4] == b"PK\x03\x04"]
            pos += fits[0] if fits else 12
    if pos > cd_start:
        raise _Stop("unreadable")
    _stray(out, data[pos:cd_start])


def _span(span: bytes, usize: int, flags: int, method: int, out: Unpacked, budget: int) -> int:
    """Check that a member's data is exactly its stream; bytes after the stream are stray. Its unpacked size.

    zipfile reads a stored member only up to its unpacked size and a deflated one only to the end of its
    stream, so anything after that inside the member's span would otherwise go unread. Office writes only
    stored and deflated members; any other method is ``unreadable``.
    """
    if flags & 0x1:
        raise _Stop("encrypted")
    if method == 0:
        if usize > len(span):
            raise _Stop("unreadable")
        _stray(out, span[usize:])
        return usize
    if method != 8:
        raise _Stop("unreadable")
    d = zlib.decompressobj(-15)
    rest, total = span, 0
    while not d.eof:
        chunk = d.decompress(rest, _CHUNK)
        rest = d.unconsumed_tail
        total += len(chunk)
        if total > budget:
            raise _Stop("too_large")
        if not chunk and not rest:
            break
    if not d.eof or total != usize:
        raise _Stop("unreadable")
    _stray(out, d.unused_data + rest)
    return total


# ----- reading the parts -----------------------------------------------------------------------


def _read(zf: zipfile.ZipFile, info: zipfile.ZipInfo, budget: int) -> bytes | None:
    """A part's bytes, or None when reading it would pass ``budget`` (the declared size isn't trusted)."""
    buf = bytearray()
    with zf.open(info) as fh:
        while chunk := fh.read(_CHUNK):
            buf += chunk
            if len(buf) > budget:  # pragma: no cover - the declared sizes are checked first and zipfile stops there
                return None
    return bytes(buf)


def is_zip(data: bytes) -> bool:
    """Whether ``data`` is, or ends with, a zip: its first bytes, or an end-of-directory record that closes it."""
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return True
    at = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    return at >= 0 and at + 22 <= len(data) and at + 22 + struct.unpack_from("<H", data, at + 20)[0] == len(data)


def _riff(kind: bytes) -> Callable[[bytes], bool]:
    return lambda d: d[:4] == b"RIFF" and d[8:12] == kind


_MOV_ATOMS = (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot")
# What a vault file of each label-only type starts with (delegate_mirror.BINARY_TYPES).
_LOOKS_LIKE: dict[str, Callable[[bytes], bool]] = {
    ".png": lambda d: d.startswith(b"\x89PNG\r\n\x1a\n"),
    ".jpg": lambda d: d.startswith(b"\xff\xd8\xff"),
    ".jpeg": lambda d: d.startswith(b"\xff\xd8\xff"),
    ".gif": lambda d: d.startswith((b"GIF87a", b"GIF89a")),
    ".webp": _riff(b"WEBP"),
    ".wav": _riff(b"WAVE"),
    ".heic": lambda d: d[4:8] == b"ftyp",
    ".m4a": lambda d: d[4:8] == b"ftyp",
    ".mp4": lambda d: d[4:8] == b"ftyp",
    ".mov": lambda d: d[4:8] in _MOV_ATOMS,
    ".bmp": lambda d: d.startswith(b"BM"),
    ".ico": lambda d: d.startswith(b"\x00\x00\x01\x00"),
    ".tif": lambda d: d.startswith((b"II*\x00", b"MM\x00*")),
    ".tiff": lambda d: d.startswith((b"II*\x00", b"MM\x00*")),
    ".pdf": lambda d: b"%PDF-" in d[:1024],
    ".mp3": lambda d: d.startswith(b"ID3") or (len(d) > 1 and d[0] == 0xFF and d[1] & 0xE0 == 0xE0),
}


def looks_like(rel: str, data: bytes) -> bool:
    """Whether a vault file's bytes are the image, sound, video or PDF its extension names."""
    check = _LOOKS_LIKE.get(_ext(rel))
    return check is None or check(data)


def unpack(data: bytes, ext: str) -> Unpacked:
    """Every reading of the Office container ``data`` (extension ``ext``), and why it can't be vouched for."""
    out = Unpacked(budget=MAX_UNPACKED_BYTES)
    if len(data) > MAX_FILE_BYTES:
        out.refuse("too_large")
        return out
    try:
        check_layout(data, out)
        _unpack(data, ext in ODF_TYPES, out, depth=0)
    except _Stop as e:
        out.refuse(e.reason)
    except Exception:  # any decoder's error (zlib, lzma, struct, XML, a corrupt header): fail closed, never raise
        out.refuse("unreadable")
    return out


def _nested(raw: bytes, out: Unpacked) -> None:
    """Read a zip inside a container (an embedded document) into ``out``, one level deep, within the same budget.

    Its own refusals don't stop the outer reading; running out of budget does.
    """
    try:
        check_layout(raw, out)
        _unpack(raw, b"opendocument" in raw[:200], out, depth=1)
    except _Stop as e:
        if e.reason == "too_large":
            raise
    except Exception:
        pass


def _unpack(data: bytes, odf: bool, out: Unpacked, *, depth: int) -> None:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:  # pragma: no cover - check_layout refused it from the end record already
            raise _Stop("too_large")
        names = [zf.comment.decode("utf-8", "replace")]
        names += [n for i in infos for n in (i.filename, i.comment.decode("utf-8", "replace"))]
        out.add("\n".join(names))
        wanted: list[zipfile.ZipInfo] = []
        for info in infos:
            low = info.filename.lower()
            if info.flag_bits & 0x1:  # pragma: no cover - check_layout refused an encrypted member already
                raise _Stop("encrypted")
            if posixpath.basename(low) == "vbaproject.bin" or (odf and low.startswith(("basic/", "scripts/"))):
                out.refuse("macros")
            if info.is_dir():
                continue
            ext = _ext(low)
            if ext in ARCHIVE_TYPES or ext in CONTAINER_TYPES or ext in LEGACY_OFFICE_TYPES:
                out.refuse("nested_archive")
            wanted.append(info)
        if sum(i.file_size for i in wanted) > out.budget:
            raise _Stop("too_large")
        for info in wanted:
            raw = _read(zf, info, out.budget)
            if raw is None:
                raise _Stop("too_large")
            out.budget -= len(raw)
            _part(info.filename, raw, odf, out, depth)


def _deobfuscated(name: str, raw: bytes) -> bytes:
    """An ``.odttf`` font's first 32 bytes un-XORed with the GUID in its name (ECMA-376 Part 1, 17.8.1)."""
    m = _GUID.match(posixpath.splitext(posixpath.basename(name))[0])
    if m is None:
        return raw
    key = bytes.fromhex("".join(m.groups()))[::-1]
    head = bytes(b ^ key[i % 16] for i, b in enumerate(raw[:32]))
    return head + raw[32:]


def _signed(name: str, raw: bytes) -> bool:
    if _ext(name) == ".odttf":
        raw = _deobfuscated(name, raw)
    return any(raw[at : at + len(sig)] == sig for at, sig in _SIGNATURES)


def _part(name: str, raw: bytes, odf: bool, out: Unpacked, depth: int = 0) -> None:
    """Add one part's readings to ``out``, and its refusal if it can't be vouched for."""
    ext = _ext(name)
    if raw.startswith(ARCHIVE_MAGIC):
        if ext == ".svgz" and raw.startswith(b"\x1f\x8b"):
            raw = gzip.GzipFile(fileobj=BytesIO(raw)).read(out.budget + 1)
            if len(raw) > out.budget:
                raise _Stop("too_large")
            out.budget -= len(raw)
            return _part(name[:-1], raw, odf, out, depth)  # the SVG inside, read as any part is
        out.refuse("nested_archive")
        out.add(strings(raw))
        if depth == 0 and is_zip(raw):
            _nested(raw, out)  # an embedded document: its names count too
        return
    text = _decode(raw)
    if text is None:
        if ext in MEDIA_TYPES and _signed(name, raw):
            # An image, a sound, a video or a font: its descriptive metadata, never a raw strings pass (compressed
            # pixels would now and then spell a name). EMF and WMF draw text as records: their strings are text.
            if ext in (".emf", ".wmf"):
                out.add(strings(raw))
                return
            meta, broken = delegate_media.read(name, raw)
            out.add(meta)
            if broken:
                out.refuse("broken_part")
            return
        out.add(strings(raw))
        if not _KNOWN_BINARY.match(name):
            out.refuse("unscannable_part")
        return
    if _TOOL_PART.search(name):
        return  # fonts and themes: vendor and typeface names, no document text
    if odf and name == "META-INF/manifest.xml" and "encryption-data" in text:
        raise _Stop("encrypted")
    if not text.lstrip().startswith("<"):
        out.add(text)
        return
    if _DTD.search(text):
        out.refuse("xml_dtd")
        out.add(_TAG.sub("\n", text))
        out.add(text)
        return
    try:
        root = _parse(text)
    except ET.ParseError:
        if ext != ".vml":
            out.refuse("broken_part")
        out.add(_TAG.sub("\n", text))
        out.add(text)
        return
    tool = _TOOL_FIELDS.get(name.lower())
    if tool is not None:  # the tool's own name and version, the installer's company, the template file
        for parent in list(root.iter()):
            for child in [c for c in parent if _local(c.tag) in tool]:
                parent.remove(child)
    for reading in xml_readings(root):
        out.add(reading)
