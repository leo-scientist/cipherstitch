"""One group declares a parameter twice."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import GROUP, LEVEL, METHOD

EXTENSION = Extension(methods=(METHOD,), params=(replace(GROUP, params=(LEVEL, LEVEL)),))
