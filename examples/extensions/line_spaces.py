"""Line-end spaces, an example method (docs/extensions.md): one bit per line of a UTF-8 text
file, a space at the end of the line for 1 and none for 0. Research mode's parameter
``lines.mark`` can make the mark a tab instead.

Method wire id 128. Every file this method writes stores it, so never change it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cipherstitch.extensions import (
    Bits,
    CarrierKind,
    Extension,
    Group,
    Kind,
    MethodSpec,
    Option,
    Order,
    Param,
    ParamType,
    Scope,
    SlotChannel,
    Tuning,
    Units,
    UnsupportedFormatError,
)

# A file to reveal from may be crafted. These two bound what opening it takes to about four
# times MAX_SIZE: the bytes, the text and a string per line.
MAX_SIZE = 16 * 1024 * 1024
MAX_LINES = 128 * 1024  # 16 KiB of payload at one bit per line
_TRAILING = (" ", "\t")
MARKS = {"space": " ", "tab": "\t"}  # the values of lines.mark


@dataclass(frozen=True, slots=True)
class EmbeddedLines:
    text: str

    def save(self, path: Path) -> None:
        path.write_bytes(self.text.encode("utf-8"))


class LineSpaceChannel(SlotChannel[EmbeddedLines]):
    """Unit i is line i, and its bit is whether the line ends with the mark, before its LF or
    CR LF. The text after the last LF is a line only if it isn't empty, so a write never adds
    a line."""

    def __init__(self, path: Path, tuning: Tuning) -> None:
        self._name = path.name
        self._mark = MARKS[str(tuning.extension_value("lines.mark"))]
        self._pieces = _text(path).split("\n")  # the lines, each with the CR of a CR LF
        count = len(self._pieces) if self._pieces[-1] else len(self._pieces) - 1
        ends = (_line(piece).endswith(self._mark) for piece in self._pieces[:count])
        self._bits = np.fromiter(ends, dtype=np.uint8, count=count)
        super().__init__(count, bits_per_unit=1)

    def _read_bits(self, units: Units) -> Bits:
        return self._bits[units]

    def _written(self, writes: Sequence[tuple[Units, Bits]]) -> EmbeddedLines:
        # A space or tab already there could read as a 1, and removing it could change the text.
        if any(_line(piece).endswith(_TRAILING) for piece in self._pieces):
            raise UnsupportedFormatError(f"{self._name} has spaces or tabs at the end of lines")

        bits = np.zeros(len(self._pieces), dtype=np.uint8)  # 0 for an empty piece at the end

        for units, chunk in writes:
            bits[units] = chunk  # one bit per unit: as many units as bits

        pieces = zip(self._pieces, bits, strict=True)

        return EmbeddedLines("\n".join(_marked(p, self._mark) if bit else p for p, bit in pieces))


def _text(path: Path) -> str:
    """The file's text, refused before reading or splitting it would pass the limits."""
    if path.stat().st_size > MAX_SIZE:
        raise UnsupportedFormatError(f"{path.name} is too large for line-end spaces")

    data = path.read_bytes()

    if data.count(b"\n") > MAX_LINES:
        raise UnsupportedFormatError(f"{path.name} has too many lines for line-end spaces")

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedFormatError(f"{path.name} is not UTF-8 text") from exc


def _line(piece: str) -> str:
    """The line without the CR of a CR LF."""
    return piece.removesuffix("\r")


def _marked(piece: str, mark: str) -> str:
    """The line with ``mark`` at its end, before the CR of a CR LF."""
    line = _line(piece)

    return f"{line}{mark}{piece[len(line) :]}"


MARK = Group(
    "lines",
    "Line-end spaces",
    "paragraph",
    "How line-end spaces marks a 1.",
    (
        Param(
            "mark",
            "Mark",
            "MARKS",
            ParamType.ENUM,
            "space",
            Kind.FORMAT,
            options=(Option("space", "Space"), Option("tab", "Tab")),
            note="The character at the end of a line that stands for 1.",
        ),
    ),
    scope=Scope(frozenset({"line-spaces"})),
)


EXTENSION = Extension(
    methods=(
        MethodSpec(
            "line-spaces",
            128,
            "Line-end spaces",
            CarrierKind.TEXT,
            (".txt", ".md"),
            open=LineSpaceChannel,
            order=Order.KEYED_IF_ENCRYPTED,
            summary="A space at the end of a line for 1, none for 0.",
            pros=("The text reads the same",),
            cons=("Editors that trim trailing spaces destroy it", "Very low capacity"),
            capacity="1 bit per line",
            risk="Trailing spaces show in a diff and in editors that mark them.",
        ),
    ),
    params=(MARK,),
    summary="An example method: one bit per line of a text file.",
)
