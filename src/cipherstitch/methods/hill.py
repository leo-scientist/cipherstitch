"""HILL costs (Li, Wang, Huang, Li 2014) for LSB: a change costs more where the cover is
smooth. Along both axes of each colour of a picture, or along each channel of a recording:
the absolute high-pass residual, its mean over 3, the reciprocal and its mean over 15, with
symmetric edges. On a picture that is conseal's HILL (KB / 4, a 3 × 3 mean floored at
float32's epsilon, a 15 × 15 mean).

Computed in stretches along the first axis, so no float copy of the whole carrier exists
besides the costs.
"""

import numpy as np
import numpy.typing as npt

Costs = npt.NDArray[np.float32]

NEAR = 3  # the residual's mean
FAR = 15  # the reciprocal's mean: Standard's, a multiple of NEAR
_FLOOR = np.finfo(np.float32).eps
_HALF = np.float32(0.5)
_STRETCH = 1 << 20  # values per stretch


def image_costs(colors: npt.NDArray[np.uint8], far: int = FAR) -> Costs:
    """The cost of changing each value of ``colors`` (rows, columns, colours) by one, with a
    reciprocal's mean over ``far`` × ``far`` values."""
    return _costs(colors, axes=2, far=far)


def audio_costs(samples: npt.NDArray[np.int32]) -> Costs:
    """The cost of changing each of ``samples`` (frames, channels) by one."""
    return _costs(samples, axes=1, far=FAR)


def _costs(values: npt.NDArray[np.integer], axes: int, far: int) -> Costs:
    """Filtered along the first ``axes`` axes; the others are independent."""
    result = np.empty(values.shape, dtype=np.float32)
    step = max(_STRETCH // max(values[0].size, 1), 1)

    for start in range(0, len(values), step):
        stop = min(start + step, len(values))
        result[start:stop] = _stretch(values, start, stop, axes, far)

    return result


def _stretch(values: npt.NDArray[np.integer], start: int, stop: int, axes: int, far: int) -> Costs:
    """Rows ``start … stop − 1`` of the costs, from the rows of each stage they depend on."""
    count = len(values)
    reach = far // 2
    low, high = _bounds(start - reach, stop + reach, count)  # of the reciprocal
    near_low, near_high = _bounds(low - 1, high + 1, count)  # of the residual
    rows = _reflected(np.arange(near_low - 1, near_high + 1), count)
    residual = np.abs(_high_pass(values[rows].astype(np.float32), axes))
    rows = _reflected(np.arange(low - 1, high + 1), count) - near_low
    mean = window_mean(residual[rows], NEAR, axes)
    del residual
    np.maximum(mean, _FLOOR, out=mean)
    np.reciprocal(mean, out=mean)
    rows = _reflected(np.arange(start - reach, stop + reach), count) - low

    return window_mean(mean[rows], far, axes)


def _bounds(start: int, stop: int, count: int) -> tuple[int, int]:
    """The rows that rows ``start … stop − 1`` reflect to, as a range."""
    rows = _reflected(np.arange(start, stop), count)

    return int(rows.min()), int(rows.max()) + 1


def _reflected(indices: npt.NDArray[np.int64], count: int) -> npt.NDArray[np.int64]:
    """Indices past either end mirrored back with the edge repeated, as often as it takes."""
    period = np.mod(indices, 2 * count)

    return np.where(period < count, period, 2 * count - 1 - period)


def _high_pass(values: Costs, axes: int) -> Costs:
    """``[-1, 2, -1] / 2`` along each axis: one row shorter at either end, the other axes
    extended."""
    result = values[1:-1] - (values[:-2] + values[2:]) * _HALF

    for axis in range(1, axes):
        extended = _extended(result, 1, axis)
        middle = extended.take(np.arange(1, extended.shape[axis] - 1), axis=axis)
        sides = extended.take(np.arange(extended.shape[axis] - 2), axis=axis)
        sides += extended.take(np.arange(2, extended.shape[axis]), axis=axis)
        result = middle - sides * _HALF

    return result


def window_mean(values: Costs, size: int, axes: int) -> Costs:
    """The mean of each ``size`` values along each axis: ``size − 1`` rows shorter, the other
    axes extended (MiPOD smooths with it too)."""
    result = _window_sums(values, size, 0)

    for axis in range(1, axes):
        result = _window_sums(_extended(result, size // 2, axis), size, axis)

    result /= size**axes

    return result


def _extended(values: Costs, reach: int, axis: int) -> Costs:
    count = values.shape[axis]

    return values.take(_reflected(np.arange(-reach, count + reach), count), axis=axis)


def _window_sums(values: Costs, size: int, axis: int) -> Costs:
    """Sums of each ``size`` consecutive values along ``axis``, added up window by window:
    running sums would carry the rounding of a silent stretch's huge costs into its
    neighbours'. A far window's sums are sums of 3, three apart: 15 is five of them."""
    lines = np.moveaxis(values, axis, 0)

    if size > NEAR and size % NEAR == 0:
        lines, size, step = _sums(lines, NEAR, 1), size // NEAR, NEAR
    else:
        step = 1

    return np.moveaxis(_sums(lines, size, step), 0, axis)


def _sums(lines: Costs, size: int, step: int) -> Costs:
    """Sums of ``size`` lines ``step`` apart."""
    count = len(lines) - (size - 1) * step
    sums = lines[:count].copy()

    for offset in range(step, size * step, step):
        sums += lines[offset : offset + count]

    return sums
