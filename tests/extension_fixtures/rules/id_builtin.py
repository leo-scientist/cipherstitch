"""The id is a built-in method's."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import METHOD

EXTENSION = Extension(methods=(replace(METHOD, id="zero-width"),))
