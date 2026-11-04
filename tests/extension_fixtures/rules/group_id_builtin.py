"""The group id is the STC group's."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import GROUP, METHOD

EXTENSION = Extension(methods=(METHOD,), params=(replace(GROUP, id="stc"),))
