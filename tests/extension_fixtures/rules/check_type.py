"""The check is a string."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import GROUP, METHOD

CHECK = "level < 4"
EXTENSION = Extension(
    methods=(METHOD,),
    params=(replace(GROUP, check=CHECK),),  # type: ignore[arg-type]
)
