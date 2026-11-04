"""Audio LSB, phase coding and echo hiding."""

from collections.abc import Mapping

from cipherstitch import standard
from cipherstitch.catalog import MethodId
from cipherstitch.methods import ecc, echo, lsb, phase
from cipherstitch.params.model import Group, Kind, Option, Param, Scope, Value, experimental
from cipherstitch.params.model import ParamType as T

F, E, A = Kind.FORMAT, Kind.EMBEDDER, Kind.GUIDANCE
MIN_ECHO_GAP_MS = 0.5
_MS_DECIMALS = 6  # 0.1 ms steps are not exact in binary: 1.4 − 0.9 < 0.5


AUDIO_LSB = Group(
    "laud",
    "LSB audio",
    "waveform",
    "Digital silence and cover rules for recordings.",
    (
        Param(
            "row",
            "Silence scan length",
            "AUDIO_ROW",
            T.INT,
            lsb.AUDIO_ROW,
            E,
            minimum=512,
            maximum=16384,
            step=512,
            unit="smp",
            note="Samples per stretch when looking for digital silence.",
        ),
        Param(
            "flat",
            "Digital-silence run",
            "FLAT_RUN",
            T.INT,
            lsb.FLAT_RUN,
            E,
            minimum=4,
            maximum=64,
            step=1,
            note="Equal samples in a row that count as digital silence. They are left out of "
            "the capacity, and changes avoid them.",
        ),
        Param(
            "depth",
            "Minimum bit depth",
            "MIN_AUDIO_DEPTH",
            T.ENUM,
            standard.MIN_AUDIO_DEPTH,
            A,
            options=(experimental(8, "8-bit"), Option(16, "16-bit"), Option(24, "24-bit")),
            note="8-bit samples make each change audible in quiet passages.",
            known="The advised rate of 76 is borrowed from images and has not been measured "
            "for audio.",
        ),
    ),
    scope=Scope(frozenset({MethodId.AUDIO_LSB})),
)


def _phase_check(values: Mapping[str, Value]) -> str | None:
    """The coded bins must lie between the DC and the Nyquist bin, whose phases are fixed."""
    if int(values["first"]) + int(values["bins"]) > int(values["seg"]) // 2:
        return "The coded bins must end below half the segment length"

    return None


PHASE = Group(
    "phase",
    "Phase coding",
    "wave-sine",
    "Segment layout and error correction.",
    (
        Param(
            "seg",
            "Segment length",
            "SEGMENT",
            T.INT,
            phase.SEGMENT,
            F,
            minimum=1024,
            maximum=16384,
            step=1024,
            unit="smp",
        ),
        Param(
            "first",
            "First bin",
            "FIRST_BIN",
            T.INT,
            phase.FIRST_BIN,
            F,
            minimum=1,
            maximum=512,
            step=1,
        ),
        Param(
            "bins",
            "Bins per segment",
            "BINS",
            T.INT,
            phase.BINS,
            F,
            minimum=8,
            maximum=512,
            step=8,
            note="Each bin carries one bit as a phase of ±π/2.",
        ),
        Param(
            "ramp",
            "Ramp",
            "RAMP",
            T.INT,
            phase.RAMP,
            E,
            minimum=0,
            maximum=32,
            step=1,
            unit="seg",
            note="Segments over which the shift returns to 0 after the data.",
        ),
        Param(
            "floor",
            "Magnitude floor",
            "FLOOR",
            T.INT,
            phase.FLOOR,
            E,
            minimum=1,
            maximum=32,
            step=1,
            unit="×",
            note="Coded bins are raised to this many times one bin's rounding noise, so their "
            "phase survives rounding.",
        ),
        Param(
            "rs",
            "Reed–Solomon parity",
            "ecc.PARITY",
            T.INT,
            ecc.PARITY,
            F,
            minimum=8,
            maximum=128,
            step=2,
            unit="B",
            note="Parity bytes per 255-byte codeword. More survives more damage but leaves "
            "less capacity.",
        ),
    ),
    scope=Scope(frozenset({MethodId.PHASE_CODING})),
    check=_phase_check,
)


def _echo_check(values: Mapping[str, Value]) -> str | None:
    gap = round(abs(float(values["d1"]) - float(values["d0"])), _MS_DECIMALS)

    if gap < MIN_ECHO_GAP_MS:
        return f"The two delays must differ by at least {MIN_ECHO_GAP_MS} ms"

    return None


ECHO = Group(
    "echo",
    "Echo hiding",
    "speaker-high",
    "Echo delays, loudness and error correction.",
    (
        Param(
            "seg",
            "Segment length",
            "SEGMENT_MS",
            T.INT,
            echo.SEGMENT_MS,
            F,
            minimum=10,
            maximum=100,
            step=1,
            unit="ms",
            note="Never shorter than 512 samples.",
        ),
        Param(
            "d0",
            "Delay for a 0",
            "ZERO_DELAY",
            T.FLOAT,
            echo.ZERO_DELAY / echo.DELAY_SCALE,
            F,
            minimum=0.5,
            maximum=5,
            step=0.1,
            unit="ms",
        ),
        Param(
            "d1",
            "Delay for a 1",
            "ONE_DELAY",
            T.FLOAT,
            echo.ONE_DELAY / echo.DELAY_SCALE,
            F,
            minimum=0.5,
            maximum=5,
            step=0.1,
            unit="ms",
        ),
        Param(
            "decay",
            "Echo level",
            "DECAY",
            T.FLOAT,
            echo.DECAY,
            E,
            minimum=0.1,
            maximum=0.9,
            step=0.05,
            note="Louder is more reliable, but more audible and easier to detect.",
        ),
        Param(
            "rs",
            "Reed–Solomon parity",
            "ecc.PARITY",
            T.INT,
            ecc.PARITY,
            F,
            minimum=8,
            maximum=128,
            step=2,
            unit="B",
        ),
    ),
    scope=Scope(frozenset({MethodId.ECHO_HIDING})),
    check=_echo_check,
)
