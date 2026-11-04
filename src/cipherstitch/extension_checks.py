"""The rules an extension must follow to load (docs/architecture.md#extensions). Each check
lists every problem it finds, one line per problem naming the entry, so that one look at a
failed extension shows all of them."""

import math
import os
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import TypeGuard

from cipherstitch import catalog
from cipherstitch.catalog import CarrierKind, Requirement
from cipherstitch.ciphers.base import Sealer
from cipherstitch.errors import DecryptionError, InvalidParameterError
from cipherstitch.extension_guards import described, value_kind
from cipherstitch.extensions import CipherSpec, CostSpec, Domain, Extension, MethodSpec
from cipherstitch.hidden_text import has_hidden
from cipherstitch.methods import Order, dct, lsb
from cipherstitch.params import values
from cipherstitch.params.groups import GROUP_IDS
from cipherstitch.params.model import Encryption, Group, Kind, Option, Param, ParamType, Scope

WIRE_IDS = range(128, 255)  # 0 and 255 are reserved, 1–127 are the built-ins'
KEY_SIZES = range(16, 65)
MAX_ID_LENGTH = 32
MAX_NAME_LENGTH = 40
BUILTIN_SUFFIXES = {
    suffix: carrier.kind for carrier in catalog.CARRIERS for suffix in carrier.extensions
}
ICONS = Path(__file__).parent / "gui" / "resources" / "icons"  # the app's, read without Qt
# The options limg.cost and dct.cost already have.
BUILTIN_COSTS = {
    Domain.SPATIAL: frozenset(function.value for function in lsb.CostFunction),
    Domain.JPEG: frozenset(function.value for function in dct.CostFunction),
}
_ID = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
_ID_RULE = (
    "the id must be lower-case letters and digits, in words joined by hyphens, at most "
    f"{MAX_ID_LENGTH} characters"
)
# Each half of a parameter key, as profiles read it: no hyphens.
_KEY_PART = re.compile(r"[a-z][a-z0-9]{0,31}")
_KEY_PART_RULE = "the id must be lower-case letters and digits, starting with a letter, at most 32"
_ICON = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_ICON_RULE = "the icon must name one of the app's, such as flask (gui/resources/icons)"
# The editor's spin boxes: Qt's int for whole numbers, and the decimals a double can step.
WHOLE_NUMBERS = range(-(2**31), 2**31)
_SUFFIX = re.compile(r"\.[a-z0-9]{1,10}")
_PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# An ASCII identifier: str.isidentifier accepts format characters such as U+200D.
_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RESERVED_IDS = frozenset({"none", "order"})  # the plain frame's cipher, the order's key info
# The self-test's input sizes: around the block sizes, and a few longer ones.
_TEST_SIZES = (*range(65), 255, 4096)
_TAMPER_SIZE = 64
_LEAK_WINDOW = 16  # no run of this many bytes may show in the output (or in another's)


def problems(extension: Extension) -> tuple[str, ...]:
    return tuple(_extension_problems(extension))


def new_suffixes(spec: MethodSpec) -> tuple[str, ...]:
    """The suffixes of ``spec`` that no built-in carrier has: they join its carrier kind."""
    return tuple(s for s in _suffixes(spec) if s not in BUILTIN_SUFFIXES)


def _extension_problems(extension: Extension) -> Iterator[str]:
    yield from _text_problems("EXTENSION", "summary", extension.summary)

    if not _is_tuple_of(extension.methods, MethodSpec):
        yield "EXTENSION.methods must be a tuple of MethodSpec"
    else:
        for spec in extension.methods:
            yield from _method_problems(spec)

        yield from _repeats("method id", (spec.id for spec in extension.methods))
        yield from _repeats("method wire id", (spec.wire_id for spec in extension.methods))
        yield from _kind_clashes(extension.methods)

    if not _is_tuple_of(extension.ciphers, CipherSpec):
        yield "EXTENSION.ciphers must be a tuple of CipherSpec"
    else:
        for cipher in extension.ciphers:
            yield from _cipher_problems(cipher)

        yield from _repeats("cipher id", (spec.id for spec in extension.ciphers))
        yield from _repeats("cipher wire id", (spec.wire_id for spec in extension.ciphers))

    yield from _params_problems(extension)
    yield from _costs_problems(extension)


def _method_problems(spec: MethodSpec) -> Iterator[str]:
    entry = f"method {spec.id!r}"
    yield from _entry_problems(entry, spec.id, spec.wire_id, catalog.is_builtin_method)
    yield from _texts_problems(entry, spec.name, spec.summary, spec.pros, spec.cons)
    yield from _text_problems(entry, "capacity", spec.capacity)

    if spec.risk is not None:
        yield from _text_problems(entry, "risk", spec.risk)

    yield from _carrier_problems(entry, spec)

    if not isinstance(spec.order, Order):
        yield f"{entry}: the order must be an Order"

    if not callable(spec.open):
        yield f"{entry}: open must be callable"

    if not isinstance(spec.codes_with_trellis, bool):
        yield f"{entry}: codes_with_trellis must be a bool"

    yield from _requirement_problems(entry, spec.requires)


def _cipher_problems(spec: CipherSpec) -> Iterator[str]:
    entry = f"cipher {spec.id!r}"
    yield from _entry_problems(entry, spec.id, spec.wire_id, catalog.is_builtin_cipher)
    yield from _texts_problems(entry, spec.name, spec.summary, spec.pros, spec.cons)
    yield from _requirement_problems(entry, spec.requires)
    # A cipher whose dependency is missing loads unavailable, as a built-in: it can't run yet.
    requirement = spec.requires
    is_missing = (
        isinstance(requirement, Requirement)
        and _is_module(requirement.module)
        and not requirement.is_installed
    )
    problem = _key_size_problem(spec.cipher) if is_missing else self_test(spec.cipher)

    if problem is not None:
        yield f"{entry}: {problem}"


def _entry_problems(
    entry: str, entry_id: object, wire_id: object, is_builtin: Callable[[str], bool]
) -> Iterator[str]:
    if not isinstance(entry_id, str) or not _is_id(entry_id):
        yield f"{entry}: {_ID_RULE}"
    elif entry_id in _RESERVED_IDS or is_builtin(entry_id):
        yield f"{entry}: the id {entry_id} is taken by CipherStitch"

    if not _is_int(wire_id) or wire_id not in WIRE_IDS:
        yield f"{entry}: the wire id must be an int in {WIRE_IDS[0]}–{WIRE_IDS[-1]}"


def _is_id(text: str) -> bool:
    return len(text) <= MAX_ID_LENGTH and _ID.fullmatch(text) is not None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _texts_problems(
    entry: str, name: object, summary: object, pros: object, cons: object
) -> Iterator[str]:
    yield from _name_problems(entry, "name", name)
    yield from _text_problems(entry, "summary", summary)

    for field, items in (("pros", pros), ("cons", cons)):
        if not _is_tuple_of(items, str):
            yield f"{entry}: the {field} must be a tuple of str"
        elif any(map(has_hidden, items)):
            yield f"{entry}: the {field} have a control or format character"


def _name_problems(entry: str, field: str, name: object) -> Iterator[str]:
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_LENGTH:
        yield f"{entry}: the {field} must be 1–{MAX_NAME_LENGTH} characters"
    else:
        yield from _text_problems(entry, field, name)


def _text_problems(entry: str, field: str, text: object) -> Iterator[str]:
    if not isinstance(text, str):
        yield f"{entry}: the {field} must be a str"
    elif has_hidden(text):
        yield f"{entry}: the {field} has a control or format character"
    elif text and not text.strip():
        yield f"{entry}: the {field} is only blanks"


def _is_tuple_of[T](items: object, kind: type[T]) -> TypeGuard[tuple[T, ...]]:
    return isinstance(items, tuple) and all(isinstance(item, kind) for item in items)


def _carrier_problems(entry: str, spec: MethodSpec) -> Iterator[str]:
    if not isinstance(spec.carrier, CarrierKind):
        yield f"{entry}: the carrier must be a CarrierKind"

    if not _is_suffixes(spec.inputs):
        yield f"{entry}: the inputs must be a non-empty tuple of suffixes such as .txt"

    if spec.outputs is not None and not _is_suffixes(spec.outputs):
        yield f"{entry}: the outputs must be None or a non-empty tuple of suffixes such as .txt"

    for suffix in _suffixes(spec):
        kind = BUILTIN_SUFFIXES.get(suffix, spec.carrier)

        if isinstance(spec.carrier, CarrierKind) and kind != spec.carrier:
            yield f"{entry}: {suffix} belongs to {kind} carriers, not {spec.carrier} ones"


def _is_suffixes(suffixes: object) -> bool:
    return (
        _is_tuple_of(suffixes, str)
        and bool(suffixes)
        and all(_SUFFIX.fullmatch(suffix) for suffix in suffixes)
    )


def _suffixes(spec: MethodSpec) -> tuple[str, ...]:
    """The well-formed suffixes ``spec`` reads and writes."""
    every = (*_items(spec.inputs), *_items(spec.outputs))

    return tuple(dict.fromkeys(s for s in every if isinstance(s, str) and _SUFFIX.fullmatch(s)))


def _items(value: object) -> tuple[object, ...]:
    return value if isinstance(value, tuple) else ()


def _kind_clashes(specs: tuple[MethodSpec, ...]) -> Iterator[str]:
    kinds: dict[str, set[CarrierKind]] = {}

    for spec in specs:
        if isinstance(spec.carrier, CarrierKind):
            for suffix in new_suffixes(spec):
                kinds.setdefault(suffix, set()).add(spec.carrier)

    for suffix, found in kinds.items():
        if len(found) > 1:
            names = ", ".join(sorted(found))
            yield f"the suffix {suffix} joins more than one carrier kind: {names}"


def _repeats(what: str, values: Iterable[object]) -> Iterator[str]:
    counts = Counter(value for value in values if isinstance(value, (str, int)))

    for value, count in counts.items():
        if count > 1:
            yield f"the {what} {value} is declared {count} times"


def _params_problems(extension: Extension) -> Iterator[str]:
    """The parameter groups: the rules of the built-in ones, scoped to the extension's own
    methods, with ids that can't clash with a built-in's (docs/extensions.md#parameters)."""
    if not _is_tuple_of(extension.params, Group):
        yield "EXTENSION.params must be a tuple of Group"

        return

    specs = _items(extension.methods)
    own = frozenset(s.id for s in specs if isinstance(s, MethodSpec) and isinstance(s.id, str))

    for group in extension.params:
        yield from _group_problems(group, own)

    yield from _repeats("parameter group id", (group.id for group in extension.params))


def _group_problems(group: Group, own: frozenset[str]) -> Iterator[str]:
    entry = f"parameter group {group.id!r}"

    if not isinstance(group.id, str) or not _KEY_PART.fullmatch(group.id):
        yield f"{entry}: {_KEY_PART_RULE} characters"
    elif group.id in GROUP_IDS:
        yield f"{entry}: the id {group.id} is taken by CipherStitch"

    yield from _name_problems(entry, "label", group.label)
    yield from _text_problems(entry, "note", group.note)

    is_icon = isinstance(group.icon, str) and _ICON.fullmatch(group.icon) is not None

    if not is_icon or not (ICONS / f"{group.icon}.svg").is_file():
        yield f"{entry}: {_ICON_RULE}"

    yield from _scope_problems(entry, group.scope, own, is_required=True)

    if group.check is not None and not callable(group.check):
        yield f"{entry}: check must be callable or None"

    if not _is_tuple_of(group.params, Param) or not group.params:
        yield f"{entry}: the params must be a non-empty tuple of Param"

        return

    found = [problem for param in group.params for problem in _param_problems(group, param, own)]
    yield from found
    yield from _repeats("parameter", (f"{group.id}.{param.id}" for param in group.params))

    if not found:
        yield from _defaults_problems(entry, group)


def _param_problems(group: Group, param: Param, own: frozenset[str]) -> Iterator[str]:
    name = f"{group.id}.{param.id}"
    entry = f"parameter {name!r}"

    if not isinstance(param.id, str) or not _KEY_PART.fullmatch(param.id):
        yield f"{entry}: {_KEY_PART_RULE} characters"

    yield from _name_problems(entry, "label", param.label)

    for field in ("code", "unit", "note", "known"):
        yield from _text_problems(entry, field, getattr(param, field))

    if not isinstance(param.kind, Kind):
        yield f"{entry}: the kind must be a Kind"

    if not isinstance(param.type, ParamType):
        yield f"{entry}: the type must be a ParamType"

        return

    shape = list(_shape_problems(entry, param))
    yield from shape

    if not shape:
        yield from _default_problems(entry, name, param)

    if not isinstance(param.scopes, tuple):
        yield f"{entry}: the scopes must be a tuple of Scope"
    else:
        for scope in param.scopes:
            yield from _scope_problems(entry, scope, own, is_required=False)


def _shape_problems(entry: str, param: Param) -> Iterator[str]:
    """The options of a choice, or the range and step of a number."""
    if param.type is ParamType.ENUM:
        yield from _options_problems(entry, param.options)
    elif param.options != ():
        yield f"{entry}: only a choice (ParamType.ENUM) has options"

    if param.type not in (ParamType.INT, ParamType.FLOAT):
        return

    low, high, step = param.minimum, param.maximum, param.step

    if low is None or high is None or step is None or not all(map(_is_number, (low, high, step))):
        yield f"{entry}: a number needs a finite minimum, maximum and step"
    elif low > high or step <= 0:
        yield f"{entry}: the minimum must not pass the maximum, and the step must be above 0"
    elif param.type is ParamType.INT and not all(map(_is_whole, (low, high, step))):
        first, last = WHOLE_NUMBERS[0], WHOLE_NUMBERS[-1]
        yield (
            f"{entry}: a whole number's minimum, maximum and step must be whole numbers from "
            f"{first} to {last}, as the editor holds them"
        )
    elif param.type is ParamType.FLOAT and values.decimals(step) > values.MAX_DECIMALS:
        yield f"{entry}: the step needs more than {values.MAX_DECIMALS} decimals"


def _options_problems(entry: str, options: object) -> Iterator[str]:
    if not _is_tuple_of(options, Option) or not options:
        yield f"{entry}: a choice needs a non-empty tuple of Option"

        return

    if any(isinstance(option.value, bool) for option in options):
        yield f"{entry}: an option's value can't be a bool: use ParamType.BOOL for a switch"
    elif not all(isinstance(option.value, int | float | str) for option in options):
        yield f"{entry}: an option's value must be an int, float or str"
    elif len({option.value for option in options}) < len(options):
        yield f"{entry}: two options have the same value"

    for option in options:
        yield from _name_problems(entry, "option label", option.label)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _is_whole(value: float) -> bool:
    return float(value).is_integer() and int(value) in WHOLE_NUMBERS


def _default_problems(entry: str, name: str, param: Param) -> Iterator[str]:
    """The default must be a value the settings allow, stored as they would store it."""
    try:
        canonical = values.checked(param, name, param.default)
    except InvalidParameterError as exc:
        yield f"{entry}: the default is refused: {exc}"

        return

    if canonical != param.default or type(canonical) is not type(param.default):
        yield f"{entry}: the default must be stored as {canonical!r}"


def _defaults_problems(entry: str, group: Group) -> Iterator[str]:
    """The group's check must accept its defaults, which is what Standard values mean."""
    check = group.check

    if check is None or not callable(check):
        return

    try:
        problem = check({param.id: param.default for param in group.params})
    except Exception as exc:
        yield f"{entry}: the check raised {described(exc)}"

        return

    if problem is not None:
        yield f"{entry}: the check refuses the defaults: {problem}"


def _scope_problems(
    entry: str, scope: object, own: frozenset[str], *, is_required: bool
) -> Iterator[str]:
    """A scope names only the extension's methods: a group must name one or more of them, a
    parameter may leave them to its group's."""
    if not isinstance(scope, Scope):
        yield f"{entry}: a scope must be a Scope"

        return

    methods = scope.methods

    if methods is None and is_required:
        yield f"{entry}: the scope must name one or more of the extension's methods"
    elif methods is not None and (not _is_frozenset_of_str(methods) or not methods):
        yield f"{entry}: the scope's methods must be a non-empty frozenset of method ids"
    elif methods is not None:
        for method in sorted(methods - own):
            yield f"{entry}: the scope names {method}, which isn't one of the extension's methods"

    if scope.encryption is not None and not isinstance(scope.encryption, Encryption):
        yield f"{entry}: the scope's encryption must be an Encryption or None"

    if scope.cover is not None or scope.trait is not None:
        yield f"{entry}: the scope may name only methods and encryption"


def _is_frozenset_of_str(items: object) -> TypeGuard[frozenset[str]]:
    return isinstance(items, frozenset) and all(isinstance(item, str) for item in items)


def _costs_problems(extension: Extension) -> Iterator[str]:
    """The cost functions: ids unique within their domain, where no built-in option has them
    (docs/extensions.md#cost-functions)."""
    if not _is_tuple_of(extension.costs, CostSpec):
        yield "EXTENSION.costs must be a tuple of CostSpec"

        return

    for spec in extension.costs:
        yield from _cost_problems(spec)

    for domain in Domain:
        ids = (spec.id for spec in extension.costs if spec.domain is domain)
        yield from _repeats(f"{domain} cost function id", ids)


def _cost_problems(spec: CostSpec) -> Iterator[str]:
    entry = f"cost function {spec.id!r}"
    is_domain = isinstance(spec.domain, Domain)

    if not isinstance(spec.id, str) or not _is_id(spec.id):
        yield f"{entry}: {_ID_RULE}"
    elif is_domain and spec.id in BUILTIN_COSTS[spec.domain]:
        yield f"{entry}: the id {spec.id} is taken by CipherStitch"

    yield from _name_problems(entry, "label", spec.label)

    if not is_domain:
        yield f"{entry}: the domain must be a Domain"

    if not callable(spec.costs):
        yield f"{entry}: costs must be callable"


def _requirement_problems(entry: str, requirement: object) -> Iterator[str]:
    if requirement is None:
        return

    if not isinstance(requirement, Requirement):
        yield f"{entry}: requires must be a Requirement"

        return

    if not _is_module(requirement.module):
        yield f"{entry}: requires must name a top-level module, such as scipy"

    names = [name for name in (requirement.extra, requirement.package) if name is not None]

    if len(names) > 1:
        yield f"{entry}: requires names an extra or a package, not both"

    if any(not isinstance(name, str) or not _PACKAGE.fullmatch(name) for name in names):
        yield f"{entry}: requires names an extra or a package pip can't install"


def _is_module(name: object) -> bool:
    """A top-level module's name: ``find_spec`` would import the parent of a dotted one."""
    return isinstance(name, str) and _MODULE.fullmatch(name) is not None


def _key_size_problem(cipher: Sealer) -> str | None:
    try:
        key_size = cipher.key_size
    except Exception as exc:
        return f"key_size raised {described(exc)}"

    if not _is_int(key_size) or key_size not in KEY_SIZES:
        return f"key_size must be an int in {KEY_SIZES[0]}–{KEY_SIZES[-1]}, not {key_size!r}"

    return None


def self_test(cipher: Sealer) -> str | None:
    """The first way ``cipher`` breaks the contract, or ``None``: sizes that are exact and
    never decrease, round trips, a flipped bit, a wrong key and a cut block refused with
    :class:`DecryptionError` alone, no plaintext in the output, and other output for the
    other counter. It needs no key derivation, so it takes microseconds."""
    problem = _key_size_problem(cipher)

    if problem is not None:
        return problem

    try:
        key = os.urandom(cipher.key_size)

        return (
            _round_trip_problem(cipher, key)
            or _tamper_problem(cipher, key)
            or _counter_problem(cipher, key)
        )
    except Exception as exc:
        return f"the self-test raised {described(exc)}"


def _round_trip_problem(cipher: Sealer, key: bytes) -> str | None:
    least = 0

    for size in _TEST_SIZES:
        expected = cipher.sealed_size(size)

        if not _is_int(expected) or expected < max(size, least):
            least = max(size, least)

            return f"sealed_size({size}) returned {expected!r}, not an int of at least {least}"

        least = expected
        data = os.urandom(size)
        counter = size % 2  # the header's and the record's counters
        sealed = cipher.seal(key, counter, data)

        if not isinstance(sealed, bytes) or len(sealed) != expected:
            return f"seal of {size} bytes returned {value_kind(sealed)}, not {expected} bytes"

        if cipher.unseal(key, counter, sealed) != data:
            return f"unseal didn't give back the {size} bytes seal took"

    return None


def _tamper_problem(cipher: Sealer, key: bytes) -> str | None:
    data = os.urandom(_TAMPER_SIZE)
    sealed = cipher.seal(key, 1, data)
    last = len(sealed) - 1
    tampered = {
        "a flipped bit": [_flipped(sealed, at) for at in (0, last // 2, last)],
        "a cut block": [sealed[:-1]],
    }

    for case, blocks in tampered.items():
        for block in blocks:
            problem = _refusal_problem(cipher, key, block)

            if problem is not None:
                return f"unseal of {case} {problem}"

    wrong_key = bytes(byte ^ 0xFF for byte in key)
    problem = _refusal_problem(cipher, wrong_key, sealed)

    if problem is not None:
        return f"unseal with a wrong key {problem}"

    if _shares_a_run(data, sealed):
        return "seal leaves the plaintext readable in its output"

    return None


def _counter_problem(cipher: Sealer, key: bytes) -> str | None:
    """The header (counter 0) and the record (counter 1) share a key. A cipher may draw a
    random IV instead of binding the counter (Fernet does), but it must not seal both alike."""
    data = os.urandom(_TAMPER_SIZE)

    if _shares_a_run(cipher.seal(key, 0, data), cipher.seal(key, 1, data)):
        return (
            "seal gives the same output for counters 0 and 1, so the header and the record "
            "would share a keystream"
        )

    return None


def _shares_a_run(data: bytes, block: bytes) -> bool:
    """Whether ``block`` holds any run of :data:`_LEAK_WINDOW` bytes of ``data``."""
    windows = (data[at : at + _LEAK_WINDOW] for at in range(len(data) - _LEAK_WINDOW + 1))

    return any(window in block for window in windows)


def _flipped(block: bytes, at: int) -> bytes:
    return block[:at] + bytes([block[at] ^ 1]) + block[at + 1 :]


def _refusal_problem(cipher: Sealer, key: bytes, block: bytes) -> str | None:
    try:
        cipher.unseal(key, 1, block)
    except DecryptionError:
        return None
    except Exception as exc:
        return f"raised {described(exc)} instead of DecryptionError"

    return "returned data instead of raising DecryptionError"
