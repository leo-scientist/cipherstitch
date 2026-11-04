import io
import struct

import numpy as np
import pytest
from PIL import ExifTags, Image, TiffImagePlugin

from cipherstitch.media import tiff_directory

SECTIONS: tiff_directory.Sections = {
    ExifTags.IFD.Exif: {
        ExifTags.Base.ISOSpeedRatings: 200,
        ExifTags.Base.ExposureTime: TiffImagePlugin.IFDRational(1, 50),
    },
    ExifTags.IFD.GPSInfo: {ExifTags.GPS.GPSLatitudeRef: "N"},
}


def _pixels(size: tuple[int, int]) -> np.ndarray:
    return np.random.default_rng(seed=0).integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)


def _tiff(pixels: np.ndarray, compression: str) -> bytes:
    output = io.BytesIO()
    Image.fromarray(pixels).save(output, "TIFF", compression=compression, software="Scanner")

    return output.getvalue()


def _directory_start(data: bytes) -> int:
    return int(struct.unpack_from("<L", data, 4)[0])


@pytest.mark.parametrize("size", [(4, 4), (640, 480)], ids=["one-strip", "many-strips"])
def test_the_sections_take_the_place_of_libtiffs_directory(size: tuple[int, int]) -> None:
    """libtiff writes the directory after the strips, so nothing is left of the old one."""
    pixels = _pixels(size)
    data = _tiff(pixels, "tiff_lzw")
    start = _directory_start(data)
    result = tiff_directory.with_sections(data, SECTIONS)

    assert result[:start] == data[:start]

    with Image.open(io.BytesIO(result)) as image:
        tags = image.getexif()
        assert tags.get_ifd(ExifTags.IFD.Exif) == SECTIONS[ExifTags.IFD.Exif]
        assert tags.get_ifd(ExifTags.IFD.GPSInfo) == SECTIONS[ExifTags.IFD.GPSInfo]
        assert tags[ExifTags.Base.Software] == "Scanner"
        assert image.info["compression"] == "tiff_lzw"
        assert np.array_equal(np.asarray(image), pixels)


def test_a_directory_before_the_strips_is_written_after_them() -> None:
    """Pillow's own writer puts the directory first."""
    pixels = _pixels((64, 48))
    data = _tiff(pixels, "raw")
    result = tiff_directory.with_sections(data, SECTIONS)

    assert _directory_start(result) >= len(data)

    with Image.open(io.BytesIO(result)) as image:
        assert image.getexif().get_ifd(ExifTags.IFD.Exif) == SECTIONS[ExifTags.IFD.Exif]
        assert np.array_equal(np.asarray(image), pixels)
