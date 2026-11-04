"""Extensions: drop-in embedding methods, ciphers, their parameters and cost functions for
Research mode.

Each module or package in this folder whose name doesn't start with ``_`` is one extension.
It exports ``EXTENSION``, an :class:`Extension`, and imports everything it builds on from
here. Research mode loads them on first need, tags every entry Experimental, and Standard
mode never imports them. See docs/extensions.md.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np
import numpy.typing as npt

from cipherstitch.catalog import CarrierKind, Requirement
from cipherstitch.ciphers.base import CounterNonceAead, Sealer
from cipherstitch.container import SEQUENTIAL_SEED, Channel
from cipherstitch.errors import (
    CapacityError,
    DecryptionError,
    NotAvailableError,
    UnsupportedFormatError,
)
from cipherstitch.media.jpeg import Coefficients, Component  # jpeglib only reads and writes
from cipherstitch.methods import Embedded, Order, Tuning
from cipherstitch.methods.slots import Bits, SlotChannel, Units
from cipherstitch.methods.stream import StreamChannel
from cipherstitch.methods.trellis import TrellisChannel
from cipherstitch.params.model import (
    Encryption,
    Group,
    Kind,
    Option,
    Param,
    ParamType,
    Scope,
    experimental,
)

__all__ = [
    "SEQUENTIAL_SEED",
    "Bits",
    "CapacityError",
    "CarrierKind",
    "Channel",
    "CipherSpec",
    "Coefficients",
    "Component",
    "CostSpec",
    "CounterNonceAead",
    "DecryptionError",
    "Domain",
    "Embedded",
    "Encryption",
    "Extension",
    "Group",
    "JpegCosts",
    "Kind",
    "MethodSpec",
    "NotAvailableError",
    "Option",
    "Order",
    "Param",
    "ParamType",
    "Requirement",
    "Scope",
    "Sealer",
    "SlotChannel",
    "SpatialCosts",
    "StreamChannel",
    "TrellisChannel",
    "Tuning",
    "Units",
    "UnsupportedFormatError",
    "experimental",
]


@dataclass(frozen=True, slots=True)
class MethodSpec:
    """An embedding method. ``wire_id`` (128–254) is in every file it writes: never change it.
    ``inputs`` are the suffixes it hides in, ``outputs`` those it writes (``None``: the
    carrier's own). ``risk`` is the Detection row's line. ``codes_with_trellis``: its channel
    is a ``TrellisChannel`` on ``tuning.coding``, so the STC parameters apply to it."""

    id: str
    wire_id: int
    name: str
    carrier: CarrierKind
    inputs: tuple[str, ...]
    open: Callable[[Path, Tuning], Channel[Embedded]]
    order: Order = Order.SEQUENTIAL  # KEYED_IF_ENCRYPTED: the channel honours seeds
    outputs: tuple[str, ...] | None = None
    summary: str = ""
    pros: tuple[str, ...] = ()
    cons: tuple[str, ...] = ()
    capacity: str = ""  # e.g. "1 bit per line"
    risk: str | None = None
    requires: Requirement | None = None
    codes_with_trellis: bool = False


@dataclass(frozen=True, slots=True)
class CipherSpec:
    """A cipher. Its key comes from ``wire_id`` (128–254), which every file it seals stores:
    never change it. ``id`` is the name the CLI and profiles use."""

    id: str
    wire_id: int
    name: str
    cipher: Sealer
    summary: str = ""
    pros: tuple[str, ...] = ()
    cons: tuple[str, ...] = ()
    requires: Requirement | None = None


class Domain(StrEnum):
    """The values a cost function weighs, and the built-in channel that steps them."""

    SPATIAL = "spatial"  # image LSB's colour values, Research's limg.cost
    JPEG = "jpeg"  # DCT's AC coefficients, Research's dct.cost


_CostArray = npt.NDArray[np.float32] | npt.NDArray[np.float64]

SpatialCosts = Callable[[npt.NDArray[np.uint8], float], _CostArray]
"""``costs(colors, rate)``. ``colors`` is the picture's red, green and blue values, rows ×
columns × 3, read-only. The result has that shape: ``result[y, x, c]`` is what a step of one,
up or down, of ``colors[y, x, c]`` costs. ``rate`` is the payload's message bits per value,
which only a cost that depends on the payload reads (MiPOD's)."""

JpegCosts = Callable[[Coefficients], _CostArray]
"""``costs(cover)``. ``cover.ac`` holds every AC coefficient, read-only: component after
component, each component's blocks row by row, each block's 63 AC terms in row-major order
(the DC term left out). The result has its shape: ``result[i]`` is what a step of one, up or
down, of ``cover.ac[i]`` costs. ``cover.components`` gives each grid's size and steps."""


@dataclass(frozen=True, slots=True)
class CostSpec:
    """A cost function for a built-in channel, offered by ``id`` as an experimental option of
    Research's ``limg.cost`` (``Domain.SPATIAL``) or ``dct.cost`` (``Domain.JPEG``). ``costs``
    is a :data:`SpatialCosts` or a :data:`JpegCosts`, returning float32 or float64 values of 0
    or more. It only steers where the changes go, so revealing never needs it."""

    id: str
    label: str  # shown in the parameter editor
    domain: Domain
    costs: SpatialCosts | JpegCosts


@dataclass(frozen=True, slots=True)
class Extension:
    """What a module adds, exported as its ``EXTENSION``. ``params`` are the Research
    parameters of its methods, which their openers read with ``tuning.extension_value``.
    ``costs`` are cost functions for image LSB and DCT."""

    methods: tuple[MethodSpec, ...] = ()
    ciphers: tuple[CipherSpec, ...] = ()
    params: tuple[Group, ...] = ()
    costs: tuple[CostSpec, ...] = ()
    summary: str = ""  # shown by ``ciphst extensions``
