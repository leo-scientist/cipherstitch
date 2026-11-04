"""What the CLI and the desktop app run: validation, estimates, hide, reveal and detection."""

import functools
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath

from cipherstitch import (
    availability,
    catalog,
    ciphers,
    container,
    extension_loader,
    kdf,
    methods,
    offers,
    payload,
    risks,
    standard,
)
from cipherstitch.catalog import CarrierKind, CipherId, CipherKey, MethodId, MethodKey
from cipherstitch.container import Capacity, Group, Step
from cipherstitch.errors import (
    CapacityError,
    CipherStitchError,
    NoHiddenDataError,
    NotAvailableError,
    UnsupportedFormatError,
)
from cipherstitch.media import description, image, jpeg, precover
from cipherstitch.methods import Embedded, Embedding, Opener, Tuning
from cipherstitch.methods.trellis import TrellisChannel
from cipherstitch.params import engine
from cipherstitch.params.settings import STANDARD as STANDARD_SETTINGS
from cipherstitch.params.settings import Settings
from cipherstitch.password import password_strength
from cipherstitch.payload import HiddenFile

# The front ends' only API includes what each mode offers (offers.py).
carrier_for_path = offers.carrier_for_path
carrier_suffixes = offers.carrier_suffixes
extension_failures = offers.extension_failures
loaded_extensions = offers.loaded_extensions
mode_carriers = offers.mode_carriers
mode_ciphers = offers.mode_ciphers
mode_methods = offers.mode_methods
output_suffixes = offers.output_suffixes
reveal_methods = offers.reveal_methods
reveal_suffixes = offers.reveal_suffixes
unavailable_carriers = offers.unavailable_carriers
unavailable_ciphers = offers.unavailable_ciphers
unavailable_methods = offers.unavailable_methods

ProgressCallback = Callable[[int, int, str], None]
"""Called as ``(step index, step count, step label)`` before each step, and once more with the
last step just before the result is saved; may raise to abort (nothing is saved then)."""

_SPACE_CACHE_SIZE = 8


@dataclass(frozen=True, slots=True)
class HideOptions:
    """What to hide with: Standard mode's rules unless ``is_research``. Only Research mode
    reads ``settings``."""

    carrier: CarrierKind = CarrierKind.IMAGE
    method: MethodKey = MethodId.LSB
    cipher: CipherKey = catalog.DEFAULT_CIPHER
    password: str | None = field(default=None, repr=False)
    is_research: bool = False
    settings: Settings = STANDARD_SETTINGS

    @property
    def is_encrypted(self) -> bool:
        return self.cipher != CipherId.NONE

    @property
    def mode_settings(self) -> Settings:
        """The settings hiding runs with: Standard's in Standard mode."""
        return self.settings if self.is_research else STANDARD_SETTINGS

    @property
    def min_password_strength(self) -> int:
        """The zxcvbn score an encrypting hide needs: Standard's, or ``crypto.minpw``."""
        return int(self.mode_settings["crypto.minpw"])


@dataclass(frozen=True, slots=True)
class CarrierDetails:
    description: str
    capacity: int | None


@dataclass(frozen=True, slots=True)
class Estimate:
    required: int
    available: int | None
    limit: int | None = None  # the most a Standard payload may take: the advised size
    advised: int | None = None  # the most a payload should take (risks.advised_size)

    @property
    def excess(self) -> int:
        allowed = self.available if self.limit is None else self.limit

        return 0 if allowed is None else max(self.required - allowed, 0)

    @property
    def share(self) -> float | None:
        """The part of the capacity the payload takes; 1.0 is full."""
        return self.required / self.available if self.available else None


@dataclass(frozen=True, slots=True)
class ModeCapacity:
    """What :func:`mode_capacity` found: the mode's default method for the carrier, and the
    bytes a payload may take with it."""

    method: MethodKey
    size: int


@dataclass(frozen=True, slots=True)
class ResearchReveal:
    """What Research mode's Reveal tries: only this method and cipher, with these settings.
    Files carry no settings, so they must be the ones that hid the payload."""

    method: MethodKey
    cipher: CipherKey
    settings: Settings = STANDARD_SETTINGS


@dataclass(frozen=True, slots=True)
class RevealResult:
    path: Path
    size: int
    method: MethodKey
    cipher: CipherKey

    @property
    def is_integrity_verified(self) -> bool:
        return self.cipher != CipherId.NONE


def unavailable_reason(options: HideOptions, carrier_path: PurePath | None = None) -> str | None:
    """Why ``options`` (on ``carrier_path``, if given) cannot run, or ``None`` if they can."""
    carrier = catalog.carrier(options.carrier)

    if not carrier.is_available:
        return f"{carrier.label} carriers are not available yet"

    if not options.is_research:
        reason = standard.carrier_reason(options.carrier) or standard.cipher_reason(options.cipher)

        if reason is not None:
            return reason

    return availability.cipher_reason(options.cipher) or availability.mode_method_reason(
        options.method, carrier_path, options.is_research, options.mode_settings
    )


def describe_carrier(
    path: Path,
    kind: CarrierKind,
    method: MethodKey | None = None,
    settings: Settings = STANDARD_SETTINGS,
    *,
    is_research: bool = False,
) -> CarrierDetails:
    """Short description (e.g. ``2400 × 1600 · PNG``) and total capacity in bytes at
    ``settings``, for ``method`` if it can use and read the file, else for the carrier type's
    default method. Only Research mode knows the extensions' methods and suffixes."""
    _check_carrier_file(path)
    described = description.describe(path, kind)
    embedding = availability.installed_embedding(method, path, is_research=is_research)

    if embedding is not None:
        try:
            space = _space(embedding, path, engine.tuning(settings, embedding.method))

            return CarrierDetails(described, space.capacity(0))
        except UnsupportedFormatError:
            pass  # this method cannot read the file; hiding with it reports why

    _carrier, default = availability.default_embedding(path, with_extensions=is_research)
    space = _space(default, path, engine.tuning(settings, default.method))

    return CarrierDetails(described, space.capacity(0))


def compresses_carrier(path: PurePath, method: MethodKey) -> bool:
    """Whether describing or estimating ``path`` for ``method`` first compresses it to JPEG
    (a camera RAW, or a PNG or TIFF for DCT), which can take a few seconds. It happens once
    per version of the file. A RAW falls back to DCT, unless an extension's method takes it."""
    suffix = path.suffix.lower()
    is_raw = suffix in catalog.RAW_EXTENSIONS

    if is_raw and not catalog.is_builtin_method(method):
        embedding = methods.get(method)  # an extension's: only Research mode names one

        return embedding is None or suffix not in embedding.inputs

    return precover.is_precover(path) and (method == MethodId.DCT or is_raw)


def estimate(
    carrier_path: Path, payload_name: str, payload_size: int, options: HideOptions
) -> Estimate:
    """Carrier space the payload needs after encryption and framing, and the space available."""
    _check_carrier_file(carrier_path)

    if not options.is_research:
        _refuse_unknown_ids(options)

    embedding = methods.get(options.method)

    if embedding is None or carrier_path.suffix.lower() not in embedding.inputs:
        sealed = ciphers.get(options.cipher).sealed_size(payload_size)

        return Estimate(required=sealed, available=None)

    space = _space(embedding, carrier_path, engine.tuning(options.mode_settings, options.method))
    total = space.capacity(0)
    is_precover = precover.is_precover(carrier_path)
    advised = risks.advised_size(options.method, space.elements, total, is_precover=is_precover)

    return Estimate(
        required=_required(space, payload_name, payload_size, options.cipher),
        available=total,
        limit=None if options.is_research else advised,
        advised=advised,
    )


def detection_risks(
    options: HideOptions, carrier_path: Path | None = None, estimate: Estimate | None = None
) -> tuple[str, ...]:
    """What makes the payload easier to detect: the method, the carrier (if given) and how
    much of it the payload takes (if ``estimate`` is given). Advice only; hiding still runs."""
    if carrier_path is not None:
        _check_carrier_file(carrier_path)

    fill = None

    if estimate is not None and estimate.available:
        fill = risks.Fill(estimate.required, estimate.available, estimate.advised)

    tuning = engine.tuning(options.mode_settings)
    found = risks.detection_risks(options.method, carrier=carrier_path, fill=fill, tuning=tuning)

    if options.is_research and not catalog.is_builtin_method(options.method):
        return (*found, _extension_risk(options.method))

    return found


def _extension_risk(method: MethodKey) -> str:
    """The extension's own line for the Detection row, or one that says nobody measured it."""
    loaded = extension_loader.find_method(method)

    return risks.EXTENSION_RISK if loaded is None or loaded.risk is None else loaded.risk


def is_conspicuous(estimate: Estimate) -> bool:
    """Whether the payload is larger than advised for the carrier, so easier to detect."""
    return risks.is_conspicuous(estimate.required, estimate.advised)


@dataclass(frozen=True, slots=True)
class _Space:
    """A carrier's capacity with no prefix, and after the 16-byte prefix both layouts write
    (a nonce or a salt), and its counted values if the method is statistical."""

    plain: int
    salted: int
    elements: int | None

    def capacity(self, prefix_size: int) -> int:
        return {0: self.plain, kdf.SALT_SIZE: self.salted}[prefix_size]


def _space(embedding: Embedding, path: Path, tuning: Tuning = methods.STANDARD) -> _Space:
    stat = path.stat()
    version = (stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_ctime_ns)

    return _carrier_space(embedding.open, path, version, tuning)


@functools.lru_cache(maxsize=_SPACE_CACHE_SIZE)
def _carrier_space(
    opener: Opener, path: Path, _version: tuple[int, int, int, int], tuning: Tuning
) -> _Space:
    """Cached: the Hide page estimates on every keystroke, and opening a carrier may decode
    all of it. The file's version in the key makes an edited carrier read again: its mtime,
    size, inode and ctime, since a copy that keeps the mtime (``cp -p``) still moves the
    ctime."""
    channel = opener(path, tuning)

    return _Space(channel.capacity(0), channel.capacity(kdf.SALT_SIZE), _element_count(channel))


def _element_count(channel: Capacity) -> int | None:
    """The values a statistical channel's capacity counts (risks.advised_size)."""
    return channel.element_count if isinstance(channel, TrellisChannel) else None


def kdf_name(settings: Settings) -> str:
    """The name of what derives the key at ``settings``, as the steps and the password meter
    show it."""
    return engine.kdf_profile(settings).function.label


def hide_step_labels(
    options: HideOptions, output_path: Path, carrier_path: PurePath | None = None
) -> tuple[str, ...]:
    """The steps of hiding, with compressing the carrier first when it is a precover."""
    labels = {
        Step.DERIVE_KEY: f"Deriving key · {kdf_name(options.mode_settings)}",
        Step.ENCRYPT: f"Encrypting · {catalog.cipher(options.cipher).name}",
        Step.EMBED: f"Embedding · {catalog.method(options.method).name}",
    }
    steps = container.hide_steps(encrypted=options.is_encrypted)
    compressing = (
        (f"Compressing {carrier_path.name} to JPEG",)
        if carrier_path is not None and compresses_carrier(carrier_path, options.method)
        else ()
    )

    return (*compressing, *(labels[step] for step in steps), _writing_label(output_path))


def reveal_step_labels(
    password: str | None, research: ResearchReveal | None = None
) -> tuple[str, ...]:
    """Standard mode tries every method and cipher. Research mode names the ones it tries."""
    if research is None:
        labels = {
            Step.LOCATE: "Trying every method",
            Step.DERIVE_KEY: f"Deriving key · {kdf_name(STANDARD_SETTINGS)}",
            Step.DECRYPT: "Decrypting and verifying",
        }
    else:
        labels = {
            Step.LOCATE: f"Extracting · {catalog.method(research.method).name}",
            Step.DERIVE_KEY: f"Deriving key · {kdf_name(research.settings)}",
            Step.DECRYPT: f"Decrypting · {catalog.cipher(research.cipher).name}",
        }

    return tuple(labels[step] for step in container.reveal_steps(encrypted=bool(password)))


def hide(
    carrier_path: Path,
    hidden: HiddenFile,
    output_path: Path,
    options: HideOptions,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Hide ``hidden`` in the carrier and save the result to ``output_path``."""
    embedding = _checked_embedding(options, carrier_path, output_path)
    labels = hide_step_labels(options, output_path, carrier_path)
    report = _reporter(labels, on_progress)
    steps = container.hide_steps(encrypted=options.is_encrypted)
    first = len(labels) - len(steps) - 1  # 1 when the carrier is compressed first

    _check_password(options)

    if first:
        report(0)

    settings = options.mode_settings
    channel = embedding.open(carrier_path, engine.tuning(settings, options.method))

    if container.framing_size(channel, hidden.name, options.cipher) > channel.capacity(0):
        method = catalog.method(options.method).name
        raise CapacityError(f"{carrier_path.name} is too small to hide a file with {method}")

    limit = container.max_data_size(channel, hidden.name, options.cipher)

    if len(hidden.data) > limit:
        raise CapacityError(
            f"Payload is {len(hidden.data)} bytes, this carrier can hide at most {limit}"
        )

    if not options.is_research:
        _check_advised_limit(channel, hidden, options, carrier_path)

    embedded = container.hide(
        channel,
        hidden,
        method=options.method,
        cipher_id=options.cipher,
        password=options.password,
        order=embedding.body_order(engine.unit_order(settings), encrypted=options.is_encrypted),
        on_step=lambda step: report(first + steps.index(step)),
        profile=engine.kdf_profile(settings),
        is_masked=engine.is_masked(settings),
    )

    last_step = len(labels) - 1
    report(last_step)
    _save(embedded, output_path, before_replacing=lambda: report(last_step))


def reveal(
    carrier_path: Path,
    output_dir: Path,
    password: str | None = None,
    on_progress: ProgressCallback | None = None,
    research: ResearchReveal | None = None,
) -> RevealResult:
    """Extract the hidden file into ``output_dir``, made if missing. Never overwrites an
    existing file. Standard mode tries every method but the Experimental ones and every
    cipher at Standard's settings (``research`` left out), Research mode only what
    ``research`` names. Every failure to find
    a payload is the same :class:`NoHiddenDataError`."""
    _check_carrier_file(carrier_path)

    if research is not None:
        _check_research_reveal(research)

        if research.cipher == CipherId.NONE:
            password = None  # a plain frame takes none: no key steps

    settings = STANDARD_SETTINGS if research is None else research.settings
    labels = reveal_step_labels(password, research)
    report = _reporter(labels, on_progress)
    steps = container.reveal_steps(encrypted=bool(password))
    method = None if research is None else research.method
    groups, missing = _open_groups(carrier_path, engine.tuning(settings, method), method)

    try:
        revealed = container.reveal(
            groups,
            password,
            lambda step: report(steps.index(step)),
            engine.kdf_profile(settings),
            None if research is None else research.cipher,
            engine.is_masked(settings),
        )
    except NoHiddenDataError as exc:
        # One of the channels skipped for a missing extra may hold the payload. A payload
        # found damaged needs no hint.
        if not missing or not str(exc).startswith(payload.NOT_FOUND_MESSAGE):
            raise

        hint = ". ".join(h for h in (exc.hint, *missing) if h)
        raise NoHiddenDataError(payload.NOT_FOUND_MESSAGE, hint) from exc

    report(len(labels) - 1)
    hidden = revealed.file
    target = payload.output_path(output_dir, hidden.name)

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, NotADirectoryError) as exc:
        in_the_way = next(p for p in (output_dir, *output_dir.parents) if p.exists())
        raise CipherStitchError(f"{in_the_way} is a file, not a folder") from exc

    try:
        file = target.open("xb")
    except FileExistsError as exc:
        raise CipherStitchError(
            f"{target.name} already exists in {target.parent.absolute()}. "
            "Choose another folder or move that file"
        ) from exc

    try:
        with file:
            file.write(hidden.data)
    except BaseException:
        target.unlink()  # a partial file would pass for the hidden one
        raise

    return RevealResult(
        path=target, size=len(hidden.data), method=revealed.method, cipher=revealed.cipher
    )


def capacity(carrier_path: str | Path, *, research: bool = False) -> int:
    """The carrier's room for a payload in the mode, in bytes: :func:`mode_capacity`'s size.
    The 16-byte nonce or salt, the frame, the file name and any encryption overhead come out
    of that room."""
    return mode_capacity(Path(carrier_path), research=research).size


def mode_capacity(
    carrier_path: Path, *, research: bool = False, with_extensions: bool = True
) -> ModeCapacity:
    """The mode's default method for the carrier and what a payload may take with it: in
    Standard mode the advised size, Standard's limit, which is at most the capacity, and in
    Research mode the capacity at Research's default settings. Raises
    :class:`NotAvailableError` with the reason when the mode can't hide in the carrier.
    ``with_extensions`` (Research mode) lets an extension's method be the default for a file
    no built-in method takes, which loads the extensions."""
    _check_carrier_file(carrier_path)
    found, embedding = availability.default_embedding(
        carrier_path, is_research=research, with_extensions=research and with_extensions
    )
    options = HideOptions(found.kind, embedding.method, is_research=research)
    reason = unavailable_reason(options, carrier_path)

    if reason is not None:
        raise NotAvailableError(_missing_extra(found, carrier_path, research) or reason)

    space = _space(embedding, carrier_path, engine.tuning(STANDARD_SETTINGS, embedding.method))
    total = space.capacity(0)
    is_precover = precover.is_precover(carrier_path)
    advised = risks.advised_size(embedding.method, space.elements, total, is_precover=is_precover)
    size = total if research or advised is None else advised

    return ModeCapacity(embedding.method, size)


def _missing_extra(carrier: catalog.Carrier, carrier_path: Path, is_research: bool) -> str | None:
    """The install hint of a method the mode offers for the carrier that lacks only an extra:
    on a core-only install, a JPEG needs DCT's jpeg extra, which says more than why LSB is off.
    """
    for embedding in availability.usable_embeddings(carrier, carrier_path):
        if is_research or standard.method_reason(embedding.method) is None:
            hint = availability.method_reason(embedding.method, carrier_path)

            if hint is not None:  # usable: implemented and takes the suffix, so an extra
                return hint

    return None


def hide_file(
    carrier_path: str | Path,
    secret_path: str | Path,
    output_path: str | Path,
    password: str | None = None,
    *,
    research: bool = False,
) -> None:
    """Hide a file in a carrier, encrypted and scattered where possible. The method is the
    carrier type's first one that fits. Standard mode needs a strong ``password``. With
    ``research``, a file hidden without a password is plain, and a password must still be
    strong: Research's default ``crypto.minpw`` (lower it through :func:`hide_options`)."""
    carrier, secret, output = Path(carrier_path), Path(secret_path), Path(output_path)
    cipher = catalog.DEFAULT_CIPHER if password is not None or not research else CipherId.NONE
    options = hide_options(carrier, output, cipher, is_research=research)
    options = replace(options, password=password)
    hide(carrier, read_payload(secret), output, options)


def hide_options(
    carrier_path: Path,
    output_path: Path,
    cipher: CipherKey,
    *,
    method: MethodKey | None = None,
    carrier: CarrierKind | None = None,
    is_research: bool = False,
    settings: Settings = STANDARD_SETTINGS,
) -> HideOptions:
    """Options (without the password) for hiding in ``carrier_path``, checked before any
    work: by default the method is the carrier type's first one the mode (and Research's
    ``settings``) allows that writes ``output_path``'s format."""
    found = availability.carrier_for_path(carrier_path, is_research=is_research)

    if carrier is not None and (found is None or found.kind != carrier):
        label = catalog.carrier(carrier).label
        suffixes = availability.carrier_suffixes(carrier, is_research=is_research)
        raise UnsupportedFormatError(f"{label} carriers are {', '.join(suffixes)} files")

    if method is None:
        found, embedding = availability.default_embedding(
            carrier_path,
            output=output_path,
            is_research=is_research,
            settings=settings,
            with_extensions=is_research,
        )
        method = embedding.method

    if found is None:
        raise availability.unsupported_carrier(carrier_path)

    options = HideOptions(found.kind, method, cipher, is_research=is_research, settings=settings)
    _checked_embedding(options, carrier_path, output_path)

    return options


def read_payload(path: Path) -> HiddenFile:
    """The file to hide, read whole, once its name passes :func:`payload.validate_name_to_hide`.
    A folder, a named pipe or a device is refused first: reading one fails in the system's
    words, or blocks."""
    problem = availability.regular_file_problem(path)

    if problem is not None:
        raise CipherStitchError(problem)

    payload.validate_name_to_hide(path.name)

    return HiddenFile(name=path.name, data=path.read_bytes())


def reveal_file(
    carrier_path: str | Path, output_dir: str | Path, password: str | None = None
) -> Path:
    """Extract the hidden file into ``output_dir``, made if missing, and return its path:
    Standard mode's Reveal."""
    return reveal(Path(carrier_path), Path(output_dir), password).path


def _check_research_reveal(research: ResearchReveal) -> None:
    """Before opening anything: an unknown method, an unimplemented one, or one whose extra
    is missing, could never have hidden the payload, and a cipher that is unknown or misses
    its extra fails late in reveal. Unknown ids load the extensions to look there."""
    reason = (
        availability.unknown_method_reason(research.method)
        or availability.method_reason(research.method, None)
        or availability.unknown_cipher_reason(research.cipher)
        or availability.cipher_reason(research.cipher)
    )

    if reason is not None:
        raise NotAvailableError(reason)


def _refuse_unknown_ids(options: HideOptions) -> None:
    """Standard mode looks up no id that isn't a built-in's, which would load the extensions."""
    method = standard.unknown_method_reason(options.method)
    reason = method or standard.unknown_cipher_reason(options.cipher)

    if reason is not None:
        raise NotAvailableError(reason)


def _required(space: Capacity, name: str, size: int, cipher_id: CipherKey) -> int:
    """The carrier space a ``size``-byte file named ``name`` takes, framing and sealing
    included: more than the capacity when even the framing does not fit."""
    return container.framing_size(space, name, cipher_id) + size


def _check_password(options: HideOptions) -> None:
    """Standard mode needs a strong password, Research mode the strength its settings set."""
    if not options.is_encrypted:
        return

    minimum = options.min_password_strength

    if password_strength(options.password or "") < minimum:
        raise CipherStitchError(standard.weak_password(minimum, is_research=options.is_research))


def _check_advised_limit(
    channel: Capacity, hidden: HiddenFile, options: HideOptions, carrier_path: Path
) -> None:
    """Standard mode's limit is the advised size. A method without advice has none."""
    allowed = risks.advised_size(
        options.method,
        _element_count(channel),
        channel.capacity(0),
        is_precover=precover.is_precover(carrier_path),
    )
    required = _required(channel, hidden.name, len(hidden.data), options.cipher)
    excess = 0 if allowed is None else required - allowed

    if excess > 0:
        raise CapacityError(standard.over_limit(f"{excess} bytes", allowed))


def _checked_embedding(options: HideOptions, carrier_path: Path, output_path: Path) -> Embedding:
    """The embedding of ``options``, if it can hide in ``carrier_path`` and write
    ``output_path``'s format into an existing folder, as a file."""
    _check_carrier_file(carrier_path)
    reason = unavailable_reason(options, carrier_path)

    if reason is not None:
        raise NotAvailableError(reason)

    embedding = methods.get(options.method)

    if embedding is None:
        raise NotAvailableError(f"The method {options.method} is not available")

    allowed = embedding.output_suffixes(carrier_path)

    if output_path.suffix.lower() not in allowed:
        raise UnsupportedFormatError(f"Output must be one of: {', '.join(allowed)}")

    if not output_path.parent.is_dir():  # else saving fails only after all the work
        raise CipherStitchError(f"Can't save in {output_path.parent}: there is no such folder")

    if output_path.is_dir():
        raise CipherStitchError(f"Can't save as {output_path.name}: there is a folder of that name")

    return embedding


def _check_carrier_file(path: PurePath) -> None:
    """Before any reader opens the carrier (availability.regular_file_problem)."""
    problem = availability.regular_file_problem(path)

    if problem is not None:
        raise UnsupportedFormatError(problem)


def _open_groups(
    carrier_path: Path, tuning: Tuning, method: MethodKey | None = None
) -> tuple[list[Group], tuple[str, ...]]:
    """Every channel that may hold data in the carrier, or only ``method``'s, and the install
    hints of the channels skipped for a missing extra. A channel that cannot read this file
    (while another can) is skipped too."""
    groups: list[Group] = []
    skipped: list[NotAvailableError | UnsupportedFormatError] = []

    for opener, candidates in _reveal_candidates(carrier_path, method):
        try:
            groups.append((opener(carrier_path, tuning), candidates))
        except (NotAvailableError, UnsupportedFormatError) as exc:
            skipped.append(exc)

    if not groups:
        raise skipped[0]

    hints = (str(exc) for exc in skipped if isinstance(exc, NotAvailableError))

    return groups, tuple(dict.fromkeys(hints))


def _reveal_candidates(
    carrier_path: Path, method: MethodKey | None
) -> list[tuple[Opener, tuple[container.Candidate, ...]]]:
    """The candidates Standard's reveal tries (``method`` left out), or Research's, which
    tries only ``method``'s."""

    def is_tried(candidate: container.Candidate) -> bool:
        return standard.reveals(candidate) if method is None else candidate.method == method

    every = methods.for_reveal(carrier_path, method)
    chosen = [(opener, tuple(filter(is_tried, candidates))) for opener, candidates in every]
    chosen = [(opener, candidates) for opener, candidates in chosen if candidates]

    if not chosen:
        kind = availability.file_kind(carrier_path)
        hidden_with = "" if method is None else f" hidden with {catalog.method(method).name}"
        raise UnsupportedFormatError(f"{kind} files can't hold a payload{hidden_with}")

    return chosen


def _save(embedded: Embedded, output_path: Path, before_replacing: Callable[[], None]) -> None:
    """Write a hidden temporary file next to ``output_path``, then move it into place: an
    aborted or failed save leaves the output as it was. A symlink's target is replaced, and
    the link stays a link."""
    target = output_path.resolve()
    # Not named after the output: a name near the length limit would leave no room.
    partial = target.with_name(f".{secrets.token_hex(4)}.partial{target.suffix}")

    try:
        embedded.save(partial)
        before_replacing()
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


_FORMAT_NAMES = {**image.OUTPUT_FORMATS, **jpeg.OUTPUT_FORMATS}


def _writing_label(output_path: Path) -> str:
    suffix = output_path.suffix.lower()

    return f"Writing {_FORMAT_NAMES.get(suffix, suffix[1:].upper())}"


def _reporter(
    labels: tuple[str, ...], on_progress: ProgressCallback | None
) -> Callable[[int], None]:
    def report(index: int) -> None:
        if on_progress is not None:
            on_progress(index, len(labels), labels[index])

    return report
