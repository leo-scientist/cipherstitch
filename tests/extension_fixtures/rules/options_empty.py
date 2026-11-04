"""A choice has no options."""

from dataclasses import replace

from cipherstitch.extensions import Extension, ParamType
from tests.extension_fixtures.parts import GROUP, LEVEL, METHOD

CHOICE = replace(LEVEL, type=ParamType.ENUM, default="a", minimum=None, maximum=None, step=None)
EXTENSION = Extension(methods=(METHOD,), params=(replace(GROUP, params=(CHOICE,)),))
