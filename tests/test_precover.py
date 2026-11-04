import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image, ImageCms, TiffImagePlugin, TiffTags

from cipherstitch import risks, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.errors import NotAvailableError, UnsupportedFormatError
from cipherstitch.media import jpeg, precover, raw
from cipherstitch.media.jpeg import Subsampling
from cipherstitch.media.precover import Precover
from cipherstitch.methods.dct import DctChannel
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
FILE = HiddenFile("note.txt", b"precover " * 40)
ENCRYPTED = HideOptions(
    method=MethodId.DCT, cipher=CipherId.AES_GCM, password=PASSWORD, is_research=True
)
PLAIN = HideOptions(method=MethodId.DCT, cipher=CipherId.NONE, is_research=True)
# Colour at every subsampling, and greyscale, which has none.
PICTURES = [*(("RGB", subsampling) for subsampling in Subsampling), ("L", precover.SUBSAMPLING)]


def _photo(path: Path, size: tuple[int, int] = (320, 240), mode: str = "RGB", **save: Any) -> Path:
    width, height = size
    rng = np.random.default_rng(seed=1)
    gradient = np.linspace(0, 255, width)[None, :] + np.linspace(0, 255, height)[:, None]
    pixels = np.clip(gradient[..., None] / 2 + rng.normal(0, 12, (height, width, 3)), 0, 255)
    Image.fromarray(pixels.astype(np.uint8)).convert(mode).save(path, **save)

    return path


def _pillows(mode: str, subsampling: Subsampling) -> dict[str, Any]:
    """Pillow's options for the same chroma sampling: none for greyscale, which has no chroma."""
    numbers = {Subsampling.H1V1: 0, Subsampling.H2V1: 1, Subsampling.H2V2: 2}

    return {"subsampling": numbers[subsampling]} if mode == "RGB" else {}


def _dng(path: Path, size: tuple[int, int] = (192, 128), orientation: int | None = None) -> Path:
    """A 16-bit Bayer DNG, as small as LibRaw reads."""
    width, height = size
    cfa = np.random.default_rng(seed=0).integers(500, 3500, (height, width), dtype=np.uint16)
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    values: list[tuple[int, Any, int]] = [
        (254, 0, TiffTags.LONG),  # a full-resolution picture
        (262, 32803, TiffTags.SHORT),  # a colour filter array
        (271, "Test", TiffTags.ASCII),
        (33421, (2, 2), TiffTags.SHORT),
        (33422, bytes([0, 1, 1, 2]), TiffTags.BYTE),  # RGGB
        (50706, bytes([1, 4, 0, 0]), TiffTags.BYTE),  # DNG 1.4
        (50708, "Test Camera", TiffTags.ASCII),
        (50714, 0, TiffTags.LONG),  # black level
        (50717, 4095, TiffTags.LONG),  # white level
        (50721, _rationals(1, 0, 0, 0, 1, 0, 0, 0, 1), TiffTags.SIGNED_RATIONAL),
        (50728, _rationals(1, 1, 1), TiffTags.RATIONAL),
        (50778, 21, TiffTags.SHORT),  # D65
    ]

    if orientation is not None:
        values.append((274, orientation, TiffTags.SHORT))

    for tag, value, kind in values:
        tags[tag] = value
        tags.tagtype[tag] = kind

    Image.frombytes("I;16", size, cfa.tobytes()).save(path, format="TIFF", tiffinfo=tags)

    return path


def _rationals(*values: int) -> tuple[TiffImagePlugin.IFDRational, ...]:
    return tuple(TiffImagePlugin.IFDRational(value) for value in values)


def _saved(cover: Precover, path: Path, ac: npt.NDArray[np.int16] | None = None) -> Path:
    components = cover.coefficients.components
    embedded = cover.coefficients.ac if ac is None else ac
    jpeg.EmbeddedCompressedJpeg(
        embedded,
        cover.coefficients.dc,
        components,
        cover.size,
        cover.icc_profile,
        cover.subsampling,
    ).save(path)

    return path


@pytest.mark.parametrize("subsampling", list(Subsampling))
@pytest.mark.parametrize("size", [(320, 240), (75, 100), (21, 37), (4000, 8)])
def test_a_saved_precover_reads_back_as_compressed(
    tmp_path: Path, size: tuple[int, int], subsampling: Subsampling
) -> None:
    """Odd numbers of 8-pixel blocks, and a picture one block high, keep their grid."""
    jpeglib = pytest.importorskip("jpeglib")
    cover = precover.compress(_photo(tmp_path / "photo.png", size), subsampling=subsampling)
    across, down = subsampling.factors
    saved = _saved(cover, tmp_path / "out.jpg")
    read = jpeg.read_coefficients(saved)
    header = jpeg.header(saved)

    assert np.array_equal(read.ac, cover.coefficients.ac)
    assert np.array_equal(read.dc, cover.coefficients.dc)
    assert [(c.rows, c.columns) for c in read.components] == [
        (c.rows, c.columns) for c in cover.coefficients.components
    ]
    assert all(
        np.array_equal(a.steps, b.steps)
        for a, b in zip(read.components, cover.coefficients.components, strict=True)
    )
    assert jpeglib.read_dct(str(saved)).samp_factor.tolist() == [[down, across], [1, 1], [1, 1]]
    assert header.component_ids == bytes([1, 2, 3])
    assert [s.payload[:5] for s in header.segments if s.is_metadata] == [b"JFIF" + bytes([0])]

    with Image.open(saved) as image:
        assert image.size == size


@pytest.mark.parametrize(("mode", "subsampling"), PICTURES)
@pytest.mark.parametrize("size", [(320, 240), (321, 241), (21, 37), (4000, 8), (8, 4000)])
def test_the_coefficients_are_libjpegs(
    tmp_path: Path, size: tuple[int, int], mode: str, subsampling: Subsampling
) -> None:
    """Pillow's JPEG of the same picture (libjpeg-turbo), and the integer DCT of IJG
    libjpeg 6b and libjpeg-turbo through jpeglib, have the very same coefficients: nothing
    marks the JPEG as CipherStitch's own."""
    jpeglib = pytest.importorskip("jpeglib")
    source = _photo(tmp_path / "photo.png", size, mode)
    cover = precover.compress(source, subsampling=subsampling).coefficients
    references = [tmp_path / "pillow.jpg"]
    across, down = subsampling.factors

    with Image.open(source) as image:
        pixels = np.asarray(image)
        image.save(references[0], quality=precover.QUALITY, **_pillows(mode, subsampling))

    previous = jpeglib.version.get()

    try:
        for version in ("6b", "turbo210"):
            jpeglib.version.set(version)
            written = jpeglib.from_spatial(
                np.ascontiguousarray(pixels[..., None] if mode == "L" else pixels)
            )

            if mode == "RGB":
                written.samp_factor = [[down, across], [1, 1], [1, 1]]

            references.append(tmp_path / f"{version}.jpg")
            written.write_spatial(
                str(references[-1]), qt=precover.QUALITY, dct_method=jpeglib.JDCT_ISLOW
            )
    finally:
        jpeglib.version.set(previous)

    for reference in references:
        read = jpeg.read_coefficients(reference)
        assert np.array_equal(read.ac, cover.ac)
        assert np.array_equal(read.dc, cover.dc)


@pytest.mark.parametrize(("mode", "subsampling"), PICTURES)
def test_without_a_profile_the_jpeg_is_pillows_byte_for_byte(
    tmp_path: Path, mode: str, subsampling: Subsampling
) -> None:
    pytest.importorskip("jpeglib")
    source = _photo(tmp_path / "photo.png", (321, 241), mode)
    saved = _saved(precover.compress(source, subsampling=subsampling), tmp_path / "out.jpg")
    options = _pillows(mode, subsampling)

    with Image.open(source) as image:
        image.save(tmp_path / "pillow.jpg", quality=precover.QUALITY, optimize=True, **options)

    assert saved.read_bytes() == (tmp_path / "pillow.jpg").read_bytes()


def test_compressing_twice_gives_the_same_arrays(tmp_path: Path) -> None:
    source = _photo(tmp_path / "photo.png", (75, 100))
    first, second = precover.compress(source), precover.compress(source)

    assert np.array_equal(first.coefficients.ac, second.coefficients.ac)
    assert np.array_equal(first.coefficients.dc, second.coefficients.dc)
    assert np.array_equal(first.rounding, second.rounding)
    assert np.abs(first.rounding.astype(np.int16)).max() <= precover.ROUNDING_SCALE // 2


def test_a_rounding_tie_carries_no_side_information() -> None:
    """The DCT's output is 8 times the coefficient, so with a step of q the quantizer
    divides by 8q: 2.5 steps either way is a tie, 1 step and 3/(8q) is not."""
    steps = precover._tables(precover.QUALITY)[0].reshape(-1)
    divisor = 8 * steps.astype(np.int64)
    output = np.zeros((1, 64), dtype=np.int64)
    output[0, 1:4] = (5 * divisor[1:4] // 2) * np.array([1, -1, 1])
    output[0, 3] = divisor[3] + 3
    quantized, rounding = precover._rounded(output, steps)

    assert quantized[0, 1:4].tolist() == [3, -3, 1]
    assert rounding[:3].tolist() == [0, 0, round(3 / divisor[3] * precover.ROUNDING_SCALE)]


@pytest.mark.parametrize(
    ("options", "size"), [(ENCRYPTED, (320, 240)), (PLAIN, (321, 241))], ids=["encrypted", "plain"]
)
def test_a_png_precover_hides_and_reveals(
    tmp_path: Path, options: HideOptions, size: tuple[int, int]
) -> None:
    pytest.importorskip("jpeglib")
    source = _photo(tmp_path / "photo.png", size)
    embedded = tmp_path / "embedded.jpg"
    service.hide(source, FILE, embedded, options)
    password = options.password if options.is_encrypted else None
    result = service.reveal(embedded, tmp_path, password)

    assert result.path.read_bytes() == FILE.data
    assert result.method == MethodId.DCT

    with Image.open(embedded) as image:
        assert (image.format, image.size) == ("JPEG", size)


def test_a_precover_gets_the_precover_advice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("jpeglib")
    monkeypatch.setattr(risks, "PRECOVER_ROOT_RATE", 3 * risks.ROOT_RATES[MethodId.DCT])
    source = _photo(tmp_path / "photo.png")
    estimate = service.estimate(source, FILE.name, len(FILE.data), ENCRYPTED)
    channel = DctChannel(source)
    elements, capacity = channel.element_count, channel.capacity(0)

    assert estimate.advised == risks.advised_size(
        MethodId.DCT, elements, capacity, is_precover=True
    )
    assert estimate.advised != risks.advised_size(MethodId.DCT, elements, capacity)


def test_changes_move_towards_the_precover(tmp_path: Path) -> None:
    """One step each, nearly always the way the rounding error points where it has one: the
    other way costs 1 + 2 |error| times as much, not 1 − 2 |error|."""
    pytest.importorskip("jpeglib")
    source = _photo(tmp_path / "photo.png")
    cover = precover.compress(source)
    embedded = tmp_path / "embedded.jpg"
    service.hide(source, FILE, embedded, ENCRYPTED)
    before = cover.coefficients.ac
    step = jpeg.read_coefficients(embedded).ac.astype(np.int16) - before
    changed = step != 0
    towards = np.sign(cover.rounding).astype(np.int16)
    informed = changed & (towards != 0)

    assert np.abs(step).max() == 1
    assert np.mean(step[informed] == towards[informed]) > 0.98
    assert np.any(informed & (np.abs(before + step) > np.abs(before)))  # away from zero too


def test_binary_changes_go_towards_the_precover(tmp_path: Path) -> None:
    """Each changed coefficient takes its cheaper step, which is towards the precover wherever
    the rounding error says which way that is."""
    pytest.importorskip("jpeglib")
    source = _photo(tmp_path / "photo.png")
    cover = precover.compress(source)
    embedded = tmp_path / "embedded.jpg"
    binary = replace(ENCRYPTED, settings=Settings.of({"stc.code": "binary"}))
    service.hide(source, FILE, embedded, binary)
    step = jpeg.read_coefficients(embedded).ac.astype(np.int16) - cover.coefficients.ac
    towards = np.sign(cover.rounding).astype(np.int16)
    informed = (step != 0) & (towards != 0)

    assert np.count_nonzero(informed) > 100
    assert np.array_equal(step[informed], towards[informed])


def test_hiding_reports_compressing_the_precover_first(tmp_path: Path) -> None:
    pytest.importorskip("jpeglib")
    seen: list[tuple[int, int, str]] = []
    source = _photo(tmp_path / "photo.png")
    service.hide(source, FILE, tmp_path / "embedded.jpg", PLAIN, lambda *step: seen.append(step))
    labels = (
        "Compressing photo.png to JPEG",
        "Embedding · DCT coefficients (JPEG)",
        "Writing JPEG",
    )
    steps = [(index, len(labels), label) for index, label in enumerate(labels)]

    assert seen == [*steps, steps[-1]]  # once more just before saving


@pytest.mark.parametrize("size", [(321, 241), (75, 100)])
def test_the_saved_jpeg_has_the_precovers_units(tmp_path: Path, size: tuple[int, int]) -> None:
    pytest.importorskip("jpeglib")
    source = _photo(tmp_path / "photo.png", size)
    saved = _saved(precover.compress(source), tmp_path / "out.jpg")

    for prefix in (0, 16):
        assert DctChannel(source).stored_limit(prefix) == DctChannel(saved).stored_limit(prefix)


def test_a_greyscale_precover_has_one_component(tmp_path: Path) -> None:
    cover = precover.compress(_photo(tmp_path / "grey.png", mode="L"))

    assert [(c.rows, c.columns) for c in cover.coefficients.components] == [(30, 40)]

    pytest.importorskip("jpeglib")
    saved = _saved(cover, tmp_path / "out.jpg")

    read = jpeg.read_coefficients(saved)

    assert np.array_equal(read.ac, cover.coefficients.ac)
    assert np.array_equal(read.dc, cover.coefficients.dc)
    assert jpeg.header(saved).component_ids == bytes([1])


def test_sixteen_bit_grey_is_rounded_to_eight_bits(tmp_path: Path) -> None:
    """To the nearest level, as libjpeg needs 8-bit samples: 128 is 0, 129 is 1 (half a
    level is 128.5), and 65535 is 255."""
    rng = np.random.default_rng(seed=2)
    deep = rng.integers(0, 65536, (24, 40), dtype=np.uint16)
    deep[0, :4] = [128, 129, 65535, 385]
    levels = np.floor(deep / 257 + 0.5).astype(np.uint8)
    Image.fromarray(levels).save(tmp_path / "grey.png")
    Image.fromarray(deep).save(tmp_path / "deep.png")

    with Image.open(tmp_path / "deep.png") as opened:
        assert opened.mode == "I;16"

    assert levels[0, :4].tolist() == [0, 1, 255, 1]

    shallow, full = (
        precover.compress(tmp_path / "grey.png"),
        precover.compress(tmp_path / "deep.png"),
    )

    assert np.array_equal(shallow.coefficients.ac, full.coefficients.ac)
    assert np.array_equal(shallow.coefficients.dc, full.coefficients.dc)
    assert np.array_equal(shallow.rounding, full.rounding)


def test_an_opaque_alpha_band_is_dropped(tmp_path: Path) -> None:
    rgba = precover.compress(_photo(tmp_path / "rgba.png", mode="RGBA"))
    rgb = precover.compress(_photo(tmp_path / "rgb.png"))

    assert np.array_equal(rgba.coefficients.ac, rgb.coefficients.ac)


@pytest.mark.parametrize(
    ("mode", "save"),
    [("RGBA", {}), ("RGB", {"transparency": (0, 0, 0)})],
    ids=["alpha", "transparency-key"],
)
def test_transparency_cannot_be_compressed(tmp_path: Path, mode: str, save: dict[str, Any]) -> None:
    pixels = np.zeros((16, 16, 4), dtype=np.uint8)
    pixels[..., 3] = 255
    pixels[:4, :4, 3] = 0
    Image.fromarray(pixels).convert(mode).save(tmp_path / "logo.png", **save)

    with pytest.raises(UnsupportedFormatError, match=r"JPEG can't keep logo\.png's transparency"):
        precover.compress(tmp_path / "logo.png")


def test_a_transparent_grey_level_cannot_be_compressed(tmp_path: Path) -> None:
    levels = np.full((16, 16), 1000, dtype=np.uint16)
    Image.fromarray(levels).save(tmp_path / "opaque.png", transparency=0)
    levels[:4, :4] = 0
    Image.fromarray(levels).save(tmp_path / "logo.png", transparency=0)

    assert precover.compress(tmp_path / "opaque.png").size == (16, 16)

    with pytest.raises(UnsupportedFormatError, match="transparency"):
        precover.compress(tmp_path / "logo.png")


def test_other_colour_spaces_are_refused(tmp_path: Path) -> None:
    source = _photo(tmp_path / "print.tif", mode="CMYK")

    with pytest.raises(UnsupportedFormatError, match="CMYK images can't be a precover"):
        precover.compress(source)


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
@pytest.mark.parametrize("limit", [320 * 240 - 1, 1000], ids=["over-the-limit", "bomb"])
def test_huge_pictures_are_refused_before_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    source = _photo(tmp_path / "photo.png")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", limit)

    with pytest.raises(UnsupportedFormatError, match=r"photo\.png is too large to read"):
        precover.compress(source)


@pytest.mark.parametrize("name", ["photo.png", "photo.tif"])
def test_a_turned_picture_is_compressed_upright(tmp_path: Path, name: str) -> None:
    """Pillow turns a TIFF while loading it: it must not be turned twice."""
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: turned a quarter clockwise
    source = _photo(tmp_path / name, (40, 24), exif=exif)

    assert precover.compress(source).size == (24, 40)


def test_an_rgb_profile_is_kept(tmp_path: Path) -> None:
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    colour = precover.compress(_photo(tmp_path / "colour.png", icc_profile=profile))
    grey = precover.compress(_photo(tmp_path / "grey.png", mode="L", icc_profile=profile))

    assert colour.icc_profile == profile
    assert grey.icc_profile is None

    pytest.importorskip("jpeglib")
    saved = _saved(colour, tmp_path / "out.jpg")

    with Image.open(saved) as image:
        assert image.info["icc_profile"] == profile


def test_a_raw_precover_hides_and_reveals(tmp_path: Path) -> None:
    pytest.importorskip("rawpy")
    pytest.importorskip("jpeglib")
    source = _dng(tmp_path / "IMG_0001.DNG")
    embedded = tmp_path / "embedded.jpg"

    assert service.describe_carrier(source, CarrierKind.IMAGE).description == ("192 × 128 · DNG")

    service.hide(source, FILE, embedded, ENCRYPTED)

    assert service.reveal(embedded, tmp_path, PASSWORD).path.read_bytes() == FILE.data

    with Image.open(embedded) as image:
        assert image.size == (192, 128)
        assert "exif" not in image.info


def test_a_turned_raw_is_developed_upright(tmp_path: Path) -> None:
    pytest.importorskip("rawpy")
    source = _dng(tmp_path / "turned.dng", orientation=6)

    assert raw.describe(source) == "128 × 192 · DNG"
    assert precover.compress(source).size == (128, 192)


def test_a_huge_raw_is_refused_before_developing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("rawpy")
    source = _dng(tmp_path / "huge.dng")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)

    with pytest.raises(
        UnsupportedFormatError, match=r"huge\.dng is too large to read \(192 × 128\)"
    ):
        precover.compress(source)


def test_a_broken_raw_is_unreadable(tmp_path: Path) -> None:
    pytest.importorskip("rawpy")
    junk = tmp_path / "junk.cr2"
    junk.write_bytes(b"junk" * 100)

    for read in (raw.describe, precover.compress):
        with pytest.raises(UnsupportedFormatError, match=r"junk\.cr2 is not a readable RAW file"):
            read(junk)


def test_a_raw_needs_the_raw_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _dng(tmp_path / "photo.dng")
    monkeypatch.setitem(sys.modules, "rawpy", None)

    for read in (raw.describe, precover.compress):
        with pytest.raises(NotAvailableError, match=r"DNG needs the raw extra, see Installation"):
            read(source)


def test_precovers_are_told_by_their_suffix() -> None:
    assert precover.is_precover(Path("photo.PNG"))
    assert precover.is_precover(Path("IMG_0001.CR3"))
    assert not precover.is_precover(Path("photo.jpg"))
