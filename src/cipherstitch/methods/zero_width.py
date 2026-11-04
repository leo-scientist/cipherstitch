"""Zero-width characters in UTF-8 text: two bits per insertion point
(docs/methods.md#zero-width-characters), one or three with Research's other symbol sets
(docs/format.md#slot-channels)."""

import bisect
import re
import string
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from cipherstitch.errors import UnsupportedFormatError
from cipherstitch.media import text
from cipherstitch.media.text import EmbeddedText
from cipherstitch.methods.feistel import ROUNDS
from cipherstitch.methods.slots import Bits, SlotChannel, Units

SYMBOLS = ("\u200b", "\u200c", "\u2063", "\u2060")  # symbol value = index: 2 bits
# Research's sets (docs/format.md#slot-channels), by size. U+200D is in none: emoji sequences
# join with it, so covers hold it for real reasons.
SYMBOL_SETS: Mapping[int, tuple[str, ...]] = {
    2: SYMBOLS[:2],
    4: SYMBOLS,
    8: SYMBOLS + tuple(chr(code) for code in (0x2061, 0x2062, 0x2064, 0xFEFF)),
}
_BYTE_ORDER_MARK = chr(0xFEFF)
_EMPTY = -1
_BASE_CATEGORIES = "LNP"  # letters, numbers, punctuation
_NAME_CATEGORIES = "LMN"  # what a domain's dot may follow: letters, marks, numbers
# Fixed tables keep the insertion points, and so hidden data, the same on every Python
# version: Unicode 3.2 categories (newer characters count as unassigned) and the
# whitespace that str.isspace() knows.
_WHITESPACE = frozenset(
    chr(code)
    for code in (
        *range(0x09, 0x0E),
        *range(0x1C, 0x21),
        0x85,
        0xA0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    )
)
_MARKUP = frozenset(string.punctuation)  # Markdown syntax: never next to a symbol
# Where U+200C inside a word changes how the text is drawn: the cursive scripts, whose
# letters it parts, and conjoining Hangul jamo, which it splits. They get word ends only.
_SHAPED = frozenset(
    chr(code)
    for start, stop in (
        (0x0600, 0x0900),  # Arabic, Syriac, Thaana, N'Ko, Samaritan, Mandaic
        (0x1100, 0x1200),  # Hangul jamo
        (0x1800, 0x18B0),  # Mongolian
        (0xA840, 0xA880),  # Phags-pa
        (0xA960, 0xA980),  # Hangul jamo extended A
        (0xD7B0, 0xD800),  # Hangul jamo extended B
        (0xFB50, 0xFE00),  # Arabic presentation forms A
        (0xFE70, 0xFEFF),  # Arabic presentation forms B
        (0x10AC0, 0x10B00),  # Manichaean
        (0x10B80, 0x10BB0),  # Psalter Pahlavi
        (0x10D00, 0x10D40),  # Hanifi Rohingya
        (0x10EC0, 0x10F00),  # Arabic extended C
        (0x10F30, 0x10FE0),  # Sogdian, Old Uyghur, Chorasmian
        (0x11660, 0x11680),  # Mongolian supplement
        (0x1E900, 0x1E960),  # Adlam
    )
    for code in range(start, stop)
)
# Scripts written without spaces between words. A whitespace token there is a whole
# sentence, so a dot next to their letters is not taken for a domain's: it would cost the
# sentence all its points.
_UNSPACED = (
    (0x0E00, 0x0F00),  # Thai, Lao
    (0x1000, 0x10A0),  # Myanmar
    (0x1780, 0x1800),  # Khmer
    (0x2E80, 0x2FE0),  # CJK radicals
    (0x3000, 0x3130),  # CJK symbols, Hiragana, Katakana, Bopomofo
    (0x31A0, 0x3200),  # Bopomofo extended, CJK strokes, Katakana extensions
    (0x3400, 0x4DC0),  # CJK ideographs extension A
    (0x4E00, 0xA000),  # CJK ideographs
    (0xF900, 0xFB00),  # CJK compatibility ideographs
    (0xFF66, 0xFFA0),  # half-width Katakana
    (0x20000, 0x30000),  # CJK ideographs extensions B and later
)
# Text a symbol inside would break, found on the cover without symbols so that the reader
# finds the same: Markdown code, HTML tags and entities, URLs, e-mail addresses, domains and
# file names. ASCII-only patterns, Unicode 3.2 categories and fixed tables, so they match
# the same on every Python version.
_TOKEN = re.compile(f"[^{re.escape(''.join(sorted(_WHITESPACE)))}]+")
_HTML = re.compile(
    r"</?[A-Za-z][A-Za-z0-9-]*(?:[ \t\r\n/][^<>]*)?>"
    r"|&(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#[xX][0-9A-Fa-f]+);"
)
_COMMENT = ("<!--", "-->")
_FENCE = re.compile(r"[ \t>]*(`{3,}|~{3,})(.*)")  # in list items and quotes too
_BACKTICKS = re.compile(r"`+")
_BLANK_LINE = re.compile(r"\n(?=[ \t\r]*\n)")  # the newline that starts an empty line


def _category(char: str) -> str:
    return unicodedata.ucd_3_2_0.category(char)[0]


def _is_base(char: str) -> bool:
    return _category(char) in _BASE_CATEGORIES


def _is_unspaced(char: str) -> bool:
    return any(start <= ord(char) < stop for start, stop in _UNSPACED)


def insertion_points(cover: str) -> list[int]:
    """Indices of the characters a symbol may follow."""
    protected = _protected(cover)

    return [
        index
        for index, (char, after) in enumerate(zip(cover, (cover + "\n")[1:], strict=True))
        if not protected[index]
        and char not in _WHITESPACE
        and char not in _MARKUP
        and after not in _MARKUP
        and (
            after in _WHITESPACE  # a word end (the end of the text counts as one)
            or (_is_base(char) and _is_base(after) and char not in _SHAPED and after not in _SHAPED)
        )
    ]


def relaxed_points(cover: str) -> list[int]:
    """Indices of the characters a symbol may follow under Research's relaxed rules: any but
    CR, where a symbol would split CRLF, unless a combining mark (Unicode 3.2) comes next and
    would attach to the symbol. The last character, LF and the cover's own zero-width
    characters count too."""
    return [
        index
        for index, (char, after) in enumerate(zip(cover, (cover + "\n")[1:], strict=True))
        if char != "\r" and _category(after) != "M"
    ]


def _protected(cover: str) -> bytearray:
    """1 for each character inside text that a symbol would break."""
    protected = bytearray(len(cover))
    _mark(protected, _fences(cover))
    _mark(protected, list(_code_spans(cover, protected)))
    _mark(protected, (match.span() for match in _HTML.finditer(cover)))
    _mark(protected, _comments(cover))
    _mark(
        protected,
        (
            match.span()
            for match in _TOKEN.finditer(cover)
            if "://" in match[0] or _has_domain(match[0])
        ),
    )

    return protected


def _has_domain(token: str) -> bool:
    """Whether a dot in ``token`` follows a letter, mark, number or hyphen and comes before
    two letters (the second may be a mark), with no letter of :data:`_UNSPACED` next to it:
    ``example.com``, ``приклад.укр``, ``a@b.org``, ``notes.txt``."""
    dot = token.find(".", 1)

    while 0 < dot < len(token) - 2:
        before, first, second = token[dot - 1], token[dot + 1], token[dot + 2]

        if (
            (before == "-" or _category(before) in _NAME_CATEGORIES)
            and _category(first) == "L"
            and _category(second) in "LM"
            and not (_is_unspaced(before) or _is_unspaced(first))
        ):
            return True

        dot = token.find(".", dot + 1)

    return False


def _mark(protected: bytearray, spans: Iterable[tuple[int, int]]) -> None:
    for start, stop in spans:
        protected[start:stop] = bytes([1]) * (stop - start)


def _fences(cover: str) -> Iterator[tuple[int, int]]:
    """Fenced code blocks, from the opening line to the closing one or the end of the text:
    3 or more backticks or tildes after any indentation or quote marks, so blocks in list
    items and quotes count (CommonMark allows fewer, and taking more only costs capacity)."""
    opening: tuple[str, int] | None = None  # the fence's characters and where it starts
    start = 0

    for line in cover.split("\n"):
        stop = start + len(line) + 1
        fence = _FENCE.fullmatch(line)

        if opening is None and fence and not (fence[1][0] == "`" and "`" in fence[2]):
            opening = (fence[1], start)
        elif (
            opening is not None
            and fence
            and fence[1][0] == opening[0][0]
            and len(fence[1]) >= len(opening[0])
            and not fence[2].strip(" \t\r")
        ):
            yield opening[1], stop
            opening = None

        start = stop

    if opening is not None:
        yield opening[1], len(cover)


def _code_spans(cover: str, protected: bytearray) -> Iterator[tuple[int, int]]:
    """Inline code outside the fenced blocks marked in ``protected``: a run of backticks up to
    the next run of the same length, unless an empty line (a paragraph's end) comes first."""
    runs = [match.span() for match in _BACKTICKS.finditer(cover) if not protected[match.start()]]
    empty_lines = [match.start() for match in _BLANK_LINE.finditer(cover)]
    by_length: dict[int, list[int]] = {}

    for index, (start, stop) in enumerate(runs):
        by_length.setdefault(stop - start, []).append(index)

    index = 0

    while index < len(runs):
        start, stop = runs[index]
        same = by_length[stop - start]
        later = bisect.bisect_right(same, index)
        empty = bisect.bisect_left(empty_lines, stop)

        if later == len(same) or (
            empty < len(empty_lines) and empty_lines[empty] < runs[same[later]][0]
        ):
            index += 1
            continue

        yield start, runs[same[later]][1]
        index = same[later] + 1


def _comments(cover: str) -> Iterator[tuple[int, int]]:
    """HTML comments, found by plain searches: a pattern would scan to the end of the text
    again for every comment left open."""
    opening, closing = _COMMENT
    start = cover.find(opening)

    while start != -1:
        stop = cover.find(closing, start + len(opening))

        if stop == -1:
            return  # no later comment closes either

        yield start, stop + len(closing)
        start = cover.find(opening, stop + len(closing))


def strip_symbols(
    embedded: str, symbol_set: tuple[str, ...] = SYMBOLS
) -> tuple[str, dict[int, int]]:
    """The text without the symbols of ``symbol_set``, and the symbol value that follows each
    character index. A leading U+FEFF is the text's byte order mark, never a symbol."""
    values = {symbol: value for value, symbol in enumerate(symbol_set)}
    start = 1 if embedded.startswith(_BYTE_ORDER_MARK) else 0
    cover = list(embedded[:start])
    symbols: dict[int, int] = {}

    for char in embedded[start:]:
        value = values.get(char)

        if value is None:
            cover.append(char)
        elif cover:
            symbols.setdefault(len(cover) - 1, value)

    return "".join(cover), symbols


@dataclass(frozen=True, slots=True)
class ZeroWidthRules:
    """Research's zero-width settings, Standard's by default."""

    symbol_set: tuple[str, ...] = SYMBOLS  # one of SYMBOL_SETS
    is_relaxed: bool = False  # relaxed_points instead of insertion_points


STANDARD_ZERO_WIDTH = ZeroWidthRules()


class ZeroWidthChannel(SlotChannel[EmbeddedText]):
    def __init__(
        self, path: Path, rounds: int = ROUNDS, rules: ZeroWidthRules = STANDARD_ZERO_WIDTH
    ) -> None:
        self._name = path.name
        self._symbol_set = rules.symbol_set
        content = text.read(path)
        self._cover, symbols = strip_symbols(content, rules.symbol_set)
        self._has_symbols = len(self._cover) != len(content)
        points = relaxed_points if rules.is_relaxed else insertion_points
        self._points = points(self._cover)
        self._symbols: npt.NDArray[np.int8] = np.array(
            [symbols.get(point, _EMPTY) for point in self._points], dtype=np.int8
        )
        bits_per_point = len(rules.symbol_set).bit_length() - 1
        super().__init__(len(self._points), bits_per_point, rounds)
        # A symbol's value holds its bits high bit first.
        self._shifts = np.arange(bits_per_point - 1, -1, -1, dtype=np.uint8)

    def _read_bits(self, units: Units) -> Bits:
        values = np.maximum(self._symbols[units], 0).astype(np.uint8)

        return ((values[:, None] >> self._shifts) & np.uint8(1)).reshape(-1)

    def _written(self, writes: Sequence[tuple[Units, Bits]]) -> EmbeddedText:
        if self._has_symbols:
            # Removing them could break intended text.
            raise UnsupportedFormatError(f"{self._name} already contains zero-width characters")

        bits = np.zeros((self.unit_count, self.bits_per_unit), dtype=np.uint8)
        used = np.zeros(self.unit_count, dtype=bool)

        for units, chunk in writes:
            flat = bits[units].reshape(-1)
            flat[: chunk.size] = chunk
            bits[units] = flat.reshape(-1, self.bits_per_unit)
            used[units] = True

        values = (bits << self._shifts).sum(axis=1)
        after = {
            self._points[unit]: self._symbol_set[values[unit]] for unit in np.flatnonzero(used)
        }

        return EmbeddedText("".join(char + after.get(i, "") for i, char in enumerate(self._cover)))
