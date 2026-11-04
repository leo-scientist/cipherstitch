"""The minimum is above the maximum."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import GROUP, LEVEL, METHOD

EXTENSION = Extension(
    methods=(METHOD,), params=(replace(GROUP, params=(replace(LEVEL, minimum=8),)),)
)
