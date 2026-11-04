"""A lead and keyed streams over syndrome-trellis codes (docs/format.md#trellis-channels), the
layout DCT and LSB share.

Unit ``u`` is value ``a · u mod N`` (:func:`golden_multiplier`), and its bits are the value's
two lowest bits. The data is the syndrome of the unit bits in both bit planes, or in the
parities alone with Research's binary coding, so the reader needs neither the changed units
nor their number. The writer weighs each step by its cost and picks a cheap set of steps; a
channel says what a step up or down of a value costs.
"""

import os
import struct
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from functools import cached_property
from pathlib import Path

import numpy as np
import numpy.typing as npt

from cipherstitch.container import SEQUENTIAL_SEED
from cipherstitch.errors import CapacityError
from cipherstitch.methods import stc, ternary
from cipherstitch.methods.feistel import ROUNDS, UNKEYED_SEED, Permutation, golden_multiplier
from cipherstitch.methods.slots import to_bits

LEAD_BYTES = 16  # holds the salt
HEAD_BYTES = 64  # the largest sealed header (AES-CBC + HMAC, Fernet)
LEAD_UNITS = 16384
HEAD_UNITS = 65536
TAIL_UNITS_PER_BIT = 512
MAX_FILL_PERCENT = 80
# The trellis steps over the cheapest units, this many per message bit, and leaves the rest:
# a few times the changes it makes keeps it near the cost of searching every unit.
KEPT_PER_BIT = 10

Indices = npt.NDArray[np.int64]
Values = npt.NDArray[np.integer]
Costs = stc.Costs
Ups = npt.NDArray[np.bool_]
Tilts = npt.NDArray[np.int8]
Steps = tuple[Indices, Ups]  # the values to step, and which of them step up
TiltsAt = Callable[[Indices], Tilts]
# The costs of the steps that flip and keep the second bit of some values, given what a step
# of each costs either way and their tilts.
PlaneCosts = Callable[[Values, Costs, Tilts], tuple[Costs, Costs]]

# A tail's split: the share of its bits in the second bits, in 1/SPLIT_SCALE.
SPLIT_SCALE = 1 << 16
EVEN_SPLIT = SPLIT_SCALE // 2  # the lead's and the head's
# Added to a tail's split on each try: a full LSB cover's parities can leave a run of blocks
# without enough units (likelier the larger the cover), and fewer parity bits may solve it.
_SPLIT_SHIFTS = tuple(percent * SPLIT_SCALE // 100 for percent in range(0, 35, 5))  # 0 … 30%

_FIELDS = struct.Struct(">IH")  # a stream's length and its tail's split
_HEAD_SIZE = _FIELDS.size + HEAD_BYTES
_HEAD_BITS = 8 * _HEAD_SIZE
_CHUNK = 1 << 17
_LAMBDA_SAMPLE = 1 << 16  # units the first layer's λ is solved on: it only shapes its costs
_COST_CAP = 1e30  # stands in for an infinite cost where λ multiplies it
_EXPONENT_CAP = 128.0  # e^(−128) is 0 in float32


@dataclass(frozen=True, slots=True)
class Format:
    """What the reader must know of the layout besides the carrier: Standard's by default,
    Research may change it (docs/format.md#trellis-channels)."""

    code: stc.Code = stc.STANDARD
    lead_units: int = LEAD_UNITS
    head_units: int = HEAD_UNITS
    tail_units_per_bit: int = TAIL_UNITS_PER_BIT
    rounds: int = ROUNDS  # of the streams' keyed order
    is_spread: bool = True  # units numbered by golden_multiplier, else in sequence
    is_ternary: bool = True  # codes over both bit planes, else over the parities alone


@dataclass(frozen=True, slots=True)
class Writer:
    """How the writer picks its steps, which Reveal never needs."""

    kept_per_bit: int = KEPT_PER_BIT
    fill_percent: int = MAX_FILL_PERCENT
    is_split_ideal: bool = True  # else a tail's bits are split evenly between the planes
    can_retry: bool = True  # with fewer parity bits when a code has no solution

    @property
    def split_shifts(self) -> tuple[int, ...]:
        return _SPLIT_SHIFTS if self.can_retry else _SPLIT_SHIFTS[:1]


@dataclass(frozen=True, slots=True)
class Config:
    format: Format = Format()
    writer: Writer = Writer()


STANDARD = Config()


class _InSequence:
    """Stream position ``i`` at unit ``R + i``: Research's sequential order."""

    def at(self, positions: npt.NDArray[np.integer]) -> npt.NDArray[np.int32]:
        return positions.astype(np.int32)


StreamOrder = Permutation | _InSequence


@dataclass(frozen=True, slots=True)
class Regions:
    """Where the lead and the streams live among ``count`` units."""

    count: int
    multiplier: int
    lead: int
    head: int
    format: Format = STANDARD.format

    @classmethod
    def of(cls, count: int, format: Format = STANDARD.format) -> "Regions":
        lead = min(format.lead_units, count // 8)
        head = min(format.head_units, (count - lead) // 4)
        multiplier = golden_multiplier(count) if format.is_spread else 1

        return cls(count, multiplier, lead, head, format)

    @property
    def holds_data(self) -> bool:
        return self.head >= _HEAD_BITS

    @property
    def tail(self) -> int:
        return self.count - self.lead - self.head

    @property
    def stream_limit(self) -> int:
        """The most data bytes a stream can hold."""
        return HEAD_BYTES + self.tail // 8

    def indices(self, units: npt.NDArray[np.integer]) -> Indices:
        """The values of ``units``."""
        return units.astype(np.int64) * self.multiplier % self.count

    def lead_indices(self) -> Indices:
        return self.indices(np.arange(self.lead))

    def lead_layout(self) -> stc.Layout:
        return self._layout(self.lead, 8 * LEAD_BYTES)

    def head_layout(self) -> stc.Layout:
        return self._layout(self.head, _HEAD_BITS)

    def tail_layout(self, length: int) -> stc.Layout:
        """The code of a stream holding ``length`` data bytes, more than :data:`HEAD_BYTES`."""
        bits = 8 * (length - HEAD_BYTES)

        return self._layout(min(self.tail, self.format.tail_units_per_bit * bits), bits)

    def code_split(self, split: int) -> int | None:
        """``split``, or None with binary coding, whose codes are over the parities alone:
        its streams store a split of 0, which readers ignore."""
        return split if self.format.is_ternary else None

    def order(self, seed: bytes) -> StreamOrder:
        """The streams' order over the units after the lead: keyed by ``seed``, or in sequence
        for :data:`SEQUENTIAL_SEED`."""
        if seed == SEQUENTIAL_SEED:
            return _InSequence()

        return Permutation(seed, self.count - self.lead, self.format.rounds)

    def _layout(self, units: int, bits: int) -> stc.Layout:
        code = self.format.code

        return stc.Layout(units, bits, code.segments(units), code)


class Units:
    """The units of one value array, and the lead and streams they hold."""

    def __init__(self, values: Values, regions: Regions) -> None:
        self.values = values
        self.regions = regions
        self._heads: dict[bytes, tuple[StreamOrder, bytes]] = {}

    def head_indices(self, order: StreamOrder) -> Indices:
        return self.regions.indices(self.regions.lead + order.at(np.arange(self.regions.head)))

    def tail_chunks(self, order: StreamOrder, span: int) -> Iterator[tuple[slice, Indices]]:
        """The values at the first ``span`` tail positions, chunk by chunk so the order is
        never stored."""
        first = self.regions.head

        for start in range(0, span, _CHUNK):
            stop = min(start + _CHUNK, span)
            units = self.regions.lead + order.at(np.arange(first + start, first + stop))
            yield slice(start, stop), self.regions.indices(units)

    def tail_values(self, order: StreamOrder, span: int) -> Values:
        values = np.empty(span, dtype=self.values.dtype)

        for part, indices in self.tail_chunks(order, span):
            values[part] = self.values[indices]

        return values

    def tail_indices(self, order: StreamOrder, positions: Indices) -> Indices:
        return self.regions.indices(self.regions.lead + order.at(self.regions.head + positions))

    @cached_property
    def lead(self) -> bytes:
        values = self.values[self.regions.lead_indices()]

        return _decode(values, self.regions.lead_layout(), self.regions.code_split(EVEN_SPLIT))

    def stream(self, seed: bytes, size: int) -> bytes:
        """Up to ``size`` data bytes of the stream in the order of ``seed``; none if its length
        is more than a stream can hold (a cover, or another key)."""
        order, head = self._head(seed)
        length, split = _FIELDS.unpack_from(head)

        if length > self.regions.stream_limit:
            return b""

        wanted = min(size, length)
        data = head[_FIELDS.size :]

        if wanted > HEAD_BYTES:
            layout = self.regions.tail_layout(length)
            values = self.tail_values(order, layout.unit_count)
            data += _decode(values, layout, self.regions.code_split(split))

        return data[:wanted]

    def _head(self, seed: bytes) -> tuple[StreamOrder, bytes]:
        """Cached: reveal reads every candidate's header with each cipher."""
        if seed not in self._heads:
            order = self.regions.order(seed)
            values = self.values[self.head_indices(order)]
            split = self.regions.code_split(EVEN_SPLIT)
            self._heads[seed] = (order, _decode(values, self.regions.head_layout(), split))

        return self._heads[seed]


class TrellisChannel[Output](ABC):
    """A :class:`cipherstitch.container.Channel`. The lead (units ``0 … R − 1``) holds the
    prefix; each body stream, ``u32 length ‖ u16 split ‖ data``, lives in units ``R …`` in
    the order of its seed. Header reads decode only the lead and a stream's head. Writing
    reads the result back."""

    _method_name: str

    def __init__(self, path: Path, value_count: int, config: Config = STANDARD) -> None:
        self._path = path
        self._writer = config.writer
        self.regions = Regions.of(value_count, config.format)

    @property
    def unit_count(self) -> int:
        return self.regions.count

    @cached_property
    def element_count(self) -> int:
        """The values the capacity counts: the N of the square-root law, by which the advised
        payload grows (risks.advised_size)."""
        return int(np.count_nonzero(self._counted()))

    def capacity(self, prefix_size: int) -> int:
        if not self.regions.holds_data or not self._holds_lead_and_head:
            return 0

        fill = self._writer.fill_percent
        bits = min(self._stream_counted * fill // 100, _HEAD_BITS + self.regions.tail)

        return max(LEAD_BYTES - prefix_size, 0) + max(bits // 8 - _FIELDS.size, 0)

    def stored_limit(self, prefix_size: int) -> int:
        """Depends only on the number of values, which embedding never changes."""
        if not self.regions.holds_data:
            return 0

        return max(LEAD_BYTES - prefix_size, 0) + self.regions.stream_limit

    def read(self, prefix_size: int, size: int, seed: bytes | None) -> bytes:
        if not self.regions.holds_data:
            return b""

        if seed is not None:
            return self._units.stream(seed, size)

        stop = prefix_size + size
        data = self._units.lead

        if stop > LEAD_BYTES:
            data += self._units.stream(UNKEYED_SEED, stop - LEAD_BYTES)

        return data[prefix_size:stop]

    def write(self, prefix: bytes, body: bytes, seed: bytes | None) -> Output:
        available = self.capacity(len(prefix))

        # Without room for the lead and a head (a capacity of 0) nothing fits, the prefix neither.
        if len(prefix) > LEAD_BYTES or not self.capacity(0) or len(body) > available:
            raise CapacityError(
                f"Payload is {len(body)} bytes, this carrier holds at most {available}"
            )

        if seed is None:
            data = prefix + body
            lead, stream, seed = data[:LEAD_BYTES], data[LEAD_BYTES:], UNKEYED_SEED
            has_stream = bool(stream)
        else:
            lead, stream, has_stream = prefix, body, True

        # Binary coding has no split to move, so it tries once.
        shifts = self._writer.split_shifts if self.regions.format.is_ternary else (0,)

        for shift in shifts:
            try:
                steps = self._steps(lead, stream if has_stream else None, seed, shift)
            except (_UnspelledError, stc.UnsolvableError):
                continue

            return self._rebuilt(_stepped(self._units.values, *steps))

        raise CapacityError(
            f"{self._path.name} can't hold this payload reliably with {self._method_name}"
        )

    def _steps(self, lead: bytes, stream: bytes | None, seed: bytes, shift: int) -> Steps:
        """All the steps that store ``lead`` and ``stream``, a tail's split moved by ``shift``."""
        # In a list, so that the tail can drop it before its search.
        costs = [self._costs(self._rate(lead, stream))]
        steps = []

        layers = _Layers(self._tilts, self._plane_costs, self._writer.kept_per_bit)

        if lead:
            steps.append(_embed_lead(self._units, costs[0], lead, layers))

        if stream is not None:
            split = _Split(shift, is_ideal=self._writer.is_split_ideal)
            steps += _embed_stream(self._units, costs, seed, stream, layers, split)

        return np.concatenate([step[0] for step in steps]), np.concatenate(
            [step[1] for step in steps]
        )

    def _rate(self, lead: bytes, stream: bytes | None) -> float:
        """The message bits per unit of the codes that store ``lead`` and ``stream``. Costs
        that depend on the payload (MiPOD's) spread it over the carrier at this rate: the codes
        change only their own units, and a short stream's tail spans few of them."""
        layouts = [self.regions.lead_layout()] if lead else []

        if stream is not None:
            layouts.append(self.regions.head_layout())

            if len(stream) > HEAD_BYTES:
                layouts.append(self.regions.tail_layout(len(stream)))

        bits = sum(layout.message_bits for layout in layouts)

        return bits / sum(layout.unit_count for layout in layouts)

    @abstractmethod
    def _values(self) -> Values:
        """The carrier's values, one per unit (read-only)."""

    @abstractmethod
    def _counted(self) -> npt.NDArray[np.bool_]:
        """Which of :meth:`_values` the capacity counts."""

    @abstractmethod
    def _costs(self, rate: float) -> Costs:
        """What a step of each of :meth:`_values` costs either way, a fresh array, when the
        codes store ``rate`` message bits per unit (:meth:`_rate`). Only costs that depend on
        the payload read it."""

    @abstractmethod
    def _value_range(self) -> tuple[int, int]:
        """The lowest and highest value a unit may take."""

    @abstractmethod
    def _rebuilt(self, values: Values) -> Output:
        """The carrier with ``values`` in place of :meth:`_values`."""

    def _tilts(self, indices: Indices) -> Tilts:
        """What sets a step up of the values at ``indices`` apart from a step down, one byte
        each, for :meth:`_step_costs`. Empty if nothing does: a tail keeps them all, and a byte
        per value is memory an LSB cover has no use for."""
        return np.empty(0, dtype=np.int8)

    def _step_costs(self, costs: Costs, tilts: Tilts) -> tuple[Costs, Costs]:
        """What a step up and a step down cost, given ``costs``, what a step costs either way,
        and their :meth:`_tilts`."""
        return costs, costs

    def _plane_costs(self, values: Values, costs: Costs, tilts: Tilts) -> tuple[Costs, Costs]:
        """What the step that flips the second bit of each of ``values`` costs, and the step
        that keeps it. In two's complement a step down from an even value or up from an odd
        one flips it. A step beyond the ends of the range costs infinity."""
        low, high = self._value_range()
        up, down = self._step_costs(costs, tilts)
        up = np.where(values < high, up, np.inf)
        down = np.where(values > low, down, np.inf)
        odd = (values & 1).astype(bool)

        return np.where(odd, up, down), np.where(odd, down, up)

    @cached_property
    def _units(self) -> Units:
        return Units(self._values(), self.regions)

    @cached_property
    def _stream_counted(self) -> int:
        """Counted values among the stream units."""
        counted = self._counted()
        lead = counted[self.regions.lead_indices()]

        return int(np.count_nonzero(counted)) - int(np.count_nonzero(lead))

    @cached_property
    def _holds_lead_and_head(self) -> bool:
        """Whether the lead and a stream's head, which every write codes whatever the payload,
        can take any message on the values that can take a bit (:meth:`_carriers`). Values at
        an end of the range, a white band for one, can leave too few, or none where some rows
        of a code need them, and then every write fails, an empty one too. A cover whose
        values can all take a bit always can. A sequential order takes the head right after
        the lead, a public one from all over the stream. So does a keyed one, whose key isn't
        known here: the public head stands for it, and the stream must hold a carrier per bit
        of the head's code in the head's share of it (with ternary coding, two per bit of the
        second bits, below which a short write failed on nearly every try)."""
        regions, units = self.regions, self._units
        lead = units.values[regions.lead_indices()]
        heads = (units.head_indices(_InSequence()), units.head_indices(regions.order(UNKEYED_SEED)))
        lead_layout, head_layout = regions.lead_layout(), regions.head_layout()
        chunks = (units.values[i : i + _CHUNK] for i in range(0, units.values.size, _CHUNK))
        everywhere = sum(int(np.count_nonzero(self._carriers(chunk))) for chunk in chunks)
        stream = everywhere - int(np.count_nonzero(self._carriers(lead)))
        # In integers: the head's share of the stream's carriers against its code's bits.
        is_dense_enough = stream * regions.head >= head_layout.message_bits * (
            regions.count - regions.lead
        )

        return (
            is_dense_enough
            and self._spans(lead, lead_layout)
            and all(self._spans(units.values[indices], head_layout) for indices in heads)
        )

    def _spans(self, values: Values, layout: stc.Layout) -> bool:
        """Whether the code of ``layout`` over ``values`` can take any message. With ternary
        coding only the code of the second bits can fall short: any value takes a parity."""
        is_ternary = self.regions.format.is_ternary
        scarce = plane_layouts(layout, EVEN_SPLIT)[0] if is_ternary else layout

        return stc.spans_every_row(self._carriers(values), scarce)

    def _carriers(self, values: Values) -> npt.NDArray[np.bool_]:
        """Which of ``values`` can take a bit: a value in the range has a step that flips its
        parity, but with ternary coding the second bits are the scarce plane, and the step
        that flips one (up from an odd value, down from an even one) must stay in the range
        too (:meth:`_plane_costs`)."""
        low, high = self._value_range()
        inside = (values >= low) & (values <= high)

        if not self.regions.format.is_ternary:
            return inside

        odd = (values & 1).astype(bool)

        return inside & np.where(odd, values < high, values > low)


class _UnspelledError(Exception):
    """The changes the search found do not spell the message."""


@dataclass(frozen=True, slots=True)
class _Layers:
    """What the two layers of a code need from the channel and the writer."""

    tilts_at: TiltsAt
    plane_costs: PlaneCosts
    kept_per_bit: int


@dataclass(frozen=True, slots=True)
class _Split:
    """How a tail's bits are split: the writer's rule, then moved by ``shift``."""

    shift: int
    is_ideal: bool


def plane_layouts(layout: stc.Layout, split: int) -> tuple[stc.Layout, stc.Layout]:
    """The codes of the second bits and of the parities, where ``layout`` is the code of the
    whole message: its bits shared out by ``split``, over the same units. Neither plane gets
    all of them or none, whatever a head that is not one says."""
    bits = layout.message_bits
    first = min(max((bits * split + EVEN_SPLIT) // SPLIT_SCALE, 1), bits - 1)

    return replace(layout, message_bits=first), replace(layout, message_bits=bits - first)


def _embed_lead(units: Units, costs: Costs, lead: bytes, layers: _Layers) -> Steps:
    indices = units.regions.lead_indices()
    padded = lead + os.urandom(LEAD_BYTES - len(lead))
    layout = units.regions.lead_layout()
    inputs = (units.values[indices], costs[indices], layers.tilts_at(indices))
    split = units.regions.code_split(EVEN_SPLIT)
    positions, ups = _solve(*inputs, layers, layout, padded, split)

    return indices[positions], ups


def _embed_stream(
    units: Units, costs: list[Costs], seed: bytes, data: bytes, layers: _Layers, split: _Split
) -> list[Steps]:
    """The steps for ``u32 length ‖ u16 split ‖ data`` in the order of ``seed``. The head
    holds the tail's split, so the tail's inputs are gathered first: they take the costs out
    of ``costs``, which frees them. Binary coding stores a split of 0."""
    regions = units.regions
    order = regions.order(seed)
    indices = units.head_indices(order)
    head_inputs = (units.values[indices], costs[0][indices], layers.tilts_at(indices))
    tail_inputs = []
    head_split = regions.code_split(EVEN_SPLIT)
    tail_split = head_split

    if len(data) > HEAD_BYTES:
        layout = regions.tail_layout(len(data))
        span = layout.unit_count
        tail_inputs.append(_tail_inputs(units, costs.pop(), order, span, layers.tilts_at))

        if tail_split is not None:  # ternary
            tail_split = _tail_split(tail_inputs[0], layout.message_bits, layers.plane_costs, split)

    stream = _FIELDS.pack(len(data), 0 if tail_split is None else tail_split) + data
    head = stream[:_HEAD_SIZE] + os.urandom(max(_HEAD_SIZE - len(stream), 0))
    positions, ups = _solve(*head_inputs, layers, regions.head_layout(), head, head_split)
    steps = [(indices[positions], ups)]

    if tail_inputs:
        # Passed straight on, so that _solve can free them before the trellis search.
        message = stream[_HEAD_SIZE:]
        positions, ups = _solve(*tail_inputs.pop(), layers, layout, message, tail_split)
        steps.append((units.tail_indices(order, positions), ups))

    return steps


def _tail_split(
    inputs: tuple[Values, Costs, Tilts], bits: int, plane_costs: PlaneCosts, split: _Split
) -> int:
    """The split of a ternary tail's ``bits`` over the values of ``inputs`` by the writer's
    rule, then moved by its shift."""
    tail_split = EVEN_SPLIT

    if split.is_ideal:
        sample = _lambda_sample(*inputs, plane_costs)
        tail_split = _ideal_split(sample, bits, inputs[0].size)

    return min(tail_split + split.shift, SPLIT_SCALE - 1)


def _tail_inputs(
    units: Units, costs: Costs, order: StreamOrder, span: int, tilts_at: TiltsAt
) -> tuple[Values, Costs, Tilts]:
    """The values at the first ``span`` tail positions, what a step of each costs either way,
    and their tilts: all the two layers need, without the order and the map of ``costs``."""
    values = np.empty(span, dtype=units.values.dtype)
    gathered = np.empty(span, dtype=np.float32)
    tilts = []

    for part, indices in units.tail_chunks(order, span):
        values[part] = units.values[indices]
        gathered[part] = costs[indices]
        tilts.append(tilts_at(indices))

    del costs

    return values, gathered, np.concatenate(tilts)


def _solve(
    values: Values,
    costs: Costs,
    tilts: Tilts,
    layers: _Layers,
    layout: stc.Layout,
    message: bytes,
    split: int | None,
) -> tuple[Indices, Ups]:
    """The positions in ``values`` to step, and which of them step up, so that their two
    lowest bit planes spell ``message`` (docs/methods.md#trellis-coding). ``costs`` and
    ``tilts`` are as in :data:`PlaneCosts`, ``costs`` overwritten. The second bits take the
    message's first bits, as many as ``split`` gives them, then the parities the rest, where
    the values the first layer stepped keep their new parity. With binary coding (``split``
    None) the parities take them all. Checked: the stepped values must spell the message."""
    wanted = to_bits(message)

    if split is None:
        units, unit_costs, cheaper = _parity_units(values, costs, tilts, layers, layout)
        del costs, tilts
        parities = _parities(values)
        kept = stc.embed(layout, parities, units, unit_costs, wanted)
        positions = kept.astype(np.int64)
        odd = (values[kept] & 1).astype(bool)
        ups = _parity_ups(odd, cheaper[np.searchsorted(units, kept)])
    else:
        second, parity = plane_layouts(layout, split)
        flipped = _first_layer(values, costs, tilts, layers, second, wanted, layout.message_bits)
        odd = (values & 1).astype(bool)
        parities = odd.astype(np.uint8)
        parities[flipped] ^= 1

        for part, _flip, keep in _plane_chunks(values, costs, tilts, layers.plane_costs):
            costs[part] = keep

        costs[flipped] = np.inf  # stepped already
        units, unit_costs = _changeable(parity, costs, layers.kept_per_bit)
        del costs, tilts
        kept = stc.embed(parity, parities, units, unit_costs, wanted[second.message_bits :])
        positions = np.concatenate((flipped, kept)).astype(np.int64)
        ups = np.concatenate((odd[flipped], ~odd[kept]))

    del units, unit_costs, parities

    if not np.array_equal(_syndromes(_stepped(values, positions, ups), layout, split), wanted):
        raise _UnspelledError

    return positions, ups


def _first_layer(
    values: Values,
    costs: Costs,
    tilts: Tilts,
    layers: _Layers,
    second: stc.Layout,
    wanted: stc.Bits,
    bits: int,
) -> stc.Units:
    """A ternary code's first layer: the positions whose second bits it flips so that they
    spell the first bits of ``wanted``, as many as ``second`` holds, at
    :func:`_first_layer_costs` for a message of ``bits``."""
    first_costs = _first_layer_costs(values, costs, tilts, layers.plane_costs, bits)
    units, unit_costs = _changeable(second, first_costs, layers.kept_per_bit)
    del first_costs

    return stc.embed(second, _second_bits(values), units, unit_costs, wanted[: second.message_bits])


def _parity_units(
    values: Values, costs: Costs, tilts: Tilts, layers: _Layers, layout: stc.Layout
) -> tuple[stc.Units, Costs, Tilts]:
    """The units a binary code's search steps over, their costs, and which of their steps is
    the cheaper (:func:`_cheaper_steps`, which overwrites ``costs``)."""
    cheaper = _cheaper_steps(values, costs, tilts, layers.plane_costs)
    units, unit_costs = _changeable(layout, costs, layers.kept_per_bit)

    return units, unit_costs, cheaper[units]


def _cheaper_steps(values: Values, costs: Costs, tilts: Tilts, plane_costs: PlaneCosts) -> Tilts:
    """Overwrites ``costs`` with the cost of each value's cheaper step, either of which flips
    its parity, and gives which step that is: 1 the one that flips the second bit, −1 the one
    that keeps it, 0 for a tie. A step beyond the range costs infinity, so the other is the
    cheaper there."""
    cheaper = np.empty(values.size, dtype=np.int8)

    for part, flip, keep in _plane_chunks(values, costs, tilts, plane_costs):
        cheaper[part] = np.less(flip, keep).view(np.int8) - np.greater(flip, keep).view(np.int8)
        costs[part] = np.minimum(flip, keep)

    return cheaper


def _parity_ups(odd: npt.NDArray[np.bool_], cheaper: Tilts) -> Ups:
    """Which of the values a binary code steps go up: those whose cheaper step is up, and at
    random where both cost the same, as in LSB matching. A fixed choice would step every
    even value down and every odd one up under symmetric costs. The step that flips the
    second bit is up from an odd value."""
    flips = np.where(cheaper == 0, _coin_flips(cheaper.size), cheaper > 0)

    return np.equal(flips, odd)


def _coin_flips(count: int) -> npt.NDArray[np.bool_]:
    random = np.frombuffer(os.urandom(-(-count // 8)), dtype=np.uint8)

    return np.unpackbits(random, count=count).astype(bool)


def _plane_chunks(
    values: Values, costs: Costs, tilts: Tilts, plane_costs: PlaneCosts
) -> Iterator[tuple[slice, Costs, Costs]]:
    """:data:`PlaneCosts` chunk by chunk: a tail's arrays are the largest the writer holds."""
    for start in range(0, values.size, _CHUNK):
        part = slice(start, start + _CHUNK)
        yield part, *plane_costs(values[part], costs[part], tilts[part])


def _first_layer_costs(
    values: Values, costs: Costs, tilts: Tilts, plane_costs: PlaneCosts, bits: int
) -> Costs:
    """What flipping each value's second bit costs the first layer: its step's cost, and the
    value's share in the second layer, which could step it the other way (Filler, Judas and
    Fridrich 2011, the multi-layered construction): ``flip + ln(1 + e^(−λ keep)) / λ``."""
    sample = _lambda_sample(values, costs, tilts, plane_costs)
    lam = ternary.ternary_lambda(sample, bits, values.size)
    cap = np.float32(_EXPONENT_CAP / lam)
    first = np.empty(values.size, dtype=np.float32)

    for part, flip, keep in _plane_chunks(values, costs, tilts, plane_costs):
        share = np.minimum(keep, cap)
        share *= -lam
        np.exp(share, out=share)
        np.log1p(share, out=share)
        share /= lam
        first[part] = flip + share

    return first


def _ideal_split(sample: npt.NDArray[np.float64], bits: int, span: int) -> int:
    """The split of ``bits`` over ``span`` values an ideal ternary code would make: the second
    bits' share of its entropy, ``H(p_flip)`` of the
    ``H(p_flip) + (1 − p_flip) H(p_keep / (1 − p_flip))`` each value carries. With halves
    instead, the parities take more changes than the second bits, and the steps that keep a
    value in its pair, 2k and 2k + 1, outnumber the others, as LSB replacement's do
    (docs/detectability.md#popular-tools-and-classical-attacks)."""
    weights = np.exp(sample * -ternary.ternary_lambda(sample, bits, span))
    p_flip, p_keep = weights / (1 + weights.sum(axis=0))
    first = float(ternary.binary_entropy(p_flip).sum())
    second = float(((1 - p_flip) * ternary.binary_entropy(p_keep / (1 - p_flip))).sum())
    split = round(SPLIT_SCALE * first / (first + second))

    return min(max(split, 1), SPLIT_SCALE - 1)


def _lambda_sample(
    values: Values, costs: Costs, tilts: Tilts, plane_costs: PlaneCosts
) -> npt.NDArray[np.float64]:
    """The flip and keep costs, one row each, of about :data:`_LAMBDA_SAMPLE` values."""
    every = max(values.size // _LAMBDA_SAMPLE, 1)
    flip, keep = plane_costs(values[::every], costs[::every], tilts[::every])

    return np.minimum(np.stack((flip, keep)).astype(np.float64), _COST_CAP)


def _changeable(layout: stc.Layout, costs: Costs, kept_per_bit: int) -> tuple[stc.Units, Costs]:
    """The units a layer's search steps over and their costs, never one whose cost is
    infinite. All it needs of ``costs``, which it can then do without."""
    units = stc.prune(layout, costs, kept_per_bit)
    units = units[np.isfinite(costs[units])]

    return units, costs[units]


def _stepped(values: Values, indices: Indices, ups: Ups) -> Values:
    """A copy of ``values`` with each value at ``indices`` a step up or down."""
    result = values.copy()
    chosen = result[indices]
    chosen += ups  # in-place adds of the masks: ufuncs with where= are 3x slower
    chosen -= ~ups
    result[indices] = chosen

    return result


def _decode(values: Values, layout: stc.Layout, split: int | None) -> bytes:
    return np.packbits(_syndromes(values, layout, split)).tobytes()


def _syndromes(values: Values, layout: stc.Layout, split: int | None) -> stc.Bits:
    """The message of a code over ``values``: its first bits in their second bits, as many
    as ``split`` gives them, the rest in their parities, or all of them in their parities
    with binary coding (``split`` None) (docs/format.md#trellis-channels)."""
    if split is None:
        return stc.syndrome(layout, _parities(values))

    second, parity = plane_layouts(layout, split)
    first = stc.syndrome(second, _second_bits(values))

    return np.concatenate((first, stc.syndrome(parity, _parities(values))))


def _parities(values: Values) -> stc.Bits:
    return (values & 1).astype(np.uint8)


def _second_bits(values: Values) -> stc.Bits:
    """⌊v / 2⌋ mod 2, the second lowest bit in two's complement."""
    return ((values >> 1) & 1).astype(np.uint8)
