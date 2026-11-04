"""The summary holds a right-to-left override."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import METHOD

EXTENSION = Extension(methods=(replace(METHOD, summary="Bits" + chr(0x202E)),))
