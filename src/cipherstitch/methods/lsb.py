"""Least-significant-bit embedding in image pixels (R, G, B; alpha is left untouched) and
audio samples, over syndrome-trellis codes (docs/format.md#trellis-channels). A step moves
a value by one at its HILL cost (docs/methods.md#lsb-matching)."""

from abc import abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import assert_never

import numpy as np
import numpy.typing as npt

from cipherstitch.media import audio
from cipherstitch.media.audio import Audio, EmbeddedAudio
from cipherstitch.media.image import EmbeddedImage
from cipherstitch.methods import hill, mipod, spatial_costs, stc, trellis
from cipherstitch.methods.picture import COLOR_CHANNELS, PIXEL_RANGE, Picture

FLAT_RUN = 16  # equal values in a row that noise never makes: plain colour, digital silence
WET_COST = 1e10  # a flat value's, as in conseal: far above any HILL cost, finite for the search

AUDIO_ROW = 4096  # samples per scan line of an audio channel when looking for silence

_CHUNK = 1 << 20  # values per step through a scan, to bound the memory taken


class CostFunction(StrEnum):
    HILL = "hill"
    UNIFORM = "uniform"  # LSB matching: every value alike
    WOW = "wow"
    S_UNIWARD = "s-uniward"
    MIPOD = "mipod"


@dataclass(frozen=True, slots=True)
class ImageRules:
    """How image LSB weighs a cover's values and what of the cover it keeps: Standard's by
    default. None of it is part of the format. ``costs`` is a built-in cost function, or the
    id of a loaded extension's (docs/extensions.md#cost-functions)."""

    costs: CostFunction | str = CostFunction.HILL
    far_window: int = hill.FAR
    flat_run: int = FLAT_RUN
    is_wet: bool = True
    keeps_metadata: bool = True


@dataclass(frozen=True, slots=True)
class AudioRules:
    """How audio LSB finds digital silence: Standard's by default."""

    silence_row: int = AUDIO_ROW
    flat_run: int = FLAT_RUN


STANDARD_IMAGE = ImageRules()
STANDARD_AUDIO = AudioRules()


def flat_blocks(
    value_count: int,
    body_size: int,
    flat_share: float,
    format: trellis.Format = trellis.STANDARD.format,
) -> float:
    """About how many code blocks hold only flat values when LSB stores a prefix and a body
    of ``body_size`` bytes in ``value_count`` values, ``flat_share`` of them flat, over both
    bit planes, the tail's bits taken as split evenly, or over the parities alone with
    binary coding. Only there can it have to change one: a block with another value always
    lets the code avoid them."""
    regions = trellis.Regions.of(value_count, format)

    if not regions.holds_data:
        return 0.0

    layouts = [regions.lead_layout(), regions.head_layout()]

    if body_size > trellis.HEAD_BYTES:
        layouts.append(regions.tail_layout(body_size))

    if not format.is_ternary:
        return sum(_flat_blocks(layout, flat_share) for layout in layouts)

    return sum(
        _flat_blocks(plane, flat_share)
        for layout in layouts
        for plane in trellis.plane_layouts(layout, trellis.EVEN_SPLIT)
    )


def _flat_blocks(layout: stc.Layout, flat_share: float) -> float:
    """How many blocks of ``layout`` are all flat when ``flat_share`` of its units are, at
    random: ``m`` blocks of ``⌊n / m⌋`` or ``⌈n / m⌉`` units."""
    size, longer = divmod(layout.unit_count, layout.message_bits)
    shorter = layout.message_bits - longer

    return float(shorter * flat_share**size + longer * flat_share ** (size + 1))


class LsbChannel[Output](trellis.TrellisChannel[Output]):
    """One unit per value of a flat integer array; its bits are the value's two lowest bits.

    A step moves a value up or down by one, never beyond the ends of its range, at its cost
    (HILL's by default). Flat values (runs of ``flat_run`` equal ones) are wet: the capacity
    leaves them out, and a code block changes one only when all of its values are flat. It
    leaves out the values at the ends of the range too.
    """

    _method_name = "LSB"

    def __init__(
        self,
        path: Path,
        value_count: int,
        config: trellis.Config = trellis.STANDARD,
        *,
        flat_run: int = FLAT_RUN,
        is_wet: bool = True,
    ) -> None:
        super().__init__(path, value_count, config)
        self._flat_run = flat_run
        self._is_wet = is_wet

    def flat_share(self) -> float:
        """The share of values in runs of ``flat_run`` or more equal neighbours."""
        count = self.regions.count

        return np.count_nonzero(self._flat) / count if count else 0.0

    @abstractmethod
    def _flat_values(self) -> npt.NDArray[np.bool_]:
        """Which of :meth:`_values` are in runs of ``flat_run`` or more equal neighbours,
        where any change stands out: along an image's rows per colour, along an audio
        channel."""

    @abstractmethod
    def _value_costs(self, rate: float) -> trellis.Costs:
        """The cost of a step of each of :meth:`_values`, flat or not, a fresh array, for a
        payload of ``rate`` (as in :meth:`_costs`)."""

    @cached_property
    def _flat(self) -> npt.NDArray[np.bool_]:
        """Cached: the capacity and the costs each need the flat mask within one write."""
        return self._flat_values()

    def _counted(self) -> npt.NDArray[np.bool_]:
        """The values that are not flat and can step both ways: one at an end of its range can
        never flip its second bit."""
        values = self._values()
        low, high = self._value_range()

        return ~self._flat & (values > low) & (values < high)

    def _costs(self, rate: float) -> trellis.Costs:
        costs = self._value_costs(rate)

        if self._is_wet:
            costs[self._flat] = WET_COST

        return costs


def _flat_runs(lines: npt.NDArray[np.integer], run: int) -> npt.NDArray[np.bool_]:
    """Which values are in runs of at least ``run`` equal values along each line."""
    flat = np.empty(lines.shape, dtype=bool)
    step = max(_CHUNK // max(lines.shape[1], 1), 1)

    for start in range(0, len(lines), step):  # bounded temporaries: a run index per value
        chunk = lines[start : start + step]
        starts = np.ones(chunk.shape, dtype=bool)
        starts[:, 1:] = chunk[:, 1:] != chunk[:, :-1]
        runs = np.diff(np.append(np.flatnonzero(starts), chunk.size))
        flat[start : start + step] = np.repeat(runs >= run, runs).reshape(chunk.shape)

    return flat


class ImageChannel(LsbChannel[EmbeddedImage]):
    """Opening reads only the image header; pixels are decoded on the first read or write."""

    def __init__(
        self,
        path: Path,
        config: trellis.Config = trellis.STANDARD,
        rules: ImageRules = STANDARD_IMAGE,
    ) -> None:
        self._picture = Picture(path, keeps_metadata=rules.keeps_metadata)
        count = self._picture.value_count
        super().__init__(path, count, config, flat_run=rules.flat_run, is_wet=rules.is_wet)
        self._rules = rules

    @property
    def _pixels(self) -> npt.NDArray[np.uint8]:
        return self._picture.pixels

    def _values(self) -> npt.NDArray[np.uint8]:
        return self._picture.values()

    def _rebuilt(self, values: npt.NDArray[np.integer]) -> EmbeddedImage:
        return self._picture.rebuilt(values)

    def _value_range(self) -> tuple[int, int]:
        return PIXEL_RANGE

    def _value_costs(self, rate: float) -> trellis.Costs:
        colors = self._pixels[..., :COLOR_CHANNELS]
        function = self._rules.costs

        if function not in CostFunction:  # an extension's id; a built-in's value as text isn't
            from cipherstitch import extension_loader  # it builds on this module

            return extension_loader.spatial_costs(function, colors, rate).reshape(-1)

        built_in = CostFunction(function)

        match built_in:
            case CostFunction.UNIFORM:
                return np.ones(self.unit_count, dtype=np.float32)
            case CostFunction.HILL:
                costs = hill.image_costs(colors, self._rules.far_window)
            case CostFunction.WOW:
                costs = spatial_costs.wow_costs(colors)
            case CostFunction.S_UNIWARD:
                costs = spatial_costs.suniward_costs(colors)
            case CostFunction.MIPOD:
                costs = mipod.image_costs(colors, rate)
            case _:  # a new cost function must not pass for another one
                assert_never(built_in)

        return costs.reshape(-1)

    def _flat_values(self) -> npt.NDArray[np.bool_]:
        colors = self._pixels[..., :COLOR_CHANNELS]
        flat = np.empty(colors.shape, dtype=bool)
        step = max(_CHUNK // max(colors[0].size, 1), 1)

        for start in range(0, len(colors), step):  # no full-size copy of the pixels
            rows = np.moveaxis(colors[start : start + step], 2, 1)  # each colour's rows
            runs = _flat_runs(rows.reshape(-1, colors.shape[1]), self._flat_run)
            runs = runs.reshape(rows.shape)
            flat[start : start + step] = np.moveaxis(runs, 1, 2)

        return flat.reshape(-1)


class AudioChannel(LsbChannel[EmbeddedAudio]):
    """Opening reads only the header; samples are decoded on the first read or write."""

    def __init__(
        self,
        path: Path,
        config: trellis.Config = trellis.STANDARD,
        rules: AudioRules = STANDARD_AUDIO,
    ) -> None:
        details = audio.info(path)
        count = details.frames * details.channels
        super().__init__(path, count, config, flat_run=rules.flat_run, is_wet=True)
        self._row = rules.silence_row

    @cached_property
    def _audio(self) -> Audio:
        return audio.read(self._path)

    def _values(self) -> npt.NDArray[np.int32]:
        return self._audio.samples.reshape(-1)

    def _rebuilt(self, values: npt.NDArray[np.integer]) -> EmbeddedAudio:
        samples = values.astype(np.int32, copy=False).reshape(self._audio.samples.shape)

        return EmbeddedAudio(self._audio.with_samples(samples))

    def _value_range(self) -> tuple[int, int]:
        return audio.sample_range(self._audio.info.depth)

    def _value_costs(self, rate: float) -> trellis.Costs:
        return hill.audio_costs(self._audio.samples).reshape(-1)

    def _flat_values(self) -> npt.NDArray[np.bool_]:
        """Runs are counted within stretches of ``silence_row`` samples."""
        samples, row, run = self._audio.samples, self._row, self._flat_run
        whole = len(samples) - len(samples) % row
        step = max(_CHUNK // row, 1) * row
        flat = np.empty(samples.shape, dtype=bool)

        for channel in range(samples.shape[1]):  # no full-size copy of the samples
            for start in range(0, whole, step):
                stop = min(start + step, whole)
                rows = samples[start:stop, channel].reshape(-1, row)
                flat[start:stop, channel] = _flat_runs(rows, run).reshape(-1)

            flat[whole:, channel] = _flat_runs(samples[whole:, channel].reshape(1, -1), run)[0]

        return flat.reshape(-1)
