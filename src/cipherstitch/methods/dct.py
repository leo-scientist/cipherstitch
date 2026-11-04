"""DCT coefficients (JPEG): ±1 changes over syndrome-trellis codes
(docs/format.md#trellis-channels).

Every AC coefficient, zeros included, is a unit whose bits are its two lowest bits, and the
data is the syndrome of the unit bits: the reader needs neither the changed units nor their
number. The writer weighs each step by its J-UNIWARD cost
(:mod:`cipherstitch.methods.juniward`; Research mode also offers UERD,
:mod:`cipherstitch.methods.uerd`), less towards the precover and more away from it where its
rounding error says which way, and less towards an estimate of the picture in a JPEG.
"""

from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import assert_never

import numpy as np
import numpy.typing as npt

from cipherstitch.media import jpeg, precover
from cipherstitch.media.jpeg import EmbeddedCompressedJpeg, EmbeddedJpeg, Subsampling, Tables
from cipherstitch.methods import juniward, trellis, uerd

MAX_MAGNITUDE = 1023  # the largest AC coefficient baseline JPEG can code

_Values = npt.NDArray[np.int16]


class CostFunction(StrEnum):
    J_UNIWARD = "j-uniward"
    UERD = "uerd"
    UNIFORM = "uniform"


@dataclass(frozen=True, slots=True)
class DctRules:
    """How DCT costs and writes its changes. The precover's quality, subsampling, side
    information and ICC profile apply to a RAW, PNG or TIFF cover, the polarization, tables
    and segments to a JPEG cover. ``costs`` is a built-in cost function, or the id of a loaded
    extension's (docs/extensions.md#cost-functions)."""

    costs: CostFunction | str = CostFunction.J_UNIWARD
    sigma: float = juniward.SIGMA
    polarization: float = juniward.POLARIZATION
    max_magnitude: int = MAX_MAGNITUDE  # coefficients beyond it never change
    tables: Tables = Tables.MATCH
    keeps_segments: bool = True
    quality: int = precover.QUALITY
    subsampling: Subsampling = precover.SUBSAMPLING
    is_side_informed: bool = True
    keeps_icc: bool = True


STANDARD_DCT = DctRules()


class DctChannel(trellis.TrellisChannel[EmbeddedJpeg | EmbeddedCompressedJpeg]):
    """Every AC coefficient is a unit (docs/format.md#trellis-channels). A precover (a RAW,
    PNG or TIFF) is compressed to JPEG first."""

    _method_name = "DCT coefficients"

    def __init__(
        self,
        path: Path,
        config: trellis.Config = trellis.STANDARD,
        rules: DctRules = STANDARD_DCT,
    ) -> None:
        self._rules = rules
        self._precover = (
            precover.compress(path, rules.quality, rules.subsampling)
            if precover.is_precover(path)
            else None
        )
        cover = (
            jpeg.read_coefficients(path) if self._precover is None else self._precover.coefficients
        )
        self._cover = cover
        super().__init__(path, cover.ac.size, config)

    def _values(self) -> _Values:
        return self._cover.ac

    def _counted(self) -> npt.NDArray[np.bool_]:
        """The non-zero coefficients within the maximum magnitude: :meth:`_plane_costs` never
        changes one beyond it."""
        counted = np.not_equal(self._cover.ac, 0)

        if self._rules.max_magnitude == MAX_MAGNITUDE:
            return counted

        return counted & (np.abs(self._cover.ac) <= self._rules.max_magnitude)

    def _costs(self, rate: float) -> trellis.Costs:
        function = self._rules.costs

        if function not in CostFunction:  # an extension's id; a built-in's value as text isn't
            from cipherstitch import extension_loader  # it builds on this module

            return extension_loader.jpeg_costs(function, self._cover)

        built_in = CostFunction(function)

        match built_in:
            case CostFunction.J_UNIWARD:
                return juniward.costs(self._cover, self._rules.sigma)
            case CostFunction.UERD:
                return uerd.costs(self._cover)
            case CostFunction.UNIFORM:
                return np.ones(self._cover.ac.size, dtype=np.float32)
            case _:
                assert_never(built_in)

    def _value_range(self) -> tuple[int, int]:
        return -self._rules.max_magnitude, self._rules.max_magnitude

    def _tilts(self, indices: trellis.Indices) -> trellis.Tilts:
        """A precover's rounding errors, a JPEG's directions."""
        if self._precover is None:
            return self._directions[indices]

        if self._rules.is_side_informed:
            return self._precover.rounding[indices]

        return super()._tilts(indices)

    def _step_costs(
        self, costs: trellis.Costs, tilts: trellis.Tilts
    ) -> tuple[trellis.Costs, trellis.Costs]:
        if self._precover is None:
            return juniward.polarized(costs, tilts, self._rules.polarization)

        if self._rules.is_side_informed:
            return juniward.side_informed(costs, tilts)

        return super()._step_costs(costs, tilts)

    def _plane_costs(
        self, values: trellis.Values, costs: trellis.Costs, tilts: trellis.Tilts
    ) -> tuple[trellis.Costs, trellis.Costs]:
        """Values beyond the maximum magnitude, which a lower one leaves in a cover, never
        change."""
        flip, keep = super()._plane_costs(values, costs, tilts)

        if self._rules.max_magnitude == MAX_MAGNITUDE:  # baseline JPEG holds nothing beyond
            return flip, keep

        beyond = np.abs(values) > self._rules.max_magnitude

        return np.where(beyond, np.inf, flip), np.where(beyond, np.inf, keep)

    def _rebuilt(self, values: trellis.Values) -> EmbeddedJpeg | EmbeddedCompressedJpeg:
        ac = values.astype(np.int16, copy=False)

        if self._precover is None:
            return EmbeddedJpeg(self._path, ac, self._rules.tables, self._rules.keeps_segments)

        compressed = self._precover
        profile = compressed.icc_profile if self._rules.keeps_icc else None
        dc, components = self._cover.dc, self._cover.components

        return EmbeddedCompressedJpeg(
            ac, dc, components, compressed.size, profile, compressed.subsampling
        )

    @cached_property
    def _directions(self) -> npt.NDArray[np.int8]:
        """Cached: every region and chunk asks for its own, on every try."""
        return juniward.directions(self._cover)
