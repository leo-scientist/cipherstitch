"""The cost function id is a built-in option of limg.cost."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import COST

EXTENSION = Extension(costs=(replace(COST, id="hill"),))
