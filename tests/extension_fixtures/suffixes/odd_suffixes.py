from dataclasses import replace

from cipherstitch.extensions import CarrierKind, Extension
from tests.extension_fixtures.parts import METHOD

IMAGE = replace(METHOD, carrier=CarrierKind.IMAGE)
EXTENSION = Extension(
    methods=(
        replace(IMAGE, id="raw-reader", wire_id=220, inputs=(".dng",)),
        replace(IMAGE, id="pgm-writer", wire_id=221, inputs=(".png",), outputs=(".pgm",)),
    )
)
