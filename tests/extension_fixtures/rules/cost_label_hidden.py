"""The cost function label ends in a line feed."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import COST

EXTENSION = Extension(costs=(replace(COST, label="Rule cost" + chr(10)),))
