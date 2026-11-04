"""Precovers for DCT: a camera RAW, PNG or TIFF that is compressed to JPEG here, once, so
each AC coefficient's rounding error is known (docs/methods.md#from-a-raw-png-or-tiff).

The compression is libjpeg-turbo's own integer arithmetic, done in numpy: the colour
conversion of ``jccolor.c``, the edge padding of ``jcprepare.c`` and ``jcsample.c``,
``h2v2_downsample``, ``h2v1_downsample`` or ``fullsize_downsample`` (4:2:0, 4:2:2 or 4:4:4
chroma), ``jpeg_fdct_islow`` (``jfdctint.c``) and the quantization of ``jcdctmgr.c``. So
the JPEG has exactly the coefficients that Pillow or jpeglib would write for the same 8-bit
picture. Being a translation of the Independent JPEG Group's software, it keeps that
software's terms (THIRD_PARTY_NOTICES.md#the-independent-jpeg-groups-software).

The coefficients are what jpeglib writes and reads back: Y keeps ⌈H/8⌉ × ⌈W/8⌉ blocks and
chroma ⌈H/8v⌉ × ⌈W/8h⌉ when a chroma sample covers h × v pixels, the grid it returns for the
declared size. Any other grid would give the reader another number of units, and the
payload would be lost.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePath

import numpy as np
import numpy.typing as npt
from PIL import Image

from cipherstitch import catalog
from cipherstitch.errors import UnsupportedFormatError
from cipherstitch.media import image, raw
from cipherstitch.media.jpeg import Coefficients, Component, Subsampling

QUALITY = 90
SUBSAMPLING = Subsampling.H2V2
SUFFIXES = (".png", ".tif", ".tiff", *catalog.RAW_EXTENSIONS)
ROUNDING_SCALE = 254  # a rounding error in -0.5 … 0.5 is stored as an int8 of -127 … 127

# ITU T.81 Annex K tables K.1 and K.2, row by row, scaled for the quality as libjpeg scales them.
_LUMINANCE = (
    (16, 11, 10, 16, 24, 40, 51, 61),
    (12, 12, 14, 19, 26, 58, 60, 55),
    (14, 13, 16, 24, 40, 57, 69, 56),
    (14, 17, 22, 29, 51, 87, 80, 62),
    (18, 22, 37, 56, 68, 109, 103, 77),
    (24, 35, 55, 64, 81, 104, 113, 92),
    (49, 64, 78, 87, 103, 121, 120, 101),
    (72, 92, 95, 98, 112, 100, 103, 99),
)
_CHROMINANCE = (
    (17, 18, 24, 47, 99, 99, 99, 99),
    (18, 21, 26, 66, 99, 99, 99, 99),
    (24, 26, 56, 99, 99, 99, 99, 99),
    (47, 66, 99, 99, 99, 99, 99, 99),
    *((99,) * 8,) * 4,
)
_MAX_STEP = 255  # baseline JPEG stores 8-bit steps
_ROWS = 64  # pixel rows compressed at a time; a multiple of 16 keeps chroma blocks whole
_BLOCK = 8
_AC = _BLOCK * _BLOCK - 1
_OPAQUE = 255
_CENTRE = 128  # the level shift before the DCT
# jcsample.c: a chroma sample is the sum of its pixels plus a bias that alternates from column
# to column, shifted right (h2v2_downsample, h2v1_downsample, fullsize_downsample).
_DOWNSAMPLING = {
    Subsampling.H2V2: ((1, 2), 2),
    Subsampling.H2V1: ((0, 1), 1),
    Subsampling.H1V1: ((0, 0), 0),
}
# jccolor.c: FIX(x) is x · 2^16 rounded; Cb and Cr round with 0.5 − ε so they never reach 256.
_SCALE_BITS = 16
_ONE_HALF = 1 << (_SCALE_BITS - 1)
_CHROMA_OFFSET = (_CENTRE << _SCALE_BITS) + _ONE_HALF - 1
# jfdctint.c (the IJG 6b integer DCT that libjpeg-turbo keeps): FIX(x) is x · 2^13 rounded.
_CONST_BITS = 13
_PASS1_BITS = 2
_OUTPUT_SCALE = 8  # jpeg_fdct_islow returns 8 times the coefficient
_FIX_0_298631336 = 2446
_FIX_0_390180644 = 3196
_FIX_0_541196100 = 4433
_FIX_0_765366865 = 6270
_FIX_0_899976223 = 7373
_FIX_1_175875602 = 9633
_FIX_1_501321110 = 12299
_FIX_1_847759065 = 15137
_FIX_1_961570560 = 16069
_FIX_2_053119869 = 16819
_FIX_2_562915447 = 20995
_FIX_3_072711026 = 25172
_COLOUR_MODES = frozenset({"RGB", "RGBA", "P", "PA"})
_GREY_MODES = frozenset({"1", "L", "LA"})

Pixels = npt.NDArray[np.uint8] | npt.NDArray[np.uint16]


def _steps(table: tuple[tuple[int, ...], ...], scale: int) -> npt.NDArray[np.uint16]:
    scaled = (np.array(table, dtype=np.int64) * scale + 50) // 100

    return np.clip(scaled, 1, _MAX_STEP).astype(np.uint16)


@cache
def _tables(quality: int) -> tuple[npt.NDArray[np.uint16], ...]:
    """The steps of Y, Cb and Cr at ``quality``, 50 or more (libjpeg's
    ``jpeg_quality_scaling``)."""
    scale = 200 - 2 * quality
    chrominance = _steps(_CHROMINANCE, scale)

    return _steps(_LUMINANCE, scale), chrominance, chrominance


@dataclass(frozen=True, slots=True)
class Precover:
    """A precover compressed to JPEG coefficients, with their rounding errors."""

    coefficients: Coefficients
    # Each AC coefficient's rounding error times ROUNDING_SCALE: its sign says which way the
    # precover lies. 0 means no side information (an exact value or a tie).
    rounding: npt.NDArray[np.int8]
    size: tuple[int, int]  # width, height
    icc_profile: bytes | None
    subsampling: Subsampling


def is_precover(path: PurePath) -> bool:
    return path.suffix.lower() in SUFFIXES


def compress(
    path: Path, quality: int = QUALITY, subsampling: Subsampling = SUBSAMPLING
) -> Precover:
    """``path`` compressed at ``quality``, its chroma sampled as ``subsampling`` says, or as
    greyscale."""
    if path.suffix.lower() in catalog.RAW_EXTENSIONS:
        pixels: Pixels = raw.develop(path)
        profile = None  # developed to sRGB
    else:
        pixels, profile = _decoded(path)

    height, width = pixels.shape[:2]
    is_colour = pixels.ndim == 3
    across, down = subsampling.factors
    grids = [(-(-height // _BLOCK), -(-width // _BLOCK))]

    if is_colour:
        grids += [(-(-height // (_BLOCK * down)), -(-width // (_BLOCK * across)))] * 2

    components = tuple(
        Component(rows, columns, steps.reshape(-1))
        for (rows, columns), steps in zip(grids, _tables(quality)[: len(grids)], strict=True)
    )
    blocks = sum(c.rows * c.columns for c in components)
    dc = np.empty(blocks, dtype=np.int16)
    ac = np.empty(blocks * _AC, dtype=np.int16)
    rounding = np.empty(blocks * _AC, dtype=np.int8)
    firsts = np.cumsum([0, *(c.rows * c.columns for c in components)])

    for top in range(0, height, _ROWS):
        for number, component in enumerate(components):
            scale = down if number else 1
            first_row = top // scale
            last_row = min((top + _ROWS) // scale, component.rows * _BLOCK)
            columns = component.columns * _BLOCK
            plane = _plane(pixels, number, first_row, last_row, columns, subsampling)
            quantized, errors = _quantized(plane, component)
            start = firsts[number] + first_row // _BLOCK * component.columns
            stop = start + len(quantized)
            dc[start:stop] = quantized[:, 0]
            ac[start * _AC : stop * _AC] = quantized[:, 1:].reshape(-1)
            rounding[start * _AC : stop * _AC] = errors

    coefficients = Coefficients(ac, components, dc)

    return Precover(coefficients, rounding, (width, height), profile, subsampling)


def _decoded(path: Path) -> tuple[Pixels, bytes | None]:
    """A PNG or TIFF's pixels, upright: 8-bit RGB or greyscale, or 16-bit greyscale."""
    with image.open_image(path) as opened:
        image.fix_text_xmp(opened)
        image.load(opened, path.name)
        image.upright(opened)  # a TIFF is turned while loading
        pixels = _pixels(opened, path.name)

        return pixels, image.rgb_profile(opened) if pixels.ndim == 3 else None


def _pixels(opened: Image.Image, name: str) -> Pixels:
    mode = opened.mode

    if mode in image.DEEP_GREY_MODES:
        pixels = np.asarray(opened, dtype=np.uint16)
        key = opened.info.get("transparency")

        if key is not None and np.any(pixels == key):
            raise _transparent(name)

        return pixels

    if mode not in _COLOUR_MODES | _GREY_MODES:
        raise UnsupportedFormatError(f"{name}: {mode} images can't be a precover")

    is_colour = mode in _COLOUR_MODES

    if _is_transparent(opened, is_colour=is_colour):
        raise _transparent(name)

    target = "RGB" if is_colour else "L"

    return np.asarray(opened if mode == target else opened.convert(target))


def _is_transparent(opened: Image.Image, *, is_colour: bool) -> bool:
    """Whether a pixel is less than opaque, by an alpha band or a transparent colour."""
    if "A" in opened.getbands():
        alpha = opened.getchannel("A")
    elif "transparency" in opened.info:
        alpha = opened.convert("RGBA" if is_colour else "LA").getchannel("A")
    else:
        return False

    return bool(np.asarray(alpha).min() < _OPAQUE)


def _transparent(name: str) -> UnsupportedFormatError:
    return UnsupportedFormatError(f"JPEG can't keep {name}'s transparency")


def _plane(
    pixels: Pixels, number: int, top: int, bottom: int, width: int, subsampling: Subsampling
) -> npt.NDArray[np.int32]:
    """Rows ``top … bottom − 1`` and ``width`` columns of component ``number`` as libjpeg
    samples it, level-shifted: the edge pixels repeated out to whole blocks. A chroma sample
    takes the pixels ``subsampling`` gives it. Below the picture, libjpeg repeats the last
    chroma row (jcprepare.c), not the picture's last row."""
    height = pixels.shape[0]

    if number == 0:
        rows = np.minimum(np.arange(top, bottom), height - 1)
        samples = _samples(pixels, rows, width)
        luminance = samples if samples.ndim == 2 else _converted(samples, number)

        return luminance - _CENTRE

    across, down = subsampling.factors
    biases, shift = _DOWNSAMPLING[subsampling]
    last = (height + down - 1) // down - 1  # the last chroma row the picture reaches
    firsts = np.minimum(np.arange(top, bottom), last) * down
    rows = np.minimum((firsts[:, None] + np.arange(down)).reshape(-1), height - 1)
    full = _converted(_samples(pixels, rows, width * across), number)
    sums = full.reshape(bottom - top, down, width, across).sum(axis=(1, 3))
    bias = np.where(np.arange(width) % 2 == 0, biases[0], biases[-1])

    return ((sums + bias) >> shift) - _CENTRE


def _samples(pixels: Pixels, rows: npt.NDArray[np.int64], width: int) -> npt.NDArray[np.int32]:
    """The picture's ``rows``, its last column repeated out to ``width``, in 8 bits."""
    columns = np.minimum(np.arange(width), pixels.shape[1] - 1)
    samples = pixels[rows][:, columns].astype(np.int32)

    if pixels.dtype == np.uint16:  # rounded to the nearest 8-bit level
        samples = image.eight_bits(samples)

    return samples


def _fixed(value: float) -> int:
    return int(value * (1 << _SCALE_BITS) + 0.5)


def _converted(rgb: npt.NDArray[np.int32], number: int) -> npt.NDArray[np.int32]:
    """Y, Cb or Cr (``number`` 0, 1, 2) of 8-bit RGB, as ``rgb_ycc_convert`` computes it."""
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    if number == 0:
        weighted = _fixed(0.299) * red + _fixed(0.587) * green + _fixed(0.114) * blue + _ONE_HALF
    elif number == 1:
        weighted = -_fixed(0.16874) * red - _fixed(0.33126) * green + _fixed(0.5) * blue
        weighted += _CHROMA_OFFSET
    else:
        weighted = _fixed(0.5) * red - _fixed(0.41869) * green - _fixed(0.08131) * blue
        weighted += _CHROMA_OFFSET

    return weighted >> _SCALE_BITS


def _quantized(
    plane: npt.NDArray[np.int32], component: Component
) -> tuple[npt.NDArray[np.int16], npt.NDArray[np.int8]]:
    """Each block's 64 quantized coefficients, and its AC coefficients' rounding errors."""
    rows, columns = plane.shape[0] // _BLOCK, plane.shape[1] // _BLOCK
    blocks = plane.reshape(rows, _BLOCK, columns, _BLOCK).transpose(0, 2, 1, 3).astype(np.int64)

    return _rounded(_islow(blocks).reshape(-1, _BLOCK * _BLOCK), component.steps)


def _rounded(
    output: npt.NDArray[np.int64], steps: npt.NDArray[np.uint16]
) -> tuple[npt.NDArray[np.int16], npt.NDArray[np.int8]]:
    """``jpeg_fdct_islow``'s ``output`` (blocks × 64) quantized as libjpeg does it: divided
    by 8 × the step, halves rounded away from zero (jcdctmgr.c). The remainder over that
    divisor is the rounding error, on a grid of 1/(8q) of a step. A remainder of exactly 4q
    is a tie, ±1/2, so it carries no side information, as an exact value does."""
    divisor = _OUTPUT_SCALE * steps.astype(np.int64)
    quantized = np.sign(output) * ((np.abs(output) + divisor // 2) // divisor)
    remainder = (output - quantized * divisor)[:, 1:]
    stored = np.rint(remainder / divisor[1:] * ROUNDING_SCALE).astype(np.int8)
    stored[2 * np.abs(remainder) == divisor[1:]] = 0

    return quantized.astype(np.int16), stored.reshape(-1)


def _islow(blocks: npt.NDArray[np.int64]) -> npt.NDArray[np.int64]:
    """``jpeg_fdct_islow`` over ``… × 8 × 8`` blocks: rows first, then columns."""
    rows = _islow_pass(blocks, axis=-1, is_last=False)

    return _islow_pass(rows, axis=-2, is_last=True)


def _islow_pass(
    blocks: npt.NDArray[np.int64], axis: int, *, is_last: bool
) -> npt.NDArray[np.int64]:
    """One pass of the 8-point integer DCT along ``axis``: the first keeps two more bits,
    the last takes them off again."""
    d = [np.take(blocks, k, axis=axis) for k in range(_BLOCK)]
    tmp0, tmp7 = d[0] + d[7], d[0] - d[7]
    tmp1, tmp6 = d[1] + d[6], d[1] - d[6]
    tmp2, tmp5 = d[2] + d[5], d[2] - d[5]
    tmp3, tmp4 = d[3] + d[4], d[3] - d[4]
    tmp10, tmp13 = tmp0 + tmp3, tmp0 - tmp3
    tmp11, tmp12 = tmp1 + tmp2, tmp1 - tmp2
    shift = _CONST_BITS + _PASS1_BITS if is_last else _CONST_BITS - _PASS1_BITS

    if is_last:
        out0, out4 = _descaled(tmp10 + tmp11, _PASS1_BITS), _descaled(tmp10 - tmp11, _PASS1_BITS)
    else:
        out0, out4 = (tmp10 + tmp11) << _PASS1_BITS, (tmp10 - tmp11) << _PASS1_BITS

    z1 = (tmp12 + tmp13) * _FIX_0_541196100
    out2 = _descaled(z1 + tmp13 * _FIX_0_765366865, shift)
    out6 = _descaled(z1 - tmp12 * _FIX_1_847759065, shift)
    # The odd part, Figure 8 of the LL&M paper: the products are √2 times sums of cosines.
    z1, z2, z3, z4 = tmp4 + tmp7, tmp5 + tmp6, tmp4 + tmp6, tmp5 + tmp7
    z5 = (z3 + z4) * _FIX_1_175875602
    tmp4, tmp5 = tmp4 * _FIX_0_298631336, tmp5 * _FIX_2_053119869
    tmp6, tmp7 = tmp6 * _FIX_3_072711026, tmp7 * _FIX_1_501321110
    z1, z2 = z1 * -_FIX_0_899976223, z2 * -_FIX_2_562915447
    z3, z4 = z3 * -_FIX_1_961570560 + z5, z4 * -_FIX_0_390180644 + z5
    out1 = _descaled(tmp7 + z1 + z4, shift)
    out3 = _descaled(tmp6 + z2 + z3, shift)
    out5 = _descaled(tmp5 + z2 + z4, shift)
    out7 = _descaled(tmp4 + z1 + z3, shift)

    return np.stack([out0, out1, out2, out3, out4, out5, out6, out7], axis=axis)


def _descaled(value: npt.NDArray[np.int64], bits: int) -> npt.NDArray[np.int64]:
    """``value`` / 2^bits, rounded half up (libjpeg's DESCALE)."""
    return (value + (1 << (bits - 1))) >> bits
