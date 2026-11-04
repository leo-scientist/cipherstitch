"""P_E, AUC and the split protocol of ``results/protocol.md`` (numpy only: both venvs)."""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from common import SEED

SPLITS = 10
FLD_RIDGE = 1e-6
WILSON_Z = 1.959964  # 95%
VOTE_CUT = 0.0  # an FLD ensemble says embedded when more base learners say so than not

Scores = npt.NDArray[np.float64]
# Trains on (cover rows, embedded rows) and returns a scorer: higher means embedded.
Trainer = Callable[[Scores, Scores], Callable[[Scores], Scores]]


@dataclass(frozen=True, slots=True)
class Result:
    pe_mean: float
    pe_std: float
    auc_mean: float
    auc_std: float
    splits: int


def split(count: int, number: int) -> tuple[npt.NDArray[np.intp], npt.NDArray[np.intp]]:
    """Image positions of the training and testing halves of split ``number``."""
    order = np.random.default_rng([SEED, number]).permutation(count)
    half = count // 2

    return np.sort(order[:half]), np.sort(order[half:])


def threshold(cover: Scores, embedded: Scores) -> tuple[float, float]:
    """The threshold minimizing (P_FA + P_MD) / 2 for "embedded when above", and that P_E."""
    values = np.concatenate([cover, embedded])
    labels = np.concatenate([np.zeros(cover.size), np.ones(embedded.size)])
    order = np.argsort(values, kind="stable")
    values, labels = values[order], labels[order]
    # Thresholds between distinct values: everything at or below index i is called cover.
    false_alarms = 1 - np.cumsum(labels == 0) / cover.size
    misses = np.cumsum(labels == 1) / embedded.size
    errors = (false_alarms + misses) / 2
    is_cut = np.append(values[1:] != values[:-1], True)
    candidates = np.flatnonzero(is_cut)
    best = candidates[np.argmin(errors[candidates])]
    chance = 0.5  # the cut below every value: everything is called embedded

    if errors[best] >= chance:
        return float(values[0] - 1), chance

    return float(values[best]), float(errors[best])


def error_at(cover: Scores, embedded: Scores, cut: float) -> float:
    return float((np.mean(cover > cut) + np.mean(embedded <= cut)) / 2)


def auc(cover: Scores, embedded: Scores) -> float:
    """The probability that an embedded score exceeds a cover score (ties count half)."""
    values = np.concatenate([cover, embedded])
    ranks = _average_ranks(values)
    embedded_ranks = ranks[cover.size :].sum()

    return float(
        (embedded_ranks - embedded.size * (embedded.size + 1) / 2) / (cover.size * embedded.size)
    )


def _average_ranks(values: Scores) -> Scores:
    order = np.argsort(values, kind="stable")
    ranks = np.empty(values.size)
    ranks[order] = np.arange(1, values.size + 1)
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    sums = np.bincount(inverse, weights=ranks)

    return sums[inverse] / counts[inverse]


def scalar_trainer(cover: Scores, embedded: Scores) -> Callable[[Scores], Scores]:
    """A scalar score, flipped if covers score higher on the training half."""
    sign = 1.0 if np.median(embedded) >= np.median(cover) else -1.0

    return lambda rows: sign * rows.reshape(len(rows), -1)[:, 0]


def fld_trainer(cover: Scores, embedded: Scores) -> Callable[[Scores], Scores]:
    """Fisher's linear discriminant with a small ridge, on standardized features."""
    both = np.concatenate([cover, embedded])
    centre, scale = both.mean(axis=0), both.std(axis=0)
    scale[scale == 0] = 1
    c, e = (cover - centre) / scale, (embedded - centre) / scale
    within = np.cov(c, rowvar=False) + np.cov(e, rowvar=False)
    within = np.atleast_2d(within)
    ridge = FLD_RIDGE * np.trace(within) / len(within)
    weights = np.linalg.solve(within + ridge * np.eye(len(within)), e.mean(axis=0) - c.mean(axis=0))

    return lambda rows: ((rows - centre) / scale) @ weights


def evaluate(cover: Scores, embedded: Scores, trainer: Trainer) -> Result:
    """P_E and AUC over :data:`SPLITS` splits; row ``i`` of both arrays is the same image."""
    pes, aucs = [], []

    for number in range(SPLITS):
        train, test = split(len(cover), number)
        score = trainer(cover[train], embedded[train])
        cut, _ = threshold(score(cover[train]), score(embedded[train]))
        test_cover, test_embedded = score(cover[test]), score(embedded[test])
        pes.append(error_at(test_cover, test_embedded, cut))
        aucs.append(auc(test_cover, test_embedded))

    return Result(
        float(np.mean(pes)), float(np.std(pes)), float(np.mean(aucs)), float(np.std(aucs)), SPLITS
    )


def wilson(successes: int, trials: int) -> tuple[float, float]:
    """The Wilson score interval (95%) of a rate."""
    if trials == 0:
        return float("nan"), float("nan")

    rate = successes / trials
    z2 = WILSON_Z**2
    centre = (rate + z2 / (2 * trials)) / (1 + z2 / trials)
    spread = (
        WILSON_Z * np.sqrt(rate * (1 - rate) / trials + z2 / (4 * trials**2)) / (1 + z2 / trials)
    )

    return float(max(centre - spread, 0.0)), float(min(centre + spread, 1.0))
