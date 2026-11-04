"""Why a carrier type, method or cipher cannot run in a mode, what each mode lists, and which
method hides in a carrier by default. The service's checks and lists build on these. Only
Research mode lists the extensions' entries, so only it loads them
(docs/architecture.md#extensions)."""

import stat
from pathlib import Path, PurePath

from cipherstitch import catalog, extension_loader, methods, standard
from cipherstitch.catalog import CarrierKind, CipherKey, MethodKey
from cipherstitch.errors import NotAvailableError, UnsupportedFormatError
from cipherstitch.methods import Embedding
from cipherstitch.params.settings import STANDARD as STANDARD_SETTINGS
from cipherstitch.params.settings import Settings


def method_entries(kind: CarrierKind, *, is_research: bool = False) -> tuple[catalog.Method, ...]:
    """The carrier type's methods: the built-ins, then in Research mode the extensions'."""
    builtins = catalog.carrier(kind).methods

    if not is_research:
        return builtins

    loaded = extension_loader.load().methods

    return (*builtins, *(method.entry for method in loaded if method.carrier == kind))


def cipher_entries(*, is_research: bool = False) -> tuple[catalog.Cipher, ...]:
    """The ciphers: the built-ins, then in Research mode the extensions'."""
    if not is_research:
        return catalog.CIPHERS

    return (*catalog.CIPHERS, *(cipher.entry for cipher in extension_loader.load().ciphers))


def carrier_for_path(path: PurePath, *, is_research: bool = False) -> catalog.Carrier | None:
    """The carrier type of ``path`` by its suffix: a built-in one's, or in Research mode one
    an extension's method adds to a carrier type."""
    carrier = catalog.carrier_for_path(path)

    if carrier is not None or not is_research:
        return carrier

    kind = extension_loader.load().carrier_kind(path.suffix.lower())

    return None if kind is None else catalog.carrier(kind)


def carrier_suffixes(kind: CarrierKind, *, is_research: bool = False) -> tuple[str, ...]:
    """Every suffix of the carrier type, with those the extensions add in Research mode."""
    builtins = catalog.carrier(kind).extensions

    if not is_research:
        return builtins

    return (*builtins, *extension_loader.load().new_suffixes(kind))


def unknown_method_reason(method_id: MethodKey) -> str | None:
    """Research mode's: neither a built-in nor a loaded extension has ``method_id``."""
    if catalog.is_builtin_method(method_id) or extension_loader.find_method(method_id):
        return None

    return f"Unknown method {method_id}. No loaded extension adds it"


def unknown_cipher_reason(cipher_id: CipherKey) -> str | None:
    """As :func:`unknown_method_reason`."""
    if catalog.is_builtin_cipher(cipher_id) or extension_loader.find_cipher(cipher_id):
        return None

    return f"Unknown cipher {cipher_id}. No loaded extension adds it"


def cipher_reason(cipher_id: CipherKey) -> str | None:
    cipher = catalog.cipher(cipher_id)

    if not cipher.is_available:
        return f"{cipher.name} is not available yet"

    if cipher.requires is not None and not cipher.requires.is_installed:
        return cipher.requires.install_hint(cipher.name)

    return None


def method_reason(method_id: MethodKey, carrier_path: PurePath | None) -> str | None:
    method = catalog.method(method_id)
    embedding = methods.get(method_id)

    if not method.is_available or embedding is None:
        return f"{method.name} is not available yet"

    if method.requires is not None and not method.requires.is_installed:
        return method.requires.install_hint(method.name)

    if carrier_path is None:
        return None

    suffix = carrier_path.suffix.lower()

    if suffix not in embedding.inputs:
        return f"{method.name} can't use {file_kind(carrier_path)} files"

    requirement = catalog.FORMAT_REQUIREMENTS.get(suffix)

    if requirement is not None and not requirement.is_installed:
        return requirement.install_hint(suffix[1:].upper())

    return None


def mode_method_reason(
    method_id: MethodKey, carrier_path: PurePath | None, is_research: bool, settings: Settings
) -> str | None:
    """:func:`method_reason`; in Standard mode the method's tier speaks first. The mode's
    cover rules speak last, after the file's type, so that the suffix and extra checks speak
    before them."""
    tier_reason = None if is_research else standard.method_reason(method_id)
    reason = tier_reason or method_reason(method_id, carrier_path)

    if reason is not None or carrier_path is None:
        return reason

    return regular_file_problem(carrier_path) or standard.cover_reason(
        method_id, Path(carrier_path), cover_rules(is_research, settings)
    )


def cover_rules(is_research: bool, settings: Settings) -> standard.CoverRules:
    if not is_research:
        return standard.STANDARD_COVERS

    return standard.CoverRules(
        allows_lossy=bool(settings["limg.lossy"]),
        min_audio_depth=int(settings["laud.depth"]),
        is_standard=False,
    )


def installed_embedding(
    method: MethodKey | None, path: PurePath, *, is_research: bool = False
) -> Embedding | None:
    """The embedding of ``method``, if it can use ``path`` and its extra is installed. Only
    Research mode looks an extension's up."""
    if method is None or not (is_research or catalog.is_builtin_method(method)):
        return None

    embedding = methods.get(method)

    if embedding is None or path.suffix.lower() not in embedding.inputs:
        return None

    requirement = catalog.method(method).requires

    if requirement is not None and not requirement.is_installed:
        return None

    return embedding


def default_embedding(
    carrier_path: PurePath,
    *,
    output: PurePath | None = None,
    is_research: bool | None = None,
    settings: Settings = STANDARD_SETTINGS,
    with_extensions: bool = False,
) -> tuple[catalog.Carrier, Embedding]:
    """The carrier type of ``carrier_path`` and its first available method that can use it
    (and write ``output``'s format, if given). With a mode, the first one the mode allows; if
    it allows none, validating the first usable one names the reason. ``with_extensions``
    (Research mode) adds the extensions' suffixes, and their methods for a file no built-in
    method can use: they are never the default otherwise, nor loaded for it."""
    carrier = carrier_for_path(carrier_path, is_research=with_extensions)

    if carrier is None:
        raise unsupported_carrier(carrier_path)

    usable = usable_embeddings(carrier, carrier_path)

    if not usable and with_extensions:
        usable = usable_embeddings(carrier, carrier_path, with_extensions=True)

    if is_research is not None:
        usable = [
            embedding
            for embedding in usable
            if mode_method_reason(embedding.method, carrier_path, is_research, settings) is None
        ] or usable

    if not usable:
        raise NotAvailableError(f"{carrier.label} carriers are not available yet")

    if output is None:
        return carrier, usable[0]

    for embedding in usable:
        if output.suffix.lower() in embedding.output_suffixes(carrier_path):
            return carrier, embedding

    allowed = dict.fromkeys(s for e in usable for s in e.output_suffixes(carrier_path))
    raise UnsupportedFormatError(f"Output must be one of: {', '.join(allowed)}")


def usable_embeddings(
    carrier: catalog.Carrier, carrier_path: PurePath, *, with_extensions: bool = False
) -> list[Embedding]:
    """The carrier type's available methods, in catalog order (then the extensions', with
    ``with_extensions``), that can hide in ``carrier_path``."""
    suffix = carrier_path.suffix.lower()
    entries = method_entries(carrier.kind, is_research=with_extensions)
    available = (methods.get(method.id) for method in entries if method.is_available)

    return [
        embedding for embedding in available if embedding is not None and suffix in embedding.inputs
    ]


def unsupported_carrier(path: PurePath) -> UnsupportedFormatError:
    return UnsupportedFormatError(f"{file_kind(path)} is not a supported carrier")


def regular_file_problem(path: PurePath) -> str | None:
    """Why no reader may open ``path``: it exists but is a folder, a named pipe, a socket or
    a device. Opening a pipe blocks until something writes to it, so every check and entry
    point asks before any reader does. A missing file is left to its reader, which names it."""
    try:
        mode = Path(path).stat().st_mode
    except OSError:
        return None

    if stat.S_ISREG(mode):
        return None

    problem = "a folder, not a file" if stat.S_ISDIR(mode) else "not a regular file"

    return f"{path.name or path} is {problem}"


def file_kind(path: PurePath) -> str:
    """How messages name the type of ``path``: its suffix, else its name, else the path as
    given (``.`` has neither)."""
    return path.suffix or path.name or str(path)
