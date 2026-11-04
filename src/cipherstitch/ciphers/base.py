"""What every cipher provides: sealing a block under a key and a per-block counter."""

from collections.abc import Callable
from typing import Protocol

from cryptography.exceptions import InvalidTag

from cipherstitch.catalog import CipherKey
from cipherstitch.errors import DecryptionError

AEAD_TAG_SIZE = 16
CBC_IV_SIZE = 16
CBC_BLOCK_SIZE = 16
HMAC_SHA256_SIZE = 32
_NONCE_SIZE = 12


class Sealer(Protocol):
    """A cipher without its id: what an extension's ``CipherSpec`` takes."""

    @property
    def key_size(self) -> int: ...

    def sealed_size(self, size: int) -> int:
        """Exact length of ``seal`` output for ``size`` bytes of input."""
        ...

    def seal(self, key: bytes, counter: int, data: bytes) -> bytes: ...

    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        """Reverse :meth:`seal`. Raises :class:`DecryptionError` on any mismatch."""
        ...


class Cipher(Sealer, Protocol):
    @property
    def id(self) -> CipherKey: ...


def cbc_hmac_sealed_size(size: int) -> int:
    """IV, PKCS7-padded ciphertext (always at least one byte of padding) and an HMAC-SHA256."""
    return CBC_IV_SIZE + (size // CBC_BLOCK_SIZE + 1) * CBC_BLOCK_SIZE + HMAC_SHA256_SIZE


class _Aead(Protocol):
    def encrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes: ...

    def decrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes: ...


class CounterNonceAead:
    """An AEAD whose nonce is the block counter.

    Safe because every embedding derives fresh keys from a fresh salt, so a (key, nonce)
    pair is never reused.
    """

    key_size = 32
    _algorithm: Callable[[bytes], _Aead]

    def sealed_size(self, size: int) -> int:
        return size + AEAD_TAG_SIZE

    def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
        return self._algorithm(key).encrypt(_nonce(counter), data, None)

    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        try:
            return self._algorithm(key).decrypt(_nonce(counter), data, None)
        except InvalidTag as exc:
            raise DecryptionError("Authentication failed") from exc


def _nonce(counter: int) -> bytes:
    return counter.to_bytes(_NONCE_SIZE, "big")
