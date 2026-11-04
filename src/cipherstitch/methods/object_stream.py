"""Embedded object stream: the payload stream stored whole as one object of a PDF (an
application's private data) or a DOCX (the printer-settings part), or with Research's
metadata stream as base64 text in a property of the document's metadata
(docs/format.md#stream-channels)."""

from dataclasses import dataclass
from pathlib import Path

from cipherstitch.media import docx, pdf, properties
from cipherstitch.media.docx import EmbeddedDocxPart, EmbeddedDocxProperty
from cipherstitch.media.pdf import EmbeddedPdf, EmbeddedPdfMetadata
from cipherstitch.methods.stream import StreamChannel, padded

# A policy, not a format limit (the object could be any size). Research mode raises it up
# to MAX_LIMIT, the most that reveal reads.
SOFT_LIMIT = 64 * 1024
MAX_LIMIT = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ObjectRules:
    """The most the object holds, whether random bytes pad it to a random length, and whether
    it goes into the document's metadata."""

    limit: int = SOFT_LIMIT
    is_padded: bool = True
    is_in_metadata: bool = False

    @property
    def capacity(self) -> int:
        """The most bytes the object stores."""
        return self._stored_size(self.limit)

    @property
    def read_limit(self) -> int:
        """The most bytes reveal reads: what the highest limit stores."""
        return self._stored_size(MAX_LIMIT)

    def stored(self, stream: bytes) -> bytes:
        return padded(stream, self.capacity) if self.is_padded else stream

    def _stored_size(self, limit: int) -> int:
        """In metadata, base64 takes 4 of the ``limit`` characters for every 3 bytes."""
        return properties.decoded_size(limit) if self.is_in_metadata else limit


STANDARD_OBJECT = ObjectRules()


class PdfObjectChannel(StreamChannel[EmbeddedPdf | EmbeddedPdfMetadata]):
    def __init__(self, path: Path, rules: ObjectRules = STANDARD_OBJECT) -> None:
        super().__init__(rules.capacity, rules.read_limit)
        self._path = path
        self._rules = rules
        read = pdf.read_metadata if rules.is_in_metadata else pdf.read_data
        self._data = read(path, rules.read_limit)  # also checks that the PDF is readable

    def _read_stream(self) -> bytes:
        return self._data

    def _written(self, stream: bytes) -> EmbeddedPdf | EmbeddedPdfMetadata:
        stored = self._rules.stored(stream)

        if self._rules.is_in_metadata:
            return EmbeddedPdfMetadata(self._path, stored)

        return EmbeddedPdf(self._path, stored)


class DocxObjectChannel(StreamChannel[EmbeddedDocxPart | EmbeddedDocxProperty]):
    def __init__(self, path: Path, rules: ObjectRules = STANDARD_OBJECT) -> None:
        super().__init__(rules.capacity, rules.read_limit)
        self._path = path
        self._rules = rules
        read = docx.read_property if rules.is_in_metadata else docx.read_part
        self._data = read(path, rules.read_limit)  # also checks that the DOCX is readable

    def _read_stream(self) -> bytes:
        return self._data

    def _written(self, stream: bytes) -> EmbeddedDocxPart | EmbeddedDocxProperty:
        stored = self._rules.stored(stream)

        if self._rules.is_in_metadata:
            return EmbeddedDocxProperty(self._path, stored)

        return EmbeddedDocxPart(self._path, stored)


def open_channel(
    path: Path, rules: ObjectRules = STANDARD_OBJECT
) -> PdfObjectChannel | DocxObjectChannel:
    if path.suffix.lower() == ".pdf":
        return PdfObjectChannel(path, rules)

    return DocxObjectChannel(path, rules)
