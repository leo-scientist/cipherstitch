"""A new suffix joins two carrier kinds."""

from dataclasses import replace

from cipherstitch.extensions import CarrierKind, Extension
from tests.extension_fixtures.parts import METHOD

TEXT = replace(METHOD, inputs=(".two",))
IMAGE = replace(TEXT, id="rule-image", wire_id=151, carrier=CarrierKind.IMAGE)
EXTENSION = Extension(methods=(TEXT, IMAGE))
