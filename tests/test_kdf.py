import hashlib
import unicodedata

import pytest
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from cipherstitch import kdf
from cipherstitch.catalog import CipherId
from cipherstitch.errors import CipherStitchError

SALT = b"s" * kdf.SALT_SIZE
# RFC 9106, section 5.3 (Argon2id test vector).
RFC_9106_TAG = bytes.fromhex("0d640df58d78766c08c037a34a8b53c9d01ef0452d75b65eb52520e96b01e659")
# master_key("pässwörd", SALT) with the production profile, and its ORDER_SEED subkey.
PRODUCTION_MASTER_KEY = bytes.fromhex(
    "345c33559a1d81bd5616ed5ac5e1d10950b0403fe299cce0558d4819c0573809"
)
PRODUCTION_ORDER_SEED = bytes.fromhex(
    "c2bc2e70a11c3fb4ffac50c224c8f0300f5c7365e5871374ce4423dea7f92ece"
)
# master_key("pässwörd", SALT) with Research's alternatives at their fixed costs.
ALTERNATIVE_MASTER_KEYS = {
    kdf.Function.SCRYPT: "67811e011c0dc9fb9ae3c8d169202bffd68e096d8677a938bcb73d429da62a61",
    kdf.Function.PBKDF2: "131adb4131f8f4bd52f155f677c6cea173f09ea7d628402779d13f1dcffdb13e",
}
FUNCTIONS = list(kdf.Function)
# RFC 7914, sections 11 and 12: the first 32 bytes of their 64-byte outputs, which a 32-byte
# derivation gives (PBKDF2 makes each 32-byte block on its own, and scrypt ends in PBKDF2).
RFC_7914 = [
    (
        kdf.Function.PBKDF2,
        {"PBKDF2_ITERATIONS": 1},
        ("passwd", b"salt"),
        "55ac046e56e3089fec1691c22544b605f94185216dde0465e68b9d57c20dacbc",
    ),
    (
        kdf.Function.SCRYPT,
        {"SCRYPT_COST": 1024, "SCRYPT_BLOCK_SIZE": 8, "SCRYPT_PARALLELISM": 16},
        ("password", b"NaCl"),
        "fdbabe1c9d3472007856e7190d01e9fe7c6ad7cbc8237830e77376634b373162",
    ),
]


def _profile(function: kdf.Function) -> kdf.Profile:
    return kdf.Profile(32, 1, 1, function=function)


def test_argon2id_matches_rfc_9106() -> None:
    argon2 = Argon2id(
        salt=b"\x02" * 16,
        length=32,
        iterations=3,
        lanes=4,
        memory_cost=32,
        ad=b"\x04" * 12,
        secret=b"\x03" * 8,
    )
    assert argon2.derive(b"\x01" * 32) == RFC_9106_TAG


def test_master_key_uses_the_configured_argon2id_profile() -> None:
    expected = Argon2id(
        salt=SALT,
        length=32,
        iterations=kdf.ARGON2_ITERATIONS,
        lanes=kdf.ARGON2_LANES,
        memory_cost=kdf.ARGON2_MEMORY_KIB,
    ).derive("pässwörd".encode())
    assert kdf.master_key("pässwörd", SALT) == expected


@pytest.mark.parametrize(("function", "costs", "inputs", "expected"), RFC_7914)
def test_scrypt_and_pbkdf2_match_rfc_7914(
    monkeypatch: pytest.MonkeyPatch,
    function: kdf.Function,
    costs: dict[str, int],
    inputs: tuple[str, bytes],
    expected: str,
) -> None:
    for name, value in costs.items():
        monkeypatch.setattr(kdf, name, value)

    assert kdf.master_key(*inputs, _profile(function)).hex() == expected


def test_scrypt_and_pbkdf2_are_hashlibs() -> None:
    """hashlib's, at the fixed costs: the arguments reach the function as meant."""
    if not hasattr(hashlib, "scrypt"):
        pytest.skip("this Python's OpenSSL has no scrypt")

    password = unicodedata.normalize("NFC", "pässwörd").encode()
    memory = 2 * 128 * kdf.SCRYPT_BLOCK_SIZE * kdf.SCRYPT_COST
    scrypt = hashlib.scrypt(
        password,
        salt=SALT,
        n=kdf.SCRYPT_COST,
        r=kdf.SCRYPT_BLOCK_SIZE,
        p=kdf.SCRYPT_PARALLELISM,
        maxmem=memory,
        dklen=32,
    )
    pbkdf2 = hashlib.pbkdf2_hmac("sha256", password, SALT, kdf.PBKDF2_ITERATIONS, 32)

    assert kdf.master_key("pässwörd", SALT, _profile(kdf.Function.SCRYPT)) == scrypt
    assert kdf.master_key("pässwörd", SALT, _profile(kdf.Function.PBKDF2)) == pbkdf2


def test_the_alternatives_are_frozen() -> None:
    """Changing their costs breaks data hidden with them in Research mode."""
    for function, expected in ALTERNATIVE_MASTER_KEYS.items():
        assert kdf.master_key("pässwörd", SALT, _profile(function)).hex() == expected


def test_each_function_derives_its_own_key() -> None:
    keys = {kdf.master_key("pw", SALT, _profile(function)) for function in FUNCTIONS}

    assert len(keys) == len(FUNCTIONS)


def test_master_key_is_deterministic_and_salted() -> None:
    key = kdf.master_key("pw", SALT)
    assert key == kdf.master_key("pw", SALT)
    assert key != kdf.master_key("pw", kdf.new_salt())
    assert key != kdf.master_key("other", SALT)


def test_subkeys_differ_per_purpose() -> None:
    master = kdf.master_key("pw", SALT)
    purposes = [kdf.ORDER_SEED, *CipherId]
    assert len({kdf.subkey(master, purpose) for purpose in purposes}) == len(purposes)
    assert kdf.subkey(master, kdf.ORDER_SEED) == kdf.subkey(master, kdf.ORDER_SEED)
    assert len(kdf.subkey(master, CipherId.AES_CBC_HMAC, 64)) == 64


def test_production_profile_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing the Argon2id profile or the order seed's HKDF info breaks hidden data."""
    monkeypatch.undo()  # drop the fast profile of conftest.py
    master = kdf.master_key("pässwörd", SALT)
    assert master == PRODUCTION_MASTER_KEY
    assert kdf.subkey(master, kdf.ORDER_SEED) == PRODUCTION_ORDER_SEED


@pytest.mark.parametrize("function", FUNCTIONS)
def test_password_is_nfc_normalized(function: kdf.Function) -> None:
    profile = _profile(function)
    composed = unicodedata.normalize("NFC", "йїä")
    decomposed = unicodedata.normalize("NFD", composed)
    assert decomposed != composed
    assert kdf.master_key(decomposed, SALT, profile) == kdf.master_key(composed, SALT, profile)
    # NFC, not NFKC: a no-break space (Option+Space on macOS) stays distinct.
    assert kdf.master_key(f"a{chr(0xA0)}b", SALT, profile) != kdf.master_key("a b", SALT, profile)


def test_a_password_that_cannot_be_encoded_is_refused() -> None:
    """A lone surrogate: what Python makes of bytes no encoding decodes, as on a piped
    stdin under surrogateescape."""
    message = "^Password contains characters that can't be encoded$"

    with pytest.raises(CipherStitchError, match=message):
        kdf.master_key(f"{chr(0xD800)}abc", SALT)

    with pytest.raises(CipherStitchError, match=message):
        kdf.master_key(f"{chr(0xD800)}abc", SALT, kdf.Profile(32, 1, 1, is_normalized=False))
