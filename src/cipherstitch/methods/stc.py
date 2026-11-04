"""Syndrome-trellis codes (Filler, Judas, Fridrich 2011) with constraint height 6, in numpy
(docs/format.md#syndrome-trellis-codes).

A code stores its message as the syndrome of the unit bits, so the reader needs neither the
changeable units nor the number of changes. Its segments are independent codes over runs of
consecutive blocks; the Viterbi search runs them in lockstep, so each Python step works on a
``(segments, 64)`` cost array.
"""

import hashlib
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

CONSTRAINT_HEIGHT = 6
UNITS_PER_SEGMENT = 32768
MAX_SEGMENTS = 4096

Bits = npt.NDArray[np.uint8]
Units = npt.NDArray[np.int32]
Costs = npt.NDArray[np.float32]
_IntArray = npt.NDArray[np.int64]

# Of 4096 numbered labels, the one whose codes cost image LSB and DCT the least in a search.
# Without a number the first columns repeat.
COLUMNS_LABEL_NUMBER = 297
_COLUMNS_LABEL = b"cipherstitch stc columns"
_INF = np.float32(1e30)
_UNREACHABLE = _INF / 2  # a path cost this high never had a finite way through
_RENORMALIZE_EVERY = 32  # steps; keeps the float32 path costs precise
_PLACE_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class Code:
    """What the reader must know of a code besides its layout: Standard's by default, Research
    may change it (docs/format.md#syndrome-trellis-codes). A height of 3 to 8 fits a column in
    a byte."""

    height: int = CONSTRAINT_HEIGHT
    label_number: int = COLUMNS_LABEL_NUMBER
    units_per_segment: int = UNITS_PER_SEGMENT
    max_segments: int = MAX_SEGMENTS

    @property
    def states(self) -> int:
        return 1 << self.height

    def segments(self, unit_count: int) -> int:
        """Segments of a code over ``unit_count`` units: about ``units_per_segment`` each."""
        return min(max(unit_count // self.units_per_segment, 1), self.max_segments)

    def columns(self, count: int) -> Bits:
        """The first ``count`` columns of the check matrix; bit ``k`` of a column is the row
        ``k`` below the unit's block. Every column has its first and last row."""
        label = b"%s %d" % (_COLUMNS_LABEL, self.label_number)
        raw = np.frombuffer(hashlib.shake_256(label).digest(count), dtype=np.uint8)
        mask = np.uint8((1 << self.height) - 1)

        return raw & mask | np.uint8(1 | 1 << (self.height - 1))


STANDARD = Code()


class UnsolvableError(Exception):
    """No change of the changeable units spells the message."""


@dataclass(frozen=True, slots=True)
class Layout:
    """``message_bits`` blocks over ``unit_count`` units (at least as many units as bits),
    split into ``segments`` runs of consecutive blocks."""

    unit_count: int
    message_bits: int
    segments: int
    code: Code = STANDARD

    def bit_starts(self) -> _IntArray:
        """The first block of every segment, then ``message_bits``."""
        return np.arange(self.segments + 1, dtype=np.int64) * self.message_bits // self.segments


def syndrome(layout: Layout, bits: Bits) -> Bits:
    """The message: bit ``r`` is the XOR of row ``r`` of the columns of every unit whose bit
    is 1."""
    height = layout.code.height
    parity = np.zeros(layout.message_bits + height, dtype=np.uint8)

    for start in range(0, bits.size, _PLACE_CHUNK):  # bounded temporaries
        units = np.flatnonzero(bits[start : start + _PLACE_CHUNK]).astype(np.int32) + start
        _segment, block, value = _place(layout, units)
        firsts = np.flatnonzero(np.diff(block, prepend=-1))  # units are sorted by block
        blocks, rows = block[firsts], np.bitwise_xor.reduceat(value, firsts)

        # Each block appears once per chunk, so these XOR updates never hit a repeated index.
        for k in range(height):
            parity[blocks + k] ^= (rows >> k) & 1

    return parity[: layout.message_bits]


def prune(layout: Layout, costs: Costs, kept_per_bit: int) -> Units:
    """The changeable units, sorted, of a code over units with these ``costs``: the cheapest
    ``kept_per_bit`` × message bits, and the cheapest unit of every block left without one.
    Every column has its first row, so one changeable unit per block always solves the
    block's own row: the search never fails, and it steps over only these units."""
    n, m = layout.unit_count, layout.message_bits
    count = min(kept_per_bit * m, n)

    if count == n:
        return np.arange(n, dtype=np.int32)

    threshold = _value_at_rank(costs, count)
    # int32 from the start: flatnonzero's int64 indices were the largest transient but the paths.
    below = np.flatnonzero(costs < threshold).astype(np.int32, copy=False)
    ties = np.flatnonzero(costs == threshold)[: count - below.size].astype(np.int32, copy=False)
    kept = np.concatenate((below, ties))
    del below, ties
    has_unit = np.zeros(m, dtype=bool)
    has_unit[kept.astype(np.int64) * m // n] = True
    empty = np.flatnonzero(~has_unit)
    starts = (empty * n + m - 1) // m
    sizes = ((empty + 1) * n + m - 1) // m - starts
    firsts = np.cumsum(sizes) - sizes  # where each empty block's units begin below
    block = np.repeat(np.arange(empty.size), sizes)
    units = starts[block] + np.arange(block.size) - firsts[block]
    by_cost = np.lexsort((units, costs[units], block))  # block by block, cheapest first

    return np.sort(np.concatenate((kept, units[by_cost[firsts]].astype(np.int32))))


def _value_at_rank(costs: Costs, rank: int) -> np.float32:
    """``np.partition(costs, rank)[rank]``. That copies ``costs``, so at a low rank this goes
    through them chunk by chunk, holding at most about twice the lowest ``rank`` of them."""
    if 2 * (rank + 1) + _PLACE_CHUNK >= costs.size:
        return np.float32(np.partition(costs, rank)[rank])

    lowest = costs[:0]
    limit = np.float32(np.inf)

    for start in range(0, costs.size, _PLACE_CHUNK):
        chunk = costs[start : start + _PLACE_CHUNK]
        lowest = np.concatenate((lowest, chunk[chunk <= limit]))

        if lowest.size > 2 * (rank + 1):
            lowest = np.partition(lowest, rank)[: rank + 1]
            limit = lowest[rank]

    return np.float32(np.partition(lowest, rank)[rank])


def embed(layout: Layout, bits: Bits, units: Units, unit_cost: Costs, message: Bits) -> Units:
    """The cheapest set of ``units`` (sorted changeable units, each with its cost) whose bits
    to flip so that the syndrome of ``bits`` becomes ``message``. Every other unit keeps its
    bit. Raises :class:`UnsolvableError` if there is none, which only a block without any of
    ``units`` can cause."""
    fixed = bits.copy()
    fixed[units] = 0
    target = message ^ syndrome(layout, fixed)
    del fixed
    steps = _steps(layout, bits, units, unit_cost)
    messages = _Messages.of(layout, target)
    states = layout.code.states
    paths, last_blocks = _forward(steps, messages, states)
    chosen = _backtrack(steps, messages, paths, last_blocks)
    flipped: Units = units[chosen.ravel()[steps.cell] != (bits[units] == 1)]

    return flipped


def _place(layout: Layout, units: Units) -> tuple[Units, Units, Bits]:
    """The segment, block and column of each unit, the column cut at the segment's end.
    Chunked: the int64 temporaries would otherwise cost several arrays of the unit count."""
    n, m = layout.unit_count, layout.message_bits
    table = layout.code.columns(-(-n // m)).astype(np.int64)
    bit_starts = layout.bit_starts()
    segment = np.empty(units.size, dtype=np.int32)
    block = np.empty(units.size, dtype=np.int32)
    value = np.empty(units.size, dtype=np.uint8)

    for start in range(0, units.size, _PLACE_CHUNK):
        part = slice(start, start + _PLACE_CHUNK)
        unit = units[part].astype(np.int64)
        unit_block = unit * m // n
        unit_segment = np.searchsorted(bit_starts, unit_block, side="right") - 1
        rows = np.minimum(bit_starts[unit_segment + 1] - unit_block, layout.code.height)
        column = table[unit - (unit_block * n + m - 1) // m]
        segment[part], block[part] = unit_segment, unit_block
        value[part] = column & ((1 << rows) - 1)

    return segment, block, value


@dataclass(frozen=True, slots=True)
class _Steps:
    """Per (segment, step) tables of the changeable units, each segment padded at its end
    with steps that change nothing."""

    valid: npt.NDArray[np.bool_]
    block: Units  # within the segment
    value: Bits
    delta: Costs
    cell: Units  # the flat (segment, step) of each unit, in input order


def _steps(layout: Layout, bits: Bits, units: Units, unit_cost: Costs) -> _Steps:
    segment, block, value = _place(layout, units)
    block -= layout.bit_starts()[segment].astype(np.int32)
    counts = np.bincount(segment, minlength=layout.segments)
    width = int(counts.max())
    first = np.concatenate(([0], np.cumsum(counts)[:-1]))
    # Units are sorted, so a unit's flat cell is its rank plus the padding of the segments
    # before it: segment * width + (rank - first[segment]).
    cell = np.arange(units.size, dtype=np.int32)
    cell += (np.arange(layout.segments, dtype=np.int32) * width - first.astype(np.int32))[segment]
    del segment
    shape = (layout.segments, width)

    valid = np.zeros(shape, dtype=bool)
    valid.ravel()[cell] = True
    block_table = np.zeros(shape, dtype=np.int32)
    block_table.ravel()[cell] = block
    del block
    value_table = np.zeros(shape, dtype=np.uint8)
    value_table.ravel()[cell] = value
    del value
    # Keeping the bit costs 0 and flipping it costs the unit's cost. The sign says which of
    # taking the column and leaving it is the flip: + for a 0 bit, - for a 1 bit. Pad steps
    # keep value and delta 0, so they change nothing.
    delta = unit_cost.astype(np.float32)
    np.negative(delta, out=delta, where=bits[units] == 1)
    delta_table = np.zeros(shape, dtype=np.float32)
    delta_table.ravel()[cell] = delta

    return _Steps(valid, block_table, value_table, delta_table, cell)


@dataclass(frozen=True, slots=True)
class _Messages:
    """The message bit of every block, one row per segment."""

    bits: Bits
    sizes: _IntArray  # blocks per segment

    @classmethod
    def of(cls, layout: Layout, message: Bits) -> "_Messages":
        bit_starts = layout.bit_starts()
        sizes = np.diff(bit_starts)
        table = np.zeros((layout.segments, int(sizes.max())), dtype=np.uint8)
        segment = np.repeat(np.arange(layout.segments), sizes)
        table[segment, np.arange(layout.message_bits) - bit_starts[segment]] = message

        return cls(table, sizes)


def _forward(steps: _Steps, messages: _Messages, states: int) -> tuple[Bits, _IntArray]:
    """The Viterbi pass: one packed choice bit per (step, segment, state), and the block of
    each segment's last unit. Works in place on its cost arrays: this is the hot loop."""
    segment_count, step_count = steps.valid.shape
    xor_index = np.arange(states)[:, None] ^ np.arange(states)[None, :]
    row_base = (np.arange(segment_count) * states)[:, None]
    cost = np.full((segment_count, states), _INF, dtype=np.float32)
    cost[:, 0] = 0
    current = np.zeros(segment_count, dtype=np.int64)
    paths = np.empty((step_count, segment_count, -(-states // 8)), dtype=np.uint8)

    for t in range(step_count):
        valid = steps.valid[:, t]
        _close_blocks(cost, current, np.where(valid, steps.block[:, t], current), messages)
        taken = cost.ravel().take(row_base + xor_index[steps.value[:, t]])
        delta = steps.delta[:, t, None]
        # Each choice adds its own cost: shifting both by a large cost instead would round
        # away the small ones in float32.
        taken += np.maximum(delta, 0)
        cost -= np.minimum(delta, 0)
        paths[t] = np.packbits(taken < cost, axis=1)
        np.minimum(cost, taken, out=cost)

        if t % _RENORMALIZE_EVERY == _RENORMALIZE_EVERY - 1:
            lowest = cost.min(axis=1, keepdims=True)
            cost -= np.where(lowest < _UNREACHABLE, lowest, 0)  # an unreachable segment stays so
            np.minimum(cost, _INF, out=cost)

    last_blocks = current.copy()
    _close_blocks(cost, current, messages.sizes, messages)

    if np.any(cost[:, 0] >= _UNREACHABLE):
        raise UnsolvableError

    return paths, last_blocks


def _close_blocks(cost: Costs, current: _IntArray, target: _IntArray, messages: _Messages) -> None:
    """Close every segment's blocks up to ``target``: a closed block's row must equal its
    message bit, then the window moves down one row."""
    half = cost.shape[1] // 2
    shift = (np.arange(half) << 1)[None, :]

    while True:
        due = np.flatnonzero(current < target)

        if due.size == 0:
            return

        bit = messages.bits[due, current[due]].astype(np.int64)
        cost[due, :half] = cost[due[:, None], shift | bit[:, None]]
        cost[due, half:] = _INF
        current[due] += 1


def _backtrack(
    steps: _Steps, messages: _Messages, paths: Bits, last_blocks: _IntArray
) -> npt.NDArray[np.bool_]:
    """The chosen bit of every (segment, step), from the final state 0 back to the start."""
    segment_count, step_count = steps.valid.shape
    rows = np.arange(segment_count)
    state = _reopen_blocks(np.zeros(segment_count, np.int64), last_blocks, messages.sizes, messages)
    chosen = np.zeros((segment_count, step_count), dtype=bool)
    start = np.zeros(segment_count, dtype=np.int64)

    for t in range(step_count - 1, -1, -1):
        valid = steps.valid[:, t]
        byte = paths[t, rows, state >> 3]
        bit = ((byte >> (7 - (state & 7))) & 1).astype(bool) & valid
        chosen[:, t] = bit
        state ^= np.where(bit, steps.value[:, t], 0)
        block = steps.block[:, t].astype(np.int64)
        previous = steps.block[:, t - 1].astype(np.int64) if t > 0 else start
        state = _reopen_blocks(state, np.where(valid, previous, block), block, messages)

    return chosen


def _reopen_blocks(
    state: _IntArray, low: _IntArray, high: _IntArray, messages: _Messages
) -> _IntArray:
    """The state before blocks ``low`` … ``high - 1`` of each segment were closed."""
    rows = np.arange(state.size)

    for j in range(int((high - low).max(initial=0))):
        block = high - 1 - j
        bit = messages.bits[rows, np.maximum(block, 0)].astype(np.int64)
        state = np.where(block >= low, (state << 1) | bit, state)

    return state


def spans_every_row(carriers: npt.NDArray[np.bool_], layout: Layout) -> bool:
    """Whether changes of ``carriers`` alone can give the code of ``layout`` any message:
    their columns, placed as :func:`syndrome` places them, span every row. Otherwise at
    least half the messages have no solution. The columns of one block span at most the
    code's height in rows, so each distinct one is tried once."""
    n, m = layout.unit_count, layout.message_bits
    units = np.flatnonzero(carriers).astype(np.int64)
    blocks = units * m // n
    starts = layout.bit_starts()
    segment_ends = starts[np.searchsorted(starts, blocks, side="right")]
    rows = np.minimum(segment_ends - blocks, layout.code.height)  # cut at the segment's end
    table = layout.code.columns(-(-n // m)).astype(np.int64)
    columns = table[units - (blocks * n + m - 1) // m] & ((1 << rows) - 1)
    pivots: dict[int, int] = {}  # by their lowest row: each new column is reduced by them

    for block, column in np.unique(np.stack((blocks, columns), axis=1), axis=0).tolist():
        vector = column << block

        while vector:
            lowest = (vector & -vector).bit_length() - 1

            if lowest not in pivots:
                pivots[lowest] = vector

                break

            vector ^= pivots[lowest]

    return len(pivots) == m
