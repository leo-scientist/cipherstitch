"""A package extension keeps its helpers inside its own package."""

from cipherstitch.extensions import CarrierKind, MethodSpec
from tests.extension_fixtures.parts import never_opened

ALPHA = MethodSpec("alpha", 140, "Alpha", CarrierKind.TEXT, (".md",), never_opened)
