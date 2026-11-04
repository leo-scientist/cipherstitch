"""Phase coding and echo hiding: Reed–Solomon-protected audio methods."""

import sys
import time
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from cipherstitch import container, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.errors import CapacityError
from cipherstitch.media import audio
from cipherstitch.media.audio import Audio, AudioInfo, Samples
from cipherstitch.methods import ecc, phase
from cipherstitch.methods.echo import EchoChannel
from cipherstitch.methods.phase import PhaseChannel
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions

pytest.importorskip("reedsolo")

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
FILE = HiddenFile("note.txt", b"meet at nine " * 5)
METHODS = [MethodId.PHASE_CODING, MethodId.ECHO_HIDING]
CHANNELS = {MethodId.PHASE_CODING: PhaseChannel, MethodId.ECHO_HIDING: EchoChannel}


def _music(path: Path, seconds: float, rate: int, channels: int = 2) -> Path:
    """Tones with a slow envelope and a little noise: a stand-in for a real recording."""
    rng = np.random.default_rng(seed=rate)
    t = np.arange(int(seconds * rate)) / rate
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
    samples = np.stack([mono * (1 - 0.1 * c) for c in range(channels)], axis=1) * 26000
    info = AudioInfo(t.size, channels, rate, 16)
    audio.write(path, Audio(info, samples.round().astype(np.int32)))

    return path


def _options(method: MethodId, cipher: CipherId) -> HideOptions:
    password = None if cipher == CipherId.NONE else PASSWORD

    return HideOptions(CarrierKind.AUDIO, method, cipher, password, is_research=True)


@pytest.fixture
def recording(tmp_path: Path) -> Path:
    """22.05 kHz: echo hiding carries ~40 bits a second, so a minute holds one codeword."""
    return _music(tmp_path / "cover.wav", seconds=60, rate=22050)


@pytest.mark.parametrize("cipher", [CipherId.AES_GCM, CipherId.NONE], ids=["encrypted", "plain"])
@pytest.mark.parametrize("method", METHODS)
def test_roundtrip_has_no_residual_errors(
    recording: Path, tmp_path: Path, method: MethodId, cipher: CipherId
) -> None:
    embedded = tmp_path / "embedded.wav"
    service.hide(recording, FILE, embedded, _options(method, cipher))
    password = None if cipher == CipherId.NONE else PASSWORD
    result = service.reveal(embedded, tmp_path, password)
    assert result.path.read_bytes() == FILE.data
    assert (result.method, result.cipher) == (method, cipher)


@pytest.mark.parametrize("suffix", [".wav", ".flac"])
@pytest.mark.parametrize("method", METHODS)
def test_capacity_matches_reality(tmp_path: Path, method: MethodId, suffix: str) -> None:
    if suffix == ".flac":
        pytest.importorskip("soundfile")

    recording = _music(tmp_path / f"long{suffix}", seconds=150, rate=22050)  # 2+ codewords each
    channel = CHANNELS[method](recording)
    assert channel.capacity(0) >= 2 * ecc.DATA
    limit = container.max_data_size(channel, FILE.name, CipherId.AES_GCM)
    fitting = HiddenFile(FILE.name, bytes(range(256)) * (limit // 256) + bytes(limit % 256))
    embedded = tmp_path / f"full{suffix}"
    service.hide(recording, fitting, embedded, _options(method, CipherId.AES_GCM))
    assert service.reveal(embedded, tmp_path, PASSWORD).path.read_bytes() == fitting.data

    with pytest.raises(CapacityError):
        service.hide(
            recording,
            HiddenFile(FILE.name, bytes(limit + 1)),
            tmp_path / f"x{suffix}",
            _options(method, CipherId.AES_GCM),
        )


def _silence(path: Path) -> Path:
    frames = 22050 * 60
    audio.write(path, Audio(AudioInfo(frames, 1, 22050, 16), np.zeros((frames, 1), np.int32)))

    return path


def test_echo_hiding_refuses_silence_rather_than_losing_data(tmp_path: Path) -> None:
    silent = _silence(tmp_path / "silent.wav")

    with pytest.raises(CapacityError, match="can't hold this payload reliably"):
        service.hide(
            silent, FILE, tmp_path / "s.wav", _options(MethodId.ECHO_HIDING, CipherId.NONE)
        )


def test_phase_coding_uses_silence_thanks_to_the_magnitude_floor(tmp_path: Path) -> None:
    silent = _silence(tmp_path / "silent.wav")
    options = _options(MethodId.PHASE_CODING, CipherId.NONE)
    service.hide(silent, FILE, tmp_path / "s.wav", options)
    assert service.reveal(tmp_path / "s.wav", tmp_path, None).method == MethodId.PHASE_CODING


def _phase_coded(cover: Path, tmp_path: Path) -> tuple[Samples, Samples]:
    """The cover's samples and the result's, after checking that the result reveals."""
    embedded = tmp_path / "embedded.wav"
    service.hide(cover, FILE, embedded, _options(MethodId.PHASE_CODING, CipherId.AES_GCM))
    assert service.reveal(embedded, tmp_path, PASSWORD).path.read_bytes() == FILE.data

    return audio.read(cover).samples, audio.read(embedded).samples


def test_phase_coding_leaves_the_rest_of_the_recording_as_it_was(
    recording: Path, tmp_path: Path
) -> None:
    """The payload takes one codeword. The shift returns to 0 over the ramp's segments."""
    before, after = _phase_coded(recording, tmp_path)
    data_segments = -(-ecc.CODEWORD * 8 // phase.BINS)
    changed = np.flatnonzero(np.any(before != after, axis=1))
    end = (data_segments + phase.RAMP) * phase.SEGMENT

    assert changed.max() // phase.SEGMENT == data_segments + phase.RAMP - 1
    assert np.array_equal(after[end:], before[end:])


@pytest.mark.parametrize("chunk", [7, 40], ids=["ramp-across-chunks", "ramp-in-next-chunk"])
def test_phase_coding_chunks_do_not_change_the_result(
    recording: Path, monkeypatch: pytest.MonkeyPatch, chunk: int
) -> None:
    """40 data segments, the last one partly filled: the ramp carries the last shift over."""
    cover = audio.read(recording)
    bits = np.random.default_rng(seed=0).integers(0, 2, 40 * phase.BINS - 5, dtype=np.uint8)
    channel = PhaseChannel(recording)
    whole = channel._embed(cover.samples, cover.info, bits)
    monkeypatch.setattr(phase, "_CHUNK", chunk)

    assert np.array_equal(channel._embed(cover.samples, cover.info, bits), whole)


def _band_energy(samples: npt.NDArray[np.float64]) -> float:
    whole = samples.size // phase.SEGMENT * phase.SEGMENT
    segments = samples[:whole].reshape(-1, phase.SEGMENT)

    return float(np.sum(np.abs(np.fft.rfft(segments, axis=1)[:, phase.STANDARD_PHASE.band]) ** 2))


def test_phase_coding_shifts_every_channel_alike(recording: Path, tmp_path: Path) -> None:
    """The stereo image stays: the mono downmix keeps the energy of the coded band."""
    before, after = _phase_coded(recording, tmp_path)
    end = int(np.flatnonzero(np.any(before != after, axis=1)).max()) + 1
    ratio = _band_energy(after[:end].mean(axis=1)) / _band_energy(before[:end].mean(axis=1))

    assert np.any(before[:, 1] != after[:, 1])
    assert abs(ratio - 1) < 0.05


@pytest.mark.parametrize("is_silent", [False, True], ids=["music", "silence"])
def test_phase_coding_keeps_a_mono_recording_mono(tmp_path: Path, is_silent: bool) -> None:
    """In silence the floor puts the data in the first channel, and so in the second."""
    path = tmp_path / "mono.wav"
    mono = audio.read(
        _silence(path) if is_silent else _music(path, seconds=20, rate=22050, channels=1)
    )
    info = AudioInfo(mono.info.frames, 2, mono.info.rate, mono.info.depth)
    cover = tmp_path / "stereo.wav"
    audio.write(cover, Audio(info, np.repeat(mono.samples, 2, axis=1)))
    _, after = _phase_coded(cover, tmp_path)

    assert np.array_equal(after[:, 0], after[:, 1])


def test_phase_coding_keeps_a_silent_channel_silent(recording: Path, tmp_path: Path) -> None:
    """Even where the first channel is silent too: here in its first 2 seconds."""
    music = audio.read(recording)
    samples = music.samples.copy()
    samples[: 2 * music.info.rate, 0] = 0
    samples[:, 1] = 0
    cover = tmp_path / "left.wav"
    audio.write(cover, Audio(music.info, samples))
    _, after = _phase_coded(cover, tmp_path)

    assert not np.any(after[:, 1])


def test_lsb_audio_reveals_without_the_ecc_extra(
    recording: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedded = tmp_path / "embedded.wav"
    lsb = HideOptions(
        CarrierKind.AUDIO, MethodId.AUDIO_LSB, CipherId.AES_GCM, PASSWORD, is_research=True
    )
    service.hide(recording, FILE, embedded, lsb)
    monkeypatch.setitem(sys.modules, "reedsolo", None)  # phase and echo channels are skipped
    assert service.reveal(embedded, tmp_path, PASSWORD).method == MethodId.AUDIO_LSB


@pytest.mark.parametrize("method", METHODS)
def test_a_recording_without_a_payload_costs_one_codeword(
    tmp_path: Path, method: MethodId, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = _music(tmp_path / "long.wav", seconds=150, rate=22050)  # 2+ codewords each
    channel = CHANNELS[method](recording)
    assert channel.capacity(0) >= 2 * ecc.DATA
    windows: list[tuple[int, int]] = []
    read_frames = audio.read_frames

    def tracked(path: Path, start: int, stop: int) -> Samples:
        windows.append((start, stop))

        return read_frames(path, start, stop)

    monkeypatch.setattr(audio, "read_frames", tracked)
    monkeypatch.setattr(audio, "read", None)  # never decodes the whole file
    assert channel.read(0, 16, None) == b""
    assert len(windows) == 1
    assert windows[0][0] == 0


def test_error_correction_repairs_up_to_16_bytes_per_codeword() -> None:
    stream = bytes(range(200))
    raw = bytearray(ecc.encode(stream))
    assert len(raw) == ecc.CODEWORD

    for index in range(0, 160, 10):  # 16 corrupted bytes
        raw[index] ^= 0xFF

    assert ecc.decode(bytes(raw))[: len(stream)] == stream
    raw[200] ^= 0xFF  # the 17th
    assert ecc.decode(bytes(raw)) == b""


@pytest.mark.parametrize("method", METHODS)
def test_three_minute_recording_takes_under_ten_seconds(tmp_path: Path, method: MethodId) -> None:
    recording = _music(tmp_path / "song.wav", seconds=180, rate=44100)
    started = time.perf_counter()
    service.hide(recording, FILE, tmp_path / "embedded.wav", _options(method, CipherId.AES_GCM))
    result = service.reveal(tmp_path / "embedded.wav", tmp_path, PASSWORD)
    assert time.perf_counter() - started < 10
    assert result.method == method


@pytest.mark.parametrize("method", METHODS)
def test_a_recording_shorter_than_one_codeword_refuses_the_prefix(
    tmp_path: Path, method: MethodId
) -> None:
    """Under one codeword the capacity is 0, and the prefix alone went into the embedding
    (a ValueError): echo hiding needs about 51 s at 22.05 kHz, phase coding about 6 s."""
    short = _music(tmp_path / "short.wav", seconds=2, rate=22050)
    channel = CHANNELS[method](short)

    assert channel.capacity(0) == 0

    with pytest.raises(CapacityError, match="holds at most 0"):
        channel.write(bytes(16), b"", None)
