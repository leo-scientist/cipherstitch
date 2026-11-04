"""Registry of the implemented embedding methods: the carriers each one can use, what it
writes and how it orders the body. :func:`get` returns ``None`` for a planned method, and
looks an id that isn't a built-in's up in the extensions (docs/architecture.md#extensions)."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path, PurePath
from typing import Protocol

from cipherstitch import catalog
from cipherstitch.catalog import CarrierKind, MethodId, MethodKey
from cipherstitch.container import Candidate, Channel, UnitOrder
from cipherstitch.errors import UnsupportedFormatError
from cipherstitch.media import image, jpeg, precover
from cipherstitch.methods import (
    dct,
    echo,
    lsb,
    object_stream,
    phase,
    trellis,
    zero_width,
)
from cipherstitch.methods.dct import DctChannel, DctRules
from cipherstitch.methods.echo import EchoChannel, EchoRules
from cipherstitch.methods.feistel import ROUNDS
from cipherstitch.methods.lsb import AudioChannel, AudioRules, ImageChannel, ImageRules
from cipherstitch.methods.lsb_replacement import LsbReplacementChannel
from cipherstitch.methods.object_stream import ObjectRules
from cipherstitch.methods.phase import PhaseChannel, PhaseRules
from cipherstitch.methods.whitespace import STANDARD_WHITESPACE, WhitespaceChannel, WhitespaceRules
from cipherstitch.methods.zero_width import ZeroWidthChannel, ZeroWidthRules


class Order(Enum):
    SEQUENTIAL = auto()  # always in sequence
    # Keyed with a password (Research's frame.order may pick another), the channel's own order
    # without one.
    KEYED_IF_ENCRYPTED = auto()


class Embedded(Protocol):
    """A carrier with a payload stored in it, ready to be written."""

    def save(self, path: Path) -> None: ...


Value = bool | int | float | str
"""A parameter's value (``params.model`` builds on it: the engine never imports ``params``)."""


@dataclass(frozen=True, slots=True)
class Tuning:
    """What Research mode changes in the channels, Standard's values by default. Hashable:
    the service caches capacities by it."""

    coding: trellis.Config = trellis.STANDARD
    image: ImageRules = lsb.STANDARD_IMAGE
    audio: AudioRules = lsb.STANDARD_AUDIO
    rounds: int = ROUNDS  # zero-width's keyed order (the trellis channels' is in ``coding``)
    dct: DctRules = dct.STANDARD_DCT
    phase: PhaseRules = phase.STANDARD_PHASE
    echo: EchoRules = echo.STANDARD_ECHO
    document: ObjectRules = object_stream.STANDARD_OBJECT
    whitespace: WhitespaceRules = STANDARD_WHITESPACE
    text: ZeroWidthRules = zero_width.STANDARD_ZERO_WIDTH
    # Every parameter of the extension whose method opens the carrier, by key, sorted, its
    # default included. None for a built-in method.
    extension_values: tuple[tuple[str, Value], ...] = ()

    def extension_value(self, key: str) -> Value:
        """The value of an extension's parameter, e.g. ``lines.mark``: Research's setting, or
        the parameter's default (docs/extensions.md#parameters)."""
        for name, value in self.extension_values:
            if name == key:
                return value

        known = ", ".join(name for name, _value in self.extension_values) or "none"

        raise LookupError(f"No extension parameter {key} here. The method's extension has {known}")


STANDARD = Tuning()


@dataclass(frozen=True, slots=True)
class Embedding:
    method: MethodKey
    order: Order
    open: Callable[[Path, Tuning], Channel[Embedded]]
    inputs: tuple[str, ...]  # carrier suffixes it can hide in
    outputs: tuple[str, ...] | None = None  # suffixes it writes; ``None``: the carrier's own

    def body_order(self, chosen: UnitOrder, *, encrypted: bool) -> UnitOrder:
        """``chosen`` (keyed by default) for an encrypted body of a method that orders it, the
        channel's own order otherwise."""
        is_ordered = encrypted and self.order is Order.KEYED_IF_ENCRYPTED

        return chosen if is_ordered else UnitOrder.PUBLIC

    def candidates(self) -> tuple[Candidate, ...]:
        """Orders a carrier written by this method may use, keyed first. Only Research hides
        in the sequential one."""
        keyed = Candidate(self.method, UnitOrder.KEYED)
        public = Candidate(self.method, UnitOrder.PUBLIC)
        sequential = Candidate(self.method, UnitOrder.SEQUENTIAL)

        return {
            Order.SEQUENTIAL: (public,),
            Order.KEYED_IF_ENCRYPTED: (keyed, public, sequential),
        }[self.order]

    def output_suffixes(self, carrier: PurePath | None) -> tuple[str, ...]:
        if self.outputs is not None:
            return self.outputs

        return self.inputs if carrier is None else (carrier.suffix.lower(),)

    def can_hold(self, path: PurePath) -> bool:
        """Whether a file like ``path`` may carry data written by this method."""
        return path.suffix.lower() in (self.outputs or self.inputs)


# A camera RAW is only ever a DCT precover (docs/methods.md#from-a-raw-png-or-tiff).
_IMAGES = tuple(
    s for s in catalog.carrier(CarrierKind.IMAGE).extensions if s not in catalog.RAW_EXTENSIONS
)
_AUDIO = catalog.carrier(CarrierKind.AUDIO).extensions
_DOCUMENTS = catalog.carrier(CarrierKind.DOCUMENT).extensions
_TEXTS = catalog.carrier(CarrierKind.TEXT).extensions
_IMAGE_OUTPUTS = tuple(image.OUTPUT_FORMATS)
_JPEGS = tuple(jpeg.OUTPUT_FORMATS)
_DCT_INPUTS = (*_JPEGS, *precover.SUFFIXES)
_DOCX = (".docx",)


# One-argument calls (the detectability study's scripts) open with Standard's tuning.
def _image_lsb(path: Path, tuning: Tuning = STANDARD) -> Channel[Embedded]:
    return ImageChannel(path, tuning.coding, tuning.image)


def _dct(path: Path, tuning: Tuning = STANDARD) -> Channel[Embedded]:
    return DctChannel(path, tuning.coding, tuning.dct)


def _audio_lsb(path: Path, tuning: Tuning = STANDARD) -> Channel[Embedded]:
    return AudioChannel(path, tuning.coding, tuning.audio)


def _phase(path: Path, tuning: Tuning = STANDARD) -> Channel[Embedded]:
    return PhaseChannel(path, tuning.phase)


def _echo(path: Path, tuning: Tuning = STANDARD) -> Channel[Embedded]:
    return EchoChannel(path, tuning.echo)


def _object(path: Path, tuning: Tuning = STANDARD) -> Channel[Embedded]:
    return object_stream.open_channel(path, tuning.document)


def _whitespace(path: Path, tuning: Tuning = STANDARD) -> Channel[Embedded]:
    return WhitespaceChannel(path, tuning.whitespace)


def _zero_width(path: Path, tuning: Tuning = STANDARD) -> Channel[Embedded]:
    return ZeroWidthChannel(path, tuning.rounds, tuning.text)


def _lsb_replacement(path: Path, tuning: Tuning = STANDARD) -> Channel[Embedded]:
    return LsbReplacementChannel(path, keeps_metadata=tuning.image.keeps_metadata)


_EMBEDDINGS = {
    embedding.method: embedding
    for embedding in (
        Embedding(MethodId.LSB, Order.KEYED_IF_ENCRYPTED, _image_lsb, _IMAGES, _IMAGE_OUTPUTS),
        Embedding(MethodId.DCT, Order.KEYED_IF_ENCRYPTED, _dct, _DCT_INPUTS, _JPEGS),
        Embedding(MethodId.AUDIO_LSB, Order.KEYED_IF_ENCRYPTED, _audio_lsb, _AUDIO),
        Embedding(MethodId.PHASE_CODING, Order.SEQUENTIAL, _phase, _AUDIO),
        Embedding(MethodId.ECHO_HIDING, Order.SEQUENTIAL, _echo, _AUDIO),
        Embedding(MethodId.OBJECT_STREAM, Order.SEQUENTIAL, _object, _DOCUMENTS),
        Embedding(MethodId.WHITESPACE, Order.SEQUENTIAL, _whitespace, _DOCX),
        Embedding(MethodId.ZERO_WIDTH, Order.KEYED_IF_ENCRYPTED, _zero_width, _TEXTS),
        Embedding(
            MethodId.LSB_REPLACEMENT, Order.SEQUENTIAL, _lsb_replacement, _IMAGES, _IMAGE_OUTPUTS
        ),
    )
}


def get(method_id: MethodKey) -> Embedding | None:
    embedding = _EMBEDDINGS.get(method_id)

    if embedding is not None or catalog.is_builtin_method(method_id):
        return embedding

    from cipherstitch import extension_loader  # it builds on this module

    loaded = extension_loader.find_method(method_id)

    return None if loaded is None else loaded.embedding


def reveal_suffixes() -> tuple[str, ...]:
    """Every suffix a file written by some implemented built-in method can have."""
    suffixes = (
        s for embedding in _EMBEDDINGS.values() for s in embedding.outputs or embedding.inputs
    )

    return tuple(dict.fromkeys(suffixes))


Opener = Callable[[Path, Tuning], Channel[Embedded]]


def for_reveal(
    path: PurePath, method: MethodKey | None = None
) -> list[tuple[Opener, tuple[Candidate, ...]]]:
    """The channel types that may hold data in ``path``, each with its method orders: the
    built-in methods', and ``method``'s too when it is an extension's (Research mode's reveal
    names one method, Standard's never runs an extension).

    Candidates sharing a channel type (e.g. a method's keyed and unkeyed orders) form one
    group, so the carrier is read once per group and its salt derives keys once.
    """
    groups: dict[Opener, list[Candidate]] = {}

    for embedding in (*_EMBEDDINGS.values(), *_named_extension(method)):
        if embedding.can_hold(path):
            groups.setdefault(embedding.open, []).extend(embedding.candidates())

    if not groups:
        raise UnsupportedFormatError(
            f"{path.suffix or path.name or path} files can't carry a payload"
        )

    return [(opener, tuple(candidates)) for opener, candidates in groups.items()]


def _named_extension(method: MethodKey | None) -> tuple[Embedding, ...]:
    if method is None or catalog.is_builtin_method(method):
        return ()

    embedding = get(method)

    return () if embedding is None else (embedding,)
