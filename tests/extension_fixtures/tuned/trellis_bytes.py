"""Each byte of a .fxt file is a value the trellis steps by ±1 at the same cost, so the STC
parameters apply. Wire id 211."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from cipherstitch.extensions import (
    CarrierKind,
    Extension,
    MethodSpec,
    Order,
    TrellisChannel,
    Tuning,
)

_LOWEST, _HIGHEST = 0, 255


@dataclass(frozen=True, slots=True)
class EmbeddedBytes:
    data: bytes

    def save(self, path: Path) -> None:
        path.write_bytes(self.data)


class ByteValues(TrellisChannel[EmbeddedBytes]):
    _method_name = "Trellis bytes"

    def __init__(self, path: Path, tuning: Tuning) -> None:
        self._bytes = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        super().__init__(path, self._bytes.size, tuning.coding)

    def _values(self) -> npt.NDArray[np.integer]:
        return self._bytes

    def _counted(self) -> npt.NDArray[np.bool_]:
        return (self._bytes > _LOWEST) & (self._bytes < _HIGHEST)

    def _costs(self, rate: float) -> npt.NDArray[np.float32]:
        return np.ones(self._bytes.size, dtype=np.float32)

    def _value_range(self) -> tuple[int, int]:
        return _LOWEST, _HIGHEST

    def _rebuilt(self, values: npt.NDArray[np.integer]) -> EmbeddedBytes:
        return EmbeddedBytes(values.astype(np.uint8).tobytes())


EXTENSION = Extension(
    methods=(
        MethodSpec(
            "trellis-bytes",
            211,
            "Trellis bytes",
            CarrierKind.DOCUMENT,
            (".fxt",),
            ByteValues,
            order=Order.KEYED_IF_ENCRYPTED,
            codes_with_trellis=True,
        ),
    ),
)
