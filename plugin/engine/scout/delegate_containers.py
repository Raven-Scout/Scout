"""What a delegate mirror can read inside an Office document (#550): the text of a zip of XML parts.

``.docx``/``.xlsx``/``.pptx`` (and their macro-enabled and template forms) and
OpenDocument files (``.odt``/``.ods``/``.odp``/``.odg``) are zip containers of
XML. :func:`unpack` opens one with the standard library alone and returns every
piece of text in it, for :class:`scout.delegate_mirror.Scanner` to check: each
XML part's text (paragraphs, table cells and shared strings joined the way a
reader sees them, and again every text node and attribute value on a line of
its own, so a name split across runs or packed between metadata fields is
found either way), the raw XML, the member names, and the zip comments. That
covers the body, headers, footers, footnotes, comments and their authors,
tracked changes, shared strings and inline strings, slides and notes,
``docProps`` (author, title) and any custom XML.

A file can't be redacted in place, so the caller keeps it or leaves it out
whole. :func:`unpack` refuses (``Unpacked.refusal``), and the caller leaves the
file out, when it can't vouch for every byte a reader could see:

- ``too_large``: over :data:`MAX_FILE_BYTES`, over :data:`MAX_MEMBERS` parts, or
  over :data:`MAX_UNPACKED_BYTES` unpacked (declared or actually read): the
  zip-bomb guard.
- ``unreadable``: not a zip, a corrupt one, an unsupported compression, or an
  XML part that doesn't parse. A password-protected Office file is an OLE
  compound file, not a zip, so it lands here too.
- ``encrypted``: a zip-encrypted part, or an OpenDocument manifest that lists
  encryption.
- ``macros``: a ``vbaProject.bin``, or an OpenDocument ``Basic/`` or
  ``Scripts/`` folder.
- ``nested_archive``: a part that is itself an archive or an Office file (an
  embedded spreadsheet is one), by name or by its first bytes.
- ``unscannable_part``: a part that isn't UTF-8 text and isn't an image, a
  sound, a video or a font (an embedded OLE object, an ActiveX control).
- ``xml_dtd``: an XML part with a ``<!DOCTYPE`` or ``<!ENTITY``, which Office
  never writes: refused before parsing, so no entity expansion happens.

Images, media and fonts inside the container aren't read, as a vault image is
kept on its label alone.
"""

from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass
from io import BytesIO

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_MEMBERS = 2000
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
_CHUNK = 1024 * 1024

OOXML_TYPES = frozenset(
    {
        ".docx", ".docm", ".dotx", ".dotm",
        ".xlsx", ".xlsm", ".xltx", ".xltm",
        ".pptx", ".pptm", ".potx", ".potm", ".ppsx", ".ppsm",
    }
)  # fmt: skip
ODF_TYPES = frozenset({".odt", ".ott", ".ods", ".ots", ".odp", ".otp", ".odg", ".otg"})
CONTAINER_TYPES = OOXML_TYPES | ODF_TYPES
# Office files that aren't zips of XML: left out unless the owner opts a path in.
LEGACY_OFFICE_TYPES = frozenset({".doc", ".dot", ".xls", ".xlt", ".xlsb", ".ppt", ".pot", ".pps"})
ARCHIVE_TYPES = frozenset(
    {
        ".zip", ".gz", ".tgz", ".tar", ".7z", ".rar", ".bz2", ".tbz", ".tbz2", ".xz", ".txz",
        ".zst", ".lz", ".lzma", ".z", ".cab", ".cpio", ".dmg", ".iso", ".jar", ".war", ".apk",
        ".epub", ".deb", ".rpm",
    }
)  # fmt: skip
# Parts not read: what a vault file of that type is kept for on its label alone.
MEDIA_TYPES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp", ".ico", ".tif", ".tiff",
        ".emf", ".wmf", ".svgz", ".mp3", ".m4a", ".wav", ".mp4", ".mov",
        ".ttf", ".otf", ".odttf", ".fntdata", ".woff", ".woff2",
    }
)  # fmt: skip
_ARCHIVE_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"Rar!", b"\xfd7zXZ", b"BZh")
_DTD = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
# Elements whose text is a unit a reader sees on its own: a paragraph, a heading, a table cell, a shared string.
_BLOCKS = frozenset(
    {"p", "h", "si", "is", "c", "tc", "tr", "row", "br", "cr", "line-break", "list-item", "table-cell", "table-row"}
)

REASONS = {
    "too_large": "too large to scan",
    "unreadable": "can't be read",
    "encrypted": "encrypted",
    "macros": "holds macros",
    "nested_archive": "holds another archive or Office file",
    "unscannable_part": "holds a part that can't be scanned",
    "xml_dtd": "holds XML with a document type declaration",
}


@dataclass(frozen=True)
class Unpacked:
    """The text of a container, or why it can't be vouched for (one of :data:`REASONS`)."""

    text: str | None
    refusal: str | None = None


def _ext(name: str) -> str:
    return posixpath.splitext(name.lower())[1]


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def xml_text(root: ET.Element) -> str:
    """Two readings of one XML part: block by block (runs joined), then every text node and attribute alone."""
    joined: list[str] = []
    leaves: list[str] = []
    stack: list[tuple[ET.Element, bool]] = [(root, False)]
    while stack:  # no recursion: a deep document can't overflow the stack
        el, closing = stack.pop()
        block = _local(el.tag) in _BLOCKS
        if closing:
            if block:
                joined.append("\n")
            if el.tail:
                joined.append(el.tail)
                leaves.append(el.tail)
            continue
        if block:
            joined.append("\n")
        leaves.extend(el.attrib.values())
        if el.text:
            joined.append(el.text)
            leaves.append(el.text)
        stack.append((el, True))
        stack.extend((child, False) for child in reversed(el))
    return "".join(joined) + "\n" + "\n".join(leaves)


def _read(zf: zipfile.ZipFile, info: zipfile.ZipInfo, budget: int) -> bytes | None:
    """A part's bytes, or None when reading it would pass ``budget`` (the declared size isn't trusted)."""
    out = bytearray()
    with zf.open(info) as fh:
        while chunk := fh.read(_CHUNK):
            out += chunk
            if len(out) > budget:
                return None
    return bytes(out)


def unpack(data: bytes, ext: str) -> Unpacked:
    """Every piece of text in the Office container ``data`` (extension ``ext``), or a refusal."""
    if len(data) > MAX_FILE_BYTES:
        return Unpacked(None, "too_large")
    try:
        return _unpack(data, ext in ODF_TYPES)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, ValueError, EOFError,
            OSError, zlib.error, ET.ParseError):  # fmt: skip
        return Unpacked(None, "unreadable")


def _unpack(data: bytes, odf: bool) -> Unpacked:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            return Unpacked(None, "too_large")
        parts: list[str] = [zf.comment.decode("utf-8", "replace")]
        wanted: list[zipfile.ZipInfo] = []
        for info in infos:
            name = info.filename
            low = name.lower()
            parts += [name, info.comment.decode("utf-8", "replace")]
            if info.flag_bits & 0x1:
                return Unpacked(None, "encrypted")
            if posixpath.basename(low) == "vbaproject.bin" or (odf and low.startswith(("basic/", "scripts/"))):
                return Unpacked(None, "macros")
            if info.is_dir():
                continue
            ext = _ext(low)
            if ext in ARCHIVE_TYPES or ext in CONTAINER_TYPES or ext in LEGACY_OFFICE_TYPES:
                return Unpacked(None, "nested_archive")
            if ext not in MEDIA_TYPES:
                wanted.append(info)
        if sum(i.file_size for i in wanted) > MAX_UNPACKED_BYTES:
            return Unpacked(None, "too_large")
        budget = MAX_UNPACKED_BYTES
        for info in wanted:
            raw = _read(zf, info, budget)
            if raw is None:
                return Unpacked(None, "too_large")
            budget -= len(raw)
            refusal = _part(info.filename, raw, odf, parts)
            if refusal is not None:
                return Unpacked(None, refusal)
    return Unpacked("\n".join(parts))


def _part(name: str, raw: bytes, odf: bool, parts: list[str]) -> str | None:
    """Add one part's text to ``parts``; a refusal when it can't be read as text."""
    if raw.startswith(_ARCHIVE_MAGIC):
        return "nested_archive"
    if b"\0" in raw:
        return "unscannable_part"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "unscannable_part"
    parts.append(text)
    if odf and name == "META-INF/manifest.xml" and "encryption-data" in text:
        return "encrypted"
    if text.lstrip().startswith("<"):
        if _DTD.search(raw):
            return "xml_dtd"
        parts.append(xml_text(ET.fromstring(raw)))
    return None
