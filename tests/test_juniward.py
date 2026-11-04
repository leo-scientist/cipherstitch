import numpy as np
import numpy.typing as npt
import pytest

from cipherstitch.media.jpeg import Coefficients, Component
from cipherstitch.methods import juniward, wavelet

BLOCK = 8
PAD = 16


def _dct() -> npt.NDArray[np.float64]:
    k = np.arange(BLOCK)
    matrix: npt.NDArray[np.float64] = np.sqrt(2 / BLOCK) * np.cos(
        (2 * k[None, :] + 1) * k[:, None] * np.pi / (2 * BLOCK)
    )
    matrix[0] /= np.sqrt(2)

    return matrix


def _coefficients(
    grids: list[npt.NDArray[np.int16]], steps: list[npt.NDArray[np.uint16]]
) -> Coefficients:
    """Components from ``rows × columns × 64`` grids of coefficients, the DC first."""
    blocks = [grid.reshape(-1, 64) for grid in grids]
    components = tuple(
        Component(grid.shape[0], grid.shape[1], step)
        for grid, step in zip(grids, steps, strict=True)
    )
    ac = np.concatenate([b[:, 1:].reshape(-1) for b in blocks]).astype(np.int16)
    dc = np.concatenate([b[:, 0] for b in blocks]).astype(np.int16)

    return Coefficients(ac, components, dc)


def _random(rows: int, columns: int, seed: int) -> npt.NDArray[np.int16]:
    rng = np.random.default_rng(seed)
    values = rng.integers(-12, 13, (rows, columns, 64)) * (rng.random((rows, columns, 64)) < 0.4)
    values[..., 0] = rng.integers(-60, 60, (rows, columns))

    return values.astype(np.int16)


def _wavelets(padded: npt.NDArray[np.float64]) -> list[npt.NDArray[np.float64]]:
    """The three high-pass bands of a padded plane, where the filters cover it whole."""
    taps = wavelet.TAPS_OF
    bands = []

    for down, across in wavelet.FILTERS:
        rows = sum(t * padded[u : u + padded.shape[0] - 15] for u, t in enumerate(taps[down]))
        bands.append(
            sum(t * rows[:, u : u + rows.shape[1] - 15] for u, t in enumerate(taps[across]))
        )

    return bands


def _distortion(
    grid: npt.NDArray[np.int16], steps: npt.NDArray[np.uint16]
) -> npt.NDArray[np.float64]:
    """Each mode's cost by definition: the relative change of every wavelet coefficient when
    that coefficient alone changes by one, the cover's mirrored margins left as they are."""
    rows, columns = grid.shape[:2]
    dct = _dct()
    dequantized = grid.reshape(rows, columns, BLOCK, BLOCK) * steps.reshape(BLOCK, BLOCK)
    pixels = (dct.T @ dequantized @ dct).transpose(0, 2, 1, 3).reshape(rows * 8, columns * 8)
    cover = _wavelets(np.pad(pixels, PAD, mode="symmetric"))
    result = np.empty((rows, columns, 64))

    for row in range(rows):
        for column in range(columns):
            for mode in range(64):
                change = np.zeros((rows * 8 + 2 * PAD, columns * 8 + 2 * PAD))
                basis = np.zeros(64)
                basis[mode] = steps[mode]
                top, left = PAD + row * 8, PAD + column * 8
                change[top : top + 8, left : left + 8] = dct.T @ basis.reshape(8, 8) @ dct
                result[row, column, mode] = sum(
                    float(np.sum(np.abs(d) / (np.abs(c) + juniward.SIGMA)))
                    for d, c in zip(_wavelets(change), cover, strict=True)
                )

    return result


@pytest.mark.parametrize("strip_blocks", [1 << 15, 2], ids=["whole", "strips"])
def test_a_change_costs_the_wavelet_distortion_it_makes(
    strip_blocks: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Border blocks included, and each component with its own steps and grid. The costs
    are float32, the reference float64."""
    monkeypatch.setattr(juniward, "_STRIP_BLOCKS", strip_blocks)
    rng = np.random.default_rng(seed=1)
    grids = [_random(3, 4, seed=2), _random(2, 2, seed=3)]
    steps = [rng.integers(1, 30, 64).astype(np.uint16) for _ in grids]
    costs = juniward.costs(_coefficients(grids, steps))
    expected = np.concatenate(
        [
            _distortion(grid, step)[..., 1:].reshape(-1)
            for grid, step in zip(grids, steps, strict=True)
        ]
    )

    assert np.allclose(costs, expected, rtol=2e-3)


def test_a_change_costs_more_in_a_smooth_area() -> None:
    """The left two blocks are flat, the right two textured."""
    grid = _random(1, 4, seed=4)
    grid[0, :2, 1:] = 0
    grid[0, :2, 0] = grid[0, 0, 0]
    costs = juniward.costs(_coefficients([grid], [np.ones(64, np.uint16)])).reshape(4, 63)

    assert costs[0].min() > costs[3].max()


def test_a_coarser_step_costs_more() -> None:
    """The same picture from steps twice as coarse: every change moves it twice as far."""
    grid = _random(2, 3, seed=5) * 2
    fine = juniward.costs(_coefficients([grid], [np.full(64, 3, np.uint16)]))
    coarse = juniward.costs(_coefficients([grid // 2], [np.full(64, 6, np.uint16)]))

    assert np.allclose(coarse, 2 * fine, rtol=1e-5)


def test_rounding_errors_near_halfway_make_steps_towards_the_precover_cheap() -> None:
    coefficients = _coefficients([_random(2, 3, seed=6)], [np.full(64, 4, np.uint16)])
    rounding = np.zeros(coefficients.ac.size, dtype=np.int8)
    rounding[0::3] = 127  # an error of almost 0.5, above the coefficient
    rounding[1::3] = -64  # about 0.25 below
    plain = juniward.costs(coefficients)
    up, down = juniward.side_informed(plain, rounding)

    assert np.array_equal(up[0::3], np.zeros_like(up[0::3]))
    assert np.allclose(down[0::3], 2 * plain[0::3])
    assert np.allclose(up[1::3], plain[1::3] * (1 + 64 / 127))
    assert np.allclose(down[1::3], plain[1::3] * (1 - 64 / 127))
    assert np.array_equal(up[2::3], plain[2::3])  # no side information: as without
    assert np.array_equal(down[2::3], plain[2::3])


def _wiener_directions(
    grid: npt.NDArray[np.int16], steps: npt.NDArray[np.uint16]
) -> npt.NDArray[np.int8]:
    """By definition: decompress, smooth as scipy.signal.wiener over 3 × 3 with mirrored
    edges and the mean local variance as the noise, compress without rounding."""
    rows, columns = grid.shape[:2]
    dct = _dct()
    dequantized = grid.reshape(rows, columns, BLOCK, BLOCK) * steps.reshape(BLOCK, BLOCK)
    pixels = (dct.T @ dequantized @ dct).transpose(0, 2, 1, 3).reshape(rows * 8, columns * 8)
    padded = np.pad(pixels, 1, mode="symmetric")
    windows = np.stack(
        [padded[a : a + rows * 8, b : b + columns * 8] for a in range(3) for b in range(3)]
    )
    mean = windows.mean(axis=0)
    variance = (windows * windows).mean(axis=0) - mean * mean
    noise = variance.mean()
    gain = np.where(variance < noise, 0, 1 - noise / np.maximum(variance, 1e-300))
    smooth = (mean + gain * (pixels - mean)).reshape(rows, 8, columns, 8).transpose(0, 2, 1, 3)
    estimate = (dct @ smooth @ dct.T).reshape(rows, columns, 64) / steps

    return np.sign(estimate - grid).astype(np.int8)


@pytest.mark.parametrize("strip_blocks", [1 << 15, 2], ids=["whole", "strips"])
def test_directions_point_at_a_wiener_filtered_picture(
    strip_blocks: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(juniward, "_STRIP_BLOCKS", strip_blocks)
    rng = np.random.default_rng(seed=7)
    grids = [_random(3, 4, seed=8), _random(2, 2, seed=9)]
    steps = [rng.integers(1, 30, 64).astype(np.uint16) for _ in grids]
    found = juniward.directions(_coefficients(grids, steps))
    expected = np.concatenate(
        [_wiener_directions(g, s)[..., 1:].reshape(-1) for g, s in zip(grids, steps, strict=True)]
    )

    assert np.mean(found == expected) > 0.999  # float rounding may flip an estimate at 0
    assert np.count_nonzero(found) > 0.9 * found.size


@pytest.mark.filterwarnings("error")
def test_a_flat_component_has_no_direction() -> None:
    """Chroma of a grey picture: no variance and no noise, so nothing to filter (0 / 0)."""
    flat = np.zeros((2, 2, 64), dtype=np.int16)
    steps = np.full(64, 4, dtype=np.uint16)

    assert not np.any(juniward.directions(_coefficients([flat], [steps])))


def test_a_step_towards_the_estimate_costs_less() -> None:
    costs = np.array([2, 2, 2], dtype=np.float32)
    up, down = juniward.polarized(costs, np.array([1, -1, 0], dtype=np.int8))

    assert np.allclose(up, [2 * juniward.POLARIZATION, 2, 2])
    assert np.allclose(down, [2, 2 * juniward.POLARIZATION, 2])
