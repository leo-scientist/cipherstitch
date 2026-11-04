"""ChaCha20-Poly1305 under one nonce whatever the counter: the header and the record would
share a keystream."""

from dataclasses import replace

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from cipherstitch.extensions import DecryptionError, Extension
from tests.extension_fixtures.parts import CIPHER, ChaChaCopy

_NONCE = bytes(12)


class FixedNonce(ChaChaCopy):
    def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
        return ChaCha20Poly1305(key).encrypt(_NONCE, data, None)

    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        try:
            return ChaCha20Poly1305(key).decrypt(_NONCE, data, None)
        except Exception as exc:
            raise DecryptionError("Authentication failed") from exc


EXTENSION = Extension(ciphers=(replace(CIPHER, cipher=FixedNonce()),))
