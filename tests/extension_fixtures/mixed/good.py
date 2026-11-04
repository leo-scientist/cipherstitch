"""The working fixtures' method and cipher in one module."""

from cipherstitch.extensions import Extension
from tests.extension_fixtures.working import byte_lsb, chacha_copy

EXTENSION = Extension(
    methods=byte_lsb.EXTENSION.methods,
    ciphers=chacha_copy.EXTENSION.ciphers,
    summary="Working fixtures",
)
