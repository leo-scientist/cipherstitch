"""Standard mode, the default: only the Recommended carriers, methods and ciphers, covers that
hiding changes by the hidden bits alone (or, for DCT, compresses once), no larger payload than
the Detection row advises (``Estimate.limit``) and a strong password
(docs/user-guide.md#standard-and-research-mode). Research mode lifts the rest but keeps the
image and audio cover rules and the password minimum as settings the user may relax
(``limg.lossy``, ``laud.depth``, ``crypto.minpw``). The service enforces these rules; the app
mirrors them."""

from dataclasses import dataclass
from pathlib import Path

from cipherstitch import catalog, methods, risks
from cipherstitch.catalog import CarrierKind, CipherId, CipherKey, MethodId, MethodKey, Tier
from cipherstitch.container import Candidate, UnitOrder
from cipherstitch.errors import CipherStitchError
from cipherstitch.media import audio, image
from cipherstitch.password import STRENGTH_LABELS
from cipherstitch.sizes import format_size

MIN_PASSWORD_STRENGTH = 3
MIN_AUDIO_DEPTH = 16
OFF = "Off in Standard mode"
RESEARCH_OFF = "Off in the Research parameters"

_UNCONVERTED_MODES = frozenset({"RGB", "RGBA"})
_RESAMPLED_RAW_MODES = (";15", ";16")  # 16 bits per channel, or 15–16 bits per pixel
_LOSSY_COVER = "a lossy cover keeps compression traces that LSB changes break"
_CONVERTED_COVER = "LSB would save this cover in another colour format, which stands out"
_LOSSY_PRECOVER = "a JPEG-compressed TIFF keeps compression traces that compressing it again shows"
_ONE_PICTURE = "this cover holds several frames or pages, and only the first would be kept"


@dataclass(frozen=True, slots=True)
class CoverRules:
    """Which covers a mode refuses: Standard's rules, or Research's as its settings set them.
    ``is_standard`` adds the rules Research only warns about: nearly all-flat covers,
    JPEG-compressed TIFF precovers and covers of several frames or pages."""

    allows_lossy: bool = False  # JPEG, WebP, greyscale and palette covers for image LSB
    min_audio_depth: int = MIN_AUDIO_DEPTH
    is_standard: bool = True


STANDARD_COVERS = CoverRules()


def carrier_reason(kind: CarrierKind) -> str | None:
    if any(method.tier is Tier.RECOMMENDED for method in catalog.carrier(kind).methods):
        return None

    return f"{OFF}: no recommended method for this carrier"


def method_reason(method_id: MethodKey) -> str | None:
    unknown = unknown_method_reason(method_id)

    if unknown is not None:
        return unknown

    method = catalog.method(method_id)

    if method.tier is Tier.RECOMMENDED:
        return None

    return f"{OFF}: {method.name} is not a {Tier.RECOMMENDED} method"


def reveals(candidate: Candidate) -> bool:
    """Whether Standard's reveal tries ``candidate``. Only Research mode hides with the
    Experimental methods and in the sequential order. Each such method would cost another
    decode of the carrier and another key derivation with a password, and the order another
    header try per cipher."""
    if not catalog.is_builtin_method(candidate.method):
        return False

    is_experimental = catalog.method(candidate.method).tier is Tier.EXPERIMENTAL

    return not is_experimental and candidate.order is not UnitOrder.SEQUENTIAL


def cipher_reason(cipher_id: CipherKey) -> str | None:
    unknown = unknown_cipher_reason(cipher_id)

    if unknown is not None:
        return unknown

    cipher = catalog.cipher(cipher_id)

    if cipher.tier is Tier.RECOMMENDED:
        return None

    if cipher_id == CipherId.NONE:
        return f"{OFF}: every payload is encrypted"

    return f"{OFF}: {cipher.name} is not a {Tier.RECOMMENDED} cipher"


def unknown_method_reason(method_id: MethodKey) -> str | None:
    """Standard mode asks before any lookup of an id that isn't a built-in's, which would
    load the extensions (docs/architecture.md#extensions)."""
    if catalog.is_builtin_method(method_id):
        return None

    return f"Unknown method {method_id}. Extension methods run only in Research mode"


def unknown_cipher_reason(cipher_id: CipherKey) -> str | None:
    """As :func:`unknown_method_reason`."""
    if catalog.is_builtin_cipher(cipher_id):
        return None

    return f"Unknown cipher {cipher_id}. Extension ciphers run only in Research mode"


def cover_reason(
    method_id: MethodKey, carrier_path: Path, rules: CoverRules = STANDARD_COVERS
) -> str | None:
    """Why the mode's ``rules`` refuse this cover for ``method_id``: from its header, then
    from its content (decoded once per version of the file)."""
    try:
        problem = _cover_problem(method_id, carrier_path, rules)
    except (OSError, CipherStitchError):
        return None  # unreadable: opening the carrier reports why

    if problem is None:
        return None

    return f"{OFF if rules.is_standard else RESEARCH_OFF}: {problem}"


def weak_password(minimum: int, *, is_research: bool) -> str:
    """Why a password weaker than ``minimum`` (a zxcvbn score) is refused."""
    needs = "The Research parameters need" if is_research else "Standard mode needs"
    strength = STRENGTH_LABELS[minimum].lower()

    return f"{needs} a {strength} password. Try a few uncommon words or a longer random string"


def carrier_formats(kind: CarrierKind) -> tuple[str, ...]:
    """The carrier type's suffixes, in catalog order, that a Standard method can hide in."""
    carrier = catalog.carrier(kind)
    accepted = {suffix for method in carrier.methods for suffix in _standard_inputs(method.id)}

    return tuple(suffix for suffix in carrier.extensions if suffix in accepted)


def over_limit(excess: str, advised: int | None = None) -> str:
    """E.g. ``Payload is 66 bytes over the advised 8.2 KiB for this carrier``, or ``… over
    the advised limit for this carrier`` without the size."""
    limit = "limit" if advised is None else format_size(advised)

    return f"Payload is {excess} over the advised {limit} for this carrier"


def _standard_inputs(method_id: MethodKey) -> tuple[str, ...]:
    embedding = methods.get(method_id)

    if embedding is None or method_reason(method_id) is not None:
        return ()

    if method_id == MethodId.LSB:
        return tuple(s for s in embedding.inputs if s not in risks.LOSSY_SUFFIXES)

    return embedding.inputs


def _cover_problem(method_id: MethodKey, carrier_path: Path, rules: CoverRules) -> str | None:
    if method_id in (MethodId.LSB, MethodId.LSB_REPLACEMENT):
        problem = None if rules.allows_lossy else _image_problem(image.info(carrier_path))
    elif method_id == MethodId.AUDIO_LSB:
        depth = audio.info(carrier_path).depth
        problem = _shallow_audio(depth) if depth < rules.min_audio_depth else None
    elif method_id == MethodId.DCT:
        is_lossy = rules.is_standard and risks.is_lossy_precover(carrier_path)
        problem = _LOSSY_PRECOVER if is_lossy else None
    else:
        return None

    if problem or not rules.is_standard:
        return problem

    if risks.keeps_one_picture(method_id, carrier_path):
        return _ONE_PICTURE

    flat = risks.flat_cover(method_id, carrier_path)

    if flat is None:
        return None

    return f"{flat}, and even a payload of the advised size could change some of it"


def _shallow_audio(depth: int) -> str:
    return f"{depth}-bit samples make each change audible in quiet passages"


def _image_problem(details: image.ImageInfo) -> str | None:
    """Image LSB decodes the cover to 8-bit RGB(A) pixels and saves them losslessly."""
    if details.format in risks.LOSSY_FORMATS or details.compression in risks.JPEG_COMPRESSIONS:
        return _LOSSY_COVER

    if details.mode not in _UNCONVERTED_MODES or details.has_transparency_key:
        return _CONVERTED_COVER

    # Pillow decodes these to 8-bit RGB too, so LSB would save them with other sample sizes.
    if any(tag in (details.raw_mode or "") for tag in _RESAMPLED_RAW_MODES):
        return _CONVERTED_COVER

    return None
