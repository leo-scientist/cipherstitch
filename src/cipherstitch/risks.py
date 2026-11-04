"""What makes a payload easier to detect (docs/security.md#staying-inconspicuous).

Every risk is advice: none of them blocks hiding.
"""

import functools
import math
from pathlib import Path
from typing import NamedTuple

from cipherstitch import catalog, container, methods
from cipherstitch.catalog import MethodId, MethodKey
from cipherstitch.media import image, jpeg, precover
from cipherstitch.media.jpeg_header import Header
from cipherstitch.methods import Tuning, ecc, lsb
from cipherstitch.methods.lsb import LsbChannel
from cipherstitch.sizes import format_size

ROOT_RATES: dict[MethodKey, int] = {MethodId.LSB: 76, MethodId.DCT: 14, MethodId.AUDIO_LSB: 76}
"""Statistical methods: the advised payload in bits per square root of the cover's counted
values, where a trained detector's error rate on 512 × 512 photos falls to 0.45
(benchmarks/detectability/results/round-3/). Audio LSB takes image LSB's, unmeasured."""
CALIBRATED_ELEMENTS: dict[MethodKey, int] = {
    MethodId.LSB: 786_432,
    MethodId.DCT: 63_441,
    MethodId.AUDIO_LSB: 786_432,
}
"""The counted values of the covers the rates were measured on: a 512 × 512 PNG, and the
median quality 75 JPEG of that size. Smaller covers were easier to detect at the same rate,
so below this size the advice keeps the share of the capacity it has here."""
PRECOVER_ROOT_RATE = 39
PRECOVER_CALIBRATED_ELEMENTS = 87_863
"""DCT from a camera RAW, PNG or TIFF, which CipherStitch compresses itself with side
information: its own rate, measured on TIFFs, and the median non-zero AC count of their
quality 90 JPEGs."""
_BITS_PER_BYTE = 8
_FLAT_COVERS: dict[MethodKey, str] = {
    MethodId.LSB: "of the image is flat colour",
    MethodId.AUDIO_LSB: "of the recording is digital silence",
}
_FLAT_CACHE_SIZE = 8
_JPEG = (
    "The carrier is a JPEG, and LSB changes to its decoded pixels are easy to detect. "
    f"Use {catalog.method(MethodId.DCT).name} or a cover that was never a JPEG."
)
_LOSSY_FORMATS = {  # Pillow's format names; MPO is a JPEG holding several pictures
    "JPEG": _JPEG,
    "MPO": _JPEG,
    "WEBP": (
        "The carrier is a WebP, which is usually lossy, and LSB changes to its decoded pixels "
        "are easy to detect. Use a cover that was never lossy-compressed."
    ),
}
LOSSY_FORMATS = frozenset(_LOSSY_FORMATS)
LOSSY_SUFFIXES = (".jpg", ".jpeg", ".webp")
JPEG_COMPRESSIONS = frozenset({"jpeg", "tiff_jpeg"})  # Pillow's names for a TIFF's
_LOSSY_PRECOVER = (
    "The carrier is a JPEG-compressed TIFF. Compressing it to JPEG again leaves traces of both. "
    "Use a RAW or a cover that was never lossy-compressed."
)
_GREYSCALE = (
    "The carrier is greyscale. It will be saved in colour, and the tinted pixels stand out."
)
_PALETTE = "The carrier uses a colour palette. It will be saved in full colour, which stands out."
_ONE_PICTURE = (
    "The carrier holds several frames or pages. Only the first will be saved, and the missing "
    "ones stand out."
)
_STRUCTURAL: dict[MethodKey, str] = {
    MethodId.OBJECT_STREAM: (
        "Anyone who looks inside the file can find the extra object and see that it holds no "
        "real application data."
    ),
    MethodId.WHITESPACE: "Anyone who looks inside the file will see the trailing whitespace.",
    MethodId.ZERO_WIDTH: (
        "Any tool that looks for zero-width characters will find them, and their number gives "
        "away the payload's size."
    ),
    MethodId.LSB_REPLACEMENT: (
        "WS, SPA and chi-square tests detect sequential LSB replacement even at small payloads, "
        "and its changes fill the top of the picture."
    ),
}
EXTENSION_RISK = (
    "This method comes from an extension. CipherStitch hasn't measured how detectable it is."
)
"""The Detection row's line for an extension's method that gives none of its own."""


class Fill(NamedTuple):
    """The carrier space a payload takes, framing included, out of the carrier's
    ``capacity``, and the most it should take (:func:`advised_size`)."""

    required: int
    capacity: int
    advised: int | None


class _Cover(NamedTuple):
    flat_share: float
    units: int
    elements: int  # counted values
    plain: int  # capacity with no prefix
    salted: int  # capacity after a nonce or a salt


def detection_risks(
    method: MethodKey,
    *,
    carrier: Path | None = None,
    fill: Fill | None = None,
    tuning: Tuning = methods.STANDARD,
) -> tuple[str, ...]:
    """The risks of hiding with ``method``, in ``carrier`` and with ``fill`` when given, by
    the channels' ``tuning``."""
    risks = (
        _fill_risk(fill),
        *_cover_risks(method, carrier),
        _flat_risk(method, carrier, fill, tuning),
        _structural_risk(method, tuning),
    )

    return tuple(risk for risk in risks if risk is not None)


def _structural_risk(method: MethodKey, tuning: Tuning) -> str | None:
    parities: dict[MethodKey, int] = {
        MethodId.PHASE_CODING: tuning.phase.parity,
        MethodId.ECHO_HIDING: tuning.echo.parity,
    }

    if method not in parities:
        return _STRUCTURAL.get(method)

    return (
        "Anyone who knows the method can look for its error-correction codes, which reveals "
        f"the payload and its size to within {ecc.CODEWORD - parities[method]} bytes."
    )


def advised_size(
    method: MethodKey, elements: int | None, capacity: int, *, is_precover: bool = False
) -> int | None:
    """The most carrier space a payload should take with a statistical ``method``, framing
    included, in a cover of ``elements`` counted values (its channel's ``element_count``).
    ``is_precover``: the carrier is a RAW, PNG or TIFF, which DCT compresses itself."""
    if method not in ROOT_RATES or elements is None:
        return None

    if is_precover and method == MethodId.DCT:
        return _root_size(PRECOVER_ROOT_RATE, PRECOVER_CALIBRATED_ELEMENTS, elements, capacity)

    return _root_size(ROOT_RATES[method], CALIBRATED_ELEMENTS[method], elements, capacity)


def _root_size(rate: int, calibrated: int, elements: int, capacity: int) -> int:
    """``rate`` bits per square root of ``elements``, at most ``capacity`` bytes. By the
    square-root law a payload grows only that fast, at the same detectability, as a cover
    from one source grows. Below ``calibrated`` values it shrinks in proportion to the cover
    instead."""
    bits = rate * elements / math.sqrt(max(elements, calibrated))

    return min(int(bits) // _BITS_PER_BYTE, capacity)


def is_conspicuous(required: int, advised: int | None) -> bool:
    """Whether a payload taking ``required`` bytes of the carrier is larger than advised."""
    return advised is not None and required > advised


def _fill_risk(fill: Fill | None) -> str | None:
    if fill is None or fill.advised is None or fill.required > fill.capacity:
        return None  # a payload too large to hide is reported as an error instead

    if not is_conspicuous(fill.required, fill.advised):
        return None

    taken, advised = format_size(fill.required), format_size(fill.advised)

    if taken == advised:  # rounded alike, the advice would read as already met
        taken, advised = f"{fill.required} B", f"{fill.advised} B"

    return (
        f"The payload takes {taken} of the carrier, which makes it easier to detect. "
        f"Keep it to {advised} or less."
    )


def _cover_risks(method: MethodKey, carrier: Path | None) -> tuple[str | None, ...]:
    if carrier is None:
        return ()

    if method in (MethodId.LSB, MethodId.LSB_REPLACEMENT):
        return _image_risks(image.info(carrier))

    if method == MethodId.DCT and precover.is_precover(carrier):
        return (
            _LOSSY_PRECOVER if is_lossy_precover(carrier) else None,
            _ONE_PICTURE if keeps_one_picture(method, carrier) else None,
        )

    if method == MethodId.DCT:
        return (_rewritten_jpeg_risk(jpeg.header(carrier)),)

    return ()


def is_lossy_precover(carrier: Path) -> bool:
    """Whether DCT would compress ``carrier`` a second time: a JPEG-compressed TIFF."""
    if not _is_picture_precover(carrier):
        return False

    return image.info(carrier).compression in JPEG_COMPRESSIONS


def keeps_one_picture(method: MethodKey, carrier: Path) -> bool:
    """Whether ``method`` keeps only the first of several pictures in ``carrier``: an
    animation's frames or a TIFF's pages. Image LSB and a precover's compression decode only
    the first."""
    is_decoded = method in (MethodId.LSB, MethodId.LSB_REPLACEMENT) or (
        method == MethodId.DCT and _is_picture_precover(carrier)
    )

    return is_decoded and image.info(carrier).has_several_frames


def _is_picture_precover(carrier: Path) -> bool:
    """A PNG or TIFF that DCT compresses itself: a RAW is developed by rawpy instead."""
    return precover.is_precover(carrier) and carrier.suffix.lower() not in catalog.RAW_EXTENSIONS


def _image_risks(details: image.ImageInfo) -> tuple[str | None, ...]:
    """Image LSB decodes the cover's first picture and always writes it in full colour."""
    return (
        _LOSSY_FORMATS.get(details.format or ""),
        _GREYSCALE if details.is_greyscale else None,
        _PALETTE if details.has_palette else None,
        _ONE_PICTURE if details.has_several_frames else None,
    )


def _rewritten_jpeg_risk(cover: Header) -> str | None:
    """DCT writes a baseline JPEG without restart markers: jpeglib can write neither."""
    if cover.is_progressive and cover.has_restart_interval:
        kind = "a progressive JPEG with restart markers"
    elif cover.is_progressive:
        kind = "a progressive JPEG"
    elif cover.has_restart_interval:
        kind = "a JPEG with restart markers"
    else:
        return None

    return (
        f"The carrier is {kind}, but the result will be a baseline JPEG without restart "
        "markers, unlike other pictures from the same camera or app."
    )


def flat_cover(
    method: MethodKey,
    carrier: Path,
    required: int | None = None,
    tuning: Tuning = methods.STANDARD,
) -> str | None:
    """The flat part of ``carrier`` (plain colour, digital silence), e.g. ``18% of the image
    is flat colour``, if LSB ``method`` may have to change some of its values with a payload
    taking ``required`` bytes of the carrier, or the advised size when not given. It does
    only in code blocks whose values are all flat, which get likelier as the payload or the
    flat part grows, unless Research's ``tuning`` makes flat values cost no more than others:
    then any of them may change. It decodes the carrier once per version of the file and
    tuning."""
    if method not in _FLAT_COVERS:
        return None

    flat = _FLAT_COVERS[method]
    stat = carrier.stat()
    cover = _cover(
        method, carrier, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, tuning
    )

    if required is None:
        rate, calibrated = ROOT_RATES[method], CALIBRATED_ELEMENTS[method]
        required = _root_size(rate, calibrated, cover.elements, cover.plain)

    body = max(required - (cover.plain - cover.salted), 0)
    blocks = lsb.flat_blocks(cover.units, body, cover.flat_share, tuning.coding.format)
    is_avoided = method != MethodId.LSB or _avoids_flat(tuning.image)

    if cover.flat_share == 0 or (is_avoided and round(blocks) < 1):
        return None

    percent = round(100 * cover.flat_share)

    return f"{percent}% {flat}" if percent else f"<1% {flat}"


def _avoids_flat(rules: lsb.ImageRules) -> bool:
    """Whether image LSB's costs keep changes off flat values: wet costs, or HILL's own, which
    are about 1 / ε there. Without wet costs, the others let changes land there: uniform
    costs as cheaply as anywhere, S-UNIWARD's at a few times the median, and WOW's and
    MiPOD's wherever their windows reach texture. Nothing says an extension's cost function
    (an id) keeps them off, so it counts as one of the others."""
    return rules.is_wet or rules.costs == lsb.CostFunction.HILL


@functools.lru_cache(maxsize=_FLAT_CACHE_SIZE)
def _cover(
    method: MethodKey,
    path: Path,
    _ino: int,
    _mtime_ns: int,
    _ctime_ns: int,
    _size: int,
    tuning: Tuning,
) -> _Cover:
    """Cached per version of the file: a same-size rewrite that keeps the mtime (``cp -p``)
    still changes the inode or the ctime."""
    embedding = methods.get(method)

    if embedding is None:
        raise ValueError(f"{method} is not implemented")

    channel = embedding.open(path, tuning)

    if not isinstance(channel, LsbChannel):
        raise TypeError(f"{method} is not an LSB method")

    salted = channel.capacity(container.NONCE_SIZE)

    return _Cover(
        channel.flat_share(), channel.unit_count, channel.element_count, channel.capacity(0), salted
    )


def _flat_risk(
    method: MethodKey, carrier: Path | None, fill: Fill | None, tuning: Tuning
) -> str | None:
    if carrier is None or fill is None or fill.required > fill.capacity:
        return None

    flat = flat_cover(method, carrier, fill.required, tuning)

    if flat is None:
        return None

    return f"{flat}, and some LSB changes may land there, where they are easy to spot."
