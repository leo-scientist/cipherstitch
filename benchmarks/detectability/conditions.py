"""Every condition of the study and the cover it is told apart from: round 1's reduced variant
(``results/protocol.md``), round 2 (``results/round-2/protocol.md``), round 3
(``results/round-3/protocol.md``) and round 4 (``results/round-4/protocol.md``). Standard
library only."""

from dataclasses import dataclass

CIPHERSTITCH_FILLS = (1, 5, 10, 20, 50)
REFERENCE_FILLS = (5, 20)
JNOSI_FILLS = (5, 20)
LSB_RATES = (140, 210, 300)  # round 2's root rates, in bits per square root of N
DCT_RATES = (12, 19, 28)
LSB3_RATES = (25, 40, 60, 90)  # round 3's, bracketing round 2's crossings (33 and 7) as moved
DCT3_RATES = (10, 18, 30)  # by ternary codes and polarization, and P-SI's 20% payloads (r 45);
# 90 came later (round 3's change log)
PSI3_RATES = (45, 65, 90)
CROPS_PER_IMAGE = 4  # the scaling check's 256 × 256 quadrants of a 512 × 512 picture


def at_fill(prefix: str, fill: int) -> str:
    """A condition's name at a fill, e.g. ``lsb-f05``."""
    return f"{prefix}-f{fill:02d}"


def at_rate(prefix: str, rate: int) -> str:
    """A condition's name at a root rate, e.g. ``lsb2-r140``."""
    return f"{prefix}-r{rate}"


@dataclass(frozen=True, slots=True)
class Condition:
    name: str  # an embedded folder, or a cover folder for E-C
    kind: str  # "lsb" (PNG, the lsb set) or "jpeg" (JPEG, the jpeg set)
    cover: str  # the cover condition (features.COVER_FOLDERS)
    group: str  # what the report groups it under
    fill: int | None = None
    rate: int | None = None  # root rate
    images: str = ""  # the index set when it is not ``kind``'s: the 256 × 256 crops

    @property
    def image_set(self) -> str:
        return self.images or self.kind


def _fills(
    prefix: str, kind: str, cover: str, group: str, fills: tuple[int, ...]
) -> list[Condition]:
    return [Condition(at_fill(prefix, fill), kind, cover, group, fill) for fill in fills]


def _rates(
    prefix: str, kind: str, cover: str, group: str, rates: tuple[int, ...], images: str = ""
) -> list[Condition]:
    return [
        Condition(at_rate(prefix, rate), kind, cover, group, rate=rate, images=images)
        for rate in rates
    ]


LSB = [
    *_fills("lsb", "lsb", "cover", "CipherStitch image LSB", CIPHERSTITCH_FILLS),
    *_fills("openstego", "lsb", "cover", "OpenStego RandomLSB", REFERENCE_FILLS),
    *_fills("lsbr", "lsb", "cover", "LSB replacement (simulated)", REFERENCE_FILLS),
    *_fills("lsbm", "lsb", "cover", "LSB matching (simulated)", REFERENCE_FILLS),
    *_fills(
        "lsbmh", "lsb", "cover", "LSB matching, Hamming efficiency (simulated)", REFERENCE_FILLS
    ),
    *_fills("hill", "lsb", "cover", "HILL (simulated)", REFERENCE_FILLS),
]
JPEG = [
    *_fills("dct-q75", "jpeg", "q75", "CipherStitch DCT on a JPEG (QF 75)", CIPHERSTITCH_FILLS),
    *_fills("steghide", "jpeg", "q75", "steghide", REFERENCE_FILLS),
    *_fills("nsf5", "jpeg", "q75", "nsF5 (simulated)", REFERENCE_FILLS),
    Condition("nsf5-bpnzac020", "jpeg", "q75", "nsF5 at 0.2 bpnzAC (simulated)"),
    *_fills(
        "psi", "jpeg", "p0", "CipherStitch DCT from a TIFF precover (P-SI)", CIPHERSTITCH_FILLS
    ),
    *_fills(
        "pnosi", "jpeg", "p0", "CipherStitch DCT on P-0, no side information", CIPHERSTITCH_FILLS
    ),
    *_fills(
        "jnosi", "jpeg", "jnosi-cover", "CipherStitch DCT on Pillow's JPEG (J-noSI)", JNOSI_FILLS
    ),
    Condition("p0", "jpeg", "jnosi-cover", "E-C: CipherStitch's compressor against Pillow's"),
]
ALL = LSB + JPEG  # round 1
ROUND_2 = [
    *_fills("lsb2", "lsb", "cover", "CipherStitch image LSB, round 2", REFERENCE_FILLS),
    *_rates("lsb2", "lsb", "cover", "CipherStitch image LSB at a root rate", LSB_RATES),
    *_fills(
        "dct2-q75", "jpeg", "q75", "CipherStitch DCT on a JPEG (QF 75), round 2", REFERENCE_FILLS
    ),
    *_rates("dct2-q75", "jpeg", "q75", "CipherStitch DCT (QF 75) at a root rate", DCT_RATES),
    *_fills(
        "psi2",
        "jpeg",
        "jnosi-cover",
        "CipherStitch DCT from a TIFF precover (P-SI), round 2",
        REFERENCE_FILLS,
    ),
    *_fills(
        "jnosi2",
        "jpeg",
        "jnosi-cover",
        "CipherStitch DCT on Pillow's JPEG (J-noSI), round 2",
        REFERENCE_FILLS,
    ),
    *_fills("hstego-png", "lsb", "cover", "HStego S-UNIWARD", REFERENCE_FILLS),
    *_fills("hstego-q75", "jpeg", "q75", "HStego J-UNIWARD", REFERENCE_FILLS),
    *_rates(
        "lsb2-crop",
        "lsb",
        "crop-cover",
        "CipherStitch image LSB at a root rate, 256 × 256",
        LSB_RATES,
        "lsb-crops",
    ),
    *_rates(
        "dct2-q75-crop",
        "jpeg",
        "crop-q75",
        "CipherStitch DCT (QF 75) at a root rate, 256 × 256",
        DCT_RATES,
        "jpeg-crops",
    ),
]
ROUND_3 = [
    *_fills("lsb3", "lsb", "cover", "CipherStitch image LSB, round 3", REFERENCE_FILLS),
    *_rates("lsb3", "lsb", "cover", "CipherStitch image LSB at a root rate, round 3", LSB3_RATES),
    *_fills(
        "dct3-q75", "jpeg", "q75", "CipherStitch DCT on a JPEG (QF 75), round 3", REFERENCE_FILLS
    ),
    *_rates(
        "dct3-q75", "jpeg", "q75", "CipherStitch DCT (QF 75) at a root rate, round 3", DCT3_RATES
    ),
    *_fills(
        "psi3",
        "jpeg",
        "jnosi-cover",
        "CipherStitch DCT from a TIFF precover (P-SI), round 3",
        REFERENCE_FILLS,
    ),
    *_rates(
        "psi3",
        "jpeg",
        "jnosi-cover",
        "CipherStitch DCT from a TIFF precover at a root rate, round 3",
        PSI3_RATES,
    ),
]
ROUND_4 = [
    *_fills("lsb4", "lsb", "cover", "CipherStitch image LSB, round 4", REFERENCE_FILLS),
    *_fills(
        "dct4-q75", "jpeg", "q75", "CipherStitch DCT on a JPEG (QF 75), round 4", REFERENCE_FILLS
    ),
    *_fills(
        "psi4",
        "jpeg",
        "jnosi-cover",
        "CipherStitch DCT from a TIFF precover (P-SI), round 4",
        REFERENCE_FILLS,
    ),
]
ROUNDS = {1: ALL, 2: ROUND_2, 3: ROUND_3, 4: ROUND_4}
ROUND_FOLDERS = {1: "", 2: "round-2", 3: "round-3", 4: "round-4"}  # under results/
BY_NAME = {condition.name: condition for condition in ALL + ROUND_2 + ROUND_3 + ROUND_4}
COVERS = {"lsb": ("cover",), "jpeg": ("q75", "p0", "jnosi-cover")}
