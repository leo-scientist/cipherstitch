import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from cipherstitch import risks, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.media import jpeg
from cipherstitch.methods import lsb
from cipherstitch.methods.lsb import ImageChannel
from cipherstitch.payload import HiddenFile
from cipherstitch.service import Estimate, HideOptions

from .conftest import ImageFactory

KEYED = HideOptions(method=MethodId.LSB, is_research=True)
CAPACITY = 10_000


def _risks(method: MethodId, required: int, advised: int | None) -> tuple[str, ...]:
    return risks.detection_risks(method, fill=risks.Fill(required, CAPACITY, advised))


def _share(cover: Path, share: float) -> risks.Fill:
    """A payload taking ``share`` of the cover's LSB capacity, with no advice."""
    capacity = service.capacity(cover, research=True)

    return risks.Fill(round(share * capacity), capacity, None)


@pytest.mark.parametrize(
    ("required", "warned"), [(500, False), (501, True), (CAPACITY, True), (CAPACITY + 1, False)]
)
def test_a_payload_is_risky_above_the_advised_size_until_it_does_not_fit(
    required: int, warned: bool
) -> None:
    found = _risks(MethodId.LSB, required, 500)

    assert (
        any("of the carrier, which makes it easier to detect" in risk for risk in found) is warned
    )


def test_fill_warning_states_the_advice_in_bytes() -> None:
    assert _risks(MethodId.DCT, 3000, 1536) == (
        "The payload takes 2.9 KiB of the carrier, which makes it easier to detect. "
        "Keep it to 1.5 KiB or less.",
    )


def test_fill_warning_never_shows_the_same_size_twice() -> None:
    assert _risks(MethodId.LSB, 7720, 7704) == (
        "The payload takes 7720 B of the carrier, which makes it easier to detect. "
        "Keep it to 7704 B or less.",
    )


def test_only_statistical_methods_get_advice() -> None:
    assert risks.advised_size(MethodId.AUDIO_LSB, 10_000, CAPACITY) == risks.advised_size(
        MethodId.LSB, 10_000, CAPACITY
    )
    assert risks.advised_size(MethodId.OBJECT_STREAM, 10_000, CAPACITY) is None
    assert risks.advised_size(MethodId.LSB, None, CAPACITY) is None
    assert not risks.is_conspicuous(CAPACITY, None)
    assert not any("takes" in risk for risk in _risks(MethodId.ZERO_WIDTH, 5000, None))


def test_the_advice_grows_with_the_square_root_of_the_cover() -> None:
    """The square-root law: four times the values hide twice the payload, so a smaller share
    of the capacity, and never more than the capacity."""
    small = risks.advised_size(MethodId.LSB, 1_000_000, 10**6)
    large = risks.advised_size(MethodId.LSB, 4_000_000, 10**6)

    assert small == risks.ROOT_RATES[MethodId.LSB] * 1000 // 8
    assert large == 2 * small
    assert risks.advised_size(MethodId.DCT, 1_000_000, 100) == 100


def test_a_precover_has_its_own_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """DCT from a RAW, PNG or TIFF: the rate measured on precovers, not on JPEG covers."""
    monkeypatch.setattr(risks, "PRECOVER_ROOT_RATE", 2 * risks.ROOT_RATES[MethodId.DCT])
    monkeypatch.setattr(risks, "PRECOVER_CALIBRATED_ELEMENTS", 1)
    on_jpeg = risks.advised_size(MethodId.DCT, 1_000_000, 10**6)
    lsb = risks.advised_size(MethodId.LSB, 1_000_000, 10**6)

    assert on_jpeg is not None
    assert risks.advised_size(MethodId.DCT, 1_000_000, 10**6, is_precover=True) == 2 * on_jpeg
    assert risks.advised_size(MethodId.LSB, 1_000_000, 10**6, is_precover=True) == lsb


@pytest.mark.parametrize("method", [MethodId.LSB, MethodId.DCT])
def test_a_cover_below_the_calibrated_size_keeps_its_share(method: MethodId) -> None:
    """Smaller covers were easier to detect at the same root rate, so below the size the rates
    were measured on, the advice shrinks with the cover."""
    calibrated = risks.CALIBRATED_ELEMENTS[method]
    full = risks.advised_size(method, calibrated, 10**6)
    quarter = risks.advised_size(method, calibrated // 4, 10**6)

    assert full is not None and quarter is not None
    assert abs(4 * quarter - full) < 8


FLAT_WARNING = (
    r"\d+% of the image is flat colour, and some LSB changes may land there, "
    r"where they are easy to spot\."
)


@pytest.mark.parametrize(
    ("flat_rows", "share", "warned"),
    [(18, 0.05, False), (18, 0.5, True), (66, 0.05, True)],
    ids=["a-quarter-at-5%", "a-quarter-at-50%", "most-at-5%"],
)
def test_lsb_warns_when_its_changes_cannot_avoid_flat_areas(
    tmp_path: Path, flat_rows: int, share: float, warned: bool
) -> None:
    """LSB can change a flat value only in a code block with no other values, which a quarter
    of flat colour leaves none of at 5% but some at 50%."""
    pixels = np.random.default_rng(seed=0).integers(0, 256, (72, 96, 3), dtype=np.uint8)
    pixels[:flat_rows] = 0
    Image.fromarray(pixels).save(tmp_path / "cover.png")
    cover = tmp_path / "cover.png"
    found = risks.detection_risks(MethodId.LSB, carrier=cover, fill=_share(cover, share))
    flat = [risk for risk in found if "flat colour" in risk]

    assert len(flat) == warned
    assert all(re.fullmatch(FLAT_WARNING, risk) for risk in flat)
    assert not risks.detection_risks(MethodId.LSB, carrier=tmp_path / "cover.png")
    Image.fromarray(pixels).save(tmp_path / "cover.jpg", quality=95)
    assert (
        service.detection_risks(
            HideOptions(method=MethodId.DCT, is_research=True), tmp_path / "cover.jpg"
        )
        == ()
    )


def test_the_flat_warning_models_the_body_hiding_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The estimate's required size becomes the body ``service.hide`` writes."""
    pixels = np.random.default_rng(seed=0).integers(0, 256, (120, 160, 3), dtype=np.uint8)
    pixels[:40] = 0
    cover = tmp_path / "cover.png"
    Image.fromarray(pixels).save(cover)
    options = HideOptions(CarrierKind.IMAGE, MethodId.LSB, CipherId.NONE, is_research=True)
    size = service.capacity(cover, research=True) // 2
    required = service.estimate(cover, "a.bin", size, options).required
    bodies: list[int] = []
    write, flat_blocks = ImageChannel.write, lsb.flat_blocks

    def recorded(channel: ImageChannel, prefix: bytes, body: bytes, seed: bytes | None) -> Any:
        bodies.append(len(body))

        return write(channel, prefix, body, seed)

    def modelled(count: int, body_size: int, flat_share: float, *format: Any) -> float:
        bodies.append(body_size)

        return flat_blocks(count, body_size, flat_share, *format)

    monkeypatch.setattr(ImageChannel, "write", recorded)
    monkeypatch.setattr(lsb, "flat_blocks", modelled)
    service.hide(cover, HiddenFile("a.bin", bytes(size)), tmp_path / "out.png", options)

    assert risks.flat_cover(MethodId.LSB, cover, required) == "33% of the image is flat colour"
    assert bodies[0] == bodies[1] > 0


def test_a_flat_share_below_one_percent_is_not_called_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A share this small never meets an all-flat code block, so one is assumed."""
    monkeypatch.setattr(lsb, "flat_blocks", lambda *_args: 1.0)
    pixels = np.random.default_rng(seed=0).integers(0, 256, (72, 96, 3), dtype=np.uint8)
    pixels[0, :20] = 0  # 0.3% of the values
    Image.fromarray(pixels).save(tmp_path / "cover.png")
    cover = tmp_path / "cover.png"
    found = risks.detection_risks(MethodId.LSB, carrier=cover, fill=_share(cover, 0.9))

    assert found[-1].startswith("<1% of the image is flat colour, and some ")


@pytest.mark.parametrize(
    ("name", "mode", "expected"),
    [
        ("cover.jpg", "RGB", "The carrier is a JPEG"),
        ("cover.webp", "RGB", "The carrier is a WebP"),
        ("cover.png", "L", "The carrier is greyscale"),
        ("cover.png", "LA", "The carrier is greyscale"),
    ],
)
def test_image_lsb_warns_about_the_cover(
    make_image: ImageFactory, name: str, mode: str, expected: str
) -> None:
    found = service.detection_risks(KEYED, make_image(name, mode=mode))

    assert len(found) == 1
    assert found[0].startswith(expected)


def test_palette_cover(make_image: ImageFactory, tmp_path: Path) -> None:
    palette = tmp_path / "palette.png"

    with Image.open(make_image()) as image:
        image.convert("P").save(palette)

    found = service.detection_risks(KEYED, palette)

    assert len(found) == 1
    assert found[0].startswith("The carrier uses a colour palette")


def test_a_multi_picture_jpeg_is_a_jpeg(make_image: ImageFactory, tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"

    with Image.open(make_image()) as image:
        image.save(photo, "MPO", save_all=True, append_images=[image.rotate(90)])

    found = service.detection_risks(KEYED, photo)

    assert len(found) == 2
    assert found[0].startswith("The carrier is a JPEG")
    assert found[1].startswith("The carrier holds several frames or pages")


@pytest.mark.parametrize(
    ("method", "name"),
    [
        (MethodId.LSB, "animated.png"),
        (MethodId.LSB, "pages.tif"),
        (MethodId.DCT, "pages.tif"),
    ],
)
def test_only_the_first_frame_or_page_is_kept(
    make_image: ImageFactory, tmp_path: Path, method: MethodId, name: str
) -> None:
    with Image.open(make_image()) as image:
        image.save(tmp_path / name, save_all=True, append_images=[image.rotate(180)])

    assert service.detection_risks(
        HideOptions(method=method, is_research=True), tmp_path / name
    ) == (
        "The carrier holds several frames or pages. Only the first will be saved, and the "
        "missing ones stand out.",
    )


def test_a_colour_png_cover_is_fine(make_image: ImageFactory) -> None:
    assert service.detection_risks(KEYED, make_image()) == ()
    assert service.detection_risks(KEYED, make_image("cover.png", mode="RGBA")) == ()


def test_dct_keeps_the_jpeg_so_the_cover_is_not_a_risk(make_image: ImageFactory) -> None:
    dct = HideOptions(method=MethodId.DCT, is_research=True)

    assert service.detection_risks(dct, make_image("cover.jpg")) == ()


def test_dct_warns_about_a_lossy_precover(
    make_image: ImageFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    dct = HideOptions(method=MethodId.DCT, is_research=True)
    scan = make_image("scan.tif")

    with Image.open(scan) as image:
        image.save(scan, compression="jpeg")

    assert service.detection_risks(dct, scan) == (
        "The carrier is a JPEG-compressed TIFF. Compressing it to JPEG again leaves traces of "
        "both. Use a RAW or a cover that was never lossy-compressed.",
    )

    def not_a_jpeg(_path: Path) -> None:
        raise AssertionError("read a precover as a JPEG")

    monkeypatch.setattr(jpeg, "header", not_a_jpeg)

    assert service.detection_risks(dct, make_image()) == ()
    assert service.detection_risks(dct, Path("IMG_0001.CR2")) == ()


@pytest.mark.parametrize(
    ("options", "kind"),
    [
        ({"progressive": True}, "a progressive JPEG"),
        ({"restart_marker_blocks": 8}, "a JPEG with restart markers"),
        (
            {"progressive": True, "restart_marker_rows": 1},
            "a progressive JPEG with restart markers",
        ),
    ],
    ids=["progressive", "restart", "both"],
)
def test_dct_warns_about_what_the_result_cannot_keep(
    make_image: ImageFactory, tmp_path: Path, options: dict[str, Any], kind: str
) -> None:
    cover = tmp_path / "web.jpg"

    with Image.open(make_image()) as image:
        image.save(cover, **options)

    assert service.detection_risks(HideOptions(method=MethodId.DCT, is_research=True), cover) == (
        f"The carrier is {kind}, but the result will be a baseline JPEG without restart "
        "markers, unlike other pictures from the same camera or app.",
    )


def test_lsb_order_is_no_risk_without_a_password() -> None:
    """Without a password LSB still spreads its changes, in a public order."""
    audio = HideOptions(CarrierKind.AUDIO, MethodId.AUDIO_LSB, is_research=True)

    for options in (KEYED, audio):
        assert service.detection_risks(options) == ()
        assert service.detection_risks(replace(options, cipher=CipherId.NONE)) == ()


@pytest.mark.parametrize("cipher", [CipherId.AES_GCM, CipherId.NONE])
def test_dct_spreads_its_changes_with_or_without_encryption(cipher: CipherId) -> None:
    assert (
        service.detection_risks(HideOptions(method=MethodId.DCT, cipher=cipher, is_research=True))
        == ()
    )


def test_dct_above_half_gets_only_the_fill_warning() -> None:
    assert _risks(MethodId.DCT, 6000, 1000) == (
        "The payload takes 5.9 KiB of the carrier, which makes it easier to detect. "
        "Keep it to 1000 B or less.",
    )


@pytest.mark.parametrize(
    ("carrier", "method", "expected"),
    [
        (CarrierKind.DOCUMENT, MethodId.OBJECT_STREAM, "no real application data"),
        (CarrierKind.DOCUMENT, MethodId.WHITESPACE, "trailing whitespace"),
        (CarrierKind.TEXT, MethodId.ZERO_WIDTH, "zero-width characters"),
        (CarrierKind.AUDIO, MethodId.PHASE_CODING, "error-correction codes"),
        (CarrierKind.AUDIO, MethodId.ECHO_HIDING, "to within 223 bytes"),
    ],
)
def test_structural_methods_are_visible_on_inspection(
    carrier: CarrierKind, method: MethodId, expected: str
) -> None:
    for cipher in (CipherId.AES_GCM, CipherId.NONE):
        found = service.detection_risks(HideOptions(carrier, method, cipher, is_research=True))

        assert len(found) == 1
        assert expected in found[0]


def test_service_reads_the_fill_from_the_estimate(make_image: ImageFactory) -> None:
    cover = make_image()
    estimate = Estimate(required=300, available=1000, advised=200)

    assert service.detection_risks(KEYED, cover, estimate)[0].startswith("The payload takes 300 B")
    assert service.is_conspicuous(estimate)
    assert not service.is_conspicuous(Estimate(required=50, available=1000, advised=200))
    assert not service.is_conspicuous(Estimate(required=300, available=1000))
    assert Estimate(required=50, available=0).share is None


def _advised_share(cover: Path) -> float:
    estimate = service.estimate(cover, "a.bin", 1, KEYED)
    assert estimate.advised is not None and estimate.available is not None

    return estimate.advised / estimate.available


def test_a_larger_cover_is_advised_a_smaller_share(make_image: ImageFactory) -> None:
    """Above the calibrated size (512 × 512) the square-root law, below it a fixed share."""
    tiny, small = make_image("tiny.png", size=(128, 128)), make_image("small.png", size=(256, 256))
    calibrated, large = make_image("512.png", size=(512, 512)), make_image("1k.png", (1024, 1024))

    assert abs(_advised_share(tiny) - _advised_share(small)) < 0.005
    assert _advised_share(calibrated) > 1.9 * _advised_share(large)


def test_a_structural_method_gets_no_advice(tmp_path: Path) -> None:
    cover = tmp_path / "cover.txt"
    cover.write_text("A few words with spaces between them. " * 20)
    options = HideOptions(CarrierKind.TEXT, MethodId.ZERO_WIDTH, CipherId.NONE, is_research=True)
    estimate = service.estimate(cover, "a.bin", 1, options)

    assert estimate.available
    assert estimate.advised is None
    assert not service.is_conspicuous(replace(estimate, required=estimate.available))


def test_the_flat_share_is_read_again_after_a_same_size_rewrite_keeping_the_mtime(
    tmp_path: Path,
) -> None:
    """``cp -p`` and ``rsync -t`` keep the mtime, and a rewrite may keep the size: the inode or
    the ctime still tells the versions apart."""
    noise = np.random.default_rng(seed=0).integers(0, 256, (72, 96, 3), dtype=np.uint8)
    cover = tmp_path / "cover.png"
    Image.fromarray(noise).save(cover, compress_level=0)
    before = cover.stat()
    required = ImageChannel(cover).capacity(0)

    assert risks.flat_cover(MethodId.LSB, cover, required) is None

    half_flat = noise.copy()
    half_flat[:36] = 0
    rewritten = tmp_path / "rewritten.png"
    Image.fromarray(half_flat).save(rewritten, compress_level=0)
    rewritten.replace(cover)
    os.utime(cover, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = cover.stat()

    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert risks.flat_cover(MethodId.LSB, cover, required) == "50% of the image is flat colour"
