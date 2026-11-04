"""Every cipher of the catalog by id, and the size overhead each one adds. :func:`get` looks
an id that isn't a built-in's up in the extensions (docs/architecture.md#extensions)."""

from cipherstitch import catalog
from cipherstitch.catalog import CipherId, CipherKey
from cipherstitch.ciphers.aes_cbc_hmac import AesCbcHmac
from cipherstitch.ciphers.aes_gcm import AesGcm
from cipherstitch.ciphers.base import CBC_BLOCK_SIZE, Cipher
from cipherstitch.ciphers.chacha20 import ChaCha20
from cipherstitch.ciphers.fernet import FernetRaw
from cipherstitch.ciphers.none import NoCipher
from cipherstitch.ciphers.xchacha20 import XChaCha20
from cipherstitch.errors import NotAvailableError

_CIPHERS: dict[CipherKey, Cipher] = {
    cipher.id: cipher
    for cipher in (AesGcm(), ChaCha20(), XChaCha20(), AesCbcHmac(), FernetRaw(), NoCipher())
}


def get(cipher_id: CipherKey) -> Cipher:
    if catalog.is_builtin_cipher(cipher_id):
        return _CIPHERS[cipher_id]

    from cipherstitch import extension_loader  # it builds on this module

    loaded = extension_loader.find_cipher(cipher_id)

    if loaded is None:
        raise NotAvailableError(f"Unknown cipher {cipher_id}")

    return loaded.cipher


def encrypting() -> tuple[Cipher, ...]:
    """Every built-in cipher except "none" whose optional dependency is installed, in catalog
    order: what Standard's reveal tries, so never an extension's."""
    return tuple(
        cipher
        for cipher in _CIPHERS.values()
        if cipher.id != CipherId.NONE and is_installed(cipher.id)
    )


def is_installed(cipher_id: CipherKey) -> bool:
    requirement = catalog.cipher(cipher_id).requires

    return requirement is None or requirement.is_installed


def missing_hint() -> str | None:
    """How to install the ciphers reveal cannot try, or ``None`` if every one is installed."""
    hints = [
        entry.requires.install_hint(entry.name)
        for entry in catalog.CIPHERS
        if entry.requires is not None and not entry.requires.is_installed
    ]

    return ". ".join(hints) or None


def overhead_label(cipher_id: CipherKey) -> str:
    """Bytes ``cipher_id`` adds to a block, e.g. ``+16 B`` or ``+49–64 B`` (with padding)."""
    cipher = get(cipher_id)
    overheads = {cipher.sealed_size(size) - size for size in range(2 * CBC_BLOCK_SIZE)}
    low, high = min(overheads), max(overheads)

    if high == 0:
        return "0 B"

    return f"+{low} B" if low == high else f"+{low}–{high} B"
