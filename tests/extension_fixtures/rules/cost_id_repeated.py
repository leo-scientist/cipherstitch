"""Two spatial cost functions share an id."""

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import COST

EXTENSION = Extension(costs=(COST, COST))
