"""The id is a built-in cipher's."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import CIPHER

EXTENSION = Extension(ciphers=(replace(CIPHER, id="fernet"),))
