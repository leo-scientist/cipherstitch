"""AES-256-CBC + HMAC-SHA256, encrypt-then-MAC (docs/format.md#ciphers)."""

import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from cipherstitch.catalog import CipherId
from cipherstitch.ciphers.base import (
    CBC_BLOCK_SIZE,
    CBC_IV_SIZE,
    HMAC_SHA256_SIZE,
    cbc_hmac_sealed_size,
)
from cipherstitch.errors import DecryptionError

_KEY_SIZE = 32
_COUNTER_SIZE = 8


class AesCbcHmac:
    id = CipherId.AES_CBC_HMAC
    key_size = 2 * _KEY_SIZE

    def sealed_size(self, size: int) -> int:
        return cbc_hmac_sealed_size(size)

    def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
        iv = os.urandom(CBC_IV_SIZE)
        padder = PKCS7(CBC_BLOCK_SIZE * 8).padder()
        padded = padder.update(data) + padder.finalize()

        encryptor = Cipher(algorithms.AES(key[:_KEY_SIZE]), modes.CBC(iv)).encryptor()
        body = iv + encryptor.update(padded) + encryptor.finalize()

        return body + _tag(key[_KEY_SIZE:], counter, body)

    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        body, tag = data[:-HMAC_SHA256_SIZE], data[-HMAC_SHA256_SIZE:]

        if not hmac.compare_digest(_tag(key[_KEY_SIZE:], counter, body), tag):
            raise DecryptionError("Authentication failed")

        iv, ciphertext = body[:CBC_IV_SIZE], body[CBC_IV_SIZE:]
        decryptor = Cipher(algorithms.AES(key[:_KEY_SIZE]), modes.CBC(iv)).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()

        unpadder = PKCS7(CBC_BLOCK_SIZE * 8).unpadder()

        return unpadder.update(padded) + unpadder.finalize()


def _tag(mac_key: bytes, counter: int, body: bytes) -> bytes:
    message = counter.to_bytes(_COUNTER_SIZE, "big") + body

    return hmac.new(mac_key, message, hashlib.sha256).digest()
