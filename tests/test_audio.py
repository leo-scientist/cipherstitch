import io
import os
import struct
import sys
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from cipherstitch import catalog, container, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.container import UnitOrder
from cipherstitch.errors import CapacityError, NotAvailableError, UnsupportedFormatError
from cipherstitch.media import audio
from cipherstitch.methods import trellis
from cipherstitch.methods.lsb import AudioChannel
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions

from .conftest import plain_method

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
FILE = HiddenFile("note.txt", "Таємниця".encode() * 20)
EVERY_DEPTH = Settings.of({"laud.depth": 8})  # Research mode lets 8-bit covers through
ENCRYPTED = HideOptions(
    CarrierKind.AUDIO,
    MethodId.AUDIO_LSB,
    CipherId.AES_GCM,
    PASSWORD,
    is_research=True,
    settings=EVERY_DEPTH,
)
PLAIN = HideOptions(
    CarrierKind.AUDIO, MethodId.AUDIO_LSB, CipherId.NONE, is_research=True, settings=EVERY_DEPTH
)
RATE = 8000
FRAMES = 2 * RATE  # two seconds

WavFactory = Callable[..., Path]


def _random_samples(depth: int, channels: int) -> np.ndarray:
    rng = np.random.default_rng(seed=depth * 10 + channels)
    low, high = -(1 << (depth - 1)), 1 << (depth - 1)

    return rng.integers(low, high, size=(FRAMES, channels), dtype=np.int32)


def _pcm(samples: np.ndarray, depth: int) -> bytes:
    """Little-endian PCM packed independently of cipherstitch.media.audio."""
    if depth == 8:
        return bytes(int(value) + 128 for value in samples.reshape(-1))

    width = depth // 8

    return b"".join(
        int(value).to_bytes(width, "little", signed=True) for value in samples.reshape(-1)
    )


@pytest.fixture
def make_wav(tmp_path: Path) -> WavFactory:
    def factory(depth: int = 16, channels: int = 2, name: str = "cover.wav") -> Path:
        path = tmp_path / name

        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(depth // 8)
            wav.setframerate(RATE)
            wav.writeframes(_pcm(_random_samples(depth, channels), depth))

        return path

    return factory


@pytest.mark.parametrize("channels", [1, 2], ids=["mono", "stereo"])
@pytest.mark.parametrize("depth", [8, 16, 24])
def test_wav_reads_the_samples_that_were_written(
    make_wav: WavFactory, depth: int, channels: int
) -> None:
    decoded = audio.read(make_wav(depth, channels))
    assert decoded.info == audio.AudioInfo(FRAMES, channels, RATE, depth)
    assert np.array_equal(decoded.samples, _random_samples(depth, channels))


@pytest.mark.parametrize("depth", [8, 16, 24])
def test_values_are_every_channels_sample_frame_after_frame(
    make_wav: WavFactory, depth: int
) -> None:
    """Part of the stored format (docs/format.md#trellis-channels), at the file's own depth."""
    values = AudioChannel(make_wav(depth, 2))._values()
    samples = _random_samples(depth, 2)

    assert values[:4].tolist() == [samples[0, 0], samples[0, 1], samples[1, 0], samples[1, 1]]
    assert np.array_equal(values, samples.reshape(-1))


@pytest.mark.parametrize("depth", [8, 16, 24])
def test_a_range_of_frames_is_read_alone(make_wav: WavFactory, tmp_path: Path, depth: int) -> None:
    expected = _random_samples(depth, 2)[100:350]
    assert np.array_equal(audio.read_frames(make_wav(depth, 2), 100, 350), expected)
    soundfile = pytest.importorskip("soundfile")
    flac = tmp_path / "song.flac"
    subtype = {8: "PCM_S8", 16: "PCM_16", 24: "PCM_24"}[depth]
    soundfile.write(str(flac), _random_samples(depth, 2) << (32 - depth), RATE, subtype=subtype)
    assert np.array_equal(audio.read_frames(flac, 100, 350), expected)


@pytest.mark.parametrize("options", [ENCRYPTED, PLAIN], ids=["encrypted", "plain"])
@pytest.mark.parametrize("channels", [1, 2], ids=["mono", "stereo"])
@pytest.mark.parametrize("depth", [8, 16, 24])
def test_wav_roundtrip_changes_samples_by_at_most_one(
    make_wav: WavFactory, tmp_path: Path, depth: int, channels: int, options: HideOptions
) -> None:
    cover = make_wav(depth, channels)
    embedded = tmp_path / "embedded.wav"
    service.hide(cover, FILE, embedded, options)

    before, after = audio.read(cover), audio.read(embedded)
    assert after.info == before.info
    assert np.abs(after.samples - before.samples).max() == 1
    out = tmp_path / "out"
    out.mkdir()
    result = service.reveal(embedded, out, options.password)
    assert result.path.read_bytes() == FILE.data
    assert (result.method, result.cipher) == (MethodId.AUDIO_LSB, options.cipher)


@pytest.mark.parametrize("depth", [8, 16, 24])
def test_lsb_matching_keeps_extreme_samples_in_range(tmp_path: Path, depth: int) -> None:
    """Two samples in three sit at an end of the range, where they can only step inwards."""
    low, high = audio.sample_range(depth)
    cover = tmp_path / "loud.wav"
    samples = np.empty((FRAMES, 1), dtype=np.int32)
    samples[0::3], samples[1::3] = low, high
    samples[2::3, 0] = np.random.default_rng(seed=0).integers(low + 1, high, (FRAMES + 1) // 3)

    with wave.open(str(cover), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(depth // 8)
        wav.setframerate(RATE)
        wav.writeframes(_pcm(samples, depth))

    data = bytes(range(256))
    embedded = AudioChannel(cover).write(b"", data, None)
    embedded.save(tmp_path / "embedded.wav")
    result = embedded.audio.samples

    assert set(np.unique(result[0::3]).tolist()) == {low, low + 1}
    assert set(np.unique(result[1::3]).tolist()) == {high - 1, high}
    assert AudioChannel(tmp_path / "embedded.wav").read(0, len(data), None) == data


def test_units_are_the_samples_frame_by_frame(tmp_path: Path) -> None:
    """docs/format.md#trellis-channels: every channel's sample of a frame, then the next
    frame."""
    samples = np.random.default_rng(seed=0).integers(-3000, 3000, (1350, 2), dtype=np.int32)
    cover = _wav_of(tmp_path / "order.wav", samples)
    values = samples.reshape(-1)
    expected = trellis.Units(values, trellis.Regions.of(values.size)).lead

    assert AudioChannel(cover).read(0, 16, None) == expected


def test_encrypted_payload_is_spread_over_the_whole_file(
    make_wav: WavFactory, tmp_path: Path
) -> None:
    cover = make_wav()
    embedded = tmp_path / "embedded.wav"
    service.hide(cover, FILE, embedded, ENCRYPTED)
    changed = np.nonzero(np.any(audio.read(embedded).samples != audio.read(cover).samples, axis=1))
    assert changed[0].max() > FRAMES * 0.9
    assert plain_method(embedded) is None


def _wav_of(path: Path, samples: np.ndarray) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(samples.shape[1])
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(_pcm(samples, 16))

    return path


@pytest.mark.parametrize("channels", [1, 2], ids=["leading-second", "right-channel"])
def test_a_small_payload_leaves_digital_silence_silent(tmp_path: Path, channels: int) -> None:
    """A payload of 5% of the capacity changes no silent sample, the prefix and the head
    included. In a stereo recording, units in sequence alternate between the channels."""
    samples = _random_samples(16, channels)
    silent = np.zeros(samples.shape, dtype=bool)

    if channels == 1:
        silent[:RATE] = True  # the first second
    else:
        silent[:, 1] = True  # the right channel

    samples[silent] = 0
    channel = AudioChannel(_wav_of(tmp_path / "gap.wav", samples))
    prefix, body = bytes(range(16)), bytes(range(channel.capacity(16) // 20))
    embedded = channel.write(prefix, body, b"seed")
    embedded.save(tmp_path / "embedded.wav")
    revealed = AudioChannel(tmp_path / "embedded.wav")

    assert not embedded.audio.samples[silent].any()
    assert np.any(embedded.audio.samples != samples)
    assert revealed.read(0, len(prefix), None) == prefix
    assert revealed.read(len(prefix), len(body), b"seed") == body


def test_silence_is_found_in_each_channel_frame_by_frame(tmp_path: Path) -> None:
    """In stretches of 4096 samples, the last one partial; the mask follows the units."""
    frames = 2 * 4096 + 100
    samples = np.random.default_rng(seed=1).integers(1, 3000, (frames, 2), dtype=np.int32)
    flat = np.zeros(samples.shape, dtype=bool)
    flat[:1000] = True  # both channels
    flat[1000:5000, 1] = True  # the right one, across a stretch
    flat[-50:, 0] = True  # the left one, in the partial stretch
    samples[flat] = 0
    channel = AudioChannel(_wav_of(tmp_path / "gaps.wav", samples))

    assert np.array_equal(channel._flat_values().reshape(frames, 2), flat)


def test_plain_payload_is_detected(make_wav: WavFactory, tmp_path: Path) -> None:
    embedded = tmp_path / "embedded.wav"
    service.hide(make_wav(), FILE, embedded, PLAIN)
    assert plain_method(embedded) == MethodId.AUDIO_LSB


@pytest.mark.parametrize("depth", [8, 16, 24])
def test_flac_roundtrip_is_bit_exact(tmp_path: Path, depth: int) -> None:
    soundfile = pytest.importorskip("soundfile")
    cover = tmp_path / "cover.flac"
    subtype = {8: "PCM_S8", 16: "PCM_16", 24: "PCM_24"}[depth]
    aligned = _random_samples(depth, 2) << (32 - depth)
    soundfile.write(str(cover), aligned, RATE, subtype=subtype, format="FLAC")

    embedded = AudioChannel(cover).write(b"", b"\xa5" * 500, None)
    embedded.save(tmp_path / "embedded.flac")
    reread = audio.read(tmp_path / "embedded.flac")
    assert reread.info == embedded.audio.info
    assert np.array_equal(reread.samples, embedded.audio.samples)
    assert np.abs(reread.samples - _random_samples(depth, 2)).max() == 1

    service.hide(cover, FILE, tmp_path / "hidden.flac", ENCRYPTED)
    assert service.reveal(tmp_path / "hidden.flac", tmp_path, PASSWORD).path.read_bytes() == (
        FILE.data
    )


def test_output_keeps_the_carrier_format(make_wav: WavFactory, tmp_path: Path) -> None:
    assert service.output_suffixes(MethodId.AUDIO_LSB, Path("a.WAV")) == (".wav",)

    with pytest.raises(UnsupportedFormatError, match=r"\.wav"):
        service.hide(make_wav(), FILE, tmp_path / "embedded.flac", ENCRYPTED)


def test_describe_and_capacity(make_wav: WavFactory) -> None:
    cover = make_wav(depth=24, channels=2)
    details = service.describe_carrier(cover, CarrierKind.AUDIO)
    assert details.description == "0:02 · 8 kHz · 24-bit stereo · WAV"
    assert details.capacity == service.capacity(cover, research=True) == _capacity(FRAMES * 2)


def _capacity(count: int) -> int:
    """docs/format.md#trellis-channels, for samples none of which are silent."""
    regions = trellis.Regions.of(count)
    bits = min((count - regions.lead) * 80 // 100, 560 + regions.tail)

    return 16 + bits // 8 - 6


def test_max_data_size_is_exact(make_wav: WavFactory) -> None:
    channel = AudioChannel(make_wav(channels=1))
    limit = container.max_data_size(channel, FILE.name, CipherId.AES_GCM)
    container.hide(
        channel,
        HiddenFile(FILE.name, bytes(limit)),
        method=MethodId.AUDIO_LSB,
        cipher_id=CipherId.AES_GCM,
        password=PASSWORD,
        order=UnitOrder.KEYED,
    )

    with pytest.raises(CapacityError):
        container.hide(
            channel,
            HiddenFile(FILE.name, bytes(limit + 1)),
            method=MethodId.AUDIO_LSB,
            cipher_id=CipherId.AES_GCM,
            password=PASSWORD,
            order=UnitOrder.KEYED,
        )


def test_unsupported_wav_files_are_rejected(tmp_path: Path) -> None:
    wide = tmp_path / "wide.wav"

    with wave.open(str(wide), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(4)
        wav.setframerate(RATE)
        wav.writeframes(bytes(400))

    with pytest.raises(UnsupportedFormatError, match="32-bit WAV is not supported"):
        service.describe_carrier(wide, CarrierKind.AUDIO)

    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not a wave file")

    with pytest.raises(UnsupportedFormatError, match="not a PCM WAV"):
        service.capacity(junk)


@pytest.mark.parametrize("cut", [1, 2, 3, 4], ids=lambda cut: f"cut-{cut}")
def test_wav_cut_inside_the_last_frames_is_rejected_on_read(make_wav: WavFactory, cut: int) -> None:
    """Cuts 1-3 leave a partial 4-byte frame; the header still fits the file."""
    cover = make_wav(depth=16, channels=2)
    cover.write_bytes(cover.read_bytes()[:-cut])
    channel = AudioChannel(cover)

    with pytest.raises(UnsupportedFormatError, match="shorter than its header"):
        channel.read(0, 16, None)


def test_wav_cut_beyond_its_header_is_rejected_on_open(make_wav: WavFactory) -> None:
    cover = make_wav()
    cover.write_bytes(cover.read_bytes()[:-400])

    with pytest.raises(UnsupportedFormatError, match="shorter than its header"):
        AudioChannel(cover)


def test_streamed_wav_with_placeholder_size_is_rejected(make_wav: WavFactory) -> None:
    cover = make_wav()
    header = bytearray(cover.read_bytes())
    assert header[36:40] == b"data"
    header[40:44] = b"\xff\xff\xff\xff"  # the size a streaming writer leaves behind
    cover.write_bytes(bytes(header))

    with pytest.raises(UnsupportedFormatError, match="shorter than its header"):
        service.describe_carrier(cover, CarrierKind.AUDIO)


def test_unreadable_flac_is_rejected(tmp_path: Path) -> None:
    pytest.importorskip("soundfile")
    junk = tmp_path / "junk.flac"
    junk.write_bytes(b"fLaC" + bytes(64))

    with pytest.raises(UnsupportedFormatError, match="not a readable FLAC file"):
        service.capacity(junk)


def test_flac_cut_mid_stream_is_rejected_on_read(tmp_path: Path) -> None:
    soundfile = pytest.importorskip("soundfile")
    flac = tmp_path / "interrupted.flac"
    soundfile.write(str(flac), _random_samples(16, 2) << 16, RATE, subtype="PCM_16")
    flac.write_bytes(flac.read_bytes()[:-2000])
    assert audio.info(flac).frames == FRAMES  # the header is intact; the capacity reads it all

    for read in (lambda: service.capacity(flac), lambda: AudioChannel(flac).read(0, 16, None)):
        with pytest.raises(UnsupportedFormatError, match="not a readable FLAC file"):
            read()


def test_wav_with_zero_sample_rate_is_rejected(make_wav: WavFactory) -> None:
    cover = make_wav()
    header = bytearray(cover.read_bytes())
    header[24:28] = bytes(4)  # the fmt chunk's sample rate
    cover.write_bytes(bytes(header))

    with pytest.raises(UnsupportedFormatError, match="invalid sample rate"):
        service.describe_carrier(cover, CarrierKind.AUDIO)


def test_flac_without_a_recorded_length_is_rejected(tmp_path: Path) -> None:
    soundfile = pytest.importorskip("soundfile")
    flac = tmp_path / "streamed.flac"
    soundfile.write(str(flac), _random_samples(16, 2) << 16, RATE, subtype="PCM_16")
    data = bytearray(flac.read_bytes())
    data[21] &= 0xF0  # STREAMINFO total samples (36 bits) = 0: unknown
    data[22:26] = bytes(4)
    flac.write_bytes(bytes(data))

    with pytest.raises(UnsupportedFormatError, match="does not record its length"):
        service.capacity(flac)


def test_the_sample_cap_admits_hours_of_stereo_and_fits_int32_indices() -> None:
    hour_of_stereo = 3600 * 48000 * 2
    assert 3 * hour_of_stereo < audio._MAX_SAMPLES < 2**31


@pytest.mark.parametrize("suffix", [".wav", ".flac"])
def test_a_recording_over_the_sample_cap_is_refused_before_decoding(
    make_wav: WavFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    """A FLAC can decode to thousands of times its size."""
    if suffix == ".flac":
        soundfile = pytest.importorskip("soundfile")
        cover = tmp_path / "cover.flac"
        soundfile.write(str(cover), _random_samples(16, 2) << 16, RATE, subtype="PCM_16")
    else:
        cover = make_wav(channels=2)

    monkeypatch.setattr(audio, "_MAX_SAMPLES", 2 * FRAMES)
    assert audio.info(cover).frames == FRAMES
    monkeypatch.setattr(audio, "_MAX_SAMPLES", 2 * FRAMES - 1)

    for read in (lambda: audio.info(cover), lambda: service.reveal(cover, tmp_path, PASSWORD)):
        with pytest.raises(UnsupportedFormatError, match="too long to read"):
            read()


def test_a_flac_that_decodes_to_far_more_than_its_size_is_refused(tmp_path: Path) -> None:
    """Silence compresses thousands of times over: a small file could fill the memory."""
    soundfile = pytest.importorskip("soundfile")
    silence = tmp_path / "silence.flac"
    soundfile.write(str(silence), np.zeros((RATE * 10, 8), dtype=np.int16), RATE)

    for read in (lambda: audio.info(silence), lambda: service.reveal(silence, tmp_path, None)):
        with pytest.raises(UnsupportedFormatError, match="far more than a recording"):
            read()


def test_flac_without_the_audio_extra(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "soundfile", None)
    monkeypatch.setattr(catalog.Requirement, "is_installed", property(lambda _self: False))
    song = tmp_path / "song.flac"
    song.write_bytes(b"fLaC")

    with pytest.raises(NotAvailableError, match="audio extra"):
        service.describe_carrier(song, CarrierKind.AUDIO)

    assert service.unavailable_reason(ENCRYPTED, song) == (
        "FLAC needs the audio extra, see Installation in README.md"
    )


def _riff(*chunks: bytes) -> bytes:
    body = b"WAVE" + b"".join(chunks)

    return b"RIFF" + struct.pack("<I", len(body)) + body


def _chunk(name: bytes, data: bytes, *, is_padded: bool = True) -> bytes:
    pad = bytes(len(data) % 2) if is_padded else b""

    return name + struct.pack("<I", len(data)) + data + pad


def _fmt(channels: int, rate: int, width: int = 2) -> bytes:
    block = channels * width
    byte_rate, block_align = rate * block % (1 << 32), block % (1 << 16)  # wave reads neither
    fields = struct.pack("<HHIIHH", 1, channels, rate, byte_rate, block_align, 8 * width)

    return _chunk(b"fmt ", fields)


def test_a_wav_whose_odd_chunk_lacks_its_pad_byte_is_refused(tmp_path: Path) -> None:
    """The data chunk's header is then read a byte late, and stdlib wave raises RuntimeError
    for the chunk size it finds there."""
    title = _chunk(b"INAM", b"Song" + bytes(1), is_padded=False)
    info = _chunk(b"LIST", b"INFO" + title, is_padded=False)
    data = _chunk(b"data", bytes([0xFF]) * 4000)
    (tmp_path / "unpadded.wav").write_bytes(_riff(_fmt(1, RATE), info, data))

    with pytest.raises(UnsupportedFormatError, match="not a PCM WAV file"):
        service.capacity(tmp_path / "unpadded.wav")


def test_a_wav_with_more_bytes_per_second_than_its_header_holds_is_refused(
    tmp_path: Path,
) -> None:
    """The header stores the byte rate in 32 bits: 65 535 channels at 48 kHz overflow it."""
    channels = 65_535
    data = _chunk(b"data", bytes(channels * 2 * 4))
    (tmp_path / "wide.wav").write_bytes(_riff(_fmt(channels, 48_000), data))
    (tmp_path / "fits.wav").write_bytes(_riff(_fmt(channels, 32_000), data))

    assert audio.info(tmp_path / "fits.wav").channels == channels

    with pytest.raises(UnsupportedFormatError, match="higher sample rate than WAV supports"):
        audio.info(tmp_path / "wide.wav")


def _with_block(flac: bytes, kind: int, content: bytes) -> bytes:
    """``flac`` with a metadata block added after STREAMINFO, which stays first."""
    end = 4 + 4 + 34  # the marker, then STREAMINFO's header and content
    header = bytes([kind | flac[4] & 0x80]) + len(content).to_bytes(3, "big")

    return flac[:4] + bytes([flac[4] & 0x7F]) + flac[5:end] + header + content + flac[end:]


def test_padding_does_not_let_a_flac_decode_to_far_more_than_its_frames(tmp_path: Path) -> None:
    """Only the frames hold samples: a padding block made the size check pass."""
    soundfile = pytest.importorskip("soundfile")
    silence = tmp_path / "silence.flac"
    soundfile.write(str(silence), np.zeros((RATE * 10, 8), dtype=np.int16), RATE)
    padding = bytes(RATE * 10 * 8 // 32)
    silence.write_bytes(_with_block(silence.read_bytes(), 1, padding))

    assert silence.stat().st_size * 32 > RATE * 10 * 8

    with pytest.raises(UnsupportedFormatError, match="far more than a recording"):
        audio.info(silence)


def test_a_flac_after_an_id3_tag_is_read(tmp_path: Path) -> None:
    """Some taggers put one before the stream, and libsndfile skips it."""
    soundfile = pytest.importorskip("soundfile")
    flac = tmp_path / "tagged.flac"
    soundfile.write(str(flac), _random_samples(16, 2) << 16, RATE, subtype="PCM_16")
    tag = b"ID3" + bytes([4, 0, 0, 0, 0, 1, 0]) + bytes(128)  # 128 bytes, as 7-bit digits
    flac.write_bytes(tag + flac.read_bytes())

    assert np.array_equal(audio.read(flac).samples, _random_samples(16, 2))


@pytest.mark.parametrize(
    "layout",
    [
        b"fLaC" + bytes([0, 0, 0, 34]) + bytes(34),  # no last block
        b"fLaC" + bytes([0x80, 0, 1, 0]) + bytes(34),  # a block past the end
        b"OggS" + bytes(64),
    ],
    ids=["unfinished", "too-long", "not-flac"],
)
def test_broken_flac_metadata_is_refused(layout: bytes) -> None:
    with pytest.raises(UnsupportedFormatError, match=r"x\.flac is not a readable FLAC file"):
        audio._flac_layout(io.BytesIO(layout), "x.flac")


def test_the_walk_over_flac_metadata_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    empty = bytes([1, 0, 0, 0])  # padding of no bytes
    flac = b"fLaC" + bytes([0, 0, 0, 34]) + bytes(34) + empty * 2 + bytes([0x81, 0, 0, 0])
    monkeypatch.setattr(audio, "_MAX_FLAC_BLOCKS", 4)

    assert len(audio._flac_layout(io.BytesIO(flac), "x.flac").blocks) == 4

    monkeypatch.setattr(audio, "_MAX_FLAC_BLOCKS", 3)

    with pytest.raises(UnsupportedFormatError, match="not a readable FLAC file"):
        audio._flac_layout(io.BytesIO(flac), "x.flac")


def test_a_missing_or_unreadable_flac_raises_its_own_error(tmp_path: Path) -> None:
    """soundfile reports both as a read error, which read as "not a readable FLAC file"."""
    soundfile = pytest.importorskip("soundfile")

    with pytest.raises(FileNotFoundError):
        audio.info(tmp_path / "gone.flac")

    if getattr(os, "geteuid", lambda: 1)() == 0:
        pytest.skip("root reads any file")

    flac = tmp_path / "locked.flac"
    soundfile.write(str(flac), _random_samples(16, 2) << 16, RATE, subtype="PCM_16")
    flac.chmod(0)

    try:
        with pytest.raises(PermissionError):
            audio.info(flac)
    finally:
        flac.chmod(0o600)
