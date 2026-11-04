"""Image carriers: PNG, BMP, TIFF, JPEG or WebP, read by Pillow and written back losslessly."""

import contextlib
import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
from PIL import Image, ImageFile, ImageOps, PngImagePlugin, TiffImagePlugin, TiffTags
from PIL.ExifTags import IFD, Base

from cipherstitch.errors import UnsupportedFormatError
from cipherstitch.media import tiff_directory

OUTPUT_FORMATS = {".png": "PNG", ".bmp": "BMP", ".tif": "TIFF", ".tiff": "TIFF"}
# The formats image carriers come in. Pillow would pick any of its readers by content, and
# its EPS reader runs Ghostscript. Its JPEG reader opens a JPEG holding several pictures as MPO.
_FORMATS = ("PNG", "BMP", "TIFF", "JPEG", "WEBP")
DEEP_GREY_MODES = frozenset({"I;16", "I;16L", "I;16B"})  # Pillow's convert() clips them
_DEEP_SCALE = 257  # 16-bit samples to 0 … 255
_PALETTE_MODES = ("P", "PA")
_PNG_FIXED_POINT = 100_000  # PNG stores gamma and chromaticity in units of 1/100 000
_TIFF_TAGS = (  # what a TIFF says about the picture, not how it stores it
    Base.DocumentName,
    Base.ImageDescription,
    Base.Make,
    Base.Model,
    Base.PageName,
    Base.Software,
    Base.DateTime,
    Base.Artist,
    Base.HostComputer,
    Base.Copyright,
    Base.XMLPacket,
    Base.ImageResources,
)
# What a TIFF cover's EXIF in PNG output takes of them: XMP has a chunk of its own, and
# Photoshop data is no EXIF.
_PNG_EXIF_TAGS = tuple(
    tag for tag in _TIFF_TAGS if tag not in {Base.XMLPacket, Base.ImageResources}
)
# Lossless, and in every libtiff build (LZMA, Zstandard and WebP depend on how it was built).
_TIFF_COMPRESSIONS = frozenset({"tiff_lzw", "tiff_adobe_deflate", "tiff_deflate", "packbits"})
_PREDICTED_COMPRESSIONS = frozenset({"tiff_lzw", "tiff_adobe_deflate", "tiff_deflate"})
_PREDICTORS = frozenset({1, 2})  # none, or each value as its difference from the left one
# The types libtiff, which writes compressed TIFFs, takes for the copied tags: it crashes on
# a text tag stored as bytes, which some writers do.
_LIBTIFF_TYPES = {
    Base.XMLPacket: frozenset({TiffTags.BYTE, TiffTags.UNDEFINED}),
    Base.ImageResources: frozenset({TiffTags.BYTE, TiffTags.UNDEFINED}),
}
_LIBTIFF_TEXT_TYPES = frozenset({TiffTags.ASCII})
_XMP_KEY = "XML:com.adobe.xmp"  # the iTXt chunk that holds a PNG's XMP
# An orientation Pillow reads from XMP and turns a TIFF by while loading it.
_XMP_TURN = re.compile(rb'tiff:Orientation(="|>)[2-8]')
# What Pillow raises on EXIF it cannot parse, or on an offset it cannot follow (negative,
# or into a file it has closed).
_EXIF_ERRORS = (SyntaxError, struct.error, ValueError)
# What Pillow's writer raises on a value it read but cannot write back: it types each tag by
# its own table, not as the cover stored it.
_WRITE_ERRORS = (AttributeError, TypeError, struct.error, ValueError)
# What Pillow's readers let through on data they cannot decode, besides OSErrors of their
# own, which carry no error number (a missing or unreadable file has one).
_DECODER_ERRORS = (
    SyntaxError,
    TypeError,
    KeyError,
    IndexError,
    struct.error,
    EOFError,
    NotImplementedError,
    ValueError,
)
_PROFILE_SPACE = slice(16, 20)  # an ICC profile's colour space, in its header
_RGB_PROFILE = b"RGB "
_MAX_DPI = 1_000_000  # PNG and BMP store pixels per metre in 32 bits


@dataclass(frozen=True, slots=True)
class ImageInfo:
    format: str | None  # Pillow's name, e.g. ``JPEG``
    is_greyscale: bool
    has_palette: bool
    mode: str  # Pillow's, e.g. ``RGB``
    compression: str | None  # Pillow's, e.g. ``tiff_jpeg``; ``None`` if the format has none
    has_transparency_key: bool  # one colour stands for transparent pixels
    raw_mode: str | None  # the decoder's, e.g. ``RGB;16B`` for 16 bits per channel
    has_several_frames: bool  # an animation's frames, a TIFF's pages: only the first is kept


def info(path: Path) -> ImageInfo:
    """Format details from the headers only (a TIFF's pages each have one)."""
    with open_image(path) as image, _decoding(path.name):
        compression = image.info.get("compression")

        return ImageInfo(
            format=image.format,
            is_greyscale=Image.getmodebase(image.mode) == "L",
            has_palette=image.mode in _PALETTE_MODES,
            mode=image.mode,
            compression=compression if isinstance(compression, str) else None,
            has_transparency_key="transparency" in image.info,
            raw_mode=_raw_mode(image),
            has_several_frames=_has_several_frames(image),
        )


def _has_several_frames(image: ImageFile.ImageFile) -> bool:
    """Whether a second picture follows the first. A TIFF's first directory says so: Pillow's
    ``n_frames`` walks the whole chain, which a hostile file makes endless. The other
    formats count theirs in their headers."""
    if isinstance(image, TiffImagePlugin.TiffImageFile):
        return bool(image.tag_v2.next)

    return getattr(image, "n_frames", 1) > 1


def _raw_mode(image: ImageFile.ImageFile) -> str | None:
    """The mode the decoder unpacks, when the header names it (WebP's does not)."""
    if not image.tile:
        return None

    args = image.tile[0].args
    raw_mode = args[0] if isinstance(args, tuple) and args else args

    return raw_mode if isinstance(raw_mode, str) else None


def describe(path: Path) -> str:
    """E.g. ``2400 × 1600 · PNG``."""
    with open_image(path) as image:
        width, height = image.size

        return f"{width} × {height} · {image.format or path.suffix[1:].upper()}"


def open_image(path: Path) -> ImageFile.ImageFile:
    """``path`` opened from its header, refused if Pillow would refuse to decode it."""
    with _decoding(path.name):
        opened = Image.open(path, formats=_FORMATS)

    try:
        check_size(path.name, *opened.size)
    except UnsupportedFormatError:
        opened.close()
        raise

    return opened


def load(image: Image.Image, name: str) -> None:
    """Decode ``image``'s pixels: chunks after them can hold what Pillow refuses too."""
    with _decoding(name):
        image.load()


@contextlib.contextmanager
def _decoding(name: str) -> Iterator[None]:
    """What Pillow raises on data it cannot decode, as :class:`UnsupportedFormatError`. It
    raises its own errors for too many pixels or too large a text chunk."""
    try:
        yield
    except Image.DecompressionBombError as exc:
        raise UnsupportedFormatError(f"{name} is too large to read") from exc
    except _DECODER_ERRORS as exc:
        raise _unreadable(name) from exc
    except OSError as exc:
        if exc.errno is not None:
            raise

        raise _unreadable(name) from exc


def _unreadable(name: str) -> UnsupportedFormatError:
    return UnsupportedFormatError(f"{name} is not a readable image")


def check_size(name: str, width: int, height: int) -> None:
    """Refuse a picture Pillow would refuse to decode, before allocating its pixels."""
    if Image.MAX_IMAGE_PIXELS is not None and width * height > Image.MAX_IMAGE_PIXELS:
        raise UnsupportedFormatError(f"{name} is too large to read ({width} × {height})")


def eight_bits(samples: npt.NDArray[np.int32]) -> npt.NDArray[np.int32]:
    """16-bit ``samples`` rounded to the nearest 8-bit level: 128 is 0, 129 is 1."""
    return (2 * samples + _DEEP_SCALE) // (2 * _DEEP_SCALE)


def rgb_profile(image: Image.Image) -> bytes | None:
    """The image's ICC profile, if it describes RGB colours."""
    profile = image.info.get("icc_profile")

    return profile if profile and profile[_PROFILE_SPACE] == _RGB_PROFILE else None


def fix_text_xmp(image: Image.Image) -> None:
    """Let Pillow decode an opened TIFF whose XMP is stored as text (ASCII), as some writers
    store it: Pillow reads it as a ``str``, then searches it with a bytes pattern and fails.
    The XMP becomes the bytes it was stored as, typed BYTE as the XMP specification types it,
    so TIFF output stores it so too and can keep the cover's compression. XMP stored as
    numbers is no XMP, and Pillow would fail on it the same way: it is left out."""
    xmp = image.info.get("xmp")

    if not isinstance(image, TiffImagePlugin.TiffImageFile) or isinstance(xmp, bytes | None):
        return

    if not isinstance(xmp, str):
        del image.tag_v2[Base.XMLPacket]
        del image.info["xmp"]
        return

    data = xmp.encode("latin-1")  # how Pillow decoded it
    image.tag_v2.tagtype[Base.XMLPacket] = TiffTags.BYTE
    image.tag_v2[Base.XMLPacket] = data
    image.info["xmp"] = data


def upright(image: Image.Image) -> None:
    """Turn a decoded ``image`` by its EXIF orientation. EXIF that Pillow cannot parse holds
    no orientation it could follow, and the picture stays as it is. EXIF it cannot write back
    after turning fails only that rewrite: the pixels are already turned."""
    with contextlib.suppress(*_EXIF_ERRORS, *_WRITE_ERRORS):
        ImageOps.exif_transpose(image, in_place=True)


@dataclass(frozen=True, slots=True)
class Metadata:
    """What a cover holds besides its pixels, kept wherever the output format has room."""

    icc_profile: bytes | None  # RGB profiles only: image LSB always writes RGB pixels
    exif: bytes | None  # for PNG output
    # A cover's own EXIF, for TIFF output (a TIFF cover's is in its tags and sections). It is
    # filtered only when saved: parsing its sections can make Pillow warn.
    tiff_exif: bytes | None
    dpi: tuple[float, float] | None
    # A PNG's text, gamma, sRGB and chromaticity chunks, or another cover's XMP.
    png_chunks: PngImagePlugin.PngInfo | None
    # A TIFF's :data:`_TIFF_TAGS`, and its predictor if its compression is kept, or else its
    # Exif and GPS sections.
    tiff_tags: TiffImagePlugin.ImageFileDirectory_v2 | None
    tiff_compression: str | None  # Pillow's name, one of :data:`_TIFF_COMPRESSIONS`
    # A compressed TIFF's Exif and GPS sections, added after libtiff writes it: Pillow's
    # libtiff path cannot write nested sections.
    tiff_sections: tiff_directory.Sections | None

    def options(self, format: str) -> dict[str, object]:
        """Pillow's save options that keep it in ``format``."""
        options: dict[str, object]

        if format == "PNG":
            options = {
                "icc_profile": self.icc_profile,
                "exif": self.exif,
                "dpi": self.dpi,
                "pnginfo": self.png_chunks,
            }
        elif format == "TIFF":
            options = {
                "icc_profile": self.icc_profile,
                "exif": None if self.tiff_exif is None else _tiff_exif(self.tiff_exif),
                "dpi": self.dpi,
                "tiffinfo": self.tiff_tags,
                "compression": self.tiff_compression,
            }
        else:
            options = {"dpi": self.dpi}

        return {name: value for name, value in options.items() if value is not None}


NO_METADATA = Metadata(None, None, None, None, None, None, None, None)  # only the pixels


def metadata(image: Image.Image) -> Metadata:
    """The metadata of an opened cover. Read it after decoding: a PNG may keep text and EXIF
    after its pixels."""
    info = image.info
    png = image if isinstance(image, PngImagePlugin.PngImageFile) else None
    tiff = image if isinstance(image, TiffImagePlugin.TiffImageFile) else None
    compression = None if tiff is None else _kept_compression(tiff)
    sections = None if tiff is None else _tiff_sections(tiff)
    exif = info.get("exif") or None
    xmp = info.get("xmp") if tiff is None else _xmp_for_tiff(tiff.info.get("xmp"))

    return Metadata(
        icc_profile=rgb_profile(image),
        exif=exif if tiff is None else _png_exif(tiff, sections),
        tiff_exif=exif if tiff is None else None,
        dpi=_dpi(info.get("dpi")),
        png_chunks=_xmp_chunk(xmp) if png is None else _png_chunks(png),
        tiff_tags=None if tiff is None else _tiff_tags(tiff, compression, sections),
        tiff_compression=compression,
        tiff_sections=None if compression is None else sections,
    )


def _dpi(dpi: tuple[float, float] | None) -> tuple[float, float] | None:
    """``dpi`` if the writers can store it: Pillow reads a resolution of 0/0 as NaN."""
    return dpi if dpi and all(0 < value < _MAX_DPI for value in dpi) else None


def _png_chunks(image: PngImagePlugin.PngImageFile) -> PngImagePlugin.PngInfo | None:
    chunks = PngImagePlugin.PngInfo()

    for key, value in image.text.items():
        chunks.add_text(key, value)

    if "gamma" in image.info:
        chunks.add(b"gAMA", _fixed_point(image.info["gamma"]))

    if "srgb" in image.info:
        chunks.add(b"sRGB", bytes([image.info["srgb"]]))

    if "chromaticity" in image.info:
        chunks.add(b"cHRM", b"".join(_fixed_point(value) for value in image.info["chromaticity"]))

    return chunks if chunks.chunks else None


def _xmp_chunk(xmp: object) -> PngImagePlugin.PngInfo | None:
    """A JPEG, WebP or TIFF cover's XMP as a PNG keeps it (a PNG cover's own is among its
    text chunks)."""
    if not isinstance(xmp, bytes) or not _is_utf8(xmp):  # iTXt holds UTF-8 only
        return None

    chunks = PngImagePlugin.PngInfo()
    chunks.add_itxt(_XMP_KEY, xmp)

    return chunks


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False

    return True


def _fixed_point(value: float) -> bytes:
    return round(value * _PNG_FIXED_POINT).to_bytes(4, "big")


def _kept_compression(image: TiffImagePlugin.TiffImageFile) -> str | None:
    """The cover's compression, if it is lossless and libtiff takes every copied tag."""
    compression = image.info.get("compression")
    types = image.tag_v2.tagtype
    is_writable = all(
        types[tag] in _LIBTIFF_TYPES.get(tag, _LIBTIFF_TEXT_TYPES)
        for tag in _TIFF_TAGS
        if tag in image.tag_v2
    )

    return compression if compression in _TIFF_COMPRESSIONS and is_writable else None


def _tiff_tags(
    image: TiffImagePlugin.TiffImageFile,
    compression: str | None,
    sections: tiff_directory.Sections | None,
) -> TiffImagePlugin.ImageFileDirectory_v2 | None:
    tags = TiffImagePlugin.ImageFileDirectory_v2()

    for tag in _TIFF_TAGS:
        is_xmp = tag == Base.XMLPacket
        value = _xmp_for_tiff(image.info.get("xmp")) if is_xmp else image.tag_v2.get(tag)

        if value is not None:
            tags[tag] = value
            tags.tagtype[tag] = image.tag_v2.tagtype[tag]

    # The predictor changes the compressed size a lot, so the output keeps it too.
    predictor = image.tag_v2.get(Base.Predictor)
    is_kept = isinstance(predictor, int) and predictor in _PREDICTORS  # libtiff takes no 2.0

    if compression in _PREDICTED_COMPRESSIONS and is_kept:
        tags[Base.Predictor] = predictor

    if compression is None:  # else they are added after libtiff writes the TIFF
        for pointer, section in (sections or {}).items():
            tags[pointer] = section
            tags.tagtype[pointer] = TiffTags.LONG

    return tags if len(tags) else None


def _png_exif(
    image: TiffImagePlugin.TiffImageFile, sections: tiff_directory.Sections | None
) -> bytes | None:
    """A TIFF cover's EXIF for PNG output: its descriptive tags and its Exif and GPS sections.
    There is no orientation: Pillow turned the cover by it while loading it."""
    tags = Image.Exif()

    for tag in _PNG_EXIF_TAGS:
        if tag in image.tag_v2:
            tags[tag] = image.tag_v2[tag]

    for pointer, section in (sections or {}).items():
        tags[pointer] = dict(section)

    if not len(tags):
        return None

    try:
        return tags.tobytes()
    except _WRITE_ERRORS:
        return None


def _tiff_sections(image: TiffImagePlugin.TiffImageFile) -> tiff_directory.Sections | None:
    """A TIFF cover's Exif and GPS sections, or ``None`` if Pillow cannot read them."""
    try:
        return _exif_sections(image.getexif()) or None
    except _EXIF_ERRORS:
        return None


def _xmp_for_tiff(xmp: object) -> bytes | None:
    """``xmp``, unless an orientation Pillow reads is in it: loading a TIFF output would turn
    it, moving the hidden bits. A TIFF cover's is taken from ``info["xmp"]``, out of which
    Pillow took the orientation it turned the cover by (it leaves the XMLPacket tag as is)."""
    return xmp if isinstance(xmp, bytes) and not _XMP_TURN.search(xmp) else None


def _tiff_exif(exif: bytes) -> Image.Exif | None:
    """``exif``'s descriptive tags and its Exif and GPS sections, or ``None`` if Pillow cannot
    parse it. Pillow would write the other tags over the TIFF's own size, strips or colours,
    and turn the TIFF by its Orientation while loading it, which would move the hidden bits.
    An XMP tag goes the same way if it holds an orientation (:func:`_xmp_for_tiff`), and any
    tag goes if Pillow cannot write it back. The sections are made ready by
    :func:`_exif_sections`.
    """
    tags = Image.Exif()

    try:
        tags.load(exif)
        sections = _exif_sections(tags)  # Pillow's writer reads them again from ``tags``
    except _EXIF_ERRORS:
        return None  # Pillow opened the cover without parsing its EXIF

    for tag in set(tags) - {*_TIFF_TAGS, *sections}:
        del tags[tag]

    for tag in set(tags) - set(sections):
        if not _is_tag_writable(tag, tags[tag]):
            del tags[tag]

    if Base.XMLPacket in tags and _xmp_for_tiff(tags[Base.XMLPacket]) is None:
        del tags[Base.XMLPacket]

    return tags


def _is_tag_writable(tag: int, value: object) -> bool:
    """Whether Pillow's writer takes ``value`` for ``tag``, typed by its own table as when it
    saves EXIF in a TIFF: a make stored as a number would fail the whole save."""
    trial = TiffImagePlugin.ImageFileDirectory_v2()

    try:
        trial[tag] = value
        trial.tobytes()
    except _WRITE_ERRORS:
        return False

    return True


def _exif_sections(tags: Image.Exif) -> dict[int, dict[int, object]]:
    """``tags``' Exif and GPS sections, the non-empty ones, made ready in place for Pillow's
    writer, or none if it cannot write them back. It writes a nested section in place, but
    copies any other offset as it is: the interoperability section is nested, and the maker's
    notes, whose offsets would point nowhere, are left out. Raises :data:`_EXIF_ERRORS` if
    Pillow cannot read the sections."""
    details = tags.get_ifd(IFD.Exif)
    gps = tags.get_ifd(IFD.GPSInfo)
    interoperability = _interoperability(tags, details)
    details.pop(Base.MakerNote, None)
    details.pop(IFD.Interop, None)  # an offset Pillow could not follow would be written as is

    if interoperability:
        details[IFD.Interop] = interoperability

    sections: dict[int, dict[int, object]] = {
        tag: section for tag, section in ((IFD.Exif, details), (IFD.GPSInfo, gps)) if section
    }
    return sections if _is_writable(sections) else {}


def _is_writable(sections: dict[int, dict[int, object]]) -> bool:
    trial = TiffImagePlugin.ImageFileDirectory_v2()

    for tag, section in sections.items():
        trial[tag] = section
        trial.tagtype[tag] = TiffTags.LONG

    try:
        trial.tobytes()
    except _WRITE_ERRORS:
        return False

    return True


def _interoperability(tags: Image.Exif, details: dict[int, object]) -> dict[int, object]:
    """The interoperability section, or none if Pillow cannot read it: a decoded TIFF has
    closed its file, and Pillow read only its Exif and GPS sections before."""
    if IFD.Interop not in details:  # Pillow would fail looking it up
        return {}

    try:
        return tags.get_ifd(IFD.Interop)
    except _EXIF_ERRORS:
        return {}


@dataclass(frozen=True, slots=True)
class EmbeddedImage:
    image: Image.Image
    metadata: Metadata

    def save(self, path: Path) -> None:
        format = OUTPUT_FORMATS[path.suffix.lower()]
        self.image.save(path, format=format, **self.metadata.options(format))
        sections = self.metadata.tiff_sections

        if format == "TIFF" and sections:
            path.write_bytes(tiff_directory.with_sections(path.read_bytes(), sections))
