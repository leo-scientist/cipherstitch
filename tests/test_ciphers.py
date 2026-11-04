import os

import pytest

from cipherstitch import catalog, ciphers
from cipherstitch.catalog import CipherId
from cipherstitch.ciphers.base import Cipher
from cipherstitch.errors import DecryptionError

from .conftest import skip_unless_installed

ENCRYPTING = [cipher_id for cipher_id in CipherId if cipher_id != CipherId.NONE]
COUNTER_BOUND = [cipher_id for cipher_id in ENCRYPTING if cipher_id != CipherId.FERNET]


def _key(cipher: Cipher) -> bytes:
    return os.urandom(cipher.key_size)


@pytest.mark.parametrize("size", [0, 1, 15, 16, 1024 * 1024])
@pytest.mark.parametrize("cipher_id", list(CipherId))
def test_roundtrip_and_exact_sealed_size(cipher_id: CipherId, size: int) -> None:
    skip_unless_installed(cipher_id)
    cipher = ciphers.get(cipher_id)
    key = _key(cipher)
    data = os.urandom(size)
    sealed = cipher.seal(key, 1, data)
    assert len(sealed) == cipher.sealed_size(size)
    assert cipher.unseal(key, 1, sealed) == data


@pytest.mark.parametrize("cipher_id", ENCRYPTING)
def test_wrong_key_and_tampering_are_detected(cipher_id: CipherId) -> None:
    skip_unless_installed(cipher_id)
    cipher = ciphers.get(cipher_id)
    key = _key(cipher)
    sealed = cipher.seal(key, 0, b"some data")

    with pytest.raises(DecryptionError):
        cipher.unseal(_key(cipher), 0, sealed)

    for index in (0, len(sealed) // 2, len(sealed) - 1):
        tampered = bytearray(sealed)
        tampered[index] ^= 1

        with pytest.raises(DecryptionError):
            cipher.unseal(key, 0, bytes(tampered))


@pytest.mark.parametrize("size", [0, 15, 16, 24, 39, 40])
@pytest.mark.parametrize("cipher_id", ENCRYPTING)
def test_a_cut_block_is_refused(cipher_id: CipherId, size: int) -> None:
    """Shorter than the nonce and tag, PyNaCl raised a plain ValueError for XChaCha20."""
    skip_unless_installed(cipher_id)
    cipher = ciphers.get(cipher_id)
    key = _key(cipher)

    with pytest.raises(DecryptionError):
        cipher.unseal(key, 0, cipher.seal(key, 0, b"some data" * 8)[:size])


@pytest.mark.parametrize("cipher_id", COUNTER_BOUND)
def test_blocks_are_bound_to_their_counter(cipher_id: CipherId) -> None:
    skip_unless_installed(cipher_id)
    cipher = ciphers.get(cipher_id)
    key = _key(cipher)

    with pytest.raises(DecryptionError):
        cipher.unseal(key, 1, cipher.seal(key, 0, b"data"))


@pytest.mark.parametrize("cipher_id", ENCRYPTING)
def test_output_has_no_fixed_prefix_or_restricted_alphabet(cipher_id: CipherId) -> None:
    skip_unless_installed(cipher_id)
    cipher = ciphers.get(cipher_id)
    first, second = (cipher.seal(_key(cipher), 0, bytes(4096)) for _ in range(2))
    assert first[:4] != second[:4]
    assert len(set(first)) > 200  # base64 text would use at most 65 byte values


@pytest.mark.parametrize(
    ("cipher_id", "header_size", "label"),
    [
        (CipherId.AES_GCM, 23, "+16 B"),
        (CipherId.CHACHA20, 23, "+16 B"),
        (CipherId.XCHACHA20, 47, "+40 B"),
        (CipherId.AES_CBC_HMAC, 64, "+49–64 B"),
        (CipherId.FERNET, 64, "+49–64 B"),
        (CipherId.NONE, 7, "0 B"),
    ],
)
def test_sealed_header_size_and_overhead_label(
    cipher_id: CipherId, header_size: int, label: str
) -> None:
    assert ciphers.get(cipher_id).sealed_size(7) == header_size
    assert ciphers.overhead_label(cipher_id) == label


def test_encrypting_ciphers_skip_missing_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    skip_unless_installed(CipherId.XCHACHA20)
    assert [cipher.id for cipher in ciphers.encrypting()] == ENCRYPTING
    monkeypatch.setattr(catalog.Requirement, "is_installed", property(lambda _self: False))
    assert CipherId.XCHACHA20 not in [cipher.id for cipher in ciphers.encrypting()]
