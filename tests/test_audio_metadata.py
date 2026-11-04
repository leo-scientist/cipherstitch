"""What an audio output keeps of its cover besides the samples."""

import io
import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from cipherstitch import service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.errors import UnsupportedFormatError
from cipherstitch.media import audio
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
FILE = HiddenFile("note.txt", b"kept in the samples " * 8)
ENCRYPTED = HideOptions(
    CarrierKind.AUDIO, MethodId.AUDIO_LSB, CipherId.AES_GCM, PASSWORD, is_research=True
)
RATE = 8000
FRAMES = 2 * RATE
SURROUND = 6  # 5.1
SURROUND_MASK = 0x3F  # front left, right and centre, LFE, back left and right
EXTENSIBLE = 0xFFFE
PCM_FORMAT = bytes.fromhex("0100000000001000800000aa00389b71")  # KSDATAFORMAT_SUBTYPE_PCM
STREAMINFO, PADDING, SEEKTABLE, VORBIS_COMMENT, PICTURE = 0, 1, 3, 4, 6


def _samples(channels: int) -> np.ndarray:
    rng = np.random.default_rng(seed=channels)

    return rng.integers(-8000, 8000, size=(FRAMES, channels), dtype=np.int32)


def _chunk(name: bytes, data: bytes) -> bytes:
    return name + struct.pack("<I", len(data)) + data + bytes(len(data) % 2)


def _surround_wav(path: Path) -> tuple[int, int]:
    """A 5.1 WAV in the extensible format, with broadcast, title and cue chunks around its
    samples, which lie at the returned offsets."""
    block = SURROUND * 2
    fmt = struct.pack("<HHIIHH", EXTENSIBLE, SURROUND, RATE, RATE * block, block, 16)
    fmt += struct.pack("<HHI", 22, 16, SURROUND_MASK) + PCM_FORMAT
    title = _chunk(b"INAM", b"Harbour at dusk" + bytes(1))
    samples = _samples(SURROUND).astype("<i2").tobytes()
    cue = struct.pack("<I", 1) + struct.pack("<II4sIII", 1, 0, b"data", 0, 0, 4000)
    before = (
        b"WAVE"
        + _chunk(b"fmt ", fmt)
        + _chunk(b"bext", b"Field recording".ljust(602, bytes(1)))
        + _chunk(b"LIST", b"INFO" + title)
        + b"data"
        + struct.pack("<I", len(samples))
    )
    after = _chunk(b"cue ", cue)
    body = before + samples + after
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    start = 8 + len(before)

    return start, start + len(samples)


def test_a_wav_keeps_every_chunk_and_changes_only_samples(tmp_path: Path) -> None:
    """Writing it anew lost the title, broadcast and cue chunks, and the extensible format
    with its channel layout."""
    cover, embedded = tmp_path / "surround.wav", tmp_path / "embedded.wav"
    start, end = _surround_wav(cover)
    service.hide(cover, FILE, embedded, ENCRYPTED)
    before, after = cover.read_bytes(), embedded.read_bytes()

    assert len(after) == len(before)
    assert after[:start] == before[:start]
    assert after[end:] == before[end:]
    assert after[start:end] != before[start:end]
    assert np.abs(audio.read(embedded).samples - _samples(SURROUND)).max() == 1
    assert service.reveal(embedded, tmp_path, PASSWORD).path.read_bytes() == FILE.data


def test_a_cover_cut_or_shrunk_since_it_was_read_is_not_written_over(tmp_path: Path) -> None:
    cover, embedded = tmp_path / "surround.wav", tmp_path / "embedded.wav"
    _surround_wav(cover)
    hidden = audio.read(cover)
    data = cover.read_bytes()
    chunk = data.index(b"data")
    shrunk = data[: chunk + 4] + struct.pack("<I", 2) + data[chunk + 8 :]

    for changed in (data[:chunk], shrunk):
        cover.write_bytes(changed)

        with pytest.raises(UnsupportedFormatError, match=r"surround\.wav changed while hiding"):
            audio.write(embedded, hidden)


def test_a_wav_without_a_source_is_written_anew(tmp_path: Path) -> None:
    samples = _samples(2)
    audio.write(tmp_path / "new.wav", audio.Audio(audio.AudioInfo(FRAMES, 2, RATE, 16), samples))

    with wave.open(str(tmp_path / "new.wav"), "rb") as wav:
        assert (wav.getnchannels(), wav.getnframes()) == (2, FRAMES)

    assert np.array_equal(audio.read(tmp_path / "new.wav").samples, samples)


def _blocks(data: bytes) -> list[tuple[int, bytes]]:
    layout = audio._flac_layout(io.BytesIO(data), "x.flac")

    return [(block.kind, data[block.start : block.start + block.size]) for block in layout.blocks]


def _rebuilt(data: bytes, blocks: list[tuple[int, bytes]], prefix: bytes = b"") -> bytes:
    """``data``, a FLAC, with ``blocks`` in place of its metadata, after ``prefix``."""
    frames = data[audio._flac_layout(io.BytesIO(data), "x.flac").frames :]
    metadata = b"".join(
        bytes([kind | (0x80 if number == len(blocks) else 0)])
        + len(content).to_bytes(3, "big")
        + content
        for number, (kind, content) in enumerate(blocks, 1)
    )

    return prefix + b"fLaC" + metadata + frames


def _picture() -> bytes:
    mime, description, image = b"image/png", b"Cover", b"not really a PNG"
    fields = struct.pack(">I", 3) + struct.pack(">I", len(mime)) + mime
    fields += struct.pack(">I", len(description)) + description
    fields += struct.pack(">IIII", 16, 16, 24, 0) + struct.pack(">I", len(image)) + image

    return fields


def test_a_flac_keeps_its_tags_pictures_and_padding(tmp_path: Path) -> None:
    """soundfile writes only stream details and its own tags. The cover's seek table points
    into its own frames, so it goes."""
    soundfile = pytest.importorskip("soundfile")
    cover, embedded = tmp_path / "song.flac", tmp_path / "embedded.flac"

    with soundfile.SoundFile(str(cover), "w", RATE, 2, "PCM_16", format="FLAC") as song:
        song.title = "Harbour at dusk"
        song.write(_samples(2).astype(np.int16))

    data = cover.read_bytes()
    details, tags = _blocks(data)
    placeholder = bytes([0xFF] * 8) + bytes(10)
    id3 = b"ID3" + bytes([4, 0, 0, 0, 0, 0, 20]) + bytes(20)
    kept = [details, tags, (PICTURE, _picture()), (PADDING, bytes(64))]
    cover.write_bytes(_rebuilt(data, [*kept[:2], (SEEKTABLE, placeholder), *kept[2:]], id3))
    service.hide(cover, FILE, embedded, ENCRYPTED)
    written = embedded.read_bytes()

    assert written.startswith(id3 + b"fLaC")
    assert [kind for kind, _ in _blocks(written)] == [STREAMINFO, VORBIS_COMMENT, PICTURE, PADDING]
    assert _blocks(written)[1:] == kept[1:]

    with soundfile.SoundFile(str(embedded)) as song:
        assert song.title == "Harbour at dusk"

    assert np.abs(audio.read(embedded).samples - _samples(2)).max() == 1
    assert service.reveal(embedded, tmp_path, PASSWORD).path.read_bytes() == FILE.data
