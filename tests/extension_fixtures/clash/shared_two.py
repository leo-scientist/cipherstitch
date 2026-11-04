from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import METHOD

EXTENSION = Extension(methods=(replace(METHOD, id="shared-two", wire_id=181, inputs=(".shared",)),))
