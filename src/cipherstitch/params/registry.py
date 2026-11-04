"""Lookups over the parameter groups: the built-ins', then the loaded extensions'
(docs/architecture.md#parameters). Only a key in a group no built-in has, a method that
isn't a built-in's, a cost function that isn't a built-in's, or the editor's groups for a
method with a cost function load the extensions, and only Research mode reads settings,
so Standard mode never gets there."""

from collections.abc import Mapping
from dataclasses import replace

from cipherstitch import catalog, extension_loader
from cipherstitch.catalog import MethodKey
from cipherstitch.errors import InvalidParameterError
from cipherstitch.extensions import Domain
from cipherstitch.methods import Order
from cipherstitch.params.groups import GROUP_IDS
from cipherstitch.params.groups import GROUPS as BUILTIN_GROUPS
from cipherstitch.params.model import Cover, Group, Param, Scope, Trait, experimental, key

COST_KEYS: Mapping[Domain, str] = {Domain.SPATIAL: "limg.cost", Domain.JPEG: "dct.cost"}
"""The parameters whose options the loaded extensions' cost functions extend
(docs/extensions.md#cost-functions)."""
_COST_DOMAINS = {name: domain for domain, name in COST_KEYS.items()}


def _index(groups: tuple[Group, ...]) -> dict[str, tuple[Group, Param]]:
    index: dict[str, tuple[Group, Param]] = {}

    for group in groups:
        for param in group.params:
            name = key(group, param)

            if name in index:
                raise ValueError(f"Parameter {name} is declared twice")

            index[name] = (group, param)

    return index


BUILTIN_PARAMS: Mapping[str, tuple[Group, Param]] = _index(BUILTIN_GROUPS)


def lookup(name: str) -> tuple[Group, Param]:
    found = BUILTIN_PARAMS.get(name)

    # No extension's group takes a built-in id, so a key in a built-in group that no
    # built-in has (a newer CipherStitch's) is unknown without loading them.
    if found is None and name.partition(".")[0] not in GROUP_IDS:
        found = _index(extension_groups()).get(name)

    if found is None:
        raise InvalidParameterError(f"Unknown parameter {name}")

    return found


def knows(name: str) -> bool:
    try:
        lookup(name)
    except InvalidParameterError:
        return False

    return True


def param_for(name: str, value: object) -> Param:
    """The parameter ``name`` as a setting of ``value`` needs it: with the loaded extensions'
    cost functions after its own options when it takes them and ``value`` is none of its
    own. Only then does it load the extensions."""
    _group, param = lookup(name)

    if any(option.value == value for option in param.options):
        return param

    return _with_costs(name, param)


def is_unknown_cost(name: str, value: object) -> bool:
    """Whether the text ``value`` names a cost function for ``name`` that neither CipherStitch
    nor a loaded extension has (this loads them)."""
    if name not in _COST_DOMAINS or not isinstance(value, str):
        return False

    return all(option.value != value for option in param_for(name, value).options)


def groups() -> tuple[Group, ...]:
    """Every group: the built-ins', then the loaded extensions' (this loads them)."""
    return (*BUILTIN_GROUPS, *extension_groups())


def extension_groups() -> tuple[Group, ...]:
    """The loaded extensions' groups, in their order (this loads them)."""
    return tuple(
        group for extension in extension_loader.load().extensions for group in extension.params
    )


def extension_keys(method: MethodKey) -> tuple[str, ...]:
    """The keys of every parameter of the extension that adds ``method``, sorted, which its
    opener can read (``Tuning.extension_value``). None for a built-in method."""
    if catalog.is_builtin_method(method):
        return ()

    owner = next(
        (
            extension
            for extension in extension_loader.load().extensions
            if any(loaded.entry.id == method for loaded in extension.methods)
        ),
        None,
    )
    params = () if owner is None else owner.params

    return tuple(sorted(key(group, param) for group in params for param in group.params))


def groups_for(
    method: MethodKey, *, is_encrypted: bool, cover: Cover | None = None
) -> tuple[Group, ...]:
    """The groups that hiding with ``method`` reads, each with only the parameters it reads.
    ``cover`` is DCT's kind of cover; ``None`` (no cover chosen yet) shows both kinds. An
    extension's method also gets the built-in groups its traits share, and a cost function
    parameter the loaded extensions' cost functions (this loads them)."""
    is_builtin = catalog.is_builtin_method(method)
    traits = frozenset() if is_builtin else _traits(method)

    def applies(scope: Scope) -> bool:
        return scope.matches(method, is_encrypted=is_encrypted, cover=cover, traits=traits)

    shown = []

    for group in BUILTIN_GROUPS if is_builtin else groups():
        if not applies(group.scope):
            continue

        params = tuple(
            _with_costs(key(group, param), param)
            for param in group.params
            if not param.scopes or any(applies(scope) for scope in param.scopes)
        )

        if params:
            shown.append(replace(group, params=params))

    return tuple(shown)


def _with_costs(name: str, param: Param) -> Param:
    """``param``, with the loaded extensions' cost functions as experimental options after
    its own if it is a cost function parameter, read afresh at each call: a loader reset
    leaves none behind."""
    domain = _COST_DOMAINS.get(name)

    if domain is None:
        return param

    loaded = extension_loader.load().costs(domain)
    costs = tuple(experimental(cost.id, cost.label) for cost in loaded)

    return replace(param, options=(*param.options, *costs))


def _traits(method: MethodKey) -> frozenset[Trait]:
    """What an extension's method shares with the built-in ones."""
    loaded = extension_loader.find_method(method)

    if loaded is None:
        return frozenset()

    found = (
        (Trait.TRELLIS, loaded.codes_with_trellis),
        (Trait.KEYED, loaded.embedding.order is Order.KEYED_IF_ENCRYPTED),
    )

    return frozenset(trait for trait, has in found if has)
