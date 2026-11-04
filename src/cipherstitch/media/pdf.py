"""PDF carriers (pikepdf, the ``documents`` extra).

The payload is the private data of an application in the document's page-piece
dictionary (docs/format.md#stream-channels), so it is referenced from the catalog:
pikepdf drops unreferenced objects when it saves, and has no incremental-update mode.
Research's metadata stream puts it in a property of the document's XMP instead.
"""

import re
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from cipherstitch import catalog
from cipherstitch.errors import NotAvailableError, UnsupportedFormatError
from cipherstitch.media import properties, untrusted_xml

PIECE_INFO = "/PieceInfo"
APPLICATION = "/Editor"
# A UUID namespace needs no domain, so it names and imitates no one.
_NAMESPACE = "urn:uuid:489bd2b8-2e41-442c-a303-bb107208f89c"
_PRIVATE = "/Private"
_METADATA = "/Metadata"
_MIN_VERSION = "1.4"  # the first with a page-piece dictionary or metadata in the catalog
_KIB = 1024
_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_PREFIX = "doc"
# A cover's XMP takes kilobytes, and the property's base64 at most 1 MiB. The parsed tree
# takes some 20 times the XML.
_MAX_XMP_SIZE = 8 << 20
# The packet a metadata write starts from when the PDF has none. The xpacket id is the one
# the XMP specification gives.
_NEW_XMP = (
    f'<?xpacket begin="{chr(0xFEFF)}" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    f'<rdf:RDF xmlns:rdf="{_RDF}">\n'
    "</rdf:RDF>\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>'
).encode()
_RDF_END = b"</rdf:RDF>"
_ELEMENT = f"{_PREFIX}:{properties.NAME}"
# The description a metadata write adds, which every write removes again.
_STORED = re.compile(
    rf'<rdf:Description rdf:about="[^"<]*" xmlns:{_PREFIX}="{re.escape(_NAMESPACE)}">'
    rf"<{_ELEMENT}>[A-Za-z0-9+/=]*</{_ELEMENT}></rdf:Description>".encode()
)


def describe(path: Path) -> str:
    """E.g. ``12 pages · 840.2 KiB · PDF``."""
    with _open(path) as pdf:
        pages = len(pdf.pages)

    size_kib = path.stat().st_size / _KIB

    return f"{pages} page{'' if pages == 1 else 's'} · {size_kib:,.1f} KiB · PDF"


def read_data(path: Path, limit: int) -> bytes:
    """Up to ``limit`` bytes of the stored stream; empty if the PDF holds none (or a stream
    this program cannot have written). Decoding stops at ``limit``, so a crafted stream
    cannot exhaust memory."""
    pikepdf = _pikepdf()

    with _open(path) as pdf:
        stored = _stored_stream(pdf, pikepdf)

        if stored is None:
            return b""

        try:
            data = _decoded(stored, pikepdf, limit)
        except (pikepdf.PikepdfError, zlib.error) as exc:
            raise UnsupportedFormatError(f"{path.name} is not a readable PDF") from exc

        return b"" if data is None else data


def read_metadata(path: Path, limit: int) -> bytes:
    """Up to ``limit`` bytes of the property in the XMP; empty if there is none (or a value
    this program cannot have written). Metadata that is not XMP, or too large to parse,
    makes the PDF unreadable."""
    pikepdf = _pikepdf()

    with _open(path) as pdf:
        xmp = _xmp(path, pdf, pikepdf)

    values = [] if xmp is None else _property_values(path, xmp)

    return properties.decoded(values[0], limit) if values else b""


@dataclass(frozen=True, slots=True)
class EmbeddedPdf:
    source: Path
    data: bytes

    def save(self, path: Path) -> None:
        pikepdf = _pikepdf()

        with _open(self.source, allow_overwriting_input=True) as pdf:
            pieces = pdf.Root.get(PIECE_INFO)
            others = dict(pieces.items()) if isinstance(pieces, pikepdf.Dictionary) else {}
            modified = datetime.now(UTC).strftime("D:%Y%m%d%H%M%SZ")
            data = {"/LastModified": modified, _PRIVATE: pdf.make_stream(self.data)}
            pdf.Root[PIECE_INFO] = pikepdf.Dictionary({**others, APPLICATION: data})
            _remove_property(self.source, pdf, pikepdf)
            # Keep the author's restrictions. pikepdf's default would rewrite the cover's
            # XMP, put its own name in for XMP it can't parse, and fail on other /Metadata.
            pdf.save(
                path,
                encryption=pdf.is_encrypted,
                min_version=_MIN_VERSION,
                fix_metadata_version=False,
            )


@dataclass(frozen=True, slots=True)
class EmbeddedPdfMetadata:
    """The payload in a property of the XMP (Research's metadata stream). Every other part
    of the XMP stays as it was, byte for byte."""

    source: Path
    data: bytes

    def save(self, path: Path) -> None:
        pikepdf = _pikepdf()
        value = properties.encoded(self.data)

        with _open(self.source, allow_overwriting_input=True) as pdf:
            xmp = _xmp(self.source, pdf, pikepdf)

            if xmp is None:
                stored = _with_property(self.source, _NEW_XMP, value)
                pdf.Root[_METADATA] = pdf.make_stream(
                    stored, Type=pikepdf.Name.Metadata, Subtype=pikepdf.Name.XML
                )
            else:
                pdf.Root[_METADATA].write(_with_property(self.source, xmp, value))

            _remove_private_data(pdf, pikepdf)
            # pikepdf's version fix would rewrite the whole XMP, and replace XMP it cannot
            # parse with a packet that names it.
            pdf.save(
                path,
                encryption=pdf.is_encrypted,
                min_version=_MIN_VERSION,
                fix_metadata_version=False,
            )


def _decoded(stream: Any, pikepdf: Any, limit: int) -> bytes | None:
    """Up to ``limit`` bytes of the stream's data, or ``None`` for filters this program never
    writes."""
    raw = bytes(stream.read_raw_bytes())
    filters = stream.get("/Filter")

    if filters is None:
        return raw[:limit]

    if filters == pikepdf.Name.FlateDecode and "/DecodeParms" not in stream:
        return zlib.decompressobj().decompress(raw, limit)

    return None


def _stored_stream(pdf: Any, pikepdf: Any) -> Any:
    """The application's private stream, or ``None`` if the PDF holds none."""
    pieces = pdf.Root.get(PIECE_INFO)
    data = pieces.get(APPLICATION) if isinstance(pieces, pikepdf.Dictionary) else None
    stored = data.get(_PRIVATE) if isinstance(data, pikepdf.Dictionary) else None

    return stored if isinstance(stored, pikepdf.Stream) else None


def _remove_private_data(pdf: Any, pikepdf: Any) -> None:
    """Remove what a write to the page-piece dictionary stored, and the dictionary if no other
    application's data is left."""
    pieces = pdf.Root.get(PIECE_INFO)

    if not isinstance(pieces, pikepdf.Dictionary) or APPLICATION not in pieces:
        return

    del pieces[APPLICATION]

    if not pieces.keys():
        del pdf.Root[PIECE_INFO]


def _remove_property(path: Path, pdf: Any, pikepdf: Any) -> None:
    """Remove the description a metadata write added, if the XMP holds it. XMP this program
    cannot read holds no property it could reveal, so it stays as it is."""
    try:
        xmp = _xmp(path, pdf, pikepdf)
    except UnsupportedFormatError:
        return

    if xmp is not None and _STORED.search(xmp):
        pdf.Root[_METADATA].write(_STORED.sub(b"", xmp))


def _xmp(path: Path, pdf: Any, pikepdf: Any) -> bytes | None:
    """The catalog's metadata, or ``None`` if it has none."""
    metadata = pdf.Root.get(_METADATA)

    if metadata is None:
        return None

    broken = UnsupportedFormatError(f"{path.name} has broken metadata")

    if not isinstance(metadata, pikepdf.Stream):
        raise broken

    try:
        xmp = _decoded(metadata, pikepdf, _MAX_XMP_SIZE + 1)
    except (pikepdf.PikepdfError, zlib.error) as exc:
        raise broken from exc

    if xmp is None:
        raise broken

    if len(xmp) > _MAX_XMP_SIZE:
        raise UnsupportedFormatError(f"{path.name} is too large to read")

    return xmp


def _property_values(path: Path, xmp: bytes) -> list[str]:
    """The values of the property in ``xmp``, in document order. XMP may hold a simple
    property as an element or as an attribute of its description."""
    root = _rdf(path, xmp)
    name = f"{{{_NAMESPACE}}}{properties.NAME}"
    values: list[str] = []

    for element in root.iter():
        if element.tag == name:
            values.append(element.text or "")

        if name in element.attrib:
            values.append(element.attrib[name])

    return values


def _rdf(path: Path, xmp: bytes) -> ElementTree.Element:
    """The ``rdf:RDF`` element of ``xmp``."""
    try:
        root = untrusted_xml.parsed(xmp)
    except ElementTree.ParseError as exc:
        raise UnsupportedFormatError(f"{path.name} has broken metadata") from exc

    rdf = root if root.tag == f"{{{_RDF}}}RDF" else root.find(f".//{{{_RDF}}}RDF")

    if rdf is None:
        raise UnsupportedFormatError(f"{path.name} has metadata that is not XMP")

    return rdf


def _with_property(path: Path, xmp: bytes, value: str) -> bytes:
    """``xmp`` holding ``value`` in a description of its own, in place of the one a write
    stored before."""
    kept = _STORED.sub(b"", xmp)
    end = kept.rfind(_RDF_END)
    unsupported = UnsupportedFormatError(f"{path.name} uses an unsupported metadata layout")

    if end < 0:
        raise unsupported

    # Every top-level description must describe the same resource.
    first = _rdf(path, kept).find(f"{{{_RDF}}}Description")
    about = "" if first is None else first.get(f"{{{_RDF}}}about", "")
    quoted = escape(about, {'"': "&quot;"})
    description = (
        f'<rdf:Description rdf:about="{quoted}" xmlns:{_PREFIX}="{_NAMESPACE}">'
        f"<{_ELEMENT}>{value}</{_ELEMENT}></rdf:Description>"
    )
    stored = kept[:end] + description.encode() + kept[end:]

    if len(stored) > _MAX_XMP_SIZE:
        raise UnsupportedFormatError(f"{path.name} is too large to read")

    # A layout the edit missed (another prefix for RDF, a property kept in another form)
    # would read as something else.
    try:
        is_stored = _property_values(path, stored) == [value]
    except UnsupportedFormatError as exc:
        raise unsupported from exc

    if not is_stored:
        raise unsupported

    return stored


def _open(path: Path, **options: Any) -> Any:
    pikepdf = _pikepdf()

    try:
        return pikepdf.open(path, **options)
    except pikepdf.PasswordError as exc:
        raise UnsupportedFormatError(f"{path.name} is password-protected") from exc
    except pikepdf.PikepdfError as exc:
        raise UnsupportedFormatError(f"{path.name} is not a readable PDF") from exc


def _pikepdf() -> Any:
    try:
        import pikepdf
    except ImportError as exc:
        raise NotAvailableError(catalog.FORMAT_REQUIREMENTS[".pdf"].install_hint("PDF")) from exc

    return pikepdf
