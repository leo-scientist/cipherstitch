import numpy as np
import numpy.typing as npt
import pytest

from cipherstitch.methods import hill

Floats = npt.NDArray[np.float64]
KB = np.array([[-1, 2, -1], [2, -4, 2], [-1, 2, -1]]) / 4  # docs/methods.md#lsb
EPSILON = float(np.finfo(np.float32).eps)


def _correlated(values: Floats, kernel: Floats) -> Floats:
    """``kernel`` slid over ``values`` with symmetric edges, as conseal's ``boundary='symm'``."""
    reach = kernel.shape[0] // 2
    padded = np.pad(values, reach, mode="symmetric")
    result = np.zeros(values.shape)

    for offset in np.ndindex(kernel.shape):
        window = tuple(slice(o, o + size) for o, size in zip(offset, values.shape, strict=True))
        result += kernel[offset] * padded[window]

    return result


def _reference(values: Floats, high_pass: Floats, size: int = hill.FAR) -> Floats:
    """HILL as Li et al. define it, one plane or one channel at a time."""
    dimensions = values.ndim
    residual = np.abs(_correlated(values, high_pass))
    near = _correlated(residual, np.full((3,) * dimensions, 1 / 3**dimensions))
    far = np.full((size,) * dimensions, 1 / size**dimensions)

    return _correlated(1 / np.maximum(near, EPSILON), far)


def _picture(rows: int, columns: int) -> npt.NDArray[np.uint8]:
    """Noise on the right, a flat corner and a gradient on the left."""
    rng = np.random.default_rng(seed=3)
    pixels = rng.integers(0, 256, (rows, columns, 3), dtype=np.uint8)
    pixels[: rows // 2, : columns // 2] = 40
    pixels[rows // 2 :, : columns // 2] = np.arange(columns // 2, dtype=np.uint8)[None, :, None]

    return pixels


@pytest.mark.parametrize("shape", [(40, 52), (9, 30), (30, 9), (3, 4)])
def test_image_costs_are_hills(shape: tuple[int, int]) -> None:
    """Planes smaller than the 15 × 15 mean, too: the edges mirror as often as it takes."""
    pixels = _picture(*shape)
    costs = hill.image_costs(pixels)

    for color in range(3):
        expected = _reference(pixels[..., color].astype(np.float64), KB)
        np.testing.assert_allclose(costs[..., color], expected, rtol=1e-5)


@pytest.mark.parametrize("far", [9, 21])
def test_other_far_windows_are_hills_too(far: int) -> None:
    """Research's far windows, multiples of the near one."""
    pixels = _picture(40, 52)
    costs = hill.image_costs(pixels, far)

    for color in range(3):
        expected = _reference(pixels[..., color].astype(np.float64), KB, far)
        np.testing.assert_allclose(costs[..., color], expected, rtol=1e-5)


def test_stretches_do_not_change_the_costs(monkeypatch: pytest.MonkeyPatch) -> None:
    pixels = _picture(64, 48)
    whole = hill.image_costs(pixels)
    monkeypatch.setattr(hill, "_STRETCH", 5 * 48 * 3)  # five rows at a time

    np.testing.assert_allclose(hill.image_costs(pixels), whole, rtol=1e-6)


def test_audio_costs_follow_each_channel() -> None:
    rng = np.random.default_rng(seed=4)
    samples = rng.integers(-3000, 3000, (500, 2), dtype=np.int32)
    samples[:100] = 0
    samples[200:300, 1] = 7
    costs = hill.audio_costs(samples)
    high_pass = np.array([-1, 2, -1]) / 2

    for channel in range(2):
        expected = _reference(samples[:, channel].astype(np.float64), high_pass)
        np.testing.assert_allclose(costs[:, channel], expected, rtol=1e-5)


def test_smooth_areas_cost_more_than_noise() -> None:
    costs = hill.image_costs(_picture(64, 64))

    assert costs[40:, 2:24].min() > 10 * costs[:, 40:].max()
