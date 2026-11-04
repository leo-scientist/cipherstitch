"""unseal lets the AEAD's own exception out, a ValueError, instead of DecryptionError."""

from dataclasses import replace

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import CIPHER, ChaChaCopy


class Leaky(ChaChaCopy):
    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        try:
            return ChaCha20Poly1305(key).decrypt(counter.to_bytes(12), data, None)
        except InvalidTag as exc:
            raise ValueError("bad tag") from exc


EXTENSION = Extension(ciphers=(replace(CIPHER, cipher=Leaky()),))
