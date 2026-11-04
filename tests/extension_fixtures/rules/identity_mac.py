"""An HMAC after the plaintext: authenticated, but nothing is encrypted."""

import hashlib
import hmac
from dataclasses import replace

from cipherstitch.extensions import DecryptionError, Extension
from tests.extension_fixtures.parts import CIPHER

_TAG_SIZE = 32


class SignedPlaintext:
    key_size = 32

    def sealed_size(self, size: int) -> int:
        return size + _TAG_SIZE

    def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
        return data + _tag(key, counter, data)

    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        body, tag = data[:-_TAG_SIZE], data[-_TAG_SIZE:]

        if len(data) < _TAG_SIZE or not hmac.compare_digest(tag, _tag(key, counter, body)):
            raise DecryptionError("Authentication failed")

        return body


def _tag(key: bytes, counter: int, data: bytes) -> bytes:
    return hmac.digest(key, counter.to_bytes(8) + data, hashlib.sha256)


EXTENSION = Extension(ciphers=(replace(CIPHER, cipher=SignedPlaintext()),))
