"""Audio carriers: 8/16/24-bit PCM WAV (stdlib ``wave``) and FLAC (``soundfile``, the
``audio`` extra). Samples are signed integers at the file's own bit depth, shape
``(frames, channels)``; a file is written back in the format it was read from, over the
file's own chunks or metadata blocks.
"""

import io
import shutil
import struct
import wave
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import numpy.typing as npt

from cipherstitch import catalog
from cipherstitch.errors import NotAvailableError, UnsupportedFormatError

Samples = npt.NDArray[np.int32]

_WAV_DEPTHS = {1: 8, 2: 16, 3: 24}  # sample width in bytes -> bits
_FLAC_DEPTHS = {"PCM_S8": 8, "PCM_16": 16, "PCM_24": 24}
# STREAMINFO stores the length in 36 bits; libsndfile reports 2**63 - 1 when it is unknown.
_FLAC_MAX_FRAMES = 1 << 36
# Samples (frames × channels) a recording may have: over 3 hours of 48 kHz stereo. A FLAC
# can decode to thousands of times its size, and the trellis channels number values in
# int32, so this must stay below 2**31.
_MAX_SAMPLES = 1 << 30
# A recording compresses to about half its 16-bit size, so it has a sample or two per byte
# of FLAC. Far more is near silence, which holds nothing, or a file made to exhaust memory.
_FLAC_MAX_SAMPLES_PER_BYTE = 32
_INT32_BITS = 32
_U8_OFFSET = 128  # 8-bit WAV samples are unsigned
_MAX_BYTE_RATE = 1 << 32  # a WAV header stores the bytes per second in 32 bits
# What stdlib wave lets through on a malformed file besides its own errors: RuntimeError for
# a chunk that runs past the end of the RIFF chunk.
_WAV_ERRORS = (wave.Error, EOFError, RuntimeError, struct.error)
_RIFF_HEADER = 12  # "RIFF", its size and "WAVE"
_CHUNK_HEADER = 8  # a chunk's id and size
_DATA_CHUNK = b"data"
_FLAC_MARKER = b"fLaC"
_FLAC_BLOCK_HEADER = 4  # a last-block flag, a type and a 24-bit size
_LAST_BLOCK = 0x80
_BLOCK_TYPE = 0x7F
# Far more than taggers write (tags, pictures, padding), and a bound on the walk over them.
_MAX_FLAC_BLOCKS = 1024
# The metadata a FLAC output keeps of its cover: padding, application data, tags, cue sheet
# and pictures. Its stream details describe the old frames, and its seek table points into
# them.
_KEPT_FLAC_BLOCKS = frozenset({1, 2, 4, 5, 6})
_ID3_MARKER = b"ID3"  # an ID3v2 tag, which libsndfile skips before a FLAC stream
_ID3_HEADER = 10
_SYNCSAFE_BITS = 7  # an ID3v2 size stores 7 bits per byte
_SECONDS_PER_MINUTE = 60
_CHANNEL_NAMES = {1: "mono", 2: "stereo"}


@dataclass(frozen=True, slots=True)
class AudioInfo:
    frames: int
    channels: int
    rate: int
    depth: int


@dataclass(frozen=True, slots=True)
class Audio:
    info: AudioInfo
    samples: Samples
    source: Path | None = None  # the file read, whose other chunks or blocks a write keeps

    def with_samples(self, samples: Samples) -> "Audio":
        return replace(self, samples=samples)


def info(path: Path) -> AudioInfo:
    """Format details from the header only."""
    if path.suffix.lower() == ".flac":
        return _flac_info(path)

    with _open_wav(path) as wav:
        return _wav_info(path, wav)


def read(path: Path) -> Audio:
    details = info(path)

    return Audio(details, read_frames(path, 0, details.frames), path)


def read_frames(path: Path, start: int, stop: int) -> Samples:
    """Frames ``start`` to ``stop`` (within the header's length), decoding nothing else."""
    if path.suffix.lower() == ".flac":
        samples = _read_flac_frames(path, start, stop)
    else:
        samples = _read_wav_frames(path, start, stop)

    if samples.shape[0] != stop - start:
        raise UnsupportedFormatError(f"{path.name} is shorter than its header says")

    return samples


def sample_range(depth: int) -> tuple[int, int]:
    return -(1 << (depth - 1)), (1 << (depth - 1)) - 1


def write(path: Path, audio: Audio) -> None:
    """``audio`` in ``path``'s format, over its source if that is in the same format: a WAV
    keeps its other chunks and a FLAC its metadata (docs/methods.md#audio)."""
    is_flac = _is_flac(path)
    source = audio.source
    cover = source if source is not None and _is_flac(source) is is_flac else None

    if is_flac:
        _write_flac(path, audio, cover)
    else:
        _write_wav(path, audio, cover)


def describe(path: Path) -> str:
    """E.g. ``3:12 · 44.1 kHz · 16-bit stereo · WAV``."""
    details = info(path)
    minutes, seconds = divmod(round(details.frames / details.rate), _SECONDS_PER_MINUTE)
    channels = _CHANNEL_NAMES.get(details.channels, f"{details.channels} ch")

    return (
        f"{minutes}:{seconds:02d} · {details.rate / 1000:g} kHz · "
        f"{details.depth}-bit {channels} · {path.suffix[1:].upper()}"
    )


@dataclass(frozen=True, slots=True)
class EmbeddedAudio:
    audio: Audio

    def save(self, path: Path) -> None:
        write(path, self.audio)


def _is_flac(path: Path) -> bool:
    return path.suffix.lower() == ".flac"


def _read_flac_frames(path: Path, start: int, stop: int) -> Samples:
    details = _flac_info(path)
    soundfile = _soundfile()

    try:
        data, _rate = soundfile.read(
            str(path), start=start, stop=stop, dtype="int32", always_2d=True
        )
    except soundfile.SoundFileError as exc:
        raise UnsupportedFormatError(f"{path.name} is not a readable FLAC file") from exc

    samples: Samples = np.asarray(data, dtype=np.int32)
    samples >>= _INT32_BITS - details.depth  # in place: the array is a fresh read

    return samples


def _read_wav_frames(path: Path, start: int, stop: int) -> Samples:
    with _open_wav(path) as wav:
        details = _wav_info(path, wav)
        wav.setpos(start)
        raw = wav.readframes(stop - start)

    frame_size = details.depth // 8 * details.channels
    raw = raw[: len(raw) - len(raw) % frame_size]  # a file cut mid-frame ends in a partial one

    return _decode_pcm(raw, details.depth).reshape(-1, details.channels)


def _write_flac(path: Path, audio: Audio, cover: Path | None) -> None:
    details = audio.info
    subtype = next(name for name, depth in _FLAC_DEPTHS.items() if depth == details.depth)
    aligned = audio.samples << (_INT32_BITS - details.depth)
    encoded = io.BytesIO()
    _soundfile().write(encoded, aligned, details.rate, subtype=subtype, format="FLAC")
    parts = [encoded.getbuffer()] if cover is None else _with_cover_blocks(encoded, cover)

    with path.open("wb") as output:
        output.writelines(parts)


def _with_cover_blocks(encoded: io.BytesIO, cover: Path) -> list[bytes | memoryview]:
    """The FLAC ``encoded``: its stream details and frames, with the cover's other metadata
    (:data:`_KEPT_FLAC_BLOCKS`) in the cover's order, after the cover's ID3v2 tag if it has
    one."""
    written = _flac_layout(encoded, cover.name)
    data = encoded.getbuffer()
    details = written.blocks[0]  # STREAMINFO always comes first
    blocks: list[tuple[int, bytes | memoryview]] = [
        (details.kind, data[details.start : details.start + details.size])
    ]

    with cover.open("rb") as file:
        layout = _flac_layout(file, cover.name)
        file.seek(0)
        tag = file.read(layout.stream)

        for block in layout.blocks:
            if block.kind in _KEPT_FLAC_BLOCKS:
                file.seek(block.start)
                blocks.append((block.kind, file.read(block.size)))

    parts: list[bytes | memoryview] = [tag, _FLAC_MARKER]

    for number, (kind, content) in enumerate(blocks, 1):
        flag = _LAST_BLOCK if number == len(blocks) else 0
        parts += [bytes([flag | kind]) + len(content).to_bytes(3, "big"), content]

    return [*parts, data[written.frames :]]


def _write_wav(path: Path, audio: Audio, cover: Path | None) -> None:
    details = audio.info
    samples = _encode_pcm(audio.samples, details.depth)

    if cover is None:
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(details.channels)
            wav.setsampwidth(details.depth // 8)
            wav.setframerate(details.rate)
            wav.writeframes(samples)

        return

    # The same frames at the same depth: only the samples change, in place.
    shutil.copyfile(cover, path)

    with path.open("r+b") as file:
        file.seek(_wav_samples_start(file, cover.name, len(samples)))
        file.write(samples)


def _wav_samples_start(file: BinaryIO, name: str, size: int) -> int:
    """Where the first data chunk's samples start, found as stdlib wave finds them: chunk
    after chunk, each padded to an even size. The chunk must hold ``size`` bytes."""
    file.seek(_RIFF_HEADER)

    while True:
        header = file.read(_CHUNK_HEADER)
        is_data = header[:4] == _DATA_CHUNK
        length = int.from_bytes(header[4:], "little")

        if len(header) < _CHUNK_HEADER or (is_data and length < size):
            raise UnsupportedFormatError(f"{name} changed while hiding")

        if is_data:
            return file.tell()

        file.seek(length + length % 2, io.SEEK_CUR)


def _decode_pcm(raw: bytes, depth: int) -> Samples:
    if depth == 8:
        return np.frombuffer(raw, dtype=np.uint8).astype(np.int32) - _U8_OFFSET

    if depth == 16:
        return np.frombuffer(raw, dtype="<i2").astype(np.int32)

    # Each 3-byte sample becomes the top of a little-endian int32; the arithmetic shift
    # then sign-extends it in place (no wider temporaries for long recordings).
    triples = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
    samples = np.zeros(len(triples), dtype="<i4")
    samples.view(np.uint8).reshape(-1, 4)[:, 1:] = triples
    samples >>= 8

    return samples.astype(np.int32, copy=False)


def _encode_pcm(samples: Samples, depth: int) -> bytes:
    if depth == 8:
        return (samples + _U8_OFFSET).astype(np.uint8).tobytes()

    if depth == 16:
        return samples.astype("<i2").tobytes()

    little_endian = samples.astype("<i4").reshape(-1, 1).view(np.uint8)

    return little_endian[:, :3].tobytes()


def _open_wav(path: Path) -> wave.Wave_read:
    try:
        return wave.open(str(path), "rb")
    except _WAV_ERRORS as exc:
        raise UnsupportedFormatError(f"{path.name} is not a PCM WAV file: {exc}") from exc


def _wav_info(path: Path, wav: wave.Wave_read) -> AudioInfo:
    depth = _WAV_DEPTHS.get(wav.getsampwidth())

    if depth is None:
        raise UnsupportedFormatError(
            f"{path.name}: {8 * wav.getsampwidth()}-bit WAV is not supported (8, 16 or 24-bit)"
        )

    if wav.getframerate() <= 0:
        raise UnsupportedFormatError(f"{path.name} has an invalid sample rate")

    if wav.getframerate() * wav.getnchannels() * wav.getsampwidth() >= _MAX_BYTE_RATE:
        raise UnsupportedFormatError(
            f"{path.name} has more channels or a higher sample rate than WAV supports"
        )

    # Streamed WAVs keep a placeholder data size; never trust more frames than the file holds.
    if wav.getnframes() * wav.getsampwidth() * wav.getnchannels() > path.stat().st_size:
        raise UnsupportedFormatError(f"{path.name} is shorter than its header says")

    _check_length(path, wav.getnframes(), wav.getnchannels())

    return AudioInfo(wav.getnframes(), wav.getnchannels(), wav.getframerate(), depth)


def _flac_info(path: Path) -> AudioInfo:
    soundfile = _soundfile()
    path.open("rb").close()  # a missing or unreadable file raises its own OSError

    try:
        details = soundfile.info(str(path))
    except soundfile.SoundFileError as exc:
        raise UnsupportedFormatError(f"{path.name} is not a readable FLAC file") from exc

    depth = _FLAC_DEPTHS.get(details.subtype)

    if depth is None:
        raise UnsupportedFormatError(f"{path.name}: {details.subtype} FLAC is not supported")

    if details.frames >= _FLAC_MAX_FRAMES:
        raise UnsupportedFormatError(
            f"{path.name} does not record its length. Re-encode it and try again"
        )

    _check_length(path, details.frames, details.channels)

    with path.open("rb") as file:
        layout = _flac_layout(file, path.name)

    # Metadata, padding included, can take any room: only the frames count.
    if details.frames * details.channels > _FLAC_MAX_SAMPLES_PER_BYTE * layout.frame_bytes:
        raise UnsupportedFormatError(f"{path.name} decodes to far more than a recording would")

    return AudioInfo(details.frames, details.channels, details.samplerate, depth)


@dataclass(frozen=True, slots=True)
class _FlacBlock:
    kind: int
    start: int  # where its content starts in the file
    size: int


@dataclass(frozen=True, slots=True)
class _FlacLayout:
    stream: int  # where the stream starts, after an ID3v2 tag
    blocks: tuple[_FlacBlock, ...]
    frames: int  # where the frames start
    end: int  # the file's size

    @property
    def frame_bytes(self) -> int:
        return self.end - self.frames


def _flac_layout(file: BinaryIO, name: str) -> _FlacLayout:
    """A FLAC's metadata blocks and where its frames start, read from the headers alone."""
    end = file.seek(0, io.SEEK_END)
    stream = _id3_size(file)
    file.seek(stream)

    if file.read(len(_FLAC_MARKER)) != _FLAC_MARKER:
        raise _unreadable_flac(name)

    blocks: list[_FlacBlock] = []
    is_last = False

    while not is_last:
        header = file.read(_FLAC_BLOCK_HEADER)

        if len(header) < _FLAC_BLOCK_HEADER or len(blocks) == _MAX_FLAC_BLOCKS:
            raise _unreadable_flac(name)

        is_last = bool(header[0] & _LAST_BLOCK)
        size = int.from_bytes(header[1:], "big")
        blocks.append(_FlacBlock(header[0] & _BLOCK_TYPE, file.tell(), size))
        file.seek(size, io.SEEK_CUR)

    if file.tell() > end:
        raise _unreadable_flac(name)

    return _FlacLayout(stream, tuple(blocks), file.tell(), end)


def _id3_size(file: BinaryIO) -> int:
    """The size of the ID3v2 tag before a FLAC stream, 0 if there is none."""
    file.seek(0)
    header = file.read(_ID3_HEADER)

    if len(header) < _ID3_HEADER or not header.startswith(_ID3_MARKER):
        return 0

    size = 0

    for byte in header[6:]:
        size = size << _SYNCSAFE_BITS | byte & 0x7F

    return _ID3_HEADER + size


def _unreadable_flac(name: str) -> UnsupportedFormatError:
    return UnsupportedFormatError(f"{name} is not a readable FLAC file")


def _check_length(path: Path, frames: int, channels: int) -> None:
    """Refuse a recording too long to decode, before allocating its samples."""
    if frames * channels > _MAX_SAMPLES:
        raise UnsupportedFormatError(f"{path.name} is too long to read")


def _soundfile() -> Any:
    try:
        import soundfile
    except ImportError as exc:
        raise NotAvailableError(catalog.FORMAT_REQUIREMENTS[".flac"].install_hint("FLAC")) from exc

    return soundfile
