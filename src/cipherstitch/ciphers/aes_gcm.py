"""AES-256-GCM with counter nonces."""

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from cipherstitch.catalog import CipherId
from cipherstitch.ciphers.base import CounterNonceAead


class AesGcm(CounterNonceAead):
    id = CipherId.AES_GCM
    _algorithm = AESGCM
