"""Two methods share an id."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import METHOD

EXTENSION = Extension(methods=(METHOD, replace(METHOD, wire_id=151)))
