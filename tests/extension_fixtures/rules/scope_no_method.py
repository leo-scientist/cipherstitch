"""The group's scope names no method, so it would apply to every one."""

from dataclasses import replace

from cipherstitch.extensions import Extension, Scope
from tests.extension_fixtures.parts import GROUP, METHOD

EXTENSION = Extension(methods=(METHOD,), params=(replace(GROUP, scope=Scope()),))
