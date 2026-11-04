import struct
import sys
import wave
import zlib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image

from cipherstitch import catalog, container, methods, service, standard
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.errors import (
    CapacityError,
    CipherStitchError,
    NoHiddenDataError,
    NotAvailableError,
    UnsupportedFormatError,
)
from cipherstitch.methods.lsb import ImageChannel
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions, ResearchReveal

from .conftest import ImageFactory, skip_unless_method_installed

STRONG = "Correct-Horse-42!"
OPTIONS = HideOptions(method=MethodId.LSB, password=STRONG)
RESEARCH = replace(OPTIONS, is_research=True)
LOSSY = replace(RESEARCH, settings=Settings.of({"limg.lossy": True}))
FILE = HiddenFile("note.txt", b"standard " * 10)
LARGE = (256, 256)  # big enough that the advised size is below the capacity


def _image(path: Path, mode: str = "RGB", **save: Any) -> Path:
    pixels = np.random.default_rng(seed=0).integers(0, 256, (48, 64, 3), dtype=np.uint8)
    Image.fromarray(pixels).convert(mode).save(path, **save)

    return path


def _wav(path: Path, depth: int) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(depth // 8)
        wav.setframerate(8000)
        wav.writeframes(bytes(range(256)) * depth)

    return path


def test_only_recommended_carriers_methods_and_ciphers_stay_on() -> None:
    off_methods = {method for method in MethodId if standard.method_reason(method)}
    off_ciphers = {cipher for cipher in CipherId if standard.cipher_reason(cipher)}

    assert set(service.unavailable_carriers()) == {CarrierKind.DOCUMENT, CarrierKind.TEXT}
    assert service.unavailable_carriers(is_research=True) == {}
    assert standard.carrier_reason(CarrierKind.TEXT) == (
        "Off in Standard mode: no recommended method for this carrier"
    )
    assert off_methods == {
        MethodId.PHASE_CODING,
        MethodId.ECHO_HIDING,
        MethodId.OBJECT_STREAM,
        MethodId.WHITESPACE,
        MethodId.ZERO_WIDTH,
        MethodId.LSB_REPLACEMENT,
    }
    assert off_ciphers == {CipherId.AES_CBC_HMAC, CipherId.FERNET, CipherId.NONE}


def test_each_mode_offers_its_carriers_methods_and_ciphers() -> None:
    assert service.mode_carriers() == (CarrierKind.IMAGE, CarrierKind.AUDIO)
    assert service.mode_carriers(is_research=True) == tuple(CarrierKind)
    assert service.mode_methods(CarrierKind.IMAGE) == (MethodId.LSB, MethodId.DCT)
    assert service.mode_methods(CarrierKind.IMAGE, is_research=True) == (
        MethodId.LSB,
        MethodId.DCT,
        MethodId.LSB_REPLACEMENT,
    )
    assert service.mode_methods(CarrierKind.AUDIO) == (MethodId.AUDIO_LSB,)
    assert service.mode_ciphers() == (CipherId.AES_GCM, CipherId.CHACHA20, CipherId.XCHACHA20)
    assert service.mode_ciphers(is_research=True) == tuple(CipherId)


def test_the_experimental_method_is_research_only() -> None:
    off = service.unavailable_methods(CarrierKind.IMAGE, is_research=True)

    assert catalog.method(MethodId.LSB_REPLACEMENT).tier is catalog.Tier.EXPERIMENTAL
    assert MethodId.LSB_REPLACEMENT not in off
    assert MethodId.LSB not in off
    assert service.unavailable_methods(CarrierKind.IMAGE)[MethodId.LSB_REPLACEMENT] == (
        "Off in Standard mode: Sequential LSB replacement is not a Recommended method"
    )


def test_a_listed_method_without_a_channel_is_not_available_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(methods._EMBEDDINGS, MethodId.LSB_REPLACEMENT)
    off = service.unavailable_methods(CarrierKind.IMAGE, is_research=True)

    assert off[MethodId.LSB_REPLACEMENT] == "Sequential LSB replacement is not available yet"


def test_service_names_the_standard_reasons() -> None:
    fernet = HideOptions(cipher=CipherId.FERNET, password=STRONG)
    document = HideOptions(CarrierKind.DOCUMENT, MethodId.OBJECT_STREAM)

    assert service.unavailable_reason(fernet) == (
        "Off in Standard mode: Fernet is not a Recommended cipher"
    )
    assert service.unavailable_reason(document) == standard.carrier_reason(CarrierKind.DOCUMENT)
    assert service.unavailable_ciphers()[CipherId.FERNET] == service.unavailable_reason(fernet)
    assert service.unavailable_ciphers()[CipherId.NONE] == (
        "Off in Standard mode: every payload is encrypted"
    )
    assert CipherId.FERNET not in service.unavailable_ciphers(is_research=True)
    off = service.unavailable_methods(CarrierKind.AUDIO)

    assert off[MethodId.PHASE_CODING] == (
        "Off in Standard mode: Phase coding is not a Recommended method"
    )
    assert service.unavailable_reason(replace(fernet, is_research=True)) is None


@pytest.mark.parametrize(
    ("name", "mode", "save"),
    [
        ("cover.jpg", "RGB", {}),
        ("cover.webp", "RGB", {"lossless": True}),
        ("cover.tif", "RGB", {"compression": "jpeg"}),
        ("cover.png", "L", {}),
        ("cover.png", "P", {}),
        ("cover.tif", "CMYK", {}),
        ("cover.png", "1", {}),
        ("cover.png", "RGB", {"transparency": (0, 0, 0)}),
    ],
    ids=["jpeg", "webp", "jpeg-tiff", "grey", "palette", "cmyk", "1-bit", "transparency-key"],
)
def test_image_lsb_refuses_covers_it_would_change_beyond_the_bits(
    tmp_path: Path, name: str, mode: str, save: dict[str, Any]
) -> None:
    cover = _image(tmp_path / name, mode, **save)
    reason = standard.cover_reason(MethodId.LSB, cover)

    assert reason is not None
    assert reason.startswith("Off in Standard mode: ")
    off = service.unavailable_methods(CarrierKind.IMAGE, cover)

    assert service.unavailable_reason(OPTIONS, cover) == reason
    assert off[MethodId.LSB] == reason
    assert service.unavailable_reason(RESEARCH, cover) == reason.replace(
        standard.OFF, standard.RESEARCH_OFF
    )
    assert service.unavailable_reason(LOSSY, cover) is None


def _wide_png(path: Path) -> Path:
    """A 48-bit RGB PNG, which Pillow cannot write: Pillow decodes it to 8-bit RGB."""
    rows = b"".join(bytes(1 + 6 * 64) for _ in range(48))
    header = struct.pack(">IIBBBBB", 64, 48, 16, 2, 0, 0, 0)
    path.write_bytes(
        bytes([0x89, *b"PNG", 0x0D, 0x0A, 0x1A, 0x0A])
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )

    return path


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _bmp_16(path: Path) -> Path:
    """A 16-bit (5-5-5) BMP, which Pillow cannot write: Pillow decodes it to 8-bit RGB."""
    pixels = bytes(2 * 64 * 48)
    info = struct.pack("<IiiHHIIiiII", 40, 64, 48, 1, 16, 0, len(pixels), 2835, 2835, 0, 0)
    header = struct.pack("<2sIHHI", b"BM", 14 + len(info) + len(pixels), 0, 0, 14 + len(info))
    path.write_bytes(header + info + pixels)

    return path


@pytest.mark.parametrize(
    ("name", "write"),
    [("cover.png", _wide_png), ("cover.bmp", _bmp_16)],
    ids=["48-bit-png", "16-bit-bmp"],
)
def test_image_lsb_refuses_covers_with_other_sample_sizes(
    tmp_path: Path, name: str, write: Callable[[Path], Path]
) -> None:
    cover = write(tmp_path / name)

    with Image.open(cover) as image:
        assert image.mode == "RGB"

    assert service.unavailable_reason(OPTIONS, cover) == standard.cover_reason(MethodId.LSB, cover)
    assert standard.cover_reason(MethodId.LSB, cover) is not None


def test_image_lsb_refuses_a_16_bit_cover(tmp_path: Path) -> None:
    cover = tmp_path / "deep.png"
    Image.fromarray(np.arange(48 * 64, dtype=np.uint16).reshape(48, 64)).save(cover)

    assert service.unavailable_reason(OPTIONS, cover) == standard.cover_reason(MethodId.LSB, cover)
    assert standard.cover_reason(MethodId.LSB, cover) is not None


@pytest.mark.parametrize(
    ("name", "mode"),
    [("cover.png", "RGB"), ("cover.png", "RGBA"), ("cover.bmp", "RGB"), ("cover.tif", "RGB")],
)
def test_image_lsb_keeps_lossless_colour_covers(tmp_path: Path, name: str, mode: str) -> None:
    cover = _image(tmp_path / name, mode)

    assert standard.cover_reason(MethodId.LSB, cover) is None
    assert service.unavailable_reason(OPTIONS, cover) is None


def test_dct_keeps_every_jpeg(make_image: ImageFactory) -> None:
    cover = make_image("cover.jpg")

    assert standard.cover_reason(MethodId.DCT, cover) is None

    skip_unless_method_installed(MethodId.DCT)
    off = service.unavailable_methods(CarrierKind.IMAGE, cover)

    assert set(off) == {MethodId.LSB, MethodId.LSB_REPLACEMENT}
    assert service.unavailable_reason(HideOptions(method=MethodId.DCT), cover) is None


@pytest.mark.parametrize(("depth", "is_refused"), [(8, True), (16, False), (24, False)])
def test_audio_lsb_needs_16_bit_samples(tmp_path: Path, depth: int, is_refused: bool) -> None:
    cover = _wav(tmp_path / "cover.wav", depth)
    reason = standard.cover_reason(MethodId.AUDIO_LSB, cover)

    assert (reason is not None) is is_refused
    assert standard.cover_reason(MethodId.AUDIO_LSB, cover) == service.unavailable_reason(
        HideOptions(CarrierKind.AUDIO, MethodId.AUDIO_LSB), cover
    )


@pytest.mark.parametrize(("minimum", "is_refused"), [(24, True), (16, True), (8, False)])
def test_research_takes_the_audio_depth_from_its_settings(
    tmp_path: Path, minimum: int, is_refused: bool
) -> None:
    cover = _wav(tmp_path / "cover.wav", 8 if minimum < 24 else 16)
    settings = Settings.of({"laud.depth": minimum})
    options = HideOptions(
        CarrierKind.AUDIO, MethodId.AUDIO_LSB, is_research=True, settings=settings
    )
    reason = service.unavailable_reason(options, cover)

    assert (reason is not None) is is_refused
    assert reason is None or reason.startswith(standard.RESEARCH_OFF)


def test_lsb_refuses_covers_too_flat_for_a_standard_payload(tmp_path: Path) -> None:
    """LSB can change a flat value only in a code block with no other values, which a payload
    of the advised size meets only in a cover that is nearly all flat. Below 512 × 512 the
    advice keeps its share of the capacity, so a small cover passes as a large one does."""
    pixels = np.random.default_rng(seed=0).integers(0, 256, (600, 800, 3), dtype=np.uint8)
    pixels[:150] = 255  # a quarter of the image is blown-out sky
    Image.fromarray(pixels).save(tmp_path / "sky.png")
    Image.fromarray(pixels[:240, :320]).save(tmp_path / "small-sky.png")
    pixels[:594] = 255
    Image.fromarray(pixels).save(tmp_path / "blank.png")
    samples = np.random.default_rng(seed=0).integers(-3000, 3000, 2_000_000, dtype=np.int16)
    samples[:500_000] = 0  # a quarter of the recording is digital silence
    _mono(tmp_path / "gap.wav", samples)
    samples[:1_980_000] = 0
    _mono(tmp_path / "quiet.wav", samples)

    assert standard.cover_reason(MethodId.LSB, tmp_path / "sky.png") is None
    assert standard.cover_reason(MethodId.LSB, tmp_path / "small-sky.png") is None
    assert standard.cover_reason(MethodId.AUDIO_LSB, tmp_path / "gap.wav") is None
    assert standard.cover_reason(MethodId.LSB, tmp_path / "blank.png") == (
        "Off in Standard mode: 99% of the image is flat colour, "
        "and even a payload of the advised size could change some of it"
    )
    assert standard.cover_reason(MethodId.AUDIO_LSB, tmp_path / "quiet.wav") == (
        "Off in Standard mode: 99% of the recording is digital silence, "
        "and even a payload of the advised size could change some of it"
    )
    assert service.unavailable_reason(RESEARCH, tmp_path / "blank.png") is None  # a warning only


def _mono(path: Path, samples: npt.NDArray[np.int16]) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(samples.tobytes())


def test_an_unreadable_cover_gives_no_reason(tmp_path: Path) -> None:
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not an image")

    assert standard.cover_reason(MethodId.LSB, tmp_path / "gone.png") is None
    assert standard.cover_reason(MethodId.LSB, junk) is None
    assert service.unavailable_reason(OPTIONS, tmp_path / "gone.png") is None


def test_dct_refuses_a_precover_that_was_lossy_compressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan = _image(tmp_path / "scan.tif", compression="jpeg")
    monkeypatch.setitem(sys.modules, "rawpy", None)  # the cover rules never develop a RAW

    assert standard.cover_reason(MethodId.DCT, scan) == (
        "Off in Standard mode: a JPEG-compressed TIFF keeps compression traces that compressing "
        "it again shows"
    )
    assert standard.cover_reason(MethodId.DCT, _image(tmp_path / "scan.png")) is None
    assert standard.cover_reason(MethodId.DCT, _image(tmp_path / "photo.jpg")) is None
    assert standard.cover_reason(MethodId.DCT, tmp_path / "IMG_0001.CR2") is None
    assert standard.cover_reason(MethodId.DCT, scan, standard.CoverRules(is_standard=False)) is None


@pytest.mark.parametrize(
    ("method", "name"),
    [(MethodId.LSB, "animated.png"), (MethodId.LSB, "pages.tif"), (MethodId.DCT, "pages.tif")],
)
def test_a_cover_of_several_frames_or_pages_is_refused(
    tmp_path: Path, method: MethodId, name: str
) -> None:
    """Image LSB and a precover's compression would keep only the first."""
    with Image.open(_image(tmp_path / "first.png")) as first:
        first.save(tmp_path / name, save_all=True, append_images=[first.rotate(180)])

    assert standard.cover_reason(method, tmp_path / name) == (
        "Off in Standard mode: this cover holds several frames or pages, and only the first "
        "would be kept"
    )
    assert (
        standard.cover_reason(method, tmp_path / name, standard.CoverRules(is_standard=False))
        is None
    )
    assert standard.cover_reason(method, tmp_path / "first.png") is None


def test_carrier_formats() -> None:
    assert standard.carrier_formats(CarrierKind.IMAGE) == (
        ".png",
        ".bmp",
        ".tif",
        ".tiff",
        ".jpg",
        ".jpeg",
        *catalog.RAW_EXTENSIONS,
    )
    assert standard.carrier_formats(CarrierKind.AUDIO) == (".wav", ".flac")
    assert standard.carrier_formats(CarrierKind.DOCUMENT) == ()
    assert standard.carrier_formats(CarrierKind.TEXT) == ()


def test_estimate_measures_the_excess_against_the_advised_size(make_image: ImageFactory) -> None:
    cover = make_image(size=LARGE)
    channel = ImageChannel(cover)
    total = channel.capacity(0)
    size = container.max_data_size(channel, FILE.name, CipherId.AES_GCM) - total // 4
    estimate = service.estimate(cover, FILE.name, size, OPTIONS)
    research = service.estimate(cover, FILE.name, size, RESEARCH)

    assert estimate.limit is not None
    assert estimate.limit == estimate.advised == research.advised
    assert estimate.limit < total
    assert estimate.excess == estimate.required - estimate.limit > 0
    assert research.limit is None
    assert research.excess == 0


def test_standard_hide_needs_a_strong_password(make_image: ImageFactory, tmp_path: Path) -> None:
    weak = replace(OPTIONS, password="password")  # noqa: S106

    with pytest.raises(CipherStitchError, match=r"^Standard mode needs a strong password"):
        service.hide(make_image(), FILE, tmp_path / "out.png", weak)

    assert not (tmp_path / "out.png").exists()


@pytest.mark.parametrize(("minimum", "is_refused"), [(None, True), (4, True), (2, False)])
def test_research_takes_the_password_minimum_from_its_settings(
    make_image: ImageFactory, tmp_path: Path, minimum: int | None, is_refused: bool
) -> None:
    """``cat-river`` is fair, but not strong."""
    settings = Settings() if minimum is None else Settings.of({"crypto.minpw": minimum})
    options = replace(RESEARCH, password="cat-river", settings=settings)  # noqa: S106
    output = tmp_path / "out.png"

    if is_refused:
        with pytest.raises(CipherStitchError, match=r"^The Research parameters need a"):
            service.hide(make_image(), FILE, output, options)
    else:
        service.hide(make_image(), FILE, output, options)

    assert output.exists() is not is_refused


def test_a_plain_research_hide_needs_no_password(make_image: ImageFactory, tmp_path: Path) -> None:
    plain = replace(RESEARCH, cipher=CipherId.NONE, password=None)
    service.hide(make_image(), FILE, tmp_path / "out.png", plain)

    assert service.reveal(tmp_path / "out.png", tmp_path).path.read_bytes() == FILE.data


def test_standard_hide_ignores_the_settings(make_image: ImageFactory, tmp_path: Path) -> None:
    """A Format change in the settings would break a Standard reveal if Standard used it."""
    ignored = replace(OPTIONS, settings=Settings.of({"stc.h": 4}))
    service.hide(make_image(size=(320, 240)), FILE, tmp_path / "out.png", ignored)

    assert service.reveal(tmp_path / "out.png", tmp_path, STRONG).path.read_bytes() == FILE.data


def test_standard_hide_stays_within_the_advised_size(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    cover = make_image(size=LARGE)
    room = container.max_data_size(ImageChannel(cover), FILE.name, CipherId.AES_GCM)
    payload = HiddenFile(FILE.name, bytes(room * 3 // 4))

    with pytest.raises(CapacityError, match=r"over the advised \d+\.\d KiB for this carrier$"):
        service.hide(cover, payload, tmp_path / "out.png", OPTIONS)

    assert not (tmp_path / "out.png").exists()
    service.hide(cover, payload, tmp_path / "out.png", RESEARCH)

    assert tmp_path.joinpath("out.png").exists()


def test_standard_hide_refuses_an_off_cipher(make_image: ImageFactory, tmp_path: Path) -> None:
    fernet = HideOptions(cipher=CipherId.FERNET, password=STRONG)

    with pytest.raises(NotAvailableError, match="Off in Standard mode"):
        service.hide(make_image(), FILE, tmp_path / "out.png", fernet)


def test_standard_payload_reveals_like_any_other(make_image: ImageFactory, tmp_path: Path) -> None:
    service.hide(make_image(size=(320, 240)), FILE, tmp_path / "out.png", OPTIONS)

    assert service.reveal(tmp_path / "out.png", tmp_path, STRONG).path.read_bytes() == FILE.data


def test_standard_options_pick_an_allowed_method(make_image: ImageFactory, tmp_path: Path) -> None:
    grey = make_image("grey.bmp", mode="L")

    with pytest.raises(NotAvailableError, match="Off in Standard mode: "):
        service.hide_options(grey, tmp_path / "out.png", CipherId.AES_GCM)

    skip_unless_method_installed(MethodId.DCT)
    precover = make_image("grey.png", mode="L")
    options = service.hide_options(precover, tmp_path / "out.jpg", CipherId.AES_GCM)

    assert options.method == MethodId.DCT

    jpeg = make_image("cover.jpg")
    options = service.hide_options(jpeg, tmp_path / "out.jpg", CipherId.AES_GCM)

    assert (options.method, options.is_research) == (MethodId.DCT, False)

    with pytest.raises(UnsupportedFormatError, match=r"Output must be one of: \.jpg, \.jpeg$"):
        service.hide_options(jpeg, tmp_path / "out.png", CipherId.AES_GCM)


def test_hide_file_is_standard_unless_asked(make_image: ImageFactory, tmp_path: Path) -> None:
    secret = tmp_path / "note.txt"
    secret.write_bytes(FILE.data)
    cover = make_image(size=(320, 240))

    with pytest.raises(CipherStitchError, match=r"^Standard mode needs a strong password"):
        service.hide_file(cover, secret, tmp_path / "out.png")

    with pytest.raises(CipherStitchError, match=r"^The Research parameters need a strong"):
        service.hide_file(cover, secret, tmp_path / "weak.png", "s3cret", research=True)

    service.hide_file(cover, secret, tmp_path / "plain.png", research=True)
    service.hide_file(cover, secret, tmp_path / "out.png", STRONG)

    assert service.reveal_file(tmp_path / "plain.png", tmp_path / "a").read_bytes() == FILE.data
    assert service.reveal_file(tmp_path / "out.png", tmp_path / "b", STRONG).read_bytes() == (
        FILE.data
    )


@pytest.fixture
def hidden(make_image: ImageFactory, tmp_path: Path) -> Path:
    """FILE hidden with image LSB and ChaCha20-Poly1305 at Research's default settings."""
    output = tmp_path / "out.png"
    options = replace(RESEARCH, cipher=CipherId.CHACHA20)
    service.hide(make_image(size=(320, 240)), FILE, output, options)

    return output


def test_research_reveal_tries_only_the_given_method_and_cipher(
    hidden: Path, tmp_path: Path
) -> None:
    same = ResearchReveal(MethodId.LSB, CipherId.CHACHA20)
    result = service.reveal(hidden, tmp_path / "a", STRONG, research=same)

    assert (result.path.read_bytes(), result.method, result.cipher) == (
        FILE.data,
        MethodId.LSB,
        CipherId.CHACHA20,
    )
    assert service.reveal(hidden, tmp_path / "b", STRONG).path.read_bytes() == FILE.data

    for other in (
        ResearchReveal(MethodId.LSB, CipherId.AES_GCM),
        ResearchReveal(MethodId.LSB, CipherId.NONE),
        ResearchReveal(MethodId.LSB, CipherId.CHACHA20, Settings.of({"stc.h": 4})),
    ):
        with pytest.raises(NoHiddenDataError, match=r"^Couldn't reveal anything"):
            service.reveal(hidden, tmp_path / "c", STRONG, research=other)


def test_research_reveal_names_a_method_the_file_type_cannot_hold(
    hidden: Path, tmp_path: Path
) -> None:
    skip_unless_method_installed(MethodId.ECHO_HIDING)  # else the missing extra is the reason
    echo = ResearchReveal(MethodId.ECHO_HIDING, CipherId.CHACHA20)

    with pytest.raises(UnsupportedFormatError, match=r"^\.png files can't hold a payload hidden"):
        service.reveal(hidden, tmp_path, STRONG, research=echo)


def test_research_reveal_needs_the_ciphers_extra(
    hidden: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(catalog.Requirement, "is_installed", property(lambda _self: False))
    xchacha = ResearchReveal(MethodId.LSB, CipherId.XCHACHA20)

    with pytest.raises(NotAvailableError, match=r"needs the crypto-extra extra, see Installation"):
        service.reveal(hidden, tmp_path, STRONG, research=xchacha)


def test_hide_options_choose_and_check_with_the_research_settings(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    cover, output = make_image("photo.jpg", size=(320, 240)), tmp_path / "out.png"
    lossy = Settings.of({"limg.lossy": True})

    with pytest.raises((UnsupportedFormatError, NotAvailableError)):  # DCT, or nothing
        service.hide_options(cover, output, CipherId.CHACHA20, is_research=True)

    options = service.hide_options(
        cover, output, CipherId.CHACHA20, is_research=True, settings=lossy
    )

    assert (options.method, options.settings) == (MethodId.LSB, lossy)


def test_research_reveal_names_an_unimplemented_method(
    hidden: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(methods._EMBEDDINGS, MethodId.LSB_REPLACEMENT)
    research = ResearchReveal(MethodId.LSB_REPLACEMENT, CipherId.CHACHA20)

    with pytest.raises(NotAvailableError, match=r"is not available yet$"):
        service.reveal(hidden, tmp_path, STRONG, research=research)
