"""ChaCha20 without Poly1305: it encrypts but can't tell a changed block."""

from dataclasses import replace

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import CIPHER


class Unauthenticated:
    key_size = 32

    def sealed_size(self, size: int) -> int:
        return size

    def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
        return _keystream(key, counter, data)

    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        return _keystream(key, counter, data)


def _keystream(key: bytes, counter: int, data: bytes) -> bytes:
    nonce = counter.to_bytes(16)
    encryptor = Cipher(algorithms.ChaCha20(key, nonce), mode=None).encryptor()

    return encryptor.update(data) + encryptor.finalize()


EXTENSION = Extension(ciphers=(replace(CIPHER, cipher=Unauthenticated()),))
