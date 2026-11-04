"""codes_with_trellis is a number."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import METHOD

SPEC = replace(METHOD, codes_with_trellis=1)  # type: ignore[arg-type]
EXTENSION = Extension(methods=(SPEC,))
