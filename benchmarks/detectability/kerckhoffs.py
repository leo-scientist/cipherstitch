"""E-K: can someone who knows the format find the public positions changed? (main venv)

    uv run python benchmarks/detectability/kerckhoffs.py --count 3000 [--method dct]
    uv run python benchmarks/detectability/kerckhoffs.py --count 3000 --round 2 [--method dct]

With a password only the salt sits at public positions: the lead's values (image LSB) or
coefficients (DCT). Round 1's LSB protocol is in
``results/protocol.md#e-k-kerckhoffs-test-of-the-public-lsb-positions``. Writes
``results/kerckhoffs.csv`` (``kerckhoffs-dct.csv``) and the per-image statistics to
``work/scores/kerckhoffs.npz`` (``kerckhoffs-dct.npz``). Round 2 reads the ``lsb2`` and
``dct2-q75`` outputs and writes to ``results/round-2/`` and ``kerckhoffs-round2*.npz``,
round 3 ``lsb3`` and ``dct3-q75`` to ``results/round-3/`` and ``kerckhoffs-round3*.npz``.

LSB has ``ws_public``, ``spa_public``, ``targeted`` (a Fisher discriminant over the
differences of 16 residual features between the lead's values and the others) and ``sca``,
the same features weighted by each value's change probability at the bound for the salt's
bits, from the HILL costs anyone can compute from the image.

DCT has two statistics, each a Fisher discriminant over 8 differences between the lead's
coefficients and all the others of the image: the shares of the values −3 or less, −2 …
2 and 3 or more, and the mean magnitude. ``histogram`` counts every coefficient alike.
``sca`` is selection-channel aware: it weighs each coefficient by its change probability
at the bound for the salt's bits, from the J-UNIWARD costs anyone can compute from the
image, with the lead's λ for both groups.

The controls change a share of the lead at random, or its cheapest values or coefficients.
The salt changes about 0.2% of the lead, so the shares start there: at 1.6% of the cheapest
``sca`` finds DCT's changes better than ``histogram``.
"""

import argparse
import csv
import dataclasses
import os
from collections.abc import Callable
from functools import partial
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import numpy.typing as npt
from common import (
    COVERS,
    MAX_WORKERS,
    RESULTS,
    SCORES,
    SEED,
    atomic,
    is_usable,
    logger,
    mark_done,
    name,
    selection,
)
from conditions import ROUND_FOLDERS, ROUNDS, at_fill
from embed import output_path
from metrics import Result, Trainer, evaluate, fld_trainer, scalar_trainer
from PIL import Image

from cipherstitch import kdf
from cipherstitch.media import jpeg
from cipherstitch.methods import dct, hill, juniward, trellis

FILLS = (1, 5)
CONTROL_DENSITIES = (0.002, 0.016, 0.1)  # ±1 changes on this share of the lead
CHEAP_DENSITIES = (0.002, 0.016)
VERDICT_PE = 0.40
FAMILIES = {
    1: {"lsb": "lsb", "dct": "dct-q75"},
    2: {"lsb": "lsb2", "dct": "dct2-q75"},
    3: {"lsb": "lsb3", "dct": "dct3-q75"},
}
_KB = np.array([[-1, 2, -1], [2, 0, 2], [-1, 2, -1]]) / 4
_WEIGHT_OFFSET = 5
_HISTOGRAM = np.arange(-4, 5)
_TARGETED = 7 + _HISTOGRAM.size
STATISTICS = ("ws_public", "spa_public", "targeted", "sca")
DCT_STATISTICS = ("histogram", "sca")
SALT_BITS = 8 * kdf.SALT_SIZE
_OUTER_VALUE = 3  # values beyond ±3 share the outer bins
_DCT_FEATURES = 2 * _OUTER_VALUE + 2
_BISECTIONS = 60

log = logger("kerckhoffs")

Pixels = npt.NDArray[np.uint8]
Mask = npt.NDArray[np.bool_]
Row = npt.NDArray[np.float64]
Summary = Callable[[np.ndarray, np.ndarray, np.ndarray], Row]


def public_mask(cover: Path) -> Mask:
    """The values holding the salt, the same for every carrier of this size."""
    with Image.open(cover) as opened:
        width, height = opened.size

    return lead_mask(width * height * 3).reshape(height, width, 3)


def _residuals(plane: npt.NDArray[np.float64]) -> tuple[npt.NDArray[np.float64], ...]:
    """The interior's values, KB predictions and WS weights."""
    height, width = plane.shape
    inner = plane[1:-1, 1:-1]
    predicted = sum(
        _KB[i, j] * plane[i : i + height - 2, j : j + width - 2]
        for i in range(3)
        for j in range(3)
        if _KB[i, j]
    )
    neighbours = np.stack([plane[:-2, 1:-1], plane[2:, 1:-1], plane[1:-1, :-2], plane[1:-1, 2:]])

    return inner, predicted, 1 / (_WEIGHT_OFFSET + neighbours.var(axis=0))


def _summary(x: np.ndarray, predicted: np.ndarray, weights: np.ndarray) -> npt.NDArray[np.float64]:
    r = x - predicted
    rounded = np.clip(np.rint(r), _HISTOGRAM[0], _HISTOGRAM[-1]).astype(np.int64)
    histogram = np.bincount(rounded - _HISTOGRAM[0], minlength=_HISTOGRAM.size) / r.size
    parity = np.mean(x % 2 == np.rint(predicted) % 2)

    return np.array(
        [
            np.sum(weights * r * r) / weights.sum(),
            np.sum(weights * np.abs(r)) / weights.sum(),
            weights.mean(),
            np.mean(np.abs(r) < 0.5),
            np.mean(np.abs(r) < 1),
            np.mean(np.abs(r) < 2),
            parity,
            *histogram,
        ]
    )


def _weighted_summary(
    x: np.ndarray, predicted: np.ndarray, weights: np.ndarray
) -> npt.NDArray[np.float64]:
    """:func:`_summary`'s features, each value counted by its weight."""
    r = x - predicted
    rounded = np.clip(np.rint(r), _HISTOGRAM[0], _HISTOGRAM[-1]).astype(np.int64)
    histogram = np.bincount(rounded - _HISTOGRAM[0], weights, minlength=_HISTOGRAM.size)
    parity = x % 2 == np.rint(predicted) % 2
    terms = [r * r, np.abs(r), np.abs(r) < 0.5, np.abs(r) < 1, np.abs(r) < 2, parity]
    total = weights.sum()

    return np.array([np.sum(weights * t) for t in terms] + list(histogram)) / total


def _spa(u: np.ndarray, v: np.ndarray) -> float:
    is_even = v % 2 == 0
    x = np.count_nonzero((is_even & (u < v)) | (~is_even & (u > v)))
    y = np.count_nonzero((is_even & (u > v)) | (~is_even & (u < v)))
    a, b, c = np.count_nonzero(u // 2 == v // 2) / 2, 2 * x - u.size, y - x

    if a == 0:
        return 0.0

    discriminant = b * b - 4 * a * c

    if discriminant < 0:
        return float(-b / (2 * a))

    root = np.sqrt(discriminant)

    return float(min((-b + root) / (2 * a), (-b - root) / (2 * a)))


def statistics(pixels: Pixels, mask: Mask) -> npt.NDArray[np.float64]:
    """``ws_public``, ``spa_public``, the 16 targeted features and the 15 of ``sca``, of
    one image."""
    values = pixels.astype(np.float64)
    change = _change_probabilities(hill.image_costs(pixels).reshape(-1), mask.reshape(-1))
    change = change.reshape(pixels.shape)
    ws_terms, ws_weights, pairs_u, pairs_v = [], [], [], []
    public_rows, other_rows, public_sca, other_sca = [], [], [], []

    for channel in range(3):
        plane = values[..., channel]
        inner, predicted, weights = _residuals(plane)
        public = mask[1:-1, 1:-1, channel]
        probability = change[1:-1, 1:-1, channel]
        x, p, w = inner[public], predicted[public], weights[public]
        ws_terms.append(np.sum(w * (2 * (x % 2) - 1) * (x - p)))
        ws_weights.append(np.sum(w))
        public_rows.append((x, p, w))
        other_rows.append((inner[~public], predicted[~public], weights[~public]))
        public_sca.append((x, p, probability[public]))
        other_sca.append((inner[~public], predicted[~public], probability[~public]))
        rows, columns = np.nonzero(mask[:, :, channel])
        right, below = columns < plane.shape[1] - 1, rows < plane.shape[0] - 1
        pairs_u += [plane[rows[right], columns[right]], plane[rows[below], columns[below]]]
        pairs_v += [plane[rows[right], columns[right] + 1], plane[rows[below] + 1, columns[below]]]

    def pooled(groups: list[tuple[np.ndarray, ...]], summary: Summary) -> Row:
        return summary(*(np.concatenate([g[k] for g in groups]) for k in range(3)))

    ws = 2 * sum(ws_terms) / sum(ws_weights)
    spa = _spa(np.concatenate(pairs_u).astype(np.int64), np.concatenate(pairs_v).astype(np.int64))
    targeted = pooled(public_rows, _summary) - pooled(other_rows, _summary)
    sca = pooled(public_sca, _weighted_summary) - pooled(other_sca, _weighted_summary)

    return np.concatenate([[ws, spa], targeted, sca])


def _read(path: Path) -> Pixels:
    with Image.open(path) as opened:
        return np.asarray(opened.convert("RGB"))


def _controlled(
    pixels: Pixels, mask: Mask, density: float, index: int, *, is_cheap: bool
) -> Pixels:
    """±1 on ``density`` of the public values, inwards at 0 and 255: at random, or the
    cheapest by HILL (``is_cheap``)."""
    rng = np.random.default_rng([SEED, index, round(density * 1000), is_cheap])
    positions = np.flatnonzero(mask)
    count = round(density * positions.size)

    if is_cheap:
        costs = hill.image_costs(pixels).reshape(-1)
        chosen = positions[np.argsort(costs[positions])[:count]]
    else:
        chosen = rng.choice(positions, count, replace=False)

    values = pixels.reshape(-1).astype(np.int16)
    steps = rng.choice(np.array([-1, 1], dtype=np.int16), chosen.size)
    steps[values[chosen] == 0] = 1
    steps[values[chosen] == 255] = -1
    values[chosen] += steps

    return values.astype(np.uint8).reshape(pixels.shape)


def image_rows(index: int, mask: Mask, family: str) -> dict[str, npt.NDArray[np.float64]]:
    """The statistics of one image's cover, ``family``'s outputs and the controls."""
    cover = _read(COVERS / "png" / f"{name(index)}.png")
    rows = {"cover": statistics(cover, mask)}

    for fill in FILLS:
        rows[at_fill(family, fill)] = statistics(_read(output_path(family, fill, index)), mask)

    for density in CONTROL_DENSITIES:
        controlled = _controlled(cover, mask, density, index, is_cheap=False)
        rows[f"control-{density:g}"] = statistics(controlled, mask)

    for density in CHEAP_DENSITIES:
        cheap = _controlled(cover, mask, density, index, is_cheap=True)
        rows[f"control-cheap-{density:g}"] = statistics(cheap, mask)

    return rows


def lead_mask(count: int) -> Mask:
    """Which of ``count`` values or AC coefficients hold the salt, the same for every
    carrier with that many."""
    mask = np.zeros(count, dtype=bool)
    mask[trellis.Regions.of(count).lead_indices()] = True

    return mask


def _shares(values: npt.NDArray[np.int64], weights: npt.NDArray[np.float64]) -> Row:
    bins = np.clip(values, -_OUTER_VALUE, _OUTER_VALUE) + _OUTER_VALUE
    total = weights.sum()
    shares = np.bincount(bins, weights=weights, minlength=2 * _OUTER_VALUE + 1) / total

    return np.append(shares, np.sum(weights * np.abs(values)) / total)


def _change_probabilities(costs: npt.NDArray[np.float32], mask: Mask) -> Row:
    """A binary embedding's change probability of every unit, 1 / (1 + e^(λρ)), with
    λ such that the lead's carry :data:`SALT_BITS`."""
    rho = costs.astype(np.float64)

    def entropy(scale: float) -> float:
        exponent = scale * rho[mask]
        log_change, log_keep = -np.logaddexp(0, exponent), -np.logaddexp(0, -exponent)

        return float(-(np.exp(log_change) * log_change + np.exp(log_keep) * log_keep).sum())

    low, high = 0.0, 1.0

    while entropy(high) > SALT_BITS * np.log(2):
        high *= 2

    for _ in range(_BISECTIONS):
        middle = (low + high) / 2
        low, high = (middle, high) if entropy(middle) > SALT_BITS * np.log(2) else (low, middle)

    return np.exp(-np.logaddexp(0, high * rho))


def dct_statistics(coefficients: jpeg.Coefficients, mask: Mask) -> Row:
    """``histogram``'s 8 differences, then ``sca``'s."""
    ac = coefficients.ac.astype(np.int64)
    weights = _change_probabilities(juniward.costs(coefficients), mask)
    ones = np.ones(ac.size)

    return np.concatenate(
        [
            _shares(ac[mask], ones[mask]) - _shares(ac[~mask], ones[~mask]),
            _shares(ac[mask], weights[mask]) - _shares(ac[~mask], weights[~mask]),
        ]
    )


def dct_controlled(
    cover: jpeg.Coefficients, mask: Mask, density: float, index: int, *, is_cheap: bool
) -> jpeg.Coefficients:
    """±1 on ``density`` of the lead's coefficients, inwards at the largest magnitude: at
    random, or the cheapest by J-UNIWARD (``is_cheap``)."""
    rng = np.random.default_rng([SEED, index, round(density * 1000), is_cheap])
    positions = np.flatnonzero(mask)
    count = round(density * positions.size)

    if is_cheap:
        chosen = positions[np.argsort(juniward.costs(cover)[positions])[:count]]
    else:
        chosen = rng.choice(positions, count, replace=False)

    ac = cover.ac.copy()
    steps = rng.choice(np.array([-1, 1], dtype=np.int16), count)
    ac[chosen] += np.where(np.abs(ac[chosen]) >= dct.MAX_MAGNITUDE, -np.sign(ac[chosen]), steps)

    return dataclasses.replace(cover, ac=ac)


def dct_image_rows(index: int, family: str) -> dict[str, Row]:
    """The statistics of one QF 75 cover, ``family``'s outputs and the controls (NaN where a
    payload did not fit)."""
    cover = jpeg.read_coefficients(COVERS / "q75" / f"{name(index)}.jpg")
    mask = lead_mask(cover.ac.size)
    rows = {"cover": dct_statistics(cover, mask)}

    for fill in FILLS:
        is_embedded = is_usable(at_fill(family, fill), index)
        embedded = output_path(family, fill, index)
        rows[at_fill(family, fill)] = (
            dct_statistics(jpeg.read_coefficients(embedded), mask)
            if is_embedded
            else np.full(2 * _DCT_FEATURES, np.nan)
        )

    for density in CONTROL_DENSITIES:
        controlled = dct_controlled(cover, mask, density, index, is_cheap=False)
        rows[f"control-{density:g}"] = dct_statistics(controlled, mask)

    for density in CHEAP_DENSITIES:
        cheap = dct_controlled(cover, mask, density, index, is_cheap=True)
        rows[f"control-cheap-{density:g}"] = dct_statistics(cheap, mask)

    return rows


def report(
    table: dict[str, Row], statistics: dict[str, tuple[slice, Trainer]]
) -> list[tuple[str, str, int, Result]]:
    """Every statistic on every condition against the covers, on the images both have."""
    results = []

    for key in (k for k in table if k != "cover"):
        keep = ~np.isnan(table[key]).any(axis=1)

        for statistic, (columns, trainer) in statistics.items():
            cover, embedded = table["cover"][keep, columns], table[key][keep, columns]
            result = evaluate(cover, embedded, trainer)
            results.append((key, statistic, int(keep.sum()), result))
            log.info(
                "%s %s: P_E %.3f ± %.3f, AUC %.3f",
                key,
                statistic,
                result.pe_mean,
                result.pe_std,
                result.auc_mean,
            )

    return results


def _write(results: list[tuple[str, str, int, Result]], path: Path) -> None:
    with atomic(path) as partial_path, partial_path.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["condition", "statistic", "n", "pe_mean", "pe_std", "auc_mean", "auc_std"])

        for key, statistic, count, r in results:
            writer.writerow(
                [
                    key,
                    statistic,
                    count,
                    f"{r.pe_mean:.4f}",
                    f"{r.pe_std:.4f}",
                    f"{r.auc_mean:.4f}",
                    f"{r.auc_std:.4f}",
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--method", choices=("lsb", "dct"), default="lsb")
    parser.add_argument("--round", type=int, choices=ROUNDS, default=1)
    args = parser.parse_args()
    is_dct = args.method == "dct"
    indices = selection("jpeg" if is_dct else "lsb", args.count)
    family = FAMILIES[args.round]["dct" if is_dct else "lsb"]

    if is_dct:
        work = partial(dct_image_rows, family=family)
        features = slice(0, _DCT_FEATURES), slice(_DCT_FEATURES, None)
        statistics = {s: (f, fld_trainer) for s, f in zip(DCT_STATISTICS, features, strict=True)}
    else:
        mask = public_mask(COVERS / "png" / f"{name(indices[0])}.png")
        log.info("%d public values", np.count_nonzero(mask))
        work = partial(image_rows, mask=mask, family=family)
        statistics = {
            "ws_public": (slice(0, 1), scalar_trainer),
            "spa_public": (slice(1, 2), scalar_trainer),
            "targeted": (slice(2, 2 + _TARGETED), fld_trainer),
            "sca": (slice(2 + _TARGETED, None), fld_trainer),
        }

    os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")  # J-UNIWARD's products, per worker

    with Pool(MAX_WORKERS) as pool:
        per_image = pool.map(work, indices, chunksize=8)

    table = {key: np.stack([rows[key] for rows in per_image]) for key in per_image[0]}
    suffix = "-dct" if is_dct else ""
    scores = f"kerckhoffs{'' if args.round == 1 else f'-round{args.round}'}{suffix}.npz"

    with atomic(SCORES / scores) as partial_path:
        np.savez_compressed(partial_path, indices=np.array(indices), **table)

    results = report(table, statistics)
    _write(results, RESULTS / ROUND_FOLDERS[args.round] / f"kerckhoffs{suffix}.csv")
    cipherstitch = [r for key, _, _, r in results if not key.startswith("control-")]
    worst = min(r.pe_mean for r in cipherstitch)
    verdict = "detects" if worst <= VERDICT_PE else "clean"
    log.info(
        "E-K %s: lowest P_E on CipherStitch output %.3f (threshold %.2f)",
        verdict,
        worst,
        VERDICT_PE,
    )
    mark_done(f"kerckhoffs{'' if args.round == 1 else f'-round{args.round}'}{suffix}-{args.count}")


if __name__ == "__main__":
    main()
