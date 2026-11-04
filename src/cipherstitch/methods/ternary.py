"""The ternary bound the trellis writer aims at: the λ under which steps taken with
probabilities ``e^(−λ c) / (1 + e^(−λ flip) + e^(−λ keep))`` carry a message, and the
entropies it is solved with (docs/methods.md#trellis-coding)."""

import numpy as np
import numpy.typing as npt

_LAMBDA_STEPS = 32  # bisections of log2 λ over [−64, 64]


def binary_entropy(p: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    tiny = np.finfo(np.float64).tiny

    return -(p * np.log(np.maximum(p, tiny)) + (1 - p) * np.log(np.maximum(1 - p, tiny)))


def ternary_lambda(costs: npt.NDArray[np.float64], bits: int, span: int) -> float:
    """The λ under which steps of cost ``c`` taken with probability
    ``e^(−λ c) / (1 + e^(−λ flip) + e^(−λ keep))`` carry ``bits`` in ``span`` values: the least
    total cost of a ternary code. Solved on a sample of them (``trellis._lambda_sample``),
    since it only shapes the first layer's costs and the tail's split."""
    target = bits * np.log(2) * costs.shape[1] / span
    low, high = -64.0, 64.0

    for _ in range(_LAMBDA_STEPS):
        middle = (low + high) / 2

        if _ternary_entropy(costs, 2.0**middle) > target:
            low = middle
        else:
            high = middle

    return float(2.0 ** ((low + high) / 2))


def _ternary_entropy(costs: npt.NDArray[np.float64], lam: float) -> float:
    """The entropy in nats of the steps' probabilities under ``lam``."""
    weights = np.exp(costs * -lam)
    total = 1 + weights.sum(axis=0)

    return float(np.sum(np.log(total) + lam * (costs * weights).sum(axis=0) / total))
