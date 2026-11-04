"""The methods are a list."""

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import METHOD

EXTENSION = Extension(methods=[METHOD])  # type: ignore[arg-type]
