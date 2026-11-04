"""The group's scope names a built-in method too."""

from dataclasses import replace

from cipherstitch.extensions import Extension, Scope
from tests.extension_fixtures.parts import GROUP, METHOD

SCOPE = Scope(frozenset({METHOD.id, "lsb"}))
EXTENSION = Extension(methods=(METHOD,), params=(replace(GROUP, scope=SCOPE),))
