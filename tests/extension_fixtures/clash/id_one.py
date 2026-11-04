from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import CIPHER, METHOD

EXTENSION = Extension(
    methods=(replace(METHOD, id="clash-method", wire_id=200),),
    ciphers=(replace(CIPHER, id="one-cipher", wire_id=200),),
)
