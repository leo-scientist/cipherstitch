"""sealed_size says one byte less than seal writes."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import CIPHER, ChaChaCopy


class Undersized(ChaChaCopy):
    def sealed_size(self, size: int) -> int:
        return super().sealed_size(size) - 1


EXTENSION = Extension(ciphers=(replace(CIPHER, cipher=Undersized()),))
