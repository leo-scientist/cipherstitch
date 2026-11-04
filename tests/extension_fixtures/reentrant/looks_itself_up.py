"""Looks the loaded extensions up while it is imported: the load isn't done, so it sees none."""

from cipherstitch import extension_loader
from cipherstitch.extensions import CarrierKind, Extension, MethodSpec
from tests.extension_fixtures.parts import never_opened

SEEN_WHILE_LOADING = extension_loader.load()
FOUND_WHILE_LOADING = extension_loader.find_method("looks-itself-up")
EXTENSION = Extension(
    methods=(
        MethodSpec(
            "looks-itself-up", 210, "Looks itself up", CarrierKind.TEXT, (".txt",), never_opened
        ),
    ),
)
