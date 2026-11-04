"""The order is a str."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import METHOD

SPEC = replace(METHOD, order="keyed")  # type: ignore[arg-type]
EXTENSION = Extension(methods=(SPEC,))
