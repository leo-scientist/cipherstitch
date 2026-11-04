"""A choice between on and off, which the settings would never take: that is a switch."""

from dataclasses import replace

from cipherstitch.extensions import Extension, Option, ParamType
from tests.extension_fixtures.parts import GROUP, LEVEL, METHOD

CHOICE = replace(
    LEVEL,
    type=ParamType.ENUM,
    default="auto",
    options=(Option("auto", "Auto"), Option(True, "On"), Option(False, "Off")),
    minimum=None,
    maximum=None,
    step=None,
)
EXTENSION = Extension(methods=(METHOD,), params=(replace(GROUP, params=(CHOICE,)),))
