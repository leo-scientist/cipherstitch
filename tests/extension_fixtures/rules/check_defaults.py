"""The group's check refuses its own defaults."""

from collections.abc import Mapping
from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import GROUP, METHOD


def _never(values: Mapping[str, object]) -> str:
    return f"level {values['level']} never works"


EXTENSION = Extension(methods=(METHOD,), params=(replace(GROUP, check=_never),))
