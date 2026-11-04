"""Sequential LSB replacement, the Experimental baseline that detector tests start from: the
stream's bits overwrite the lowest bit of each colour value, from the first pixel on
(docs/format.md#lsb-replacement). Half the values it touches change, each by one, and only
ever within its pair (2k, 2k + 1), which is what WS, SPA and χ² detect."""

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from cipherstitch.media.image import EmbeddedImage
from cipherstitch.methods.picture import Picture
from cipherstitch.methods.slots import Bits, SlotChannel, Units

_LOWEST_BIT = np.uint8(1)


class LsbReplacementChannel(SlotChannel[EmbeddedImage]):
    """Each colour value is a unit of one bit (docs/format.md#slot-channels), never keyed."""

    def __init__(self, path: Path, *, keeps_metadata: bool = True) -> None:
        self._picture = Picture(path, keeps_metadata=keeps_metadata)
        super().__init__(self._picture.value_count, 1)

    def _read_bits(self, units: Units) -> Bits:
        return self._picture.values()[units] & _LOWEST_BIT

    def _written(self, writes: Sequence[tuple[Units, Bits]]) -> EmbeddedImage:
        values = self._picture.values().copy()

        for units, bits in writes:
            values[units] = (values[units] & ~_LOWEST_BIT) | bits

        return self._picture.rebuilt(values)
