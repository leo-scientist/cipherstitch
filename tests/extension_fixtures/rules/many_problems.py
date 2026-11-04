"""Three problems in one method, and one in a cipher: all of them are listed."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import CIPHER, METHOD

EXTENSION = Extension(
    methods=(replace(METHOD, wire_id=999, name="", inputs=()),),
    ciphers=(replace(CIPHER, id="Rule Cipher"),),
)
