"""Fernet as a raw token without its constant prefix, so it looks random
(docs/format.md#ciphers)."""

import base64

from cryptography.fernet import Fernet, InvalidToken

from cipherstitch.catalog import CipherId
from cipherstitch.ciphers.base import cbc_hmac_sealed_size
from cipherstitch.errors import DecryptionError

_CONSTANT_PREFIX = b"\x80" + bytes(8)


class FernetRaw:
    id = CipherId.FERNET
    key_size = 32  # Fernet's signing key | encryption key

    def sealed_size(self, size: int) -> int:
        return cbc_hmac_sealed_size(size)

    def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
        token = _fernet(key).encrypt_at_time(data, current_time=0)

        return base64.urlsafe_b64decode(token)[len(_CONSTANT_PREFIX) :]

    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        token = base64.urlsafe_b64encode(_CONSTANT_PREFIX + data)

        try:
            return _fernet(key).decrypt(token)
        except InvalidToken as exc:
            raise DecryptionError("Authentication failed") from exc


def _fernet(key: bytes) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(key))
