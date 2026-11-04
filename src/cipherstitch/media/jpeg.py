"""JPEG carriers at the coefficient level (jpeglib, the ``jpeg`` extra).

Only the quantized DCT coefficients change, so no pixel is decoded or re-compressed:
reading and writing them is bit-exact. A precover is compressed by
:mod:`cipherstitch.media.precover`, never by libjpeg, which only writes its coefficients.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from PIL import Image

from cipherstitch import catalog
from cipherstitch.errors import NotAvailableError, UnsupportedFormatError
from cipherstitch.media import image, jpeg_header
from cipherstitch.media.jpeg_header import Header

OUTPUT_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG"}
_BLOCK = 64  # coefficients of one 8x8 block; index 0 is the DC term
_OPTIMIZED_TABLES = ["+OPTIMIZE_CODING"]


class Subsampling(StrEnum):
    """How much coarser than luminance a picture's chroma is sampled, across and down."""

    H2V2 = "4:2:0"
    H2V1 = "4:2:2"
    H1V1 = "4:4:4"

    @property
    def factors(self) -> tuple[int, int]:
        """The pixels across and down that one chroma sample covers."""
        return _FACTORS[self]


_FACTORS = {Subsampling.H2V2: (2, 2), Subsampling.H2V1: (2, 1), Subsampling.H1V1: (1, 1)}


class Tables(StrEnum):
    """The Huffman tables a JPEG is written with."""

    MATCH = "match"  # the standard ones when the cover has them, optimized ones otherwise
    STANDARD = "standard"
    OPTIMIZED = "optimized"


@dataclass(frozen=True, slots=True)
class Component:
    """A colour component's grid of 8x8 blocks and its quantization steps (64, row-major,
    the DC first)."""

    rows: int
    columns: int
    steps: npt.NDArray[np.uint16]


@dataclass(frozen=True, slots=True)
class Coefficients:
    """Every AC coefficient of every component, block by block (libjpeg's own int16), and
    every block's DC term, component after component."""

    ac: npt.NDArray[np.int16]
    components: tuple[Component, ...]
    dc: npt.NDArray[np.int16]


def read_coefficients(path: Path) -> Coefficients:
    jpeg = _read(path)
    components = _components(jpeg)
    grids = (
        Component(component.shape[0], component.shape[1], jpeg.qt[table].reshape(-1))
        for component, table in zip(components, jpeg.quant_tbl_no, strict=True)
    )

    ac = np.concatenate([_ac(c) for c in components])
    dc = np.concatenate([_dc(c) for c in components])

    return Coefficients(ac, tuple(grids), dc)


def header(path: Path) -> Header:
    """The segments before the first scan, read without decoding anything."""
    with path.open("rb") as file:
        return jpeg_header.read(file, path.name)


@dataclass(frozen=True, slots=True)
class EmbeddedJpeg:
    """The cover with ``ac`` in place of its AC coefficients, its APPn and COM segments kept
    unless ``keeps_segments`` is false."""

    source: Path
    ac: npt.NDArray[np.int16]
    tables: Tables = Tables.MATCH
    keeps_segments: bool = True

    def save(self, path: Path) -> None:
        jpeg = _read(self.source)
        start = 0

        for component in _components(jpeg):
            blocks = component.reshape(-1, _BLOCK)
            end = start + blocks.shape[0] * (_BLOCK - 1)
            blocks[:, 1:] = self.ac[start:end].reshape(-1, _BLOCK - 1)
            component[...] = blocks.reshape(component.shape)
            start = end

        if not self.keeps_segments:
            jpeg.markers = []  # jpeglib writes the cover's APPn and COM segments after its JFIF

        # libjpeg writes a baseline JPEG: the cover's own kind of Huffman tables keeps its size.
        cover = header(self.source)
        is_standard = {
            Tables.MATCH: cover.has_standard_tables,
            Tables.STANDARD: True,
            Tables.OPTIMIZED: False,
        }[self.tables]
        jpeg.write_dct(str(path), flags=[] if is_standard else _OPTIMIZED_TABLES)
        written = path.read_bytes()
        kept = self.keeps_segments
        path.write_bytes(jpeg_header.matched(written, cover, path.name, keeps_segments=kept))


@dataclass(frozen=True, slots=True)
class EmbeddedCompressedJpeg:
    """A JPEG written from coefficients alone: a precover's, its chroma sampled as
    ``subsampling`` says when it has colour."""

    ac: npt.NDArray[np.int16]
    dc: npt.NDArray[np.int16]  # every block's DC term, component after component
    components: tuple[Component, ...]
    size: tuple[int, int]  # width, height
    icc_profile: bytes | None
    subsampling: Subsampling

    def save(self, path: Path) -> None:
        jpeglib = _jpeglib()
        grids = []
        start = 0

        for component in self.components:
            end = start + component.rows * component.columns
            blocks = np.empty((end - start, _BLOCK), dtype=np.int16)
            blocks[:, 0] = self.dc[start:end]
            blocks[:, 1:] = self.ac[start * (_BLOCK - 1) : end * (_BLOCK - 1)].reshape(
                -1, _BLOCK - 1
            )
            grids.append(blocks.reshape(component.rows, component.columns, 8, 8))
            start = end

        tables = np.stack([component.steps.reshape(8, 8) for component in self.components[:2]])
        jpeg = jpeglib.from_dct(*grids, qt=tables)
        # from_dct declares whole blocks and infers 4:2:2 for a picture 8 pixels high.
        jpeg.width, jpeg.height = self.size

        if len(grids) > 1:
            across, down = self.subsampling.factors
            jpeg.samp_factor = np.array([[down, across], [1, 1], [1, 1]])  # jpeglib's order

        jpeg.write_dct(str(path), flags=_OPTIMIZED_TABLES)
        segments = jpeg_header.icc_segments(self.icc_profile)
        path.write_bytes(jpeg_header.numbered(path.read_bytes(), path.name, segments))


def _ac(component: Any) -> npt.NDArray[np.int16]:
    return np.asarray(component, dtype=np.int16).reshape(-1, _BLOCK)[:, 1:].reshape(-1)


def _dc(component: Any) -> npt.NDArray[np.int16]:
    return np.asarray(component, dtype=np.int16).reshape(-1, _BLOCK)[:, 0].copy()


def _components(jpeg: Any) -> list[Any]:
    return [component for component in (jpeg.Y, jpeg.Cb, jpeg.Cr) if component is not None]


def _read(path: Path) -> Any:
    jpeglib = _jpeglib()
    _check_size(path)

    try:
        jpeg = jpeglib.read_dct(str(path))
    except OSError as exc:
        raise UnsupportedFormatError(f"{path.name} is not a readable JPEG") from exc

    if jpeg.has_black:
        raise UnsupportedFormatError(f"{path.name}: CMYK JPEG is not supported")

    return jpeg


def _check_size(path: Path) -> None:
    """The coefficient arrays follow the header's dimensions: refuse what Pillow refuses to
    decode, before allocating them."""
    try:
        with Image.open(path, formats=["JPEG"]) as opened:
            width, height = opened.size
    except Image.DecompressionBombError as exc:
        raise UnsupportedFormatError(f"{path.name} is too large to read") from exc
    except OSError as exc:
        if exc.errno is not None:  # a missing or unreadable file, not a broken one
            raise

        raise UnsupportedFormatError(f"{path.name} is not a readable JPEG") from exc

    image.check_size(path.name, width, height)


def _jpeglib() -> Any:
    try:
        import jpeglib
    except ImportError as exc:
        raise NotAvailableError(catalog.JPEGLIB.install_hint("DCT coefficients (JPEG)")) from exc

    return jpeglib
