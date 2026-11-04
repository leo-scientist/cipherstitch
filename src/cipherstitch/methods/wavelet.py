"""The Daubechies 8 filter bank of the UNIWARD costs (J-UNIWARD, S-UNIWARD) and WOW: three
directional high-pass filters, each a pair of 16 taps, and filtering with mirrored edges."""

import numpy as np
import numpy.typing as npt

# Decomposition filters: the high pass, and the low pass mirrored from it.
HIGH = np.array(
    [-0.0544158422, 0.3128715909, -0.6756307363, 0.5853546837, 0.0158291053, -0.2840155430,
     -0.0004724846, 0.1287474266, 0.0173693010, -0.0440882539, -0.0139810279, 0.0087460940,
     0.0048703530, -0.0003917404, -0.0006754494, -0.0001174768]
)  # fmt: skip
LOW = (-1.0) ** np.arange(HIGH.size) * HIGH[::-1]
TAPS_OF = {"low": LOW, "high": HIGH}
# The three filters, as (down the columns, along the rows): LH, HL and HH.
FILTERS = (("low", "high"), ("high", "low"), ("high", "high"))
TAPS = HIGH.size


def mirrored(indices: npt.NDArray[np.int64], size: int) -> npt.NDArray[np.int64]:
    """Indices reflected at both ends, the edge value repeated (… b a | a b …), as often as
    a margin wider than ``size`` needs."""
    folded = np.mod(indices, 2 * size)

    return np.where(folded >= size, 2 * size - 1 - folded, folded)


def filtered(
    values: npt.NDArray[np.float32], taps: npt.NDArray[np.float64], axis: int
) -> npt.NDArray[np.float32]:
    """The correlation with ``taps`` where it covers ``values`` whole along ``axis``."""
    length = values.shape[axis] - taps.size + 1
    moved = np.moveaxis(values, axis, 0)
    result = np.float32(taps[0]) * moved[:length]

    for u in range(1, taps.size):
        result += np.float32(taps[u]) * moved[u : u + length]

    return np.moveaxis(result, 0, axis)
