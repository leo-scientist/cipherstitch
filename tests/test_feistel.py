import hashlib
import struct

import numpy as np
import pytest

from cipherstitch.container import SEQUENTIAL_SEED
from cipherstitch.methods.feistel import Permutation

GOLDEN_ORDER = [946, 323, 4, 480, 633, 959, 226, 25, 200, 929, 564, 676, 16, 710, 478, 10]
SIZES = sorted(
    {0, 1, 2, 3, 4, 5, 15, 16, 17, 63, 64, 65, 1000}
    | {(1 << k) + d for k in range(2, 17) for d in (-1, 0, 1)}
)


def keyed_order(seed: bytes, count: int) -> list[int]:
    """The reference implementation printed in docs/format.md#keyed-order."""
    bits = max(2, (count - 1).bit_length())
    widths = (bits // 2, bits - bits // 2)  # of the high half A and the low half B
    size = 1 << widths[1]
    stream = hashlib.shake_256(seed + b"cipherstitch order").digest(24 * size)
    tables = struct.unpack(f"<{6 * size}I", stream)

    def permute(x: int) -> int:
        while True:  # cycle-walking: repeat until the value is below count
            a, b = x >> widths[1], x & (size - 1)
            for r in range(6):
                a, b = b, a ^ (tables[r * size + b] & ((1 << widths[r % 2]) - 1))
            x = a << widths[1] | b
            if x < count:
                return x

    return [permute(i) for i in range(count)]


def _order(seed: bytes, count: int) -> list[int]:
    order: list[int] = Permutation(seed, count).at(np.arange(count)).tolist()

    return order


@pytest.mark.parametrize("count", SIZES)
def test_is_a_permutation(count: int) -> None:
    assert sorted(_order(b"seed", count)) == list(range(count))


def test_is_a_permutation_of_a_million() -> None:
    order = Permutation(bytes(range(32)), 10**6).at(np.arange(10**6))

    assert (np.bincount(order, minlength=10**6) == 1).all()


@pytest.mark.parametrize("count", [*range(1, 70), 1000, 4097, 5000])
def test_matches_the_reference_implementation(count: int) -> None:
    assert _order(b"seed", count) == keyed_order(b"seed", count)


def test_any_positions_give_the_same_values_as_the_whole_order() -> None:
    permutation = Permutation(b"seed", 300_001)
    full = permutation.at(np.arange(300_001))
    positions = np.random.default_rng(5).integers(0, 300_001, 1000)

    assert np.array_equal(permutation.at(positions), full[positions])
    assert np.array_equal(permutation.at(np.arange(1000, 2000)), full[1000:2000])


def test_depends_on_the_seed_only() -> None:
    assert _order(b"seed", 1000) == _order(b"seed", 1000)
    assert _order(b"seed", 1000) != _order(b"other", 1000)


def test_the_sequential_seed_keys_no_order() -> None:
    """It asks for the units in sequence: a permutation keyed by it would be a silent keyed
    order."""
    with pytest.raises(ValueError, match="empty seed"):
        Permutation(SEQUENTIAL_SEED, 1000)


def test_order_is_stable_across_releases() -> None:
    """Changing this order makes every previously embedded JPEG unreadable."""
    assert _order(b"seed", 1000)[:16] == GOLDEN_ORDER
