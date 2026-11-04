"""The icon is a path, not one of the app's icons."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import GROUP, METHOD

EXTENSION = Extension(methods=(METHOD,), params=(replace(GROUP, icon="../theme"),))
