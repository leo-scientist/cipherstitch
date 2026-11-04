"""MiPOD's costs against the paper's definitions, computed directly in float64."""

from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image

from cipherstitch.methods import lsb, mipod

Floats = npt.NDArray[np.float64]
Pixels = npt.NDArray[np.uint8]

SHAPES = [(24, 31), (5, 40), (40, 7)]
RATE = 0.2


def _picture(rows: int, columns: int) -> Pixels:
    """Noise on the right, a flat corner and a gradient on the left."""
    rng = np.random.default_rng(seed=5)
    pixels = rng.integers(0, 256, (rows, columns, 3), dtype=np.uint8)
    pixels[: rows // 2, : columns // 2] = 40
    pixels[rows // 2 :, : columns // 2] = np.arange(columns // 2, dtype=np.uint8)[None, :, None]

    return pixels


def _wiener_residual(plane: Floats) -> Floats:
    """x − wiener2(x, [2 2]) as MATLAB computes it: each window holds the pixel and its
    neighbours to the right and below, zeros beyond the plane, and the noise is the mean
    local variance."""
    rows, columns = plane.shape
    padded = np.pad(plane, ((0, 1), (0, 1)))

    def mean(values: Floats) -> Floats:
        corners = values[:rows, :columns] + values[:rows, 1:] + values[1:, :columns]

        return (corners + values[1:, 1:]) / 4

    local_mean = mean(padded)
    local_variance = mean(padded**2) - local_mean**2
    noise = local_variance.mean()
    gain = np.maximum(local_variance - noise, 0) / np.maximum(local_variance, noise)

    return plane - (local_mean + gain * (plane - local_mean))


def _fitted_basis() -> Floats:
    """G: the 2D DCT basis functions of degree below 9 over a 9 × 9 block, one per column."""
    samples = np.arange(mipod.BLOCK)

    def vector(u: int) -> Floats:
        scale = np.sqrt((1 if u == 0 else 2) / mipod.BLOCK)
        vector: Floats = scale * np.cos(np.pi * (2 * samples + 1) * u / (2 * mipod.BLOCK))

        return vector

    functions = [
        np.outer(vector(u), vector(v)).reshape(-1)
        for u in range(mipod.BLOCK)
        for v in range(mipod.BLOCK)
        if u + v < mipod.DEGREE
    ]

    return np.stack(functions, axis=1)


def _variances(residual: Floats) -> Floats:
    """‖P⊥ r‖² / (p² − q) over the 9 × 9 block around each residual, edges mirrored, with
    P⊥ = I − G (Gᵀ G)⁻¹ Gᵀ."""
    basis = _fitted_basis()
    size = mipod.BLOCK * mipod.BLOCK
    orthogonal = np.eye(size) - basis @ np.linalg.solve(basis.T @ basis, basis.T)
    padded = np.pad(residual, mipod.BLOCK // 2, mode="symmetric")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (mipod.BLOCK, mipod.BLOCK))
    left = windows.reshape(*residual.shape, size) @ orthogonal
    variances: Floats = (left**2).sum(axis=-1) / (size - basis.shape[1])

    return variances


def _fisher_information(plane: Floats) -> Floats:
    """1 / σ⁴, σ² floored, averaged over 7 × 7 values with symmetric edges."""
    variances = np.maximum(_variances(_wiener_residual(plane)), mipod.VARIANCE_FLOOR)
    padded = np.pad(1 / variances**2, mipod.SMOOTHING // 2, mode="symmetric")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (mipod.SMOOTHING,) * 2)

    return windows.mean(axis=(-2, -1))


def _change_rates(products: Floats) -> Floats:
    """β with ln(1 / β − 2) = λ I β for each λ I in ``products``, by bisection on β: the left
    side falls from infinity to 0 over (0, 1/3), the right side grows."""
    low, high = np.zeros_like(products), np.full_like(products, 1 / 3)

    for _ in range(200):
        middle = (low + high) / 2
        below = np.log(1 / middle - 2) > products * middle
        low, high = np.where(below, middle, low), np.where(below, high, middle)

    return (low + high) / 2


def _entropy(rates: Floats) -> float:
    """In bits: a step up and a step down each with probability β."""
    kept = 1 - 2 * rates

    return float(-np.sum(2 * rates * np.log2(rates) + kept * np.log2(kept)))


def _lambda(information: Floats, rate: float) -> float:
    """The λ whose change rates carry ``rate`` bits per value, by bisection on λ itself."""
    low, high = 0.0, 1.0

    while _entropy(_change_rates(high * information)) > rate * information.size:
        low, high = high, 2 * high

    for _ in range(100):
        middle = (low + high) / 2

        if _entropy(_change_rates(middle * information)) > rate * information.size:
            low = middle
        else:
            high = middle

    return (low + high) / 2


@pytest.mark.parametrize("shape", SHAPES)
def test_the_residual_is_wiener2s(shape: tuple[int, int]) -> None:
    plane = _picture(*shape)[..., 0]
    computed = mipod._residuals(plane, mipod._noise(plane), 0, shape[0])

    np.testing.assert_allclose(computed, _wiener_residual(plane.astype(np.float64)), atol=1e-4)


def test_the_variance_is_what_the_fitted_basis_leaves() -> None:
    residual = np.random.default_rng(seed=2).normal(0, 4, (30, 26))
    padded = np.pad(residual, mipod.BLOCK // 2, mode="symmetric").astype(np.float32)

    np.testing.assert_allclose(mipod._variances(padded), _variances(residual), rtol=1e-5)


@pytest.mark.parametrize("shape", SHAPES)
def test_the_fisher_information_follows_the_definition(shape: tuple[int, int]) -> None:
    """Planes smaller than the blocks too: the edges mirror as often as it takes."""
    pixels = _picture(*shape)
    computed = mipod.fisher_information(pixels)

    for color in range(3):
        expected = _fisher_information(pixels[..., color].astype(np.float64))
        np.testing.assert_allclose(computed[..., color], expected, rtol=1e-4)


@pytest.mark.parametrize("rate", [0.01, RATE, 1.0])
def test_lambda_spreads_the_payload_over_every_value(rate: float) -> None:
    pixels = _picture(24, 31)
    information = mipod.fisher_information(pixels)
    lam = mipod.ternary_lambda(information, rate)
    rates = 1 / (np.exp(mipod.image_costs(pixels, rate).astype(np.float64)) + 2)

    assert lam == pytest.approx(_lambda(information.astype(np.float64), rate), rel=1e-6)
    assert _entropy(rates) == pytest.approx(rate * pixels.size, rel=1e-5)


def test_costs_are_ln_of_the_change_rates() -> None:
    pixels = _picture(24, 31)
    information = mipod.fisher_information(pixels)
    lam = mipod.ternary_lambda(information, RATE)
    rates = _change_rates(lam * information.astype(np.float64))

    np.testing.assert_allclose(mipod.image_costs(pixels, RATE), np.log(1 / rates - 2), rtol=1e-6)


def test_the_cost_solves_its_equation_at_every_product() -> None:
    """From the smallest λ I a search reaches to the largest, 2⁶⁴ times the floor's."""
    products = np.exp(np.linspace(-60, 54, 20_001))
    costs = mipod.costs_at(products)

    # The reference's ln(1 / β − 2) loses digits as β nears 1/3, where costs near 0.
    resolved = products > 1e-3

    np.testing.assert_allclose(costs * (np.exp(costs) + 2), products, rtol=1e-12)
    np.testing.assert_allclose(
        costs[resolved], np.log(1 / _change_rates(products[resolved]) - 2), rtol=1e-9
    )


def test_a_smaller_payload_costs_more_everywhere() -> None:
    """Its λ is larger, so every change rate is smaller."""
    pixels = _picture(40, 40)
    costs = [mipod.image_costs(pixels, rate) for rate in (0.05, RATE, 0.6)]

    assert np.all(costs[0] > costs[1])
    assert np.all(costs[1] > costs[2])


def test_more_information_costs_more() -> None:
    pixels = _picture(40, 40)
    information = mipod.fisher_information(pixels).reshape(-1)
    costs = mipod.image_costs(pixels, RATE).reshape(-1)[np.argsort(information, kind="stable")]

    assert np.all(np.diff(costs) >= 0)


@pytest.mark.parametrize(
    "pixels",
    [
        _picture(32, 32),
        np.zeros((32, 32, 3), dtype=np.uint8),
        np.full((32, 32, 3), 255, dtype=np.uint8),
        np.full((1, 1, 3), 7, dtype=np.uint8),
    ],
    ids=["picture", "black", "white", "one pixel"],
)
def test_costs_are_finite_and_positive(pixels: Pixels) -> None:
    """A black plane has no noise at all, where wiener2 would divide 0 by 0."""
    costs = mipod.image_costs(pixels, RATE)

    assert np.all(np.isfinite(costs))
    assert np.all(costs > 0)


def test_flat_colour_costs_the_most() -> None:
    """Where every block and window stays in flat colour, the variance is at its floor."""
    pixels = _picture(48, 48)
    pixels[:, :32] = 90
    costs = mipod.image_costs(pixels, RATE)

    np.testing.assert_allclose(costs[:, :16], costs.max(), rtol=1e-6)
    assert costs[:, 40:].max() < costs.max() / 2


def test_stretches_join_seamlessly(monkeypatch: pytest.MonkeyPatch) -> None:
    pixels = _picture(37, 29)
    whole = mipod.image_costs(pixels, RATE)
    monkeypatch.setattr(mipod, "_STRETCH", 29 * 4)  # four rows at a time

    # Within rounding, not bit for bit: the tensordot's BLAS kernel sums in an order that
    # depends on the operand's shape and the CPU (one float32 ulp apart on some x86_64
    # runners, identical on macOS arm64). A seam would differ by far more than 1e-6.
    np.testing.assert_allclose(mipod.image_costs(pixels, RATE), whole, rtol=1e-6)


def test_the_lambda_sample_reaches_every_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    """One colour's information times 10: a sample of red values alone would miss it."""
    information = mipod.fisher_information(_picture(60, 60))
    information[..., 1] *= 10
    exact = mipod.ternary_lambda(information, RATE)
    monkeypatch.setattr(mipod, "_LAMBDA_SAMPLE", 3000)

    assert len(mipod._sample(information.reshape(-1))) == 3000
    assert mipod.ternary_lambda(information, RATE) == pytest.approx(exact, rel=0.02)


def test_flat_values_stay_wet(tmp_path: Path) -> None:
    pixels = np.random.default_rng(seed=6).integers(0, 256, (64, 64, 3), dtype=np.uint8)
    pixels[:20] = 90
    path = tmp_path / "cover.png"
    Image.fromarray(pixels).save(path)
    wet = lsb.ImageChannel(path, rules=lsb.ImageRules(costs=lsb.CostFunction.MIPOD))
    dry = lsb.ImageChannel(path, rules=lsb.ImageRules(lsb.CostFunction.MIPOD, is_wet=False))
    flat, costs = wet._flat, wet._costs(RATE)
    expected = mipod.image_costs(pixels, RATE).reshape(-1)

    assert np.count_nonzero(flat) == 20 * 64 * 3
    assert np.all(costs[flat] == lsb.WET_COST)
    np.testing.assert_array_equal(costs[~flat], expected[~flat])
    np.testing.assert_array_equal(dry._costs(RATE), expected)
