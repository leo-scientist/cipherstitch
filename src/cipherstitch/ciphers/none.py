"""No encryption: the identity. Used for overhead labels and capacity estimates."""

from cipherstitch.catalog import CipherId


class NoCipher:
    id = CipherId.NONE
    key_size = 0

    def sealed_size(self, size: int) -> int:
        return size

    def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
        return data

    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        return data
