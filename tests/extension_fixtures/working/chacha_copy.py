"""ChaCha20-Poly1305 again, under an extension's key info. Wire id 201: the format vectors
store it."""

from cipherstitch.extensions import CipherSpec, Extension
from tests.extension_fixtures.parts import ChaChaCopy

EXTENSION = Extension(
    ciphers=(
        CipherSpec(
            "chacha-copy",
            201,
            "ChaCha20 copy",
            ChaChaCopy(),
            summary="ChaCha20-Poly1305 for the tests.",
        ),
    ),
)
