"""The descriptive metadata a delegate mirror reads in an image, a sound, a video or a PDF (#550).

Those files are kept on their label alone: their pixels, samples and page
streams aren't read. What people write *about* them is: a caption, an author, a
title, keywords, a comment. :func:`metadata` parses each format's own metadata
structures and returns that text, for :class:`scout.delegate_mirror.Scanner`:

- EXIF (in JPEG, PNG, WebP, HEIC and TIFF): only the descriptive tags,
  :data:`EXIF_TEXT` (description, artist, copyright, user comment, the Windows
  ``XP*`` title/author/comment/keywords/subject, the camera owner);
- XMP packets, anywhere in the file: every field except the tool and process
  ones (:data:`XMP_TOOL`: creator tool, producer, make, model, software, lens,
  history and Camera Raw settings);
- IPTC (JPEG APP13): every dataset but the originating program and its version;
- JPEG comments, PNG ``tEXt``/``iTXt``/``zTXt`` (all but ``Software`` and
  ``Source``, the device), MP4/MOV/HEIC ``udta``/``ilst`` tags and QuickTime
  ``mdta`` keys (all but tool, make, model and software), RIFF ``INFO`` (all but
  ``ISFT``), ID3 frames (all but the encoder ones);
- a PDF's string entries (``/Title``, ``/Author``, an annotation's ``/Contents``…),
  except ``/Producer`` and ``/Creator`` (the tool), and its XMP;
- text after the end of a PNG or JPEG, when it decodes as UTF-8, and the tags of
  a video appended after a JPEG (a motion photo).

Tool fields are left out on purpose: a hidden note named after a vendor
(``Apple``, ``Adobe``) would otherwise leave out every photo and every PDF. And no
raw byte window is scanned: compressed data is full of short printable runs, and
one of them would now and then spell a hidden name.
"""

from __future__ import annotations

import contextlib
import posixpath
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Iterator

MAX_ITEMS = 2000  # entries read per structure, so a crafted file can't spin
_WINDOW = 4 * 1024 * 1024  # zTXt and similar are inflated at most this far

EXIF_TEXT = {
    0x010D: "DocumentName", 0x010E: "ImageDescription", 0x013B: "Artist", 0x8298: "Copyright",
    0x9286: "UserComment", 0x9C9B: "XPTitle", 0x9C9C: "XPComment", 0x9C9D: "XPAuthor", 0x9C9E: "XPKeywords",
    0x9C9F: "XPSubject", 0xA430: "CameraOwnerName",
}  # fmt: skip
_EXIF_IFD = 0x8769
XMP_TOOL = frozenset(
    {
        "CreatorTool", "Producer", "Make", "Model", "Software", "softwareAgent", "HostComputer", "Lens",
        "LensMake", "LensModel", "LensInfo", "LensID", "SerialNumber", "LensSerialNumber", "Firmware",
        "ColorSpace", "ICCProfile", "MetadataDate", "CreateDate", "ModifyDate", "DateCreated",
    }
)  # fmt: skip
_XMP_TOOL_NS = (
    "http://ns.adobe.com/xap/1.0/mm/",  # document ids and history
    "http://ns.adobe.com/xap/1.0/sType/",  # resource events and refs
    "http://ns.adobe.com/camera-raw-settings/",
    "http://ns.adobe.com/exif/1.0/aux/",
    "http://ns.adobe.com/tiff/1.0/",
    "http://ns.adobe.com/photoshop/1.0/camera-profile",
    "http://ns.apple.com/",
    "http://ns.google.com/photos/",
)
_PNG_TOOL = frozenset({b"Software", b"Source", b"Creation Time"})
_BMFF_CONTAINERS = frozenset({b"moov", b"udta", b"meta", b"ilst", b"trak", b"mdia"})
_BMFF_TOOL = frozenset({b"\xa9too", b"\xa9swr", b"\xa9enc", b"\xa9mak", b"\xa9mod", b"\xa9day", b"\xa9xyz"})
_MDTA_TOOL = re.compile(r"\.(make|model|software|creationdate|location\.[\w.]+|location)$", re.IGNORECASE)
_RIFF_TOOL = frozenset({b"ISFT"})
_ID3_TOOL = frozenset({"TSSE", "TENC", "TDEN", "TDRC", "TDTG", "TFLT", "TMED"})
_IPTC_TOOL = frozenset({65, 70})  # originating program, program version
_PDF_TOOL = frozenset({b"Producer", b"Creator", b"CreationDate", b"ModDate", b"Trapped"})
_PDF_STRING = re.compile(rb"/([A-Za-z][A-Za-z0-9]*)\s*(\(|<(?!<))")
_XMP = re.compile(rb"<x:xmpmeta\b.*?</x:xmpmeta>|<rdf:RDF\b.*?</rdf:RDF>", re.DOTALL)
_EXIF_HEADER = b"Exif\x00\x00"


def _ext(rel: str) -> str:
    return posixpath.splitext(rel.lower())[1]


def _local(name: str) -> tuple[str, str]:
    ns, _, local = name.rpartition("}")
    return ns.lstrip("{"), local


def _text(raw: bytes) -> str:
    """Bytes of a metadata value as text: UTF-16 by its byte-order mark, else UTF-8, else Latin-1."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


# ----- EXIF --------------------------------------------------------------------------------------


def exif(tiff: bytes) -> list[str]:
    """The descriptive tags (:data:`EXIF_TEXT`) of a TIFF/EXIF block: in IFD0, the EXIF IFD and every IFD the
    chain links (a multi-page TIFF), up to 32."""
    if tiff[:4] not in (b"II*\x00", b"MM\x00*"):
        return []
    end = "<" if tiff[:2] == b"II" else ">"
    out: list[str] = []
    seen: set[int] = set()
    (first,) = struct.unpack_from(end + "L", tiff, 4)
    todo = [first]
    while todo and len(seen) < 32:
        at = todo.pop()
        if at in seen or at + 2 > len(tiff):
            continue
        seen.add(at)
        (count,) = struct.unpack_from(end + "H", tiff, at)
        for i in range(min(count, MAX_ITEMS)):
            entry = at + 2 + 12 * i
            if entry + 12 > len(tiff):
                break
            tag, kind, n = struct.unpack_from(end + "HHL", tiff, entry)
            if tag == _EXIF_IFD:
                todo.append(struct.unpack_from(end + "L", tiff, entry + 8)[0])
                continue
            if tag not in EXIF_TEXT or kind not in (1, 2, 7):
                continue
            where = entry + 8 if n <= 4 else struct.unpack_from(end + "L", tiff, entry + 8)[0]
            value = tiff[where : where + n]
            if tag in (0x9C9B, 0x9C9C, 0x9C9D, 0x9C9E, 0x9C9F):
                out.append(value.decode("utf-16-le", "replace"))
            elif tag == 0x9286:  # an 8-byte character-code prefix, then the comment
                code, body = value[:8], value[8:]
                out.append(body.decode("utf-16", "replace") if code.startswith(b"UNICODE") else _text(body))
            else:
                out.append(_text(value.split(b"\x00", 1)[0]))
        chain = at + 2 + 12 * count  # the next IFD: a multi-page TIFF's next page
        if chain + 4 <= len(tiff):
            (following,) = struct.unpack_from(end + "L", tiff, chain)
            if following:
                todo.append(following)
    return [v.strip("\x00 ") for v in out if v.strip("\x00 ")]


def _exif_blocks(data: bytes) -> Iterator[bytes]:
    """Every ``Exif\\0\\0``-prefixed TIFF block in ``data`` (JPEG APP1, a HEIC item, a PNG chunk)."""
    at = data.find(_EXIF_HEADER)
    found = 0
    while at >= 0 and found < 8:
        yield data[at + 6 :]
        found += 1
        at = data.find(_EXIF_HEADER, at + 6)


# ----- XMP and IPTC ------------------------------------------------------------------------------


def xmp(packet: bytes) -> list[str]:
    """Every field of an XMP packet but the tool and process ones (:data:`XMP_TOOL`, :data:`_XMP_TOOL_NS`)."""
    try:
        root = ET.fromstring(packet)
    except ET.ParseError:
        return [re.sub(r"<[^>]*>", "\n", packet.decode("utf-8", "replace"))]
    out: list[str] = []
    stack = [(root, False)]
    while stack:
        el, skip = stack.pop()
        ns, local = _local(el.tag) if isinstance(el.tag, str) else ("", "")
        skip = skip or local in XMP_TOOL or ns.startswith(_XMP_TOOL_NS)
        if not skip:
            for key, value in el.attrib.items():
                kns, klocal = _local(key)
                if klocal not in XMP_TOOL and not kns.startswith(_XMP_TOOL_NS) and "rdf-syntax" not in kns:
                    out.append(value)
            if el.text and el.text.strip():
                out.append(el.text.strip())
        stack.extend((child, skip) for child in el)
    return out


def _xmp_packets(data: bytes) -> list[str]:
    out: list[str] = []
    for i, m in enumerate(_XMP.finditer(data)):
        if i >= 8:
            break
        out += xmp(m.group())
    return out


def iptc(block: bytes) -> list[str]:
    """IPTC record-2 datasets (caption, by-line, keywords, headline…) in a Photoshop resource block."""
    out: list[str] = []
    at = block.find(b"\x1c\x02")
    count = 0
    while 0 <= at and at + 5 <= len(block) and block[at] == 0x1C and count < MAX_ITEMS:
        record, dataset, size = block[at + 1], block[at + 2], struct.unpack_from(">H", block, at + 3)[0]
        if record == 2 and dataset not in _IPTC_TOOL and dataset != 0:
            out.append(_text(block[at + 5 : at + 5 + size]))
        at += 5 + size
        count += 1
    return out


# ----- per format --------------------------------------------------------------------------------

_ERRORS = (struct.error, zlib.error, ValueError, IndexError, OverflowError, UnicodeError)


class _Read:
    """What one file's metadata gave: its readings, and whether any structure in it couldn't be parsed."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.failed = 0

    @contextlib.contextmanager
    def item(self) -> Iterator[None]:
        """One structure: an error in it is counted, and the readings before it are kept."""
        try:
            yield
        except _ERRORS:
            self.failed += 1


def _png(data: bytes, r: _Read) -> None:
    at = 8
    for _ in range(MAX_ITEMS):
        if at + 12 > len(data):
            break
        size, kind = struct.unpack_from(">L4s", data, at)
        body = data[at + 8 : at + 8 + size]
        at += 12 + size
        with r.item():
            if kind == b"tEXt":
                key, _, value = body.partition(b"\x00")
                if key not in _PNG_TOOL:
                    r.parts.append(value.decode("latin-1"))
            elif kind == b"zTXt":
                key, _, rest = body.partition(b"\x00")
                if key not in _PNG_TOOL:
                    r.parts.append(_inflate(rest[1:]).decode("latin-1"))
            elif kind == b"iTXt":
                key, _, rest = body.partition(b"\x00")
                compressed = rest[:1] == b"\x01"
                text = rest[2:].split(b"\x00", 2)[-1]  # after the language tag and the translated keyword
                if compressed:
                    text = _inflate(text)
                if key == b"XML:com.adobe.xmp":
                    r.parts += _xmp_packets(text) or [text.decode("utf-8", "replace")]
                elif key not in _PNG_TOOL:
                    r.parts.append(text.decode("utf-8", "replace"))
            elif kind == b"eXIf":
                r.parts += exif(body)
        if kind == b"IEND":
            break
    with r.item():
        r.parts += _trailing(data[at:])


def _inflate(raw: bytes) -> bytes:
    d = zlib.decompressobj()
    out = d.decompress(raw, _WINDOW)
    if not d.eof and not d.unconsumed_tail:
        raise zlib.error("truncated stream")
    return out


def _jpeg(data: bytes, r: _Read) -> None:
    at = 2
    for _ in range(MAX_ITEMS):
        if at + 4 > len(data) or data[at] != 0xFF:
            break
        marker = data[at + 1]
        if marker == 0xDA:
            break
        (size,) = struct.unpack_from(">H", data, at + 2)
        body = data[at + 4 : at + 2 + size]
        at += 2 + size
        with r.item():
            if marker == 0xE1 and body.startswith(_EXIF_HEADER):
                r.parts += exif(body[6:])
            elif marker == 0xE1 and body.startswith(b"http://ns.adobe.com/xap/1.0/"):
                r.parts += _xmp_packets(body)
            elif marker == 0xED:
                r.parts += iptc(body)
            elif marker == 0xFE:
                r.parts.append(_text(body))
    end = data.rfind(b"\xff\xd9")
    if end >= 0:
        with r.item():
            r.parts += _trailing(data[end + 2 :])


def _trailing(tail: bytes) -> list[str]:
    """Bytes after the end of an image: text that decodes, or an appended video's tags (a motion photo)."""
    if not tail.strip(b"\x00"):
        return []
    if tail[4:8] == b"ftyp":
        r = _Read()
        _bmff(tail, r)
        return r.parts
    if b"\x00" not in tail:
        try:
            return [tail.decode("utf-8")]
        except UnicodeDecodeError:
            pass
    return _xmp_packets(tail)


def _boxes(data: bytes, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """ISO-BMFF boxes between ``start`` and ``end``: (type, body start, body end)."""
    at = start
    for _ in range(MAX_ITEMS):
        if at + 8 > end:
            return
        size, kind = struct.unpack_from(">L4s", data, at)
        head = 8
        if size == 1 and at + 16 <= end:
            (size,) = struct.unpack_from(">Q", data, at + 8)
            head = 16
        elif size == 0:
            size = end - at
        if size < head or at + size > end:
            return
        yield kind, at + head, at + size
        at += size


def _bmff(data: bytes, r: _Read) -> None:
    """MP4/MOV/HEIC tags: ``udta`` ``©xxx`` boxes, ``ilst`` items and QuickTime ``mdta`` keys, but tool ones."""
    keys: list[str] = []

    def walk(start: int, end: int, depth: int) -> None:
        for kind, s, e in _boxes(data, start, end):
            with r.item():
                if kind == b"meta":
                    inner = s + 4 if data[s : s + 4] == b"\x00\x00\x00\x00" else s  # a full box in MP4 only
                    walk(inner, e, depth + 1)
                elif kind == b"keys" and e - s >= 8:
                    at = s + 8
                    for _ in range(min(struct.unpack_from(">L", data, s + 4)[0], MAX_ITEMS)):
                        if at + 8 > e:
                            break
                        (size,) = struct.unpack_from(">L", data, at)
                        keys.append(data[at + 8 : at + size].decode("utf-8", "replace"))
                        at += max(size, 8)
                elif kind == b"ilst":
                    for item, is_, ie in _boxes(data, s, e):
                        index = int.from_bytes(item, "big")
                        name = keys[index - 1] if keys and 0 < index <= len(keys) else ""
                        if item in _BMFF_TOOL or (name and _MDTA_TOOL.search(name)):
                            continue
                        for sub, ds, de in _boxes(data, is_, ie):
                            typed = data[ds : ds + 4] in (b"\x00\x00\x00\x01", b"\x00\x00\x00\x02")
                            if sub == b"data" and de - ds > 8 and typed:
                                r.parts.append(_text(data[ds + 8 : de]))
                elif kind[:1] == b"\xa9" and kind not in _BMFF_TOOL and depth > 0:
                    body = data[s:e]
                    r.parts.append(_text(body[4:]) if len(body) > 4 else "")  # QuickTime: size, language, text
                elif kind in _BMFF_CONTAINERS and depth < 6:
                    walk(s, e, depth + 1)

    walk(0, len(data), 0)


def _riff(data: bytes, r: _Read) -> None:
    """RIFF ``INFO`` text (WAV, WebP) but the software one, a WAV's ``bext`` description and originator, and
    WebP's EXIF and XMP chunks."""
    at = 12
    for _ in range(MAX_ITEMS):
        if at + 8 > len(data):
            break
        kind, size = struct.unpack_from("<4sL", data, at)
        body = data[at + 8 : at + 8 + size]
        at += 8 + size + (size & 1)
        with r.item():
            if kind == b"LIST" and body[:4] == b"INFO":
                i = 4
                while i + 8 <= len(body):
                    sub, n = struct.unpack_from("<4sL", body, i)
                    if sub not in _RIFF_TOOL:
                        r.parts.append(_text(body[i + 8 : i + 8 + n].rstrip(b"\x00")))
                    i += 8 + n + (n & 1)
            elif kind == b"bext":  # Broadcast WAV: description (256), originator (32), its reference (32)
                for lo, hi in ((0, 256), (256, 288), (288, 320)):
                    r.parts.append(_text(body[lo:hi].split(b"\x00", 1)[0]))
            elif kind == b"EXIF":
                r.parts += exif(body[6:] if body.startswith(_EXIF_HEADER) else body)
            elif kind == b"XMP ":
                r.parts += _xmp_packets(body)


def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _id3(data: bytes, r: _Read) -> None:
    """ID3v2 text and comment frames, but the encoder ones."""
    if len(data) < 10:
        return
    version = data[3]
    end = min(len(data), 10 + _syncsafe(data[6:10]))
    at = 10
    for _ in range(MAX_ITEMS):
        if at + 10 > end or data[at] == 0:
            break
        frame = data[at : at + 4].decode("latin-1")
        raw = data[at + 4 : at + 8]
        size = _syncsafe(raw) if version >= 4 else struct.unpack(">L", raw)[0]
        body = data[at + 10 : at + 10 + size]
        at += 10 + size
        if frame in _ID3_TOOL or not body or not (frame.startswith(("T", "W")) or frame in ("COMM", "USLT")):
            continue
        with r.item():
            encoding, text = body[0], body[1:]
            if frame in ("COMM", "USLT"):
                text = text[3:]  # the language
            codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(encoding, "latin-1")
            r.parts.append(text.decode(codec, "replace").replace("\x00", "\n"))


MAX_PDF_INFLATED = 16 * 1024 * 1024
_PDF_STREAM = re.compile(rb"(?<!end)stream(?:\r\n|\n|\r)")


def _pdf(data: bytes, r: _Read) -> None:
    """A PDF's string entries but the tool ones, and its XMP, in the file and in its compressed object and
    metadata streams (PDF 1.5+ writers put the Info dictionary, annotations and bookmarks in those)."""
    r.parts += _pdf_strings(data) + _xmp_packets(data)
    budget = MAX_PDF_INFLATED
    for i, m in enumerate(_PDF_STREAM.finditer(data)):
        if i >= MAX_ITEMS or budget <= 0:
            break
        head = data[max(0, m.start() - 2048) : m.start()]
        dictionary = head[head.rfind(b"obj") :] if b"obj" in head else head[-512:]
        if b"/FlateDecode" not in dictionary or not any(k in dictionary for k in (b"/ObjStm", b"/Metadata", b"/XML")):
            continue
        with r.item():
            d = zlib.decompressobj()
            inflated = d.decompress(data[m.end() :], budget)
            budget -= len(inflated)
            if not d.eof and not d.unconsumed_tail:
                raise zlib.error("a truncated stream")
            r.parts += _pdf_strings(inflated) + _xmp_packets(inflated)
    if budget <= 0:
        r.failed += 1  # streams past the budget weren't read


def _pdf_strings(data: bytes) -> list[str]:
    out: list[str] = []
    count = 0
    for m in _PDF_STRING.finditer(data):
        if count >= MAX_ITEMS * 10:
            break
        if m.group(1) in _PDF_TOOL:
            continue
        count += 1
        value = _pdf_literal(data, m.end()) if m.group(2) == b"(" else _pdf_hex(data, m.end())
        if _plausible(value):
            out.append(value)
    return out


def _plausible(value: str) -> bool:
    """Whether a PDF string reads as text: compressed page data can look like ``/Key (…)`` by chance, and its
    bytes then are mostly not printable."""
    if not value.strip() or len(value) > 16384:
        return False
    printable = sum(1 for c in value if c.isprintable() or c in "\n\t")
    return printable >= 0.95 * len(value)


_PDF_ESCAPES = {0x6E: b"\n", 0x72: b"\r", 0x74: b"\t", 0x62: b"\b", 0x66: b"\f", 0x28: b"(", 0x29: b")", 0x5C: b"\\"}


def _pdf_literal(data: bytes, at: int) -> str:
    """A PDF literal string from just after its ``(``: escapes (``\\n``, ``\\(``, ``\\ddd`` octal, a backslash
    line continuation) decoded, then read as UTF-16 by its byte-order mark (as hyperref writes) or as text."""
    out = bytearray()
    depth = 1
    i = at
    while i < len(data) and len(out) < 65536:
        c = data[i]
        if c == 0x5C and i + 1 < len(data):
            nxt = data[i + 1]
            if 0x30 <= nxt <= 0x37:
                j = i + 1
                while j < len(data) and j < i + 4 and 0x30 <= data[j] <= 0x37:
                    j += 1
                out.append(int(data[i + 1 : j], 8) & 0xFF)
                i = j
                continue
            if nxt in (0x0D, 0x0A):  # a line continuation
                i += 3 if data[i + 1 : i + 3] == b"\r\n" else 2
                continue
            out += _PDF_ESCAPES.get(nxt, bytes([nxt]))
            i += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                break
        out.append(c)
        i += 1
    return _text(bytes(out))


def _pdf_hex(data: bytes, at: int) -> str:
    end = data.find(b">", at, at + 131072)
    digits = re.sub(rb"\s", b"", data[at:end] if end >= 0 else b"")
    try:
        return _text(bytes.fromhex(digits.decode("ascii") + ("0" if len(digits) % 2 else "")))
    except ValueError:
        return ""


def _tiff(data: bytes, r: _Read) -> None:
    r.parts += exif(data) + _xmp_packets(data)  # XMP (tag 700) sits in the file as a packet


def _iso(data: bytes, r: _Read) -> None:
    _bmff(data, r)
    for block in _exif_blocks(data):
        with r.item():
            r.parts += exif(block)
    r.parts += _xmp_packets(data)


def _any(data: bytes, r: _Read) -> None:
    r.parts += _xmp_packets(data)


def read(rel: str, data: bytes) -> tuple[str, bool]:
    """The descriptive metadata of a label-only file (module docstring), one value a line, and whether some
    structure in it couldn't be parsed (the readings before it are kept)."""
    ext = _ext(rel)
    if data.startswith(b"\x89PNG"):
        parser = _png
    elif data.startswith(b"\xff\xd8"):
        parser = _jpeg
    elif data.startswith(b"RIFF"):
        parser = _riff
    elif data.startswith(b"ID3"):
        parser = _id3
    elif data[:4] in (b"II*\x00", b"MM\x00*"):
        parser = _tiff
    elif data[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"):
        parser = _iso
    elif ext == ".pdf" or b"%PDF-" in data[:1024]:
        parser = _pdf
    else:
        parser = _any
    r = _Read()
    with r.item():
        parser(data, r)
    return "\n".join(p for p in r.parts if p), r.failed > 0


def metadata(rel: str, data: bytes) -> str:
    """:func:`read`'s text alone."""
    return read(rel, data)[0]
