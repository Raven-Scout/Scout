"""scout.delegate_containers: the text a delegate mirror scans inside an Office file, and what it refuses."""

from __future__ import annotations

import gzip
import io
import random
import struct
import zipfile
import zlib

import pytest

from scout import delegate_containers as dc
from scout import delegate_media as media
from scout import delegate_mirror as dm
from scout.kb.outbound import note_from_text, terms_of

from . import office_fixtures as of

SCAN = dm.Scanner(
    terms_of(
        [
            note_from_text("people/sam-lee.md", "---\nname: Sam Lee\naliases: [Sammy]\n---\n"),
            note_from_text("people/priya.md", "---\nname: Priya Sharma\naliases: [Priya]\n---\n"),
        ]
    )
)


def _named(got: dc.Unpacked) -> bool:
    return dm._readings_name_hidden(got.parts, SCAN, "x.docx") == "names_hidden"


def _clean(data: bytes, ext: str = ".docx") -> dc.Unpacked:
    got = dc.unpack(data, ext)
    assert got.refusal is None, got.refusal
    return got


def _runs(*parts: str) -> str:
    return "<w:p>" + "".join(parts) + "</w:p>"


def _r(text: str, tag: str = "t") -> str:
    return f'<w:r><w:{tag} xml:space="preserve">{text}</w:{tag}></w:r>'


# ----- what is read --------------------------------------------------------------------------


def test_a_clean_file_of_each_kind_names_nothing() -> None:
    for data, ext in ((of.docx(), ".docx"), (of.xlsx(), ".xlsx"), (of.pptx(), ".pptx"), (of.odt(), ".odt")):
        assert not _named(_clean(data, ext)), ext


def test_runs_split_inside_a_word_are_joined_and_metadata_is_one_field_a_line() -> None:
    got = _clean(of.docx(_runs(_r("Pri"), "<w:r><w:rPr><w:b/></w:rPr><w:t>ya</w:t></w:r>")))
    assert _named(got)
    lines = set(got.text.splitlines())
    assert {"Alex", "Quarterly plan", "Example Co"} <= lines  # app.xml's Company is read: it names an organisation


def test_attributes_comments_and_member_names_are_read() -> None:
    comment = of.word_part("comments", '<w:comment w:author="Priya" w:id="0"><w:p/></w:comment>')
    assert _named(_clean(of.docx(extra={"word/comments.xml": comment})))
    assert _named(_clean(of.docx(extra={"word/notes on Priya.xml": "<x/>"})))
    assert _named(_clean(of.docx(extra={"customXml/item1.xml": "<root><!-- Priya --></root>"})))
    assert _named(_clean(of.docx(extra={"customXml/item2.xml": "<root><?note Priya?></root>"})))


def test_cells_shared_strings_slides_notes_and_odf_are_read_and_cells_never_join() -> None:
    assert {"Budget", "Total", "Notes", "42"} <= set(_clean(of.xlsx(), ".xlsx").text.splitlines())
    assert _named(_clean(of.xlsx(strings=("Priya",)), ".xlsx"))
    assert _named(_clean(of.pptx(notes="ask Priya"), ".pptx"))
    assert _named(_clean(of.odt("Dear Priya,"), ".odt"))
    assert not _named(_clean(of.xlsx(strings=("Pri", "ya")), ".xlsx"))


@pytest.mark.parametrize(
    "body",
    [
        # a tracked typo fix inside the name: the final text names Priya, the markup interleaves "Priaya"
        _runs(_r("Pri"), f"<w:del>{_r('a', 'delText')}</w:del>", f"<w:ins>{_r('y')}</w:ins>", _r("a")),
        # the name deleted: only the original text names it
        _runs(_r("Pri"), f"<w:del>{_r('ya', 'delText')}</w:del>", f"<w:ins>{_r('nce')}</w:ins>"),
        # a hidden run and a field code inside the name
        _runs(_r("Pri"), "<w:r><w:rPr><w:vanish/></w:rPr><w:t>XX</w:t></w:r>", _r("ya")),
        _runs(_r("Pri"), _r(" PAGE ", "instrText"), _r("ya")),
        _runs(_r("Pri"), "<w:moveFrom><w:r><w:t>zz</w:t></w:r></w:moveFrom>", _r("ya")),
    ],
)
def test_tracked_changes_hidden_runs_and_field_codes_cannot_split_a_name(body: str) -> None:
    assert _named(_clean(of.docx(body)))


@pytest.mark.parametrize(
    "header", ["&amp;LPriya", "&amp;LPri&amp;Bya", '&amp;C&amp;"Arial,Bold"&amp;12Priya&amp;RPage']
)
def test_excel_header_and_footer_codes_cannot_hide_a_name(header: str) -> None:
    sheet = f'<worksheet xmlns="{of.S}"><headerFooter><oddHeader>{header}</oddHeader></headerFooter></worksheet>'
    data = of.zipped({"[Content_Types].xml": of.CT, "xl/worksheets/sheet1.xml": sheet})
    assert _named(_clean(data, ".xlsx"))


def test_phonetic_runs_do_not_split_a_name() -> None:
    sst = f'<sst xmlns="{of.S}"><si><r><t>Pri</t></r><rPh sb="0" eb="1"><t>x</t></rPh><r><t>ya</t></r></si></sst>'
    data = of.zipped({"[Content_Types].xml": of.CT, "xl/sharedStrings.xml": sst})
    assert _named(_clean(data, ".xlsx"))


def test_invisible_characters_inside_a_name_hide_nothing() -> None:
    assert _named(_clean(of.docx(_runs(_r("Pri​ya")))))
    assert _named(_clean(of.docx(_runs(_r("Pri­ya")))))


def test_utf16_parts_legacy_vml_and_known_binaries_are_read_not_refused() -> None:
    utf16 = '<?xml version="1.0" encoding="UTF-16"?><props><owner>Priya</owner></props>'.encode("utf-16")
    assert _named(_clean(of.docx(extra={"customXml/item1.xml": utf16})))
    vml = "<xml><v:shape><v:textbox><div>Priya<br></div></v:textbox></v:shape></xml>"
    assert _named(_clean(of.docx(extra={"xl/drawings/vmlDrawing1.vml": vml})))  # not well-formed: tags stripped
    devmode = "Office printer".encode("utf-16-le") + b"\x00" * 64 + "Priya".encode("utf-16-le")
    assert _named(_clean(of.docx(extra={"xl/printerSettings/printerSettings1.bin": devmode})))
    assert _clean(of.odt(extra={"layout-cache": b"\x00\x01\x02\x03cache"}), ".odt")


def test_media_parts_are_checked_by_their_bytes() -> None:
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    assert _clean(of.docx(extra={"word/media/image2.png": png}))
    assert _clean(of.docx(extra={"word/media/image3.png": b"\xff\xd8\xff\xe0 a JPEG named png"}))
    assert _named(_clean(of.docx(extra={"word/media/image4.emf": b"Priya as UTF-8 text"})))
    emf = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 20 + "Priya".encode("utf-16-le")
    assert _named(_clean(of.docx(extra={"word/media/image5.emf": emf})))
    svgz = gzip.compress(b"<svg xmlns='http://www.w3.org/2000/svg'><text>Priya</text></svg>")
    assert _named(_clean(of.docx(extra={"word/media/image6.svgz": svgz})))
    hidden = dc.unpack(of.docx(extra={"word/media/image7.png": of.docx(_runs(_r("Priya")))}), ".docx")
    assert hidden.refusal == "nested_archive"
    odd = dc.unpack(of.docx(extra={"word/media/image8.png": b"\x00\x01 not an image at all"}), ".docx")
    assert odd.refusal == "unscannable_part"


def test_a_file_written_with_data_descriptors_has_no_stray_bytes() -> None:
    class Stream(io.RawIOBase):
        def __init__(self) -> None:
            self.buf = io.BytesIO()

        def writable(self) -> bool:
            return True

        def write(self, b: object) -> int:
            return self.buf.write(b)  # type: ignore[arg-type]

    s = Stream()
    with zipfile.ZipFile(s, "w", zipfile.ZIP_DEFLATED) as zf:  # not seekable: zipfile writes descriptors
        zf.writestr("word/document.xml", f'<w:document xmlns:w="{of.W}"><w:body/></w:document>')
        zf.writestr("docProps/core.xml", of.core())
    data = s.buf.getvalue()
    assert zipfile.ZipFile(io.BytesIO(data)).infolist()[0].flag_bits & 0x8
    _clean(data)


# ----- what is refused -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "ext", "why"),
    [
        (of.docx(extra={"word/vbaProject.bin": b"\xd0\xcf\x11\xe0 macro"}), ".docm", "macros"),
        (of.odt(extra={"Basic/Standard/Module1.xml": "<x/>"}), ".odt", "macros"),
        (of.mark_encrypted(of.docx()), ".docx", "encrypted"),
        (of.odt(extra={"META-INF/manifest.xml": "<m><encryption-data/></m>"}), ".odt", "encrypted"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 a password-protected Office file", ".docx", "unreadable"),
        (of.docx()[:-30], ".docx", "unreadable"),
        (of.docx(extra={"word/broken.xml": "<a><b></a>"}), ".docx", "broken_part"),
        (of.docx(extra={"word/embeddings/sheet.xlsx": of.xlsx()}), ".docx", "nested_archive"),
        (of.docx(extra={"word/embeddings/blob.dat": of.zipped({"a.txt": "x"})}), ".docx", "nested_archive"),
        (of.docx(extra={"word/embeddings/oleObject1.bin": b"\x00\x01ole"}), ".docx", "unscannable_part"),
        (of.docx(extra={"word/x.xml": '<!DOCTYPE x [<!ENTITY a "y">]><x>&a;</x>'}), ".docx", "xml_dtd"),
        (b"some prefix\n" + of.docx(), ".docx", "stray_bytes"),
        (of.docx() + b"trailing", ".docx", "stray_bytes"),
    ],
)
def test_what_cant_be_vouched_for_is_refused(data: bytes, ext: str, why: str) -> None:
    assert dc.unpack(data, ext).refusal == why


@pytest.mark.parametrize(
    "data",
    [
        of.docx(_runs(_r("Priya")), extra={"word/vbaProject.bin": b"\xd0\xcf\x11\xe0 macro"}),
        of.docx(_runs(_r("Priya")), extra={"word/embeddings/sheet.xlsx": of.xlsx()}),
        of.docx(_runs(_r("Priya")), extra={"word/embeddings/oleObject1.bin": b"\x00\x01ole"}),
        of.docx(extra={"customXml/item1.xml": '<!DOCTYPE x [<!ENTITY a "y">]><x>Priya</x>'}),
        of.docx(extra={"word/embeddings/oleObject1.bin": b"\x00\x01" + "Priya".encode("utf-16-le")}),
        b"Priya Sharma, private\n" + of.docx(),
    ],
)
def test_a_refusal_never_stops_the_scan_of_what_can_be_read(data: bytes) -> None:
    got = dc.unpack(data, ".docm")
    assert got.refusal is not None and got.refusal not in dc.HARD
    assert _named(got)


def test_an_unknown_extra_field_is_stray_and_read() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("word/document.xml")
        info.extra = struct.pack("<HH", 0x4242, 5) + b"Priya"
        zf.writestr(info, "<x/>")
    got = dc.unpack(buf.getvalue(), ".docx")
    assert got.refusal == "stray_bytes" and _named(got)


def test_a_zip_bomb_is_capped_by_size_members_and_bytes_read(monkeypatch: pytest.MonkeyPatch) -> None:
    bomb = of.docx(extra={"word/filler.xml": "<x>" + "a" * 2_000_000 + "</x>"})
    assert len(bomb) < 50_000  # it really is a small file that unpacks large
    monkeypatch.setattr(dc, "MAX_UNPACKED_BYTES", 1_000_000)
    assert dc.unpack(bomb, ".docx").refusal == "too_large"
    monkeypatch.setattr(dc, "MAX_UNPACKED_BYTES", 16 * 1024 * 1024)
    monkeypatch.setattr(dc, "MAX_TEXT_CHARS", 100_000)
    assert dc.unpack(of.docx(extra={"word/f.xml": "<x>" + "a b " * 50_000 + "</x>"}), ".docx").refusal == "too_large"
    monkeypatch.setattr(dc, "MAX_FILE_BYTES", 100)
    assert dc.unpack(of.docx(), ".docx").refusal == "too_large"


def test_the_member_cap_is_read_from_the_end_record_before_the_directory_is_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = of.docx()
    monkeypatch.setattr(dc, "MAX_MEMBERS", 5)

    def never(*_a: object, **_k: object) -> object:
        raise AssertionError("ZipFile parsed a directory over the cap")

    monkeypatch.setattr(dc.zipfile, "ZipFile", never)
    assert dc.unpack(data, ".docx").refusal == "too_large"


def test_a_part_that_lies_about_its_size_is_unreadable() -> None:
    data = bytearray(of.docx(extra={"word/filler.xml": "<x>" + "a" * 500_000 + "</x>"}))
    at = data.find(b"PK\x01\x02")
    while at != -1:  # every central directory entry claims 10 bytes unpacked
        struct.pack_into("<L", data, at + 24, 10)
        at = data.find(b"PK\x01\x02", at + 4)
    assert dc.unpack(bytes(data), ".docx").refusal == "unreadable"  # the CRC no longer matches


def test_a_read_past_the_budget_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dc, "_read", lambda *_a: None)
    assert dc.unpack(of.docx(), ".docx").refusal == "too_large"


def test_a_very_deep_document_is_read_without_recursion() -> None:
    deep = "<w:p>" * 5000 + _r("Priya") + "</w:p>" * 5000
    got = dc.unpack(of.docx(deep), ".docx")
    assert got.refusal is None and _named(got)


def test_mutated_files_never_raise() -> None:
    rng = random.Random(550)
    seeds = [of.docx(_runs(_r("Plain"))), of.xlsx(), of.pptx(), of.odt()]
    for n in range(400):
        data = bytearray(rng.choice(seeds))
        for _ in range(rng.randint(1, 8)):
            kind = rng.random()
            at = rng.randrange(len(data))
            if kind < 0.6:
                data[at] = rng.randrange(256)
            elif kind < 0.8:
                del data[at : at + rng.randint(1, 64)]
            else:
                data[at:at] = rng.randbytes(rng.randint(1, 64))
        got = dc.unpack(bytes(data), rng.choice([".docx", ".odt"]))
        assert got.refusal is None or got.refusal in dc.REASONS, (n, got.refusal)


# ----- round 2 of the #639 review --------------------------------------------------------------

DOC = f'<w:document xmlns:w="{of.W}"><w:body><w:p><w:r><w:t>Plain</w:t></w:r></w:p></w:body></w:document>'.encode()


def test_a_hand_written_zip_with_exact_spans_is_clean() -> None:
    data = of.raw_zip([("word/document.xml", 8, of.deflated(DOC), DOC), ("docProps/app.xml", 0, b"<a/>", b"<a/>")])
    assert not _named(_clean(data))


@pytest.mark.parametrize(
    "entry",
    [
        ("word/document.xml", 8, of.deflated(DOC) + b"Sammy CANARY after the stream", DOC),  # zipfile stops at its end
        ("word/document.xml", 0, DOC + b"Sammy CANARY past file_size", DOC),  # zipfile reads file_size bytes
    ],
)
def test_bytes_inside_a_members_span_that_zipfile_never_reads_are_stray_and_read(
    entry: tuple[str, int, bytes, bytes],
) -> None:
    got = dc.unpack(of.raw_zip([entry]), ".docx")
    assert got.refusal == "stray_bytes"
    assert dm._readings_name_hidden(got.parts, SCAN, "x.docx") == "names_hidden"


@pytest.mark.parametrize("method", [12, 14, 99])  # bzip2, LZMA, unknown: Office writes none of them
def test_a_member_packed_other_than_stored_or_deflated_is_unreadable(method: int) -> None:
    assert dc.unpack(of.raw_zip([("word/document.xml", method, b"\x00garbage", DOC)]), ".docx").refusal == "unreadable"


def test_a_deflate_stream_that_ends_early_or_sizes_that_lie_are_unreadable() -> None:
    assert dc.unpack(of.raw_zip([("a.xml", 8, of.deflated(DOC)[:-5], DOC)]), ".docx").refusal == "unreadable"
    assert dc.unpack(of.raw_zip([("a.xml", 8, of.deflated(DOC), DOC + b"x")]), ".docx").refusal == "unreadable"
    assert dc.unpack(of.raw_zip([("a.xml", 0, DOC[:-1], DOC)]), ".docx").refusal == "unreadable"


def test_any_decoder_error_is_unreadable_never_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    import lzma

    def boom(*_a: object, **_k: object) -> None:
        raise lzma.LZMAError("corrupt input data")

    monkeypatch.setattr(dc, "_unpack", boom)
    assert dc.unpack(of.docx(), ".docx").refusal == "unreadable"


def test_an_embedded_document_is_read_one_level_deep() -> None:
    inner = of.docx(_runs(_r("Dear Priya")))
    for name in ("word/embeddings/Document1.docx", "word/media/image1.png", "word/embeddings/blob.bin"):
        got = dc.unpack(of.docx(extra={name: inner}), ".docx")
        assert got.refusal == "nested_archive" and _named(got), name
    deeper = of.docx(extra={"word/embeddings/outer.docx": of.docx(extra={"word/embeddings/inner.docx": inner})})
    got = dc.unpack(deeper, ".docx")
    assert got.refusal == "nested_archive" and not _named(got)  # two levels down: refused, not read


def test_an_embedded_document_shares_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    inner = of.docx(extra={"word/fill.xml": "<x>" + "a" * 600_000 + "</x>"})
    monkeypatch.setattr(dc, "MAX_UNPACKED_BYTES", 1_000_000)
    got = dc.unpack(of.docx(extra={"word/fill.xml": "<x>" + "a" * 600_000 + "</x>", "word/e/d.docx": inner}), ".docx")
    assert got.refusal == "too_large"


def test_is_zip_finds_a_zip_at_the_start_or_closing_the_file() -> None:
    assert dc.is_zip(of.docx()) and dc.is_zip(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50 + of.docx())
    assert not dc.is_zip(b"\x89PNG\r\n\x1a\n" + b"PK\x05\x06" + b"\x00" * 40)  # a stray marker that closes nothing


def test_label_only_types_are_checked_by_their_first_bytes() -> None:
    assert dc.looks_like("a.png", b"\x89PNG\r\n\x1a\n...") and not dc.looks_like("a.png", b"GIF89a")
    assert dc.looks_like("a.pdf", b"junk\n%PDF-1.7") and not dc.looks_like("a.pdf", b"plain")
    assert dc.looks_like("a.mp3", b"\xff\xfb\x90") and dc.looks_like("a.mov", b"\x00\x00\x00\x08wide")
    assert dc.looks_like("a.unknown", b"anything")


def test_media_metadata_reads_text_chunks_comments_and_what_follows_the_image() -> None:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">L", len(body)) + kind + body + b"\x00\x00\x00\x00"

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", b"\x00" * 13)
    text = png + chunk(b"tEXt", b"Author\x00Priya") + chunk(b"IDAT", b"Hidden pixels") + chunk(b"IEND", b"")
    meta = media.metadata("a.png", text)
    assert "Priya" in meta and "Hidden pixels" not in meta  # image data isn't read
    ztxt = png + chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(b"for Priya")) + chunk(b"IEND", b"")
    assert "for Priya" in media.metadata("a.png", ztxt)
    assert "Priya" in media.metadata("a.png", png + chunk(b"IEND", b"") + b"notes for Priya")
    jpeg = b"\xff\xd8" + b"\xff\xfe" + struct.pack(">H", 10) + b"Priya x." + b"\xff\xda\x00\x02scan\xff\xd9"
    assert "Priya" in media.metadata("a.jpg", jpeg)
    assert "Priya" in media.metadata("a.jpg", b"\xff\xd8\xff\xda\x00\x02scan\xff\xd9 trailing Priya")
    big = b"%PDF-1.7\n" + b"\x00" * 300_000 + b"<< /Author (Priya) >>"
    assert "Priya" in media.metadata("a.pdf", big) and media.metadata("a.png", b"\x89PNG\x00") is not None


# ----- every branch of the layout check and the part reader ----------------------------------------


def test_mixed_content_tails_are_read_in_every_reading() -> None:
    mixed = "<r>one <b>two</b> Priya</r>"
    assert _named(_clean(of.docx(extra={"customXml/item1.xml": mixed})))
    dropped = _runs(_r("Hello"), "<w:del/>Priya")  # a deleted run's tail is still in the final text
    assert _named(_clean(of.docx(dropped)))


def test_extra_fields_strong_encryption_unicode_paths_and_zip64_sizes() -> None:
    encrypted = struct.pack("<HH", 0x0017, 2) + b"\x00\x00"
    assert dc.unpack(of.raw_zip([("a.xml", 0, b"<a/>", b"<a/>", 0, b"", encrypted)]), ".docx").refusal == "encrypted"
    path = struct.pack("<HH", 0x7075, 10) + b"\x01\x00\x00\x00\x00Priya"
    assert _named(dc.unpack(of.raw_zip([("a.xml", 0, b"<a/>", b"<a/>", 0, b"", path)]), ".docx"))
    z64 = struct.pack("<HH", 0x0001, 16) + struct.pack("<QQ", 4, 4)  # the unpacked and the packed size
    data = bytearray(of.raw_zip([("a.xml", 0, b"<a/>", b"<a/>", 0, b"", z64)]))
    cd = data.find(b"PK\x01\x02")
    struct.pack_into("<LL", data, cd + 20, 0xFFFFFFFF, 0xFFFFFFFF)  # the sizes live in the zip64 field
    got = dc.unpack(bytes(data), ".docx")
    assert got.refusal is None  # a zip64 extra field is known, its values used where the header says so


def test_a_zip64_end_record_is_followed_and_a_broken_one_refused() -> None:
    data = of.docx()
    assert dc.unpack(of.zip64_end(data), ".docx").refusal is None
    no_locator = data[: data.rfind(b"PK\x05\x06")] + struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF,
                                                                    0xFFFFFFFF, 0xFFFFFFFF, 0)  # fmt: skip
    assert dc.unpack(no_locator, ".docx").refusal == "unreadable"
    moved = bytearray(of.zip64_end(data))
    at = moved.find(b"PK\x06\x06")
    moved[at : at + 4] = b"XXXX"  # the locator points at something that isn't the zip64 record
    assert dc.unpack(bytes(moved), ".docx").refusal == "unreadable"


def test_a_data_descriptor_without_its_signature_and_one_that_overruns() -> None:
    content = b"<a/>"
    plain = struct.pack("<3L", zlib.crc32(content), len(content), len(content))  # unsigned, 32-bit sizes
    assert dc.unpack(of.raw_zip([("a.xml", 0, content, content, 0x8, plain)]), ".docx").refusal is None
    assert dc.unpack(of.raw_zip([("a.xml", 0, content, content, 0x8)]), ".docx").refusal == "unreadable"


def test_directory_entries_are_skipped() -> None:
    assert _clean(of.docx(extra={"word/media/": b""}))


def test_a_nested_zip_that_cant_be_read_is_refused_without_stopping_the_outer_one() -> None:
    inner = of.docx(_runs(_r("Plain")))
    for broken in (of.mark_encrypted(inner), _bad_crc(inner)):
        got = dc.unpack(of.docx(_runs(_r("Dear Priya")), extra={"word/embeddings/d.docx": broken}), ".docx")
        assert got.refusal == "nested_archive" and _named(got)


def _bad_crc(data: bytes) -> bytes:
    """``data`` with every member's CRC wrong: its layout is whole, but zipfile refuses to read it."""
    out = bytearray(data)
    for sig, offset in ((b"PK\x03\x04", 14), (b"PK\x01\x02", 16)):
        at = out.find(sig)
        while at != -1:
            struct.pack_into("<L", out, at + offset, 0xDEADBEEF)
            at = out.find(sig, at + 4)
    return bytes(out)


def test_an_obfuscated_font_is_recognised_by_its_guid() -> None:
    guid = "{6C8B7F0A-1234-4ABC-9DEF-0123456789AB}"
    key = bytes.fromhex(guid.strip("{}").replace("-", ""))[::-1]
    font = b"\x00\x01\x00\x00" + b"\x10" * 60
    hidden = bytes(b ^ key[i % 16] for i, b in enumerate(font[:32])) + font[32:]
    assert _clean(of.docx(extra={f"word/fonts/{guid}.odttf": hidden}))
    assert dc.unpack(of.docx(extra={"word/fonts/font1.odttf": hidden}), ".docx").refusal == "unscannable_part"


def test_a_svgz_past_the_budget_and_an_image_whose_metadata_breaks(monkeypatch: pytest.MonkeyPatch) -> None:
    svgz = gzip.compress(b"<svg>" + b" " * 500_000 + b"</svg>")
    monkeypatch.setattr(dc, "MAX_UNPACKED_BYTES", 300_000)
    assert dc.unpack(of.docx(extra={"word/media/big.svgz": svgz}), ".docx").refusal == "too_large"
    monkeypatch.undo()
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">L", 9) + b"zTXtKey\x00\x00xx\x00\x00\x00\x00"
    assert dc.unpack(of.docx(extra={"word/media/a.png": png}), ".docx").refusal == "broken_part"
