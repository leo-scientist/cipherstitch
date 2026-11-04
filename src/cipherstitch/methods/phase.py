"""Phase coding, where bits set the phase of frequency bins in segments of the first
channel, and every channel takes the same phase shift (docs/methods.md#phase-coding)."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from cipherstitch.media.audio import AudioInfo, Samples, sample_range
from cipherstitch.methods import ecc
from cipherstitch.methods.ecc import EccAudioChannel

SEGMENT = 4096
FIRST_BIN = 32
BINS = 64
RAMP = 8  # segments over which the shift returns to 0 after the data
FLOOR = 8  # the coded bins' magnitude floor, in one bin's rounding noise
_CHUNK = 256  # segments transformed at a time


@dataclass(frozen=True, slots=True)
class PhaseRules:
    """The segment layout, its ramp and floor, and the Reed–Solomon parity."""

    segment: int = SEGMENT
    first_bin: int = FIRST_BIN
    bins: int = BINS
    ramp: int = RAMP
    floor: int = FLOOR
    parity: int = ecc.PARITY

    @property
    def band(self) -> slice:
        return slice(self.first_bin, self.first_bin + self.bins)

    @property
    def magnitude_floor(self) -> float:
        """``floor`` times the rounding noise of one bin."""
        return float(self.floor * np.sqrt(self.segment / 12))


STANDARD_PHASE = PhaseRules()


class PhaseChannel(EccAudioChannel):
    _method_name = "phase coding"

    def __init__(self, path: Path, rules: PhaseRules = STANDARD_PHASE) -> None:
        self._rules = rules
        super().__init__(path, rules.parity)

    def _bit_capacity(self, info: AudioInfo) -> int:
        return info.frames // self._rules.segment * self._rules.bins

    def _embed(self, samples: Samples, info: AudioInfo, bits: npt.NDArray[np.uint8]) -> Samples:
        """The segments after the ramp stay exactly as they were."""
        rules = self._rules
        targets = _target_phases(bits, rules.bins)
        low, high = sample_range(info.depth)
        result = samples.copy()
        last = np.zeros(rules.bins)
        count = min(len(targets) + rules.ramp, samples.shape[0] // rules.segment)
        # Decided over all the data segments, so the chunks do not change the result.
        is_silent = ~np.any(samples[: len(targets) * rules.segment], axis=0)
        stays_silent = is_silent & ~is_silent[0]

        for first in range(0, count, _CHUNK):  # bounded temporaries for long recordings
            start, stop = first * rules.segment, min(first + _CHUNK, count) * rules.segment
            segments = samples[start:stop].astype(np.float64)
            chunk_targets = targets[first : first + _CHUNK]
            ramped = max(first - len(targets), 0)
            coded, last = _coded(segments, chunk_targets, last, ramped, stays_silent, rules)
            result[start:stop] = np.clip(np.round(coded), low, high)

        return result

    def _frames(self, info: AudioInfo, first: int, count: int) -> tuple[int, int]:
        segment, bins = self._rules.segment, self._rules.bins

        return first // bins * segment, _ceil_div(first + count, bins) * segment

    def _extract(
        self, window: Samples, info: AudioInfo, first: int, count: int
    ) -> npt.NDArray[np.uint8]:
        segment, bins = self._rules.segment, self._rules.bins
        start = first % bins  # the window starts with the segment holding bit ``first``
        segments = min(_ceil_div(start + count, bins), window.shape[0] // segment)

        if segments <= 0:
            return np.zeros(0, dtype=np.uint8)

        block = window[: segments * segment, 0].astype(np.float64)
        spectrum = np.fft.rfft(block.reshape(-1, segment), axis=1)
        bits = (np.angle(spectrum[:, self._rules.band]) < 0).astype(np.uint8).reshape(-1)

        return bits[start : start + count]


def _coded(
    segments: npt.NDArray[np.float64],
    targets: npt.NDArray[np.float64],
    last: npt.NDArray[np.float64],
    ramped: int,
    stays_silent: npt.NDArray[np.bool_],
    rules: PhaseRules,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Consecutive segments of every channel, the leading ones with the first channel's bins
    set to ``targets``, and the last data segment's shift (``last`` if none is here). Every
    channel takes the first channel's shift, so the channels keep their phase differences.
    After the data the shift returns to 0 over ``rules.ramp`` segments, ``ramped`` of which
    came before. The first channel's coded bins get a magnitude floor, so quantization cannot
    flip a bit. Another channel's get it only where they equal the first's, so a mono
    recording stays mono, and never in the channels ``stays_silent`` marks: those silent in
    every data segment, unlike the first. In one silent segment the two cases look alike."""
    frames, channels = segments.shape
    spectrum = np.fft.rfft(segments.reshape(-1, rules.segment, channels), axis=1)
    band = spectrum[:, rules.band]

    used = len(targets)
    shift = np.empty((len(spectrum), rules.bins))
    shift[:used] = targets - np.angle(band[:used, :, 0])
    last = shift[used - 1] if used else last
    steps = ramped + np.arange(1, len(spectrum) - used + 1)
    shift[used:] = np.angle(np.exp(1j * last)) * (1 - steps / (rules.ramp + 1))[:, None]

    magnitude, phase = np.abs(band), np.angle(band) + shift[..., None]
    same = (band[:used] == band[:used, :, :1]) & ~stays_silent
    floor = rules.magnitude_floor
    magnitude[:used] = np.where(same, np.maximum(magnitude[:used], floor), magnitude[:used])
    spectrum[:, rules.band] = magnitude * np.exp(1j * phase)
    coded = np.fft.irfft(spectrum, n=rules.segment, axis=1).reshape(frames, channels)

    return coded, last


def _target_phases(bits: npt.NDArray[np.uint8], bins: int) -> npt.NDArray[np.float64]:
    """One row of ``bins`` phases per segment: +π/2 for a 0 (and the padding), −π/2 for a 1."""
    used = _ceil_div(bits.size, bins)
    padded = np.zeros(used * bins, dtype=np.uint8)
    padded[: bits.size] = bits

    return np.where(padded.reshape(used, bins) == 0, np.pi / 2, -np.pi / 2)


def _ceil_div(dividend: int, divisor: int) -> int:
    return -(-dividend // divisor)
