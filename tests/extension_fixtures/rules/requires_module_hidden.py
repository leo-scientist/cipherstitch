"""requires names a module with a zero-width joiner, which ``str.isidentifier`` accepts."""

from dataclasses import replace

from cipherstitch.extensions import Extension, Requirement
from tests.extension_fixtures.parts import METHOD

MODULE = "sci" + chr(0x200D) + "py"
EXTENSION = Extension(methods=(replace(METHOD, requires=Requirement(MODULE)),))
