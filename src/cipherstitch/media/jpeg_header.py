"""JPEG marker segments up to the first scan: what a cover's header says, and libjpeg's output
given the cover's own header segments back, or a precover's numbered as cjpeg numbers them
(docs/methods.md#dct-coefficients-jpeg)."""

import io
from collections.abc import Mapping
from dataclasses import dataclass
from typing import BinaryIO

from cipherstitch.errors import UnsupportedFormatError

_MARKER = 0xFF
_SOI = 0xD8
_EOI = 0xD9
_SOS = 0xDA
_DHT = 0xC4
_DRI = 0xDD
_COM = 0xFE
_APP2 = 0xE2
_APPLICATION = range(0xE0, 0xF0)  # APP0 … APP15
_FRAMES = frozenset(range(0xC0, 0xD0)) - {_DHT, 0xC8, 0xCC}  # SOF0 … SOF15
_PROGRESSIVE = frozenset({0xC2, 0xC6, 0xCA, 0xCE})
_STUFFED = 0x00  # 0xFF 0x00 is a data byte, not a marker
_NO_LENGTH = frozenset({0x01, *range(0xD0, 0xD8)})  # TEM and RSTn: libjpeg skips them here
_LENGTH_SIZE = 2
_COUNTS = 16  # a Huffman table's code counts, one per code length
_DC, _AC = 0, 1
_ICC_LABEL = b"ICC_PROFILE" + bytes([0])
_ICC_CHUNK = 65_519  # the most of a profile one APP2 segment holds after its label and counters
_MAX_ICC_CHUNKS = 255  # the chunk counters are one byte each
# An APP2 segment that locates the pictures after the main one (CIPA DC-007), which libjpeg
# does not write: kept, it would point past the end of the file.
_MULTI_PICTURE_LABEL = b"MPF" + bytes([0])
# ITU T.81 Annex K.3 (tables K.3 to K.6): (class, code counts ‖ symbols). libjpeg writes them
# unless it optimizes the tables for the image.
_STANDARD_TABLES = frozenset(
    {
        (_DC, bytes.fromhex("00 01 05 01 01 01 01 01 01 00 00 00 00 00 00 00") + bytes(range(12))),
        (_DC, bytes.fromhex("00 03 01 01 01 01 01 01 01 01 01 00 00 00 00 00") + bytes(range(12))),
        (
            _AC,
            bytes.fromhex(
                "00 02 01 03 03 02 04 03 05 05 04 04 00 00 01 7d"
                "01 02 03 00 04 11 05 12 21 31 41 06 13 51 61 07 22 71 14 32 81 91 a1 08"
                "23 42 b1 c1 15 52 d1 f0 24 33 62 72 82 09 0a 16 17 18 19 1a 25 26 27 28"
                "29 2a 34 35 36 37 38 39 3a 43 44 45 46 47 48 49 4a 53 54 55 56 57 58 59"
                "5a 63 64 65 66 67 68 69 6a 73 74 75 76 77 78 79 7a 83 84 85 86 87 88 89"
                "8a 92 93 94 95 96 97 98 99 9a a2 a3 a4 a5 a6 a7 a8 a9 aa b2 b3 b4 b5 b6"
                "b7 b8 b9 ba c2 c3 c4 c5 c6 c7 c8 c9 ca d2 d3 d4 d5 d6 d7 d8 d9 da e1 e2"
                "e3 e4 e5 e6 e7 e8 e9 ea f1 f2 f3 f4 f5 f6 f7 f8 f9 fa"
            ),
        ),
        (
            _AC,
            bytes.fromhex(
                "00 02 01 02 04 04 03 04 07 05 04 04 00 01 02 77"
                "00 01 02 03 11 04 05 21 31 06 12 41 51 07 61 71 13 22 32 81 08 14 42 91"
                "a1 b1 c1 09 23 33 52 f0 15 62 72 d1 0a 16 24 34 e1 25 f1 17 18 19 1a 26"
                "27 28 29 2a 35 36 37 38 39 3a 43 44 45 46 47 48 49 4a 53 54 55 56 57 58"
                "59 5a 63 64 65 66 67 68 69 6a 73 74 75 76 77 78 79 7a 82 83 84 85 86 87"
                "88 89 8a 92 93 94 95 96 97 98 99 9a a2 a3 a4 a5 a6 a7 a8 a9 aa b2 b3 b4"
                "b5 b6 b7 b8 b9 ba c2 c3 c4 c5 c6 c7 c8 c9 ca d2 d3 d4 d5 d6 d7 d8 d9 da"
                "e2 e3 e4 e5 e6 e7 e8 e9 ea f2 f3 f4 f5 f6 f7 f8 f9 fa"
            ),
        ),
    }
)


@dataclass(frozen=True, slots=True)
class Segment:
    marker: int  # the byte after 0xFF
    payload: bytes  # after the length field

    @property
    def is_metadata(self) -> bool:
        """An APPn or COM segment: EXIF, ICC, XMP, JFIF, Adobe, comments."""
        return self.marker in _APPLICATION or self.marker == _COM

    @property
    def is_picture_index(self) -> bool:
        return self.marker == _APP2 and self.payload.startswith(_MULTI_PICTURE_LABEL)

    def encoded(self) -> bytes:
        length = (_LENGTH_SIZE + len(self.payload)).to_bytes(_LENGTH_SIZE, "big")

        return bytes([_MARKER, self.marker]) + length + self.payload

    def renumbered(self, ids: Mapping[int, int]) -> "Segment":
        """A frame or scan header with its component ids mapped through ``ids``; any other
        segment as it is."""
        if self.marker in _FRAMES:
            positions = _id_positions(first=6, step=3, count=self.payload[5])
        elif self.marker == _SOS:
            positions = _id_positions(first=1, step=2, count=self.payload[0])
        else:
            return self

        payload = bytearray(self.payload)

        for position in positions:
            payload[position] = ids[payload[position]]

        return Segment(self.marker, bytes(payload))


@dataclass(frozen=True, slots=True)
class Header:
    """The segments after SOI, up to and including the first SOS."""

    segments: tuple[Segment, ...]
    size: int  # bytes from SOI to the end of the first SOS, where the scan data starts

    @property
    def is_progressive(self) -> bool:
        return any(segment.marker in _PROGRESSIVE for segment in self.segments)

    @property
    def has_restart_interval(self) -> bool:
        return any(s.marker == _DRI and any(s.payload) for s in self.segments)

    @property
    def has_standard_tables(self) -> bool:
        """Whether every Huffman table is one of the standard ones (none at all counts)."""
        tables = (table for s in self.segments if s.marker == _DHT for table in _tables(s.payload))

        return all(table in _STANDARD_TABLES for table in tables)

    @property
    def component_ids(self) -> bytes:
        frame = next(s.payload for s in self.segments if s.marker in _FRAMES)

        return bytes(frame[position] for position in _id_positions(6, 3, frame[5]))


def read(file: BinaryIO, name: str) -> Header:
    if file.read(_LENGTH_SIZE) != bytes([_MARKER, _SOI]):
        raise _unreadable(name)

    segments = [_segment(file, name)]

    while segments[-1].marker != _SOS:
        segments.append(_segment(file, name))

    return Header(tuple(segments), file.tell())


def matched(written: bytes, cover: Header, name: str, *, keeps_segments: bool = True) -> bytes:
    """``written``, libjpeg's output for the cover's coefficients, with the cover's APPn and
    COM segments in place of its own (libjpeg adds a JFIF segment) unless not
    ``keeps_segments``, and the cover's component ids (libjpeg numbers them from 0). The
    index of the pictures after the main one goes, since libjpeg writes only the main one."""
    header = read(io.BytesIO(written), name)
    ids = dict(zip(header.component_ids, cover.component_ids, strict=True))
    kept = cover if keeps_segments else header
    metadata = tuple(s for s in kept.segments if s.is_metadata and not s.is_picture_index)

    return _rebuilt(written, header, metadata, ids)


def numbered(written: bytes, name: str, extra: tuple[Segment, ...] = ()) -> bytes:
    """``written``, libjpeg's output for coefficients alone, with component ids 1 … n as
    cjpeg numbers them (libjpeg numbers them from 0) and ``extra`` after its JFIF segment."""
    header = read(io.BytesIO(written), name)
    ids = {written_id: number for number, written_id in enumerate(header.component_ids, 1)}
    metadata = (*(s for s in header.segments if s.is_metadata), *extra)

    return _rebuilt(written, header, metadata, ids)


def icc_segments(profile: bytes | None) -> tuple[Segment, ...]:
    """APP2 segments holding ``profile`` in numbered chunks, as the ICC specification
    stores a profile in a JPEG, or none if it is too large to number."""
    if profile is None:
        return ()

    chunks = [profile[i : i + _ICC_CHUNK] for i in range(0, len(profile), _ICC_CHUNK)]

    if len(chunks) > _MAX_ICC_CHUNKS:
        return ()

    return tuple(
        Segment(_APP2, _ICC_LABEL + bytes([number, len(chunks)]) + chunk)
        for number, chunk in enumerate(chunks, 1)
    )


def _rebuilt(
    written: bytes, header: Header, metadata: tuple[Segment, ...], ids: Mapping[int, int]
) -> bytes:
    """``written`` with ``metadata`` in place of its APPn and COM segments and its component
    ids mapped through ``ids``."""
    tables = (s.renumbered(ids) for s in header.segments if not s.is_metadata)
    segments = (*metadata, *tables)

    return bytes([_MARKER, _SOI]) + b"".join(s.encoded() for s in segments) + written[header.size :]


def _segment(file: BinaryIO, name: str) -> Segment:
    marker = _next_marker(file, name)

    while marker in _NO_LENGTH:
        marker = _next_marker(file, name)

    if marker in (_SOI, _EOI):
        raise _unreadable(name)

    length = int.from_bytes(_exactly(file, _LENGTH_SIZE, name), "big")

    if length < _LENGTH_SIZE:
        raise _unreadable(name)

    return Segment(marker, _exactly(file, length - _LENGTH_SIZE, name))


def _next_marker(file: BinaryIO, name: str) -> int:
    """The next marker code, past what libjpeg skips with a warning: stray bytes, fill bytes
    and stuffed zeros."""
    while True:
        while _exactly(file, 1, name)[0] != _MARKER:
            pass

        marker = _exactly(file, 1, name)[0]

        while marker == _MARKER:
            marker = _exactly(file, 1, name)[0]

        if marker != _STUFFED:
            return marker


def _exactly(file: BinaryIO, size: int, name: str) -> bytes:
    data = file.read(size)

    if len(data) < size:
        raise _unreadable(name)

    return data


def _tables(payload: bytes) -> list[tuple[int, bytes]]:
    """A DHT segment's tables: (class, code counts ‖ symbols)."""
    tables = []
    start = 0

    while start < len(payload):
        counts = payload[start + 1 : start + 1 + _COUNTS]
        end = start + 1 + _COUNTS + sum(counts)
        tables.append((payload[start] >> 4, payload[start + 1 : end]))
        start = end

    return tables


def _id_positions(first: int, step: int, count: int) -> range:
    return range(first, first + step * count, step)


def _unreadable(name: str) -> UnsupportedFormatError:
    return UnsupportedFormatError(f"{name} is not a readable JPEG")
