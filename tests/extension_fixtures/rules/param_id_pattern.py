"""The parameter id has an upper-case letter."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import GROUP, LEVEL, METHOD

EXTENSION = Extension(
    methods=(METHOD,), params=(replace(GROUP, params=(replace(LEVEL, id="Level"),)),)
)
