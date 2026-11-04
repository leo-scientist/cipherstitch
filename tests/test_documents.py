import re
import struct
import sys
import time
import zipfile
import zlib
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from cipherstitch import catalog, container, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.container import UnitOrder
from cipherstitch.errors import (
    CapacityError,
    NoHiddenDataError,
    NotAvailableError,
    UnsupportedFormatError,
)
from cipherstitch.media import docx as docx_media
from cipherstitch.media import pdf as pdf_media
from cipherstitch.methods import object_stream, whitespace
from cipherstitch.methods.whitespace import WhitespaceChannel
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions

from .conftest import plain_method

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
FILE = HiddenFile("note.txt", "Таємниця".encode() * 20)
TRAILING = " " + chr(0xA0)
PARAGRAPHS = 80
CONTENT = b"BT /F1 12 Tf 72 720 Td (Hello) Tj ET"
MAIN_RELATIONSHIPS = "word/_rels/document.xml.rels"
PACKAGE_LISTS = ("_rels/.rels", MAIN_RELATIONSHIPS, "[Content_Types].xml")
WORD_DATE = (1980, 1, 1, 0, 0, 0)
WORD_ZIP_VERSION = 45
WORD_DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + bytes([13, 10])
WORDPROCESSING = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CENTRAL_ENTRY_NAME = 46  # where a central directory entry's file name starts
LOCAL_HEADER_EXTRA = 28  # a local file header's extra field length
LOCAL_HEADER_NAME = 30


def _options(method: MethodId, cipher: CipherId) -> HideOptions:
    password = None if cipher == CipherId.NONE else PASSWORD

    return HideOptions(CarrierKind.DOCUMENT, method, cipher, password, is_research=True)


@pytest.fixture
def make_pdf(tmp_path: Path) -> Path:
    pikepdf = pytest.importorskip("pikepdf")
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.pages[0].Contents = pdf.make_stream(CONTENT)
    path = tmp_path / "cover.pdf"
    pdf.save(path)

    return path


@pytest.fixture
def make_docx(tmp_path: Path) -> Path:
    docx = pytest.importorskip("docx")
    document = docx.Document()

    for index in range(PARAGRAPHS - 2):
        document.add_paragraph(f"Paragraph {index}: a manuscript keeps traces.")

    document.add_paragraph("This one already ends with spaces   ")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "A table cell."
    path = tmp_path / "cover.docx"
    document.save(str(path))

    return path


def _paragraph_texts(path: Path) -> list[str]:
    docx = pytest.importorskip("docx")
    document = docx.Document(str(path))
    texts = [paragraph.text for paragraph in document.paragraphs]
    cells = [cell.text for table in document.tables for row in table.rows for cell in row.cells]

    return texts + cells


def _reveal(path: Path, tmp_path: Path, password: str | None) -> service.RevealResult:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    result = service.reveal(path, out, password)
    assert result.path.read_bytes() == FILE.data
    result.path.unlink()

    return result


@pytest.mark.parametrize("cipher", [CipherId.AES_GCM, CipherId.NONE], ids=["encrypted", "plain"])
def test_pdf_object_stream_roundtrip(make_pdf: Path, tmp_path: Path, cipher: CipherId) -> None:
    pikepdf = pytest.importorskip("pikepdf")
    embedded = tmp_path / "embedded.pdf"
    service.hide(make_pdf, FILE, embedded, _options(MethodId.OBJECT_STREAM, cipher))

    with pikepdf.open(embedded) as pdf:
        assert pdf.check_pdf_syntax() == []
        assert len(pdf.pages) == 1
        assert pdf.pages[0].Contents.read_bytes() == CONTENT
        assert pdf.pdf_version >= "1.4"
        data = pdf.Root[pdf_media.PIECE_INFO][pdf_media.APPLICATION]
        assert str(data.LastModified).startswith("D:")
        assert isinstance(data.Private, pikepdf.Stream)

    assert b"cipherstitch" not in embedded.read_bytes().lower()
    password = None if cipher == CipherId.NONE else PASSWORD
    result = _reveal(embedded, tmp_path, password)
    assert (result.method, result.cipher) == (MethodId.OBJECT_STREAM, cipher)


def test_pdf_keeps_its_encryption_and_restrictions(tmp_path: Path) -> None:
    pikepdf = pytest.importorskip("pikepdf")
    cover = tmp_path / "restricted.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    no_copying = pikepdf.Permissions(extract=False)
    pdf.save(cover, encryption=pikepdf.Encryption(owner="owner-secret", user="", allow=no_copying))
    embedded = tmp_path / "embedded.pdf"
    service.hide(cover, FILE, embedded, _options(MethodId.OBJECT_STREAM, CipherId.AES_GCM))

    with pikepdf.open(embedded) as result:
        assert result.is_encrypted
        assert not result.allow.extract

    _reveal(embedded, tmp_path, PASSWORD)


def _pdf_with_payload_stream(path: Path, data: bytes) -> Path:
    pikepdf = pytest.importorskip("pikepdf")
    pdf = pikepdf.new()
    pdf.add_blank_page()
    stream = pikepdf.Stream(pdf, data)
    stream.Filter = pikepdf.Name.FlateDecode
    editor = pikepdf.Dictionary(LastModified="D:20261016000000Z", Private=stream)
    pdf.Root[pdf_media.PIECE_INFO] = pikepdf.Dictionary({pdf_media.APPLICATION: editor})
    pdf.save(path)

    return path


def test_pdf_payload_read_is_bounded(tmp_path: Path) -> None:
    """16 MiB of zeros compress to ~16 KiB; only the soft limit is ever decoded."""
    bomb = _pdf_with_payload_stream(tmp_path / "bomb.pdf", zlib.compress(bytes(16 << 20)))
    assert pdf_media.read_data(bomb, 1024) == bytes(1024)
    assert plain_method(bomb) is None


def test_corrupt_pdf_payload_stream_is_rejected(tmp_path: Path) -> None:
    corrupt = _pdf_with_payload_stream(tmp_path / "corrupt.pdf", b"not deflate data")

    with pytest.raises(UnsupportedFormatError, match="not a readable PDF"):
        plain_method(corrupt)


def _rewritten_package(source: Path, target: Path, changes: Mapping[str, bytes | None]) -> Path:
    """A copy of the package ``source`` with entries replaced, added or (``None``) dropped."""
    with zipfile.ZipFile(source) as package, zipfile.ZipFile(target, "w") as output:
        entries = {info.filename: package.read(info) for info in package.infolist()}

        for name, content in {**entries, **changes}.items():
            if content is not None:
                output.writestr(name, content)

    return target


def _printer_settings_linked(
    relationships: bytes, target: str, identifier: str = "rId99", *, end: str = "/>"
) -> bytes:
    """``relationships`` with a printer-settings relationship to ``target`` added."""
    link = (
        f'<Relationship Id="{identifier}" Type="{docx_media.PRINTER_SETTINGS}" '
        f'Target="{target}"{end}'
    )

    return relationships.replace(b"</Relationships>", link.encode() + b"</Relationships>")


def _prefixed(relationships: bytes) -> bytes:
    content = relationships.replace(b"<Relationships xmlns=", b"<r:Relationships xmlns:r=")
    content = content.replace(b"<Relationship ", b"<r:Relationship ")

    return content.replace(b"</Relationships>", b"</r:Relationships>")


def _open_link(relationships: bytes) -> bytes:
    target = "printerSettings/printerSettings2.bin"

    return _printer_settings_linked(relationships, target, end="></Relationship>")


def _emptied(relationships: bytes) -> bytes:
    start = relationships.index(b"<Relationships")
    end = relationships.index(b">", start)

    return relationships[:end] + b"/>"


@pytest.mark.parametrize(
    "change",
    [_prefixed, _open_link, _emptied, lambda _relationships: None],
    ids=["prefixed", "open-element", "empty", "missing"],
)
def test_unsupported_package_layout_leaves_the_carrier_intact(
    make_docx: Path, change: Callable[[bytes], bytes | None]
) -> None:
    with zipfile.ZipFile(make_docx) as package:
        relationships = package.read(MAIN_RELATIONSHIPS)

    changed = change(relationships)
    cover = _rewritten_package(
        make_docx, make_docx.with_name("odd.docx"), {MAIN_RELATIONSHIPS: changed}
    )
    original = cover.read_bytes()
    options = _options(MethodId.OBJECT_STREAM, CipherId.NONE)

    with pytest.raises(UnsupportedFormatError, match="unsupported package layout"):
        service.hide(cover, FILE, cover, options)

    assert cover.read_bytes() == original


@pytest.mark.parametrize("cipher", [CipherId.AES_GCM, CipherId.NONE], ids=["encrypted", "plain"])
def test_docx_object_stream_roundtrip(make_docx: Path, tmp_path: Path, cipher: CipherId) -> None:
    embedded = tmp_path / "embedded.docx"
    service.hide(make_docx, FILE, embedded, _options(MethodId.OBJECT_STREAM, cipher))

    assert _paragraph_texts(embedded) == _paragraph_texts(make_docx)

    with zipfile.ZipFile(embedded) as package:
        assert docx_media.PART in package.namelist()
        assert docx_media.PRINTER_SETTINGS.encode() in package.read(MAIN_RELATIONSHIPS)
        assert docx_media.PART.encode() in package.read("[Content_Types].xml")
        assert not any("cipherstitch" in name for name in package.namelist())
        assert not any(b"cipherstitch" in package.read(name) for name in PACKAGE_LISTS)

    password = None if cipher == CipherId.NONE else PASSWORD
    result = _reveal(embedded, tmp_path, password)
    assert (result.method, result.cipher) == (MethodId.OBJECT_STREAM, cipher)


def test_hiding_again_in_place_replaces_the_payload(make_docx: Path, tmp_path: Path) -> None:
    options = _options(MethodId.OBJECT_STREAM, CipherId.AES_GCM)
    service.hide(make_docx, HiddenFile("old.txt", b"old"), make_docx, options)
    service.hide(make_docx, FILE, make_docx, options)

    with zipfile.ZipFile(make_docx) as package:
        assert package.namelist().count(docx_media.PART) == 1
        assert package.read(MAIN_RELATIONSHIPS).count(docx_media.PRINTER_SETTINGS.encode()) == 1
        assert package.read("[Content_Types].xml").count(docx_media.PART.encode()) == 1

    assert _reveal(make_docx, tmp_path, PASSWORD).path.name == FILE.name


def _word_like(source: Path, target: Path) -> Path:
    """``source`` as Word writes a package: every entry dated 1980-01-01 and made on MS-DOS
    by zip 4.5, in another order than python-docx's, and Word's XML declarations. The
    thumbnail is stored uncompressed, as some writers store pictures."""
    with zipfile.ZipFile(source) as package, zipfile.ZipFile(target, "w") as output:
        types, *others = package.infolist()

        for info in [types, *reversed(others)]:
            entry = zipfile.ZipInfo(info.filename, date_time=WORD_DATE)
            entry.create_system = 0
            entry.create_version = WORD_ZIP_VERSION
            is_picture = info.filename.endswith(".jpeg")
            entry.compress_type = zipfile.ZIP_STORED if is_picture else zipfile.ZIP_DEFLATED
            content = package.read(info)

            if content.startswith(b"<?xml"):
                content = WORD_DECLARATION + content.split(b"?>", 1)[1].lstrip()

            output.writestr(entry, content)

    return target


@pytest.mark.parametrize("method", [MethodId.OBJECT_STREAM, MethodId.WHITESPACE])
def test_entries_keep_the_covers_dates(make_docx: Path, tmp_path: Path, method: MethodId) -> None:
    """Entries the cover lacks, such as the printer settings, take the main document's."""
    cover = _word_like(make_docx, tmp_path / "word.docx")
    embedded = tmp_path / "embedded.docx"
    service.hide(cover, FILE, embedded, _options(method, CipherId.AES_GCM))

    with zipfile.ZipFile(cover) as before, zipfile.ZipFile(embedded) as after:
        covers = {info.filename: info for info in before.infolist()}
        entries = after.infolist()

    for entry in entries:
        like = covers.get(entry.filename, covers["word/document.xml"])
        found = (entry.date_time, entry.create_system, entry.create_version, entry.compress_type)
        expected = (like.date_time, like.create_system, like.create_version, like.compress_type)
        assert found == expected, entry.filename

    assert {(entry.date_time, entry.create_version) for entry in entries} == {
        (WORD_DATE, WORD_ZIP_VERSION)
    }
    assert zipfile.ZIP_STORED in {entry.compress_type for entry in entries}
    assert docx_media.PART not in covers
    assert any(entry.filename == docx_media.PART for entry in entries) is (
        method == MethodId.OBJECT_STREAM
    )
    _reveal(embedded, tmp_path, PASSWORD)


def test_whitespace_changes_only_the_main_document(make_docx: Path, tmp_path: Path) -> None:
    """Every other entry stays the cover's, in its order and byte for byte."""
    cover = _word_like(make_docx, tmp_path / "word.docx")
    embedded = tmp_path / "embedded.docx"
    service.hide(cover, FILE, embedded, _options(MethodId.WHITESPACE, CipherId.AES_GCM))

    with zipfile.ZipFile(cover) as before, zipfile.ZipFile(embedded) as after:
        names = after.namelist()
        assert names == before.namelist()
        assert [name for name in names if after.read(name) != before.read(name)] == [
            "word/document.xml"
        ]
        assert after.read("word/document.xml").startswith(WORD_DECLARATION + b"<w:document ")

    _reveal(embedded, tmp_path, PASSWORD)


def test_pdf_keeps_other_applications_data(make_pdf: Path, tmp_path: Path) -> None:
    pikepdf = pytest.importorskip("pikepdf")

    with pikepdf.open(make_pdf, allow_overwriting_input=True) as pdf:
        other = pikepdf.Dictionary(LastModified="D:20200101000000Z", Private=pikepdf.Name.Kept)
        pdf.Root[pdf_media.PIECE_INFO] = pikepdf.Dictionary(Illustrator=other)
        pdf.save(make_pdf)

    options = _options(MethodId.OBJECT_STREAM, CipherId.AES_GCM)
    service.hide(make_pdf, HiddenFile("old.txt", b"old"), make_pdf, options)
    service.hide(make_pdf, FILE, make_pdf, options)

    with pikepdf.open(make_pdf) as pdf:
        pieces = pdf.Root[pdf_media.PIECE_INFO]
        assert set(pieces.keys()) == {"/Illustrator", pdf_media.APPLICATION}
        assert pieces.Illustrator.Private == pikepdf.Name.Kept

    assert _reveal(make_pdf, tmp_path, PASSWORD).path.name == FILE.name


@pytest.mark.parametrize("method", [MethodId.OBJECT_STREAM, MethodId.WHITESPACE])
@pytest.mark.parametrize(
    "target",
    ["printerSettings/printerSettings3.bin", "/word/printerSettings/printerSettings3.bin"],
    ids=["relative", "absolute"],
)
def test_hiding_replaces_the_printer_settings(
    make_docx: Path, tmp_path: Path, target: str, method: MethodId
) -> None:
    """A cover with two sections, each referring to its own printer settings."""
    own = ("word/printerSettings/printerSettings2.bin", "word/printerSettings/printerSettings3.bin")

    with zipfile.ZipFile(make_docx) as package:
        relationships = package.read(MAIN_RELATIONSHIPS)
        content_types = package.read("[Content_Types].xml")
        document = package.read("word/document.xml")

    first = _printer_settings_linked(relationships, "printerSettings/printerSettings2.bin", "rId98")
    printer_settings = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.printerSettings"
    )
    overrides = "".join(
        f'<Override PartName="/{part}" ContentType="{printer_settings}"/>' for part in own
    )
    first_section = (
        b'<w:p><w:pPr><w:sectPr><w:printerSettings r:id="rId98"/></w:sectPr></w:pPr></w:p>'
    )
    document = document.replace(b"<w:body>", b"<w:body>" + first_section)
    changes = {
        MAIN_RELATIONSHIPS: _printer_settings_linked(first, target, "rId99"),
        "[Content_Types].xml": content_types.replace(b"</Types>", overrides.encode() + b"</Types>"),
        "word/document.xml": document.replace(
            b"</w:sectPr></w:body>", b'<w:printerSettings r:id="rId99"/></w:sectPr></w:body>'
        ),
    }
    cover = _rewritten_package(
        make_docx, tmp_path / "printer.docx", {**changes, **dict.fromkeys(own, b"settings")}
    )
    assert docx_media.read_part(cover, 64) == b"settings"
    embedded = tmp_path / "embedded.docx"
    service.hide(cover, FILE, embedded, _options(method, CipherId.AES_GCM))

    with zipfile.ZipFile(embedded) as package:
        assert not set(own) & set(package.namelist())
        relationships = package.read(MAIN_RELATIONSHIPS)
        links = relationships.count(docx_media.PRINTER_SETTINGS.encode())
        assert links == (method == MethodId.OBJECT_STREAM)
        content_types = package.read("[Content_Types].xml")
        assert not any(part.encode() in content_types for part in own)
        document = package.read("word/document.xml")
        references = set(re.findall(rb'\br:id="([^"]+)"', document))
        assert references <= set(re.findall(rb'\bId="([^"]+)"', relationships))
        assert b"<w:printerSettings" not in document

    assert _reveal(embedded, tmp_path, PASSWORD).method == method


@pytest.mark.parametrize("method", [MethodId.OBJECT_STREAM, MethodId.WHITESPACE])
def test_a_printer_settings_link_to_another_part_keeps_that_part(
    make_docx: Path, tmp_path: Path, method: MethodId
) -> None:
    with zipfile.ZipFile(make_docx) as package:
        relationships = package.read(MAIN_RELATIONSHIPS)

    changes = {MAIN_RELATIONSHIPS: _printer_settings_linked(relationships, "styles.xml")}
    cover = _rewritten_package(make_docx, tmp_path / "odd.docx", changes)
    embedded = tmp_path / "embedded.docx"
    service.hide(cover, FILE, embedded, _options(method, CipherId.AES_GCM))

    with zipfile.ZipFile(embedded) as package:
        assert "word/styles.xml" in package.namelist()

    assert _paragraph_texts(embedded)[0].rstrip(TRAILING) == _paragraph_texts(cover)[0]
    assert _reveal(embedded, tmp_path, PASSWORD).method == method


@pytest.mark.parametrize("cover", ["make_pdf", "make_docx"])
def test_object_length_does_not_show_the_payload_size(
    cover: str, request: pytest.FixtureRequest
) -> None:
    channel = object_stream.open_channel(request.getfixturevalue(cover))
    stream = b"salt" + bytes(100)
    objects = [channel.write(stream[:4], stream[4:], None).data for _ in range(8)]

    assert len({len(data) for data in objects}) > 1
    assert all(data.startswith(stream) for data in objects)
    assert all(len(data) <= object_stream.SOFT_LIMIT for data in objects)
    full = bytes(object_stream.SOFT_LIMIT)
    assert channel.write(b"", full, None).data == full


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (MethodId.WHITESPACE, MethodId.OBJECT_STREAM),
        (MethodId.OBJECT_STREAM, MethodId.WHITESPACE),
    ],
    ids=["whitespace-then-object", "object-then-whitespace"],
)
def test_hiding_with_the_other_method_replaces_the_payload(
    make_docx: Path, tmp_path: Path, first: MethodId, second: MethodId
) -> None:
    service.hide(
        make_docx, HiddenFile("old.txt", b"old"), make_docx, _options(first, CipherId.NONE)
    )
    service.hide(make_docx, FILE, make_docx, _options(second, CipherId.AES_GCM))
    assert plain_method(make_docx) is None  # no stale unencrypted payload is left
    assert _reveal(make_docx, tmp_path, PASSWORD).method == second

    with zipfile.ZipFile(make_docx) as package:
        assert (docx_media.PART in package.namelist()) == (second == MethodId.OBJECT_STREAM)
        content_types = package.read("[Content_Types].xml")
        assert (b"printerSettings" in content_types) == (second == MethodId.OBJECT_STREAM)


def test_reveal_ignores_a_method_that_cannot_read_the_file(make_docx: Path, tmp_path: Path) -> None:
    """A package python-docx rejects (here: broken body XML) still reveals its object."""
    broken = tmp_path / "broken.docx"

    with zipfile.ZipFile(make_docx) as source, zipfile.ZipFile(broken, "w") as target:
        for info in source.infolist():
            content = source.read(info)
            target.writestr(
                info, b"<w:document" if info.filename == "word/document.xml" else content
            )

    embedded = tmp_path / "embedded.docx"
    service.hide(broken, FILE, embedded, _options(MethodId.OBJECT_STREAM, CipherId.AES_GCM))
    assert _reveal(embedded, tmp_path, PASSWORD).method == MethodId.OBJECT_STREAM


def test_whitespace_before_a_tab_is_kept(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    run = document.add_paragraph().add_run("Name: ")
    run.add_tab()

    for index in range(60):
        document.add_paragraph(f"Line {index}.")

    cover = tmp_path / "form.docx"
    document.save(str(cover))
    embedded = tmp_path / "embedded.docx"
    service.hide(cover, FILE, embedded, _options(MethodId.WHITESPACE, CipherId.NONE))
    assert docx.Document(str(embedded)).paragraphs[0].text.startswith("Name: \t")
    assert _reveal(embedded, tmp_path, None).method == MethodId.WHITESPACE


def test_whitespace_before_an_inline_equation_is_kept(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    from docx.oxml import OxmlElement

    document = docx.Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("The energy is ")
    equation, math_run, math_text = (OxmlElement(tag) for tag in ("m:oMath", "m:r", "m:t"))
    math_text.text = "E=mc2"
    math_run.append(math_text)
    equation.append(math_run)
    paragraph._p.append(equation)

    for index in range(60):
        document.add_paragraph(f"Line {index}.")

    cover = tmp_path / "physics.docx"
    document.save(str(cover))
    embedded = tmp_path / "embedded.docx"
    service.hide(cover, FILE, embedded, _options(MethodId.WHITESPACE, CipherId.NONE))
    assert docx.Document(str(embedded)).paragraphs[0].runs[0].text == "The energy is "
    assert _reveal(embedded, tmp_path, None).method == MethodId.WHITESPACE


@pytest.mark.parametrize("cipher", [CipherId.AES_GCM, CipherId.NONE], ids=["encrypted", "plain"])
def test_whitespace_roundtrip_keeps_the_visible_text(
    make_docx: Path, tmp_path: Path, cipher: CipherId
) -> None:
    embedded = tmp_path / "embedded.docx"
    service.hide(make_docx, FILE, embedded, _options(MethodId.WHITESPACE, cipher))

    before, after = _paragraph_texts(make_docx), _paragraph_texts(embedded)
    assert [text.rstrip(TRAILING) for text in after] == [text.rstrip(TRAILING) for text in before]
    trailing = [text[len(text.rstrip(TRAILING)) :] for text in after]
    assert max(len(run) for run in trailing) <= 64
    assert sum(len(run) for run in trailing) > 0
    password = None if cipher == CipherId.NONE else PASSWORD
    result = _reveal(embedded, tmp_path, password)
    assert (result.method, result.cipher) == (MethodId.WHITESPACE, cipher)

    if cipher == CipherId.NONE:
        assert plain_method(embedded) == MethodId.WHITESPACE


def test_whitespace_length_does_not_show_the_payload_size(make_docx: Path, tmp_path: Path) -> None:
    channel = WhitespaceChannel(make_docx)
    stream = b"salt" + bytes(100)
    lengths = set()

    for index in range(8):
        embedded = tmp_path / f"embedded{index}.docx"
        channel.write(stream[:4], stream[4:], None).save(embedded)
        assert WhitespaceChannel(embedded).read(0, len(stream), None) == stream
        texts = _paragraph_texts(embedded)
        lengths.add(sum(len(text) - len(text.rstrip(TRAILING)) for text in texts))

    assert len(lengths) > 1
    assert 8 * len(stream) <= min(lengths) <= max(lengths) <= 8 * channel.capacity(0)


def test_whitespace_capacity_is_exact(make_docx: Path) -> None:
    channel = WhitespaceChannel(make_docx)
    assert channel.capacity(0) == PARAGRAPHS * 64 // 8
    limit = container.max_data_size(channel, FILE.name, CipherId.AES_GCM)
    container.hide(
        channel,
        HiddenFile(FILE.name, bytes(limit)),
        method=MethodId.WHITESPACE,
        cipher_id=CipherId.AES_GCM,
        password=PASSWORD,
        order=UnitOrder.PUBLIC,
    )

    with pytest.raises(CapacityError):
        container.hide(
            channel,
            HiddenFile(FILE.name, bytes(limit + 1)),
            method=MethodId.WHITESPACE,
            cipher_id=CipherId.AES_GCM,
            password=PASSWORD,
            order=UnitOrder.PUBLIC,
        )


def test_clean_documents_hold_nothing(make_pdf: Path, make_docx: Path, tmp_path: Path) -> None:
    for document in (make_pdf, make_docx):
        assert plain_method(document) is None

        with pytest.raises(NoHiddenDataError):
            service.reveal(document, tmp_path, PASSWORD)


def test_soft_limit_is_the_object_stream_capacity(make_docx: Path, tmp_path: Path) -> None:
    options = _options(MethodId.OBJECT_STREAM, CipherId.NONE)
    assert service.estimate(make_docx, "a", 1, options).available == object_stream.SOFT_LIMIT
    too_big = HiddenFile("big.bin", bytes(object_stream.SOFT_LIMIT))

    with pytest.raises(CapacityError):
        service.hide(make_docx, too_big, tmp_path / "embedded.docx", options)


def test_describe(make_pdf: Path, make_docx: Path) -> None:
    pdf_details = service.describe_carrier(make_pdf, CarrierKind.DOCUMENT)
    assert pdf_details.description.startswith("1 page · ")
    assert pdf_details.description.endswith(" KiB · PDF")
    assert pdf_details.capacity == object_stream.SOFT_LIMIT
    assert service.describe_carrier(make_docx, CarrierKind.DOCUMENT).description.endswith(
        " KiB · DOCX"
    )


def test_output_keeps_the_carrier_format(make_docx: Path, tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFormatError, match=r"\.docx"):
        service.hide(
            make_docx,
            FILE,
            tmp_path / "embedded.pdf",
            _options(MethodId.OBJECT_STREAM, CipherId.NONE),
        )

    reason = service.unavailable_reason(
        _options(MethodId.WHITESPACE, CipherId.NONE), Path("report.pdf")
    )
    assert reason == "Whitespace padding can't use .pdf files"


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("junk.pdf", b"%PDF-1.7 not really", "not a readable PDF"),
        ("junk.docx", b"not a zip", "not a DOCX file"),
    ],
)
def test_unreadable_documents_are_rejected(
    tmp_path: Path, name: str, content: bytes, message: str
) -> None:
    pytest.importorskip("pikepdf")
    path = tmp_path / name
    path.write_bytes(content)

    with pytest.raises(UnsupportedFormatError, match=message):
        service.capacity(path, research=True)


def test_an_oversized_relationships_part_is_refused(
    make_docx: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with zipfile.ZipFile(make_docx) as package:
        size = package.getinfo(MAIN_RELATIONSHIPS).file_size

    monkeypatch.setattr(docx_media, "_MAX_LIST_SIZE", size)
    assert docx_media.read_part(make_docx, 1) == b""
    monkeypatch.setattr(docx_media, "_MAX_LIST_SIZE", size - 1)

    with pytest.raises(UnsupportedFormatError, match="too large to read"):
        docx_media.read_part(make_docx, 1)


@pytest.mark.parametrize("cap", ["_MAX_PARTS", "_MAX_PACKAGE_SIZE", "_MAX_XML_SIZE"])
def test_whitespace_refuses_a_package_too_large_to_load(
    make_docx: Path, monkeypatch: pytest.MonkeyPatch, cap: str
) -> None:
    with zipfile.ZipFile(make_docx) as package:
        entries = package.infolist()

    used = {
        "_MAX_PARTS": len(entries),
        "_MAX_PACKAGE_SIZE": sum(entry.file_size for entry in entries),
        "_MAX_XML_SIZE": sum(
            entry.file_size for entry in entries if entry.filename.endswith((".xml", ".rels"))
        ),
    }
    monkeypatch.setattr(docx_media, cap, used[cap])
    WhitespaceChannel(make_docx)
    monkeypatch.setattr(docx_media, cap, used[cap] - 1)

    with pytest.raises(UnsupportedFormatError, match="too large to read"):
        WhitespaceChannel(make_docx)


@pytest.mark.parametrize(
    ("content_type", "is_refused"), [("application/xml", True), ("image/png", False)]
)
def test_the_xml_cap_goes_by_content_type_not_name(
    make_docx: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content_type: str,
    is_refused: bool,
) -> None:
    """python-docx parses a part by its declared content type, whatever its name."""
    extra = b"<w:p/>" * 4096

    with zipfile.ZipFile(make_docx) as package:
        types = package.read("[Content_Types].xml")
        entries = package.infolist()

    xml_size = sum(
        entry.file_size for entry in entries if entry.filename.endswith((".xml", ".rels"))
    )
    override = f'<Override PartName="/word/extra.bin" ContentType="{content_type}"/>'.encode()
    changes = {
        "word/extra.bin": extra,
        "[Content_Types].xml": types.replace(b"</Types>", override + b"</Types>"),
    }
    cover = _rewritten_package(make_docx, tmp_path / "extra.docx", changes)
    monkeypatch.setattr(docx_media, "_MAX_XML_SIZE", xml_size + len(override) + len(extra) - 1)

    if is_refused:
        with pytest.raises(UnsupportedFormatError, match="too large to read"):
            WhitespaceChannel(cover)
    else:
        WhitespaceChannel(cover)


def test_whitespace_refuses_more_paragraphs_than_it_reads_quickly(
    make_docx: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(whitespace, "_MAX_PARAGRAPHS", PARAGRAPHS)
    assert WhitespaceChannel(make_docx).capacity(0) == PARAGRAPHS * 64 // 8
    monkeypatch.setattr(whitespace, "_MAX_PARAGRAPHS", PARAGRAPHS - 1)

    with pytest.raises(UnsupportedFormatError, match="too large to read"):
        WhitespaceChannel(make_docx)


def test_a_paragraph_of_many_runs_is_read_in_linear_time(make_docx: Path, tmp_path: Path) -> None:
    """Gathering its tail once took quadratic time: 40 000 runs took 2 s."""
    with zipfile.ZipFile(make_docx) as package:
        main = package.read("word/document.xml")

    paragraph = b"<w:p>" + b"<w:r><w:t>x</w:t></w:r>" * 100_000 + b"</w:p>"
    changes = {"word/document.xml": main.replace(b"<w:body>", b"<w:body>" + paragraph, 1)}
    cover = _rewritten_package(make_docx, tmp_path / "long.docx", changes)
    start = time.perf_counter()
    channel = WhitespaceChannel(cover)
    assert time.perf_counter() - start < 2
    assert channel.capacity(0) == (PARAGRAPHS + 1) * 64 // 8


def _corrupted(source: Path, target: Path, where: str, offset: int, value: bytes) -> Path:
    """A copy of the package ``source`` whose main relationships entry has ``value`` written
    ``offset`` bytes into its central directory record (``where`` is "central") or its data."""
    raw = bytearray(source.read_bytes())
    name = MAIN_RELATIONSHIPS.encode()

    with zipfile.ZipFile(source) as package:
        header = package.getinfo(MAIN_RELATIONSHIPS).header_offset

    extra = int.from_bytes(raw[header + LOCAL_HEADER_EXTRA : header + LOCAL_HEADER_NAME], "little")
    data = header + LOCAL_HEADER_NAME + len(name) + extra
    central = raw.rindex(name) - CENTRAL_ENTRY_NAME  # the central directory comes last
    start = (central if where == "central" else data) + offset
    raw[start : start + len(value)] = value
    target.write_bytes(bytes(raw))

    return target


@pytest.mark.parametrize(
    ("where", "offset", "value"),
    [
        ("central", 16, bytes(4)),  # the CRC-32
        ("central", 10, struct.pack("<H", 9)),  # the method
        ("central", 8, struct.pack("<H", 1)),  # the flags
        ("central", 20, struct.pack("<I", 1 << 20)),  # the compressed size
        ("data", 0, bytes([0xFF] * 4)),  # a reserved block type
    ],
    ids=["bad-crc", "deflate64", "encrypted", "overlapping", "broken-deflate"],
)
def test_a_corrupt_entry_makes_the_docx_unreadable(
    make_docx: Path, tmp_path: Path, where: str, offset: int, value: bytes
) -> None:
    """python-docx writes deflated entries."""
    cover = _corrupted(make_docx, tmp_path / "corrupt.docx", where, offset, value)

    for read in (lambda: docx_media.read_part(cover, 1), lambda: WhitespaceChannel(cover)):
        with pytest.raises(UnsupportedFormatError, match="not a readable DOCX file"):
            read()


@pytest.mark.parametrize(
    "main_part",
    [
        f'<w:document xmlns:w="{WORDPROCESSING}"/>',
        f'<w:hdr xmlns:w="{WORDPROCESSING}"><w:p/></w:hdr>',
    ],
    ids=["no-body", "other-root"],
)
def test_a_main_part_without_a_body_has_no_paragraphs(
    make_docx: Path, tmp_path: Path, main_part: str
) -> None:
    changes = {"word/document.xml": main_part.encode()}
    cover = _rewritten_package(make_docx, tmp_path / "bodiless.docx", changes)
    assert WhitespaceChannel(cover).capacity(0) == 0

    with pytest.raises(NoHiddenDataError):
        service.reveal(cover, tmp_path, PASSWORD)


def test_missing_documents_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog.Requirement, "is_installed", property(lambda _self: False))
    hint = "needs the documents extra, see Installation in README.md"
    whitespace = _options(MethodId.WHITESPACE, CipherId.NONE)
    assert service.unavailable_reason(whitespace) == f"Whitespace padding {hint}"
    pdf = _options(MethodId.OBJECT_STREAM, CipherId.NONE)
    assert service.unavailable_reason(pdf, Path("report.pdf")) == f"PDF {hint}"


def test_reveal_needs_the_extra_when_no_method_is_left(
    make_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "pikepdf", None)

    with pytest.raises(NotAvailableError, match="PDF needs the documents extra"):
        service.reveal(make_pdf, tmp_path, PASSWORD)


def test_reveal_skips_a_method_whose_extra_is_missing(
    make_docx: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedded = tmp_path / "embedded.docx"
    service.hide(make_docx, FILE, embedded, _options(MethodId.OBJECT_STREAM, CipherId.AES_GCM))
    monkeypatch.setitem(sys.modules, "docx", None)
    result = _reveal(embedded, tmp_path, PASSWORD)
    assert result.method == MethodId.OBJECT_STREAM


def test_every_pikepdf_error_reads_as_an_unreadable_pdf(
    make_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pikepdf raises more than ``PdfError``: a ``QpdfRuntimeError`` or a ``ForeignObjectError``
    would have escaped as a plain exception."""
    pikepdf = pytest.importorskip("pikepdf")
    payload = _pdf_with_payload_stream(make_pdf.with_name("payload.pdf"), b"data")

    def failing(*args: object, **kwargs: object) -> object:
        raise pikepdf.ForeignObjectError("a stream from another document")

    monkeypatch.setattr(pdf_media, "_decoded", failing)

    with pytest.raises(UnsupportedFormatError, match="not a readable PDF"):
        pdf_media.read_data(payload, 1024)

    monkeypatch.setattr(pikepdf, "open", failing)

    with pytest.raises(UnsupportedFormatError, match="not a readable PDF"):
        pdf_media.read_data(make_pdf, 1024)
