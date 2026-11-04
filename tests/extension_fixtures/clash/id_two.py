from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import CIPHER, METHOD

EXTENSION = Extension(
    methods=(replace(METHOD, id="clash-method", wire_id=201),),
    ciphers=(replace(CIPHER, id="two-cipher", wire_id=200),),
)
