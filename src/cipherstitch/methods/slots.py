"""Bytes spread over the fixed-width units of a carrier (docs/format.md#slot-channels)."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from cipherstitch.container import SEQUENTIAL_SEED
from cipherstitch.errors import CapacityError
from cipherstitch.methods.feistel import ROUNDS, Permutation

Units = slice | npt.NDArray[np.integer]
Bits = npt.NDArray[np.uint8]

_BITS_PER_BYTE = 8


def to_bits(data: bytes) -> Bits:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8))


class SlotChannel[Output](ABC):
    """A :class:`cipherstitch.container.Channel` over ``unit_count`` units of fixed width,
    keyed over ``rounds`` Feistel rounds."""

    def __init__(self, unit_count: int, bits_per_unit: int, rounds: int = ROUNDS) -> None:
        self.unit_count = unit_count
        self.bits_per_unit = bits_per_unit
        self._rounds = rounds

    def units_for(self, size: int) -> int:
        """Units needed to carry ``size`` bytes."""
        return -(-size * _BITS_PER_BYTE // self.bits_per_unit)

    def capacity(self, prefix_size: int) -> int:
        free_units = max(self.unit_count - self.units_for(prefix_size), 0)

        return free_units * self.bits_per_unit // _BITS_PER_BYTE

    def stored_limit(self, prefix_size: int) -> int:
        return self.capacity(prefix_size)

    def read(self, prefix_size: int, size: int, seed: bytes | None) -> bytes:
        bits = self._read_bits(self._units(prefix_size, seed, 0, self.units_for(size)))
        whole_bytes = min(size, bits.size // _BITS_PER_BYTE)

        return np.packbits(bits[: whole_bytes * _BITS_PER_BYTE]).tobytes()

    def write(self, prefix: bytes, body: bytes, seed: bytes | None) -> Output:
        self._check_room(len(prefix), len(body))
        prefix_units = self._units(0, None, 0, self.units_for(len(prefix)))
        body_units = self._units(len(prefix), seed, 0, self.units_for(len(body)))

        return self._written(((prefix_units, to_bits(prefix)), (body_units, to_bits(body))))

    @abstractmethod
    def _read_bits(self, units: Units) -> Bits:
        """The bits carried by ``units``, unit after unit."""

    @abstractmethod
    def _written(self, writes: Sequence[tuple[Units, Bits]]) -> Output:
        """The carrier with each bit array stored in the first bits of its units."""

    def _check_room(self, prefix_size: int, body_size: int) -> None:
        available = self.capacity(prefix_size)

        if prefix_size > self.capacity(0) or body_size > available:
            raise CapacityError(
                f"Payload is {body_size} bytes, this carrier holds at most {available}"
            )

    def _units(self, prefix_size: int, seed: bytes | None, start: int, stop: int) -> Units:
        """The body's units ``start … stop − 1`` (as many as there are) after a prefix of
        ``prefix_size`` bytes: in sequence without a seed or with :data:`SEQUENTIAL_SEED`, or
        in the keyed order of ``seed`` (docs/format.md#keyed-order)."""
        skip = self.units_for(prefix_size)
        count = max(self.unit_count - skip, 0)
        stop = min(stop, count)
        start = min(start, stop)

        if seed is None or seed == SEQUENTIAL_SEED:
            return slice(skip + start, skip + stop)

        return skip + Permutation(seed, count, self._rounds).at(np.arange(start, stop))
