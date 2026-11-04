import io
from typing import Any

import numpy as np
import pytest
from PIL import Image

from cipherstitch.errors import UnsupportedFormatError
from cipherstitch.media import jpeg_header
from cipherstitch.media.jpeg_header import Header, Segment

_SOI = bytes([0xFF, 0xD8])
_COM = 0xFE
_DRI = 0xDD


def _jpeg(mode: str = "RGB", **options: Any) -> bytes:
    pixels = np.random.default_rng(seed=0).integers(0, 256, (32, 48, 3), dtype=np.uint8)
    output = io.BytesIO()
    Image.fromarray(pixels).convert(mode).save(output, "JPEG", quality=90, **options)

    return output.getvalue()


def _header(data: bytes) -> Header:
    return jpeg_header.read(io.BytesIO(data), "cover.jpg")


@pytest.mark.parametrize(
    ("mode", "options", "expected"),
    [
        ("RGB", {}, True),
        ("L", {}, True),
        ("RGB", {"subsampling": 0}, True),
        ("RGB", {"optimize": True}, False),
        ("RGB", {"progressive": True}, False),
    ],
    ids=["colour", "grey", "4:4:4", "optimized", "progressive"],
)
def test_libjpeg_default_tables_are_the_standard_ones(
    mode: str, options: dict[str, Any], expected: bool
) -> None:
    assert _header(_jpeg(mode, **options)).has_standard_tables is expected


def test_a_cover_without_tables_uses_the_standard_ones() -> None:
    """A Motion-JPEG frame leaves the tables out and means the standard ones."""
    cover = _header(_jpeg())
    segments = tuple(s for s in cover.segments if s.marker != jpeg_header._DHT)

    assert Header(segments, cover.size).has_standard_tables


@pytest.mark.parametrize(
    ("options", "is_progressive", "has_restart_interval"),
    [
        ({}, False, False),
        ({"progressive": True}, True, False),
        ({"restart_marker_blocks": 4}, False, True),
        ({"progressive": True, "restart_marker_rows": 1}, True, True),
    ],
    ids=["baseline", "progressive", "restart", "both"],
)
def test_layouts_jpeglib_cannot_write(
    options: dict[str, Any], is_progressive: bool, has_restart_interval: bool
) -> None:
    cover = _header(_jpeg(**options))

    assert cover.is_progressive is is_progressive
    assert cover.has_restart_interval is has_restart_interval


def test_a_zero_restart_interval_is_none() -> None:
    cover = _header(_jpeg())
    off = Header((Segment(_DRI, bytes(2)), *cover.segments), cover.size)

    assert not off.has_restart_interval


def test_the_header_ends_with_the_first_scan() -> None:
    data = _jpeg()
    cover = _header(data)

    assert cover.segments[-1].marker == jpeg_header._SOS
    assert data[: cover.size].endswith(cover.segments[-1].encoded())
    assert cover.component_ids == bytes([1, 2, 3])


@pytest.mark.parametrize(
    "skipped",
    [bytes([0xFF]), bytes([0x00, 0x00]), bytes([0xFF, 0x00]), bytes([0xFF, 0xD0])],
    ids=["fill-byte", "stray-bytes", "stuffed-zero", "restart-marker"],
)
def test_what_libjpeg_skips_before_a_marker_is_skipped(skipped: bytes) -> None:
    data = _jpeg()
    padded = _SOI + skipped + data[len(_SOI) :]

    assert _header(padded).segments == _header(data).segments


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"GIF89a",
        _SOI,
        _SOI + bytes([0xFF, _COM, 0x00]),
        _SOI + bytes([0xFF, _COM, 0x00, 0x01]),
        _SOI + bytes([0xFF, _COM, 0x00, 0x05, 0x41]),
        _SOI + bytes([0x00, _COM, 0x00, 0x02]),
        _SOI + bytes([0xFF, 0xD8]),
        _SOI + bytes([0xFF, 0xD9]),
    ],
    ids=[
        "empty",
        "not-jpeg",
        "soi-only",
        "short-length",
        "length-below-two",
        "short-payload",
        "stray-bytes-to-the-end",
        "second-start",
        "end-before-scan",
    ],
)
def test_broken_headers_are_rejected(data: bytes) -> None:
    with pytest.raises(UnsupportedFormatError, match=r"cover\.jpg is not a readable JPEG"):
        _header(data)


def test_matched_takes_the_covers_metadata_and_component_ids() -> None:
    cover = _header(_jpeg(comment=b"cover", dpi=(300, 300)))
    written = bytearray(_jpeg(comment=b"written"))
    frame = written.index(bytes([0xFF, 0xC0]))
    written[frame + 10 : frame + 19 : 3] = bytes([0, 1, 2])  # libjpeg numbers them from 0
    scan = written.index(bytes([0xFF, jpeg_header._SOS]))
    written[scan + 5 : scan + 11 : 2] = bytes([0, 1, 2])

    result = jpeg_header.matched(bytes(written), cover, "out.jpg")
    header = _header(result)

    assert [s for s in header.segments if s.is_metadata] == [
        s for s in cover.segments if s.is_metadata
    ]
    assert header.component_ids == bytes([1, 2, 3])
    assert header.segments[-1].payload[1:7:2] == bytes([1, 2, 3])
    assert result[header.size :] == bytes(written[_header(bytes(written)).size :])


def test_numbered_counts_libjpegs_component_ids_from_one() -> None:
    written = bytearray(_jpeg())
    frame = written.index(bytes([0xFF, 0xC0]))
    written[frame + 10 : frame + 19 : 3] = bytes([0, 1, 2])  # libjpeg numbers them from 0
    scan = written.index(bytes([0xFF, jpeg_header._SOS]))
    written[scan + 5 : scan + 11 : 2] = bytes([0, 1, 2])
    extra = (Segment(_COM, b"after the JFIF segment"),)

    result = jpeg_header.numbered(bytes(written), "out.jpg", extra)
    header = _header(result)

    assert [s.payload[:4] for s in header.segments if s.is_metadata] == [b"JFIF", b"afte"]
    assert header.component_ids == bytes([1, 2, 3])
    assert header.segments[-1].payload[1:7:2] == bytes([1, 2, 3])
    assert result[header.size :] == bytes(written[_header(bytes(written)).size :])


def test_a_long_profile_is_split_into_numbered_segments() -> None:
    profile = bytes(range(256)) * 280
    segments = jpeg_header.icc_segments(profile)
    label = b"ICC_PROFILE" + bytes([0])

    assert [s.payload[: len(label) + 2] for s in segments] == [
        label + bytes([1, 2]),
        label + bytes([2, 2]),
    ]
    assert b"".join(s.payload[len(label) + 2 :] for s in segments) == profile
    assert all(len(s.encoded()) <= 65_537 for s in segments)  # marker and a 16-bit length
    assert jpeg_header.icc_segments(None) == ()


def test_a_profile_too_large_to_number_is_left_out() -> None:
    """Each counter is one byte: 255 chunks of a profile at most."""
    chunk = 65_519

    assert len(jpeg_header.icc_segments(bytes(255 * chunk))) == 255
    assert jpeg_header.icc_segments(bytes(255 * chunk + 1)) == ()


def test_matched_leaves_out_the_index_of_the_pictures_after_the_main_one() -> None:
    """libjpeg writes only the main picture of an MPO: its index would point past the end."""
    pictures = io.BytesIO()
    image = Image.fromarray(np.full((32, 48, 3), 90, dtype=np.uint8))
    image.save(pictures, "MPO", save_all=True, append_images=[image.resize((24, 16))])
    cover = _header(pictures.getvalue())
    index = [s for s in cover.segments if s.payload.startswith(b"MPF" + bytes([0]))]

    result = _header(jpeg_header.matched(_jpeg(), cover, "out.jpg"))

    assert len(index) == 1
    assert [s for s in result.segments if s.is_metadata] == [
        s for s in cover.segments if s.is_metadata and s not in index
    ]
