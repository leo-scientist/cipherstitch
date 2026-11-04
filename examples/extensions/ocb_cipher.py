"""AES-256-OCB3, an example cipher (docs/extensions.md), from the cryptography package that
CipherStitch already needs.

Cipher wire id 128. Every file this cipher seals stores it, and its key is derived from it,
so never change it.
"""

from cryptography.hazmat.primitives.ciphers.aead import AESOCB3

from cipherstitch.extensions import CipherSpec, CounterNonceAead, Extension


class AesOcb3(CounterNonceAead):
    """A 32-byte key, the block counter as its 12-byte nonce and a 16-byte tag."""

    _algorithm = AESOCB3


EXTENSION = Extension(
    ciphers=(
        CipherSpec(
            "aes-256-ocb3",
            128,
            "AES-256-OCB3",
            AesOcb3(),
            summary="AES in OCB mode: one pass encrypts and authenticates.",
            pros=("One pass, no separate MAC",),
            cons=("Rarely used outside research",),
        ),
    ),
    summary="An example cipher: AES-256-OCB3 from cryptography.",
)
