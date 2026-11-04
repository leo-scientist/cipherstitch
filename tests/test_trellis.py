import os
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image

from cipherstitch import ciphers, container
from cipherstitch.catalog import MethodId
from cipherstitch.errors import CapacityError
from cipherstitch.methods import stc, ternary, trellis
from cipherstitch.methods.feistel import UNKEYED_SEED
from cipherstitch.methods.lsb import ImageChannel
from cipherstitch.params import engine
from cipherstitch.params.settings import Settings

SEQUENTIAL_UNITS = {"stc.spread": "sequential"}  # unit u is value u: the lead is the first ones
WHITE = 255


def _picture(path: Path, values: npt.NDArray[np.uint8], width: int) -> Path:
    Image.fromarray(values.reshape(-1, width, 3)).save(path)

    return path


def _noise(count: int, seed: int = 0) -> npt.NDArray[np.uint8]:
    return np.random.default_rng(seed).integers(20, 236, count, dtype=np.uint8)


def _capacity(path: Path, changes: Mapping[str, object] | None = None) -> int:
    tuning = engine.tuning(Settings.of(changes or {}), MethodId.LSB)

    return ImageChannel(path, tuning.coding, tuning.image).capacity(16)


def test_every_header_fits_in_the_head() -> None:
    """The tail's code depends on the stream's length, so a header read must stay in the
    head."""
    sealed = [cipher.sealed_size(container._HEADER.size) for cipher in ciphers.encrypting()]

    assert max(container._PLAIN.size, *sealed) <= trellis.HEAD_BYTES


@pytest.mark.parametrize(
    ("count", "lead", "head", "holds_data"),
    [(1_000_000, 16_384, 65_536, True), (2560, 320, 560, True), (2552, 319, 558, False)],
)
def test_regions_follow_the_number_of_units(
    count: int, lead: int, head: int, holds_data: bool
) -> None:
    """docs/format.md#trellis-channels: the head must hold its 560 bits."""
    regions = trellis.Regions.of(count)

    assert (regions.lead, regions.head, regions.holds_data) == (lead, head, holds_data)
    assert regions.tail == count - lead - head


def test_runs_of_units_are_spread_over_the_values() -> None:
    """Unit ``u`` is value ``a · u mod N``, ``a`` near 0.618 N and coprime to it."""
    indices = trellis.Regions.of(1000).lead_indices()

    assert indices[:4].tolist() == [0, 619, 238, 857]
    assert np.unique(trellis.Regions.of(1000).indices(np.arange(1000))).size == 1000


@pytest.mark.parametrize(
    ("split", "first"),
    [(0, 1), (40_000, 61), (trellis.EVEN_SPLIT, 50), (trellis.SPLIT_SCALE - 1, 99)],
)
def test_a_split_never_leaves_a_plane_empty(split: int, first: int) -> None:
    """docs/format.md#trellis-channels: the second bits take the split's share of the bits,
    to the nearest, but at least one and never all, whatever a head that is not one says."""
    second, parity = trellis.plane_layouts(stc.Layout(1000, 100, 1), split)

    assert (second.message_bits, parity.message_bits) == (first, 100 - first)
    assert second.unit_count == parity.unit_count == 1000


def test_the_ideal_split_is_the_second_bits_share_of_the_entropy() -> None:
    """A ternary code's entropy per value is H(p_flip) + (1 − p_flip) H(p_keep / (1 − p_flip)).
    With a step as dear either way, the second bit's part is the larger."""
    costs = np.random.default_rng(seed=3).uniform(0.1, 10, 50_000).astype(np.float32)
    bits = 20_000
    sample = np.stack((costs, costs)).astype(np.float64)
    weight = np.exp(-ternary.ternary_lambda(sample, bits, costs.size) * costs.astype(np.float64))
    p = weight / (1 + 2 * weight)

    def entropy(q: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        return -(q * np.log(q) + (1 - q) * np.log(1 - q))

    first = entropy(p).sum()
    second = ((1 - p) * entropy(p / (1 - p))).sum()
    split = trellis._ideal_split(sample, bits, costs.size)

    assert split == round(trellis.SPLIT_SCALE * first / (first + second))
    assert split > trellis.EVEN_SPLIT


def test_a_binary_step_is_the_cheaper_one() -> None:
    """Either step flips the parity. The one that flips the second bit too is up from an odd
    value and down from an even one."""
    odd = np.array([True, True, False, False])
    cheaper = np.array([1, -1, 1, -1], dtype=np.int8)  # the step that flips it, or keeps it

    assert trellis._parity_ups(odd, cheaper).tolist() == [True, False, False, True]


@pytest.mark.parametrize("is_odd", [True, False])
def test_a_binary_step_goes_either_way_at_random_on_a_tie(is_odd: bool) -> None:
    """As in LSB matching. A fixed way would step every even value down and every odd one up
    wherever a step costs the same both ways."""
    count = 10_000
    ups = trellis._parity_ups(np.full(count, is_odd), np.zeros(count, dtype=np.int8))

    assert 0.47 < np.count_nonzero(ups) / count < 0.53


def test_a_white_band_over_the_lead_leaves_no_room(tmp_path: Path) -> None:
    """A white value can't flip its second bit. With the lead in the band every write failed,
    an empty one too, though the capacity promised 22944 bytes. Binary coding steps its
    parity down, and the units spread over the picture reach past the band."""
    values = _noise(300 * 300 * 3)
    values[: 45 * 300 * 3] = WHITE
    path = _picture(tmp_path / "band.png", values, 300)

    assert _capacity(path, SEQUENTIAL_UNITS) == 0
    assert _capacity(path, {**SEQUENTIAL_UNITS, "stc.code": "binary"}) > 0
    assert _capacity(path) > 0


def test_a_white_row_at_the_start_of_the_lead_leaves_no_room(tmp_path: Path) -> None:
    """Values enough, but none in the first blocks of the lead's code: nothing else reaches
    its first rows, so a write failed whenever their bits came out wrong."""
    values = _noise(300 * 300 * 3)
    values[: 300 * 3] = WHITE
    path = _picture(tmp_path / "margin.png", values, 300)

    assert _capacity(path, SEQUENTIAL_UNITS) == 0
    assert _capacity(path) > 0


def test_values_at_the_ends_of_the_range_leave_no_room(tmp_path: Path) -> None:
    """Black and white with 3% of the values between: the capacity promised 25 bytes, but
    the lead and the head could carry none of theirs."""
    rng = np.random.default_rng(13)
    values = rng.choice(np.array([0, WHITE], dtype=np.uint8), 100 * 100 * 3)
    between = rng.random(values.size) < 0.03
    values[between] = rng.integers(1, WHITE, np.count_nonzero(between))

    assert _capacity(_picture(tmp_path / "ends.png", values, 100)) == 0


def test_a_sequential_order_takes_the_head_right_after_the_lead(tmp_path: Path) -> None:
    count = 300 * 300 * 3
    regions = trellis.Regions.of(count)
    values = _noise(count)
    values[regions.lead : regions.lead + regions.head] = WHITE
    path = _picture(tmp_path / "band.png", values, 300)

    assert _capacity(path, SEQUENTIAL_UNITS) == 0
    assert _capacity(path) > 0


def _keyed_head_cover(path: Path, *, public_head: bool) -> Path:
    """Units in sequence: the lead noise, 600 values spread over the head right after it,
    and, if asked, one value in each block of the public order's head's second bits. The
    rest is white."""
    count = 300 * 300 * 3
    regions = trellis.Regions.of(count, trellis.Format(is_spread=False))
    values = np.full(count, WHITE, dtype=np.uint8)
    values[: regions.lead] = _noise(regions.lead)
    spread = regions.lead + np.linspace(0, regions.head - 1, 600).astype(np.int64)
    values[spread] = _noise(600, seed=1)

    if public_head:
        blocks = trellis.plane_layouts(regions.head_layout(), trellis.EVEN_SPLIT)[0].message_bits
        firsts = -(-np.arange(blocks) * regions.head // blocks)
        order = regions.order(UNKEYED_SEED)
        indices = regions.indices(regions.lead + order.at(firsts))
        values[indices] = _noise(blocks, seed=2)

    return _picture(path, values, 300)


@pytest.mark.parametrize("public_head", [False, True])
def test_a_keyed_order_takes_the_head_from_the_whole_stream(
    tmp_path: Path, public_head: bool
) -> None:
    """The lead and the units after it are fine, but a keyed order draws its head from a
    stream nearly all white. The public order's head stands for it, and even when that one
    holds enough values by chance, the stream has too few for any other."""
    path = _keyed_head_cover(tmp_path / "white.png", public_head=public_head)

    assert _capacity(path, SEQUENTIAL_UNITS) == 0


def test_a_public_order_takes_the_head_from_its_own_units(tmp_path: Path) -> None:
    """The public order's head is all white, where an unencrypted payload's head goes, though
    the rest of the stream has plenty."""
    count = 300 * 300 * 3
    regions = trellis.Regions.of(count, trellis.Format(is_spread=False))
    values = _noise(count)
    public = regions.order(UNKEYED_SEED).at(np.arange(regions.head))
    values[regions.indices(regions.lead + public)] = WHITE
    path = _picture(tmp_path / "public.png", values, 300)

    assert _capacity(path, SEQUENTIAL_UNITS) == 0


def test_only_the_second_bits_need_values_that_can_take_a_bit(tmp_path: Path) -> None:
    """Two in each block of the lead's second bits, both in its second half: enough for
    them, though half the lead's blocks of both planes together have none. The parities can
    step a white value."""
    count = 300 * 300 * 3
    regions = trellis.Regions.of(count)
    values = _noise(count)
    values[: regions.lead] = WHITE
    block = regions.lead // 64
    halves = np.arange(64)[:, None] * block + block // 2 + np.array([10, 60])
    values[halves.ravel()] = _noise(halves.size, seed=3)
    path = _picture(tmp_path / "halves.png", values, 300)

    assert _capacity(path, SEQUENTIAL_UNITS) > 0


def test_a_cover_whose_values_all_take_a_bit_holds_data_at_the_least_size(
    tmp_path: Path,
) -> None:
    """The smallest picture whose head holds its bits: every value can take one, so the lead
    and the head have enough, and a short write goes through."""
    path = tmp_path / "least.png"
    Image.fromarray(_noise(2560).reshape(40, 64)).save(path)
    channel = ImageChannel(path)

    assert trellis.Regions.of(2560).holds_data
    assert channel.capacity(16) > 0

    channel.write(os.urandom(16), os.urandom(8), os.urandom(32))


def _rank(rows: npt.NDArray[np.uint8]) -> int:
    """Over GF(2)."""
    matrix, rank = rows.copy(), 0

    for column in range(matrix.shape[1]):
        pivot = next((row for row in range(rank, len(matrix)) if matrix[row, column]), None)

        if pivot is None:
            continue

        matrix[[rank, pivot]] = matrix[[pivot, rank]]
        others = matrix[:, column].astype(bool)
        others[rank] = False
        matrix[others] ^= matrix[rank]
        rank += 1

    return rank


@pytest.mark.parametrize(
    "layout",
    [stc.Layout(60, 12, 1), stc.Layout(60, 12, 2), stc.Layout(60, 12, 1, stc.Code(height=4))],
    ids=["one-segment", "two-segments", "height-4"],
)
def test_carriers_span_a_code_as_their_syndromes_do(layout: stc.Layout) -> None:
    """Checked against what the reader computes: each carrier's syndrome alone."""
    rng = np.random.default_rng(4)
    outcomes = set()

    for density in np.repeat([0.15, 0.3, 0.5], 10):
        carriers = rng.random(layout.unit_count) < density
        units = np.flatnonzero(carriers)
        alone = np.zeros((units.size, layout.unit_count), dtype=np.uint8)
        alone[np.arange(units.size), units] = 1
        syndromes = np.array([stc.syndrome(layout, bits) for bits in alone], dtype=np.uint8)
        spans = units.size > 0 and _rank(syndromes) == layout.message_bits
        outcomes.add(spans)

        assert stc.spans_every_row(carriers, layout) is spans

    assert outcomes == {False, True}


@pytest.mark.parametrize("side", [1, 29], ids=["one-pixel", "no-room-for-a-head"])
def test_a_cover_without_room_for_the_prefix_refuses_every_write(tmp_path: Path, side: int) -> None:
    """Nothing fits a carrier whose capacity is 0, an empty body neither: a 1 × 1 picture
    divided by zero in the rate, and a 29 × 29 one wrote a lead that no read returned."""
    cover = _picture(tmp_path / "tiny.png", _noise(3 * side * side), side)
    channel = ImageChannel(cover)

    assert channel.capacity(16) == channel.capacity(0) == 0

    for seed in (None, b"seed"):
        with pytest.raises(CapacityError, match="holds at most 0"):
            channel.write(bytes(16), b"", seed)
