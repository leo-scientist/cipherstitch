"""What each mode offers: the carrier types, methods, ciphers and file suffixes it lists, the
reasons it turns options off, and the extensions Research mode loaded. The service re-exports
these for the front ends. Only Research mode lists the extensions' entries, so only it loads
them (docs/architecture.md#extensions)."""

from pathlib import PurePath

from cipherstitch import availability, catalog, extension_loader, methods, standard
from cipherstitch.catalog import CarrierKind, CipherKey, MethodKey
from cipherstitch.errors import NotAvailableError, UnsupportedFormatError
from cipherstitch.params.settings import STANDARD as STANDARD_SETTINGS
from cipherstitch.params.settings import Settings


def mode_carriers(*, is_research: bool = False) -> tuple[CarrierKind, ...]:
    """The carrier types a mode offers: in Standard, those with a Recommended method."""
    return tuple(
        carrier.kind
        for carrier in catalog.CARRIERS
        if is_research or standard.carrier_reason(carrier.kind) is None
    )


def mode_methods(carrier: CarrierKind, *, is_research: bool = False) -> tuple[MethodKey, ...]:
    """The carrier type's methods a mode offers: in Standard, the Recommended ones, in
    Research every built-in, then the extensions'."""
    return tuple(
        method.id
        for method in availability.method_entries(carrier, is_research=is_research)
        if is_research or standard.method_reason(method.id) is None
    )


def mode_ciphers(*, is_research: bool = False) -> tuple[CipherKey, ...]:
    """The ciphers a mode offers: in Standard, the Recommended ones, in Research every
    built-in, then the extensions'."""
    return tuple(
        cipher.id
        for cipher in availability.cipher_entries(is_research=is_research)
        if is_research or standard.cipher_reason(cipher.id) is None
    )


def carrier_for_path(path: PurePath, *, is_research: bool = False) -> catalog.Carrier | None:
    """The carrier type of ``path`` by its suffix. Research mode adds the suffixes the
    extensions' methods bring to a carrier type."""
    return availability.carrier_for_path(path, is_research=is_research)


def carrier_suffixes(kind: CarrierKind, *, is_research: bool = False) -> tuple[str, ...]:
    """The suffixes a mode can hide in for the carrier type, for file dialogs: in Standard
    those a Standard method takes, in Research every one, the extensions' included."""
    if not is_research:
        return standard.carrier_formats(kind)

    return availability.carrier_suffixes(kind, is_research=True)


def unavailable_carriers(*, is_research: bool = False) -> dict[CarrierKind, str]:
    """Why each carrier type that Standard mode turns off is off (none in Research)."""
    if is_research:
        return {}

    reasons = {carrier.kind: standard.carrier_reason(carrier.kind) for carrier in catalog.CARRIERS}

    return {kind: reason for kind, reason in reasons.items() if reason is not None}


def unavailable_methods(
    carrier: CarrierKind,
    carrier_path: PurePath | None = None,
    *,
    is_research: bool = False,
    settings: Settings = STANDARD_SETTINGS,
) -> dict[MethodKey, str]:
    """The carrier type's methods that cannot run (on ``carrier_path``, if given), and why.
    Research mode's cover rules come from ``settings``."""
    reasons = {
        method.id: availability.mode_method_reason(method.id, carrier_path, is_research, settings)
        for method in availability.method_entries(carrier, is_research=is_research)
    }

    return {method: reason for method, reason in reasons.items() if reason is not None}


def unavailable_ciphers(*, is_research: bool = False) -> dict[CipherKey, str]:
    """The ciphers that cannot run, and why."""
    reasons = {
        cipher.id: (None if is_research else standard.cipher_reason(cipher.id))
        or availability.cipher_reason(cipher.id)
        for cipher in availability.cipher_entries(is_research=is_research)
    }

    return {cipher: reason for cipher, reason in reasons.items() if reason is not None}


def output_suffixes(
    method: MethodKey, carrier_path: PurePath | None, *, is_research: bool = False
) -> tuple[str, ...]:
    """File suffixes ``method`` can write for this carrier, the preferred one first. Only
    Research mode knows the extensions' methods."""
    unknown = None if is_research else standard.unknown_method_reason(method)

    if unknown is not None:
        raise NotAvailableError(unknown)

    embedding = methods.get(method)

    if embedding is not None:
        return embedding.output_suffixes(carrier_path)

    if carrier_path is not None:
        return (carrier_path.suffix.lower(),)

    return next(c.extensions for c in catalog.CARRIERS if catalog.method(method) in c.methods)


def reveal_methods(
    carrier_path: PurePath | None = None, *, is_research: bool = False
) -> tuple[MethodKey, ...]:
    """The methods Research mode's Reveal offers: those that may have hidden a payload in a
    file like ``carrier_path``, every implemented one without it. The built-ins come first,
    and only ``is_research`` adds the extensions'."""
    builtins = (m.id for c in catalog.CARRIERS for m in c.methods)
    added = (m.entry.id for m in extension_loader.load().methods) if is_research else ()
    embeddings = (methods.get(method) for method in (*builtins, *added))
    offered = tuple(
        embedding.method
        for embedding in embeddings
        if embedding is not None and (carrier_path is None or embedding.can_hold(carrier_path))
    )

    if not offered and carrier_path is not None:
        kind = availability.file_kind(carrier_path)
        raise UnsupportedFormatError(f"{kind} files can't carry a payload")

    return offered


def reveal_suffixes(*, is_research: bool = False) -> tuple[str, ...]:
    """Every suffix a file written by an implemented method can have, for file dialogs:
    the built-ins', then in Research mode the extensions'."""
    if not is_research:
        return methods.reveal_suffixes()

    loaded = (m.embedding for m in extension_loader.load().methods)
    added = (s for embedding in loaded for s in embedding.outputs or embedding.inputs)

    return tuple(dict.fromkeys((*methods.reveal_suffixes(), *added)))


def loaded_extensions() -> extension_loader.Loaded:
    """The extensions Research mode loaded, in name order, and those that failed. It loads
    them on the first call (docs/extensions.md)."""
    return extension_loader.load()


def extension_failures() -> tuple[extension_loader.Failure, ...]:
    """The extensions that couldn't load, each with every problem found and the traceback of
    an exception, for Research mode to show. Asking loads the extensions."""
    return extension_loader.load().failures
