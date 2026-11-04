"""Two ciphers share an id."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import CIPHER

EXTENSION = Extension(ciphers=(CIPHER, replace(CIPHER, wire_id=151)))
