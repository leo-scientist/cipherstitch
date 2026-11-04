"""Costs of changing DCT coefficients: UERD (Guo, Ni, Su, Tang and Shi, 2015).

A change costs its mode's quantization step over its block's suitability: the block's energy,
Σ |coefficient| · step over its 63 AC modes, plus a quarter of the energies of the eight
blocks around it, with the border blocks repeated past the edges. Changes therefore go to
busy blocks in busy neighbourhoods, and within a block to the finely quantized modes. Each
component has its own grid and steps. The DC term is left out of the energy, since a block's
brightness says nothing of its texture.
"""

import numpy as np
import numpy.typing as npt

from cipherstitch.media.jpeg import Coefficients, Component

NEIGHBOUR_WEIGHT = 0.25  # each of the eight surrounding blocks' share of the suitability
WET = 1e10  # where a block and its neighbours hold nothing at all: finite for the search

_AC_MODES = 63
_CHUNK_BLOCKS = 1 << 15  # blocks weighed at a time; bounds the float copy


def costs(coefficients: Coefficients) -> npt.NDArray[np.float32]:
    """The cost of changing each of ``coefficients.ac`` by one."""
    result = np.empty(coefficients.ac.size, dtype=np.float32)
    first_block = 0

    for component in coefficients.components:
        blocks = component.rows * component.columns
        start = first_block * _AC_MODES
        out = result[start : start + blocks * _AC_MODES].reshape(blocks, _AC_MODES)
        suitability = _suitability(_energies(coefficients.ac[start:], component)).reshape(-1)

        with np.errstate(divide="ignore"):
            np.multiply.outer(np.reciprocal(suitability), component.steps[1:], out=out)

        out[suitability == 0] = WET
        first_block += blocks

    return result


def _energies(ac: npt.NDArray[np.int16], component: Component) -> npt.NDArray[np.float64]:
    """Each block's Σ |coefficient| · step over its AC modes, as a ``rows × columns`` grid,
    from the component's AC coefficients at the start of ``ac``."""
    steps = component.steps[1:].astype(np.float64)
    blocks = component.rows * component.columns
    grouped = ac[: blocks * _AC_MODES].reshape(blocks, _AC_MODES)
    result = np.empty(blocks)

    for start in range(0, blocks, _CHUNK_BLOCKS):
        stop = min(start + _CHUNK_BLOCKS, blocks)
        result[start:stop] = np.abs(grouped[start:stop].astype(np.float64)) @ steps

    return result.reshape(component.rows, component.columns)


def _suitability(energies: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Each block's energy plus a quarter of its eight neighbours', border blocks repeated."""
    rows, columns = energies.shape
    padded = np.pad(energies, 1, mode="edge")
    around = -energies

    for a in range(3):
        for b in range(3):
            around = around + padded[a : a + rows, b : b + columns]

    return energies + NEIGHBOUR_WEIGHT * around
