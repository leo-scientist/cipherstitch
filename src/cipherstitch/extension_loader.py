"""Finds the extensions in ``cipherstitch.extensions``, checks them (``extension_checks``) and
builds the same catalog entries, embeddings, ciphers, parameter groups and cost functions the
built-ins have, once per process (docs/architecture.md#extensions). Only Research mode gets
here: Standard mode never asks for an id that isn't a built-in's."""

import importlib
import pkgutil
import threading
import traceback
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt

from cipherstitch import catalog, extension_checks
from cipherstitch.catalog import CarrierKind, CipherKey, MethodKey, Tier
from cipherstitch.ciphers.base import Cipher
from cipherstitch.errors import NotAvailableError
from cipherstitch.extension_guards import (
    GuardedCipher,
    GuardedCosts,
    described,
    guarded_check,
    guarded_opener,
)
from cipherstitch.extensions import CipherSpec, CostSpec, Domain, Extension, MethodSpec
from cipherstitch.media.jpeg import Coefficients
from cipherstitch.methods import Embedding
from cipherstitch.params.model import Group

PACKAGE = "cipherstitch.extensions"


@dataclass(frozen=True, slots=True)
class LoadedMethod:
    entry: catalog.Method
    carrier: CarrierKind
    new_suffixes: tuple[str, ...]  # those no built-in carrier has: they join ``carrier``
    embedding: Embedding
    wire_id: int
    risk: str | None  # the Detection row's line
    codes_with_trellis: bool = False

    @property
    def new_inputs(self) -> tuple[str, ...]:
        """The new suffixes it hides in. One it only writes joins ``carrier`` for the clash
        rules, but is no carrier."""
        return tuple(suffix for suffix in self.new_suffixes if suffix in self.embedding.inputs)


@dataclass(frozen=True, slots=True)
class LoadedCipher:
    entry: catalog.Cipher
    cipher: Cipher
    wire_id: int


@dataclass(frozen=True, slots=True)
class LoadedCost:
    id: str
    label: str
    domain: Domain
    costs: GuardedCosts


@dataclass(frozen=True, slots=True)
class LoadedExtension:
    name: str  # the module's, e.g. line_spaces
    summary: str
    methods: tuple[LoadedMethod, ...]
    ciphers: tuple[LoadedCipher, ...]
    params: tuple[Group, ...] = ()  # each group's check guarded
    costs: tuple[LoadedCost, ...] = ()


@dataclass(frozen=True, slots=True)
class Failure:
    """An extension that didn't load: every problem found, or the exception its import (or a
    check) raised, with the traceback."""

    extension: str
    problems: tuple[str, ...]
    traceback: str | None = None

    @property
    def brief(self) -> str:
        """The first problem, and how many more there are: one line for a warning."""
        more = len(self.problems) - 1

        return f"{self.problems[0]} (and {more} more)" if more else self.problems[0]


@dataclass(frozen=True, slots=True)
class Loaded:
    """In module order, then in the order each module declares its entries."""

    extensions: tuple[LoadedExtension, ...] = ()
    failures: tuple[Failure, ...] = ()

    @property
    def methods(self) -> tuple[LoadedMethod, ...]:
        return tuple(method for extension in self.extensions for method in extension.methods)

    @property
    def ciphers(self) -> tuple[LoadedCipher, ...]:
        return tuple(cipher for extension in self.extensions for cipher in extension.ciphers)

    def method(self, method_id: MethodKey) -> LoadedMethod | None:
        return next((method for method in self.methods if method.entry.id == method_id), None)

    def cipher(self, cipher_id: CipherKey) -> LoadedCipher | None:
        return next((cipher for cipher in self.ciphers if cipher.entry.id == cipher_id), None)

    def costs(self, domain: Domain) -> tuple[LoadedCost, ...]:
        return tuple(c for e in self.extensions for c in e.costs if c.domain is domain)

    def cost(self, domain: Domain, cost_id: str) -> LoadedCost | None:
        return next((cost for cost in self.costs(domain) if cost.id == cost_id), None)

    def new_suffixes(self, kind: CarrierKind) -> tuple[str, ...]:
        """The suffixes no built-in carrier has that the methods of ``kind`` hide in."""
        found = (s for m in self.methods if m.carrier == kind for s in m.new_inputs)

        return tuple(dict.fromkeys(found))

    def carrier_kind(self, suffix: str) -> CarrierKind | None:
        """The carrier kind of a suffix no built-in carrier has, if a method hides in it."""
        return next((m.carrier for m in self.methods if suffix in m.new_inputs), None)


_lock = threading.RLock()
_loaded: Loaded | None = None


def load() -> Loaded:
    """The extensions of :data:`PACKAGE`, loaded on the first call and kept for the process:
    Python caches the modules anyway, so a changed extension needs a restart."""
    global _loaded

    with _lock:
        if _loaded is None:
            # An extension that looks an id up while it is imported finds no extension.
            _loaded = Loaded()

            try:
                _loaded = load_package(PACKAGE)
            except BaseException:
                _loaded = None
                raise

        return _loaded


def reset() -> None:
    """Forget what :func:`load` found (tests point :data:`PACKAGE` elsewhere)."""
    global _loaded

    with _lock:
        _loaded = None


@contextmanager
def alone(name: str, extension: Extension) -> Iterator[Loaded]:
    """``extension``, as the module ``name``, the only one loaded until the block ends,
    whatever the folder holds (``extension_testing``). Its rules aren't checked here."""
    global _loaded

    with _lock:
        previous = _loaded
        _loaded = Loaded((_built(name, extension),))
        loaded = _loaded

    try:
        yield loaded
    finally:
        with _lock:
            _loaded = previous


def find_method(method_id: MethodKey) -> LoadedMethod | None:
    return load().method(method_id)


def find_cipher(cipher_id: CipherKey) -> LoadedCipher | None:
    return load().cipher(cipher_id)


def spatial_costs(
    cost_id: str, colors: npt.NDArray[np.uint8], rate: float
) -> npt.NDArray[np.float32]:
    """Image LSB's costs from the loaded extension cost function ``cost_id``, checked."""
    return _cost(Domain.SPATIAL, cost_id).spatial(colors, rate)


def jpeg_costs(cost_id: str, cover: Coefficients) -> npt.NDArray[np.float32]:
    """DCT's costs from the loaded extension cost function ``cost_id``, checked."""
    return _cost(Domain.JPEG, cost_id).jpeg(cover)


def _cost(domain: Domain, cost_id: str) -> GuardedCosts:
    """Research's settings name only loaded cost functions."""
    loaded = load().cost(domain, cost_id)

    if loaded is None:
        raise NotAvailableError(f"Unknown cost function {cost_id}. No loaded extension adds it")

    return loaded.costs


def load_package(package: str) -> Loaded:
    """Every module and package in ``package`` whose name doesn't start with ``_``, sorted by
    name. A module that breaks a rule fails whole, and modules that claim the same id, wire
    id or new suffix (for different carrier kinds) all fail."""
    root = importlib.import_module(package)
    names = sorted(
        module.name
        for module in pkgutil.iter_modules(root.__path__)
        if not module.name.startswith("_")
    )
    results = [_imported(package, name) for name in names]
    found = [result for result in results if isinstance(result, LoadedExtension)]
    clashes = _clashes(found)
    failures = (
        result if isinstance(result, Failure) else Failure(result.name, clashes[result.name])
        for result in results
        if isinstance(result, Failure) or result.name in clashes
    )

    return Loaded(
        tuple(extension for extension in found if extension.name not in clashes),
        tuple(failures),
    )


def _imported(package: str, name: str) -> LoadedExtension | Failure:
    """Any ``Exception`` fails the module, and so does ``SystemExit`` (an argument parser or
    ``sys.exit`` run at import), which would otherwise end the app on every Research start.
    Any other ``BaseException`` (Ctrl+C) stops the load."""
    try:
        extension = getattr(importlib.import_module(f"{package}.{name}"), "EXTENSION", None)

        if extension is None:
            return Failure(name, ("it has no EXTENSION",))

        if not isinstance(extension, Extension):
            kind = type(extension).__name__
            return Failure(name, (f"EXTENSION must be an Extension, not a {kind}",))

        problems = extension_checks.problems(extension)

        if problems:
            return Failure(name, problems)

        return _built(name, extension)
    except (Exception, SystemExit) as exc:
        return Failure(name, (described(exc),), "".join(traceback.format_exception(exc)))


def _built(name: str, extension: Extension) -> LoadedExtension:
    methods = tuple(_method(name, spec) for spec in extension.methods)
    ciphers = tuple(_cipher(name, spec) for spec in extension.ciphers)
    params = tuple(_group(name, group) for group in extension.params)
    costs = tuple(_cost_function(name, spec) for spec in extension.costs)

    return LoadedExtension(name, extension.summary, methods, ciphers, params, costs)


def _method(extension: str, spec: MethodSpec) -> LoadedMethod:
    entry = catalog.Method(
        spec.id,
        spec.name,
        Tier.EXPERIMENTAL,
        spec.summary,
        spec.pros,
        spec.cons,
        spec.capacity,
        is_available=True,
        requires=spec.requires,
        extension=extension,
    )
    opener = guarded_opener(extension, spec.open)
    embedding = Embedding(spec.id, spec.order, opener, spec.inputs, spec.outputs)

    new_suffixes = extension_checks.new_suffixes(spec)

    return LoadedMethod(
        entry,
        spec.carrier,
        new_suffixes,
        embedding,
        spec.wire_id,
        spec.risk,
        spec.codes_with_trellis,
    )


def _cipher(extension: str, spec: CipherSpec) -> LoadedCipher:
    entry = catalog.Cipher(
        spec.id,
        spec.name,
        Tier.EXPERIMENTAL,
        spec.summary,
        spec.pros,
        spec.cons,
        is_available=True,
        requires=spec.requires,
        extension=extension,
    )
    cipher = GuardedCipher(extension, spec.id, spec.cipher.key_size, spec.cipher)

    return LoadedCipher(entry, cipher, spec.wire_id)


def _group(extension: str, group: Group) -> Group:
    return (
        group
        if group.check is None
        else replace(group, check=guarded_check(extension, group.check))
    )


def _cost_function(extension: str, spec: CostSpec) -> LoadedCost:
    return LoadedCost(spec.id, spec.label, spec.domain, GuardedCosts(extension, spec.costs))


def _clashes(extensions: Sequence[LoadedExtension]) -> dict[str, tuple[str, ...]]:
    """The problems of each extension that claims what another one claims."""
    problems: dict[str, list[str]] = {}

    for name, problem in (*_id_clashes(extensions), *_suffix_clashes(extensions)):
        problems.setdefault(name, []).append(problem)

    return {name: tuple(found) for name, found in problems.items()}


def _id_clashes(extensions: Sequence[LoadedExtension]) -> Iterator[tuple[str, str]]:
    owners: dict[str, list[str]] = {}

    for extension in extensions:
        for claim in _claimed_ids(extension):
            owners.setdefault(claim, []).append(extension.name)

    for claim, names in owners.items():
        for name in names if len(names) > 1 else ():
            others = ", ".join(other for other in names if other != name)
            yield name, f"{claim} is also claimed by {others}"


def _claimed_ids(extension: LoadedExtension) -> Iterator[str]:
    for method in extension.methods:
        yield f"the method id {method.entry.id}"
        yield f"the method wire id {method.wire_id}"

    for cipher in extension.ciphers:
        yield f"the cipher id {cipher.entry.id}"
        yield f"the cipher wire id {cipher.wire_id}"

    for group in extension.params:
        yield f"the parameter group id {group.id}"

    for cost in extension.costs:
        yield f"the {cost.domain} cost function id {cost.id}"


def _suffix_clashes(extensions: Sequence[LoadedExtension]) -> Iterator[tuple[str, str]]:
    """Two extensions may both hide in a new suffix, as long as it joins one carrier kind."""
    kinds: dict[str, dict[str, CarrierKind]] = {}  # by suffix, then by extension

    for extension in extensions:
        for method in extension.methods:
            for suffix in method.new_suffixes:
                kinds.setdefault(suffix, {})[extension.name] = method.carrier

    for suffix, owners in kinds.items():
        if len(set(owners.values())) == 1:
            continue

        for name, kind in owners.items():
            others = ", ".join(f"{k} carriers in {n}" for n, k in owners.items() if n != name)
            yield name, f"the suffix {suffix} joins {kind} carriers here, but {others}"
