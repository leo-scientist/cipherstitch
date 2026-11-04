"""A whole number's maximum is past what the editor's spin box holds."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import GROUP, LEVEL, METHOD

EXTENSION = Extension(
    methods=(METHOD,), params=(replace(GROUP, params=(replace(LEVEL, maximum=2**31),)),)
)
