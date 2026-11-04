"""The costs are an array, not a function."""

from dataclasses import replace

import numpy as np

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import COST

COSTS = np.ones((2, 2, 3), dtype=np.float32)
EXTENSION = Extension(costs=(replace(COST, costs=COSTS),))  # type: ignore[arg-type]
