"""Costs of one for every value, with the same id in both domains. The spatial ones come as
float64, which the channel casts."""

import numpy as np
import numpy.typing as npt

from cipherstitch.extensions import Coefficients, CostSpec, Domain, Extension


def spatial_ones(colors: npt.NDArray[np.uint8], rate: float) -> npt.NDArray[np.float64]:
    return np.ones(colors.shape)


def jpeg_ones(cover: Coefficients) -> npt.NDArray[np.float32]:
    return np.ones(cover.ac.shape, dtype=np.float32)


EXTENSION = Extension(
    costs=(
        CostSpec("ones", "Ones", Domain.SPATIAL, spatial_ones),
        CostSpec("ones", "Ones", Domain.JPEG, jpeg_ones),
    ),
    summary="Costs of one",
)
