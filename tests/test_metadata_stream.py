"""Research's metadata stream for the object method: the stream as base64 text in a property
of a PDF's XMP or a DOCX's custom properties (docs/format.md#stream-channels)."""

import base64
import re
import zipfile
import zlib
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from cipherstitch import container, kdf, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.container import Candidate, UnitOrder
from cipherstitch.errors import CapacityError, NoHiddenDataError, UnsupportedFormatError
from cipherstitch.media import docx as docx_media
from cipherstitch.media import pdf as pdf_media
from cipherstitch.media import properties
from cipherstitch.methods import object_stream
from cipherstitch.methods.object_stream import ObjectRules
from cipherstitch.params import engine
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import ResearchReveal

from .conftest import plain_method

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
FILE = HiddenFile("note.txt", b"a manuscript keeps traces " * 8)
OLD = HiddenFile("old.txt", b"an older payload")
METADATA = Settings.of({"obj.loc": "metadata"})
RULES = ObjectRules(is_in_metadata=True)
KIB = 1024
MAX_READ = object_stream.MAX_LIMIT * 3 // 4  # the most reveal decodes
PARAGRAPHS = 40
CONTENT = b"BT /F1 12 Tf 72 720 Td (Hello) Tj ET"
CRLF = bytes([13, 10])
WORD_DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + CRLF
WORD_DATE = (2024, 5, 6, 7, 8, 10)  # not ZipInfo's default, so a copied date shows
CUSTOM_PART = "docProps/custom.xml"
PACKAGE_RELATIONSHIPS = "_rels/.rels"
CONTENT_TYPES = "[Content_Types].xml"
MAIN_PART = "word/document.xml"
MAIN_RELATIONSHIPS = "word/_rels/document.xml.rels"
NAMESPACE = "urn:uuid:489bd2b8-2e41-442c-a303-bb107208f89c"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
CUSTOM = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
VARIANTS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
CUSTOM_LINK = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
)
CUSTOM_TYPE = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
USER_DEFINED = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
# What a metadata write adds to a PDF's XMP and to a DOCX's custom properties.
DESCRIPTION = re.compile(
    rb'<rdf:Description rdf:about="([^"]*)" xmlns:doc="'
    + re.escape(NAMESPACE.encode())
    + rb'"><doc:DocumentState>([A-Za-z0-9+/=]*)</doc:DocumentState></rdf:Description>'
)
PROPERTY = re.compile(
    rb'<property fmtid="'
    + re.escape(USER_DEFINED.encode())
    + rb'" pid="(\d+)" name="DocumentState"><vt:blob>([A-Za-z0-9+/=]*)</vt:blob></property>'
)
ABOUT = "uuid:6c3f2a8e-0d1b-4d55-9a3c-5b7f2e9d1a40"
# A cover's XMP as a word processor writes it: two descriptions of one resource, indented,
# with padding before the packet's end.
COVER_XMP = (
    f'<?xpacket begin="{chr(0xFEFF)}" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    f'  <rdf:RDF xmlns:rdf="{RDF}">\n'
    f'    <rdf:Description rdf:about="{ABOUT}" xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
    "      <dc:format>application/pdf</dc:format>\n"
    '      <dc:title><rdf:Alt><rdf:li xml:lang="x-default">Field notes</rdf:li></rdf:Alt>'
    "</dc:title>\n"
    "    </rdf:Description>\n"
    f'    <rdf:Description rdf:about="{ABOUT}" xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
    ' xmp:CreateDate="2026-10-01T09:30:00Z"/>\n'
    "  </rdf:RDF>\n"
    "</x:xmpmeta>\n" + " " * 256 + '\n<?xpacket end="w"?>'
).encode()
# A cover's custom properties as Word writes them.
COVER_CUSTOM = (
    WORD_DECLARATION
    + (
        f'<Properties xmlns="{CUSTOM}" xmlns:vt="{VARIANTS}">'
        f'<property fmtid="{USER_DEFINED}" pid="2" name="Client">'
        "<vt:lpwstr>Harbour Archive</vt:lpwstr></property>"
        f'<property fmtid="{USER_DEFINED}" pid="5" name="Revision"><vt:i4>7</vt:i4></property>'
        "</Properties>"
    ).encode()
)

# The base64 of an object stream holding VECTOR_FILE under AES-256-GCM, with VECTOR_PASSWORD
# and the cheap VECTOR_ARGON2 profile, unpadded. Never regenerate it: data hidden in
# metadata must stay readable.
VECTOR_FILE = HiddenFile("a.txt", b"cipherstitch")
VECTOR_PASSWORD = "пароль-йї"  # noqa: S105
VECTOR_ARGON2 = {"ARGON2_MEMORY_KIB": 32, "ARGON2_ITERATIONS": 1}
VECTOR = (
    "fRqE7bJWUOJzmP2ZaIgUBfOLDw3G86QK33IbH+/pGGbNRezJhDpxrZ28WLvlykbOzys7IqI6rKUuf2JyfxIkHgRp"
    "C/z3AQe+xwk="
)

Make = Callable[[Path], Path]


def _pdf(path: Path, xmp: bytes | None = None, *, is_compressed: bool = False) -> Path:
    pikepdf = pytest.importorskip("pikepdf")
    document = pikepdf.new()
    document.add_blank_page()
    document.pages[0].Contents = document.make_stream(CONTENT)
    document.docinfo["/Title"] = "Field notes"

    if xmp is not None:
        stream = pikepdf.Stream(document, zlib.compress(xmp) if is_compressed else xmp)
        stream.Type = pikepdf.Name.Metadata
        stream.Subtype = pikepdf.Name.XML

        if is_compressed:
            stream.Filter = pikepdf.Name.FlateDecode

        document.Root.Metadata = stream

    document.save(path, fix_metadata_version=False)  # pikepdf would rewrite the XMP

    return path


def _docx(path: Path, custom: bytes | None = None) -> Path:
    python_docx = pytest.importorskip("docx")
    document = python_docx.Document()

    for index in range(PARAGRAPHS):
        document.add_paragraph(f"Paragraph {index}: a manuscript keeps traces.")

    document.save(str(path))

    return path if custom is None else _with_custom_part(path, custom)


def _with_custom_part(path: Path, custom: bytes, target: str = CUSTOM_PART) -> Path:
    """``path`` with ``custom`` as its custom properties, linked as Word links them."""
    with zipfile.ZipFile(path) as package:
        relationships = package.read(PACKAGE_RELATIONSHIPS)
        types = package.read(CONTENT_TYPES)

    link = f'<Relationship Id="rId9" Type="{CUSTOM_LINK}" Target="{target}"/>'
    override = f'<Override PartName="/{CUSTOM_PART}" ContentType="{CUSTOM_TYPE}"/>'
    changes = {
        PACKAGE_RELATIONSHIPS: relationships.replace(
            b"</Relationships>", link.encode() + b"</Relationships>"
        ),
        CONTENT_TYPES: types.replace(b"</Types>", override.encode() + b"</Types>"),
        CUSTOM_PART: custom,
    }

    return _rewritten(path, path, changes)


def _rewritten(source: Path, target: Path, changes: Mapping[str, bytes | None]) -> Path:
    """A copy of the package ``source`` with entries replaced, added or (``None``) dropped."""
    with zipfile.ZipFile(source) as package:
        entries = {info.filename: package.read(info) for info in package.infolist()}

    with zipfile.ZipFile(target, "w") as output:
        for name, content in {**entries, **changes}.items():
            if content is not None:
                output.writestr(name, content)

    return target


def _entries(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as package:
        return {info.filename: package.read(info) for info in package.infolist()}


def _xmp(path: Path) -> bytes:
    pikepdf = pytest.importorskip("pikepdf")

    with pikepdf.open(path) as document:
        return bytes(document.Root.Metadata.read_bytes())


def _options(
    cipher: CipherId, settings: Settings = METADATA, method: MethodId = MethodId.OBJECT_STREAM
) -> service.HideOptions:
    password = None if cipher == CipherId.NONE else PASSWORD

    return service.HideOptions(
        CarrierKind.DOCUMENT,
        method,
        cipher,
        password,
        settings=settings,
        is_research=True,
    )


def _reveal(path: Path, tmp_path: Path, cipher: CipherId, settings: Settings | None) -> bytes:
    """Research's reveal with ``settings``, Standard's with ``None``."""
    password = None if cipher == CipherId.NONE else PASSWORD
    research = (
        None if settings is None else ResearchReveal(MethodId.OBJECT_STREAM, cipher, settings)
    )
    result = service.reveal(path, tmp_path / "out", password, research=research)
    data = result.path.read_bytes()
    result.path.unlink()

    return data


def _has_plain_metadata_payload(path: Path, tmp_path: Path) -> bool:
    try:
        _reveal(path, tmp_path, CipherId.NONE, METADATA)
    except NoHiddenDataError:
        return False

    return True


COVERS: dict[str, Make] = {"pdf": lambda path: _pdf(path), "docx": lambda path: _docx(path)}
CIPHERS = pytest.mark.parametrize(
    "cipher", [CipherId.AES_GCM, CipherId.NONE], ids=["encrypted", "plain"]
)


@CIPHERS
@pytest.mark.parametrize("kind", COVERS)
def test_each_location_reveals_only_with_its_setting(
    tmp_path: Path, kind: str, cipher: CipherId
) -> None:
    cover = COVERS[kind](tmp_path / f"cover.{kind}")
    embedded, standard = tmp_path / f"embedded.{kind}", tmp_path / f"standard.{kind}"
    service.hide(cover, FILE, embedded, _options(cipher))
    service.hide(cover, FILE, standard, _options(cipher, Settings()))

    assert _reveal(embedded, tmp_path, cipher, METADATA) == FILE.data
    assert _reveal(standard, tmp_path, cipher, None) == FILE.data

    for settings in (Settings(), None):  # the standard location, in Research and Standard
        with pytest.raises(NoHiddenDataError):
            _reveal(embedded, tmp_path, cipher, settings)

    with pytest.raises(NoHiddenDataError):
        _reveal(standard, tmp_path, cipher, METADATA)


@pytest.mark.parametrize("is_compressed", [False, True], ids=["plain-xmp", "compressed-xmp"])
def test_a_pdfs_other_metadata_is_kept(tmp_path: Path, is_compressed: bool) -> None:
    pikepdf = pytest.importorskip("pikepdf")
    cover = _pdf(tmp_path / "cover.pdf", COVER_XMP, is_compressed=is_compressed)
    embedded = tmp_path / "embedded.pdf"
    service.hide(cover, FILE, embedded, _options(CipherId.AES_GCM))
    xmp = _xmp(embedded)
    found = DESCRIPTION.search(xmp)

    assert found is not None
    assert found[1] == ABOUT.encode()  # every top-level description describes one resource
    assert DESCRIPTION.sub(b"", xmp) == COVER_XMP
    assert xmp.index(found[0]) < xmp.rindex(b"</rdf:RDF>")

    with pikepdf.open(embedded) as document:
        assert document.check_pdf_syntax() == []
        assert document.pages[0].Contents.read_bytes() == CONTENT
        assert dict(document.docinfo.items()) == {"/Title": "Field notes"}
        assert document.Root.Metadata.Type == pikepdf.Name.Metadata
        assert document.Root.Metadata.Subtype == pikepdf.Name.XML
        assert pdf_media.PIECE_INFO not in document.Root

    assert _reveal(embedded, tmp_path, CipherId.AES_GCM, METADATA) == FILE.data


@CIPHERS
def test_a_pdf_without_metadata_gets_a_packet_that_names_no_tool(
    tmp_path: Path, cipher: CipherId
) -> None:
    pikepdf = pytest.importorskip("pikepdf")
    cover, embedded = _pdf(tmp_path / "cover.pdf"), tmp_path / "embedded.pdf"
    service.hide(cover, FILE, embedded, _options(cipher))
    xmp = _xmp(embedded)
    found = DESCRIPTION.search(xmp)

    assert found is not None and found[1] == b""
    assert xmp.startswith(f'<?xpacket begin="{chr(0xFEFF)}"'.encode())
    assert b'<x:xmpmeta xmlns:x="adobe:ns:meta/">' in xmp
    assert xmp.endswith(b'<?xpacket end="w"?>')
    rest = embedded.read_bytes().replace(found[2], b"").lower()  # the base64 is random text

    for name in (b"pikepdf", b"cipherstitch", b"qpdf", b"xmptk"):
        assert name not in rest

    with pikepdf.open(embedded) as document:
        assert dict(document.docinfo.items()) == {"/Title": "Field notes"}
        assert document.pdf_version >= "1.4"

    assert _reveal(embedded, tmp_path, cipher, METADATA) == FILE.data


def test_a_pdf_keeps_its_encryption_and_restrictions(tmp_path: Path) -> None:
    pikepdf = pytest.importorskip("pikepdf")
    cover = tmp_path / "restricted.pdf"
    document = pikepdf.new()
    document.add_blank_page()
    no_copying = pikepdf.Permissions(extract=False)
    encryption = pikepdf.Encryption(owner="owner-secret", user="", allow=no_copying)
    document.save(cover, encryption=encryption)
    embedded = tmp_path / "embedded.pdf"
    service.hide(cover, FILE, embedded, _options(CipherId.AES_GCM))

    with pikepdf.open(embedded) as result:
        assert result.is_encrypted
        assert not result.allow.extract

    assert _reveal(embedded, tmp_path, CipherId.AES_GCM, METADATA) == FILE.data


def test_a_docxs_other_custom_properties_and_entries_are_kept(tmp_path: Path) -> None:
    cover = _docx(tmp_path / "cover.docx", COVER_CUSTOM)
    embedded = tmp_path / "embedded.docx"
    service.hide(cover, FILE, embedded, _options(CipherId.AES_GCM))
    before, after = _entries(cover), _entries(embedded)
    custom = after[CUSTOM_PART]
    found = PROPERTY.search(custom)

    assert list(after) == list(before)
    assert [name for name in after if after[name] != before[name]] == [CUSTOM_PART]
    assert found is not None
    assert found[1] == b"6"  # after the highest property id
    assert PROPERTY.sub(b"", custom) == COVER_CUSTOM
    assert custom.endswith(found[0] + b"</Properties>")
    assert _reveal(embedded, tmp_path, CipherId.AES_GCM, METADATA) == FILE.data


@CIPHERS
def test_a_docx_without_custom_properties_gets_them_as_word_links_them(
    tmp_path: Path, cipher: CipherId
) -> None:
    """The new part gets the main part's XML declaration, date and compression, not defaults."""
    plain = _docx(tmp_path / "plain.docx")
    cover, embedded = tmp_path / "word.docx", tmp_path / "embedded.docx"
    main_declaration = b"<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"

    with zipfile.ZipFile(plain) as source, zipfile.ZipFile(cover, "w") as output:
        for info in source.infolist():
            entry = zipfile.ZipInfo(info.filename, date_time=WORD_DATE)
            entry.compress_type = zipfile.ZIP_DEFLATED
            content = source.read(info)

            if content.startswith(b"<?xml"):
                declaration = main_declaration if info.filename == MAIN_PART else WORD_DECLARATION
                content = declaration + content.split(b"?>", 1)[1].lstrip()

            output.writestr(entry, content)

    service.hide(cover, FILE, embedded, _options(cipher))
    before, after = _entries(cover), _entries(embedded)
    changed = [name for name in before if after[name] != before[name]]

    assert list(after) == [*before, CUSTOM_PART]
    assert changed == [CONTENT_TYPES, PACKAGE_RELATIONSHIPS]
    assert after[CUSTOM_PART].startswith(
        main_declaration + f'<Properties xmlns="{CUSTOM}" xmlns:vt="{VARIANTS}">'.encode()
    )
    assert (
        f'<Relationship Id="rId5" Type="{CUSTOM_LINK}" Target="{CUSTOM_PART}"/>'
        "</Relationships>".encode()
    ) in after[PACKAGE_RELATIONSHIPS]
    assert (
        f'<Override PartName="/{CUSTOM_PART}" ContentType="{CUSTOM_TYPE}"/></Types>'.encode()
    ) in after[CONTENT_TYPES]

    with zipfile.ZipFile(embedded) as package:
        main, part = package.getinfo(MAIN_PART), package.getinfo(CUSTOM_PART)

    assert (part.date_time, part.compress_type) == (main.date_time, main.compress_type)
    assert not any(b"cipherstitch" in content.lower() for content in after.values())
    assert _reveal(embedded, tmp_path, cipher, METADATA) == FILE.data


@pytest.mark.parametrize("kind", COVERS)
def test_hiding_again_in_place_replaces_the_property(tmp_path: Path, kind: str) -> None:
    path = COVERS[kind](tmp_path / f"cover.{kind}")
    service.hide(path, OLD, path, _options(CipherId.NONE))
    service.hide(path, FILE, path, _options(CipherId.AES_GCM))

    if kind == "pdf":
        assert len(DESCRIPTION.findall(_xmp(path))) == 1
    else:
        entries = _entries(path)

        assert len(PROPERTY.findall(entries[CUSTOM_PART])) == 1
        assert entries[PACKAGE_RELATIONSHIPS].count(CUSTOM_LINK.encode()) == 1
        assert entries[CONTENT_TYPES].count(CUSTOM_PART.encode()) == 1

    assert not _has_plain_metadata_payload(path, tmp_path)
    assert _reveal(path, tmp_path, CipherId.AES_GCM, METADATA) == FILE.data


@pytest.mark.parametrize("has_others", [False, True], ids=["alone", "beside-other-data"])
def test_a_metadata_write_removes_a_page_piece_payload(tmp_path: Path, has_others: bool) -> None:
    pikepdf = pytest.importorskip("pikepdf")
    path = _pdf(tmp_path / "cover.pdf")

    if has_others:
        with pikepdf.open(path, allow_overwriting_input=True) as document:
            other = pikepdf.Dictionary(LastModified="D:20200101000000Z", Private=1)
            document.Root[pdf_media.PIECE_INFO] = pikepdf.Dictionary(Illustrator=other)
            document.save(path)

    service.hide(path, OLD, path, _options(CipherId.NONE, Settings()))
    service.hide(path, FILE, path, _options(CipherId.AES_GCM))

    with pikepdf.open(path) as document:
        pieces = document.Root.get(pdf_media.PIECE_INFO)
        kept = set() if pieces is None else set(pieces.keys())

    assert kept == ({"/Illustrator"} if has_others else set())
    assert plain_method(path) is None
    assert _reveal(path, tmp_path, CipherId.AES_GCM, METADATA) == FILE.data


def test_a_page_piece_write_removes_a_metadata_payload(tmp_path: Path) -> None:
    path = _pdf(tmp_path / "cover.pdf", COVER_XMP)
    service.hide(path, OLD, path, _options(CipherId.NONE))
    service.hide(path, FILE, path, _options(CipherId.AES_GCM, Settings()))

    assert NAMESPACE.encode() not in _xmp(path)
    assert b"Field notes" in _xmp(path)
    assert not _has_plain_metadata_payload(path, tmp_path)
    assert _reveal(path, tmp_path, CipherId.AES_GCM, None) == FILE.data


Write = tuple[MethodId, Settings]
STANDARD_OBJECT: Write = (MethodId.OBJECT_STREAM, Settings())
WHITESPACE: Write = (MethodId.WHITESPACE, Settings())
IN_METADATA: Write = (MethodId.OBJECT_STREAM, METADATA)


def _hide_in_place(path: Path, file: HiddenFile, write: Write, cipher: CipherId) -> None:
    method, settings = write
    service.hide(path, file, path, _options(cipher, settings, method))


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (STANDARD_OBJECT, IN_METADATA),
        (WHITESPACE, IN_METADATA),
        (IN_METADATA, STANDARD_OBJECT),
        (IN_METADATA, WHITESPACE),
    ],
    ids=[
        "object-then-metadata",
        "whitespace-then-metadata",
        "metadata-then-object",
        "metadata-then-whitespace",
    ],
)
def test_a_docx_write_removes_what_another_left(
    tmp_path: Path, first: Write, second: Write
) -> None:
    path = _docx(tmp_path / "cover.docx", COVER_CUSTOM)
    _hide_in_place(path, OLD, first, CipherId.NONE)
    _hide_in_place(path, FILE, second, CipherId.AES_GCM)
    entries = _entries(path)

    assert plain_method(path) is None
    assert not _has_plain_metadata_payload(path, tmp_path)
    assert (docx_media.PART in entries) is (second == STANDARD_OBJECT)
    assert (entries[CUSTOM_PART] == COVER_CUSTOM) is (second != IN_METADATA)
    settings = METADATA if second == IN_METADATA else None

    assert _reveal(path, tmp_path, CipherId.AES_GCM, settings) == FILE.data


@pytest.mark.parametrize("write", [STANDARD_OBJECT, WHITESPACE], ids=["object", "whitespace"])
def test_other_writes_leave_custom_properties_without_the_property_alone(
    tmp_path: Path, write: Write
) -> None:
    path = _docx(tmp_path / "cover.docx", COVER_CUSTOM)
    _hide_in_place(path, FILE, write, CipherId.AES_GCM)

    assert _entries(path)[CUSTOM_PART] == COVER_CUSTOM


@pytest.mark.parametrize("is_attribute", [True, False], ids=["attribute", "empty-rdf-element"])
def test_xmp_the_writer_cannot_edit_is_read_but_refused(tmp_path: Path, is_attribute: bool) -> None:
    """XMP may hold a simple property as an attribute of its description, as XMP tools that
    rewrite a packet do."""
    stream = bytes(range(256))
    value = base64.b64encode(stream).decode()
    xmp = COVER_XMP.replace(
        b"</rdf:RDF>",
        f'<rdf:Description rdf:about="{ABOUT}" xmlns:doc="{NAMESPACE}" '
        f'doc:DocumentState="{value}"/></rdf:RDF>'.encode(),
    )
    empty = f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="{RDF}"/></x:xmpmeta>'
    cover = _pdf(tmp_path / "cover.pdf", xmp if is_attribute else empty.encode())
    expected = stream if is_attribute else b""

    assert object_stream.open_channel(cover, RULES).read(0, 300, None) == expected

    with pytest.raises(UnsupportedFormatError, match="unsupported metadata layout"):
        service.hide(cover, FILE, tmp_path / "embedded.pdf", _options(CipherId.AES_GCM))


LAUGHS = (
    b'<!DOCTYPE x [<!ENTITY a "aaaaaaaaaa">'
    + b"".join(
        f'<!ENTITY {name} "{f"&{previous};" * 10}">'.encode()
        for previous, name in zip("abcdefg", "bcdefgh", strict=True)
    )
    + b"]>"
)
EXTERNAL = b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
BROKEN_XMP = {
    "not-xml": (b"Field notes, not XML", "broken metadata"),
    "not-xmp": (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><a/></x:xmpmeta>', "not XMP"),
    "entity-bomb": (LAUGHS + f'<rdf:RDF xmlns:rdf="{RDF}">&h;</rdf:RDF>'.encode(), "broken"),
    "external-entity": (EXTERNAL + f'<rdf:RDF xmlns:rdf="{RDF}">&e;</rdf:RDF>'.encode(), "broken"),
}


@pytest.mark.parametrize("case", BROKEN_XMP)
def test_broken_xmp_makes_the_metadata_unreadable(tmp_path: Path, case: str) -> None:
    xmp, message = BROKEN_XMP[case]
    cover = _pdf(tmp_path / "cover.pdf", xmp)

    with pytest.raises(UnsupportedFormatError, match=message):
        object_stream.open_channel(cover, RULES)

    with pytest.raises(UnsupportedFormatError, match=message):
        service.hide(cover, FILE, tmp_path / "embedded.pdf", _options(CipherId.AES_GCM))

    assert not (tmp_path / "embedded.pdf").exists()

    service.hide(cover, FILE, tmp_path / "standard.pdf", _options(CipherId.AES_GCM, Settings()))

    assert _reveal(tmp_path / "standard.pdf", tmp_path, CipherId.AES_GCM, None) == FILE.data


@pytest.mark.parametrize("kind", ["dictionary", "filter", "corrupt"])
def test_a_metadata_object_this_program_cannot_decode_is_refused(tmp_path: Path, kind: str) -> None:
    pikepdf = pytest.importorskip("pikepdf")
    cover = _pdf(tmp_path / "cover.pdf", COVER_XMP)

    with pikepdf.open(cover, allow_overwriting_input=True) as document:
        if kind == "dictionary":
            document.Root.Metadata = pikepdf.Dictionary(Type=pikepdf.Name.Metadata)
        else:
            stream = pikepdf.Stream(document, COVER_XMP.hex().encode() + b">")
            is_hex = kind == "filter"
            stream.Filter = pikepdf.Name.ASCIIHexDecode if is_hex else pikepdf.Name.FlateDecode
            document.Root.Metadata = stream

        # Kept as it is: qpdf would decode the filter and compress the stream again.
        document.save(
            cover,
            fix_metadata_version=False,
            compress_streams=False,
            stream_decode_level=pikepdf.StreamDecodeLevel.none,
        )

    with pytest.raises(UnsupportedFormatError, match="broken metadata"):
        object_stream.open_channel(cover, RULES)

    if kind == "filter":  # Standard writes on. pikepdf's own XMP fix fails on the others.
        service.hide(cover, FILE, tmp_path / "standard.pdf", _options(CipherId.AES_GCM, Settings()))


def test_a_compressed_xmp_is_decompressed_only_up_to_the_cap(tmp_path: Path) -> None:
    bomb = _pdf(tmp_path / "bomb.pdf", COVER_XMP + b" " * (16 << 20), is_compressed=True)

    with pytest.raises(UnsupportedFormatError, match="too large to read"):
        object_stream.open_channel(bomb, RULES)


@pytest.mark.parametrize("kind", COVERS)
def test_metadata_too_large_to_parse_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """The writer refuses what the reader would refuse once the property is in."""
    is_pdf = kind == "pdf"
    metadata = COVER_XMP if is_pdf else COVER_CUSTOM
    cover = (
        _pdf(tmp_path / "cover.pdf", metadata) if is_pdf else _docx(tmp_path / "c.docx", metadata)
    )
    module, cap = (pdf_media, "_MAX_XMP_SIZE") if is_pdf else (docx_media, "_MAX_PROPERTIES_SIZE")
    monkeypatch.setattr(module, cap, len(metadata))
    object_stream.open_channel(cover, RULES)

    with pytest.raises(UnsupportedFormatError, match="too large to read"):
        service.hide(cover, FILE, tmp_path / f"embedded.{kind}", _options(CipherId.AES_GCM))

    monkeypatch.setattr(module, cap, len(metadata) - 1)

    with pytest.raises(UnsupportedFormatError, match="too large to read"):
        object_stream.open_channel(cover, RULES)


def _custom(*properties_xml: str) -> bytes:
    body = "".join(properties_xml)

    return (
        WORD_DECLARATION
        + f'<Properties xmlns="{CUSTOM}" xmlns:vt="{VARIANTS}">{body}'.encode()
        + b"</Properties>"
    )


def _property(value: str, variant: str = "blob") -> str:
    return (
        f'<property fmtid="{USER_DEFINED}" pid="2" name="DocumentState">'
        f"<vt:{variant}>{value}</vt:{variant}></property>"
    )


BROKEN_CUSTOM = {
    "not-xml": b"Harbour Archive",
    "other-root": f'<Other xmlns="{CUSTOM}"/>'.encode(),
    "entity-bomb": LAUGHS + f'<Properties xmlns="{CUSTOM}">&h;</Properties>'.encode(),
    "external-entity": EXTERNAL + f'<Properties xmlns="{CUSTOM}">&e;</Properties>'.encode(),
}


@pytest.mark.parametrize("case", BROKEN_CUSTOM)
def test_broken_custom_properties_make_the_metadata_unreadable(tmp_path: Path, case: str) -> None:
    cover = _docx(tmp_path / "cover.docx", BROKEN_CUSTOM[case])

    with pytest.raises(UnsupportedFormatError, match="broken custom properties"):
        object_stream.open_channel(cover, RULES)

    assert object_stream.open_channel(cover).read(0, 1, None) == b""  # the standard location


def _pdf_holding(path: Path, value: str) -> Path:
    description = (
        f'<rdf:Description rdf:about="{ABOUT}" xmlns:doc="{NAMESPACE}">'
        f"<doc:DocumentState>{value}</doc:DocumentState></rdf:Description></rdf:RDF>"
    )

    return _pdf(path, COVER_XMP.replace(b"</rdf:RDF>", description.encode()))


HOLDERS: dict[str, Callable[[Path, str], Path]] = {
    "pdf": _pdf_holding,
    "docx": lambda path, value: _docx(path, _custom(_property(value))),
}


@pytest.mark.parametrize("value", ["not base64!", "QUJD=", "QUJ" + chr(0x0411)])
@pytest.mark.parametrize("kind", HOLDERS)
def test_a_value_that_is_not_base64_reads_as_no_payload(
    tmp_path: Path, kind: str, value: str
) -> None:
    cover = HOLDERS[kind](tmp_path / f"cover.{kind}", value)

    assert object_stream.open_channel(cover, RULES).read(0, 16, None) == b""

    with pytest.raises(NoHiddenDataError):
        _reveal(cover, tmp_path, CipherId.AES_GCM, METADATA)


@pytest.mark.parametrize("kind", HOLDERS)
def test_a_value_is_decoded_only_up_to_the_most_reveal_reads(tmp_path: Path, kind: str) -> None:
    stream = bytes(range(256)) * (MAX_READ // 256 + 64)
    cover = HOLDERS[kind](tmp_path / f"cover.{kind}", base64.b64encode(stream).decode())
    channel = object_stream.open_channel(cover, RULES)

    assert channel.stored_limit(0) == MAX_READ
    assert channel.read(0, len(stream), None) == stream[:MAX_READ]


def test_base64_may_hold_whitespace_and_is_read_to_the_limit() -> None:
    assert properties.decoded(" QUJD\r\n REVG\t", 6) == b"ABCDEF"
    assert properties.decoded("QUJDREVG", 4) == b"ABCD"
    assert properties.decoded("QUJDREVG" + "!" * 8, 6) == b"ABCDEF"  # what follows is not read
    assert properties.decoded("", 6) == b""
    assert properties.decoded_size(64 * KIB) == 48 * KIB


def _another_type(path: Path) -> Path:
    return _docx(path, _custom(_property("QUJD", variant="lpwstr")))


def _missing_part(path: Path) -> Path:
    linked = _with_custom_part(_docx(path), COVER_CUSTOM, "docProps/missing.xml")

    return _rewritten(linked, path, {CUSTOM_PART: None})


def _unlinked_part(path: Path) -> Path:
    return _rewritten(_docx(path), path, {CUSTOM_PART: _custom(_property(VECTOR))})


def _printer_settings_part(path: Path) -> Path:
    """Custom properties where the printer settings are, which a write removes."""
    linked = _with_custom_part(_docx(path), _custom(), docx_media.PART)

    with zipfile.ZipFile(linked) as package:
        relationships = package.read(MAIN_RELATIONSHIPS)

    link = (
        f'<Relationship Id="rId99" Type="{docx_media.PRINTER_SETTINGS}" '
        'Target="printerSettings/printerSettings1.bin"/></Relationships>'
    )
    changes = {
        docx_media.PART: _custom(),
        MAIN_RELATIONSHIPS: relationships.replace(b"</Relationships>", link.encode()),
    }

    return _rewritten(linked, path, changes)


def _no_variant_prefix(path: Path) -> Path:
    return _docx(path, WORD_DECLARATION + f'<Properties xmlns="{CUSTOM}"></Properties>'.encode())


@pytest.mark.parametrize(
    "make",
    [_another_type, _missing_part, _unlinked_part, _printer_settings_part, _no_variant_prefix],
)
def test_custom_properties_the_writer_cannot_take_are_refused(tmp_path: Path, make: Make) -> None:
    cover = make(tmp_path / "cover.docx")

    assert object_stream.open_channel(cover, RULES).read(0, 1, None) == b""

    with pytest.raises(UnsupportedFormatError, match="unsupported package layout"):
        service.hide(cover, FILE, tmp_path / "embedded.docx", _options(CipherId.AES_GCM))


def test_other_writes_skip_package_relationships_they_cannot_read(tmp_path: Path) -> None:
    changes = {PACKAGE_RELATIONSHIPS: b"broken"}
    cover = _rewritten(_docx(tmp_path / "cover.docx"), tmp_path / "odd.docx", changes)

    with pytest.raises(UnsupportedFormatError, match="broken package relationships"):
        object_stream.open_channel(cover, RULES)

    service.hide(cover, FILE, tmp_path / "embedded.docx", _options(CipherId.AES_GCM, Settings()))

    assert _reveal(tmp_path / "embedded.docx", tmp_path, CipherId.AES_GCM, None) == FILE.data


@pytest.mark.parametrize("kind", COVERS)
def test_the_object_holds_three_quarters_of_the_limit(tmp_path: Path, kind: str) -> None:
    cover = COVERS[kind](tmp_path / f"cover.{kind}")
    channel = object_stream.open_channel(cover, RULES)
    stream = b"salt" + bytes(100)
    objects = [channel.write(stream[:4], stream[4:], None).data for _ in range(8)]

    assert channel.capacity(0) == 48 * KIB
    assert len({len(data) for data in objects}) > 1
    assert all(data.startswith(stream) and len(data) <= 48 * KIB for data in objects)

    with pytest.raises(CapacityError):
        channel.write(b"", bytes(48 * KIB + 1), None)

    embedded = tmp_path / f"full.{kind}"
    channel.write(b"", bytes(48 * KIB), None).save(embedded)

    if kind == "pdf":
        found = DESCRIPTION.search(_xmp(embedded))
    else:
        found = PROPERTY.search(_entries(embedded)[CUSTOM_PART])

    assert found is not None
    assert len(found[2]) == object_stream.SOFT_LIMIT  # characters
    assert object_stream.open_channel(embedded, RULES).read(0, 48 * KIB, None) == bytes(48 * KIB)


def test_the_location_settings_reach_the_channel() -> None:
    larger = Settings.of({"obj.loc": "metadata", "obj.limit": 128})

    assert engine.tuning(METADATA).document == RULES
    assert engine.tuning(larger).document == ObjectRules(128 * KIB, is_in_metadata=True)
    assert engine.tuning(Settings()).document == ObjectRules()
    assert ObjectRules(128 * KIB, is_in_metadata=True).capacity == 96 * KIB
    assert ObjectRules(is_padded=False, is_in_metadata=True).stored(b"stream") == b"stream"


@pytest.mark.parametrize("kind", HOLDERS)
def test_the_metadata_vector_stays_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    for name, value in VECTOR_ARGON2.items():
        monkeypatch.setattr(kdf, name, value)

    path = HOLDERS[kind](tmp_path / f"vector.{kind}", VECTOR)
    candidates = (Candidate(MethodId.OBJECT_STREAM, UnitOrder.PUBLIC),)
    revealed = container.reveal(
        [(object_stream.open_channel(path, RULES), candidates)], VECTOR_PASSWORD
    )

    assert revealed == container.Revealed(VECTOR_FILE, MethodId.OBJECT_STREAM, CipherId.AES_GCM)

    with pytest.raises(NoHiddenDataError):
        container.reveal([(object_stream.open_channel(path), candidates)], VECTOR_PASSWORD)


def test_a_pikepdf_error_while_decoding_the_xmp_is_broken_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pikepdf = pytest.importorskip("pikepdf")
    cover = _pdf(tmp_path / "cover.pdf", xmp=b"<x:xmpmeta/>")

    def failing(*args: object, **kwargs: object) -> object:
        raise pikepdf.ForeignObjectError("a stream from another document")

    monkeypatch.setattr(pdf_media, "_decoded", failing)

    with pytest.raises(UnsupportedFormatError, match="has broken metadata"):
        pdf_media.read_metadata(cover, 1024)


@pytest.mark.parametrize("kind", ["pdf", "docx"])
def test_a_metadata_stream_too_small_for_the_prefix_refuses_it(tmp_path: Path, kind: str) -> None:
    """17 base64 characters decode to 12 bytes: a 16-byte prefix with an empty body passed the
    body's check and failed in the padding (a ValueError)."""
    rules = ObjectRules(limit=17, is_in_metadata=True)
    cover = _pdf(tmp_path / "cover.pdf") if kind == "pdf" else _docx(tmp_path / "cover.docx")
    channel = object_stream.open_channel(cover, rules)

    assert channel.capacity(0) == properties.decoded_size(17) == 12

    with pytest.raises(CapacityError, match="holds at most 0"):
        channel.write(bytes(16), b"", None)
