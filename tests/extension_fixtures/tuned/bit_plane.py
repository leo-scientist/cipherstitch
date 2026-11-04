"""One bit of each byte of a .fxb file carries a bit, the parameter plane.bit says which, in
the keyed order when the payload is encrypted. Wire id 210."""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cipherstitch.extensions import (
    Bits,
    CarrierKind,
    Extension,
    Group,
    Kind,
    MethodSpec,
    Order,
    Param,
    ParamType,
    Scope,
    SlotChannel,
    Tuning,
    Units,
)


@dataclass(frozen=True, slots=True)
class EmbeddedBytes:
    data: bytes

    def save(self, path: Path) -> None:
        path.write_bytes(self.data)


class PlaneChannel(SlotChannel[EmbeddedBytes]):
    def __init__(self, path: Path, tuning: Tuning) -> None:
        self._values = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        self._bit = int(tuning.extension_value("plane.bit"))
        self._fills = bool(tuning.extension_value("plane.fill"))
        super().__init__(self._values.size, 1)

    def _read_bits(self, units: Units) -> Bits:
        return ((self._values[units] >> self._bit) & 1).astype(np.uint8)

    def _written(self, writes: Sequence[tuple[Units, Bits]]) -> EmbeddedBytes:
        mask = np.uint8(1 << self._bit)
        values = self._values.copy()

        if self._fills:
            noise = np.frombuffer(os.urandom(values.size), dtype=np.uint8)
            values = (values & ~mask) | (noise & mask)

        for units, bits in writes:
            values[units] = (values[units] & ~mask) | (bits << self._bit)

        return EmbeddedBytes(values.tobytes())


PLANE = Group(
    "plane",
    "Bit plane",
    "squares-four",
    "Which bit of each byte holds the payload.",
    (
        Param(
            "bit",
            "Bit",
            "PlaneChannel._bit",
            ParamType.INT,
            0,
            Kind.FORMAT,
            minimum=0,
            maximum=7,
            step=1,
            note="0 is the lowest bit.",
        ),
        Param(
            "fill",
            "Fill the rest",
            "PlaneChannel._fills",
            ParamType.BOOL,
            False,
            Kind.EMBEDDER,
            note="Random bits in the bytes the payload leaves.",
        ),
    ),
    scope=Scope(frozenset({"bit-plane"})),
)
EXTENSION = Extension(
    methods=(
        MethodSpec(
            "bit-plane",
            210,
            "Bit plane",
            CarrierKind.DOCUMENT,
            (".fxb",),
            PlaneChannel,
            order=Order.KEYED_IF_ENCRYPTED,
        ),
    ),
    params=(PLANE,),
)
