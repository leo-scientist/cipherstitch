from cipherstitch.extensions import CarrierKind, Extension, MethodSpec
from tests.extension_fixtures.parts import never_opened

EXTENSION = Extension(
    methods=(MethodSpec("beta", 141, "Beta", CarrierKind.TEXT, (".txt",), never_opened),),
    summary="A module",
)
