"""Classical LSB detectors against image LSB. Weighted Stego (Ker & Böhme 2008) and Sample
Pairs (Dumitrescu, Wu & Wang 2003) estimate the share of values that carry LSB-replaced bits
from the parity asymmetry replacement leaves. LSB matching, which image LSB writes, leaves
none, so both must find about nothing in its output."""

from collections.abc import Callable
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image

from cipherstitch import service
from cipherstitch.catalog import CipherId, MethodId
from cipherstitch.payload import HiddenFile

Pixels = npt.NDArray[np.uint8]
Estimator = Callable[[Pixels], float]

SIZE = 512
FILL = 0.5
TOLERANCE = 0.1
PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
OPTIONS = service.HideOptions(
    method=MethodId.LSB, cipher=CipherId.AES_GCM, password=PASSWORD, is_research=True
)
_NOISE = 1.5  # enough to make the cover look natural, little enough to keep it smooth
_PREDICTOR = np.array([[-1, 2, -1], [2, 0, 2], [-1, 2, -1]]) / 4  # Ker & Böhme's KB filter
_WEIGHT_OFFSET = 5  # w = 1 / (5 + local variance), as in Ker & Böhme


def _smooth_cover(path: Path) -> Pixels:
    """Gradients and waves plus a little Gaussian noise, saved as PNG: WS predicts each value
    from its neighbours, which uniform noise makes impossible."""
    rng = np.random.default_rng(seed=0)
    y, x = np.mgrid[0:SIZE, 0:SIZE] / SIZE
    channels = [
        128
        + 70 * np.sin(2 * np.pi * (x * (1 + k) + 0.3 * k)) * np.cos(2 * np.pi * y * (1.5 + k / 2))
        + 20 * np.sin(8 * np.pi * (x + y))
        + rng.normal(0, _NOISE, (SIZE, SIZE))
        for k in range(3)
    ]
    pixels = np.clip(np.rint(np.stack(channels, axis=-1)), 0, 255).astype(np.uint8)
    Image.fromarray(pixels).save(path)

    return pixels


def _weighted_stego(values: Pixels) -> float:
    """The estimated share of values carrying replaced bits, over the interior of one colour
    plane: 2 · Σ w (x − x̄)(x − x̂) / Σ w, with x̄ the value with its lowest bit flipped and x̂
    its KB prediction."""
    plane = values.astype(np.float64)
    height, width = plane.shape
    inner = plane[1:-1, 1:-1]
    predicted = sum(
        _PREDICTOR[i, j] * plane[i : i + height - 2, j : j + width - 2]
        for i in range(3)
        for j in range(3)
    )
    neighbours = np.stack([plane[:-2, 1:-1], plane[2:, 1:-1], plane[1:-1, :-2], plane[1:-1, 2:]])
    weights = 1 / (_WEIGHT_OFFSET + neighbours.var(axis=0))
    flipped_difference = 2 * (inner % 2) - 1  # x − x̄

    return float(2 * np.sum(weights * flipped_difference * (inner - predicted)) / weights.sum())


def _sample_pairs(values: Pixels) -> float:
    """The estimated share of values carrying replaced bits, from horizontal and vertical
    pairs (u, v) of one colour plane: the smaller root of
    C/2 · p² + (2X − P) · p + Y − X = 0, with C the pairs within one {2k, 2k + 1}."""
    plane = values.astype(np.int64)
    u = np.concatenate([plane[:, :-1].ravel(), plane[:-1].ravel()])
    v = np.concatenate([plane[:, 1:].ravel(), plane[1:].ravel()])
    is_even = v % 2 == 0
    x = np.count_nonzero((is_even & (u < v)) | (~is_even & (u > v)))
    y = np.count_nonzero((is_even & (u > v)) | (~is_even & (u < v)))
    same_pair = np.count_nonzero(u // 2 == v // 2)  # both in one pair {2k, 2k + 1}
    a, b, c = same_pair / 2, 2 * x - u.size, y - x
    root = np.sqrt(b * b - 4 * a * c)

    return float(min((-b + root) / (2 * a), (-b - root) / (2 * a)))


def _replaced(pixels: Pixels, bits: int, rng: np.random.Generator) -> Pixels:
    """``pixels`` with ``bits`` random bits written by LSB replacement into as many values
    picked at random."""
    values = pixels.reshape(-1).copy()
    positions = rng.choice(values.size, bits, replace=False)
    values[positions] = (values[positions] & 0xFE) | rng.integers(0, 2, bits, dtype=np.uint8)

    return values.reshape(pixels.shape)


def _hidden_at_fill(cover: Path, output: Path) -> int:
    """Hide random bytes filling :data:`FILL` of the capacity as the app's meter shows it;
    the bits the payload takes in the carrier."""
    overhead = service.estimate(cover, "payload.bin", 0, OPTIONS)
    assert overhead.available is not None
    size = int(FILL * overhead.available) - overhead.required
    estimate = service.estimate(cover, "payload.bin", size, OPTIONS)
    assert estimate.share == pytest.approx(FILL, abs=1e-3)
    data = np.random.default_rng(seed=1).bytes(size)
    service.hide(cover, HiddenFile("payload.bin", data), output, OPTIONS)

    return estimate.required * 8


@pytest.mark.parametrize("estimator", [_weighted_stego, _sample_pairs], ids=["ws", "spa"])
def test_image_lsb_leaves_no_parity_asymmetry(tmp_path: Path, estimator: Estimator) -> None:
    """At 50% fill (0.5 bits per value), as many bits written by LSB replacement are
    estimated within 0.1 of their share in every colour, and image LSB's output within 0.1
    of 0, like the cover.

    Measured over 40 hides: replacement within 0.012 of its share, the cover within 0.012 of
    0, image LSB within 0.024 (the salt and the writer's ±1 directions vary per hide)."""
    cover = _smooth_cover(tmp_path / "cover.png")
    bits = _hidden_at_fill(tmp_path / "cover.png", tmp_path / "embedded.png")

    with Image.open(tmp_path / "embedded.png") as saved:
        embedded = np.asarray(saved.convert("RGB"))

    replaced = _replaced(cover, bits, np.random.default_rng(seed=2))
    share = bits / cover.size

    for channel in range(3):
        assert estimator(cover[..., channel]) == pytest.approx(0, abs=TOLERANCE)
        assert estimator(replaced[..., channel]) == pytest.approx(share, abs=TOLERANCE)
        assert estimator(embedded[..., channel]) == pytest.approx(0, abs=TOLERANCE)
