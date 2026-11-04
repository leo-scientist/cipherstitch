"""Exif and GPS sections for a TIFF that libtiff wrote: Pillow's libtiff path cannot write
nested sections, so the first directory is written again with them."""

import io
import struct
from collections.abc import Mapping

from PIL import TiffImagePlugin, TiffTags
from PIL.ExifTags import Base

_HEADER_SIZE = 8
_ENTRY_SIZE = 12
_ENTRY_VALUE = 8  # where an entry's value, or the offset of its values, starts
_INLINE_SIZE = 4
_FORMATS = {TiffTags.SHORT: "H", TiffTags.LONG: "L"}

Sections = Mapping[int, Mapping[int, object]]


def with_sections(data: bytes, sections: Sections) -> bytes:
    """``data``, a single-page TIFF, with ``sections`` (``{IFD.Exif: {…}, …}``) nested in its
    first directory. The new directory follows the strips: libtiff writes its own there, so
    the new one takes its place."""
    written = TiffImagePlugin.ImageFileDirectory_v2(data[:_HEADER_SIZE])
    file = io.BytesIO(data)
    file.seek(written.next)
    written.load(file)

    directory = TiffImagePlugin.ImageFileDirectory_v2(data[:_HEADER_SIZE])

    for tag in written:  # a copy decodes every tag: tobytes writes only decoded ones
        directory[tag] = written[tag]
        directory.tagtype[tag] = written.tagtype[tag]

    for tag, section in sections.items():
        directory[tag] = dict(section)
        directory.tagtype[tag] = TiffTags.LONG

    strips = _values(directory[Base.StripOffsets])
    counts = _values(directory[Base.StripByteCounts])
    end = max(written.offset, *(start + count for start, count in zip(strips, counts, strict=True)))
    position = end + end % 2  # a directory starts on a word boundary
    endian = "<" if data[:2] == b"II" else ">"
    new = _with_strips(bytearray(directory.tobytes(position)), position, strips, endian)

    return (
        data[:4]
        + struct.pack(f"{endian}L", position)
        + data[_HEADER_SIZE:end]
        + bytes(position - end)
        + new
    )


def _values(value: int | tuple[int, ...]) -> tuple[int, ...]:
    return value if isinstance(value, tuple) else (value,)


def _with_strips(
    directory: bytearray, position: int, strips: tuple[int, ...], endian: str
) -> bytes:
    """``directory`` with the strip offsets back: Pillow's writer puts the strips after the
    directory, and moves the offsets there."""
    (count,) = struct.unpack_from(f"{endian}H", directory)

    for index in range(count):
        entry = 2 + index * _ENTRY_SIZE
        tag, kind = struct.unpack_from(f"{endian}HH", directory, entry)

        if tag == Base.StripOffsets:
            packed = struct.pack(f"{endian}{len(strips)}{_FORMATS[kind]}", *strips)

            if len(packed) <= _INLINE_SIZE:
                where = entry + _ENTRY_VALUE
            else:
                (offset,) = struct.unpack_from(f"{endian}L", directory, entry + _ENTRY_VALUE)
                where = offset - position

            directory[where : where + len(packed)] = packed

    return bytes(directory)
