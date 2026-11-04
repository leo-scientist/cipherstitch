"""What the fixture extensions share. The loader never sees this module: it sits beside the
scenario packages, not in one."""

from pathlib import Path

import numpy as np
import numpy.typing as npt
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from cipherstitch.extensions import (
    CarrierKind,
    Channel,
    CipherSpec,
    CostSpec,
    CounterNonceAead,
    Domain,
    Embedded,
    Group,
    Kind,
    MethodSpec,
    Param,
    ParamType,
    Scope,
    Tuning,
)


class ChaChaCopy(CounterNonceAead):
    """ChaCha20-Poly1305 on counter nonces, like the built-in: it passes the self-test."""

    _algorithm = ChaCha20Poly1305


def never_opened(path: Path, tuning: Tuning) -> Channel[Embedded]:
    raise AssertionError(f"A fixture that is only loaded opened {path}")


def ones(colors: npt.NDArray[np.uint8], rate: float) -> npt.NDArray[np.float32]:
    """What the built-in uniform costs are."""
    return np.ones(colors.shape, dtype=np.float32)


METHOD = MethodSpec("rule-method", 150, "Rule method", CarrierKind.TEXT, (".txt",), never_opened)
CIPHER = CipherSpec("rule-cipher", 150, "Rule cipher", ChaChaCopy())
LEVEL = Param(
    "level", "Level", "LEVEL", ParamType.INT, 1, Kind.FORMAT, minimum=0, maximum=7, step=1
)
GROUP = Group(
    "rule", "Rule group", "flask", "A group for the rules.", (LEVEL,), Scope(frozenset({METHOD.id}))
)
COST = CostSpec("rule-cost", "Rule cost", Domain.SPATIAL, ones)
