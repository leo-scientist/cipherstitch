"""The parameter groups are a list."""

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import GROUP, METHOD

PARAMS = [GROUP]
EXTENSION = Extension(methods=(METHOD,), params=PARAMS)  # type: ignore[arg-type]
