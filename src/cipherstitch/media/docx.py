"""DOCX carriers: the printer-settings part or a custom property (stdlib :mod:`zipfile`)
and paragraph text (python-docx, the ``documents`` extra).

The part is linked from the main document's relationships and declared in
``[Content_Types].xml`` (docs/format.md#stream-channels): Word and python-docx drop
unlinked parts. Storing a payload replaces the cover's own printer settings. Research's
metadata stream stores it in a custom property instead.
"""

import itertools
import lzma
import posixpath
import re
import zipfile
import zlib
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from cipherstitch import catalog
from cipherstitch.errors import NotAvailableError, UnsupportedFormatError
from cipherstitch.media import properties, untrusted_xml

PART = "word/printerSettings/printerSettings1.bin"
PRINTER_SETTINGS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/printerSettings"
)
_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.printerSettings"
_CUSTOM_PROPERTIES = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
)
_CUSTOM_PART = "docProps/custom.xml"  # where Word keeps them
_CUSTOM_OVERRIDE = (
    f'<Override PartName="/{_CUSTOM_PART}" '
    'ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>'
).encode()
_CUSTOM_NAMESPACE = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_VARIANT_NAMESPACE = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_USER_DEFINED = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"  # FMTID_UserDefinedProperties
_FIRST_PROPERTY_ID = 2  # 0 and 1 are reserved
_PACKAGE_RELATIONSHIPS = "_rels/.rels"
_CONTENT_TYPES = "[Content_Types].xml"
_MAIN_PART = "word/document.xml"
_MAIN_RELATIONSHIPS = "word/_rels/document.xml.rels"
_TARGET = PART.removeprefix("word/")  # relative to the main document
_OVERRIDE = f'<Override PartName="/{PART}" ContentType="{_CONTENT_TYPE}"/>'.encode()
_KIB = 1024
_TYPES_NAMESPACE = "{http://schemas.openxmlformats.org/package/2006/content-types}"
# A hostile package can inflate a few kilobytes into gigabytes. zipfile never inflates an
# entry past its declared size, so declared sizes are checked before reading.
# The content types and relationships list the parts. Word keeps them in kilobytes (about
# 150 bytes a relationship).
_MAX_LIST_SIZE = 4 << 20
# python-docx parses the XML parts it reaches into trees some 20 times their size (about
# 700 MiB at this cap, less than a maximum-size image costs).
_MAX_XML_SIZE = 32 << 20
# python-docx holds every part it reaches in memory, pictures included, and its walk over
# the parts takes quadratic time (20 000 parts take 4 s).
_MAX_PACKAGE_SIZE = 256 << 20
_MAX_PARTS = 10_000
# Custom properties take kilobytes, and the payload's base64 at most 1 MiB.
_MAX_PROPERTIES_SIZE = 8 << 20
# What reading a corrupt, truncated, encrypted or Deflate64 entry raises (OSError for one
# at a crafted offset or in a broken bzip2 stream).
_ENTRY_ERRORS = (
    zipfile.BadZipFile,
    zlib.error,
    lzma.LZMAError,
    EOFError,
    NotImplementedError,
    RuntimeError,
    OSError,
)
# What one method or location leaves behind is cleared when another stores a payload, so a
# document never holds a stale payload: whitespace padding runs (see methods/whitespace.py),
# the printer-settings parts with their relationships, references and content-type entries,
# and the custom property a metadata write stores (as it stores it).
# Runs with tabs (Research's space and tab) stay: a cover's own runs of tabs look the same.
_BIT_RUNS = re.compile(rb'<w:r><w:t xml:space="preserve">(?: |\xc2\xa0)+</w:t></w:r>(?=</w:p>)')


def _empty_element(name: str, attribute: str, value: str) -> bytes:
    """A pattern for an empty element ``name`` whose ``attribute`` is ``value``. It takes
    linear time on hostile parts, such as a comment that holds the element's start or the
    attribute thousands of times and no ``>``: a match never crosses another tag, and once
    the attribute is found, nothing is tried again."""
    found = rf'<{name}\b(?>[^<>]*\b{attribute}="'.encode() + re.escape(value.encode())

    return found + rb'")[^<>]*+(?<=/)>'


_PRINTER_SETTINGS_LINK = re.compile(_empty_element("Relationship", "Type", PRINTER_SETTINGS))
# python-docx declares the part's type by its extension.
_PRINTER_SETTINGS_DEFAULT = re.compile(_empty_element("Default", "ContentType", _CONTENT_TYPE))
_RELATIONSHIP_ID = re.compile(rb'\bId="([^"]*)"')
_PRINTER_SETTINGS_REFERENCE = re.compile(rb"<w:printerSettings\b[^<>]*/>")
_STORED_PROPERTY = re.compile(
    rf'<property fmtid="{re.escape(_USER_DEFINED)}" pid="\d+" name="{properties.NAME}">'
    r"<vt:blob>[A-Za-z0-9+/=]*</vt:blob></property>".encode()
)
# At most 9 digits: an int32, as Word stores it, and never too long for int().
_PROPERTY_ID = re.compile(rb'\bpid="(\d{1,9})"')
# A part's XML declaration and the line break after it, as Word writes them: double quotes
# and CRLF, where python-docx writes single quotes and LF.
_UTF8_DECLARATION = re.compile(rb"""<\?xml\b[^>]*\bencoding=["'](?i:utf-8)["'][^>]*\?>\s*""")
_WORD_DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + bytes([13, 10])


def describe(path: Path) -> str:
    """E.g. ``48.0 KiB · DOCX``."""
    with _package(path):
        pass

    return f"{path.stat().st_size / _KIB:,.1f} KiB · DOCX"


def read_part(path: Path, limit: int) -> bytes:
    """Up to ``limit`` bytes of the printer-settings part; empty if there is none."""
    with _package(path) as package:
        targets = _linked_parts(path, package)

        if not targets or targets[0] not in package.namelist():
            return b""

        with package.open(targets[0]) as part:
            return part.read(limit)


def read_property(path: Path, limit: int) -> bytes:
    """Up to ``limit`` bytes of the custom property; empty if there is none (or a value this
    program cannot have written)."""
    with _package(path) as package:
        part = _custom_part(path, package)

        if part is None or part not in package.namelist():
            return b""

        values = _property_values(path, _read(path, package, part, _MAX_PROPERTIES_SIZE))

    value = values[0] if values else None

    return b"" if value is None else properties.decoded(value, limit)


@dataclass(frozen=True, slots=True)
class EmbeddedDocxPart:
    source: Path
    data: bytes

    def save(self, path: Path) -> None:
        with _package(self.source) as package:
            if _MAIN_RELATIONSHIPS not in package.namelist():
                raise UnsupportedFormatError(
                    f"{self.source.name} uses an unsupported package layout"
                )

            replaced = {PART, *_linked_parts(self.source, package)}
            stale = _stale_property_part(self.source, package)
            entries = [
                (info, self._rewritten(info.filename, package.read(info), replaced, stale))
                for info in package.infolist()
                if info.filename not in replaced
            ]
            part = _entry(PART, like=package.getinfo(_MAIN_PART))

        # Everything that can fail is done: only now is the output (maybe the source) replaced.
        with zipfile.ZipFile(path, "w") as output:
            for info, content in entries:
                output.writestr(info, content)

            output.writestr(part, self.data)

    def _rewritten(
        self, name: str, content: bytes, replaced: Collection[str], stale: str | None
    ) -> bytes:
        kept = _unlinked(name, content, replaced)

        if name == _MAIN_RELATIONSHIPS:
            link = _relationship(kept, PRINTER_SETTINGS, _TARGET)
            linked = _insert(self.source, kept, b"<Relationships", link)

            # A link _PRINTER_SETTINGS_LINK missed (e.g. not a self-closing element) would
            # be followed instead of ours.
            if _printer_settings_targets(self.source, linked) != [PART]:
                raise UnsupportedFormatError(
                    f"{self.source.name} uses an unsupported package layout"
                )

            return linked

        if name == _CONTENT_TYPES:
            return _insert(self.source, kept, b"<Types", _OVERRIDE)

        if name == _MAIN_PART:
            return _BIT_RUNS.sub(b"", kept)

        if name == stale:
            return _STORED_PROPERTY.sub(b"", content)

        return content


@dataclass(frozen=True, slots=True)
class EmbeddedDocxProperty:
    """The payload in a custom property (Research's metadata stream). Like
    :class:`EmbeddedDocxPart`, it clears the printer settings and whitespace padding that
    other writes leave."""

    source: Path
    data: bytes

    def save(self, path: Path) -> None:
        with _package(self.source) as package:
            linked = _linked_parts(self.source, package)
            found = _custom_part(self.source, package)
            part = _CUSTOM_PART if found is None else found

            # A new part must not take the name of a part no one links, and a linked part
            # must be there and stay.
            if (part in package.namelist()) is (found is None) or part in linked:
                raise _unsupported_layout(self.source)

            entries = [
                (info, self._rewritten(info.filename, package.read(info), linked, part, found))
                for info in package.infolist()
                if info.filename not in linked
            ]

            if found is None:
                like = package.getinfo(_MAIN_PART)
                created = _new_properties(package.read(like))
                stored = _with_property(self.source, created, properties.encoded(self.data))
                entries.append((_entry(part, like=like), stored))

        with zipfile.ZipFile(path, "w") as output:
            for info, content in entries:
                output.writestr(info, content)

    def _rewritten(
        self, name: str, content: bytes, linked: Collection[str], part: str, found: str | None
    ) -> bytes:
        """The entry ``name`` once ``part`` holds the payload. A new part (none ``found``)
        gets a package relationship and a content type."""
        if linked:
            content = _unlinked(name, content, linked)

        if name == _MAIN_PART:
            return _BIT_RUNS.sub(b"", content)

        if name == found:
            return _with_property(self.source, content, properties.encoded(self.data))

        if found is None and name == _PACKAGE_RELATIONSHIPS:
            link = _relationship(content, _CUSTOM_PROPERTIES, part)
            relationships = _insert(self.source, content, b"<Relationships", link)

            if _targets(self.source, relationships, _CUSTOM_PROPERTIES, "") != [part]:
                raise _unsupported_layout(self.source)

            return relationships

        if found is None and name == _CONTENT_TYPES:
            return _insert(
                self.source, _without_overrides(content, [part]), b"<Types", _CUSTOM_OVERRIDE
            )

        return content


def load_document(path: Path) -> Any:
    docx = _python_docx()

    with _package(path) as package:
        _check_loadable(path, package)

    # python-docx checks little: a relationship without a target or a content type without a
    # part name fails in Python's own errors.
    try:
        return docx.Document(str(path))
    except (
        docx.opc.exceptions.OpcError,
        KeyError,
        SyntaxError,
        ValueError,
        TypeError,
        AttributeError,
        IndexError,
        *_ENTRY_ERRORS,
    ) as exc:
        raise UnsupportedFormatError(f"{path.name} is not a readable DOCX file") from exc


@dataclass(frozen=True, slots=True)
class EmbeddedDocxDocument:
    source: Path
    document: Any  # a python-docx Document

    def save(self, path: Path) -> None:
        """The cover's package with the main document python-docx changed: every other
        entry is the cover's, in its place and byte for byte (python-docx would write every
        part anew, in its own order). The printer-settings parts go, with everything that
        links them, if present."""
        with _package(self.source) as package:
            linked = _linked_parts(self.source, package)
            stale = _stale_property_part(self.source, package)
            entries = [
                (info, self._rewritten(info.filename, package.read(info), linked, stale))
                for info in package.infolist()
                if info.filename not in linked
            ]

        with zipfile.ZipFile(path, "w") as output:
            for info, content in entries:
                output.writestr(info, content)

    def _rewritten(
        self, name: str, content: bytes, linked: Collection[str], stale: str | None
    ) -> bytes:
        if name == self.document.part.partname.membername:
            content = _main_part(content, self.document.part.blob)

        if name == stale:
            content = _STORED_PROPERTY.sub(b"", content)

        return _unlinked(name, content, linked) if linked else content


def _main_part(cover: bytes, xml: bytes) -> bytes:
    """``xml``, the main part as python-docx writes it, with the cover's XML declaration."""
    declaration = _UTF8_DECLARATION.match(cover)

    if declaration is None:
        return xml

    return declaration[0] + xml[xml.index(b"?>") + 2 :].lstrip()


def _entry(name: str, like: zipfile.ZipInfo) -> zipfile.ZipInfo:
    """A new package entry dated, compressed and made like ``like``, so that it does not stand
    out among the cover's entries. :mod:`zipfile` writes its own flags, and permissions
    where ``like`` has none."""
    entry = zipfile.ZipInfo(name, date_time=like.date_time)
    entry.compress_type = like.compress_type
    entry.create_system = like.create_system
    entry.create_version = like.create_version
    entry.extract_version = like.extract_version
    entry.external_attr = like.external_attr

    return entry


def _unlinked(name: str, content: bytes, parts: Collection[str]) -> bytes:
    """The package entry ``name`` without printer-settings relationships, the sections'
    references to them, or their content types (and those of ``parts``)."""
    if name == _MAIN_RELATIONSHIPS:
        return _PRINTER_SETTINGS_LINK.sub(b"", content)

    if name == _MAIN_PART:
        return _PRINTER_SETTINGS_REFERENCE.sub(b"", content)

    if name == _CONTENT_TYPES:
        return _without_overrides(_PRINTER_SETTINGS_DEFAULT.sub(b"", content), parts)

    return content


def _without_overrides(content_types: bytes, parts: Collection[str]) -> bytes:
    """``content_types`` without the content types declared for ``parts``."""
    for part in parts:
        override = _empty_element("Override", "PartName", f"/{part}")
        content_types = re.sub(override, b"", content_types)

    return content_types


def _relationship(relationships: bytes, kind: str, target: str) -> bytes:
    """A relationship of type ``kind``, with the first ``rIdN`` not in ``relationships``."""
    used = set(_RELATIONSHIP_ID.findall(relationships))
    number = next(n for n in itertools.count(1) if f"rId{n}".encode() not in used)

    return f'<Relationship Id="rId{number}" Type="{kind}" Target="{target}"/>'.encode()


@contextmanager
def _package(path: Path) -> Iterator[zipfile.ZipFile]:
    """The package, open for the block. An entry the block cannot read makes the whole file
    unreadable."""
    # Opening fails on a later zip version, or an entry name marked UTF-8 that isn't (ValueError).
    try:
        package = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, NotImplementedError, ValueError, EOFError) as exc:
        raise UnsupportedFormatError(f"{path.name} is not a DOCX file") from exc

    with package:
        if not {_PACKAGE_RELATIONSHIPS, _CONTENT_TYPES, _MAIN_PART} <= set(package.namelist()):
            raise UnsupportedFormatError(f"{path.name} is not a DOCX file")

        try:
            yield package
        except _ENTRY_ERRORS as exc:
            raise UnsupportedFormatError(f"{path.name} is not a readable DOCX file") from exc


def _read(path: Path, package: zipfile.ZipFile, name: str, limit: int) -> bytes:
    """The entry ``name``, refused before inflating if it declares more than ``limit`` bytes."""
    if package.getinfo(name).file_size > limit:
        raise UnsupportedFormatError(f"{path.name} is too large to read")

    return package.read(name)


def _check_loadable(path: Path, package: zipfile.ZipFile) -> None:
    """Refuse a package that python-docx would take too much memory or time to load."""
    entries = package.infolist()

    if (
        len(entries) > _MAX_PARTS
        or sum(entry.file_size for entry in entries) > _MAX_PACKAGE_SIZE
        or _xml_size(path, package) > _MAX_XML_SIZE
    ):
        raise UnsupportedFormatError(f"{path.name} is too large to read")


def _xml_size(path: Path, package: zipfile.ZipFile) -> int:
    """What python-docx may parse as XML: the content types, the relationships and every
    part they declare XML, whatever its name (looked up as python-docx does)."""
    content_types = _read(path, package, _CONTENT_TYPES, _MAX_LIST_SIZE)
    types = _parsed(path, content_types, "content types")
    defaults = {
        element.get("Extension", "").lower(): element.get("ContentType", "")
        for element in types.iter(f"{_TYPES_NAMESPACE}Default")
    }
    overrides = {
        element.get("PartName", "").lower(): element.get("ContentType", "")
        for element in types.iter(f"{_TYPES_NAMESPACE}Override")
    }
    size = 0

    for entry in package.infolist():
        name = entry.filename
        extension = posixpath.splitext(name)[1].removeprefix(".").lower()
        content_type = overrides.get(f"/{name}".lower(), defaults.get(extension, ""))

        if name == _CONTENT_TYPES or name.endswith(".rels") or content_type.endswith("xml"):
            size += entry.file_size

    return size


def _linked_parts(path: Path, package: zipfile.ZipFile) -> list[str]:
    """The package names of the printer-settings parts the main document links, in order.

    Only parts next to :data:`PART` count: a link to any other part (a crafted file) must
    not make storing a payload drop that part.
    """
    if _MAIN_RELATIONSHIPS not in package.namelist():
        return []

    relationships = _read(path, package, _MAIN_RELATIONSHIPS, _MAX_LIST_SIZE)
    targets = _printer_settings_targets(path, relationships)
    folder = posixpath.dirname(PART)

    return [target for target in targets if posixpath.dirname(target) == folder]


def _printer_settings_targets(path: Path, relationships: bytes) -> list[str]:
    """The package names that the main document's ``relationships`` link as printer settings."""
    return _targets(path, relationships, PRINTER_SETTINGS, posixpath.dirname(_MAIN_PART))


def _targets(path: Path, relationships: bytes, kind: str, base: str) -> list[str]:
    """The package names that ``relationships``, those of a part in the folder ``base``, link
    as ``kind``."""
    return [
        posixpath.normpath(posixpath.join(base, link.get("Target", ""))).lstrip("/")
        for link in _parsed(path, relationships, "package relationships")
        if link.get("Type") == kind
    ]


def _custom_part(path: Path, package: zipfile.ZipFile) -> str | None:
    """The package name of the custom-properties part, or ``None`` if the package links none."""
    relationships = _read(path, package, _PACKAGE_RELATIONSHIPS, _MAX_LIST_SIZE)
    targets = _targets(path, relationships, _CUSTOM_PROPERTIES, "")

    return targets[0] if targets else None


def _stale_property_part(path: Path, package: zipfile.ZipFile) -> str | None:
    """The custom-properties part, which may hold what a metadata write stored. Package
    relationships that cannot be read link no part a reader could reveal from."""
    try:
        return _custom_part(path, package)
    except UnsupportedFormatError:
        return None


def _property_values(path: Path, xml: bytes) -> list[str | None]:
    """The values of the custom properties named :data:`properties.NAME`, in order: a blob's
    text, ``None`` for another type."""
    root = _parsed(path, xml, "custom properties")

    if root.tag != f"{{{_CUSTOM_NAMESPACE}}}Properties":
        raise UnsupportedFormatError(f"{path.name} has broken custom properties")

    return [
        _blob_text(element)
        for element in root.findall(f"{{{_CUSTOM_NAMESPACE}}}property")
        if element.get("name") == properties.NAME
    ]


def _blob_text(element: ElementTree.Element) -> str | None:
    """The text of a custom property's blob, ``None`` for a value of another type."""
    value = next(iter(element), None)

    if value is None or value.tag != f"{{{_VARIANT_NAMESPACE}}}blob":
        return None

    return value.text or ""


def _new_properties(main: bytes) -> bytes:
    """An empty custom-properties part with the XML declaration of ``main``, the main part."""
    declaration = _UTF8_DECLARATION.match(main)
    root = f'<Properties xmlns="{_CUSTOM_NAMESPACE}" xmlns:vt="{_VARIANT_NAMESPACE}"></Properties>'

    return (_WORD_DECLARATION if declaration is None else declaration[0]) + root.encode()


def _with_property(path: Path, xml: bytes, value: str) -> bytes:
    """The custom properties ``xml`` holding ``value``, in place of what a write stored
    before."""
    kept = _STORED_PROPERTY.sub(b"", xml)
    number = max(map(int, _PROPERTY_ID.findall(kept)), default=_FIRST_PROPERTY_ID - 1) + 1
    element = (
        f'<property fmtid="{_USER_DEFINED}" pid="{number}" name="{properties.NAME}">'
        f"<vt:blob>{value}</vt:blob></property>"
    )
    stored = _insert(path, kept, b"<Properties", element.encode())

    if len(stored) > _MAX_PROPERTIES_SIZE:
        raise UnsupportedFormatError(f"{path.name} is too large to read")

    # A layout the edit missed (a prefixed root, another vt prefix, a property of that name
    # stored another way) would read as something else.
    try:
        is_stored = _property_values(path, stored) == [value]
    except UnsupportedFormatError as exc:
        raise _unsupported_layout(path) from exc

    if not is_stored:
        raise _unsupported_layout(path)

    return stored


def _unsupported_layout(path: Path) -> UnsupportedFormatError:
    return UnsupportedFormatError(f"{path.name} uses an unsupported package layout")


def _parsed(path: Path, xml: bytes, what: str) -> ElementTree.Element:
    try:
        return untrusted_xml.parsed(xml)
    except ElementTree.ParseError as exc:
        raise UnsupportedFormatError(f"{path.name} has broken {what}") from exc


def _insert(path: Path, xml: bytes, root: bytes, element: bytes) -> bytes:
    """``element`` added as the last child of the root, which must use the default namespace
    and have an end tag."""
    if root not in xml or b"</" not in xml:
        raise UnsupportedFormatError(f"{path.name} uses an unsupported package layout")

    end = xml.rindex(b"</")

    return xml[:end] + element + xml[end:]


def _python_docx() -> Any:
    try:
        import docx
        import docx.opc.exceptions
    except ImportError as exc:
        raise NotAvailableError(catalog.PYTHON_DOCX.install_hint("Whitespace padding")) from exc

    return docx
