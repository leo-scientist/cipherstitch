"""The cost function id has capitals and an underscore."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import COST

EXTENSION = Extension(costs=(replace(COST, id="Rule_Cost"),))
