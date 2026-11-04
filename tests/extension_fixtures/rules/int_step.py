"""A whole number steps by a half."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import GROUP, LEVEL, METHOD

EXTENSION = Extension(
    methods=(METHOD,), params=(replace(GROUP, params=(replace(LEVEL, step=0.5),)),)
)
