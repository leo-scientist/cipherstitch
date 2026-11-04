"""WOW and S-UNIWARD costs for image LSB, from the Daubechies 8 filter bank J-UNIWARD uses.

For each of the three directional filters K, the residual is the convolution R = X * K of a
colour plane X, with symmetric edges, as the authors' code computes it. A change at a pixel
reaches the residuals under the filter's 16 × 16 footprint, each weighted by |K| there:

- WOW (Holub and Fridrich, 2012): the change's embedding suitability is ξ = Σ |K| · |R| over
  that footprint, and it costs Σ 1 / ξ over the three filters (their aggregation with
  p = −1), at most :data:`WET`, where no filter sees any texture.
- S-UNIWARD (Holub, Fridrich and Denemark, 2014): it costs the relative change it makes to
  the residuals, Σ |K| / (σ + |R|) over the footprint and the filters, with σ = 1.

The footprint is aligned with the change, so a cost weighs exactly the residuals the change
moves. Computed per colour plane in stretches of rows, so no float copy of the whole
picture exists besides the costs.
"""

from collections.abc import Callable, Iterator

import numpy as np
import numpy.typing as npt

from cipherstitch.methods.wavelet import FILTERS, TAPS, TAPS_OF, filtered, mirrored

Costs = npt.NDArray[np.float32]
Plane = npt.NDArray[np.uint8]

SIGMA = 1.0  # S-UNIWARD's stabilizer for pixels
WET = 1e10  # WOW's cost where no filter sees texture: finite for the search
_FLOOR = np.float32(len(FILTERS) / WET)  # the least suitability: three filters add up to WET
_STRETCH = 1 << 20  # values per stretch


def wow_costs(colors: npt.NDArray[np.uint8]) -> Costs:
    """The cost of changing each value of ``colors`` (rows, columns, colours) by one."""
    return _costs(colors, _wow)


def suniward_costs(colors: npt.NDArray[np.uint8]) -> Costs:
    """The cost of changing each value of ``colors`` (rows, columns, colours) by one."""
    return _costs(colors, _suniward)


def _costs(colors: npt.NDArray[np.uint8], stretch: Callable[[Plane, int, int], Costs]) -> Costs:
    rows, columns, planes = colors.shape
    result = np.empty(colors.shape, dtype=np.float32)
    step = max(_STRETCH // max(columns, 1), 1)

    for color in range(planes):
        for start in range(0, rows, step):
            stop = min(start + step, rows)
            result[start:stop, :, color] = stretch(colors[..., color], start, stop)

    return result


def _wow(plane: Plane, start: int, stop: int) -> Costs:
    total = np.zeros((stop - start, plane.shape[1]), dtype=np.float32)

    for residual, down, across in _residuals(plane, start, stop):
        suitability = _footprint(np.abs(residual), down, across)
        np.maximum(suitability, _FLOOR, out=suitability)
        total += np.reciprocal(suitability)

    return total


def _suniward(plane: Plane, start: int, stop: int) -> Costs:
    total = np.zeros((stop - start, plane.shape[1]), dtype=np.float32)

    for residual, down, across in _residuals(plane, start, stop):
        total += _footprint(np.reciprocal(np.abs(residual) + np.float32(SIGMA)), down, across)

    return total


def _residuals(
    plane: Plane, start: int, stop: int
) -> Iterator[tuple[Costs, npt.NDArray[np.float64], npt.NDArray[np.float64]]]:
    """Each filter's residual over rows ``start … stop + 14`` and columns ``0 … width + 14``,
    what a change in rows ``start … stop − 1`` reaches, with the filter's taps."""
    height, width = plane.shape
    rows = mirrored(np.arange(start - TAPS + 1, stop + TAPS - 1), height)
    columns = mirrored(np.arange(-TAPS + 1, width + TAPS - 1), width)
    extended = plane[rows][:, columns].astype(np.float32)
    # A convolution is the correlation with the taps reversed.
    along = {name: filtered(extended, taps[::-1], axis=1) for name, taps in TAPS_OF.items()}
    del extended

    for down, across in FILTERS:
        residual = filtered(along[across], TAPS_OF[down][::-1], axis=0)

        yield residual, TAPS_OF[down], TAPS_OF[across]


def _footprint(
    values: Costs, down: npt.NDArray[np.float64], across: npt.NDArray[np.float64]
) -> Costs:
    """Σ |K| · values over the residuals a change at each pixel reaches: K's own rows and
    columns from the pixel on, since R = X * K moves R[p + a] by K[a]."""
    return filtered(filtered(values, np.abs(across), axis=1), np.abs(down), axis=0)
