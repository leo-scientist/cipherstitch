"""Camera RAW carriers (rawpy and LibRaw, the ``raw`` extra): developed to upright 16-bit
sRGB, which DCT compresses to JPEG itself (:mod:`cipherstitch.media.precover`)."""

from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from cipherstitch import catalog
from cipherstitch.errors import NotAvailableError, UnsupportedFormatError
from cipherstitch.media import image

if TYPE_CHECKING:
    from rawpy._rawpy import RawPy

_QUARTER_TURNS = frozenset({5, 6})  # LibRaw's flip values that swap width and height


def describe(path: Path) -> str:
    """E.g. ``6000 × 4000 · CR2``, upright."""
    with _opened(path) as raw:
        width, height = _size(raw)

    return f"{width} × {height} · {path.suffix[1:].upper()}"


def develop(path: Path) -> npt.NDArray[np.uint16]:
    """The picture as rows × columns × RGB: LibRaw's defaults (AHD demosaicing, automatic
    brightness) with the camera's white balance, in sRGB and turned upright."""
    rawpy = _rawpy(path)

    with _opened(path) as raw:
        width, height = _size(raw)
        image.check_size(path.name, width, height)  # opening reads no pixels yet

        try:
            pixels = raw.postprocess(
                use_camera_wb=True,
                output_color=rawpy.ColorSpace.sRGB,
                output_bps=16,
                gamma=(2.4, 12.92),
            )
        except rawpy.LibRawError as exc:
            raise _unreadable(path) from exc

    # rawpy's stub types every result as uint8, whatever ``output_bps``.
    return np.asarray(pixels, dtype=np.uint16)


def _opened(path: Path) -> "RawPy":
    rawpy = _rawpy(path)

    try:
        opened: RawPy = rawpy.imread(str(path))  # anything but a str is read as a file object
    except (rawpy.LibRawError, rawpy.NotSupportedError) as exc:
        raise _unreadable(path) from exc

    return opened


def _size(raw: "RawPy") -> tuple[int, int]:
    sizes = raw.sizes

    if sizes.flip in _QUARTER_TURNS:
        return sizes.iheight, sizes.iwidth

    return sizes.iwidth, sizes.iheight


def _unreadable(path: Path) -> UnsupportedFormatError:
    return UnsupportedFormatError(f"{path.name} is not a readable RAW file")


def _rawpy(path: Path) -> Any:
    try:
        import rawpy
    except ImportError as exc:
        raise NotAvailableError(catalog.RAWPY.install_hint(path.suffix[1:].upper())) from exc

    return rawpy
