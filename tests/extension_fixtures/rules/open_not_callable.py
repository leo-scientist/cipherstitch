"""open is not callable."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import METHOD

SPEC = replace(METHOD, open=None)  # type: ignore[arg-type]
EXTENSION = Extension(methods=(SPEC,))
