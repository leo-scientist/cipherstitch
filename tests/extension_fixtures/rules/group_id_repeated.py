"""Two groups have one id."""

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import GROUP, METHOD

EXTENSION = Extension(methods=(METHOD,), params=(GROUP, GROUP))
