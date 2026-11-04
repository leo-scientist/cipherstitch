"""Research settings of DCT, the precover, phase coding, echo hiding and the documents: a Format
change needs the same settings to reveal, an Embedder change reveals with Standard's."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageCms, JpegImagePlugin

from cipherstitch import methods, risks, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.container import Channel
from cipherstitch.errors import CapacityError, NoHiddenDataError
from cipherstitch.media import audio, docx, jpeg, pdf
from cipherstitch.media.audio import Audio, AudioInfo
from cipherstitch.methods import Embedded, dct, ecc, echo, object_stream, phase, uerd, whitespace
from cipherstitch.methods.echo import EchoChannel
from cipherstitch.methods.object_stream import ObjectRules
from cipherstitch.methods.phase import PhaseChannel
from cipherstitch.params import engine
from cipherstitch.params.model import Value
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import ResearchReveal

from .conftest import skip_unless_method_installed

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
SMALL = HiddenFile("n.txt", b"traces" * 5)  # encrypted, it fits one codeword
FILE = HiddenFile("note.txt", bytes(range(256)) + b"traces" * 30)
RATE = 22050  # echo hiding carries ~40 bits a second: a minute holds one codeword
SECONDS = 60
PAYLOAD_RATE = 0.1  # message bits per unit, which DCT's costs never read
PARAGRAPHS = 40
KIB = 1024

AUDIO_FORMAT_CHANGES: list[tuple[MethodId, str, Value]] = [
    (MethodId.PHASE_CODING, "phase.seg", 2048),
    (MethodId.PHASE_CODING, "phase.first", 16),
    (MethodId.PHASE_CODING, "phase.bins", 32),
    (MethodId.PHASE_CODING, "phase.rs", 16),
    (MethodId.ECHO_HIDING, "echo.seg", 20),
    (MethodId.ECHO_HIDING, "echo.d0", 0.8),
    (MethodId.ECHO_HIDING, "echo.d1", 2.0),
    (MethodId.ECHO_HIDING, "echo.rs", 16),
]
AUDIO_EMBEDDER_CHANGES: list[tuple[MethodId, str, Value]] = [
    (MethodId.PHASE_CODING, "phase.ramp", 2),
    (MethodId.PHASE_CODING, "phase.floor", 16),
    (MethodId.ECHO_HIDING, "echo.decay", 0.3),
]
JPEG_EMBEDDER_CHANGES: list[tuple[str, Value]] = [
    ("dct.cost", "uniform"),
    ("dct.cost", "uerd"),
    ("dct.sigma", 2.0**-4),
    ("dct.sigma", 2.0**-8),
    ("dct.pol", 1.0),
    ("dct.pol", 0.0),
    ("dct.mag", 300),
    ("dct.huff", "standard"),
    ("dct.huff", "optimized"),
    ("dct.app", False),
]
PRECOVER_EMBEDDER_CHANGES: list[tuple[str, Value]] = [
    ("pre.q", 75),
    ("pre.sub", "4:2:2"),
    ("pre.sub", "4:4:4"),
    ("pre.si", False),
    ("pre.icc", False),
    ("dct.cost", "uniform"),
    ("dct.cost", "uerd"),
    ("dct.mag", 300),
]


@pytest.fixture(scope="module")
def music(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A minute of quiet chords, slowly louder and softer: echo hiding needs that long."""
    path = tmp_path_factory.mktemp("audio") / "music.wav"
    rng = np.random.default_rng(seed=RATE)
    t = np.arange(SECONDS * RATE) / RATE
    tones = sum(
        np.sin(2 * np.pi * frequency * t + phase) * amplitude
        for frequency, phase, amplitude in zip(
            rng.uniform(80, 2000, 12),
            rng.uniform(0, 6.3, 12),
            rng.uniform(0.02, 0.08, 12),
            strict=True,
        )
    )
    mono = tones * (0.6 + 0.4 * np.sin(2 * np.pi * 0.3 * t)) + rng.normal(0, 0.01, t.size)
    samples = np.stack([mono, mono * 0.9], axis=1) * 26000
    audio.write(path, Audio(AudioInfo(t.size, 2, RATE, 16), samples.round().astype(np.int32)))

    return path


def _photo(path: Path, **options: Any) -> Path:
    rng = np.random.default_rng(seed=1)
    gradient = np.linspace(0, 255, 320)[None, :] + np.linspace(0, 255, 240)[:, None]
    pixels = np.clip(gradient[..., None] / 2 + rng.normal(0, 30, (240, 320, 3)), 0, 255)
    Image.fromarray(pixels.astype(np.uint8)).save(path, **options)

    return path


def _docx(path: Path) -> Path:
    python_docx = pytest.importorskip("docx")
    document = python_docx.Document()

    for index in range(PARAGRAPHS):
        document.add_paragraph(f"Paragraph {index}: a manuscript keeps traces.")

    document.save(str(path))

    return path


def _pdf(path: Path) -> Path:
    pikepdf = pytest.importorskip("pikepdf")
    document = pikepdf.new()
    document.add_blank_page()
    document.save(path)

    return path


DOCUMENTS = {".docx": (_docx, docx.read_part), ".pdf": (_pdf, pdf.read_data)}


def _hide(
    cover: Path,
    output: Path,
    method: MethodId,
    settings: Settings,
    file: HiddenFile = SMALL,
) -> None:
    carrier = {
        MethodId.DCT: CarrierKind.IMAGE,
        MethodId.PHASE_CODING: CarrierKind.AUDIO,
        MethodId.ECHO_HIDING: CarrierKind.AUDIO,
        MethodId.OBJECT_STREAM: CarrierKind.DOCUMENT,
        MethodId.WHITESPACE: CarrierKind.DOCUMENT,
    }[method]
    options = service.HideOptions(
        carrier, method, CipherId.AES_GCM, PASSWORD, settings=settings, is_research=True
    )
    service.hide(cover, file, output, options)


def _reveal(path: Path, tmp_path: Path, research: ResearchReveal | None = None) -> bytes:
    result = service.reveal(path, tmp_path / "out", PASSWORD, research=research)
    data = result.path.read_bytes()
    result.path.unlink()

    return data


def _channel(method: MethodId, cover: Path, settings: Settings) -> Channel[Embedded]:
    """Opened the way hiding opens it."""
    embedding = methods.get(method)
    assert embedding is not None

    return embedding.open(cover, engine.tuning(settings))


def _dct_channel(cover: Path, settings: Settings) -> dct.DctChannel:
    channel = _channel(MethodId.DCT, cover, settings)
    assert isinstance(channel, dct.DctChannel)

    return channel


def test_the_echo_layout_is_the_one_standard_always_had() -> None:
    for rate in (8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 192000):
        expected = (max(round(rate * 0.025), 512), max(rate // 1000, 2), max(rate * 3 // 2000, 3))

        assert echo._layout(rate) == expected


def test_settings_in_other_units_reach_the_engine() -> None:
    tuning = engine.tuning(Settings.of({"echo.d0": 0.8, "echo.d1": 2.3, "obj.limit": 128}))

    assert (tuning.echo.zero_delay, tuning.echo.one_delay) == (8, 23)
    assert tuning.document.limit == 128 * KIB


@pytest.mark.parametrize(
    ("method", "name", "value"), AUDIO_FORMAT_CHANGES, ids=lambda item: str(item)
)
def test_an_audio_format_change_needs_the_same_settings_to_reveal(
    music: Path, tmp_path: Path, method: MethodId, name: str, value: Value
) -> None:
    skip_unless_method_installed(method)
    settings = Settings.of({name: value})
    embedded = tmp_path / "embedded.wav"
    _hide(music, embedded, method, settings)

    research = ResearchReveal(method, CipherId.AES_GCM, settings)

    assert _reveal(embedded, tmp_path, research) == SMALL.data

    with pytest.raises(NoHiddenDataError):
        _reveal(embedded, tmp_path)


@pytest.mark.parametrize(
    ("method", "name", "value"), AUDIO_EMBEDDER_CHANGES, ids=lambda item: str(item)
)
def test_an_audio_embedder_change_reveals_with_standard_settings(
    music: Path, tmp_path: Path, method: MethodId, name: str, value: Value
) -> None:
    skip_unless_method_installed(method)
    embedded = tmp_path / "embedded.wav"
    _hide(music, embedded, method, Settings.of({name: value}))

    assert _reveal(embedded, tmp_path) == SMALL.data


@pytest.mark.parametrize(
    ("method", "name", "value", "share"),
    [
        (MethodId.PHASE_CODING, "phase.bins", 32, 1 / 2),
        (MethodId.PHASE_CODING, "phase.seg", 8192, 1 / 2),
        (MethodId.PHASE_CODING, "phase.rs", 16, (ecc.CODEWORD - 16) / ecc.DATA),
        (MethodId.ECHO_HIDING, "echo.rs", 64, (ecc.CODEWORD - 64) / ecc.DATA),
    ],
    ids=lambda item: str(item),
)
def test_the_audio_layout_sets_the_capacity(
    music: Path, method: MethodId, name: str, value: Value, share: float
) -> None:
    skip_unless_method_installed(method)
    standard = _channel(method, music, Settings()).capacity(0)
    changed = _channel(method, music, Settings.of({name: value})).capacity(0)

    assert changed == pytest.approx(standard * share, abs=ecc.CODEWORD)


def test_the_phase_ramp_sets_where_the_changes_end(music: Path, tmp_path: Path) -> None:
    """The payload takes one codeword, so the data takes the segments its bits need."""
    skip_unless_method_installed(MethodId.PHASE_CODING)
    embedded = tmp_path / "embedded.wav"
    ramp = 2
    _hide(music, embedded, MethodId.PHASE_CODING, Settings.of({"phase.ramp": ramp}))
    before, after = audio.read(music).samples, audio.read(embedded).samples
    data_segments = -(-ecc.CODEWORD * 8 // phase.BINS)
    changed = np.flatnonzero(np.any(before != after, axis=1))

    assert changed.max() // phase.SEGMENT == data_segments + ramp - 1


def test_the_phase_floor_sets_the_coded_bins_magnitude(tmp_path: Path) -> None:
    """In a quiet recording every coded bin is raised to the floor."""
    skip_unless_method_installed(MethodId.PHASE_CODING)
    cover = tmp_path / "quiet.wav"
    rng = np.random.default_rng(seed=6)
    segments = 10
    samples = rng.normal(0, 1, (phase.SEGMENT * 2 * segments, 1)).round().astype(np.int32)
    audio.write(cover, Audio(AudioInfo(samples.shape[0], 1, 8000, 16), samples))
    bits = rng.integers(0, 2, segments * phase.BINS, dtype=np.uint8)
    noise = np.sqrt(phase.SEGMENT / 12)  # one bin's rounding noise
    medians = {}

    for floor in (phase.FLOOR, 2 * phase.FLOOR):
        channel = _channel(MethodId.PHASE_CODING, cover, Settings.of({"phase.floor": floor}))
        assert isinstance(channel, PhaseChannel)
        coded = channel._embed(samples, audio.info(cover), bits)[: segments * phase.SEGMENT, 0]
        spectrum = np.fft.rfft(coded.reshape(segments, phase.SEGMENT), axis=1)
        medians[floor] = float(np.median(np.abs(spectrum[:, phase.STANDARD_PHASE.band])))

        assert medians[floor] == pytest.approx(floor * noise, rel=0.25)

    assert medians[2 * phase.FLOOR] > 1.5 * medians[phase.FLOOR]


def test_the_echo_level_sets_how_much_the_recording_changes(music: Path) -> None:
    skip_unless_method_installed(MethodId.ECHO_HIDING)
    cover = audio.read(music)
    bits = np.random.default_rng(seed=7).integers(0, 2, 200, dtype=np.uint8)
    changes = {}

    for decay in (0.3, 0.9):
        channel = _channel(MethodId.ECHO_HIDING, music, Settings.of({"echo.decay": decay}))
        assert isinstance(channel, EchoChannel)
        echoed = channel._embed(cover.samples, cover.info, bits)
        changes[decay] = float(np.abs(echoed - cover.samples).mean())

    assert changes[0.9] > 2 * changes[0.3]


def test_the_error_correction_risk_follows_the_parity() -> None:
    tuning = engine.tuning(Settings.of({"phase.rs": 16}))

    assert any(
        "to within 239 bytes" in risk
        for risk in risks.detection_risks(MethodId.PHASE_CODING, tuning=tuning)
    )
    assert any(
        "to within 223 bytes" in risk for risk in risks.detection_risks(MethodId.ECHO_HIDING)
    )


@pytest.mark.parametrize(("name", "value"), JPEG_EMBEDDER_CHANGES, ids=lambda item: str(item))
def test_a_jpeg_embedder_change_reveals_with_standard_settings(
    tmp_path: Path, name: str, value: Value
) -> None:
    skip_unless_method_installed(MethodId.DCT)
    cover = _photo(tmp_path / "cover.jpg", quality=85)
    embedded = tmp_path / "embedded.jpg"
    _hide(cover, embedded, MethodId.DCT, Settings.of({name: value}), FILE)

    assert _reveal(embedded, tmp_path) == FILE.data


@pytest.mark.parametrize(("name", "value"), PRECOVER_EMBEDDER_CHANGES, ids=lambda item: str(item))
def test_a_precover_embedder_change_reveals_with_standard_settings(
    tmp_path: Path, name: str, value: Value
) -> None:
    skip_unless_method_installed(MethodId.DCT)
    cover = _photo(tmp_path / "cover.png")
    embedded = tmp_path / "embedded.jpg"
    _hide(cover, embedded, MethodId.DCT, Settings.of({name: value}), FILE)

    assert _reveal(embedded, tmp_path) == FILE.data


@pytest.mark.parametrize(
    ("optimized", "value", "is_standard"),
    [
        (False, "match", True),
        (True, "match", False),
        (False, "optimized", False),
        (True, "standard", True),
    ],
)
def test_the_huffman_tables_follow_the_setting(
    tmp_path: Path, optimized: bool, value: str, is_standard: bool
) -> None:
    skip_unless_method_installed(MethodId.DCT)
    cover = _photo(tmp_path / "cover.jpg", quality=85, optimize=optimized)
    embedded = tmp_path / "embedded.jpg"
    _hide(cover, embedded, MethodId.DCT, Settings.of({"dct.huff": value}))

    assert jpeg.header(embedded).has_standard_tables is is_standard


@pytest.mark.parametrize("keeps", [True, False])
def test_the_cover_segments_are_kept_unless_set_off(tmp_path: Path, keeps: bool) -> None:
    skip_unless_method_installed(MethodId.DCT)
    cover = _photo(tmp_path / "cover.jpg", quality=85, comment=b"a cover's own note")
    embedded = tmp_path / "embedded.jpg"
    _hide(cover, embedded, MethodId.DCT, Settings.of({"dct.app": keeps}))

    with Image.open(embedded) as written:
        assert ("comment" in written.info) is keeps
        assert "jfif" in written.info


@pytest.mark.parametrize("coding", ["ternary", "binary"])
def test_coefficients_beyond_the_maximum_magnitude_never_change(
    tmp_path: Path, coding: str
) -> None:
    skip_unless_method_installed(MethodId.DCT)
    rng = np.random.default_rng(seed=4)
    stripes = np.tile(np.array([0, 255], dtype=np.uint8), 64)
    rows = np.where(
        rng.integers(0, 2, (128, 1, 1)) == 1, stripes[None, :, None], 255 - stripes[None, :, None]
    )
    pixels = np.clip(
        np.broadcast_to(rows, (128, 128, 3)) + rng.integers(-40, 40, (128, 128, 3)), 0, 255
    )
    cover, embedded = tmp_path / "cover.jpg", tmp_path / "embedded.jpg"
    Image.fromarray(pixels.astype(np.uint8)).save(cover, quality=100)
    _hide(cover, embedded, MethodId.DCT, Settings.of({"dct.mag": 255, "stc.code": coding}), FILE)
    before = jpeg.read_coefficients(cover).ac.astype(np.int32)
    after = jpeg.read_coefficients(embedded).ac.astype(np.int32)
    beyond = np.abs(before) > 255

    assert np.count_nonzero(beyond) > 100
    assert np.array_equal(after[beyond], before[beyond])
    assert np.abs(after[~beyond]).max() <= 255
    assert np.any(after != before)


def test_the_cost_settings_reach_the_costs(tmp_path: Path) -> None:
    skip_unless_method_installed(MethodId.DCT)
    cover = _photo(tmp_path / "cover.jpg", quality=85)
    standard = _dct_channel(cover, Settings())._costs(PAYLOAD_RATE)
    sharper = _dct_channel(cover, Settings.of({"dct.sigma": 2.0**-4}))._costs(PAYLOAD_RATE)
    uniform = _dct_channel(cover, Settings.of({"dct.cost": "uniform"}))._costs(PAYLOAD_RATE)
    weighed = _dct_channel(cover, Settings.of({"dct.cost": "uerd"}))._costs(PAYLOAD_RATE)

    assert not np.allclose(standard, sharper)
    assert np.all(uniform == 1)
    np.testing.assert_array_equal(weighed, uerd.costs(jpeg.read_coefficients(cover)))


@pytest.mark.parametrize(("value", "is_tilted"), [(True, True), (False, False)])
def test_side_information_is_a_setting(tmp_path: Path, value: bool, is_tilted: bool) -> None:
    skip_unless_method_installed(MethodId.DCT)
    cover = _photo(tmp_path / "cover.png")
    channel = _dct_channel(cover, Settings.of({"pre.si": value}))
    costs = np.ones(64, dtype=np.float32)
    up, down = channel._step_costs(costs, channel._tilts(np.arange(64)))

    assert (not np.array_equal(up, down)) is is_tilted


def test_the_polarization_share_reaches_the_costs(tmp_path: Path) -> None:
    skip_unless_method_installed(MethodId.DCT)
    cover = _photo(tmp_path / "cover.jpg", quality=85)
    channel = _dct_channel(cover, Settings.of({"dct.pol": 0.4}))
    tilts = channel._tilts(np.arange(4096))
    up, down = channel._step_costs(np.ones(tilts.size, dtype=np.float32), tilts)

    assert np.unique(np.concatenate([up, down])).tolist() == pytest.approx([0.4, 1.0])


def test_the_precover_quality_sets_the_tables(tmp_path: Path) -> None:
    skip_unless_method_installed(MethodId.DCT)
    cover = _photo(tmp_path / "cover.png")
    embedded, pillows = tmp_path / "embedded.jpg", tmp_path / "pillows.jpg"
    _hide(cover, embedded, MethodId.DCT, Settings.of({"pre.q": 75}))

    with Image.open(cover) as opened:
        opened.save(pillows, quality=75)

    with Image.open(embedded) as ours, Image.open(pillows) as theirs:
        assert isinstance(ours, JpegImagePlugin.JpegImageFile)
        assert isinstance(theirs, JpegImagePlugin.JpegImageFile)
        assert ours.quantization == theirs.quantization


@pytest.mark.parametrize(("value", "pillows"), [("4:2:0", 2), ("4:2:2", 1), ("4:4:4", 0)])
def test_the_precover_subsampling_reaches_the_jpeg(
    tmp_path: Path, value: str, pillows: int
) -> None:
    skip_unless_method_installed(MethodId.DCT)
    cover = _photo(tmp_path / "cover.png")
    embedded = tmp_path / "embedded.jpg"
    _hide(cover, embedded, MethodId.DCT, Settings.of({"pre.sub": value}))

    with Image.open(embedded) as written:
        assert isinstance(written, JpegImagePlugin.JpegImageFile)
        assert JpegImagePlugin.get_sampling(written) == pillows


@pytest.mark.parametrize("keeps", [True, False])
def test_the_precover_icc_profile_is_kept_unless_set_off(tmp_path: Path, keeps: bool) -> None:
    skip_unless_method_installed(MethodId.DCT)
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    cover = _photo(tmp_path / "cover.png", icc_profile=profile)
    embedded = tmp_path / "embedded.jpg"
    _hide(cover, embedded, MethodId.DCT, Settings.of({"pre.icc": keeps}))

    with Image.open(embedded) as written:
        assert (written.info.get("icc_profile") == profile) is keeps


@pytest.mark.parametrize("suffix", DOCUMENTS)
def test_a_larger_object_limit_reveals_with_standard_settings(tmp_path: Path, suffix: str) -> None:
    make, _read = DOCUMENTS[suffix]
    cover, embedded = make(tmp_path / f"cover{suffix}"), tmp_path / f"embedded{suffix}"
    large = HiddenFile("large.bin", bytes(range(256)) * 400)  # 100 KiB, above Standard's limit

    with pytest.raises(CapacityError):
        _hide(cover, embedded, MethodId.OBJECT_STREAM, Settings(), large)

    _hide(cover, embedded, MethodId.OBJECT_STREAM, Settings.of({"obj.limit": 128}), large)

    assert _reveal(embedded, tmp_path) == large.data


def test_the_object_limit_sets_the_capacity_but_not_what_is_read(tmp_path: Path) -> None:
    cover = _docx(tmp_path / "cover.docx")
    channel = _channel(MethodId.OBJECT_STREAM, cover, Settings.of({"obj.limit": 8}))

    assert channel.capacity(0) == 8 * KIB
    assert channel.stored_limit(0) == object_stream.MAX_LIMIT


@pytest.mark.parametrize("suffix", DOCUMENTS)
def test_without_padding_the_object_holds_only_the_stream(tmp_path: Path, suffix: str) -> None:
    make, read = DOCUMENTS[suffix]
    cover = make(tmp_path / f"cover{suffix}")
    settings = Settings.of({"obj.pad": False})
    sizes = set()

    for number in range(2):
        embedded = tmp_path / f"embedded-{number}{suffix}"
        _hide(cover, embedded, MethodId.OBJECT_STREAM, settings)
        sizes.add(len(read(embedded, object_stream.MAX_LIMIT)))

        assert _reveal(embedded, tmp_path) == SMALL.data

    assert len(sizes) == 1
    assert sizes.pop() < len(SMALL.data) + 128
    assert ObjectRules(is_padded=False).stored(b"stream") == b"stream"


def test_more_bits_per_paragraph_reveal_with_standard_settings(tmp_path: Path) -> None:
    cover, embedded = _docx(tmp_path / "cover.docx"), tmp_path / "embedded.docx"
    settings = Settings.of({"ws.bpp": 256})
    channel = _channel(MethodId.WHITESPACE, cover, settings)
    standard = PARAGRAPHS * whitespace.BITS_PER_PARAGRAPH // 8
    large = HiddenFile("w.bin", bytes(range(200)) + b"traces" * 50)  # above Standard's capacity

    assert channel.capacity(0) == PARAGRAPHS * 256 // 8
    assert len(large.data) > standard

    _hide(cover, embedded, MethodId.WHITESPACE, settings, large)

    assert _reveal(embedded, tmp_path) == large.data
