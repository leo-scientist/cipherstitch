"""The master key (Argon2id, or Research's scrypt or PBKDF2) and HKDF-SHA256 subkeys
(docs/format.md#keys)."""

import os
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from cipherstitch.errors import CipherStitchError

SALT_SIZE = 16
ORDER_SEED = "order"
# About 0.2 s on an Apple M3 Pro.
ARGON2_MEMORY_KIB = 256 * 1024
ARGON2_ITERATIONS = 6
ARGON2_LANES = 4
# Research's alternatives, at the costs of OWASP's password storage cheat sheet: both are
# far cheaper to brute-force than Argon2id at Standard's.
SCRYPT_COST = 1 << 17  # N: 128 MiB at r = 8
SCRYPT_BLOCK_SIZE = 8
SCRYPT_PARALLELISM = 1
PBKDF2_ITERATIONS = 600_000
_MASTER_KEY_SIZE = 32
_SUBKEY_SIZE = 32


class Function(StrEnum):
    """What derives the master key. Standard's is Argon2id."""

    ARGON2ID = "argon2id"
    SCRYPT = "scrypt"
    PBKDF2 = "pbkdf2"

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    Function.ARGON2ID: "Argon2id",
    Function.SCRYPT: "scrypt",
    Function.PBKDF2: "PBKDF2-SHA256",
}


@dataclass(frozen=True, slots=True)
class Profile:
    """Argon2id's costs, whether the password is NFC-normalized first, and the function
    (Argon2id's costs apply to it only). Revealing needs the profile that hid the file:
    nothing in it records the profile."""

    memory_kib: int
    iterations: int
    lanes: int
    is_normalized: bool = True
    function: Function = Function.ARGON2ID


def standard_profile() -> Profile:
    """Read when called, so that the tests can lower the costs."""
    return Profile(ARGON2_MEMORY_KIB, ARGON2_ITERATIONS, ARGON2_LANES)


def new_salt() -> bytes:
    return os.urandom(SALT_SIZE)


def master_key(password: str, salt: bytes, profile: Profile | None = None) -> bytes:
    """Normalizes the password to NFC (RFC 8265) unless ``profile`` says not to: a composed and
    a decomposed "й" match. ``None`` is :func:`standard_profile`."""
    if profile is None:
        profile = standard_profile()

    text = unicodedata.normalize("NFC", password) if profile.is_normalized else password

    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:  # a lone surrogate, from bytes no encoding decodes
        raise CipherStitchError("Password contains characters that can't be encoded") from exc

    return _derivation(salt, profile).derive(encoded)


def _derivation(salt: bytes, profile: Profile) -> Argon2id | Scrypt | PBKDF2HMAC:
    """The function's costs are read when called, so that the tests can lower them."""
    match profile.function:
        case Function.ARGON2ID:
            return Argon2id(
                salt=salt,
                length=_MASTER_KEY_SIZE,
                iterations=profile.iterations,
                lanes=profile.lanes,
                memory_cost=profile.memory_kib,
            )
        case Function.SCRYPT:
            return Scrypt(
                salt=salt,
                length=_MASTER_KEY_SIZE,
                n=SCRYPT_COST,
                r=SCRYPT_BLOCK_SIZE,
                p=SCRYPT_PARALLELISM,
            )
        case Function.PBKDF2:
            return PBKDF2HMAC(
                algorithm=SHA256(),
                length=_MASTER_KEY_SIZE,
                salt=salt,
                iterations=PBKDF2_ITERATIONS,
            )
        case _:
            assert_never(profile.function)


def subkey(master: bytes, purpose: str, size: int = _SUBKEY_SIZE) -> bytes:
    """``purpose`` (:data:`ORDER_SEED` or a cipher id) is the HKDF info: never rename one."""
    hkdf = HKDF(algorithm=SHA256(), length=size, salt=None, info=purpose.encode("utf-8"))

    return hkdf.derive(master)
