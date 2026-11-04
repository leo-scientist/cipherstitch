"""The wire id is a str."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import METHOD

SPEC = replace(METHOD, wire_id="150")  # type: ignore[arg-type]
EXTENSION = Extension(methods=(SPEC,))
