"""The short description of a carrier file the capacity meter shows, e.g. ``2400 × 1600 · PNG``."""

from collections.abc import Callable
from pathlib import Path

from cipherstitch import catalog
from cipherstitch.catalog import CarrierKind
from cipherstitch.media import audio, docx, image, pdf, raw, text
from cipherstitch.sizes import format_size


def describe(path: Path, kind: CarrierKind) -> str:
    """By the reader of the carrier type, or by size for a suffix that only an extension's
    method takes, which no built-in reader knows."""
    if catalog.carrier_for_path(path) is None:
        return _describe_other(path)

    return _DESCRIBERS[kind](path)


def _describe_image(path: Path) -> str:
    """Through rawpy for a RAW: Pillow would describe a DNG by its thumbnail."""
    if path.suffix.lower() in catalog.RAW_EXTENSIONS:
        return raw.describe(path)

    return image.describe(path)


def _describe_document(path: Path) -> str:
    return pdf.describe(path) if path.suffix.lower() == ".pdf" else docx.describe(path)


def _describe_other(path: Path) -> str:
    """E.g. ``48.0 KiB · FXB``."""
    return f"{format_size(path.stat().st_size)} · {path.suffix[1:].upper()}"


_DESCRIBERS: dict[CarrierKind, Callable[[Path], str]] = {
    CarrierKind.IMAGE: _describe_image,
    CarrierKind.AUDIO: audio.describe,
    CarrierKind.DOCUMENT: _describe_document,
    CarrierKind.TEXT: text.describe,
}
