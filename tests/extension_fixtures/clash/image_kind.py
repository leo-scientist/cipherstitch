from dataclasses import replace

from cipherstitch.extensions import CarrierKind, Extension
from tests.extension_fixtures.parts import METHOD

IMAGE = replace(METHOD, id="image-kind", wire_id=170, carrier=CarrierKind.IMAGE)
EXTENSION = Extension(methods=(replace(IMAGE, inputs=(".clash",)),))
