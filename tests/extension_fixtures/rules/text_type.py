"""The pros are a list."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import CIPHER

SPEC = replace(CIPHER, pros=["Fast"])  # type: ignore[arg-type]
EXTENSION = Extension(ciphers=(SPEC,))
