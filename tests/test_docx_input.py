"""DOCX packages that python-docx or zipfile fail on in their own ways, and parts made to
make the byte edits slow."""

import re
import time
import zipfile
from pathlib import Path

import pytest

from cipherstitch import service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.errors import NoHiddenDataError, UnsupportedFormatError
from cipherstitch.media import docx as docx_media
from cipherstitch.methods.whitespace import WhitespaceChannel
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions

MAIN_RELATIONSHIPS = "word/_rels/document.xml.rels"
CONTENT_TYPES = "[Content_Types].xml"
CENTRAL_ENTRY_NAME = 46  # where a central directory entry's file name starts
CENTRAL_ENTRY_FLAGS = 8
UTF8_NAME = 0x800
FILE = HiddenFile("note.txt", b"in the printer settings")
OBJECT = HideOptions(CarrierKind.DOCUMENT, MethodId.OBJECT_STREAM, CipherId.NONE, is_research=True)
HOSTILE_SIZE = 224_000  # took 7.5 s in an object-stream hide


@pytest.fixture
def cover(tmp_path: Path) -> Path:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("A short letter.")
    path = tmp_path / "cover.docx"
    document.save(str(path))

    return path


def _rewritten(source: Path, target: Path, name: str, change: tuple[bytes, bytes]) -> Path:
    """A copy of the package ``source`` with ``change`` made in the entry ``name``."""
    with zipfile.ZipFile(source) as package, zipfile.ZipFile(target, "w") as output:
        for info in package.infolist():
            content = package.read(info)
            output.writestr(info, content.replace(*change) if info.filename == name else content)

    return target


def test_an_entry_name_marked_utf8_that_is_not_is_refused(cover: Path, tmp_path: Path) -> None:
    raw = bytearray(cover.read_bytes())
    central = raw.rindex(MAIN_RELATIONSHIPS.encode()) - CENTRAL_ENTRY_NAME
    flags = int.from_bytes(raw[central + CENTRAL_ENTRY_FLAGS : central + 10], "little")
    raw[central + CENTRAL_ENTRY_FLAGS : central + 10] = (flags | UTF8_NAME).to_bytes(2, "little")
    raw[central + CENTRAL_ENTRY_NAME : central + CENTRAL_ENTRY_NAME + 4] = bytes(
        [0xFF, 0xFE, 0xFD, 0xFC]
    )
    (tmp_path / "named.docx").write_bytes(bytes(raw))

    with pytest.raises(UnsupportedFormatError, match="not a DOCX file"):
        service.describe_carrier(tmp_path / "named.docx", CarrierKind.DOCUMENT)


@pytest.mark.parametrize(
    ("name", "change"),
    [
        (
            MAIN_RELATIONSHIPS,
            (b"</Relationships>", b'<Relationship Id="rId99" Type="other"/></Relationships>'),
        ),
        (CONTENT_TYPES, (b"</Types>", b'<Override ContentType="application/xml"/></Types>')),
    ],
    ids=["relationship-without-target", "override-without-part-name"],
)
def test_a_package_python_docx_fails_on_is_not_readable(
    cover: Path, tmp_path: Path, name: str, change: tuple[bytes, bytes]
) -> None:
    """python-docx raised TypeError and AttributeError, and Standard's reveal stopped there
    instead of trying the other channels."""
    broken = _rewritten(cover, tmp_path / "broken.docx", name, change)

    with pytest.raises(UnsupportedFormatError, match="not a readable DOCX file"):
        WhitespaceChannel(broken)

    with pytest.raises(NoHiddenDataError):
        service.reveal(broken, tmp_path)


@pytest.mark.parametrize(
    ("start", "repeated"),
    [
        (b"", b"<Relationship "),
        (b"<Relationship", f' Type="{docx_media.PRINTER_SETTINGS}"'.encode()),
    ],
    ids=["element-starts", "attributes"],
)
def test_the_package_edits_take_linear_time(
    cover: Path, tmp_path: Path, start: bytes, repeated: bytes
) -> None:
    """A comment can hold an element's start, or one start with the attribute, thousands of
    times and no ``>``: the edits tried every start, or every attribute, against the rest of
    the comment."""
    comment = b"<!-- " + start + repeated * (HOSTILE_SIZE // len(repeated)) + b" -->"
    change = (b"</Relationships>", comment + b"</Relationships>")
    hostile = _rewritten(cover, tmp_path / "hostile.docx", MAIN_RELATIONSHIPS, change)
    began = time.perf_counter()
    service.hide(hostile, FILE, tmp_path / "embedded.docx", OBJECT)

    assert time.perf_counter() - began < 1
    assert service.reveal(tmp_path / "embedded.docx", tmp_path).path.read_bytes() == FILE.data


@pytest.mark.parametrize(
    ("pattern", "element"),
    [
        (
            docx_media._PRINTER_SETTINGS_LINK,
            f'<Relationship Id="rId9" Type="{docx_media.PRINTER_SETTINGS}" Target="x.bin"/>',
        ),
        (
            docx_media._PRINTER_SETTINGS_LINK,
            f'<Relationship\nType="{docx_media.PRINTER_SETTINGS}"\nId="rId9" />',
        ),
        (
            docx_media._PRINTER_SETTINGS_DEFAULT,
            f'<Default Extension="bin" ContentType="{docx_media._CONTENT_TYPE}"/>',
        ),
    ],
    ids=["link", "link-on-lines", "default"],
)
def test_the_edits_still_find_what_they_remove(pattern: re.Pattern[bytes], element: str) -> None:
    """An element that is not empty is left as it is: the writers then refuse the layout."""
    other = '<Relationship Id="rId1" Type="other" Target="y.xml"/>'
    xml = f"<Root>{other}{element}{element.replace('/>', '></Relationship>')}</Root>".encode()

    assert pattern.sub(b"", xml) == xml.replace(element.encode(), b"")
