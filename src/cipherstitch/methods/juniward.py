"""Costs of changing DCT coefficients: J-UNIWARD (Holub, Fridrich and Denemark, 2014).

A change costs the relative change it makes to the picture's wavelet coefficients: each
component is decompressed, filtered with the three Daubechies 8 high-pass filter pairs,
and a change costs the sum of every wavelet coefficient's change over that coefficient's
magnitude plus :data:`SIGMA`. Changes therefore go to textured areas, where the picture's
own detail hides them, rather than to smooth areas or clean edges. With a precover's
rounding errors, a change towards the precover costs less, and one away from it more, the
closer the error lies to halfway (SI-UNIWARD, docs/methods.md#from-a-raw-png-or-tiff).
Without them, a change towards an estimate of the picture costs less (cost polarization,
docs/methods.md#dct-coefficients-jpeg).

The windows are aligned so that a single change costs exactly the distortion it makes,
the correction of Beneš and Lorch (2023) to the published code's off-by-one alignment.
"""

from functools import cache

import numpy as np
import numpy.typing as npt

from cipherstitch.media import precover
from cipherstitch.media.jpeg import Coefficients, Component
from cipherstitch.methods.wavelet import FILTERS, TAPS, TAPS_OF, filtered, mirrored

SIGMA = 2.0**-6  # keeps flat areas from dividing by zero, and sets the content sensitivity
POLARIZATION = 0.65  # a step towards the estimated picture costs this share: HStego's

_BLOCK = 8
_MODES = _BLOCK * _BLOCK
_WINDOW = 3  # blocks across a change's wavelet footprint of 8 + 16 − 1 = 23 values
_PAD = 2 * _BLOCK  # mirrored margin, so border blocks have whole windows
_HALFWAY = precover.ROUNDING_SCALE // 2  # a stored rounding error of ±0.5
_STRIP_BLOCKS = 1 << 15  # blocks costed at a time; bounds the temporaries


def costs(coefficients: Coefficients, sigma: float = SIGMA) -> npt.NDArray[np.float32]:
    """The cost of changing each of ``coefficients.ac`` by one, at ``sigma``."""
    result = np.empty(coefficients.ac.size, dtype=np.float32)
    first_block = 0

    for component in coefficients.components:
        _write_costs(result, coefficients, component, first_block, sigma)
        first_block += component.rows * component.columns

    return result


def side_informed(
    costs: npt.NDArray[np.float32], rounding: npt.NDArray[np.int8]
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
    """The costs of a step up and a step down, given a precover's ``rounding``
    (:class:`cipherstitch.media.precover.Precover`): 1 − 2 |error| times the cost towards the
    precover, 1 + 2 |error| times away from it. A positive error lies above the coefficient."""
    tilt = rounding.astype(np.float32)
    tilt /= _HALFWAY

    return costs * (1 - tilt), costs * (1 + tilt)


def directions(coefficients: Coefficients) -> npt.NDArray[np.int8]:
    """Which way the picture probably lies from each of ``coefficients.ac``: the sign of its
    estimate less the coefficient, 0 where they are equal. The estimate is the coefficient of
    the component decompressed, smoothed by a 3 × 3 Wiener filter and compressed again
    without rounding."""
    result = np.empty(coefficients.ac.size, dtype=np.int8)
    first_block = 0

    for component in coefficients.components:
        _write_directions(result, coefficients, component, first_block)
        first_block += component.rows * component.columns

    return result


def polarized(
    costs: npt.NDArray[np.float32],
    directions: npt.NDArray[np.int8],
    share: float = POLARIZATION,
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
    """The costs of a step up and a step down: ``share`` times the cost the way
    ``directions`` points, the cost itself the other way."""
    toward = costs * np.float32(share)

    return np.where(directions > 0, toward, costs), np.where(directions < 0, toward, costs)


def _write_directions(
    result: npt.NDArray[np.int8], coefficients: Coefficients, component: Component, first_block: int
) -> None:
    """The directions of the component whose blocks start at ``first_block``, into ``result``,
    strip by strip. As in scipy.signal.wiener, the noise is the mean local variance, here over
    the whole component, with mirrored edges."""
    columns = component.columns
    strip = max(_STRIP_BLOCKS // columns, 1)
    strips = [(top, min(top + strip, component.rows)) for top in range(0, component.rows, strip)]
    variances = (_moments(coefficients, component, first_block, *rows)[2] for rows in strips)
    noise = sum(float(v.sum()) for v in variances) / (component.rows * columns * _MODES)
    dct = _dct()

    for top, bottom in strips:
        pixels, mean, variance = _moments(coefficients, component, first_block, top, bottom)

        with np.errstate(divide="ignore", invalid="ignore"):
            gain = np.where(variance <= noise, np.float32(0), 1 - np.float32(noise) / variance)

        smooth = mean + gain * (pixels - mean)
        blocks = smooth.reshape(bottom - top, _BLOCK, columns, _BLOCK).transpose(0, 2, 1, 3)
        estimate = (dct @ blocks @ dct.T).reshape(-1, _MODES) / component.steps
        ac = slice(
            (first_block + top * columns) * (_MODES - 1),
            (first_block + bottom * columns) * (_MODES - 1),
        )
        quantized = coefficients.ac[ac].reshape(-1, _MODES - 1)
        result[ac] = np.sign(estimate[:, 1:] - quantized).reshape(-1)


def _moments(
    coefficients: Coefficients, component: Component, first_block: int, top: int, bottom: int
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32], npt.NDArray[np.float32]]:
    """The pixels of block rows ``top … bottom − 1``, and their mean and variance over each
    3 × 3 neighbourhood, edges mirrored."""
    height, width = component.rows * _BLOCK, component.columns * _BLOCK
    first, last = max(top - 1, 0), min(bottom + 1, component.rows)
    pixels = _decompressed(_dequantized(coefficients, component, first_block, first, last))
    rows = mirrored(np.arange(top * _BLOCK - 1, bottom * _BLOCK + 1), height)
    columns = mirrored(np.arange(-1, width + 1), width)
    padded = pixels[rows - first * _BLOCK][:, columns]
    ninth = np.float32(1 / 9)
    mean = _box(padded) * ninth
    variance = _box(padded * padded) * ninth
    variance -= mean * mean

    return padded[1:-1, 1:-1], mean, variance


def _box(values: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    """The sums over every 3 × 3 window that ``values`` holds whole."""
    rows = values[:-2] + values[1:-1] + values[2:]
    result: npt.NDArray[np.float32] = rows[:, :-2] + rows[:, 1:-1] + rows[:, 2:]

    return result


def _write_costs(
    result: npt.NDArray[np.float32],
    coefficients: Coefficients,
    component: Component,
    first_block: int,
    sigma: float,
) -> None:
    """Every AC cost of the component whose blocks start at ``first_block``, into ``result``,
    strip by strip."""
    columns = component.columns
    steps = component.steps.astype(np.float32)
    kernels = [_impact()[f] * steps for f in range(len(FILTERS))]  # scaled per mode
    strip = max(_STRIP_BLOCKS // columns, 1)

    for top in range(0, component.rows, strip):
        bottom = min(top + strip, component.rows)
        planes = _reciprocals(coefficients, component, first_block, top, bottom, sigma)
        total = np.zeros(((bottom - top) * columns, _MODES), dtype=np.float32)

        for plane, kernel in zip(planes, kernels, strict=True):
            for a in range(_WINDOW):
                for b in range(_WINDOW):
                    window = plane[a : a + bottom - top, b : b + columns].reshape(-1, _MODES)
                    total += window @ kernel[a, b]

        start = (first_block + top * columns) * (_MODES - 1)
        result[start : start + total.shape[0] * (_MODES - 1)] = total[:, 1:].reshape(-1)


def _reciprocals(
    coefficients: Coefficients,
    component: Component,
    first_block: int,
    top: int,
    bottom: int,
    sigma: float,
) -> list[npt.NDArray[np.float32]]:
    """For block rows ``top … bottom − 1``: 1 / (|wavelet coefficient| + sigma) for each filter,
    as 8 × 8 tiles, two more each way than the strip has blocks, so a block's tiles ``a, b``
    (0 … 2) cover the wavelet coefficients its change reaches."""
    height, width = component.rows * _BLOCK, component.columns * _BLOCK
    first = max(top - _PAD // _BLOCK, 0)
    last = min(bottom + _PAD // _BLOCK, component.rows)
    pixels = _decompressed(_dequantized(coefficients, component, first_block, first, last))
    rows = mirrored(np.arange(top * _BLOCK - _PAD, bottom * _BLOCK + _PAD), height)
    columns = mirrored(np.arange(-_PAD, width + _PAD), width)
    padded = pixels[rows - first * _BLOCK][:, columns]
    along = {name: filtered(padded, taps, axis=1) for name, taps in TAPS_OF.items()}
    result = []

    for down, across in FILTERS:
        wavelet = filtered(along[across], TAPS_OF[down], axis=0)
        # A change in block row m reaches wavelet rows 8m + 1 … 8m + 23 of this valid output.
        tiles = np.reciprocal(np.abs(wavelet[1:, 1:]) + np.float32(sigma))
        tiled_rows, tiled_columns = tiles.shape[0] // _BLOCK, tiles.shape[1] // _BLOCK
        result.append(
            tiles.reshape(tiled_rows, _BLOCK, tiled_columns, _BLOCK).transpose(0, 2, 1, 3)
        )

    return result


def _dequantized(
    coefficients: Coefficients, component: Component, first_block: int, top: int, bottom: int
) -> npt.NDArray[np.float32]:
    """Block rows ``top … bottom − 1`` of the component: ``rows × columns × 64`` values,
    each coefficient times its step."""
    start = first_block + top * component.columns
    stop = first_block + bottom * component.columns
    blocks = np.empty((stop - start, _MODES), dtype=np.float32)
    blocks[:, 0] = coefficients.dc[start:stop]
    blocks[:, 1:] = coefficients.ac[start * (_MODES - 1) : stop * (_MODES - 1)].reshape(
        -1, _MODES - 1
    )
    blocks *= component.steps.astype(np.float32)

    return blocks.reshape(bottom - top, component.columns, _MODES)


def _decompressed(dequantized: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    """The pixels of whole block rows, before rounding and without the level shift (the
    filters are high pass, so neither matters)."""
    block_rows, columns = dequantized.shape[0], dequantized.shape[1]
    blocks = dequantized.reshape(block_rows, columns, _BLOCK, _BLOCK)
    pixels = (_dct().T @ blocks @ _dct()).astype(np.float32, copy=False)

    return pixels.transpose(0, 2, 1, 3).reshape(block_rows * _BLOCK, columns * _BLOCK)


@cache
def _dct() -> npt.NDArray[np.float32]:
    """The orthonormal 8-point DCT-II, which JPEG uses on rows and columns."""
    k = np.arange(_BLOCK)
    matrix: npt.NDArray[np.float64] = np.sqrt(2 / _BLOCK) * np.cos(
        (2 * k[None, :] + 1) * k[:, None] * np.pi / (2 * _BLOCK)
    )
    matrix[0] /= np.sqrt(2)

    return matrix.astype(np.float32)


@cache
def _impact() -> npt.NDArray[np.float32]:
    """|wavelet response| to a change of 1 in each mode at a step of 1, per filter, as
    kernels ``[filter, a, b]`` mapping a 8 × 8 tile of reciprocals to the 64 modes' costs."""
    basis = np.eye(_MODES).reshape(_MODES, _BLOCK, _BLOCK)
    spatial = _dct().astype(np.float64).T @ basis @ _dct().astype(np.float64)
    span = _WINDOW * _BLOCK
    result = np.zeros((len(FILTERS), _WINDOW, _WINDOW, _MODES, _MODES), dtype=np.float32)

    for number, (down, across) in enumerate(FILTERS):
        response = np.abs(_full(_full(spatial, TAPS_OF[down], axis=1), TAPS_OF[across], axis=2))
        padded = np.zeros((_MODES, span, span))
        padded[:, : response.shape[1], : response.shape[2]] = response
        tiles = padded.reshape(_MODES, _WINDOW, _BLOCK, _WINDOW, _BLOCK).transpose(1, 3, 2, 4, 0)
        result[number] = tiles.reshape(_WINDOW, _WINDOW, _MODES, _MODES)

    return result


def _full(
    values: npt.NDArray[np.float64], taps: npt.NDArray[np.float64], axis: int
) -> npt.NDArray[np.float64]:
    """The full correlation with ``taps`` along ``axis``: output ``n`` is Σ x[n − 15 + u] t[u]."""
    moved = np.moveaxis(values, axis, 0)
    result = np.zeros((moved.shape[0] + TAPS - 1, *moved.shape[1:]))

    for u in range(TAPS):
        result[TAPS - 1 - u : TAPS - 1 - u + moved.shape[0]] += taps[u] * moved

    return np.moveaxis(result, 0, axis)
