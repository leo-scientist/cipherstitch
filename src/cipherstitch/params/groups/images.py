"""Image LSB, and DCT from a JPEG or from a precover."""

from cipherstitch.catalog import MethodId
from cipherstitch.media import precover
from cipherstitch.methods import dct, hill, juniward, lsb
from cipherstitch.params.model import Cover, Group, Kind, Option, Param, Scope, experimental
from cipherstitch.params.model import ParamType as T

F, E, A = Kind.FORMAT, Kind.EMBEDDER, Kind.GUIDANCE
_JPEG_COVER = (Scope(cover=Cover.JPEG),)  # a precover is compressed with its own settings
_MATCHING = (Scope(frozenset({MethodId.LSB})),)  # LSB replacement has no costs or steps


IMAGE_LSB = Group(
    "limg",
    "LSB image",
    "image",
    "Costs, flat areas and cover rules for lossless images.",
    (
        Param(
            "cost",
            "Cost function",
            "hill.image_costs",
            T.ENUM,
            "hill",
            E,
            options=(
                Option("hill", "HILL"),
                experimental("uniform", "Uniform (LSB matching)"),
                experimental("wow", "WOW"),
                experimental("s-uniward", "S-UNIWARD"),
                experimental("mipod", "MiPOD"),
            ),
            note="Uniform costs change values anywhere, busy or smooth. WOW and S-UNIWARD "
            "weigh the texture through J-UNIWARD's wavelet filters. MiPOD weighs each value by "
            "how much the picture varies around it, and its costs depend on the payload's size.",
            scopes=_MATCHING,
        ),
        Param(
            "far",
            "HILL far window",
            "FAR",
            T.ENUM,
            hill.FAR,
            E,
            options=(Option(9, "9 × 9"), Option(15, "15 × 15"), Option(21, "21 × 21")),
            note="The mean over the reciprocal costs, a multiple of HILL's 3 × 3 near window.",
            scopes=_MATCHING,
        ),
        Param(
            "flat",
            "Flat-run length",
            "FLAT_RUN",
            T.INT,
            lsb.FLAT_RUN,
            E,
            minimum=4,
            maximum=64,
            step=1,
            note="Equal values in a row that count as flat colour. They are left out of the "
            "capacity, and changes avoid them.",
            scopes=_MATCHING,
        ),
        Param(
            "wet",
            "Wet costs on flat areas",
            "WET_COST",
            T.BOOL,
            True,
            E,
            note="Off: flat values cost what the cost function says, so changes can land in "
            "plain colour.",
            scopes=_MATCHING,
        ),
        Param(
            "lossy",
            "Allow lossy, greyscale and palette covers",
            "standard cover rules",
            T.BOOL,
            False,
            A,
            note="JPEG and WebP covers keep compression traces that LSB changes break, and "
            "greyscale or palette covers are saved in another colour format. Standard always "
            "refuses them.",
        ),
        Param(
            "meta",
            "Keep metadata",
            "EXIF · XMP · ICC",
            T.BOOL,
            True,
            E,
            note="Off: the output keeps nothing of the cover but its pixels.",
        ),
    ),
    scope=Scope(frozenset({MethodId.LSB, MethodId.LSB_REPLACEMENT})),
)

DCT = Group(
    "dct",
    "DCT",
    "squares-four",
    "Costs and coefficients for every DCT cover, and how a JPEG cover's output is written.",
    (
        Param(
            "cost",
            "Cost function",
            "juniward.costs",
            T.ENUM,
            "j-uniward",
            E,
            options=(
                Option("j-uniward", "J-UNIWARD"),
                experimental("uerd", "UERD"),
                experimental("uniform", "Uniform"),
            ),
            note="UERD weighs each block's coefficients and its neighbours' by their "
            "quantization steps, so it needs no decompression. Uniform costs change "
            "coefficients anywhere.",
            known="J-UNIWARD replaced UERD after the first round of detection tests.",
        ),
        Param(
            "sigma",
            "J-UNIWARD σ",
            "SIGMA",
            T.ENUM,
            juniward.SIGMA,
            E,
            options=(
                Option(2.0**-4, "2⁻⁴"),
                Option(2.0**-6, "2⁻⁶"),
                Option(2.0**-8, "2⁻⁸"),
            ),
            note="Keeps flat areas from dividing by zero, and sets how much texture matters.",
        ),
        Param(
            "pol",
            "Polarization",
            "POLARIZATION",
            T.FLOAT,
            juniward.POLARIZATION,
            E,
            minimum=0,
            maximum=1,
            step=0.05,
            note="A step towards the 3 × 3 Wiener estimate costs this share of a step away. "
            "1 means no polarization.",
            known="Without polarization the writer trailed HStego.",
            scopes=_JPEG_COVER,
        ),
        Param(
            "mag",
            "Maximum magnitude",
            "MAX_MAGNITUDE",
            T.INT,
            dct.MAX_MAGNITUDE,
            E,
            minimum=255,
            maximum=dct.MAX_MAGNITUDE,
            step=1,
            note="Coefficients never step beyond it, and those already beyond it stay as they "
            "are. Baseline JPEG stores at most 1023.",
        ),
        Param(
            "huff",
            "Huffman tables",
            "has_standard_tables",
            T.ENUM,
            "match",
            E,
            options=(
                Option("match", "Match the cover"),
                Option("standard", "Standard"),
                Option("optimized", "Optimized"),
            ),
            note="Matching writes standard tables when the cover has them, and optimized ones "
            "otherwise.",
            scopes=_JPEG_COVER,
        ),
        Param(
            "app",
            "Keep APPn and COM segments",
            "jpeg_header.matched",
            T.BOOL,
            True,
            E,
            note="Off: the output has libjpeg's JFIF header only. Progressive scans and restart "
            "markers are never kept, and the Detection row says so.",
            scopes=_JPEG_COVER,
        ),
    ),
    scope=Scope(frozenset({MethodId.DCT})),
)

PRECOVER = Group(
    "pre",
    "DCT from precover",
    "aperture",
    "Compression and side information for RAW, PNG and TIFF covers.",
    (
        Param(
            "q",
            "JPEG quality",
            "QUALITY",
            T.INT,
            precover.QUALITY,
            E,
            minimum=70,
            maximum=100,
            step=1,
            note="Lower quality leaves fewer non-zero coefficients, so less capacity.",
        ),
        Param(
            "sub",
            "Chroma subsampling",
            "SUBSAMPLING",
            T.ENUM,
            "4:2:0",
            E,
            options=(
                Option("4:2:0", "4:2:0"),
                experimental("4:2:2", "4:2:2"),
                experimental("4:4:4", "4:4:4"),
            ),
            note="4:2:0 keeps one colour sample for every 2 × 2 pixels, 4:2:2 for every 2 × 1, "
            "and 4:4:4 one for each pixel. More colour samples give more capacity and a "
            "larger file.",
        ),
        Param(
            "si",
            "Side-informed costs",
            "juniward.side_informed",
            T.BOOL,
            True,
            E,
            note="A step towards the uncompressed picture costs 1 − 2|e| times the J-UNIWARD "
            "cost, a step away 1 + 2|e|.",
            known="Without side information the results were worse (P-0 noSI).",
        ),
        Param(
            "icc",
            "Keep the ICC profile",
            "image.rgb_profile",
            T.BOOL,
            True,
            E,
            note="Only RGB profiles are kept. EXIF is always left out.",
        ),
    ),
    scope=Scope(frozenset({MethodId.DCT}), cover=Cover.PRECOVER),
)
