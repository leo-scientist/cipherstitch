"""XML read from a carrier: a document type declaration is refused before any tree is built,
so neither entities nor attribute defaults can grow memory (docs/security.md#untrusted-carriers)."""

import base64
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from cipherstitch import service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.errors import UnsupportedFormatError
from cipherstitch.media import docx as docx_media
from cipherstitch.media import untrusted_xml
from cipherstitch.methods import object_stream
from cipherstitch.methods.object_stream import ObjectRules
from cipherstitch.payload import HiddenFile

RULES = ObjectRules(is_in_metadata=True)
PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
FILE = HiddenFile("note.txt", b"a manuscript keeps traces " * 4)
# ASCII text is valid Shift_JIS, but expat reads no multi-byte encoding.
SHIFT_JIS = b'<?xml version="1.0" encoding="Shift_JIS" standalone="yes"?>\n'
# Every <a/> after it gets a copy of the 4 KiB default: a few megabytes of them ask for
# gigabytes once parsed.
DEFAULTS = b'<!DOCTYPE r [<!ATTLIST a x CDATA "' + b"x" * 4096 + b'">]>'
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
CUSTOM = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
VARIANTS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
VALUE = b"a stored stream"
# A package as small as the readers take, with one custom property holding VALUE.
PACKAGE = {
    "[Content_Types].xml": (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/></Types>'
    ),
    "_rels/.rels": (
        f'<Relationships xmlns="{RELATIONSHIPS}">'
        f'<Relationship Id="rId1" Type="{OFFICE}/officeDocument" Target="word/document.xml"/>'
        f'<Relationship Id="rId2" Type="{OFFICE}/custom-properties" '
        'Target="docProps/custom.xml"/></Relationships>'
    ),
    "word/document.xml": (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p/></w:body></w:document>"
    ),
    "word/_rels/document.xml.rels": f'<Relationships xmlns="{RELATIONSHIPS}"></Relationships>',
    "docProps/custom.xml": (
        f'<Properties xmlns="{CUSTOM}" xmlns:vt="{VARIANTS}">'
        '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="DocumentState">'
        f"<vt:blob>{base64.b64encode(VALUE).decode()}</vt:blob></property></Properties>"
    ),
}


@pytest.mark.parametrize(
    "xml",
    [
        DEFAULTS + b"<r>" + b"<a/>" * 20_000 + b"</r>",
        b'<!DOCTYPE r [<!ENTITY e "entity">]><r>&e;</r>',
        b'<!DOCTYPE r [<!ENTITY e SYSTEM "file:///etc/passwd">]><r>&e;</r>',
        b'<!DOCTYPE r SYSTEM "http://example.org/r.dtd"><r/>',
        b"<!DOCTYPE r><r/>",
        "<!DOCTYPE r []><r/>".encode("utf-16"),
        bytes([0xEF, 0xBB, 0xBF]) + b'<?xml version="1.0"?><!-- a note --><!DOCTYPE r><r/>',
    ],
    ids=["attribute-defaults", "entity", "external", "external-subset", "bare", "utf-16", "bom"],
)
def test_a_document_type_declaration_is_refused(xml: bytes) -> None:
    with pytest.raises(ElementTree.ParseError, match="document type declaration"):
        untrusted_xml.parsed(xml)


@pytest.mark.parametrize(
    "xml",
    [
        bytes([0xEF, 0xBB, 0xBF]) + b'<?xml version="1.0"?><!-- a note --><?pi x?><r><a/></r>',
        '<?xml version="1.0" encoding="UTF-16"?><r><a/></r>'.encode("utf-16"),
        f'<?xpacket begin="{chr(0xFEFF)}" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        f'<rdf:RDF xmlns:rdf="{RDF}"><a/></rdf:RDF>\n<?xpacket end="w"?>'.encode(),
    ],
    ids=["prolog", "utf-16", "xpacket"],
)
def test_xml_without_one_parses_as_before(xml: bytes) -> None:
    expected = ElementTree.fromstring(xml)  # noqa: S314
    found = untrusted_xml.parsed(xml)

    assert ElementTree.tostring(found) == ElementTree.tostring(expected)


@pytest.mark.parametrize(
    "encoding", ["Shift_JIS", "GBK", "bogus", "hex", "mbcs", "idna", "undefined"]
)
def test_an_encoding_expat_cannot_read_is_refused(encoding: str) -> None:
    with pytest.raises(ElementTree.ParseError):
        untrusted_xml.parsed(f'<?xml version="1.0" encoding="{encoding}"?><r/>'.encode())


@pytest.mark.parametrize("xml", [b"", b"not XML", b"<r>", b"<r/><r/>"])
def test_xml_that_is_not_well_formed_is_refused(xml: bytes) -> None:
    with pytest.raises(ElementTree.ParseError):
        untrusted_xml.parsed(xml)


def _package(path: Path, part: str = "", prefix: bytes = b"") -> Path:
    """PACKAGE, with ``prefix`` before the root of ``part``."""
    with zipfile.ZipFile(path, "w") as package:
        for name, content in PACKAGE.items():
            package.writestr(name, (prefix if name == part else b"") + content.encode())

    return path


@pytest.mark.parametrize(
    ("part", "message"),
    [
        ("docProps/custom.xml", "broken custom properties"),
        ("_rels/.rels", "broken package relationships"),
    ],
)
def test_metadata_parts_with_a_dtd_are_refused(tmp_path: Path, part: str, message: str) -> None:
    assert docx_media.read_property(_package(tmp_path / "plain.docx"), 64) == VALUE

    cover = _package(tmp_path / "cover.docx", part, DEFAULTS)

    with pytest.raises(UnsupportedFormatError, match=message):
        object_stream.open_channel(cover, RULES)


def test_main_relationships_with_a_dtd_are_refused(tmp_path: Path) -> None:
    """Standard's location reads them too."""
    assert docx_media.read_part(_package(tmp_path / "plain.docx"), 64) == b""

    cover = _package(tmp_path / "cover.docx", "word/_rels/document.xml.rels", DEFAULTS)

    with pytest.raises(UnsupportedFormatError, match="broken package relationships"):
        object_stream.open_channel(cover)


def test_content_types_with_a_dtd_are_refused(tmp_path: Path) -> None:
    pytest.importorskip("docx")
    cover = _package(tmp_path / "cover.docx", "[Content_Types].xml", DEFAULTS)

    with pytest.raises(UnsupportedFormatError, match="broken content types"):
        docx_media.load_document(cover)


def test_xmp_with_a_dtd_is_refused(tmp_path: Path) -> None:
    pikepdf = pytest.importorskip("pikepdf")
    document = pikepdf.new()
    document.add_blank_page()
    xmp = DEFAULTS + f'<rdf:RDF xmlns:rdf="{RDF}"><a/><a/></rdf:RDF>'.encode()
    document.Root.Metadata = document.make_stream(xmp)
    cover = tmp_path / "cover.pdf"
    document.save(cover, fix_metadata_version=False)  # pikepdf would rewrite the XMP

    with pytest.raises(UnsupportedFormatError, match="broken metadata"):
        object_stream.open_channel(cover, RULES)


def test_xmp_in_an_encoding_expat_cannot_read_is_refused(tmp_path: Path) -> None:
    pikepdf = pytest.importorskip("pikepdf")
    document = pikepdf.new()
    document.add_blank_page()
    document.Root.Metadata = document.make_stream(
        SHIFT_JIS + f'<rdf:RDF xmlns:rdf="{RDF}"/>'.encode()
    )
    cover = tmp_path / "cover.pdf"
    document.save(cover, fix_metadata_version=False)

    with pytest.raises(UnsupportedFormatError, match="broken metadata"):
        object_stream.open_channel(cover, RULES)


@pytest.mark.parametrize("method", [MethodId.OBJECT_STREAM, MethodId.WHITESPACE])
def test_other_writes_take_package_relationships_expat_cannot_read(
    tmp_path: Path, method: MethodId
) -> None:
    """Only the stale-property cleanup reads them, and it skips what it cannot read."""
    python_docx = pytest.importorskip("docx")
    document = python_docx.Document()

    for index in range(40):
        document.add_paragraph(f"Paragraph {index}: a manuscript keeps traces.")

    plain, cover = tmp_path / "plain.docx", tmp_path / "cover.docx"
    document.save(str(plain))

    with zipfile.ZipFile(plain) as source, zipfile.ZipFile(cover, "w") as output:
        for info in source.infolist():
            content = source.read(info)

            if info.filename == "_rels/.rels":
                content = SHIFT_JIS + content.split(b"?>", 1)[1].lstrip()
                relationships = content

            output.writestr(info, content)

    embedded = tmp_path / "embedded.docx"
    options = service.HideOptions(
        CarrierKind.DOCUMENT, method, CipherId.AES_GCM, PASSWORD, is_research=True
    )
    service.hide(cover, FILE, embedded, options)

    with zipfile.ZipFile(embedded) as package:
        assert package.read("_rels/.rels") == relationships

    assert service.reveal(embedded, tmp_path / "out", PASSWORD).path.read_bytes() == FILE.data
