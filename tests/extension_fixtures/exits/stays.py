from cipherstitch.extensions import CarrierKind, Extension, MethodSpec
from tests.extension_fixtures.parts import never_opened

EXTENSION = Extension(
    methods=(MethodSpec("stays", 142, "Stays", CarrierKind.TEXT, (".txt",), never_opened),),
)
