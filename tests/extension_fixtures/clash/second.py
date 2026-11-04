from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import CIPHER, METHOD

EXTENSION = Extension(
    methods=(replace(METHOD, id="second", wire_id=160),),
    ciphers=(replace(CIPHER, id="clash-cipher", wire_id=161),),
)
