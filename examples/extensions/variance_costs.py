"""Local variance, an example cost function (docs/extensions.md): image LSB weighs a step of
each colour value by the reciprocal of the variance of the 3 × 3 window around it, in its own
colour plane, the edges mirrored. Busy areas get cheap changes and smooth ones dear changes.

A toy that shows the shape of a cost function. Nobody has measured how detectable its
changes are, and HILL, the default, does the same job in a way that has been studied.
"""

import numpy as np
import numpy.typing as npt

from cipherstitch.extensions import CostSpec, Domain, Extension

WINDOW = 3  # values across and down
FLOOR = 0.01  # added to the variance, so that flat colour costs 100 rather than infinity


def variance_costs(colors: npt.NDArray[np.uint8], rate: float) -> npt.NDArray[np.float32]:
    """The cost of a step of each value of ``colors`` (rows × columns × 3). The payload's
    ``rate`` doesn't change it."""
    values = colors.astype(np.int32)
    count = WINDOW * WINDOW
    sums = _window_sums(values)
    # count² times the variance, exact in integers, so it is never below zero
    spread = count * _window_sums(values * values) - sums * sums
    variance = spread.astype(np.float32) / (count * count)

    return np.reciprocal(variance + np.float32(FLOOR))


def _window_sums(values: npt.NDArray[np.int32]) -> npt.NDArray[np.int32]:
    """Each value's sum over the window around it in its colour plane, the edges mirrored."""
    reach = WINDOW // 2
    padded = np.pad(values, ((reach, reach), (reach, reach), (0, 0)), mode="symmetric")
    rows, columns = values.shape[:2]
    sums = np.zeros_like(values)

    for down in range(WINDOW):
        for across in range(WINDOW):
            sums += padded[down : down + rows, across : across + columns]

    return sums


EXTENSION = Extension(
    costs=(CostSpec("local-variance", "Local variance", Domain.SPATIAL, variance_costs),),
    summary="An example cost function: image LSB by the local variance.",
)
