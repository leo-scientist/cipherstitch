"""A fraction steps finer than a double can show."""

from dataclasses import replace

from cipherstitch.extensions import Extension, ParamType
from tests.extension_fixtures.parts import GROUP, LEVEL, METHOD

FINE = replace(LEVEL, type=ParamType.FLOAT, default=1.0, step=1e-16)
EXTENSION = Extension(methods=(METHOD,), params=(replace(GROUP, params=(FINE,)),))
