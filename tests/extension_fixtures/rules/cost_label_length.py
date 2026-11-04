"""The cost function label is 41 characters long."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import COST

EXTENSION = Extension(costs=(replace(COST, label="l" * 41),))
