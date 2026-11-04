"""Image files that are not what their names say, or that Pillow cannot decode."""

import struct
import time
import zlib
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PIL import EpsImagePlugin, Image, TiffImagePlugin, TiffTags

from cipherstitch import risks, service
from cipherstitch.catalog import CarrierKind, MethodId
from cipherstitch.errors import CipherStitchError, UnsupportedFormatError
from cipherstitch.media import image as image_media
from cipherstitch.media import precover
from cipherstitch.methods.lsb import ImageChannel

from .conftest import ImageFactory

EPS = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 64 64\n%%EndComments\nshowpage\n"
XMP_TAG = 700


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _with_chunk(path: Path, kind: bytes, data: bytes) -> Path:
    """``path``, a PNG, with a chunk added before its end."""
    raw = path.read_bytes()
    end = raw.index(b"IEND") - 4
    path.write_bytes(raw[:end] + _png_chunk(kind, data) + raw[end:])

    return path


def test_an_eps_named_as_a_png_is_refused_without_running_ghostscript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pillow picks its reader by content, and its EPS reader runs Ghostscript."""

    def ghostscript(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Ghostscript ran")

    monkeypatch.setattr(EpsImagePlugin, "Ghostscript", ghostscript)
    photo = tmp_path / "photo.png"
    photo.write_bytes(EPS)

    for read in (
        lambda: image_media.info(photo),
        lambda: ImageChannel(photo),
        lambda: service.describe_carrier(photo, CarrierKind.IMAGE),
    ):
        with pytest.raises(UnsupportedFormatError, match=r"photo\.png is not a readable image"):
            read()

    with pytest.raises(CipherStitchError):
        service.reveal(photo, tmp_path / "out")


def test_a_jpeg_holding_several_pictures_still_opens(tmp_path: Path) -> None:
    picture = Image.new("RGB", (32, 24), (40, 90, 160))
    picture.save(tmp_path / "pictures.jpg", "MPO", save_all=True, append_images=[picture])

    details = image_media.info(tmp_path / "pictures.jpg")

    assert (details.format, details.has_several_frames) == ("MPO", True)


def test_a_missing_file_is_reported_as_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        image_media.info(tmp_path / "gone.png")


def _broken_chunk_length(path: Path) -> Path:
    raw = bytearray(path.read_bytes())
    start = raw.index(b"IDAT") - 4
    length = struct.unpack(">I", raw[start : start + 4])[0]
    raw[start : start + 4] = struct.pack(">I", length - 100)
    path.write_bytes(bytes(raw))

    return path


def _cut(path: Path) -> Path:
    path.write_bytes(path.read_bytes()[:3000])

    return path


@pytest.mark.parametrize("damage", [_broken_chunk_length, _cut], ids=["broken-chunk", "cut"])
def test_a_damaged_png_is_not_a_readable_image(
    make_image: ImageFactory, damage: Callable[[Path], Path]
) -> None:
    """Pillow raises SyntaxError for a broken chunk, OSError for a cut file."""
    cover = damage(make_image(size=(64, 64)))

    for read in (
        lambda: ImageChannel(cover)._values(),
        lambda: service.capacity(cover),
        lambda: precover.compress(cover),
    ):
        with pytest.raises(UnsupportedFormatError, match="not a readable image"):
            read()


@pytest.mark.filterwarnings("ignore:Metadata Warning:UserWarning")
def test_xmp_stored_as_numbers_is_left_out(tmp_path: Path) -> None:
    """Pillow searched it as text and failed while decoding."""
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[XMP_TAG] = (60, 120, 62)
    tags.tagtype[XMP_TAG] = TiffTags.SHORT
    pixels = np.random.default_rng(seed=0).integers(0, 256, (48, 48, 3), dtype=np.uint8)
    Image.fromarray(pixels).save(tmp_path / "numbers.tif", tiffinfo=tags)

    assert ImageChannel(tmp_path / "numbers.tif")._values().size == 48 * 48 * 3
    assert precover.compress(tmp_path / "numbers.tif").size == (48, 48)


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        (b"eXIf", b"XXXXgarbage"),
        (b"tEXt", b"Raw profile type exif" + bytes(1) + b"\nexif\n  10\nnot hex at all\n"),
    ],
    ids=["broken-exif", "broken-raw-profile"],
)
def test_a_precover_whose_exif_pillow_cannot_parse_is_not_turned(
    make_image: ImageFactory, kind: bytes, data: bytes
) -> None:
    plain = precover.compress(make_image(size=(64, 48)))
    cover = _with_chunk(make_image(name="broken.png", size=(64, 48)), kind, data)
    compressed = precover.compress(cover)

    assert compressed.size == (64, 48)
    assert np.array_equal(compressed.coefficients.ac, plain.coefficients.ac)


def test_a_precover_turned_by_exif_pillow_cannot_write_back_stays_turned(
    make_image: ImageFactory,
) -> None:
    # Make (271) typed RATIONAL, then Orientation (274) 6: Pillow turns the picture, then
    # fails to write the EXIF back without the orientation.
    entries = struct.pack("<HHII", 271, 5, 1, 38) + struct.pack("<HHIHH", 274, 3, 1, 6, 0)
    exif = b"II*\x00" + struct.pack("<IH", 8, 2) + entries + struct.pack("<III", 0, 1, 0)
    cover = _with_chunk(make_image(name="odd.png", size=(64, 48)), b"eXIf", exif)

    assert precover.compress(cover).size == (48, 64)


def test_a_cover_of_several_frames_or_pages_is_told_from_a_still(tmp_path: Path) -> None:
    frames = [Image.new("RGB", (16, 16), (value, 0, 0)) for value in (0, 80, 160)]
    frames[0].save(tmp_path / "animated.png", save_all=True, append_images=frames[1:])
    frames[0].save(tmp_path / "pages.tif", save_all=True, append_images=frames[1:])
    frames[0].save(tmp_path / "still.png")
    frames[0].save(tmp_path / "still.tif")

    assert image_media.info(tmp_path / "animated.png").has_several_frames
    assert image_media.info(tmp_path / "pages.tif").has_several_frames
    assert not image_media.info(tmp_path / "still.png").has_several_frames
    assert not image_media.info(tmp_path / "still.tif").has_several_frames


def test_a_tiff_with_a_long_chain_of_directories_is_described_at_once(tmp_path: Path) -> None:
    """Pillow's ``n_frames`` walks the whole chain (6.5 s for 40 000 directories, on the Hide
    page's UI thread): only the first directory's pointer to a second one is read."""
    Image.new("RGB", (16, 16), (40, 90, 160)).save(tmp_path / "plain.tif")
    data = bytearray((tmp_path / "plain.tif").read_bytes())
    order = "<" if data[:2] == b"II" else ">"
    (first,) = struct.unpack(order + "I", data[4:8])
    (count,) = struct.unpack(order + "H", data[first : first + 2])
    pointer = first + 2 + 12 * count
    directory = bytes(data[first:pointer])
    copies = 20_000
    data[pointer : pointer + 4] = struct.pack(order + "I", len(data))

    for index in range(copies):  # every copy of the first directory points to the next one
        following = len(data) + len(directory) + 4 if index + 1 < copies else 0
        data += directory + struct.pack(order + "I", following)

    chain = tmp_path / "chain.tif"
    chain.write_bytes(data)
    started = time.perf_counter()

    assert image_media.info(chain).has_several_frames
    assert risks.keeps_one_picture(MethodId.LSB, chain)
    assert time.perf_counter() - started < 1
