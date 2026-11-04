"""Stream layouts of a hidden file, and trial reveal (docs/format.md)."""

import hashlib
import os
import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum, StrEnum, auto
from typing import Protocol, assert_never

from cipherstitch import catalog, ciphers, kdf, payload
from cipherstitch.catalog import CipherId, CipherKey, MethodId, MethodKey
from cipherstitch.ciphers.base import Cipher
from cipherstitch.errors import (
    CipherStitchError,
    DecryptionError,
    NoHiddenDataError,
    NotAvailableError,
)
from cipherstitch.payload import HiddenFile

MAGIC = b"CPST"
VERSION = 1
NONCE_SIZE = kdf.SALT_SIZE  # the plain prefix is as long as the encrypted one
_MASK_LABEL = b"cipherstitch plain frame"
_PLAIN = struct.Struct(">4sBBI")  # magic, version, method id, record length
_HEADER = struct.Struct(">BBBI")  # version, cipher id, method id, record length
_HEADER_COUNTER = 0
_RECORD_COUNTER = 1
# Asks a channel for its units in sequence. Empty, so that it can never key an order:
# feistel.Permutation refuses it.
SEQUENTIAL_SEED = b""

# Wire ids are part of the format: never renumber or reuse them. Id 2 is reserved. Built-ins
# take 1–127, extensions declare their own in 128–254 (docs/format.md#ids).
_METHOD_IDS = {
    MethodId.LSB: 1,
    MethodId.DCT: 3,
    MethodId.AUDIO_LSB: 4,
    MethodId.PHASE_CODING: 5,
    MethodId.ECHO_HIDING: 6,
    MethodId.OBJECT_STREAM: 7,
    MethodId.WHITESPACE: 8,
    MethodId.ZERO_WIDTH: 9,
    MethodId.LSB_REPLACEMENT: 10,
}
_CIPHER_IDS = {
    CipherId.AES_GCM: 1,
    CipherId.CHACHA20: 2,
    CipherId.XCHACHA20: 3,
    CipherId.AES_CBC_HMAC: 4,
    CipherId.FERNET: 5,
}
# An extension cipher's HKDF info, from its wire id: part of the format too.
_EXTENSION_KEY_INFO = "cipherstitch extension cipher {}"


def method_wire_id(method: MethodKey) -> int:
    """The id a plain frame and a sealed header store for ``method`` (docs/format.md#ids):
    from the built-ins' table, or else from the loaded extensions."""
    if catalog.is_builtin_method(method):
        return _METHOD_IDS[MethodId(method)]

    return _extension_method_wire_id(method)


def cipher_wire_id(cipher_id: CipherKey) -> int:
    """The id a sealed header stores for ``cipher_id`` (docs/format.md#ids), as
    :func:`method_wire_id`."""
    if catalog.is_builtin_cipher(cipher_id):
        return _CIPHER_IDS[CipherId(cipher_id)]

    return _extension_cipher_wire_id(cipher_id)


def cipher_key_info(cipher_id: CipherKey) -> str:
    """The HKDF info of ``cipher_id``'s key (docs/format.md#keys): a built-in's id, or an
    extension cipher's wire id, so renaming that cipher breaks nothing."""
    if catalog.is_builtin_cipher(cipher_id):
        return CipherId(cipher_id).value

    return _EXTENSION_KEY_INFO.format(_extension_cipher_wire_id(cipher_id))


def _extension_method_wire_id(method: MethodKey) -> int:
    from cipherstitch import extension_loader  # it builds on this module

    loaded = extension_loader.find_method(method)

    if loaded is None:
        raise NotAvailableError(f"Unknown method {method}")

    return loaded.wire_id


def _extension_cipher_wire_id(cipher_id: CipherKey) -> int:
    from cipherstitch import extension_loader  # it builds on this module

    loaded = extension_loader.find_cipher(cipher_id)

    if loaded is None:
        raise NotAvailableError(f"Unknown cipher {cipher_id}")

    return loaded.wire_id


class Capacity(Protocol):
    def capacity(self, prefix_size: int) -> int:
        """Body bytes that fit after a prefix of ``prefix_size`` bytes."""
        ...


class Channel[Output](Capacity, Protocol):
    """A carrier opened for one embedding method: it stores a prefix and a body."""

    def stored_limit(self, prefix_size: int) -> int:
        """The most body bytes a write can have stored after a prefix of ``prefix_size``
        bytes. Reveal checks stored lengths against it: embedding may lower ``capacity``."""
        ...

    def read(self, prefix_size: int, size: int, seed: bytes | None) -> bytes:
        """Up to ``size`` body bytes, in the order derived from ``seed`` if one is given, in
        sequence for :data:`SEQUENTIAL_SEED`. Callers read at most the first 64 bytes or the
        whole body: LSB channels need the body's length to read further."""
        ...

    def write(self, prefix: bytes, body: bytes, seed: bytes | None) -> Output:
        """The carrier with ``prefix`` and ``body`` stored (raises ``CapacityError``)."""
        ...


class UnitOrder(StrEnum):
    """The order of an encrypted body's units (docs/format.md#keyed-order). A plain frame is
    always in the channel's own order, the public one."""

    KEYED = "keyed"  # from the password
    PUBLIC = "public"  # the channel's own, as without a password
    SEQUENTIAL = "sequential"  # Research's: in sequence, never permuted


@dataclass(frozen=True, slots=True)
class Candidate:
    """A method a carrier may hold, and the order of its body."""

    method: MethodKey
    order: UnitOrder


@dataclass(frozen=True, slots=True)
class Revealed:
    file: HiddenFile
    method: MethodKey
    cipher: CipherKey


class Step(Enum):
    """Stages reported while hiding or revealing, in the order they run."""

    LOCATE = auto()
    DERIVE_KEY = auto()
    ENCRYPT = auto()
    EMBED = auto()
    DECRYPT = auto()


StepCallback = Callable[[Step], None]


def _ignore_step(_step: Step) -> None:
    return None


def hide_steps(*, encrypted: bool) -> tuple[Step, ...]:
    return (Step.DERIVE_KEY, Step.ENCRYPT, Step.EMBED) if encrypted else (Step.EMBED,)


def reveal_steps(*, encrypted: bool) -> tuple[Step, ...]:
    return (Step.LOCATE, Step.DERIVE_KEY, Step.DECRYPT) if encrypted else (Step.LOCATE,)


def max_data_size(channel: Capacity, name: str, cipher_id: CipherKey) -> int:
    """Largest file (in bytes) named ``name`` that fits into ``channel``."""
    return max(channel.capacity(0) - framing_size(channel, name, cipher_id), 0)


def framing_size(channel: Capacity, name: str, cipher_id: CipherKey) -> int:
    """The space of ``channel.capacity(0)`` that a file named ``name`` takes besides its
    bytes: the capacity less the largest file that fits, or, when not even an empty file
    fits, what an empty one needs, which is more than the capacity."""
    overhead = payload.record_overhead(name)

    if cipher_id == CipherId.NONE:
        return channel.capacity(0) - channel.capacity(NONCE_SIZE) + _PLAIN.size + overhead

    cipher = ciphers.get(cipher_id)
    room = channel.capacity(kdf.SALT_SIZE) - cipher.sealed_size(_HEADER.size)
    largest = _largest_record(cipher, room) - overhead

    if largest >= 0:
        return channel.capacity(0) - largest

    return channel.capacity(0) - room + cipher.sealed_size(overhead)


def hide[Output](
    channel: Channel[Output],
    file: HiddenFile,
    *,
    method: MethodKey,
    cipher_id: CipherKey,
    password: str | None,
    order: UnitOrder,
    on_step: StepCallback = _ignore_step,
    profile: kdf.Profile | None = None,
    is_masked: bool = True,
) -> Output:
    """``on_step`` runs before each stage of :func:`hide_steps` and may raise to abort.
    ``profile`` is the KDF's, Standard's if ``None``. ``is_masked`` off (Research) stores a
    plain frame unmasked."""
    record = payload.pack_record(file)

    if cipher_id == CipherId.NONE:
        on_step(Step.EMBED)
        nonce = os.urandom(NONCE_SIZE)
        frame = _PLAIN.pack(MAGIC, VERSION, method_wire_id(method), len(record)) + record

        return channel.write(nonce, _mask(nonce, frame, is_masked), None)

    if not password:
        raise CipherStitchError("A password is required to encrypt")

    on_step(Step.DERIVE_KEY)
    salt = kdf.new_salt()
    master = kdf.master_key(password, salt, profile)

    on_step(Step.ENCRYPT)
    cipher = ciphers.get(cipher_id)
    key = kdf.subkey(master, cipher_key_info(cipher.id), cipher.key_size)
    header = _HEADER.pack(VERSION, cipher_wire_id(cipher_id), method_wire_id(method), len(record))
    body = cipher.seal(key, _HEADER_COUNTER, header) + cipher.seal(key, _RECORD_COUNTER, record)

    on_step(Step.EMBED)
    seed = _body_seed(order, kdf.subkey(master, kdf.ORDER_SEED))

    return channel.write(salt, body, seed)


Group = tuple[Channel[object], Sequence[Candidate]]
"""A carrier opened by one channel type, and the method orders to try on it."""


def reveal(
    groups: Sequence[Group],
    password: str | None = None,
    on_step: StepCallback = _ignore_step,
    profile: kdf.Profile | None = None,
    cipher_id: CipherKey | None = None,
    is_masked: bool = True,
) -> Revealed:
    """See docs/format.md#revealing. ``on_step`` runs once before each stage of
    :func:`reveal_steps` and may raise to abort. ``profile`` is the KDF's, Standard's if
    ``None``. ``cipher_id`` limits it to that cipher (or to the plain frame), every installed
    one if ``None``. ``is_masked`` off (Research) reads plain frames unmasked."""
    on_step(Step.LOCATE)

    if cipher_id in (None, CipherId.NONE):
        revealed = _reveal_plain(groups, is_masked)

        if revealed is not None:
            return revealed

    if not password or cipher_id == CipherId.NONE:
        raise NoHiddenDataError(payload.NOT_FOUND_MESSAGE)

    tried = ciphers.encrypting() if cipher_id is None else (ciphers.get(cipher_id),)
    revealed = _reveal_encrypted(groups, password, on_step, profile, tried)

    if revealed is None:
        hint = ciphers.missing_hint() if cipher_id is None else None
        raise NoHiddenDataError(payload.NOT_FOUND_MESSAGE, hint)

    return revealed


def _reveal_plain(groups: Sequence[Group], is_masked: bool) -> Revealed | None:
    for channel, candidates in groups:
        frame = _plain_frame(channel, candidates, is_masked)

        if frame is not None:
            method, length, nonce = frame
            masked = channel.read(NONCE_SIZE, _PLAIN.size + length, None)

            if len(masked) < _PLAIN.size + length:  # a stream channel lost its end
                raise NoHiddenDataError("Hidden data is truncated")

            record = _mask(nonce, masked, is_masked)[_PLAIN.size :]

            return Revealed(payload.unpack_record(record), method, CipherId.NONE)

    return None


def _reveal_encrypted(
    groups: Sequence[Group],
    password: str,
    on_step: StepCallback,
    profile: kdf.Profile | None,
    tried: Sequence[Cipher],
) -> Revealed | None:
    """One key derivation per group that has room for a salt."""
    is_first = True

    for channel, candidates in groups:
        salt = channel.read(0, kdf.SALT_SIZE, None)

        if len(salt) < kdf.SALT_SIZE:
            continue

        if is_first:
            on_step(Step.DERIVE_KEY)

        master = kdf.master_key(password, salt, profile)

        if is_first:
            on_step(Step.DECRYPT)
            is_first = False

        revealed = _try_candidates(channel, candidates, master, tried)

        if revealed is not None:
            return revealed

    return None


def _try_candidates(
    channel: Channel[object],
    candidates: Sequence[Candidate],
    master: bytes,
    tried: Sequence[Cipher],
) -> Revealed | None:
    order_seed = kdf.subkey(master, kdf.ORDER_SEED)

    for candidate in candidates:
        seed = _body_seed(candidate.order, order_seed)

        for cipher in tried:
            revealed = _try_cipher(channel, candidate, seed, cipher, master)

            if revealed is not None:
                return revealed

    return None


def _plain_frame(
    channel: Channel[object], candidates: Sequence[Candidate], is_masked: bool
) -> tuple[MethodKey, int, bytes] | None:
    """Method, record length and nonce of a plain frame at the start of ``channel``, if
    valid."""
    nonce = channel.read(0, NONCE_SIZE, None)
    masked = channel.read(NONCE_SIZE, _PLAIN.size, None)

    if len(nonce) < NONCE_SIZE or len(masked) < _PLAIN.size:
        return None

    magic, version, method_id, length = _PLAIN.unpack(_mask(nonce, masked, is_masked))
    methods = {
        method_wire_id(c.method): c.method for c in candidates if c.order is UnitOrder.PUBLIC
    }

    if magic != MAGIC or version != VERSION or method_id not in methods:
        return None

    if _PLAIN.size + length > channel.stored_limit(NONCE_SIZE):
        return None

    return methods[method_id], length, nonce


def _body_seed(order: UnitOrder, order_seed: bytes) -> bytes | None:
    """What a channel orders an encrypted body by: ``order_seed`` (the ``order`` subkey) for
    the keyed order."""
    match order:
        case UnitOrder.KEYED:
            return order_seed
        case UnitOrder.PUBLIC:
            return None
        case UnitOrder.SEQUENTIAL:
            return SEQUENTIAL_SEED
        case _:
            assert_never(order)


def _mask(nonce: bytes, data: bytes, is_masked: bool = True) -> bytes:
    """``data`` XOR a keystream from a public label and ``nonce``: tools that do not know the
    format see random bytes instead of a plain frame. Masking twice gives ``data`` back.
    ``data`` itself when ``is_masked`` is off."""
    if not is_masked:
        return data

    stream = hashlib.shake_256(_MASK_LABEL + nonce).digest(len(data))

    return (int.from_bytes(data) ^ int.from_bytes(stream)).to_bytes(len(data))


def _try_cipher(
    channel: Channel[object],
    candidate: Candidate,
    seed: bytes | None,
    cipher: Cipher,
    master: bytes,
) -> Revealed | None:
    """The payload if ``cipher`` opens the header in this order, ``None`` if it does not."""
    key = kdf.subkey(master, cipher_key_info(cipher.id), cipher.key_size)
    length = _open_header(channel, candidate, seed, cipher, key)

    if length is None:
        return None

    header_size = cipher.sealed_size(_HEADER.size)
    record_size = cipher.sealed_size(length)
    sealed = channel.read(kdf.SALT_SIZE, header_size + record_size, seed)[header_size:]

    if len(sealed) < record_size:  # a stream channel lost its end
        raise NoHiddenDataError("Hidden data is truncated")

    try:
        record = cipher.unseal(key, _RECORD_COUNTER, sealed)
    except DecryptionError as exc:  # header authentic, record not: modified after hiding
        raise NoHiddenDataError(payload.NOT_FOUND_MESSAGE) from exc

    return Revealed(payload.unpack_record(record), candidate.method, cipher.id)


def _open_header(
    channel: Channel[object],
    candidate: Candidate,
    seed: bytes | None,
    cipher: Cipher,
    key: bytes,
) -> int | None:
    """The record length, if ``key`` opens the sealed header in this order and the header
    names this cipher and method with a record that fits."""
    header_size = cipher.sealed_size(_HEADER.size)
    sealed_header = channel.read(kdf.SALT_SIZE, header_size, seed)

    if len(sealed_header) < header_size:
        return None

    try:
        header = cipher.unseal(key, _HEADER_COUNTER, sealed_header)
    except DecryptionError:
        return None

    # An authentic block from the wrong slot (Fernet ignores the counter): a record of another
    # size fails here, one of the header's size fails on its first byte, the u16 name length's
    # high byte, which is 0 for every valid name (at most 255 bytes), never VERSION.
    if len(header) != _HEADER.size:
        return None

    version, cipher_id, method_id, length = _HEADER.unpack(header)
    expected = (VERSION, cipher_wire_id(cipher.id), method_wire_id(candidate.method))

    if (version, cipher_id, method_id) != expected:
        return None

    if header_size + cipher.sealed_size(length) > channel.stored_limit(kdf.SALT_SIZE):
        return None

    return int(length)


def _largest_record(cipher: Cipher, room: int) -> int:
    """Largest record length whose sealed size fits into ``room`` bytes, or -1."""
    low, high = -1, room

    while low < high:
        middle = (low + high + 1) // 2

        if cipher.sealed_size(middle) <= room:
            low = middle
        else:
            high = middle - 1

    return low
