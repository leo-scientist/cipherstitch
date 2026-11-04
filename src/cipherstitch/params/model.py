"""What a registry entry is: groups of parameters, each with a type, a default and a kind."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum, auto

from cipherstitch.catalog import MethodKey
from cipherstitch.methods import Value as Value  # the engine's: it never imports params


class Kind(Enum):
    FORMAT = auto()  # Reveal needs the same value: nothing in the file records it
    EMBEDDER = auto()  # changes how hiding works, Reveal is unaffected
    GUIDANCE = auto()  # a rule or a warning, not part of the file


class ParamType(Enum):
    ENUM = auto()
    INT = auto()
    FLOAT = auto()
    BOOL = auto()


class Cover(Enum):
    """What DCT starts from: a JPEG's coefficients, or a precover it compresses itself."""

    JPEG = auto()
    PRECOVER = auto()


class Encryption(Enum):
    ON = auto()
    OFF = auto()


class Trait(Enum):
    """What an extension's method shares with built-in ones, so that the built-in parameters
    that read it apply to the method too (docs/extensions.md#parameters)."""

    TRELLIS = auto()  # it codes with the trellis (MethodSpec.codes_with_trellis): tuning.coding
    KEYED = auto()  # Order.KEYED_IF_ENCRYPTED: an encrypted body follows frame.order


@dataclass(frozen=True, slots=True)
class Scope:
    """Where a group or a parameter applies: ``None`` fields match anything. A method with
    ``trait`` (an extension's) matches as if ``methods`` named it."""

    methods: frozenset[MethodKey] | None = None
    encryption: Encryption | None = None
    cover: Cover | None = None  # DCT only: the kind of cover
    trait: Trait | None = None

    def matches(
        self,
        method: MethodKey,
        *,
        is_encrypted: bool,
        cover: Cover | None,
        traits: frozenset[Trait] = frozenset(),
    ) -> bool:
        """``cover`` ``None`` (no cover chosen yet) matches either kind. ``traits`` are the
        method's, if it is an extension's."""
        is_named = self.methods is None or method in self.methods

        if not is_named and self.trait not in traits:
            return False

        if self.encryption is not None and (self.encryption is Encryption.ON) != is_encrypted:
            return False

        return self.cover is None or cover is None or self.cover is cover


@dataclass(frozen=True, slots=True)
class Option:
    value: Value
    label: str
    is_experimental: bool = False


@dataclass(frozen=True, slots=True)
class Param:
    id: str
    label: str
    code: str  # the constant or function it sets, shown in the editor
    type: ParamType
    default: Value
    kind: Kind
    options: tuple[Option, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str = ""
    note: str = ""  # what changing it does
    known: str = ""  # a measured result
    scopes: tuple[Scope, ...] = ()  # narrow the group's: it applies where any of them does


Check = Callable[[Mapping[str, Value]], str | None]
"""Why a group's values (by parameter id) cannot work together, or ``None`` if they can."""


@dataclass(frozen=True, slots=True)
class Group:
    id: str
    label: str
    icon: str
    note: str
    params: tuple[Param, ...]
    scope: Scope = Scope()
    check: Check | None = None


def key(group: Group, param: Param) -> str:
    """How a value is named in settings, profiles and tests, e.g. ``stc.h``."""
    return f"{group.id}.{param.id}"


def experimental(value: Value, label: str) -> Option:
    return Option(value, label, is_experimental=True)
