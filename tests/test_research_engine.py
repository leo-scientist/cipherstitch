"""Research settings in the engine: Format values must match on Reveal, Embedder values never
matter to it, and Standard's settings run the engine's own defaults."""

import unicodedata
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, PngImagePlugin

from cipherstitch import kdf, methods, risks, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.container import UnitOrder
from cipherstitch.errors import CapacityError, NoHiddenDataError
from cipherstitch.media import audio
from cipherstitch.media.audio import Audio, AudioInfo
from cipherstitch.methods import lsb, mipod, spatial_costs, stc, trellis
from cipherstitch.methods.feistel import UNKEYED_SEED
from cipherstitch.methods.zero_width import SYMBOL_SETS, SYMBOLS, ZeroWidthRules
from cipherstitch.params import engine
from cipherstitch.params.model import Value
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import ResearchReveal

from .conftest import skip_unless_method_installed

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
FILE = HiddenFile("note.txt", bytes(range(256)) + b"traces" * 30)
# A tail spread over fewer positions per bit than the carrier has: stc.tail then matters.
SHORT_FILE = HiddenFile("n.txt", b"traces" * 6)
SIDE = 128  # 49 152 values: a tail long enough for shorter STC segments to matter
FRAMES = 12000
SEQUENTIAL = Settings.of({"frame.order": "sequential"})
BINARY = Settings.of({"stc.code": "binary"})
PAYLOAD_RATE = 0.1  # message bits per unit, which only MiPOD's costs read

# Changes a reader must know of. stc.maxseg is left out: it matters only above 4096 segments
# of 32 768 units, far more than a test cover has.
FORMAT_CHANGES: list[tuple[str, Value]] = [
    ("crypto.kdf", "scrypt"),
    ("crypto.kdf", "pbkdf2"),
    ("crypto.iter", 2),
    ("crypto.lanes", 2),
    ("frame.rounds", 5),
    ("stc.h", 4),
    ("stc.cols", 5),
    ("stc.seg", 8192),
    ("stc.spread", "sequential"),
    ("stc.code", "binary"),
    ("stc.lead", 1024),
    ("stc.head", 4096),
    ("stc.tail", 64),
]
EMBEDDER_CHANGES: list[tuple[str, Value]] = [
    ("frame.order", "public"),
    ("stc.kept", 4),
    ("stc.fill", 60),
    ("stc.split", "even"),
    ("stc.retry", False),
    ("limg.cost", "uniform"),
    ("limg.cost", "wow"),
    ("limg.cost", "s-uniward"),
    ("limg.cost", "mipod"),
    ("limg.far", 9),
    ("limg.far", 21),
    ("limg.flat", 8),
    ("limg.wet", False),
    ("limg.meta", False),
    ("crypto.minpw", 4),
]


@pytest.fixture
def cover(tmp_path: Path) -> Path:
    rng = np.random.default_rng(seed=11)
    pixels = rng.integers(0, 256, size=(SIDE, SIDE, 3), dtype=np.uint8)
    pixels[:8] = 200  # flat rows
    pixels[20, :, 0] = np.repeat(np.arange(13, dtype=np.uint8), 10)[:SIDE]  # runs of ten
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "a cover's own text")
    path = tmp_path / "cover.png"
    Image.fromarray(pixels).save(path, pnginfo=info)

    return path


def _hide(
    cover: Path, output: Path, settings: Settings, cipher: CipherId, file: HiddenFile = FILE
) -> None:
    password = None if cipher == CipherId.NONE else PASSWORD
    options = service.HideOptions(
        CarrierKind.IMAGE, MethodId.LSB, cipher, password, settings=settings, is_research=True
    )
    service.hide(cover, file, output, options)


def _reveal(path: Path, tmp_path: Path, research: ResearchReveal | None = None) -> bytes:
    out = tmp_path / "out"
    result = service.reveal(path, out, PASSWORD, research=research)
    data = result.path.read_bytes()
    result.path.unlink()

    return data


def _image_channel(cover: Path, settings: Settings) -> lsb.ImageChannel:
    """Opened the way hiding opens it."""
    embedding = methods.get(MethodId.LSB)
    assert embedding is not None
    channel = embedding.open(cover, engine.tuning(settings))
    assert isinstance(channel, lsb.ImageChannel)

    return channel


def _audio_channel(cover: Path, settings: Settings) -> lsb.AudioChannel:
    embedding = methods.get(MethodId.AUDIO_LSB)
    assert embedding is not None
    channel = embedding.open(cover, engine.tuning(settings))
    assert isinstance(channel, lsb.AudioChannel)

    return channel


def test_standard_settings_run_the_engines_defaults() -> None:
    assert engine.tuning(Settings()) == methods.STANDARD
    assert engine.kdf_profile(Settings()) == kdf.standard_profile()
    assert engine.is_masked(Settings())
    assert engine.unit_order(Settings()) is UnitOrder.KEYED


@pytest.mark.parametrize("order", list(UnitOrder))
def test_every_unit_order_is_a_setting(order: UnitOrder) -> None:
    assert engine.unit_order(Settings.of({"frame.order": order.value})) is order


@pytest.mark.parametrize(("name", "value"), FORMAT_CHANGES, ids=lambda item: str(item))
def test_a_format_change_needs_the_same_settings_to_reveal(
    cover: Path, tmp_path: Path, name: str, value: Value
) -> None:
    settings = Settings.of({name: value})
    embedded = tmp_path / "embedded.png"
    file = SHORT_FILE if name == "stc.tail" else FILE
    _hide(cover, embedded, settings, CipherId.AES_GCM, file)

    research = ResearchReveal(MethodId.LSB, CipherId.AES_GCM, settings)

    assert _reveal(embedded, tmp_path, research) == file.data

    with pytest.raises(NoHiddenDataError):
        _reveal(embedded, tmp_path)


@pytest.mark.parametrize(("name", "value"), EMBEDDER_CHANGES, ids=lambda item: str(item))
def test_an_embedder_change_reveals_with_standard_settings(
    cover: Path, tmp_path: Path, name: str, value: Value
) -> None:
    embedded = tmp_path / "embedded.png"
    _hide(cover, embedded, Settings.of({name: value}), CipherId.AES_GCM)

    assert _reveal(embedded, tmp_path) == FILE.data


@pytest.mark.parametrize("cipher", [CipherId.NONE, CipherId.AES_GCM])
def test_mipod_costs_hide_what_both_modes_reveal(
    cover: Path, tmp_path: Path, cipher: CipherId
) -> None:
    """MiPOD's costs depend on the payload, but reading needs nothing of them. The flat rows
    stay as they were."""
    settings = Settings.of({"limg.cost": "mipod"})
    embedded = tmp_path / "embedded.png"
    _hide(cover, embedded, settings, cipher)
    password = None if cipher == CipherId.NONE else PASSWORD

    for research in (ResearchReveal(MethodId.LSB, cipher, settings), None):
        result = service.reveal(embedded, tmp_path / "out", password, research=research)

        assert result.path.read_bytes() == FILE.data

        result.path.unlink()

    with Image.open(cover) as before, Image.open(embedded) as after:
        pixels, changed = np.asarray(before), np.asarray(after)

    assert np.array_equal(changed[:8], pixels[:8])
    assert not np.array_equal(changed, pixels)


@pytest.mark.parametrize(
    ("name", "value"), [("frame.rounds", 5), ("stc.h", 5), ("frame.mask", "off")]
)
def test_a_format_change_applies_without_encryption_too(
    cover: Path, tmp_path: Path, name: str, value: Value
) -> None:
    settings = Settings.of({name: value})
    embedded = tmp_path / "embedded.png"
    _hide(cover, embedded, settings, CipherId.NONE)
    out = tmp_path / "out"
    research = ResearchReveal(MethodId.LSB, CipherId.NONE, settings)

    assert service.reveal(embedded, out, research=research).path.read_bytes() == FILE.data

    with pytest.raises(NoHiddenDataError):
        service.reveal(embedded, tmp_path / "other")


def test_format_settings_reach_the_channel(cover: Path) -> None:
    changes: dict[str, Value] = {
        "stc.h": 5,
        "stc.cols": 7,
        "stc.seg": 8192,
        "stc.maxseg": 512,
        "stc.lead": 2048,
        "stc.head": 8192,
        "stc.tail": 128,
        "stc.spread": "sequential",
        "stc.code": "binary",
        "frame.rounds": 4,
    }
    code = stc.Code(height=5, label_number=7, units_per_segment=8192, max_segments=512)
    expected = trellis.Format(code, 2048, 8192, 128, rounds=4, is_spread=False, is_ternary=False)

    assert _image_channel(cover, Settings.of(changes)).regions.format == expected


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("stc.kept", 4, trellis.Writer(kept_per_bit=4)),
        ("stc.fill", 60, trellis.Writer(fill_percent=60)),
        ("stc.split", "even", trellis.Writer(is_split_ideal=False)),
        ("stc.retry", False, trellis.Writer(can_retry=False)),
    ],
)
def test_writer_settings_reach_the_channel(
    cover: Path, name: str, value: Value, expected: trellis.Writer
) -> None:
    assert _image_channel(cover, Settings.of({name: value}))._writer == expected


def test_image_rules_reach_the_costs(cover: Path) -> None:
    standard = _image_channel(cover, Settings())
    costs = standard._costs(PAYLOAD_RATE)
    hill_costs = standard._value_costs(PAYLOAD_RATE)

    def channel(name: str, value: Value) -> lsb.ImageChannel:
        return _image_channel(cover, Settings.of({name: value}))

    assert np.any(costs == lsb.WET_COST)
    assert not np.any(channel("limg.wet", False)._costs(PAYLOAD_RATE) == lsb.WET_COST)
    assert np.all(channel("limg.cost", "uniform")._value_costs(PAYLOAD_RATE) == 1)
    assert not np.allclose(channel("limg.far", 9)._value_costs(PAYLOAD_RATE), hill_costs)
    assert channel("limg.flat", 8).flat_share() > standard.flat_share()

    with Image.open(cover) as opened:
        colors = np.asarray(opened.convert("RGB"))

    for name, function in [
        ("wow", spatial_costs.wow_costs),
        ("s-uniward", spatial_costs.suniward_costs),
        ("mipod", lambda colors: mipod.image_costs(colors, PAYLOAD_RATE)),
    ]:
        expected = function(colors).reshape(-1)
        computed = channel("limg.cost", name)._value_costs(PAYLOAD_RATE)

        np.testing.assert_array_equal(computed, expected)


def test_audio_rules_reach_the_channel(tmp_path: Path) -> None:
    """A run of 20 zeros across a 512-sample boundary, and runs of nine zeros."""
    rng = np.random.default_rng(seed=4)
    samples = rng.integers(-(1 << 15), 1 << 15, size=(FRAMES, 1), dtype=np.int32)
    samples[500:520] = 0
    samples[2000:2900] = 0
    samples[2000:2900:10] = 5
    cover = tmp_path / "cover.wav"
    audio.write(cover, Audio(AudioInfo(FRAMES, 1, 8000, 16), samples))
    standard = _audio_channel(cover, Settings()).flat_share()

    assert standard == 20 / FRAMES
    assert _audio_channel(cover, Settings.of({"laud.row": 512})).flat_share() == 0
    assert _audio_channel(cover, Settings.of({"laud.flat": 8})).flat_share() > 0.05


def test_without_metadata_the_output_keeps_only_the_pixels(cover: Path, tmp_path: Path) -> None:
    kept, bare = tmp_path / "kept.png", tmp_path / "bare.png"
    _hide(cover, kept, Settings(), CipherId.AES_GCM)
    _hide(cover, bare, Settings.of({"limg.meta": False}), CipherId.AES_GCM)

    with Image.open(kept) as with_text, Image.open(bare) as without:
        assert with_text.info.get("Comment") == "a cover's own text"
        assert "Comment" not in without.info


def test_unnormalized_passwords_differ_by_composition() -> None:
    salt = b"s" * kdf.SALT_SIZE
    composed, decomposed = "й", unicodedata.normalize("NFD", "й")
    plain = kdf.Profile(32, 1, 1, is_normalized=False)
    normalized = kdf.Profile(32, 1, 1)

    assert kdf.master_key(composed, salt, normalized) == kdf.master_key(
        decomposed, salt, normalized
    )
    assert kdf.master_key(composed, salt, plain) != kdf.master_key(decomposed, salt, plain)


@pytest.mark.parametrize(("name", "value"), [("laud.row", 1024), ("laud.flat", 6)])
def test_audio_rules_are_embedder_settings(tmp_path: Path, name: str, value: Value) -> None:
    rng = np.random.default_rng(seed=2)
    samples = rng.integers(-(1 << 15), 1 << 15, size=(FRAMES, 2), dtype=np.int32)
    samples[:3000] = 0
    cover, embedded = tmp_path / "cover.wav", tmp_path / "embedded.wav"
    audio.write(cover, Audio(AudioInfo(FRAMES, 2, 8000, 16), samples))
    settings = Settings.of({name: value})
    options = service.HideOptions(
        CarrierKind.AUDIO,
        MethodId.AUDIO_LSB,
        CipherId.AES_GCM,
        PASSWORD,
        settings=settings,
        is_research=True,
    )
    service.hide(cover, FILE, embedded, options)

    assert _reveal(embedded, tmp_path) == FILE.data


def _photo(path: Path) -> Path:
    rng = np.random.default_rng(seed=1)
    gradient = np.linspace(0, 255, 320)[None, :] + np.linspace(0, 255, 240)[:, None]
    pixels = np.clip(gradient[..., None] / 2 + rng.normal(0, 30, (240, 320, 3)), 0, 255)
    Image.fromarray(pixels.astype(np.uint8)).save(path, quality=85)

    return path


def _noise(path: Path) -> Path:
    rng = np.random.default_rng(seed=2)
    samples = rng.integers(-(1 << 15), 1 << 15, size=(FRAMES, 2), dtype=np.int32)
    audio.write(path, Audio(AudioInfo(FRAMES, 2, 8000, 16), samples))

    return path


@pytest.mark.parametrize(
    ("carrier", "method", "make", "suffix"),
    [
        (CarrierKind.AUDIO, MethodId.AUDIO_LSB, _noise, ".wav"),
        (CarrierKind.IMAGE, MethodId.DCT, _photo, ".jpg"),
    ],
    ids=["audio-lsb", "dct"],
)
def test_other_trellis_channels_read_the_format_too(
    tmp_path: Path,
    carrier: CarrierKind,
    method: MethodId,
    make: Callable[[Path], Path],
    suffix: str,
) -> None:
    skip_unless_method_installed(method)
    cover = make(tmp_path / f"cover{suffix}")
    embedded = tmp_path / f"embedded{suffix}"
    settings = Settings.of({"stc.h": 5})
    options = service.HideOptions(
        carrier, method, CipherId.AES_GCM, PASSWORD, settings=settings, is_research=True
    )
    service.hide(cover, SHORT_FILE, embedded, options)

    research = ResearchReveal(method, CipherId.AES_GCM, settings)

    assert _reveal(embedded, tmp_path, research) == SHORT_FILE.data

    with pytest.raises(NoHiddenDataError):
        _reveal(embedded, tmp_path)


@pytest.mark.parametrize(
    ("name", "value", "cipher"),
    [
        ("frame.rounds", 4, CipherId.AES_GCM),
        ("zw.sym", 2, CipherId.AES_GCM),
        ("zw.sym", 2, CipherId.NONE),
        ("zw.sym", 8, CipherId.AES_GCM),
        ("zw.sym", 8, CipherId.NONE),
        ("zw.rules", "relaxed", CipherId.AES_GCM),
        ("zw.rules", "relaxed", CipherId.NONE),
    ],
    ids=lambda item: str(item),
)
def test_a_zero_width_format_change_needs_the_same_settings_to_reveal(
    tmp_path: Path, name: str, value: Value, cipher: CipherId
) -> None:
    cover, embedded = tmp_path / "cover.txt", tmp_path / "embedded.txt"
    cover.write_text(" ".join(["a manuscript keeps traces"] * 400), encoding="utf-8")
    settings = Settings.of({name: value})
    secret = HiddenFile("n.txt", b"short note")
    password = None if cipher == CipherId.NONE else PASSWORD
    options = service.HideOptions(
        CarrierKind.TEXT,
        MethodId.ZERO_WIDTH,
        cipher,
        password,
        settings=settings,
        is_research=True,
    )
    service.hide(cover, secret, embedded, options)
    out = tmp_path / "out"
    research = ResearchReveal(MethodId.ZERO_WIDTH, cipher, settings)

    assert service.reveal(embedded, out, password, research=research).path.read_bytes() == (
        b"short note"
    )

    with pytest.raises(NoHiddenDataError):
        service.reveal(embedded, tmp_path / "other", password)


def test_zero_width_settings_reach_the_channel() -> None:
    settings = Settings.of({"zw.sym": 8, "zw.rules": "relaxed"})

    assert engine.tuning(settings).text == ZeroWidthRules(SYMBOL_SETS[8], is_relaxed=True)
    assert engine.tuning(Settings.of({"zw.sym": 2})).text == ZeroWidthRules(SYMBOL_SETS[2])
    assert engine.tuning(Settings()).text == ZeroWidthRules(SYMBOLS)


@pytest.mark.parametrize(
    ("carrier", "method", "make", "suffix"),
    [
        (CarrierKind.IMAGE, MethodId.LSB, None, ".png"),
        (CarrierKind.AUDIO, MethodId.AUDIO_LSB, _noise, ".wav"),
        (CarrierKind.IMAGE, MethodId.DCT, _photo, ".jpg"),
    ],
    ids=["lsb", "audio-lsb", "dct"],
)
def test_only_research_reveals_a_sequential_payload(
    cover: Path,
    tmp_path: Path,
    carrier: CarrierKind,
    method: MethodId,
    make: Callable[[Path], Path] | None,
    suffix: str,
) -> None:
    """Research's reveal tries every order at any settings, Standard's not the sequential
    one."""
    skip_unless_method_installed(method)
    source = cover if make is None else make(tmp_path / f"cover{suffix}")
    embedded = tmp_path / f"embedded{suffix}"
    options = service.HideOptions(
        carrier, method, CipherId.AES_GCM, PASSWORD, settings=SEQUENTIAL, is_research=True
    )
    service.hide(source, SHORT_FILE, embedded, options)
    research = ResearchReveal(method, CipherId.AES_GCM, Settings())

    assert _reveal(embedded, tmp_path, research) == SHORT_FILE.data

    with pytest.raises(NoHiddenDataError):
        _reveal(embedded, tmp_path)


@pytest.mark.parametrize(("spread", "is_at_start"), [("sequential", True), ("golden", False)])
def test_without_spreading_a_sequential_payload_fills_the_carrier_from_its_start(
    cover: Path, tmp_path: Path, spread: str, is_at_start: bool
) -> None:
    """A short head and tail keep the payload's units in the first half of the values."""
    changes: dict[str, Value] = {
        "frame.order": "sequential",
        "stc.spread": spread,
        "stc.head": 4096,
        "stc.tail": 64,
    }
    settings = Settings.of(changes)
    embedded = tmp_path / "embedded.png"
    _hide(cover, embedded, settings, CipherId.AES_GCM, SHORT_FILE)

    with Image.open(cover) as before, Image.open(embedded) as after:
        changed = np.flatnonzero(np.asarray(before) != np.asarray(after))

    research = ResearchReveal(MethodId.LSB, CipherId.AES_GCM, settings)

    assert _reveal(embedded, tmp_path, research) == SHORT_FILE.data
    assert bool(changed.max() < SIDE * SIDE * 3 // 2) is is_at_start


def _precover(path: Path) -> Path:
    """A PNG, which DCT compresses to JPEG itself."""
    rng = np.random.default_rng(seed=3)
    gradient = np.linspace(0, 255, 160)[None, :] + np.linspace(0, 255, 120)[:, None]
    pixels = np.clip(gradient[..., None] / 2 + rng.normal(0, 12, (120, 160, 3)), 0, 255)
    Image.fromarray(pixels.astype(np.uint8)).save(path)

    return path


@pytest.mark.parametrize("cipher", [CipherId.AES_GCM, CipherId.NONE], ids=["encrypted", "plain"])
@pytest.mark.parametrize(
    ("carrier", "method", "make", "suffix", "output"),
    [
        (CarrierKind.IMAGE, MethodId.LSB, None, ".png", ".png"),
        (CarrierKind.AUDIO, MethodId.AUDIO_LSB, _noise, ".wav", ".wav"),
        (CarrierKind.IMAGE, MethodId.DCT, _photo, ".jpg", ".jpg"),
        (CarrierKind.IMAGE, MethodId.DCT, _precover, ".png", ".jpg"),
    ],
    ids=["lsb", "audio-lsb", "dct", "dct-precover"],
)
def test_binary_coding_round_trips_only_with_its_own_setting(
    cover: Path,
    tmp_path: Path,
    cipher: CipherId,
    carrier: CarrierKind,
    method: MethodId,
    make: Callable[[Path], Path] | None,
    suffix: str,
    output: str,
) -> None:
    """Every trellis channel, with and without encryption: ternary settings read nothing of a
    binary payload."""
    skip_unless_method_installed(method)
    source = cover if make is None else make(tmp_path / f"cover{suffix}")
    embedded = tmp_path / f"embedded{output}"
    password = None if cipher == CipherId.NONE else PASSWORD
    options = service.HideOptions(
        carrier, method, cipher, password, settings=BINARY, is_research=True
    )
    service.hide(source, SHORT_FILE, embedded, options)
    revealed = service.reveal(
        embedded, tmp_path / "out", password, research=ResearchReveal(method, cipher, BINARY)
    )

    assert revealed.path.read_bytes() == SHORT_FILE.data

    with pytest.raises(NoHiddenDataError):
        service.reveal(
            embedded,
            tmp_path / "other",
            password,
            research=ResearchReveal(method, cipher, Settings()),
        )


def test_a_ternary_payload_is_not_read_with_binary_coding(cover: Path, tmp_path: Path) -> None:
    embedded = tmp_path / "embedded.png"
    _hide(cover, embedded, Settings(), CipherId.AES_GCM)

    with pytest.raises(NoHiddenDataError):
        _reveal(embedded, tmp_path, ResearchReveal(MethodId.LSB, CipherId.AES_GCM, BINARY))


def test_a_sequential_zero_width_payload_is_in_the_public_order(tmp_path: Path) -> None:
    """Zero-width keeps its units in sequence without a keyed order, so Standard's reveal
    finds it."""
    cover, embedded = tmp_path / "cover.txt", tmp_path / "embedded.txt"
    cover.write_text(" ".join(["a manuscript keeps traces"] * 400), encoding="utf-8")
    secret = HiddenFile("n.txt", b"short note")
    options = service.HideOptions(
        CarrierKind.TEXT,
        MethodId.ZERO_WIDTH,
        CipherId.AES_GCM,
        PASSWORD,
        settings=SEQUENTIAL,
        is_research=True,
    )
    service.hide(cover, secret, embedded, options)
    research = ResearchReveal(MethodId.ZERO_WIDTH, CipherId.AES_GCM, Settings())

    assert _reveal(embedded, tmp_path, research) == b"short note"
    assert _reveal(embedded, tmp_path) == b"short note"


def test_the_capacity_follows_the_fill(cover: Path) -> None:
    standard = service.estimate(cover, "n", 1, service.HideOptions(is_research=True))
    fuller = service.estimate(
        cover, "n", 1, service.HideOptions(settings=Settings.of({"stc.fill": 95}), is_research=True)
    )

    assert standard.available is not None
    assert fuller.available is not None
    assert fuller.available > standard.available


def _runs_cover(path: Path) -> Path:
    """Rows 0 … 39 in runs of ten equal values per colour, rows 40 … 44 flat, noise below."""
    rng = np.random.default_rng(seed=12)
    pixels = rng.integers(0, 256, size=(SIDE, SIDE, 3), dtype=np.uint8)
    runs = rng.integers(0, 256, size=(40, SIDE // 10 + 1, 3), dtype=np.uint8)
    pixels[:40] = np.repeat(runs, 10, axis=1)[:, :SIDE]
    pixels[40:45] = 90
    Image.fromarray(pixels).save(path)

    return path


def test_the_flat_warning_follows_the_tuning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each tuning opens the cover with its own rules, cached apart."""
    cover = _runs_cover(tmp_path / "runs.png")
    monkeypatch.setattr(lsb, "flat_blocks", lambda *_args: 1.0)

    def flat_risk(settings: Settings) -> str:
        options = service.HideOptions(settings=settings, is_research=True)
        estimate = service.estimate(cover, "n.txt", 100, options)
        found = [r for r in service.detection_risks(options, cover, estimate) if "flat" in r]
        assert len(found) == 1

        return found[0]

    assert flat_risk(Settings()).startswith("4% of the image is flat colour")
    assert flat_risk(Settings.of({"limg.flat": 8})).startswith("35% of the image is flat colour")


@pytest.mark.parametrize("costs", ["uniform", "wow", "s-uniward", "mipod"])
def test_costs_other_than_hills_without_wet_ones_warn_of_any_flat_part(
    tmp_path: Path, costs: str
) -> None:
    """Flat values then cost too little to keep every change off them (only HILL's are
    about 1 / ε there), so the code may change them."""
    cover = _runs_cover(tmp_path / "runs.png")
    careless = engine.tuning(Settings.of({"limg.cost": costs, "limg.wet": False}))
    dry_hill = engine.tuning(Settings.of({"limg.wet": False}))

    assert risks.flat_cover(MethodId.LSB, cover) is None
    assert risks.flat_cover(MethodId.LSB, cover, tuning=dry_hill) is None
    assert (
        risks.flat_cover(MethodId.LSB, cover, tuning=careless) == "4% of the image is flat colour"
    )


@pytest.mark.parametrize(("value", "is_even"), [("even", True), ("ideal", False)])
def test_the_split_setting_is_what_the_head_stores(
    cover: Path, tmp_path: Path, value: str, is_even: bool
) -> None:
    embedded = tmp_path / "embedded.png"
    _hide(cover, embedded, Settings.of({"stc.split": value}), CipherId.NONE)

    with Image.open(embedded) as image:
        values = np.asarray(image)[..., :3].reshape(-1)

    head = trellis.Units(values, trellis.Regions.of(values.size))._head(UNKEYED_SEED)[1]
    split = trellis._FIELDS.unpack_from(head)[1]

    assert (split == trellis.EVEN_SPLIT) is is_even


def test_the_search_keeps_as_many_units_as_set(
    cover: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept: list[int] = []
    prune = stc.prune

    def recorded(layout: stc.Layout, costs: stc.Costs, kept_per_bit: int) -> stc.Units:
        kept.append(kept_per_bit)

        return prune(layout, costs, kept_per_bit)

    monkeypatch.setattr(stc, "prune", recorded)
    _hide(cover, tmp_path / "embedded.png", Settings.of({"stc.kept": 4}), CipherId.AES_GCM)

    assert kept
    assert set(kept) == {4}


@pytest.mark.parametrize(
    ("changes", "tries"),
    [
        ({"stc.retry": True}, 7),
        ({"stc.retry": False}, 1),
        ({"stc.retry": True, "stc.code": "binary"}, 1),  # binary coding has no split to move
    ],
)
def test_retrying_is_a_setting(
    cover: Path, monkeypatch: pytest.MonkeyPatch, changes: dict[str, Value], tries: int
) -> None:
    calls: list[int] = []

    def unsolvable(*_args: object) -> trellis.Steps:
        calls.append(1)
        raise stc.UnsolvableError

    channel = _image_channel(cover, Settings.of(changes))
    monkeypatch.setattr(trellis.TrellisChannel, "_steps", unsolvable)

    with pytest.raises(CapacityError):
        channel.write(bytes(16), b"body", None)

    assert len(calls) == tries


def test_kdf_settings_reach_the_profile() -> None:
    changes: dict[str, Value] = {
        "crypto.kdf": "scrypt",
        "crypto.mem": 64 * 1024,
        "crypto.iter": 3,
        "crypto.lanes": 2,
        "crypto.nfc": False,
    }
    expected = kdf.Profile(64 * 1024, 3, 2, False, kdf.Function.SCRYPT)

    assert engine.kdf_profile(Settings.of(changes)) == expected
