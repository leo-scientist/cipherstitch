"""A keyed bijection on ``[0, count)``: a Feistel network with cycle-walking
(docs/format.md#keyed-order). It is evaluated at any positions, so a whole order is never
stored."""

import hashlib
import math

import numpy as np
import numpy.typing as npt

ROUNDS = 6
UNKEYED_SEED = bytes(32)  # keys the public order of payloads hidden without a password
_LABEL = b"cipherstitch order"


def golden_multiplier(count: int) -> int:
    """The first integer from about 0.618 · count on that is coprime to it. Multiplying by
    it mod ``count`` numbers units so that runs of them cover the whole carrier."""
    multiplier = (math.isqrt(5 * count * count) - count) // 2

    while math.gcd(multiplier, count) != 1:
        multiplier += 1

    return multiplier


class Permutation:
    """π on ``[0, count)`` keyed by ``seed``, for ``count`` below 2³¹, over ``rounds`` rounds:
    any number is a bijection, fewer are less random."""

    def __init__(self, seed: bytes, count: int, rounds: int = ROUNDS) -> None:
        # container.SEQUENTIAL_SEED asks for no order at all: keying one with it would put the
        # units in another order without a word.
        if not seed:
            raise ValueError("An empty seed keys no order")

        bits = max(2, (count - 1).bit_length())
        # The halves alternate, as in NIST FF1: A (the high bits) and B, B never narrower.
        self._widths = (bits // 2, bits - bits // 2)
        self._count = count
        size = 1 << self._widths[1]
        raw = hashlib.shake_256(seed + _LABEL).digest(4 * rounds * size)
        masks = np.array([(1 << self._widths[r % 2]) - 1 for r in range(rounds)], np.uint32)
        tables = np.frombuffer(raw, "<u4").reshape(rounds, size) & masks[:, None]
        # int64 fancy indexing gathers several times faster than np.take or uint32 indices.
        self._tables = list(tables.astype(np.int64))

    def at(self, positions: npt.NDArray[np.integer]) -> npt.NDArray[np.int32]:
        """π at each of ``positions`` (all below ``count``)."""
        values = self._network(positions.astype(np.int64))
        pending = np.flatnonzero(values >= self._count)

        # The network permutes the whole 2^bits domain, so every walk comes back below count.
        while pending.size:
            walked = self._network(values[pending])
            values[pending] = walked
            pending = pending[walked >= self._count]

        return values.astype(np.int32)

    def _network(self, x: npt.NDArray[np.int64]) -> npt.NDArray[np.int64]:
        low = self._widths[1]
        a, b = x >> low, x & ((1 << low) - 1)

        for table in self._tables:
            a, b = b, a ^ table[b]

        return (a << low) | b
