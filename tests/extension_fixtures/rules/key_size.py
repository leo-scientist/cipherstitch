"""The key is too short."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import CIPHER, ChaChaCopy


class ShortKey(ChaChaCopy):
    key_size = 8


EXTENSION = Extension(ciphers=(replace(CIPHER, cipher=ShortKey()),))
