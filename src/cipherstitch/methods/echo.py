"""Echo hiding: each bit adds a faint echo to its own segment, delayed by 1 ms for a 0 or
1.5 ms for a 1 (docs/methods.md#echo-hiding)."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from cipherstitch.media.audio import AudioInfo, Samples, sample_range
from cipherstitch.methods import ecc
from cipherstitch.methods.ecc import EccAudioChannel

SEGMENT_MS = 25
DELAY_SCALE = 10  # delays are in tenths of a millisecond, which binary fractions miss
ZERO_DELAY = 10
ONE_DELAY = 15
DECAY = 0.5
_MIN_SEGMENT = 512
_TENTHS_PER_SECOND = 1000 * DELAY_SCALE
_LOG_FLOOR = 1e-9


@dataclass(frozen=True, slots=True)
class EchoRules:
    """The segment length, the echo delays for a 0 and a 1, the echo's level and the
    Reed–Solomon parity."""

    segment_ms: int = SEGMENT_MS
    zero_delay: int = ZERO_DELAY  # tenths of a millisecond
    one_delay: int = ONE_DELAY
    decay: float = DECAY
    parity: int = ecc.PARITY


STANDARD_ECHO = EchoRules()


def _layout(rate: int, rules: EchoRules = STANDARD_ECHO) -> tuple[int, int, int]:
    """Segment length and the echo delays for a 0 and a 1, in samples."""
    return (
        max(round(rate * (rules.segment_ms / 1000)), _MIN_SEGMENT),
        max(rate * rules.zero_delay // _TENTHS_PER_SECOND, 2),
        max(rate * rules.one_delay // _TENTHS_PER_SECOND, 3),
    )


def _smoothed(signal: npt.NDArray[np.float64], width: int) -> npt.NDArray[np.float64]:
    """Moving average of ``width`` samples, aligned with ``signal``."""
    sums = np.cumsum(
        np.concatenate([[0.0], np.full(width // 2, signal[0]), signal, np.full(width, signal[-1])])
    )
    average = sums[width : width + signal.size] - sums[: signal.size]
    average /= width

    return average


def _gains(
    bits: npt.NDArray[np.uint8], segment: int, width: int, stop: int, decay: float
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """The gains of the echo for a 1 and the echo for a 0 for the first ``stop`` samples."""
    used = bits.size * segment
    ones = np.zeros(stop)
    ones[:used] = np.repeat(bits.astype(np.float64), segment)
    one_gain = _smoothed(ones, width)
    one_gain *= decay
    del ones  # one recording-long array fewer while the other gain is computed

    active = np.zeros(stop)
    active[:used] = 1.0
    zero_gain = _smoothed(active, width)
    zero_gain *= decay
    zero_gain -= one_gain

    return one_gain, zero_gain


class EchoChannel(EccAudioChannel):
    _method_name = "echo hiding"

    def __init__(self, path: Path, rules: EchoRules = STANDARD_ECHO) -> None:
        self._rules = rules
        super().__init__(path, rules.parity)

    def _bit_capacity(self, info: AudioInfo) -> int:
        return info.frames // _layout(info.rate, self._rules)[0]

    def _embed(self, samples: Samples, info: AudioInfo, bits: npt.NDArray[np.uint8]) -> Samples:
        segment, zero, one = _layout(info.rate, self._rules)
        used = bits.size * segment
        width = segment // 4  # echo changes cross-fade over a quarter of a segment
        stop = min(used + width, samples.shape[0])  # the echoes fade out within width // 2
        one_gain, zero_gain = _gains(bits, segment, width, stop, self._rules.decay)

        low, high = sample_range(info.depth)
        result = samples.copy()
        original, echoed, echo = np.empty(stop), np.empty(stop), np.empty(stop)

        for channel in range(samples.shape[1]):
            original[:] = samples[:stop, channel]
            echoed[:] = original
            np.multiply(zero_gain[zero:], original[:-zero], out=echo[zero:])
            echoed[zero:] += echo[zero:]
            np.multiply(one_gain[one:], original[:-one], out=echo[one:])
            echoed[one:] += echo[one:]
            np.round(echoed, out=echoed)
            result[:stop, channel] = np.clip(echoed, low, high, out=echoed)

        return result

    def _frames(self, info: AudioInfo, first: int, count: int) -> tuple[int, int]:
        segment = _layout(info.rate, self._rules)[0]  # one bit per segment

        return first * segment, (first + count) * segment

    def _extract(
        self, window: Samples, info: AudioInfo, first: int, count: int
    ) -> npt.NDArray[np.uint8]:
        segment, zero, one = _layout(info.rate, self._rules)
        count = min(count, window.shape[0] // segment)

        if count <= 0:
            return np.zeros(0, dtype=np.uint8)

        mixed = window[: count * segment].sum(axis=1, dtype=np.float64)
        frames = mixed.reshape(count, segment) * np.hanning(segment)
        spectrum = np.abs(np.fft.rfft(frames, axis=1))
        cepstrum = np.fft.irfft(np.log(spectrum + _LOG_FLOOR), axis=1)

        return (cepstrum[:, one] > cepstrum[:, zero]).astype(np.uint8)
