"""UERD costs against their definition, written out block by block."""

import numpy as np
import numpy.typing as npt
import pytest

from cipherstitch.media.jpeg import Coefficients, Component
from cipherstitch.methods import uerd


def _coefficients(
    grids: list[npt.NDArray[np.int16]], steps: list[npt.NDArray[np.uint16]]
) -> Coefficients:
    """Components from ``rows × columns × 64`` grids of coefficients, the DC first."""
    components = tuple(
        Component(grid.shape[0], grid.shape[1], step)
        for grid, step in zip(grids, steps, strict=True)
    )
    ac = np.concatenate([grid.reshape(-1, 64)[:, 1:].reshape(-1) for grid in grids])
    dc = np.concatenate([grid.reshape(-1, 64)[:, 0] for grid in grids])

    return Coefficients(ac.astype(np.int16), components, dc.astype(np.int16))


def _random(rows: int, columns: int, seed: int) -> npt.NDArray[np.int16]:
    rng = np.random.default_rng(seed)
    values = rng.integers(-12, 13, (rows, columns, 64)) * (rng.random((rows, columns, 64)) < 0.4)
    values[..., 0] = rng.integers(-60, 60, (rows, columns))

    return values.astype(np.int16)


def _steps(seed: int) -> npt.NDArray[np.uint16]:
    return np.random.default_rng(seed).integers(1, 100, 64).astype(np.uint16)


def _reference(grid: npt.NDArray[np.int16], steps: npt.NDArray[np.uint16]) -> list[float]:
    """Step over the block's AC energy plus a quarter of each neighbour's, the nearest
    block standing in past the edges."""
    rows, columns = grid.shape[:2]
    energy = [
        [sum(abs(int(grid[m, n, k])) * int(steps[k]) for k in range(1, 64)) for n in range(columns)]
        for m in range(rows)
    ]
    result = []

    for m in range(rows):
        for n in range(columns):
            around = sum(
                energy[min(max(m + a, 0), rows - 1)][min(max(n + b, 0), columns - 1)]
                for a in (-1, 0, 1)
                for b in (-1, 0, 1)
                if (a, b) != (0, 0)
            )
            suitability = energy[m][n] + around / 4

            for k in range(1, 64):
                result.append(int(steps[k]) / suitability if suitability else uerd.WET)

    return result


def test_the_costs_follow_the_definition_in_every_component() -> None:
    grids = [_random(5, 7, seed=1), _random(3, 4, seed=2), _random(1, 2, seed=3)]
    steps = [_steps(seed=4), _steps(seed=5), _steps(seed=6)]
    expected = [cost for g, s in zip(grids, steps, strict=True) for cost in _reference(g, s)]

    np.testing.assert_allclose(uerd.costs(_coefficients(grids, steps)), expected, rtol=1e-6)


def test_the_dc_terms_do_not_count() -> None:
    grid, steps = _random(4, 4, seed=7), _steps(seed=8)
    brighter = grid.copy()
    brighter[..., 0] += 40

    np.testing.assert_array_equal(
        uerd.costs(_coefficients([grid], [steps])), uerd.costs(_coefficients([brighter], [steps]))
    )


def test_blocks_with_nothing_in_or_around_them_are_wet() -> None:
    grid = np.zeros((6, 6, 64), dtype=np.int16)
    grid[0, 0, 5] = 3
    grid[..., 0] = 20
    costs = uerd.costs(_coefficients([grid], [_steps(seed=9)])).reshape(6, 6, 63)
    near = np.zeros((6, 6), dtype=bool)
    near[:2, :2] = True

    assert np.all(costs[near] < uerd.WET)
    assert np.all(costs[~near] == np.float32(uerd.WET))


def test_chunks_give_the_same_costs(monkeypatch: pytest.MonkeyPatch) -> None:
    coefficients = _coefficients([_random(9, 5, seed=10)], [_steps(seed=11)])
    whole = uerd.costs(coefficients)
    monkeypatch.setattr(uerd, "_CHUNK_BLOCKS", 4)

    np.testing.assert_array_equal(uerd.costs(coefficients), whole)
