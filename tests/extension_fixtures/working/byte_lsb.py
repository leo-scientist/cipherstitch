"""The lowest bit of each byte of a .fxb file carries one bit, in the keyed order when the
payload is encrypted. Wire id 200: the format vectors store it."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cipherstitch.extensions import (
    Bits,
    CarrierKind,
    Extension,
    MethodSpec,
    Order,
    SlotChannel,
    Tuning,
    Units,
)

_LOWEST_BIT = np.uint8(1)


@dataclass(frozen=True, slots=True)
class EmbeddedBytes:
    data: bytes

    def save(self, path: Path) -> None:
        path.write_bytes(self.data)


class ByteChannel(SlotChannel[EmbeddedBytes]):
    def __init__(self, path: Path, tuning: Tuning) -> None:
        self._values = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        super().__init__(self._values.size, 1)

    def _read_bits(self, units: Units) -> Bits:
        return self._values[units] & _LOWEST_BIT

    def _written(self, writes: Sequence[tuple[Units, Bits]]) -> EmbeddedBytes:
        values = self._values.copy()

        for units, bits in writes:
            values[units] = (values[units] & ~_LOWEST_BIT) | bits

        return EmbeddedBytes(values.tobytes())


EXTENSION = Extension(
    methods=(
        MethodSpec(
            "byte-lsb",
            200,
            "Byte LSB",
            CarrierKind.DOCUMENT,
            (".fxb",),
            ByteChannel,
            order=Order.KEYED_IF_ENCRYPTED,
            summary="Bits in the lowest bit of each byte.",
            pros=("Simple",),
            cons=("Easy to find",),
            capacity="1 bit per byte",
            risk="Anyone who compares the file with its original sees the changed bytes.",
        ),
    ),
    summary="A method for the tests",
)
