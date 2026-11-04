"""Bytes a carrier stores as one stream: the prefix and the body concatenated, so methods
built on it are always sequential."""

import secrets
from abc import ABC, abstractmethod
from functools import cached_property

from cipherstitch.errors import CapacityError


class StreamChannel[Output](ABC):
    """A :class:`cipherstitch.container.Channel` holding at most ``limit`` bytes. It reads
    streams of up to ``read_limit`` bytes (``limit`` if not given): what a larger limit may
    have written."""

    def __init__(self, limit: int, read_limit: int | None = None) -> None:
        self._limit = limit
        self._read_limit = limit if read_limit is None else read_limit

    def capacity(self, prefix_size: int) -> int:
        return max(self._limit - prefix_size, 0)

    def stored_limit(self, prefix_size: int) -> int:
        return max(self._read_limit - prefix_size, 0)

    def read(self, prefix_size: int, size: int, seed: bytes | None) -> bytes:
        return self._stored[prefix_size : prefix_size + size]

    def write(self, prefix: bytes, body: bytes, seed: bytes | None) -> Output:
        available = self.capacity(len(prefix))

        if len(prefix) > self.capacity(0) or len(body) > available:
            raise CapacityError(
                f"Payload is {len(body)} bytes, this carrier holds at most {available}"
            )

        return self._written(prefix + body)

    @cached_property
    def _stored(self) -> bytes:
        return self._read_stream()

    @abstractmethod
    def _read_stream(self) -> bytes:
        """The stream stored in the carrier (empty if there is none)."""

    @abstractmethod
    def _written(self, stream: bytes) -> Output:
        """The carrier storing ``stream`` (replacing any stream it held)."""


def padded(stream: bytes, limit: int) -> bytes:
    """``stream`` followed by random bytes, up to a random length of at most ``limit``: what
    the carrier stores must not show the payload's size."""
    padding = secrets.randbelow(limit - len(stream) + 1)

    return stream + secrets.token_bytes(padding)
