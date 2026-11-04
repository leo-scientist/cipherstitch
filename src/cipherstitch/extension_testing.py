"""Checks for an extension's own tests (docs/extensions.md#testing). Each one applies the rules
the loader applies, then hides and reveals through the service in Research mode, as the CLI
and the app do. A failure raises ``AssertionError`` with what went wrong, so pytest shows it.
The spec is checked on its own, whatever the extensions folder holds."""

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from cipherstitch import (
    availability,
    catalog,
    container,
    extension_checks,
    extension_loader,
    methods,
    service,
)
from cipherstitch.catalog import CipherId, CipherKey, MethodId, MethodKey
from cipherstitch.container import UnitOrder
from cipherstitch.errors import (
    CapacityError,
    CipherStitchError,
    NoHiddenDataError,
    UnsupportedFormatError,
)
from cipherstitch.extensions import (
    CipherSpec,
    Coefficients,
    CostSpec,
    Domain,
    Extension,
    MethodSpec,
    Order,
)
from cipherstitch.media import jpeg, precover
from cipherstitch.methods import Embedding
from cipherstitch.methods.picture import COLOR_CHANNELS, Picture
from cipherstitch.params import engine, registry
from cipherstitch.params.settings import STANDARD as STANDARD_SETTINGS
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile

# As strong as Research's default minimum asks. It protects nothing: the checks hide test data.
PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105 (a public test value)
_NAME = "check.bin"
_DATA = b"CipherStitch checks an extension"
_ORDER = "frame.order"
# A cipher's checks hide with zero-width characters, which need no extra: 8 insertion points
# a word, 2 bits each, so this text holds 800 bytes.
_COVER_TEXT = "lanterns " * 400
_COST_METHODS = {Domain.SPATIAL: MethodId.LSB, Domain.JPEG: MethodId.DCT}
_COST_RATE = 0.1  # message bits per value, which only a cost that depends on the payload reads


@dataclass(frozen=True, slots=True)
class _Case:
    method: MethodKey
    cipher: CipherKey
    settings: Settings
    label: str  # e.g. "a payload encrypted with AES-256-GCM in the keyed order"

    @property
    def password(self) -> str | None:
        return None if self.cipher == CipherId.NONE else PASSWORD


def check_method(spec: MethodSpec, cover_path: Path, extension: Extension | None = None) -> None:
    """Hide in ``cover_path`` with ``spec`` and reveal again: a plain payload, then one
    encrypted with the default cipher in every order the method allows, then a plain payload
    of exactly the most the cover holds, while one byte more raises ``CapacityError``. Then
    a reveal of the untouched cover with ``spec``, and of a hidden payload with another
    method that can read such files, must find nothing. The cover must hold about 100 bytes.

    ``extension`` is the ``Extension`` that declares ``spec``, which a method with
    parameters needs: the rules apply to it as declared, and the parameters keep their
    defaults. Without it, ``spec`` is checked on its own.
    """
    declared = Extension(methods=(spec,)) if extension is None else extension

    if spec not in declared.methods:
        raise AssertionError(f"The extension doesn't declare the method {spec.id}")

    with _loaded_alone(declared, spec.open), _folder() as folder:
        plain = _Case(spec.id, CipherId.NONE, STANDARD_SETTINGS, "a plain payload")
        is_ordered = spec.order is Order.KEYED_IF_ENCRYPTED
        encrypted = _encrypted_cases(spec.id, catalog.DEFAULT_CIPHER, is_ordered=is_ordered)
        embedded = [_round_trip(cover_path, folder, case) for case in (plain, *encrypted)]
        _check_largest_payload(cover_path, folder, plain)
        _check_finds_nothing(cover_path, folder, plain, f"the untouched cover with {spec.id}")
        _check_other_methods(embedded[0], folder, plain)


def check_cipher(spec: CipherSpec) -> None:
    """The self-test and the other rules, then hide and reveal with ``spec`` through the
    service, in zero-width characters in a generated text: a payload in every order, one of
    exactly the most the text holds while one byte more raises ``CapacityError``, and a
    reveal with a wrong password that must find nothing."""
    with _loaded_alone(Extension(ciphers=(spec,)), spec.cipher), _folder() as folder:
        cover = folder / "cover.txt"
        cover.write_text(_COVER_TEXT, encoding="utf-8")
        cases = list(_encrypted_cases(MethodId.ZERO_WIDTH, spec.id, is_ordered=True))
        embedded = [_round_trip(cover, folder, case) for case in cases]
        _check_largest_payload(cover, folder, cases[0])
        subject = f"{cases[0].label} with a wrong password"
        _check_finds_nothing(embedded[0], folder, cases[0], subject, password=f"not {PASSWORD}")


def check_cost(spec: CostSpec, cover_path: Path) -> None:
    """The rules, then ``spec``'s costs of ``cover_path`` checked as the channel checks them,
    then a payload encrypted with the default cipher hidden with ``spec`` through image LSB
    (``Domain.SPATIAL``, a PNG, BMP or TIFF cover) or DCT (``Domain.JPEG``, a JPEG or a RAW,
    PNG or TIFF one) in Research mode. It must reveal with the same settings, and in Standard
    mode too: a cost function only steers where the changes go, never what a reveal reads."""
    with _loaded_alone(Extension(costs=(spec,)), spec.costs), _folder() as folder:
        _check_cover_costs(spec, cover_path)
        settings = Settings.of({registry.COST_KEYS[spec.domain]: spec.id})
        name = catalog.cipher(catalog.DEFAULT_CIPHER).name
        label = f"a payload encrypted with {name} and the cost function {spec.id}"
        case = _Case(_COST_METHODS[spec.domain], catalog.DEFAULT_CIPHER, settings, label)
        embedded = _round_trip(cover_path, folder, case)
        _check_standard_reveal(embedded, folder, case)


def _check_cover_costs(spec: CostSpec, cover: Path) -> None:
    """The costs of ``cover``'s values, read as the channel reads them, checked as it checks
    them (``extension_guards.GuardedCosts``). A cover that can't be read fails here too."""
    try:
        if spec.domain is Domain.SPATIAL:
            colors = Picture(cover).pixels[..., :COLOR_CHANNELS]
            extension_loader.spatial_costs(spec.id, colors, _COST_RATE)
        else:
            extension_loader.jpeg_costs(spec.id, _coefficients(cover))
    except (CipherStitchError, OSError) as exc:
        raise AssertionError(f"Computing the costs of {cover.name} failed: {exc}") from exc


def _coefficients(cover: Path) -> Coefficients:
    """A JPEG's, or those a precover compresses to at Standard's quality and subsampling."""
    if precover.is_precover(cover):
        return precover.compress(cover, precover.QUALITY, precover.SUBSAMPLING).coefficients

    return jpeg.read_coefficients(cover)


def _check_standard_reveal(embedded: Path, folder: Path, case: _Case) -> None:
    """Standard mode's reveal, which reads no settings and never loads an extension, finds
    the payload."""
    try:
        revealed = service.reveal(embedded, Path(tempfile.mkdtemp(dir=folder)), case.password)
    except CipherStitchError as exc:
        raise AssertionError(f"Revealing {case.label} in Standard mode failed: {exc}") from exc

    if revealed.path.read_bytes() != _DATA:
        raise AssertionError(f"Revealing {case.label} in Standard mode gave back other bytes")


@contextmanager
def _loaded_alone(extension: Extension, code: object) -> Iterator[None]:
    """``extension`` as the only one loaded, named after the module that defines ``code``
    (the opener or the cipher), as the loader would name it."""
    problems = extension_checks.problems(extension)

    if problems:
        raise AssertionError("The extension breaks the rules:\n" + "\n".join(problems))

    module = str(getattr(code, "__module__", None) or type(code).__module__)

    with extension_loader.alone(module.rsplit(".", 1)[-1], extension):
        yield


@contextmanager
def _folder() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as folder:
        yield Path(folder)


def _encrypted_cases(method: MethodKey, cipher: CipherKey, *, is_ordered: bool) -> Iterator[_Case]:
    """With ``is_ordered`` (``Order.KEYED_IF_ENCRYPTED``), one for each value of Research's
    ``frame.order``. A sequential method ignores it."""
    name = catalog.cipher(cipher).name

    if not is_ordered:
        yield _Case(method, cipher, STANDARD_SETTINGS, f"a payload encrypted with {name}")

        return

    for order in UnitOrder:
        label = f"a payload encrypted with {name} in the {order} order"
        yield _Case(method, cipher, Settings.of({_ORDER: order.value}), label)


def _round_trip(cover: Path, folder: Path, case: _Case, data: bytes = _DATA) -> Path:
    """The file ``data`` was hidden in, after checking that it reveals."""
    work = Path(tempfile.mkdtemp(dir=folder))

    try:
        embedded = _hidden(cover, work, case, data)
    except CipherStitchError as exc:
        raise AssertionError(f"Hiding {case.label} failed: {exc}") from exc

    research = service.ResearchReveal(case.method, case.cipher, case.settings)

    try:
        revealed = service.reveal(embedded, work / "revealed", case.password, research=research)
    except CipherStitchError as exc:
        raise AssertionError(f"Revealing {case.label} failed: {exc}") from exc

    if revealed.path.read_bytes() != data:
        raise AssertionError(f"Revealing {case.label} gave back other bytes than were hidden")

    return embedded


def _hidden(cover: Path, work: Path, case: _Case, data: bytes) -> Path:
    """The file ``data`` was hidden in, through the service as the CLI does."""
    suffix = _embedding(case.method).output_suffixes(cover)[0]
    embedded = work / f"embedded{suffix}"
    options = service.hide_options(
        cover, embedded, case.cipher, method=case.method, is_research=True, settings=case.settings
    )
    service.hide(cover, HiddenFile(_NAME, data), embedded, replace(options, password=case.password))

    return embedded


def _embedding(method: MethodKey) -> Embedding:
    embedding = methods.get(method)

    if embedding is None:
        raise AssertionError(f"{method} has no embedding")

    return embedding


def _check_largest_payload(cover: Path, folder: Path, case: _Case) -> None:
    """The capacity the channel reports is what its write stores, to the byte."""
    channel = _embedding(case.method).open(cover, engine.tuning(case.settings, case.method))
    largest = container.max_data_size(channel, _NAME, case.cipher)
    data = bytes(index % 256 for index in range(largest))
    _round_trip(cover, folder, replace(case, label=f"{case.label} of {largest} bytes"), data)

    try:
        _hidden(cover, Path(tempfile.mkdtemp(dir=folder)), case, data + b"!")
    except CapacityError:
        return

    raise AssertionError(f"Hiding {case.label} of {largest + 1} bytes raised no CapacityError")


def _check_finds_nothing(
    path: Path, folder: Path, case: _Case, subject: str, password: str | None = None
) -> None:
    """A Research reveal of ``path`` with ``case``'s method and cipher finds no payload."""
    research = service.ResearchReveal(case.method, case.cipher, case.settings)

    try:
        service.reveal(path, Path(tempfile.mkdtemp(dir=folder)), password, research=research)
    except (NoHiddenDataError, UnsupportedFormatError):
        return
    except CipherStitchError as exc:
        raise AssertionError(f"Revealing {subject} failed: {exc}") from exc

    raise AssertionError(f"Revealing {subject} found a payload")


def _check_other_methods(embedded: Path, folder: Path, case: _Case) -> None:
    """The first other installed method that can read files like ``embedded`` finds nothing
    in it. Files only the extension writes have none."""
    offered = service.reveal_methods(embedded, is_research=True)
    others = [
        m for m in offered if m != case.method and availability.method_reason(m, None) is None
    ]

    if others:
        subject = f"{case.label} hidden with {case.method}, with {others[0]}"
        _check_finds_nothing(embedded, folder, replace(case, method=others[0]), subject)
