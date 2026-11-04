"""A fraction's default is a whole number, where the settings store a float."""

from dataclasses import replace

from cipherstitch.extensions import Extension, ParamType
from tests.extension_fixtures.parts import GROUP, LEVEL, METHOD

FRACTION = replace(LEVEL, type=ParamType.FLOAT, step=0.5)
EXTENSION = Extension(methods=(METHOD,), params=(replace(GROUP, params=(FRACTION,)),))
