"""scout.delegate_media: descriptive metadata only, so vendor names and random bytes never leave a file out.

Every fixture is built in-test. Hidden notes are named after vendors ("Apple",
"Adobe", "Microsoft", "Office", "Calibri"), which tools write into ordinary
files, and after random four-letter words, which compressed data can spell.
"""

from __future__ import annotations

import random
import struct
import zlib

import pytest

from scout import delegate_containers as dc
from scout import delegate_media as media
from scout import delegate_mirror as dm
from scout.kb.outbound import note_from_text, terms_of
from scout.kb.privacy import load_policy

from . import office_fixtures as of

VENDORS = ("Apple", "Adobe", "Microsoft", "Office", "Calibri")


def _scanner(names: tuple[str, ...]) -> dm.Scanner:
    notes = [note_from_text(f"clients/{n.lower()}.md", f"---\nname: {n}\naliases: [{n}]\n---\n") for n in names]
    notes.append(note_from_text("people/priya.md", "---\nname: Priya Sharma\naliases: [Priya]\n---\n"))
    return dm.Scanner(terms_of(notes))


VENDOR_SCAN = _scanner(VENDORS)
POLICY = load_policy(None)


def _verdict(rel: str, data: bytes, scan: dm.Scanner = VENDOR_SCAN) -> str | None:
    kind, text = dm._content(rel, data)
    assert text is None, rel
    return dm.binary_verdict(rel, kind, data, scan, POLICY)


# ----- builders ----------------------------------------------------------------------------------


def _box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">L", 8 + len(body)) + kind + body


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">L", len(body)) + kind + body + struct.pack(">L", zlib.crc32(kind + body))


def _png(*chunks: bytes, tail: bytes = b"") -> bytes:
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", b"\x00" * 13) + b"".join(chunks) + _chunk(b"IEND", b"") + tail


def _tiff(ifd0: list[tuple[int, int, bytes]], sub: list[tuple[int, int, bytes]] | None = None) -> bytes:
    """A little-endian TIFF/EXIF block: IFD0, then an EXIF IFD, then the values that don't fit in an entry."""
    n0 = len(ifd0) + (1 if sub else 0)
    sub_at = 8 + 2 + 12 * n0 + 4
    data_at = sub_at + (2 + 12 * len(sub) + 4 if sub else 0)
    data = bytearray()

    def entries(items: list[tuple[int, int, bytes]]) -> bytes:
        out = b""
        for tag, kind, value in items:
            if len(value) <= 4:
                field = value.ljust(4, b"\x00")
            else:
                field = struct.pack("<L", data_at + len(data))
                data.extend(value)
            out += struct.pack("<HHL", tag, kind, len(value)) + field
        return out

    first = entries(ifd0) + (struct.pack("<HHLL", 0x8769, 4, 1, sub_at) if sub else b"")
    head = b"II*\x00" + struct.pack("<L", 8) + struct.pack("<H", n0) + first + b"\x00" * 4
    if sub:
        head += struct.pack("<H", len(sub)) + entries(sub) + b"\x00" * 4
    return head + bytes(data)


def _ascii(text: str) -> bytes:
    return text.encode() + b"\x00"


CAMERA = [(0x010F, 2, _ascii("Apple")), (0x0110, 2, _ascii("iPhone 15 Pro")), (0x0131, 2, _ascii("Adobe Lightroom"))]


def _jpeg(*segments: tuple[int, bytes], scan: bytes = b"\x12\x34", tail: bytes = b"") -> bytes:
    out = b"\xff\xd8"
    for marker, body in segments:
        out += bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body
    return out + b"\xff\xda\x00\x02" + scan + b"\xff\xd9" + tail


def _xmp(creator: str) -> bytes:
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:tiff="http://ns.adobe.com/tiff/1.0/"'
        ' xmlns:pdf="http://ns.adobe.com/pdf/1.3/" xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmp:CreatorTool="Adobe Photoshop 25" tiff:Make="Apple" pdf:Producer="Microsoft Word">'
        f"<dc:creator><rdf:Seq><rdf:li>{creator}</rdf:li></rdf:Seq></dc:creator>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
    ).encode()


def _mov(description: str) -> bytes:
    keys = b"\x00\x00\x00\x00" + struct.pack(">L", 2)
    for name in (b"com.apple.quicktime.make", b"com.apple.quicktime.description"):
        keys += struct.pack(">L", 8 + len(name)) + b"mdta" + name
    item = lambda i, v: _box(struct.pack(">L", i), _box(b"data", b"\x00\x00\x00\x01\x00\x00\x00\x00" + v))  # noqa: E731
    meta = _box(b"keys", keys) + _box(b"ilst", item(1, b"Apple") + item(2, description.encode()))
    udta = _box(b"\xa9too", b"\x00\x05\x55\xc4Adobe Premiere") + _box(b"\xa9nam", b"\x00\x05\x55\xc4Holiday")
    return _box(b"ftyp", b"qt  \x00\x00\x00\x00qt  ") + _box(b"moov", _box(b"udta", udta) + _box(b"meta", meta))


def _pdf(extra: bytes = b"") -> bytes:
    return (
        b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj << /Producer (Adobe PDF Library 17.0)"
        b" /Creator (Microsoft Word for Office 365) /Title (Quarterly plan)" + extra + b" >> endobj\n%%EOF\n"
    )


# ----- vendor names in tool fields never leave a file out ---------------------------------------


@pytest.mark.parametrize(
    ("rel", "clean", "named"),
    [
        (
            "photos/a.jpg",
            _jpeg((0xE1, b"Exif\x00\x00" + _tiff(CAMERA, [(0x9286, 7, b"ASCII\x00\x00\x00Holiday")]))),
            _jpeg((0xE1, b"Exif\x00\x00" + _tiff([*CAMERA, (0x013B, 2, _ascii("Priya"))]))),
        ),
        (
            "photos/b.jpg",
            _jpeg((0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + _xmp("Alex")), (0xEE, b"Adobe\x00d\x00\x00")),
            _jpeg((0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + _xmp("Priya"))),
        ),
        (
            "photos/c.png",
            _png(_chunk(b"tEXt", b"Software\x00Adobe ImageReady"), _chunk(b"eXIf", _tiff(CAMERA))),
            _png(_chunk(b"tEXt", b"Author\x00Priya")),
        ),
        ("photos/d.mov", _mov("Holiday"), _mov("with Priya")),
        ("docs/e.pdf", _pdf(), _pdf(b" /Author (Priya)")),
        ("docs/f.pdf", _pdf(b" /Metadata 2 0 R\n" + _xmp("Alex")), _pdf(b"\n" + _xmp("Priya"))),
        (
            "photos/g.tif",
            _tiff(CAMERA),
            _tiff([*CAMERA, (0x9C9D, 1, "Priya".encode("utf-16-le") + b"\x00\x00")]),
        ),
        (
            "sound/h.wav",
            b"RIFF\x00\x00\x00\x00WAVE" + b"LIST" + struct.pack("<L", 4 + 8 + 14) + b"INFO" + b"ISFT"
            + struct.pack("<L", 14) + b"Adobe Audition",
            b"RIFF\x00\x00\x00\x00WAVE" + b"LIST" + struct.pack("<L", 4 + 8 + 6) + b"INFO" + b"IART"
            + struct.pack("<L", 6) + b"Priya\x00",
        ),
    ],
)  # fmt: skip
def test_vendor_names_in_tool_fields_are_not_read_but_descriptive_fields_are(
    rel: str, clean: bytes, named: bytes
) -> None:
    assert _verdict(rel, clean) is None, media.metadata(rel, clean)
    assert _verdict(rel, named) == "names_hidden", media.metadata(rel, named)


def test_an_office_file_from_microsoft_office_isnt_left_out_for_its_vendor() -> None:
    app = (
        "<Properties><Template>Normal.dotm</Template><Application>Microsoft Office Word</Application>"
        "<Company>Microsoft</Company><AppVersion>16.0000</AppVersion><Manager>Alex</Manager></Properties>"
    )
    fonts = of.word_part("fonts", '<w:font w:name="Microsoft YaHei"/><w:font w:name="Calibri"/>')
    theme = (
        '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office Theme">'
        '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri Light"/></a:majorFont></a:fontScheme>'
        "</a:theme>"
    )
    styles = of.word_part(
        "styles", '<w:style w:styleId="Normal"><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/></w:rPr></w:style>'
    )
    photo = _jpeg((0xE1, b"Exif\x00\x00" + _tiff(CAMERA)))
    extra = {"docProps/app.xml": app, "word/fontTable.xml": fonts, "word/theme/theme1.xml": theme,
             "word/styles.xml": styles, "word/media/image1.jpeg": photo}  # fmt: skip
    clean = of.docx(extra=extra)
    assert dm.binary_verdict("a.docx", "container", clean, VENDOR_SCAN, POLICY) is None
    named = of.docx(extra={**extra, "docProps/app.xml": app.replace("Alex", "Priya")})  # the manager is read
    assert dm.binary_verdict("a.docx", "container", named, VENDOR_SCAN, POLICY) == "names_hidden"
    xl_styles = f'<styleSheet xmlns="{of.S}"><fonts><font><name val="Calibri"/></font></fonts></styleSheet>'
    xl_strings = f'<sst xmlns="{of.S}"><si><r><rPr><rFont val="Calibri"/></rPr><t>Budget</t></r></si></sst>'
    sheet = of.zipped({"xl/styles.xml": xl_styles, "xl/sharedStrings.xml": xl_strings})
    assert dm.binary_verdict("a.xlsx", "container", sheet, VENDOR_SCAN, POLICY) is None
    ns = "urn:oasis:names:tc:opendocument:xmlns"
    meta = (
        f'<office:document-meta xmlns:office="{ns}:office:1.0" xmlns:meta="{ns}:meta:1.0"><office:meta>'
        "<meta:generator>Microsoft Office/16.0</meta:generator></office:meta></office:document-meta>"
    )
    odt = of.odt(extra={"meta.xml": meta})
    assert dm.binary_verdict("a.odt", "container", odt, VENDOR_SCAN, POLICY) is None


def test_text_after_the_image_and_a_motion_photos_tags_are_read() -> None:
    assert _verdict("a.png", _png(tail=b"notes for Priya")) == "names_hidden"
    assert _verdict("a.jpg", _jpeg(tail=b"trailing Priya")) == "names_hidden"
    assert _verdict("a.jpg", _jpeg(tail=_mov("with Priya"))) == "names_hidden"
    assert _verdict("a.jpg", _jpeg((0xFE, b"for Priya"))) == "names_hidden"
    ztxt = _chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(b"for Priya"))
    assert _verdict("a.png", _png(ztxt)) == "names_hidden"
    iptc = b"Photoshop 3.0\x008BIM\x04\x04\x00\x00\x00\x00\x00\x0c" + b"\x1c\x02\x50\x00\x05Priya"
    assert _verdict("a.jpg", _jpeg((0xED, iptc))) == "names_hidden"
    program = b"Photoshop 3.0\x008BIM\x04\x04\x00\x00\x00\x00\x00\x0c" + b"\x1c\x02\x41\x00\x05Adobe"
    assert _verdict("a.jpg", _jpeg((0xED, program))) is None  # the originating program


# ----- random bytes never spell a hidden name ----------------------------------------------------

RANDOM_NAMES = ("Qorv", "Zelk", "Mupa", "Tazo", "Vrix", "Kelu", "Nobe", "Jirs", "Pywa", "Daxo", "Lufe", "Hoqi",
                "Bevu", "Wych")  # fmt: skip


HEIC_HEAD = _box(b"ftyp", b"heic\x00\x00\x00\x00mif1heic") + _box(b"meta", b"\x00" * 4)


def test_files_of_random_bytes_never_hit_a_four_letter_hidden_name() -> None:
    """Round 3 of the #639 review: a strings pass over compressed data found one of these in 300 HEICs."""
    scan = _scanner(RANDOM_NAMES)
    assert scan.lines("a Qorv b") == [1]  # the names are matched where they are written
    rng = random.Random(639)
    hits = []
    for i in range(300):
        payload = rng.randbytes(65536)
        files = {
            f"p/{i}.heic": HEIC_HEAD + _box(b"mdat", payload),
            f"p/{i}.mp4": _box(b"ftyp", b"isom\x00\x00\x02\x00isom") + _box(b"mdat", payload),
            f"p/{i}.jpg": _jpeg((0xE0, b"JFIF\x00\x01\x01"), scan=payload.replace(b"\xff", b"\xff\x00")),
            f"p/{i}.png": _png(_chunk(b"IDAT", payload)),
            f"p/{i}.pdf": b"%PDF-1.7\n1 0 obj << /Length 65536 /Filter /FlateDecode >> stream\n" + payload
            + b"\nendstream endobj\n%%EOF\n",
        }  # fmt: skip
        for rel, data in files.items():
            if _verdict(rel, data, scan) is not None:
                hits.append(rel)
    assert hits == []


def test_metadata_of_a_broken_file_is_empty_not_an_error() -> None:
    for data in (b"\x89PNG\x00", b"\xff\xd8\xff\xe1\x00", b"RIFF\x00", b"ID3\x04\x00\x00\x00\x00\x00\x7f",
                 b"II*\x00\xff\xff\xff\xff", b"\x00\x00\x00\x08ftyp", b"%PDF-1.7 /Title (unclosed"):  # fmt: skip
        assert isinstance(media.metadata("x.bin", data), str)
    assert dc.is_zip(b"") is False
