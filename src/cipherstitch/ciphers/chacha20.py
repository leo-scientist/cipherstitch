"""ChaCha20-Poly1305 with counter nonces."""

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from cipherstitch.catalog import CipherId
from cipherstitch.ciphers.base import CounterNonceAead


class ChaCha20(CounterNonceAead):
    id = CipherId.CHACHA20
    _algorithm = ChaCha20Poly1305
