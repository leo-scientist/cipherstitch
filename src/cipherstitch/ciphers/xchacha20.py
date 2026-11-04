"""XChaCha20-Poly1305 (PyNaCl): a random nonce in front, and the block counter as
associated data so that blocks cannot be swapped."""

import os

from cipherstitch.catalog import CipherId
from cipherstitch.ciphers.base import AEAD_TAG_SIZE
from cipherstitch.errors import DecryptionError

_NONCE_SIZE = 24
_COUNTER_SIZE = 8


class XChaCha20:
    id = CipherId.XCHACHA20
    key_size = 32

    def sealed_size(self, size: int) -> int:
        return _NONCE_SIZE + size + AEAD_TAG_SIZE

    def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
        from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_encrypt

        nonce = os.urandom(_NONCE_SIZE)

        return nonce + crypto_aead_xchacha20poly1305_ietf_encrypt(
            data, _associated_data(counter), nonce, key
        )

    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt
        from nacl.exceptions import CryptoError

        if len(data) < _NONCE_SIZE + AEAD_TAG_SIZE:  # PyNaCl raises a plain ValueError
            raise DecryptionError("Authentication failed")

        nonce, sealed = data[:_NONCE_SIZE], data[_NONCE_SIZE:]

        try:
            return crypto_aead_xchacha20poly1305_ietf_decrypt(
                sealed, _associated_data(counter), nonce, key
            )
        except CryptoError as exc:
            raise DecryptionError("Authentication failed") from exc


def _associated_data(counter: int) -> bytes:
    return counter.to_bytes(_COUNTER_SIZE, "big")
