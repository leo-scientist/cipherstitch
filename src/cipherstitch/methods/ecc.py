"""Reed–Solomon framing for audio methods whose embedded bits can flip: a single wrong bit
would fail the authenticated decryption (docs/methods.md#error-correction)."""

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from cipherstitch import catalog
from cipherstitch.errors import CapacityError, NotAvailableError
from cipherstitch.media import audio
from cipherstitch.media.audio import AudioInfo, EmbeddedAudio, Samples
from cipherstitch.methods.slots import to_bits
from cipherstitch.methods.stream import StreamChannel

CODEWORD = 255
PARITY = 32
DATA = CODEWORD - PARITY
_CODEWORD_BITS = CODEWORD * 8


def capacity(raw_size: int, parity: int = PARITY) -> int:
    """Stream bytes that ``raw_size`` embedded bytes carry after error correction with
    ``parity`` bytes per codeword."""
    return raw_size // CODEWORD * (CODEWORD - parity)


def encode(stream: bytes, parity: int = PARITY) -> bytes:
    padded = stream + os.urandom(-len(stream) % (CODEWORD - parity))

    return bytes(_reedsolo().RSCodec(parity).encode(padded))


def decode(raw: bytes, parity: int = PARITY) -> bytes:
    """The data of the leading codewords that decode; stops at the first that does not."""
    reedsolo = _reedsolo()
    codec = reedsolo.RSCodec(parity)
    data = bytearray()

    for start in range(0, len(raw) - CODEWORD + 1, CODEWORD):
        try:
            decoded = codec.decode(raw[start : start + CODEWORD])[0]
        except reedsolo.ReedSolomonError:
            break

        data += decoded

    return bytes(data)


class EccAudioChannel(StreamChannel[EmbeddedAudio], ABC):
    """After embedding, the quantized result is decoded again: clipping, silence or rounding
    can flip more bits than the code repairs, and then hiding fails instead of producing a
    carrier that cannot be revealed."""

    _method_name: str

    def __init__(self, path: Path, parity: int = PARITY) -> None:
        _reedsolo()  # report a missing extra on opening
        self._path = path
        self._parity = parity
        self._info = audio.info(path)
        super().__init__(capacity(self._bit_capacity(self._info) // 8, parity))

    def _read_stream(self) -> bytes:
        """Codeword by codeword while they decode: a recording without a payload costs one
        codeword's worth of audio, not the whole file."""
        data = bytearray()
        last = self._bit_capacity(self._info) - _CODEWORD_BITS

        for first in range(0, last + 1, _CODEWORD_BITS):
            decoded = self._decode_codeword(first)

            if not decoded:
                break

            data += decoded

        return bytes(data)

    def _decode_codeword(self, first: int) -> bytes:
        """The data of the codeword whose raw bits start at ``first``; empty if it does not
        decode."""
        start, stop = self._frames(self._info, first, _CODEWORD_BITS)
        window = audio.read_frames(self._path, start, stop)
        bits = self._extract(window, self._info, first, _CODEWORD_BITS)

        return decode(np.packbits(bits).tobytes(), self._parity)

    def _written(self, stream: bytes) -> EmbeddedAudio:
        bits = to_bits(encode(stream, self._parity))
        original = audio.read(self._path)
        samples = self._embed(original.samples, self._info, bits)

        extracted = self._extract(samples, self._info, 0, bits.size)
        readback = decode(np.packbits(extracted).tobytes(), self._parity)

        if readback[: len(stream)] != stream:
            raise CapacityError(
                f"{self._path.name} can't hold this payload reliably with {self._method_name} "
                "(it is too quiet or clips). Try another method or recording"
            )

        return EmbeddedAudio(original.with_samples(samples))

    @abstractmethod
    def _bit_capacity(self, info: AudioInfo) -> int:
        """Raw bits the recording can carry."""

    @abstractmethod
    def _embed(self, samples: Samples, info: AudioInfo, bits: npt.NDArray[np.uint8]) -> Samples:
        """``samples`` carrying ``bits`` from the start, rounded and clipped to the depth."""

    @abstractmethod
    def _frames(self, info: AudioInfo, first: int, count: int) -> tuple[int, int]:
        """The frames (start, stop) that carry the raw bits ``first`` to ``first + count``."""

    @abstractmethod
    def _extract(
        self, window: Samples, info: AudioInfo, first: int, count: int
    ) -> npt.NDArray[np.uint8]:
        """The raw bits ``first`` to ``first + count`` from ``window``, the frames from where
        :meth:`_frames` starts (fewer bits if the window ends)."""


def _reedsolo() -> Any:
    try:
        import reedsolo
    except ImportError as exc:
        raise NotAvailableError(
            catalog.REEDSOLO.install_hint("Phase coding or echo hiding")
        ) from exc

    return reedsolo
