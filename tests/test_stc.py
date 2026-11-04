import hashlib
import itertools

import numpy as np
import pytest

from cipherstitch.methods import stc

# Part of the stored format (docs/format.md#syndrome-trellis-codes).
GOLDEN_COLUMNS = "253d372139313b2f2b333f35273d2933"
GOLDEN_BLOCK_STARTS = [0, 5, 9, 13, 17, 21, 25, 30, 34, 38, 42, 46]
GOLDEN_BITS = "b99f531a0e2b40"
GOLDEN_SYNDROME = [0, 1, 1, 0, 0, 1, 1, 0, 0, 0, 1, 1]
UNITS = 40_000


def reference_syndrome(bits: list[int], m: int) -> list[int]:
    """The reference implementation printed in docs/format.md#syndrome-trellis-codes."""
    n = len(bits)
    segments = min(max(n // 32768, 1), 4096)
    columns = hashlib.shake_256(b"cipherstitch stc columns 297").digest(-(-n // m))
    message = [0] * m
    for unit, bit in enumerate(bits):
        block = unit * m // n
        segment = ((block + 1) * segments + m - 1) // m - 1
        end = (segment + 1) * m // segments
        column = columns[unit - (block * n + m - 1) // m] & 0x3F | 0x21
        for k in range(min(6, end - block)):
            message[block + k] ^= bit & column >> k
    return message


def _cover(seed: int = 1) -> tuple[stc.Bits, stc.Costs]:
    """Random unit bits, and random costs from 1 to 10."""
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, UNITS, dtype=np.uint8)

    return bits, rng.uniform(1, 10, UNITS).astype(np.float32)


def _embed(
    layout: stc.Layout, bits: stc.Bits, costs: stc.Costs, message: stc.Bits, kept_per_bit: int
) -> tuple[stc.Units, stc.Units]:
    """The changeable units and the units flipped among them."""
    units = stc.prune(layout, costs, kept_per_bit)

    return units, stc.embed(layout, bits, units, costs[units], message)


def _message(size: int, seed: int = 2) -> stc.Bits:
    return np.random.default_rng(seed).integers(0, 2, size, dtype=np.uint8)


@pytest.mark.parametrize("kept_per_bit", [0, 4, 10])
@pytest.mark.parametrize("fill", [0.05, 0.3, 1.0])
@pytest.mark.parametrize("segments", [1, 7])
def test_roundtrip_flips_only_changeable_units(
    fill: float, segments: int, kept_per_bit: int
) -> None:
    """The units left out keep their bits, ones included, and still count in the syndrome."""
    bits, costs = _cover()
    layout = stc.Layout(UNITS, int(UNITS * fill), segments)
    message = _message(layout.message_bits)
    units, flipped = _embed(layout, bits, costs, message, kept_per_bit)
    embedded = bits.copy()
    embedded[flipped] ^= 1

    assert np.array_equal(stc.syndrome(layout, embedded), message)
    assert np.isin(flipped, units).all()


def test_pruning_keeps_the_cheapest_units_and_one_in_every_block() -> None:
    _bits, costs = _cover()
    layout = stc.Layout(UNITS, 2000, 1)
    units = stc.prune(layout, costs, 4)
    blocks = units.astype(np.int64) * layout.message_bits // UNITS
    rest = np.setdiff1d(np.arange(UNITS), units)

    assert units.size < 4 * 2000 + 2000 * np.exp(-3)  # about e^-4 of the blocks get one more
    assert np.unique(blocks).size == layout.message_bits
    assert np.count_nonzero(costs[units] > costs[rest].min()) < 2000 * np.exp(-3)
    assert np.array_equal(stc.prune(layout, costs, 20), np.arange(UNITS))


@pytest.mark.parametrize("rank", [0, 1, 7, 50, 300])
def test_the_value_at_a_rank_is_the_same_without_a_copy(
    monkeypatch: pytest.MonkeyPatch, rank: int
) -> None:
    """With ties, and with fewer finite costs than the highest rank."""
    monkeypatch.setattr(stc, "_PLACE_CHUNK", 64)
    rng = np.random.default_rng(rank)
    ties = rng.integers(1, 40, 5000).astype(np.float32)
    sparse = np.full(5000, np.inf, dtype=np.float32)
    sparse[rng.integers(0, 5000, 100)] = 1.5

    for costs in (ties, sparse):
        assert stc._value_at_rank(costs, rank) == np.partition(costs, rank)[rank]


def test_few_changes_per_bit_at_low_fill() -> None:
    """Parity embedding changes 0.5 units per bit."""
    bits, costs = _cover()
    layout = stc.Layout(UNITS, UNITS // 80, 1)
    _units, flipped = _embed(layout, bits, costs, _message(layout.message_bits), 10)

    assert flipped.size < 0.2 * layout.message_bits


def test_finds_the_cheapest_set_of_changes() -> None:
    """Against every subset of 14 units: the code is small enough for a full search."""
    rng = np.random.default_rng(seed=4)
    layout = stc.Layout(14, 5, 1)
    bits = rng.integers(0, 2, 14, dtype=np.uint8)
    costs = rng.uniform(0, 1, 14).astype(np.float32)
    message = _message(5, seed=5)
    flipped = stc.embed(layout, bits, np.arange(14, dtype=np.int32), costs, message)
    best = min(
        (float(costs[list(subset)].sum()), subset)
        for size in range(15)
        for subset in itertools.combinations(range(14), size)
        if np.array_equal(stc.syndrome(layout, bits ^ np.isin(np.arange(14), subset)), message)
    )

    assert sorted(flipped.tolist()) == list(best[1])


@pytest.mark.parametrize("segments", [1, 7])
def test_neighbours_spell_the_bits_of_blocks_without_changeable_units(segments: int) -> None:
    """Every fifth block has none, except the first of a segment, which only its own units
    reach: the columns of the blocks before it make up for them."""
    bits, costs = _cover()
    layout = stc.Layout(UNITS, UNITS // 40, segments)
    message = _message(layout.message_bits)
    units = stc.prune(layout, costs, 10)
    blocks = units.astype(np.int64) * layout.message_bits // UNITS
    units = units[(blocks % 5 != 4) | np.isin(blocks, layout.bit_starts())]
    embedded = bits.copy()
    embedded[stc.embed(layout, bits, units, costs[units], message)] ^= 1

    assert np.array_equal(stc.syndrome(layout, embedded), message)


def test_a_message_no_changeable_unit_can_spell_raises() -> None:
    """Only the units of block 0 reach its row, and none of them is changeable."""
    layout = stc.Layout(64, 8, 1)
    units = np.arange(8, 64, dtype=np.int32)
    message = np.eye(1, 8, dtype=np.uint8)[0]

    with pytest.raises(stc.UnsolvableError):
        stc.embed(layout, np.zeros(64, np.uint8), units, np.ones(56, np.float32), message)


def test_segments_hold_about_the_same_number_of_units() -> None:
    assert stc.STANDARD.segments(0) == stc.STANDARD.segments(stc.UNITS_PER_SEGMENT - 1) == 1
    assert stc.STANDARD.segments(10 * stc.UNITS_PER_SEGMENT + 5) == 10
    assert stc.STANDARD.segments(10**12) == stc.MAX_SEGMENTS


@pytest.mark.parametrize(("units", "message_bits"), [(50, 12), (999, 1), (70_000, 300)])
def test_matches_the_reference_implementation(units: int, message_bits: int) -> None:
    bits = np.random.default_rng(units).integers(0, 2, units, dtype=np.uint8)
    layout = stc.Layout(units, message_bits, stc.STANDARD.segments(units))

    assert stc.syndrome(layout, bits).tolist() == reference_syndrome(bits.tolist(), message_bits)


def test_code_is_stable_across_releases() -> None:
    """Changing the code makes every previously embedded JPEG unreadable."""
    layout = stc.Layout(50, 12, 3)
    bits = np.unpackbits(np.frombuffer(bytes.fromhex(GOLDEN_BITS), dtype=np.uint8))[:50]

    assert stc.STANDARD.columns(16).tobytes().hex() == GOLDEN_COLUMNS
    assert stc.prune(layout, np.zeros(50, np.float32), 0).tolist() == GOLDEN_BLOCK_STARTS
    assert stc.syndrome(layout, bits).tolist() == GOLDEN_SYNDROME
