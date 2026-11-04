"""A parameter's note holds a zero-width space."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import GROUP, LEVEL, METHOD

NOTE = "Higher" + chr(0x200B)
EXTENSION = Extension(
    methods=(METHOD,), params=(replace(GROUP, params=(replace(LEVEL, note=NOTE),)),)
)
