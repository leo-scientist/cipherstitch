"""The cost functions are a list."""

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import COST

COSTS = [COST]
EXTENSION = Extension(costs=COSTS)  # type: ignore[arg-type]
