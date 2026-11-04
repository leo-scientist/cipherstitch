from collections.abc import Callable

import numpy as np
import numpy.typing as npt
import pytest

from cipherstitch.methods import spatial_costs, wavelet

Floats = npt.NDArray[np.float64]


def _footprints(plane: Floats) -> list[tuple[Floats, Floats]]:
    """Per filter: the residual R = X * K over every row and column a change reaches, from
    the definition with symmetric edges, and |K|."""
    taps = wavelet.TAPS
    padded = np.pad(plane, taps - 1, mode="symmetric")
    height, width = plane.shape
    result = []

    for down, across in wavelet.FILTERS:
        kernel = np.outer(wavelet.TAPS_OF[down], wavelet.TAPS_OF[across])
        residual = np.zeros((height + taps - 1, width + taps - 1))

        for a, b in np.ndindex(kernel.shape):  # R[m] = Σ K[a] X[m − a]
            rows = slice(taps - 1 - a, taps - 1 - a + residual.shape[0])
            columns = slice(taps - 1 - b, taps - 1 - b + residual.shape[1])
            residual += kernel[a, b] * padded[rows, columns]

        result.append((residual, np.abs(kernel)))

    return result


def _weighed(values: Floats, weights: Floats, shape: tuple[int, ...]) -> Floats:
    """Σ weights[a] · values[p + a]: what a change at each pixel reaches."""
    result = np.zeros(shape)

    for a, b in np.ndindex(weights.shape):
        result += weights[a, b] * values[a : a + shape[0], b : b + shape[1]]

    return result


def _wow(plane: Floats) -> Floats:
    terms = [
        1 / np.maximum(_weighed(np.abs(r), k, plane.shape), 3 / spatial_costs.WET)
        for r, k in _footprints(plane)
    ]

    return np.sum(terms, axis=0)


def _suniward(plane: Floats) -> Floats:
    terms = [
        _weighed(1 / (np.abs(r) + spatial_costs.SIGMA), k, plane.shape)
        for r, k in _footprints(plane)
    ]

    return np.sum(terms, axis=0)


def _picture(rows: int, columns: int) -> npt.NDArray[np.uint8]:
    """Noise on the right, a flat corner and a gradient on the left."""
    rng = np.random.default_rng(seed=5)
    pixels = rng.integers(0, 256, (rows, columns, 3), dtype=np.uint8)
    pixels[: rows // 2, : columns // 2] = 40
    pixels[rows // 2 :, : columns // 2] = np.arange(columns // 2, dtype=np.uint8)[None, :, None]

    return pixels


@pytest.mark.parametrize("shape", [(24, 31), (5, 40), (40, 7)])
@pytest.mark.parametrize(
    ("costs", "reference"),
    [(spatial_costs.wow_costs, _wow), (spatial_costs.suniward_costs, _suniward)],
    ids=["wow", "s-uniward"],
)
def test_costs_follow_the_definition(
    shape: tuple[int, int],
    costs: Callable[[npt.NDArray[np.uint8]], npt.NDArray[np.float32]],
    reference: Callable[[Floats], Floats],
) -> None:
    """Planes smaller than the 16 × 16 filters too: the edges mirror as often as it takes."""
    pixels = _picture(*shape)
    computed = costs(pixels)

    for color in range(3):
        expected = reference(pixels[..., color].astype(np.float64))
        np.testing.assert_allclose(computed[..., color], expected, rtol=2e-4)


@pytest.mark.parametrize("costs", [spatial_costs.wow_costs, spatial_costs.suniward_costs])
def test_stretches_join_seamlessly(
    costs: Callable[[npt.NDArray[np.uint8]], npt.NDArray[np.float32]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pixels = _picture(37, 29)
    whole = costs(pixels)
    monkeypatch.setattr(spatial_costs, "_STRETCH", 29 * 4)  # four rows at a time

    # Within rounding (see test_mipod's stretch test): a seam would differ by far more.
    np.testing.assert_allclose(costs(pixels), whole, rtol=1e-6)


def test_flat_colour_is_dear_and_wow_makes_it_wet() -> None:
    """Black on the left: its residuals are exactly 0 (any other flat colour leaves float
    rounding). Noise on the right is cheap."""
    pixels = _picture(48, 48)
    pixels[:, :32] = 0
    flat = (slice(16, 32), slice(0, 16))  # no filter reaches the noise from here
    wow, suniward = spatial_costs.wow_costs(pixels), spatial_costs.suniward_costs(pixels)

    np.testing.assert_allclose(wow[flat], spatial_costs.WET, rtol=1e-6)
    assert wow.max() <= spatial_costs.WET * (1 + 1e-6)
    assert np.all(np.isfinite(suniward))
    assert suniward[flat].min() > 10 * np.median(suniward[:, 40:])
