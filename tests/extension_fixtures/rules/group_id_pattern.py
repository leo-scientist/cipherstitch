"""The group id has a hyphen, which a parameter key can't hold."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import GROUP, METHOD

EXTENSION = Extension(methods=(METHOD,), params=(replace(GROUP, id="rule-group"),))
