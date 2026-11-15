"""scout.delegate_containers: the text a delegate mirror scans inside an Office file, and what it refuses."""

from __future__ import annotations

import zipfile

import pytest

from scout import delegate_containers as dc

from . import office_fixtures as of


def _text(data: bytes, ext: str = ".docx") -> str:
    got = dc.unpack(data, ext)
    assert got.refusal is None and got.text is not None, got
    return got.text


def test_runs_split_inside_a_word_are_joined_and_metadata_is_one_field_a_line() -> None:
    text = _text(of.docx("<w:p><w:r><w:t>Sam</w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>my</w:t></w:r></w:p>"))
    assert "Sammy" in text.splitlines()
    assert {"Alex", "Quarterly plan", "Example Co"} <= set(text.splitlines())


def test_attributes_comments_authors_and_member_names_are_text_too() -> None:
    comment = of.word_part(
        "comments", '<w:comment w:author="Priya" w:id="0"><w:p><w:r><w:t>ok</w:t></w:r></w:p></w:comment>'
    )
    text = _text(of.docx(extra={"word/comments.xml": comment, "word/notes on Priya.xml": "<x/>"}))
    lines = text.splitlines()
    assert "Priya" in lines and "word/notes on Priya.xml" in lines


def test_cells_shared_strings_slides_notes_and_odf_are_read() -> None:
    assert {"Budget", "Total", "Notes", "42"} <= set(_text(of.xlsx(), ".xlsx").splitlines())
    assert {"Roadmap", "Speak slowly"} <= set(_text(of.pptx(), ".pptx").splitlines())
    assert "Plain words only." in _text(of.odt(), ".odt").splitlines()
    cells = _text(of.xlsx(strings=("Ac", "me")), ".xlsx").splitlines()
    assert "Acme" not in cells  # two cells never join into one word


def test_an_xml_comment_reaches_the_scan_through_the_raw_part() -> None:
    assert "<!-- Sammy -->" in _text(of.docx(extra={"customXml/item1.xml": "<root><!-- Sammy --></root>"}))


@pytest.mark.parametrize(
    ("data", "ext", "why"),
    [
        (of.docx(extra={"word/vbaProject.bin": b"\xd0\xcf\x11\xe0 macro"}), ".docm", "macros"),
        (of.odt(extra={"Basic/Standard/Module1.xml": "<x/>"}), ".odt", "macros"),
        (of.mark_encrypted(of.docx()), ".docx", "encrypted"),
        (of.odt(extra={"META-INF/manifest.xml": "<m><encryption-data/></m>"}), ".odt", "encrypted"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 a password-protected Office file", ".docx", "unreadable"),
        (of.docx()[:-30], ".docx", "unreadable"),
        (of.docx(extra={"word/broken.xml": "<a><b></a>"}), ".docx", "unreadable"),
        (of.docx(extra={"word/embeddings/sheet.xlsx": of.xlsx()}), ".docx", "nested_archive"),
        (of.docx(extra={"word/embeddings/blob.dat": of.zipped({"a.txt": "x"})}), ".docx", "nested_archive"),
        (of.docx(extra={"word/embeddings/oleObject1.bin": b"\xd0\xcf\x11\xe0\x00\x01"}), ".docx", "unscannable_part"),
        (of.docx(extra={"word/x.xml": '<!DOCTYPE x [<!ENTITY a "Sammy">]><x>&a;</x>'}), ".docx", "xml_dtd"),
    ],
)
def test_what_cant_be_vouched_for_is_refused(data: bytes, ext: str, why: str) -> None:
    assert dc.unpack(data, ext) == dc.Unpacked(None, why)


def test_a_zip_bomb_is_capped_by_size_members_and_bytes_actually_read(monkeypatch: pytest.MonkeyPatch) -> None:
    bomb = of.docx(extra={"word/filler.xml": "<x>" + "a" * 2_000_000 + "</x>"})
    assert len(bomb) < 50_000  # it really is a small file that unpacks large
    monkeypatch.setattr(dc, "MAX_UNPACKED_BYTES", 1_000_000)
    assert dc.unpack(bomb, ".docx").refusal == "too_large"
    monkeypatch.setattr(dc, "MAX_UNPACKED_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr(dc, "MAX_MEMBERS", 5)
    assert dc.unpack(of.docx(), ".docx").refusal == "too_large"
    monkeypatch.setattr(dc, "MAX_MEMBERS", 2000)
    monkeypatch.setattr(dc, "MAX_FILE_BYTES", 100)
    assert dc.unpack(of.docx(), ".docx").refusal == "too_large"


def test_a_part_that_lies_about_its_size_is_stopped_while_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    data = of.docx(extra={"word/filler.xml": "<x>" + "a" * 500_000 + "</x>"})
    monkeypatch.setattr(dc, "MAX_UNPACKED_BYTES", 400_000)
    real = zipfile.ZipFile.infolist

    def shrunk(self: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        infos = real(self)
        for info in infos:  # every declared size says 10 bytes: only the read can catch it
            info.file_size = 10
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", shrunk)
    assert dc.unpack(data, ".docx").refusal in ("too_large", "unreadable")
    monkeypatch.setattr(dc, "_read", lambda *_a: None)
    assert dc.unpack(data, ".docx").refusal == "too_large"


def test_a_very_deep_document_does_not_overflow() -> None:
    deep = "<w:p>" * 5000 + "<w:r><w:t>deep</w:t></w:r>" + "</w:p>" * 5000
    got = dc.unpack(of.docx(deep), ".docx")
    assert got.refusal in (None, "unreadable")  # expat may refuse the depth; nothing raises
