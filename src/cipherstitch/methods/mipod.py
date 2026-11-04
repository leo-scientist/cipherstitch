"""MiPOD costs for image LSB (Sedighi, Cogranne and Fridrich, "Content-Adaptive Steganography
by Minimizing Statistical Detectability", IEEE TIFS 11(2), 2016).

MiPOD models each value as a Gaussian with a variance of its own and spreads the payload so
that the best detector of the changes gains the least. Per colour plane:

- the residual of an adaptive 2 × 2 Wiener filter, as MATLAB's ``wiener2`` computes it: the
  window is the pixel with its neighbours to the right and below, zeros lie beyond the
  plane, and the noise is the mean local variance;
- the variance σ² of each residual: what the 45 two-dimensional DCT basis functions of
  degree below 9 leave of the 9 × 9 block around it, over the 36 others, with symmetric
  edges, and at least :data:`VARIANCE_FLOOR`;
- the Fisher information 1 / σ⁴ (the paper's 2 / σ⁴ differs by a factor λ absorbs),
  averaged over 7 × 7 values with symmetric edges.

Over the whole picture, each value's change rate β, a step up and a step down alike, solves
λ I β = ln(1 / β − 2), where λ makes the ternary entropy of every β add up to the payload.
A value costs ln(1 / β − 2): under that cost, a code at the payload bound changes it with
probability β. Unlike the other costs, these depend on the payload, and a smaller one costs
more everywhere.

Computed in stretches of rows with halos, so no float copy of the whole picture exists
besides the costs.
"""

from functools import cache

import numpy as np
import numpy.typing as npt

from cipherstitch.methods import hill
from cipherstitch.methods.feistel import golden_multiplier
from cipherstitch.methods.wavelet import mirrored

Costs = npt.NDArray[np.float32]
Floats = npt.NDArray[np.float64]
Plane = npt.NDArray[np.uint8]

BLOCK = 9  # p: the side of the block each variance is estimated from
DEGREE = 9  # the fitted DCT basis functions are those with u + v < DEGREE
VARIANCE_FLOOR = 0.01  # keeps flat colour from dividing by zero
SMOOTHING = 7  # the side of the Fisher information's mean
_HALF = BLOCK // 2
_REACH = SMOOTHING // 2
_LEFT = BLOCK * BLOCK - DEGREE * (DEGREE + 1) // 2  # basis functions the fit leaves: 36
_STRETCH = 1 << 18  # values per stretch
_LAMBDA_SAMPLE = 1 << 16  # values λ is solved on: it only shapes the costs
_LAMBDA_STEPS = 32  # bisections of log2 λ over [−64, 64]
_NEWTON_STEPS = 5  # float64 precision from costs_at's start, at every λ I up to e^56


def image_costs(colors: npt.NDArray[np.uint8], rate: float) -> Costs:
    """The cost of changing each value of ``colors`` (rows, columns, colours) by one, for a
    payload of ``rate`` bits per value."""
    costs = fisher_information(colors)
    lam = ternary_lambda(costs, rate)
    flat = costs.reshape(-1)

    for start in range(0, flat.size, _STRETCH):
        part = slice(start, start + _STRETCH)
        flat[part] = costs_at(lam * flat[part].astype(np.float64))

    return costs


def fisher_information(colors: npt.NDArray[np.uint8]) -> Costs:
    """The smoothed Fisher information 1 / σ⁴ of each value of ``colors`` (rows, columns,
    colours), plane by plane."""
    rows, columns, planes = colors.shape
    result = np.empty(colors.shape, dtype=np.float32)
    step = max(_STRETCH // max(columns, 1), 1)

    for color in range(planes):
        plane = colors[..., color]
        noise = _noise(plane)

        for start in range(0, rows, step):
            stop = min(start + step, rows)
            result[start:stop, :, color] = _smoothed(plane, noise, start, stop)

    return result


def ternary_lambda(information: Costs, rate: float) -> float:
    """The λ under which the change rates of values with Fisher ``information`` carry ``rate``
    bits per value. Solved by bisection on a sample of the values."""
    sample = _sample(information.reshape(-1)).astype(np.float64)
    target = rate * np.log(2) * sample.size
    low, high = -64.0, 64.0

    for _ in range(_LAMBDA_STEPS):
        middle = (low + high) / 2

        if _entropy(costs_at(2.0**middle * sample)) > target:
            low = middle
        else:
            high = middle

    return float(2.0 ** ((low + high) / 2))


def costs_at(products: Floats) -> Floats:
    """The cost c = ln(1 / β − 2) of each value whose λ I is in ``products``. With
    β = 1 / (e^c + 2), λ I β = ln(1 / β − 2) becomes c (e^c + 2) = λ I, whose left side is
    convex and increasing, so Newton's method converges from either side of the root. It
    starts from ln(1 + y) − ln(1 + ln(1 + y)), y = λ I, which is close to the root for
    every y."""
    costs = np.log1p(products)
    costs -= np.log1p(costs)

    for _ in range(_NEWTON_STEPS):
        grown = np.exp(costs)
        costs -= (costs * (grown + 2) - products) / (grown * (costs + 1) + 2)

    return costs


def _entropy(costs: Floats) -> float:
    """The ternary entropy in nats of the change rates β = 1 / (e^c + 2) of ``costs``."""
    rates = 1 / (np.exp(costs) + 2)
    kept = 1 - 2 * rates

    return float(-np.sum(2 * rates * np.log(rates) + kept * np.log(kept)))


def _sample(values: Costs) -> Costs:
    """About :data:`_LAMBDA_SAMPLE` of ``values``, all of them if there are no more, spread
    over every row and colour by the golden-ratio multiplier."""
    if values.size <= _LAMBDA_SAMPLE:
        return values

    positions = np.arange(_LAMBDA_SAMPLE, dtype=np.int64) * golden_multiplier(values.size)

    return values[positions % values.size]


def _smoothed(plane: Plane, noise: float, start: int, stop: int) -> Costs:
    """Rows ``start … stop − 1`` of the plane's smoothed Fisher information."""
    wanted = mirrored(np.arange(start - _REACH, stop + _REACH), plane.shape[0])
    low, high = int(wanted.min()), int(wanted.max()) + 1
    information = _information(plane, noise, low, high)[wanted - low]

    return hill.window_mean(information, SMOOTHING, axes=2)


def _information(plane: Plane, noise: float, low: int, high: int) -> Costs:
    """Rows ``low … high − 1`` of the plane's Fisher information 1 / σ⁴, not smoothed."""
    rows, columns = plane.shape
    wanted = mirrored(np.arange(low - _HALF, high + _HALF), rows)
    first, last = int(wanted.min()), int(wanted.max()) + 1
    residuals = _residuals(plane, noise, first, last)[wanted - first]
    residuals = residuals[:, mirrored(np.arange(-_HALF, columns + _HALF), columns)]
    variances = _variances(residuals)
    np.maximum(variances, np.float32(VARIANCE_FLOOR), out=variances)
    variances *= variances

    return np.reciprocal(variances, out=variances)


def _variances(residuals: Costs) -> Costs:
    """The variance of each residual whose :data:`BLOCK` × :data:`BLOCK` block lies within
    ``residuals``, so ``BLOCK − 1`` fewer along each axis: the energy of the block's DCT
    coefficients with u + v ≥ :data:`DEGREE`, over their number. The basis is orthonormal,
    so that is the paper's ‖P⊥ r‖² / (p² − q), and separable: first down the columns,
    then along the rows."""
    rows, columns = residuals.shape[0] - BLOCK + 1, residuals.shape[1] - BLOCK + 1
    basis = _basis()
    stacked = np.stack([residuals[i : i + rows] for i in range(BLOCK)])
    down = np.tensordot(basis, stacked, axes=(1, 0))
    del stacked
    total = np.zeros((rows, columns), dtype=np.float32)

    for u in range(DEGREE - BLOCK + 1, BLOCK):
        stacked = np.stack([down[u, :, j : j + columns] for j in range(BLOCK)])
        coefficients = np.tensordot(basis[DEGREE - u :], stacked, axes=(1, 0))
        coefficients *= coefficients
        total += coefficients.sum(axis=0)

    total /= np.float32(_LEFT)

    return total


@cache
def _basis() -> npt.NDArray[np.float32]:
    """The orthonormal DCT-II basis vectors of :data:`BLOCK` values, one per row: MATLAB's
    ``idct2`` of a unit impulse at (u, v) is the outer product of rows u and v."""
    k = np.arange(BLOCK)
    basis: npt.NDArray[np.float64] = np.sqrt(2 / BLOCK) * np.cos(
        (2 * k[None, :] + 1) * k[:, None] * np.pi / (2 * BLOCK)
    )
    basis[0] /= np.sqrt(2)

    return basis.astype(np.float32)


def _residuals(plane: Plane, noise: float, low: int, high: int) -> Costs:
    """Rows ``low … high − 1`` of x − F(x), F the Wiener filter: the deviation from the
    local mean μ, shrunk by noise / v where the local variance v is above the noise, which
    is :func:`_noise`'s."""
    sums, squares = _local_sums(plane, low, high)
    centred = 4 * plane[low:high].astype(np.int64) - sums  # 4 (x − μ)
    variance = 4 * squares - sums * sums  # 16 v, exact in integers
    shrink = np.divide(noise, variance, out=np.ones(variance.shape), where=variance > noise)

    return (centred * shrink / 4).astype(np.float32)


def _noise(plane: Plane) -> float:
    """16 times the mean local variance of ``plane``, as ``wiener2`` estimates the noise."""
    rows, columns = plane.shape
    step = max(_STRETCH // max(columns, 1), 1)
    total = 0

    for start in range(0, rows, step):
        sums, squares = _local_sums(plane, start, min(start + step, rows))
        total += int(np.sum(4 * squares - sums * sums))

    return total / plane.size


def _local_sums(
    plane: Plane, low: int, high: int
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    """For rows ``low … high − 1``, the sum of each value and its neighbours to the right,
    below and below right, zero beyond the plane, and the sum of their squares."""
    rows, columns = plane.shape
    padded = np.zeros((high - low + 1, columns + 1), dtype=np.int64)
    stop = min(high + 1, rows)
    padded[: stop - low, :columns] = plane[low:stop]
    sums = padded[:-1, :-1] + padded[:-1, 1:] + padded[1:, :-1] + padded[1:, 1:]
    padded *= padded
    squares = padded[:-1, :-1] + padded[:-1, 1:] + padded[1:, :-1] + padded[1:, 1:]

    return sums, squares
