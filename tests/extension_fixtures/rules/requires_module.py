"""requires names a submodule."""

from dataclasses import replace

from cipherstitch.extensions import Extension, Requirement
from tests.extension_fixtures.parts import METHOD

EXTENSION = Extension(methods=(replace(METHOD, requires=Requirement("scipy.signal")),))
