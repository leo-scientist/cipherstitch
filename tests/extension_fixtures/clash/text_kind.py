from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import METHOD

EXTENSION = Extension(methods=(replace(METHOD, id="text-kind", wire_id=171, inputs=(".clash",)),))
