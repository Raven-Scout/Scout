"""Tiny synthetic Office files, built in-test with zipfile: never a real document.

Each builder writes the parts a reader needs (content types, relationships,
the main part, ``docProps``) plus any ``extra`` parts, so a test can put a
name in exactly one place. Text is passed as XML fragments.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
CT = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
RELS = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'


def core(creator: str = "Alex", title: str = "Quarterly plan") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{title}</dc:title><dc:creator>{creator}</dc:creator>"
        "<cp:lastModifiedBy>Alex</cp:lastModifiedBy></cp:coreProperties>"
    )


def zipped(parts: dict[str, str | bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buf.getvalue()


def docx(body: str = "<w:p><w:r><w:t>Plain words only.</w:t></w:r></w:p>", extra: dict[str, str | bytes] | None = None,
         creator: str = "Alex") -> bytes:  # fmt: skip
    return zipped(
        {
            "[Content_Types].xml": CT,
            "_rels/.rels": RELS,
            "word/document.xml": f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>',
            "docProps/core.xml": core(creator),
            "docProps/app.xml": "<Properties><Company>Example Co</Company></Properties>",
            "docProps/thumbnail.jpeg": b"\xff\xd8\xff\xe0 not scanned",
            **(extra or {}),
        }
    )


def word_part(root: str, inner: str) -> str:
    return f'<w:{root} xmlns:w="{W}">{inner}</w:{root}>'


def xlsx(strings: tuple[str, ...] = ("Budget", "Total"), inline: str = "Notes") -> bytes:
    sst = "".join(f"<si><t>{s}</t></si>" for s in strings)
    sheet = (
        f'<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>'
        f'<c r="B1" t="inlineStr"><is><t>{inline}</t></is></c><c r="C1"><v>42</v></c></row></sheetData></worksheet>'
    )
    return zipped(
        {
            "[Content_Types].xml": CT,
            "_rels/.rels": RELS,
            "xl/workbook.xml": f'<workbook xmlns="{S}"><sheets><sheet name="Sheet1" sheetId="1"/></sheets></workbook>',
            "xl/sharedStrings.xml": f'<sst xmlns="{S}">{sst}</sst>',
            "xl/worksheets/sheet1.xml": sheet,
            "docProps/core.xml": core(),
        }
    )


def pptx(slide: str = "Roadmap", notes: str = "Speak slowly") -> bytes:
    def sp(text: str) -> str:
        return f"<p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"

    ns = f'xmlns:p="{P}" xmlns:a="{A}"'
    return zipped(
        {
            "[Content_Types].xml": CT,
            "_rels/.rels": RELS,
            "ppt/presentation.xml": f"<p:presentation {ns}/>",
            "ppt/slides/slide1.xml": f"<p:sld {ns}><p:cSld><p:spTree>{sp(slide)}</p:spTree></p:cSld></p:sld>",
            "ppt/notesSlides/notesSlide1.xml": (
                f"<p:notes {ns}><p:cSld><p:spTree>{sp(notes)}</p:spTree></p:cSld></p:notes>"
            ),
            "ppt/media/image1.png": b"\x89PNG\r\n\x1a\n not scanned",
            "docProps/core.xml": core(),
        }
    )


def odt(text: str = "Plain words only.", extra: dict[str, str | bytes] | None = None) -> bytes:
    office = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    tns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    return zipped(
        {
            "mimetype": "application/vnd.oasis.opendocument.text",
            "content.xml": (
                f'<office:document-content xmlns:office="{office}" xmlns:text="{tns}"><office:body><office:text>'
                f"<text:p>{text}</text:p></office:text></office:body></office:document-content>"
            ),
            "meta.xml": f'<office:document-meta xmlns:office="{office}"><office:meta/></office:document-meta>',
            "META-INF/manifest.xml": f'<manifest:manifest xmlns:manifest="{MANIFEST}"/>',
            **(extra or {}),
        },
        compression=zipfile.ZIP_STORED,
    )


def raw_zip(entries: list[tuple[str, int, bytes, bytes]]) -> bytes:
    """A zip written by hand: ``(name, method, payload as stored, the content it stands for)`` per entry.

    The sizes and CRC come from the content, the span from the payload, so a test can put bytes in a member's
    span that zipfile itself never reads.
    """
    local, central = bytearray(), bytearray()
    for name, method, payload, content in entries:
        n = name.encode()
        crc = zlib.crc32(content)
        head = struct.pack("<4s5H3L2H", b"PK\x03\x04", 20, 0, method, 0, 0x21, crc, len(payload), len(content),
                           len(n), 0)  # fmt: skip
        central += struct.pack("<4s6H3L5H2L", b"PK\x01\x02", 20, 20, 0, method, 0, 0x21, crc, len(payload),
                               len(content), len(n), 0, 0, 0, 0, 0, len(local)) + n  # fmt: skip
        local += head + n + payload
    end = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, len(entries), len(entries), len(central), len(local), 0)
    return bytes(local + central + end)


def deflated(content: bytes) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    return c.compress(content) + c.flush()


def mark_encrypted(data: bytes) -> bytes:
    """``data`` with every entry's "encrypted" flag set, as a password-protected zip has it."""
    out = bytearray(data)
    for sig, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = out.find(sig)
        while at != -1:
            (flags,) = struct.unpack_from("<H", out, at + offset)
            struct.pack_into("<H", out, at + offset, flags | 0x1)
            at = out.find(sig, at + 4)
    return bytes(out)
