"""A text method takes an audio suffix."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import METHOD

EXTENSION = Extension(methods=(replace(METHOD, inputs=(".txt", ".wav")),))
