import functools
import struct
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import ExifTags, Image, ImageCms, PngImagePlugin, TiffImagePlugin, TiffTags

from cipherstitch.errors import CapacityError, UnsupportedFormatError
from cipherstitch.media import image as image_media
from cipherstitch.media import tiff_directory
from cipherstitch.media.image import EmbeddedImage
from cipherstitch.methods import trellis
from cipherstitch.methods.lsb import ImageChannel

from .conftest import ImageFactory

DATA = bytes(range(256)) * 4


def _saved(embedded: EmbeddedImage, path: Path) -> Path:
    embedded.save(path)

    return path


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "L"])
def test_roundtrip(make_image: ImageFactory, tmp_path: Path, mode: str) -> None:
    embedded = ImageChannel(make_image(mode=mode)).write(b"", DATA, None)
    assert ImageChannel(_saved(embedded, tmp_path / "s.png")).read(0, len(DATA), None) == DATA


def test_values_are_each_pixels_red_green_and_blue_row_by_row(tmp_path: Path) -> None:
    """Part of the stored format (docs/format.md#trellis-channels). Alpha is not a value, and
    a grey picture is read as RGB."""
    Image.fromarray(np.arange(24, dtype=np.uint8).reshape(2, 3, 4)).save(tmp_path / "rgba.png")
    Image.fromarray(np.array([[10, 20], [30, 40]], dtype=np.uint8)).save(tmp_path / "grey.png")

    assert ImageChannel(tmp_path / "rgba.png")._values().tolist() == [
        0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13, 14, 16, 17, 18, 20, 21, 22,
    ]  # fmt: skip
    assert ImageChannel(tmp_path / "grey.png")._values().tolist() == [
        10, 10, 10, 20, 20, 20, 30, 30, 30, 40, 40, 40,
    ]  # fmt: skip


def test_palette_image_roundtrip(tmp_path: Path) -> None:
    rng = np.random.default_rng(seed=0)
    image = Image.fromarray(rng.integers(0, 256, (64, 64), dtype=np.uint8), "P")
    image.putpalette(rng.integers(0, 256, 768, dtype=np.uint8).tobytes())
    image.save(tmp_path / "p.png")
    embedded = ImageChannel(tmp_path / "p.png").write(b"", DATA[:500], None)
    assert ImageChannel(_saved(embedded, tmp_path / "s.png")).read(0, 500, None) == DATA[:500]


@pytest.mark.parametrize("seed", [None, b"seed"], ids=["unkeyed", "keyed"])
def test_prefix_then_body(make_image: ImageFactory, tmp_path: Path, seed: bytes | None) -> None:
    prefix, body = DATA[:16], DATA[16:500]
    embedded = ImageChannel(make_image()).write(prefix, body, seed)
    channel = ImageChannel(_saved(embedded, tmp_path / "s.png"))
    assert channel.read(0, len(prefix), None) == prefix
    assert channel.read(len(prefix), len(body), seed) == body

    if seed is not None:
        assert channel.read(len(prefix), len(body), None) != body


def test_changes_only_lsb_and_keeps_alpha(make_image: ImageFactory) -> None:
    cover = make_image(mode="RGBA")
    embedded = ImageChannel(cover).write(b"", DATA, None).image

    with Image.open(cover) as original:
        before = np.asarray(original, dtype=np.int16)

    after = np.asarray(embedded, dtype=np.int16)
    assert embedded.mode == "RGBA"
    assert np.abs(after - before).max() <= 1
    assert np.array_equal(after[..., 3], before[..., 3])


def test_writing_leaves_the_channel_unchanged(make_image: ImageFactory) -> None:
    cover = make_image()
    channel = ImageChannel(cover)
    channel.write(b"", bytes(len(DATA)), None)
    second = np.asarray(channel.write(b"", DATA, None).image, dtype=np.int16)

    with Image.open(cover) as original:
        before = np.asarray(original, dtype=np.int16)

    assert np.abs(second - before).max() <= 1  # a write on top of the first one may move 2


def test_lsb_matching_moves_values_up_or_down_within_range(tmp_path: Path) -> None:
    rng = np.random.default_rng(seed=0)
    pixels = rng.choice(np.array([0, 1, 127, 128, 254, 255], dtype=np.uint8), (96, 96, 3))
    Image.fromarray(pixels).save(tmp_path / "ends.png")
    embedded = ImageChannel(tmp_path / "ends.png").write(b"", DATA, None)
    after = np.asarray(embedded.image).astype(np.int16)
    steps = after - pixels

    assert set(np.unique(steps).tolist()) == {-1, 0, 1}
    assert set(np.unique(after[pixels == 0]).tolist()) == {0, 1}
    assert set(np.unique(after[pixels == 255]).tolist()) == {254, 255}
    assert ImageChannel(_saved(embedded, tmp_path / "s.png")).read(0, len(DATA), None) == DATA


def test_units_are_the_colour_values_pixel_by_pixel(tmp_path: Path) -> None:
    """docs/format.md#trellis-channels: R, G and B of each pixel, row by row; alpha is no
    unit."""
    pixels = np.random.default_rng(seed=0).integers(0, 256, (30, 30, 4), dtype=np.uint8)
    Image.fromarray(pixels, "RGBA").save(tmp_path / "order.png")
    values = pixels[..., :3].reshape(-1)
    expected = trellis.Units(values, trellis.Regions.of(values.size)).lead

    assert ImageChannel(tmp_path / "order.png").read(0, 16, None) == expected


def test_capacity(make_image: ImageFactory) -> None:
    """docs/format.md#trellis-channels: 80% of the stream's units, none of them flat but
    some at an end of the range, then the lead's 16 bytes."""
    cover = make_image(size=(64, 48))
    channel = ImageChannel(cover)
    values = np.asarray(Image.open(cover)).reshape(-1)
    counted = (values > 0) & (values < 255)
    lead = counted[trellis.Regions.of(values.size).lead_indices()]
    stream = np.count_nonzero(counted) - np.count_nonzero(lead)
    assert channel.capacity(16) == stream * 80 // 100 // 8 - 6
    assert channel.capacity(0) == 16 + channel.capacity(16)


def test_a_tiny_image_holds_nothing(tmp_path: Path) -> None:
    """270 values leave the head too few units for its 560 bits."""
    Image.fromarray(np.random.default_rng(seed=0).integers(0, 256, (3, 30, 3), np.uint8)).save(
        tmp_path / "tiny.png"
    )
    channel = ImageChannel(tmp_path / "tiny.png")
    assert channel.read(0, 100, None) == b""
    assert channel.capacity(0) == channel.stored_limit(0) == 0


@pytest.mark.parametrize("seed", [None, b"seed"], ids=["unkeyed", "keyed"])
def test_a_small_payload_leaves_flat_colour_as_it_was(tmp_path: Path, seed: bytes | None) -> None:
    """A third of the image is flat; a payload of 5% of the capacity changes none of it,
    the prefix and the head included."""
    pixels = np.random.default_rng(seed=1).integers(0, 256, (120, 160, 3), dtype=np.uint8)
    pixels[:40] = 240
    Image.fromarray(pixels).save(tmp_path / "cover.png")
    channel = ImageChannel(tmp_path / "cover.png")
    prefix, body = DATA[:16], DATA[: channel.capacity(16) // 20]
    embedded = _saved(channel.write(prefix, body, seed), tmp_path / "s.png")

    with Image.open(embedded) as after:
        changed = np.asarray(after) != pixels

    assert changed.any()
    assert not changed[:40].any()
    assert ImageChannel(embedded).read(0, len(prefix), None) == prefix
    assert ImageChannel(embedded).read(len(prefix), len(body), seed) == body


def test_rejects_body_larger_than_capacity(make_image: ImageFactory) -> None:
    channel = ImageChannel(make_image(size=(8, 8)))

    with pytest.raises(CapacityError):
        channel.write(DATA[:16], bytes(channel.capacity(16) + 1), b"seed")


@functools.cache
def _profile() -> bytes:
    """Made once: a profile records the time it was made."""
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _turned_exif() -> Image.Exif:
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6  # turned a quarter clockwise
    exif[ExifTags.Base.Make] = "Phone"

    return exif


def _cover(make_image: ImageFactory, path: Path, **options: Any) -> Path:
    with Image.open(make_image()) as image:
        image.save(path, **options)

    return path


def _embedded(cover: Path, output: Path) -> Path:
    embedded = _saved(ImageChannel(cover).write(b"", DATA, None), output)
    assert ImageChannel(embedded).read(0, len(DATA), None) == DATA

    return embedded


def test_png_keeps_the_covers_metadata(make_image: ImageFactory, tmp_path: Path) -> None:
    chunks = PngImagePlugin.PngInfo()
    chunks.add_text("Title", "Sky")
    chunks.add_itxt("Description", "Таємниця", lang="uk")
    chunks.add(b"gAMA", (45455).to_bytes(4, "big"))
    chromaticity = (31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)
    chunks.add(b"cHRM", b"".join(value.to_bytes(4, "big") for value in chromaticity))
    options = {"icc_profile": _profile(), "exif": _turned_exif(), "dpi": (300, 300)}
    cover = _cover(make_image, tmp_path / "photo.png", pnginfo=chunks, **options)

    with Image.open(cover) as before, Image.open(_embedded(cover, tmp_path / "s.png")) as after:
        kept = ("icc_profile", "exif", "dpi", "gamma", "chromaticity")
        assert {key: after.info.get(key) for key in kept} == {key: before.info[key] for key in kept}
        assert isinstance(before, PngImagePlugin.PngImageFile)
        assert isinstance(after, PngImagePlugin.PngImageFile)
        assert after.text == before.text == {"Title": "Sky", "Description": "Таємниця"}


def _with_late_chunk(path: Path, kind: bytes, content: bytes) -> Path:
    """``path`` with a chunk after the pixels, just before IEND."""
    data = path.read_bytes()
    end = data.rindex(b"IEND") - 4  # IEND's length field
    chunk = kind + content
    framed = len(content).to_bytes(4, "big") + chunk + zlib.crc32(chunk).to_bytes(4, "big")
    path.write_bytes(data[:end] + framed + data[end:])

    return path


def test_png_keeps_metadata_that_follows_the_pixels(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    cover = _with_late_chunk(make_image(), b"tEXt", b"Comment" + bytes(1) + b"late")
    cover = _with_late_chunk(cover, b"eXIf", _turned_exif().tobytes())

    with Image.open(_embedded(cover, tmp_path / "s.png")) as after:
        assert isinstance(after, PngImagePlugin.PngImageFile)
        assert after.text == {"Comment": "late"}
        assert after.getexif()[ExifTags.Base.Orientation] == 6


def test_png_keeps_the_srgb_intent(make_image: ImageFactory, tmp_path: Path) -> None:
    chunks = PngImagePlugin.PngInfo()
    chunks.add(b"sRGB", bytes([1]))
    cover = _cover(make_image, tmp_path / "photo.png", pnginfo=chunks)

    with Image.open(_embedded(cover, tmp_path / "s.png")) as after:
        assert after.info["srgb"] == 1


def test_a_turned_jpeg_stays_turned_as_png(make_image: ImageFactory, tmp_path: Path) -> None:
    cover = _cover(make_image, tmp_path / "phone.jpg", exif=_turned_exif(), icc_profile=_profile())

    with Image.open(_embedded(cover, tmp_path / "s.png")) as after:
        assert after.getexif()[ExifTags.Base.Orientation] == 6
        assert after.info["icc_profile"] == _profile()


_TURNED_XMP = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description xmlns:tiff="http://ns.adobe.com/tiff/1.0/" tiff:Orientation="6"/>'
    b"</rdf:RDF></x:xmpmeta>"
)


@pytest.mark.parametrize("name", ["photo.jpg", "photo.webp"])
def test_png_keeps_the_xmp_of_another_cover(
    make_image: ImageFactory, tmp_path: Path, name: str
) -> None:
    cover = _cover(make_image, tmp_path / name, xmp=_TURNED_XMP)

    with Image.open(cover) as before, Image.open(_embedded(cover, tmp_path / "s.png")) as after:
        assert after.info["xmp"] == before.info["xmp"]


def test_xmp_that_is_not_utf8_is_left_out_of_png(make_image: ImageFactory, tmp_path: Path) -> None:
    """PNG keeps XMP in an iTXt chunk, which holds UTF-8 only."""
    cover = _cover(make_image, tmp_path / "photo.jpg", xmp=_TURNED_XMP + bytes([0xFF]))

    with Image.open(_embedded(cover, tmp_path / "s.png")) as after:
        assert "xmp" not in after.info


def test_tiff_output_takes_exif_without_an_interoperability_section(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    exif = _turned_exif()
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.ISOSpeedRatings] = 100
    cover = _cover(make_image, tmp_path / "photo.jpg", exif=exif)

    with Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        assert after.getexif().get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.ISOSpeedRatings] == 100


def _directory(*entries: tuple[int, int, bytes]) -> bytes:
    """A little-endian EXIF section of ``(tag, type, 4-byte value)`` entries."""
    packed = b"".join(struct.pack("<HHI", tag, kind, 1) + value for tag, kind, value in entries)

    return struct.pack("<H", len(entries)) + packed + bytes(4)


_FIRST_SECTION_END = 26  # where a second section starts after a one-entry first one


@pytest.mark.parametrize(
    "sections",
    [
        [(ExifTags.IFD.Exif, TiffTags.SIGNED_LONG, struct.pack("<i", -100))],
        [(ExifTags.IFD.GPSInfo, TiffTags.SIGNED_LONG, struct.pack("<i", -100))],
        [
            (ExifTags.IFD.Exif, TiffTags.LONG, struct.pack("<I", _FIRST_SECTION_END)),
            (ExifTags.IFD.Interop, TiffTags.LONG, struct.pack("<I", 9999)),
        ],
        [
            (ExifTags.IFD.Exif, TiffTags.LONG, struct.pack("<I", _FIRST_SECTION_END)),
            (ExifTags.IFD.Interop, TiffTags.ASCII, b"abc" + bytes(1)),
        ],
    ],
    ids=["negative-exif-offset", "negative-gps-offset", "interop-past-the-end", "text-interop"],
)
@pytest.mark.filterwarnings("ignore:Corrupt EXIF data:UserWarning")
def test_tiff_output_survives_exif_offsets_it_cannot_follow(
    make_image: ImageFactory, tmp_path: Path, sections: list[tuple[int, int, bytes]]
) -> None:
    exif = b"Exif" + bytes(2) + b"II*" + bytes(1) + struct.pack("<I", 8)
    exif += b"".join(_directory(entry) for entry in sections)
    cover = _cover(make_image, tmp_path / "photo.jpg", exif=exif)

    with Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        assert ExifTags.IFD.Interop not in after.getexif().get_ifd(ExifTags.IFD.Exif)


_TWO_ENTRIES_END = 38  # where data starts after a section of two entries


@pytest.mark.parametrize(
    ("entry", "data"),
    [
        (
            (ExifTags.Base.Make, TiffTags.RATIONAL, struct.pack("<I", _TWO_ENTRIES_END)),
            struct.pack("<II", 3, 4),
        ),
        ((ExifTags.Base.ImageDescription, TiffTags.FLOAT, struct.pack("<f", 1.5)), b""),
    ],
    ids=["rational-make", "float-description"],
)
def test_tiff_output_leaves_out_tags_pillow_cannot_write_back(
    make_image: ImageFactory, tmp_path: Path, entry: tuple[int, int, bytes], data: bytes
) -> None:
    """Pillow types a descriptive tag by its own table: a make stored as a fraction failed
    the whole save."""
    model = (ExifTags.Base.Model, TiffTags.ASCII, b"C" + bytes(3))
    exif = b"Exif" + bytes(2) + b"II*" + bytes(1) + struct.pack("<I", 8)
    exif += _directory(entry, model) + data
    cover = _cover(make_image, tmp_path / "photo.png", exif=exif)

    with Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        assert after.getexif()[ExifTags.Base.Model] == "C"
        assert entry[0] not in after.getexif()


def test_xmp_in_the_exif_of_another_cover_does_not_turn_tiff_output(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    exif = _turned_exif()
    exif[ExifTags.Base.XMLPacket] = _TURNED_XMP
    cover = _cover(make_image, tmp_path / "photo.jpg", exif=exif)

    with Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        assert after.size == (96, 72)
        assert "xmp" not in after.info


@pytest.mark.parametrize("suffix", [".tif", ".png"])
def test_a_tiff_turned_by_its_xmp_is_not_turned_twice(
    make_image: ImageFactory, tmp_path: Path, suffix: str
) -> None:
    """Pillow turns a TIFF by the XMP's orientation while loading it, and takes it out of the
    XMP. Kept, it would turn the output again: :func:`_embedded` reads the hidden bits back."""
    tags = {ExifTags.Base.XMLPacket: _TURNED_XMP}
    cover = _cover(make_image, tmp_path / "scan.tif", tiffinfo=tags)

    with Image.open(_embedded(cover, tmp_path / f"s{suffix}")) as after:
        assert after.info["xmp"] == _TURNED_XMP.replace(b'tiff:Orientation="6"', b"")


@pytest.mark.parametrize("suffix", [".tif", ".png"])
def test_an_orientation_pillow_cannot_take_out_leaves_the_xmp_out(
    make_image: ImageFactory, tmp_path: Path, suffix: str
) -> None:
    """Pillow reads this orientation, but its pattern for taking it out does not match."""
    element = b"><tiff:Orientation>6 </tiff:Orientation></rdf:Description>"
    tags = {ExifTags.Base.XMLPacket: _TURNED_XMP.replace(b'tiff:Orientation="6"/>', element)}
    cover = _cover(make_image, tmp_path / "scan.tif", tiffinfo=tags)

    with Image.open(_embedded(cover, tmp_path / f"s{suffix}")) as after:
        assert "xmp" not in after.info


_XMP = _TURNED_XMP.replace(b' tiff:Orientation="6"', b"")


@pytest.mark.parametrize("compression", ["raw", "tiff_lzw"])
@pytest.mark.parametrize("suffix", [".tif", ".png"])
def test_a_tiff_with_xmp_stored_as_text_is_read(
    make_image: ImageFactory, tmp_path: Path, compression: str, suffix: str
) -> None:
    """Pillow fails decoding it. TIFF output stores the XMP as the spec types it."""
    tags = {ExifTags.Base.XMLPacket: _XMP}
    cover = _cover(make_image, tmp_path / "scan.tif", compression=compression, tiffinfo=tags)
    _retyped(cover, ExifTags.Base.XMLPacket, TiffTags.ASCII)

    with Image.open(_embedded(cover, tmp_path / f"s{suffix}")) as after:
        assert after.info["xmp"] == _XMP

        if isinstance(after, TiffImagePlugin.TiffImageFile):
            assert after.tag_v2.tagtype[ExifTags.Base.XMLPacket] == TiffTags.BYTE
            assert after.info["compression"] == compression


def test_tiff_output_keeps_only_the_descriptive_exif(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    """Pillow would write the other tags over the TIFF's own layout, and turn the TIFF by its
    Orientation while loading it: :func:`_embedded` reads the hidden bits back."""
    exif = _turned_exif()
    exif[ExifTags.Base.ImageWidth] = 4032  # the width before the photo was cropped
    exif[ExifTags.Base.PhotometricInterpretation] = 6  # YCbCr
    details = exif.get_ifd(ExifTags.IFD.Exif)
    details[ExifTags.Base.ISOSpeedRatings] = 200
    details[ExifTags.Base.MakerNote] = b"offsets into the cover's block"
    details[ExifTags.IFD.Interop] = {ExifTags.Interop.InteropIndex: "R98"}
    cover = _cover(make_image, tmp_path / "phone.jpg", exif=exif, dpi=(240, 240))

    with Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        tags = after.getexif()
        assert after.size == (96, 72)
        assert ExifTags.Base.Orientation not in tags
        assert tags[ExifTags.Base.Make] == "Phone"
        assert tags.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.ISOSpeedRatings] == 200
        assert ExifTags.Base.MakerNote not in tags.get_ifd(ExifTags.IFD.Exif)
        assert tags.get_ifd(ExifTags.IFD.Interop) == {ExifTags.Interop.InteropIndex: "R98"}
        assert after.info["dpi"] == (240, 240)


def test_tiff_output_leaves_out_exif_it_cannot_parse(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    cover = _cover(make_image, tmp_path / "photo.png", exif=b"Exif" + bytes(2) + b"II*" + bytes(1))

    with Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        assert ExifTags.Base.Make not in after.getexif()


def test_a_resolution_the_writers_cannot_store_is_left_out(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    """Pillow reads a resolution of 0/0 as NaN."""
    unknown = TiffImagePlugin.IFDRational(0, 0)
    resolution = {ExifTags.Base.XResolution: unknown, ExifTags.Base.YResolution: unknown}
    cover = _cover(make_image, tmp_path / "scan.tif", tiffinfo=resolution)

    with Image.open(_embedded(cover, tmp_path / "s.png")) as after:
        assert "dpi" not in after.info


def test_only_an_rgb_profile_is_kept(tmp_path: Path) -> None:
    """The output is always RGB, which a profile for grey or CMYK does not describe."""
    profile = _profile()
    grey_profile = profile[:16] + b"GRAY" + profile[20:]
    grey = np.random.default_rng(seed=0).integers(0, 256, (72, 96), dtype=np.uint8)
    Image.fromarray(grey).save(tmp_path / "grey.png", icc_profile=grey_profile)

    with Image.open(_embedded(tmp_path / "grey.png", tmp_path / "s.png")) as after:
        assert "icc_profile" not in after.info


def test_tiff_keeps_the_covers_tags(make_image: ImageFactory, tmp_path: Path) -> None:
    tags = {"software": "Scanner", "artist": "Someone", "description": "A page"}
    options = {"icc_profile": _profile(), "dpi": (150, 150), "compression": "tiff_lzw"}
    cover = _cover(make_image, tmp_path / "scan.tif", **tags, **options)

    with Image.open(cover) as before, Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        named = (ExifTags.Base.Software, ExifTags.Base.Artist, ExifTags.Base.ImageDescription)
        assert {tag: after.getexif()[tag] for tag in named} == {
            tag: before.getexif()[tag] for tag in named
        }
        assert after.info["icc_profile"] == _profile()
        assert after.info["dpi"] == (150, 150)


def _smooth_tiff(path: Path, compression: str, predictor: int | None = None) -> Path:
    rows, columns = np.mgrid[0:120, 0:160]
    pixels = np.stack([rows, columns, (rows + columns) // 2], axis=-1).astype(np.uint8)
    tags = {} if predictor is None else {ExifTags.Base.Predictor: predictor}
    Image.fromarray(pixels).save(path, compression=compression, tiffinfo=tags)

    return path


@pytest.mark.parametrize(
    ("compression", "predictor"),
    [
        ("tiff_lzw", 2),
        ("tiff_adobe_deflate", 2),
        ("tiff_lzw", 1),
        ("tiff_lzw", None),
        ("packbits", None),
    ],
)
def test_tiff_keeps_the_covers_compression(
    tmp_path: Path, compression: str, predictor: int | None
) -> None:
    """Written uncompressed, this 1.6 KiB LZW cover with a predictor would take 56 KiB."""
    cover = _smooth_tiff(tmp_path / "scan.tif", compression, predictor)

    with Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        assert after.info["compression"] == compression
        assert after.getexif().get(ExifTags.Base.Predictor) == predictor


@pytest.mark.parametrize("compression", ["raw", "tiff_lzw"])
def test_tiff_keeps_the_covers_exif_and_gps_sections(
    make_image: ImageFactory, tmp_path: Path, compression: str
) -> None:
    """Pillow's libtiff path cannot write them, so a compressed TIFF gets them afterwards.
    The maker's notes are left out, as their offsets would no longer match. Pillow cannot read
    the interoperability section of a decoded TIFF, and the rest is kept without it."""
    details: dict[int, object] = {
        ExifTags.Base.ISOSpeedRatings: 200,
        ExifTags.Base.MakerNote: b"offsets",
        ExifTags.IFD.Interop: {ExifTags.Interop.InteropIndex: "R98"},
    }
    position: dict[int, object] = {ExifTags.GPS.GPSLatitudeRef: "N"}
    cover = _cover(make_image, tmp_path / "photo.tif", compression=compression)
    sections: tiff_directory.Sections = {ExifTags.IFD.Exif: details, ExifTags.IFD.GPSInfo: position}
    cover.write_bytes(tiff_directory.with_sections(cover.read_bytes(), sections))

    with Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        tags = after.getexif()
        assert tags.get_ifd(ExifTags.IFD.Exif) == {ExifTags.Base.ISOSpeedRatings: 200}
        assert tags.get_ifd(ExifTags.IFD.GPSInfo) == position
        assert after.info["compression"] == compression


_TAG_FIELD, _TYPE_FIELD = 0, 2  # in a directory entry


@pytest.mark.parametrize("compression", ["raw", "tiff_lzw"])
def test_png_takes_the_exif_of_a_tiff_cover(
    make_image: ImageFactory, tmp_path: Path, compression: str
) -> None:
    """XMP has a chunk of its own in PNG, Photoshop data is no EXIF, and the orientation is
    left out: Pillow turned the cover by it while loading it."""
    tags = {
        ExifTags.Base.Make: "Scanner",
        ExifTags.Base.Artist: "Someone",
        ExifTags.Base.Orientation: 6,
        ExifTags.Base.XMLPacket: b"<x:xmpmeta/>",
        ExifTags.Base.ImageResources: b"8BIM",
    }
    cover = _cover(make_image, tmp_path / "scan.tif", compression=compression, tiffinfo=tags)
    details: dict[int, object] = {ExifTags.Base.ISOSpeedRatings: 200}
    position: dict[int, object] = {ExifTags.GPS.GPSLatitudeRef: "N"}
    sections: tiff_directory.Sections = {ExifTags.IFD.Exif: details, ExifTags.IFD.GPSInfo: position}
    cover.write_bytes(tiff_directory.with_sections(cover.read_bytes(), sections))

    with Image.open(_embedded(cover, tmp_path / "s.png")) as after:
        exif = after.getexif()
        assert {tag: exif[tag] for tag in exif} == {
            ExifTags.Base.Make: "Scanner",
            ExifTags.Base.Artist: "Someone",
            ExifTags.IFD.Exif: exif[ExifTags.IFD.Exif],
            ExifTags.IFD.GPSInfo: exif[ExifTags.IFD.GPSInfo],
        }
        assert exif.get_ifd(ExifTags.IFD.Exif) == details
        assert exif.get_ifd(ExifTags.IFD.GPSInfo) == position
        assert after.size == (72, 96)


def test_png_takes_no_exif_from_a_tiff_cover_it_cannot_write_back(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    cover = _cover(make_image, tmp_path / "scan.tif", dpi=(300, 300))
    data = bytearray(cover.read_bytes())
    _patch_entry(data, None, ExifTags.Base.XResolution, _TAG_FIELD, ExifTags.Base.Make)
    cover.write_bytes(data)

    with Image.open(_embedded(cover, tmp_path / "s.png")) as after:
        assert "exif" not in after.info


def test_png_takes_no_exif_from_a_tiff_cover_without_any(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    cover = _cover(make_image, tmp_path / "scan.tif")

    with Image.open(_embedded(cover, tmp_path / "s.png")) as after:
        assert "exif" not in after.info


def _patch_entry(
    data: bytearray, pointer: int | None, tag: int, field: int, value: int, start: int = 0
) -> None:
    """Set ``field`` of ``tag``'s entry, in the section at ``pointer`` (the first directory if
    ``None``) of the TIFF at ``start`` of ``data``, to ``value``, against the spec, as some
    writers do."""
    endian = "<" if data[start : start + 2] == b"II" else ">"

    def read(format: str, at: int) -> int:
        return int(struct.unpack_from(endian + format, data, start + at)[0])

    def entry(directory: int, wanted: int) -> int:
        entries = (directory + 2 + 12 * index for index in range(read("H", directory)))

        return next(at for at in entries if read("H", at) == wanted)

    first = read("I", 4)
    section = first if pointer is None else read("I", entry(first, pointer) + 8)
    struct.pack_into(endian + "H", data, start + entry(section, tag) + field, value)


_UNWRITABLE = [
    # (the GPS section written, the tag patched, the field, its value)
    ({ExifTags.GPS.GPSAltitudeRef: 0}, ExifTags.GPS.GPSAltitudeRef, _TYPE_FIELD, TiffTags.ASCII),
    (
        {ExifTags.GPS.GPSAltitude: TiffImagePlugin.IFDRational(100)},
        ExifTags.GPS.GPSAltitude,
        _TAG_FIELD,
        ExifTags.GPS.GPSLatitudeRef,  # a text tag that now holds a fraction
    ),
]


@pytest.mark.parametrize(("gps", "tag", "field", "value"), _UNWRITABLE, ids=["text", "fraction"])
@pytest.mark.parametrize("compression", ["raw", "tiff_lzw"])
def test_a_section_pillow_cannot_write_back_is_left_out(
    make_image: ImageFactory,
    tmp_path: Path,
    compression: str,
    gps: dict[int, object],
    tag: int,
    field: int,
    value: int,
) -> None:
    """Pillow writes a tag with the type its table gives, not the one it read."""
    cover = _cover(make_image, tmp_path / "photo.tif", compression=compression)
    sections: tiff_directory.Sections = {ExifTags.IFD.GPSInfo: gps}
    data = bytearray(tiff_directory.with_sections(cover.read_bytes(), sections))
    _patch_entry(data, ExifTags.IFD.GPSInfo, tag, field, value)
    cover.write_bytes(data)

    with Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        assert after.getexif().get_ifd(ExifTags.IFD.GPSInfo) == {}


def test_tiff_output_keeps_the_tags_of_exif_whose_sections_cannot_be_written(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    exif = _turned_exif()
    exif.get_ifd(ExifTags.IFD.GPSInfo)[ExifTags.GPS.GPSAltitudeRef] = 0
    data = bytearray(exif.tobytes())
    tiff_start = len(b"Exif") + 2
    _patch_entry(
        data,
        ExifTags.IFD.GPSInfo,
        ExifTags.GPS.GPSAltitudeRef,
        _TYPE_FIELD,
        TiffTags.ASCII,
        tiff_start,
    )
    cover = _cover(make_image, tmp_path / "photo.jpg", exif=bytes(data))

    with Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        assert after.getexif()[ExifTags.Base.Make] == "Phone"
        assert after.getexif().get_ifd(ExifTags.IFD.GPSInfo) == {}


def test_a_floating_point_predictor_is_left_out(tmp_path: Path) -> None:
    """It suits floating-point samples only, and the output has 8-bit ones."""
    samples = np.random.default_rng(seed=0).uniform(0, 255, (120, 160)).astype(np.float32)
    cover = tmp_path / "scan.tif"
    predictor = {ExifTags.Base.Predictor: 3}
    Image.fromarray(samples).save(cover, compression="tiff_lzw", tiffinfo=predictor)

    with Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        assert after.info["compression"] == "tiff_lzw"
        assert ExifTags.Base.Predictor not in after.getexif()


@pytest.mark.parametrize("tag_type", [TiffTags.BYTE, TiffTags.UNDEFINED])
def test_xmp_and_photoshop_data_keep_the_tiff_compressed(
    make_image: ImageFactory, tmp_path: Path, tag_type: int
) -> None:
    """Photoshop and Lightroom write both into nearly every TIFF."""
    data = {ExifTags.Base.XMLPacket: b"<x:xmpmeta/>", ExifTags.Base.ImageResources: b"8BIM"}
    cover = _cover(make_image, tmp_path / "photo.tif", compression="tiff_lzw", tiffinfo=data)

    for tag in data:
        _retyped(cover, tag, tag_type)

    with Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        assert after.info["compression"] == "tiff_lzw"
        assert {tag: after.getexif()[tag] for tag in data} == data


def _retyped(path: Path, tag: int, tag_type: int) -> Path:
    """``path`` with ``tag`` stored as ``tag_type``, against the spec, as some writers do."""
    data = bytearray(path.read_bytes())
    (directory,) = struct.unpack_from("<I", data, 4)
    (count,) = struct.unpack_from("<H", data, directory)
    entries = (directory + 2 + 12 * index for index in range(count))
    entry = next(at for at in entries if struct.unpack_from("<H", data, at)[0] == tag)
    struct.pack_into("<H", data, entry + 2, tag_type)
    path.write_bytes(data)

    return path


@pytest.mark.parametrize(
    ("tag", "value", "tag_type"),
    [
        (ExifTags.Base.ImageDescription, "A page", TiffTags.UNDEFINED),
        (ExifTags.Base.Copyright, "Someone", TiffTags.BYTE),
        (ExifTags.Base.ImageResources, b"8BIM", TiffTags.ASCII),
    ],
)
def test_a_tag_libtiff_cannot_take_keeps_the_tiff_uncompressed(
    make_image: ImageFactory, tmp_path: Path, tag: int, value: str | bytes, tag_type: int
) -> None:
    """libtiff, which writes compressed TIFFs, would crash the process on such a tag."""
    cover = _retyped(
        _cover(make_image, tmp_path / "scan.tif", compression="tiff_lzw", tiffinfo={tag: value}),
        tag,
        tag_type,
    )

    with Image.open(cover) as before, Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        assert after.info["compression"] == "raw"
        assert after.getexif()[tag] == before.getexif()[tag]


def test_a_lossy_tiff_is_written_uncompressed(tmp_path: Path) -> None:
    cover = _smooth_tiff(tmp_path / "scan.tif", "tiff_jpeg")

    with Image.open(_embedded(cover, tmp_path / "s.tif")) as after:
        assert after.info["compression"] == "raw"


def test_bmp_keeps_the_resolution(make_image: ImageFactory, tmp_path: Path) -> None:
    cover = _cover(make_image, tmp_path / "photo.png", dpi=(300, 300))

    with Image.open(_embedded(cover, tmp_path / "s.bmp")) as after:
        assert after.info["dpi"] == pytest.approx((300, 300), abs=0.01)


def test_sixteen_bit_grey_is_rounded_to_eight_bits(tmp_path: Path) -> None:
    """Pillow's convert would clip it: nearly every pixel would turn white."""
    rng = np.random.default_rng(seed=0)
    deep = rng.integers(0, 4096, (72, 96), dtype=np.uint16)
    Image.fromarray(deep).save(tmp_path / "deep.png")
    eight = (2 * deep.astype(np.int32) + 257) // 514
    channel = ImageChannel(tmp_path / "deep.png")

    assert np.array_equal(channel._values().reshape(72, 96, 3)[..., 0], eight)

    embedded = channel.write(b"", DATA[:500], None)
    assert ImageChannel(_saved(embedded, tmp_path / "s.png")).read(0, 500, None) == DATA[:500]


def test_a_transparent_sixteen_bit_grey_level_stays_transparent(tmp_path: Path) -> None:
    deep = np.full((8, 8), 1000, dtype=np.uint16)
    deep[0, 0] = 7
    Image.fromarray(deep).save(tmp_path / "deep.png", transparency=7)
    pixels = ImageChannel(tmp_path / "deep.png")._pixels

    assert pixels.shape == (8, 8, 4)
    assert pixels[0, 0, 3] == 0
    assert np.all(pixels.reshape(-1, 4)[1:, 3] == 255)


@pytest.mark.parametrize("value", [-1, 256])
@pytest.mark.parametrize("dtype", ["int32", "float32"])
def test_values_beyond_eight_bits_are_refused(tmp_path: Path, dtype: str, value: int) -> None:
    """``convert`` would clip them. 32-bit values within 0 … 255 are kept (see the
    floating-point predictor's test)."""
    samples = np.full((8, 8), 100, dtype=dtype)
    samples[0, 0] = value
    Image.fromarray(samples).save(tmp_path / "wide.tif")

    with pytest.raises(UnsupportedFormatError, match="deep values"):
        ImageChannel(tmp_path / "wide.tif")._values()


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
@pytest.mark.parametrize("side", [10_000, 15_000], ids=["above-the-limit", "a-bomb-to-pillow"])
def test_a_picture_too_large_to_decode_is_refused(tmp_path: Path, side: int) -> None:
    """Only the header is read: such a PNG of a few bytes would take 300 MB or more."""
    header = struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0)
    (tmp_path / "huge.png").write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(b""))
        + _png_chunk(b"IEND", b"")
    )

    with pytest.raises(UnsupportedFormatError, match="too large to read"):
        ImageChannel(tmp_path / "huge.png")

    with pytest.raises(UnsupportedFormatError, match="too large to read"):
        image_media.info(tmp_path / "huge.png")


def test_a_text_chunk_pillow_refuses_is_reported(tmp_path: Path) -> None:
    chunks = PngImagePlugin.PngInfo()
    chunks.add_text("Comment", "x" * (PngImagePlugin.MAX_TEXT_CHUNK + 1), zip=True)
    Image.new("RGB", (8, 8)).save(tmp_path / "text.png", pnginfo=chunks)

    with pytest.raises(UnsupportedFormatError, match="not a readable image"):
        ImageChannel(tmp_path / "text.png")._values()
