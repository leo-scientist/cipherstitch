"""Key derivation, and how the frame is masked and ordered: every method's."""

from cipherstitch import kdf, standard
from cipherstitch.catalog import MethodId
from cipherstitch.container import UnitOrder
from cipherstitch.methods import feistel
from cipherstitch.params.groups.coding import TRELLIS, TRELLIS_METHODS
from cipherstitch.params.model import (
    Encryption,
    Group,
    Kind,
    Option,
    Param,
    Scope,
    Trait,
    experimental,
)
from cipherstitch.params.model import ParamType as T
from cipherstitch.password import STRENGTH_LABELS

F, E, A = Kind.FORMAT, Kind.EMBEDDER, Kind.GUIDANCE
_ZERO_WIDTH = frozenset({MethodId.ZERO_WIDTH})
_KEYED = TRELLIS_METHODS | _ZERO_WIDTH
_KIB_PER_MIB = 1024


def _mebibytes(*sizes: int) -> tuple[Option, ...]:
    return tuple(Option(size * _KIB_PER_MIB, f"{size} MiB") for size in sizes)


CRYPTO = Group(
    "crypto",
    "Key derivation",
    "key",
    "Applies to every encrypted payload. Weaker settings make password guessing cheaper.",
    (
        Param(
            "kdf",
            "Key derivation function",
            "kdf.master_key",
            T.ENUM,
            "argon2id",
            F,
            options=(
                Option(kdf.Function.ARGON2ID.value, kdf.Function.ARGON2ID.label),
                experimental(kdf.Function.SCRYPT.value, kdf.Function.SCRYPT.label),
                experimental(kdf.Function.PBKDF2.value, kdf.Function.PBKDF2.label),
            ),
            note="Both alternatives are much cheaper to brute-force than Argon2id. Their costs "
            "are fixed (scrypt N = 2¹⁷, r = 8, p = 1, PBKDF2 600 000 iterations), and the "
            "Argon2 rows apply to Argon2id only.",
        ),
        Param(
            "mem",
            "Argon2 memory",
            "ARGON2_MEMORY_KIB",
            T.ENUM,
            kdf.ARGON2_MEMORY_KIB,
            F,
            options=(*_mebibytes(64, 128, 256, 512), Option(1024 * _KIB_PER_MIB, "1 GiB")),
            note="Less memory makes password guessing cheaper. More slows hiding and revealing.",
        ),
        Param(
            "iter",
            "Argon2 iterations",
            "ARGON2_ITERATIONS",
            T.INT,
            kdf.ARGON2_ITERATIONS,
            F,
            minimum=1,
            maximum=16,
            step=1,
        ),
        Param(
            "lanes",
            "Argon2 lanes",
            "ARGON2_LANES",
            T.INT,
            kdf.ARGON2_LANES,
            F,
            minimum=1,
            maximum=16,
            step=1,
        ),
        Param(
            "nfc",
            "NFC-normalise the password",
            'unicodedata.normalize("NFC")',
            T.BOOL,
            True,
            F,
            note="Off: the same password typed on another system may not open the file.",
        ),
        Param(
            "minpw",
            "Minimum password strength",
            "MIN_PASSWORD_STRENGTH · zxcvbn, first 72 characters",
            T.ENUM,
            standard.MIN_PASSWORD_STRENGTH,
            A,
            options=(
                experimental(0, "No minimum"),
                *(Option(score, STRENGTH_LABELS[score]) for score in (2, 3, 4)),
            ),
        ),
    ),
    scope=Scope(encryption=Encryption.ON),
)

FRAME = Group(
    "frame",
    "Container & order",
    "shuffle",
    "How the frame is written and in which order the carrier's units are visited.",
    (
        Param(
            "mask",
            "Frame mask",
            "_mask · SHAKE-256",
            T.ENUM,
            "shake256",
            F,
            options=(Option("shake256", "SHAKE-256 keystream"), Option("off", "Off")),
            note="Without the mask, anyone who reads the carrier's bits sees the CPST signature "
            "and the payload.",
            scopes=(Scope(encryption=Encryption.OFF),),
        ),
        Param(
            "order",
            "Unit order",
            "UnitOrder · UNKEYED_SEED · SEQUENTIAL_SEED",
            T.ENUM,
            UnitOrder.KEYED.value,
            E,
            options=(
                Option(UnitOrder.KEYED.value, "Keyed (from the password)"),
                Option(UnitOrder.PUBLIC.value, "Public (as without a password)"),
                experimental(UnitOrder.SEQUENTIAL.value, "Sequential"),
            ),
            note="A public or sequential order lets an attacker test exactly the positions "
            "that hold data. With sequential unit spreading too, the payload fills the carrier "
            "from its start. For zero-width, sequential is the same as public. Standard's "
            "reveal tries the keyed and public orders, and Research's tries all three.",
            scopes=(Scope(_KEYED, Encryption.ON, trait=Trait.KEYED),),
        ),
        Param(
            "rounds",
            "Feistel rounds",
            "feistel.ROUNDS",
            T.INT,
            feistel.ROUNDS,
            F,
            minimum=2,
            maximum=16,
            step=1,
            note="Fewer rounds give a less random order. Zero-width uses them in the keyed order "
            "only.",
            # Zero-width permutes its units only in the keyed order: without a password, or with
            # a public order, they stay in sequence. The scopes cannot see the order's value.
            scopes=(TRELLIS, Scope(_ZERO_WIDTH, Encryption.ON)),
        ),
    ),
)
