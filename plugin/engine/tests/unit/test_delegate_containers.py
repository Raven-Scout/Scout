"""scout.delegate_containers: the text a delegate mirror scans inside an Office file, and what it refuses."""

from __future__ import annotations

import gzip
import io
import random
import struct
import zipfile

import pytest

from scout import delegate_containers as dc
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
    assert {"Alex", "Quarterly plan", "Example Co"} <= set(got.text.splitlines())


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
