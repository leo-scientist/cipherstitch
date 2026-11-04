"""An image's colour values as the LSB methods read and write them: the red, green and blue
of every pixel, row by row. Alpha is kept as it is, and the output is always in colour."""

from functools import cached_property
from pathlib import Path
from typing import cast

import numpy as np
import numpy.typing as npt
from PIL import Image

from cipherstitch.errors import UnsupportedFormatError
from cipherstitch.media import image as image_media
from cipherstitch.media.image import EmbeddedImage, Metadata

COLOR_CHANNELS = 3
PIXEL_RANGE = (0, 255)


class Picture:
    """Opening reads only the image header; the pixels are decoded on first use."""

    def __init__(self, path: Path, *, keeps_metadata: bool = True) -> None:
        with image_media.open_image(path) as image:
            width, height = image.size
            self._mode = _carrier_mode(image)

        self._path = path
        self._keeps_metadata = keeps_metadata
        self.value_count = width * height * COLOR_CHANNELS

    @property
    def pixels(self) -> npt.NDArray[np.uint8]:
        """Rows × columns × 3 or 4, read-only."""
        return self._decoded[0]

    def values(self) -> npt.NDArray[np.uint8]:
        """Every pixel's red, green and blue, row by row, read-only."""
        return self.pixels[..., :COLOR_CHANNELS].reshape(-1)

    def rebuilt(self, values: npt.NDArray[np.integer]) -> EmbeddedImage:
        """The picture with ``values`` in place of :meth:`values`, and the cover's metadata."""
        pixels, metadata = self._decoded

        if pixels.shape[2] == COLOR_CHANNELS:  # no alpha: the values are every pixel
            return EmbeddedImage(Image.fromarray(values.reshape(pixels.shape)), metadata)

        rebuilt = pixels.copy()
        rebuilt[..., :COLOR_CHANNELS] = values.reshape(*pixels.shape[:2], COLOR_CHANNELS)

        return EmbeddedImage(Image.fromarray(rebuilt), metadata)

    @cached_property
    def _decoded(self) -> tuple[npt.NDArray[np.uint8], Metadata]:
        """The pixels, read-only: viewing the decoded bytes, and converting only when the mode
        differs, saves two copies of the image. Then the metadata, which decoding completes."""
        with image_media.open_image(self._path) as image:
            image_media.fix_text_xmp(image)
            image_media.load(image, self._path.name)

            if _has_deep_values(image):
                raise UnsupportedFormatError(f"LSB can't keep {self._path.name}'s deep values")

            if image.mode in image_media.DEEP_GREY_MODES:
                pixels = _deep_grey(image, self._mode)
            else:
                converted = image if image.mode == self._mode else image.convert(self._mode)
                pixels = np.asarray(converted, dtype=np.uint8)

            if not self._keeps_metadata:
                return pixels, image_media.NO_METADATA

            return pixels, image_media.metadata(image)


def _has_deep_values(image: Image.Image) -> bool:
    """32-bit integer or float samples beyond 0 … 255, which ``convert`` would clip."""
    if not image.mode.startswith(("I", "F")) or image.mode in image_media.DEEP_GREY_MODES:
        return False

    low, high = cast(tuple[float, float], image.getextrema())  # a single band

    return low < PIXEL_RANGE[0] or high > PIXEL_RANGE[1]


def _deep_grey(image: Image.Image, mode: str) -> npt.NDArray[np.uint8]:
    """A 16-bit greyscale picture in 8-bit ``mode``, rounded to the nearest level as a
    precover is: ``convert`` would clip it. A transparent level becomes alpha 0."""
    samples = np.asarray(image, dtype=np.uint16)
    grey = image_media.eight_bits(samples.astype(np.int32)).astype(np.uint8)
    planes = [grey] * COLOR_CHANNELS

    if mode == "RGBA":
        is_clear = samples == image.info.get("transparency")
        planes.append(np.where(is_clear, 0, PIXEL_RANGE[1]).astype(np.uint8))

    return np.stack(planes, axis=-1)


def _carrier_mode(image: Image.Image) -> str:
    has_alpha = "A" in image.getbands() or "transparency" in image.info

    return "RGBA" if has_alpha else "RGB"
