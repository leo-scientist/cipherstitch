"""Syndrome-trellis coding, shared by image LSB, audio LSB and DCT."""

from cipherstitch.catalog import MethodId
from cipherstitch.methods import stc, trellis
from cipherstitch.params.model import Group, Kind, Option, Param, Scope, Trait, experimental
from cipherstitch.params.model import ParamType as T

F, E = Kind.FORMAT, Kind.EMBEDDER
TRELLIS_METHODS = frozenset({MethodId.LSB, MethodId.AUDIO_LSB, MethodId.DCT})
# The built-in trellis methods, and the extensions' that code with the trellis too.
TRELLIS = Scope(TRELLIS_METHODS, trait=Trait.TRELLIS)


STC = Group(
    "stc",
    "STC core",
    "tree-structure",
    "Syndrome-trellis coding, shared by image LSB, audio LSB and DCT.",
    (
        Param(
            "h",
            "Constraint height",
            "CONSTRAINT_HEIGHT",
            T.INT,
            stc.CONSTRAINT_HEIGHT,
            F,
            minimum=4,
            maximum=8,
            step=1,
            note="Higher makes fewer changes, and each step up doubles the time. Below 6, image "
            "LSB can fail near its full capacity, so leave about a tenth of it free.",
            known="h = 8 missed the speed threshold on a 12 MP image.",
        ),
        Param(
            "cols",
            "Column label",
            "COLUMNS_LABEL_NUMBER · cipherstitch stc columns N",
            T.INT,
            stc.COLUMNS_LABEL_NUMBER,
            F,
            minimum=0,
            maximum=9999,
            step=1,
            note="The number in the label the check matrix is drawn from. Any other number "
            "gives another code.",
            known="Of 4096 labels, 297 coded best. A label whose first columns repeat cost up "
            "to 33% more.",
        ),
        Param(
            "seg",
            "Units per segment",
            "UNITS_PER_SEGMENT",
            T.ENUM,
            stc.UNITS_PER_SEGMENT,
            F,
            options=tuple(Option(units, f"{units:,}") for units in (8192, 16384, 32768, 65536)),
            note="A long code runs as segments in lockstep, each about this many units.",
        ),
        Param(
            "maxseg",
            "Maximum segments",
            "MAX_SEGMENTS",
            T.INT,
            stc.MAX_SEGMENTS,
            F,
            minimum=256,
            maximum=16384,
            step=256,
            note="More segments are faster, but the code is cut at each segment's edges.",
        ),
        Param(
            "kept",
            "Units kept per bit",
            "KEPT_PER_BIT",
            T.INT,
            trellis.KEPT_PER_BIT,
            E,
            minimum=2,
            maximum=32,
            step=1,
            note="The search takes the cheapest units, this many per message bit. More come "
            "closer to the best code but take more time and memory.",
        ),
        Param(
            "fill",
            "Maximum fill",
            "MAX_FILL_PERCENT",
            T.INT,
            trellis.MAX_FILL_PERCENT,
            E,
            minimum=40,
            maximum=95,
            step=5,
            unit="%",
            note="The capacity in bits, as a share of the values the method counts. Fuller "
            "carriers are much easier to detect, and hiding near the limit fails more often.",
        ),
        Param(
            "code",
            "Coding",
            "Format.is_ternary · plane_layouts",
            T.ENUM,
            "ternary",
            F,
            options=(
                Option("ternary", "Ternary (two bit planes)"),
                experimental("binary", "Binary (parity only)"),
            ),
            note="Binary coding stores every bit in the parities, and each changed value takes "
            "its cheaper step. It needs more changes per bit, and Retry near the limit does not "
            "apply to it.",
        ),
        Param(
            "split",
            "Split between the planes",
            "_ideal_split · EVEN_SPLIT",
            T.ENUM,
            "ideal",
            E,
            options=(Option("ideal", "Ideal"), Option("even", "Even")),
            note="The stream records the split, so Reveal is unaffected.",
            known="An even split let Weighted Stego see image LSB at 20% fill (P_E 0.483).",
        ),
        Param(
            "retry",
            "Retry near the limit",
            "_SPLIT_SHIFTS",
            T.BOOL,
            True,
            E,
            note="Near full capacity, codes with fewer parity bits are tried when the first has "
            "no solution. Off: hiding fails more often there.",
        ),
        Param(
            "spread",
            "Unit spreading",
            "golden_multiplier",
            T.ENUM,
            "golden",
            F,
            options=(
                Option("golden", "Golden-ratio step"),
                experimental("sequential", "Sequential"),
            ),
            note="Sequential numbering puts the lead in the carrier's first values, so its "
            "changes cluster there.",
        ),
        Param(
            "lead",
            "Lead units",
            "LEAD_UNITS",
            T.INT,
            trellis.LEAD_UNITS,
            F,
            minimum=1024,
            maximum=262144,
            step=1024,
            note="Units that hold the salt, or the nonce without encryption.",
        ),
        Param(
            "head",
            "Head units",
            "HEAD_UNITS",
            T.INT,
            trellis.HEAD_UNITS,
            F,
            minimum=4096,
            maximum=1048576,
            step=4096,
            note="Stream positions that hold its first 70 bytes.",
        ),
        Param(
            "tail",
            "Tail units per bit",
            "TAIL_UNITS_PER_BIT",
            T.INT,
            trellis.TAIL_UNITS_PER_BIT,
            F,
            minimum=64,
            maximum=4096,
            step=64,
            note="How far the rest of the stream is spread: at most this many positions per bit.",
        ),
    ),
    scope=TRELLIS,
)
