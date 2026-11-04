"""The id has upper-case letters and an underscore."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import METHOD

EXTENSION = Extension(methods=(replace(METHOD, id="Rule_Method"),))
