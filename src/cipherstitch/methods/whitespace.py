"""Whitespace padding in DOCX: bits as trailing whitespace of paragraphs
(docs/format.md#stream-channels)."""

import itertools
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from cipherstitch.errors import UnsupportedFormatError
from cipherstitch.media import docx
from cipherstitch.media.docx import EmbeddedDocxDocument
from cipherstitch.methods.slots import Bits, to_bits
from cipherstitch.methods.stream import StreamChannel, padded

BITS_PER_PARAGRAPH = 64  # keeps each trailing run shorter than a line
MAX_BITS_PER_PARAGRAPH = 256  # the most Research mode allows: reveal reads up to it
NO_BREAK_SPACE = chr(0xA0)
TAB = chr(9)  # a w:tab element in the document
_ZERO = " "
_STRIPPED = _ZERO + NO_BREAK_SPACE
_BITS_PER_BYTE = 8
# Each paragraph takes microseconds to read, and a hostile file packs millions of empty
# ones into a few kilobytes. 250 000 paragraphs are some 25 000 pages.
_MAX_PARAGRAPHS = 250_000
_OTHER_CONTENT = (
    "w:tab",
    "w:ptab",
    "w:br",
    "w:cr",
    "w:drawing",
    "w:pict",
    "w:object",
    "w:sym",
    "w:noBreakHyphen",
    "w:softHyphen",
    "w:footnoteReference",
    "w:endnoteReference",
    "w:fldSimple",
    "w:fldChar",
    "m:oMath",
)


@dataclass(frozen=True, slots=True)
class WhitespaceRules:
    """Research's whitespace settings, Standard's by default."""

    bits_per_paragraph: int = BITS_PER_PARAGRAPH
    has_tabs: bool = False  # a tab for 1 instead of a no-break space


STANDARD_WHITESPACE = WhitespaceRules()


class WhitespaceChannel(StreamChannel[EmbeddedDocxDocument]):
    def __init__(self, path: Path, rules: WhitespaceRules = STANDARD_WHITESPACE) -> None:
        self._path = path
        self._one = TAB if rules.has_tabs else NO_BREAK_SPACE
        document = docx.load_document(path)
        paragraphs = list(itertools.islice(_paragraphs(document), _MAX_PARAGRAPHS + 1))

        if len(paragraphs) > _MAX_PARAGRAPHS:
            raise UnsupportedFormatError(f"{path.name} is too large to read")

        tails = [_tail_text(paragraph, has_tabs=rules.has_tabs) for paragraph in paragraphs]
        self._trailing_texts = [_trailing(tail, _ZERO + self._one) for tail in tails]
        # A tab left at a paragraph's end once writing strips the spaces and no-break spaces
        # would read as a 1 bit, and removing it could break a form or a tab leader.
        self._has_final_tabs = rules.has_tabs and any(
            tail.rstrip(_STRIPPED).endswith(TAB) for tail in tails
        )
        count = len(self._trailing_texts)
        super().__init__(
            count * rules.bits_per_paragraph // _BITS_PER_BYTE,
            count * MAX_BITS_PER_PARAGRAPH // _BITS_PER_BYTE,
        )

    def _read_stream(self) -> bytes:
        bits = [char == self._one for text in self._trailing_texts for char in text]
        whole = len(bits) - len(bits) % _BITS_PER_BYTE

        return np.packbits(np.array(bits[:whole], dtype=np.uint8)).tobytes()

    def _written(self, stream: bytes) -> EmbeddedDocxDocument:
        if self._has_final_tabs:
            raise UnsupportedFormatError(
                f"{self._path.name} already has tabs at the end of paragraphs"
            )

        document = docx.load_document(self._path)  # a fresh copy to modify
        paragraphs = list(_paragraphs(document))
        bits = to_bits(padded(stream, self._limit))

        for index, paragraph in enumerate(paragraphs):
            _strip_trailing(paragraph)
            start = index * bits.size // len(paragraphs)
            end = (index + 1) * bits.size // len(paragraphs)

            if end > start:
                _append_run(paragraph, _whitespace(bits[start:end], self._one))

        return EmbeddedDocxDocument(self._path, document)


def _paragraphs(document: Any) -> Iterator[Any]:
    """Body paragraphs (in tables too) that do not contain other paragraphs (text boxes).
    A crafted main part may have no body, and so no paragraphs."""
    from docx.oxml.ns import qn

    body = document.element.find(qn("w:body"))

    if body is None:
        return

    for paragraph in body.iter(qn("w:p")):
        if paragraph.find(".//" + qn("w:p")) is None:
            yield paragraph


def _tail_elements(paragraph: Any, *, has_tabs: bool = False) -> list[Any]:
    """The text elements after the paragraph's last tab, break, picture, symbol, note
    mark, field or equation: only whitespace there is really at the end of the paragraph.
    With ``has_tabs``, the tabs of runs belong to the tail too (the tab stops of the
    paragraph's properties do not)."""
    from docx.oxml.ns import qn

    text, tab, run = qn("w:t"), qn("w:tab"), qn("w:r")
    elements = list(paragraph.iter(text, *(qn(tag) for tag in _OTHER_CONTENT)))
    tail = list(
        itertools.takewhile(
            lambda element: (
                element.tag == text
                or (has_tabs and element.tag == tab and element.getparent().tag == run)
            ),
            reversed(elements),
        )
    )

    return tail[::-1]


def _tail_text(paragraph: Any, *, has_tabs: bool) -> str:
    from docx.oxml.ns import qn

    tab = qn("w:tab")

    return "".join(
        TAB if element.tag == tab else element.text or ""
        for element in _tail_elements(paragraph, has_tabs=has_tabs)
    )


def _trailing(text: str, symbols: str) -> str:
    return text[len(text.rstrip(symbols)) :]


def _strip_trailing(paragraph: Any) -> None:
    """Remove the trailing spaces and no-break spaces after the paragraph's last tab, whatever
    the symbols, so a no-break-space payload never stays under a new one. Space and tab
    refuses covers whose paragraphs end in tabs instead."""
    for element in reversed(_tail_elements(paragraph)):
        kept = (element.text or "").rstrip(_STRIPPED)
        element.text = kept

        if kept:
            return


def _whitespace(bits: Bits, one: str) -> str:
    return "".join(one if bit else _ZERO for bit in bits)


def _append_run(paragraph: Any, text: str) -> None:
    """``text`` as a new run at the end of the paragraph, each tab as a ``w:tab``."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    run = OxmlElement("w:r")

    for is_tab, chars in itertools.groupby(text, key=lambda char: char == TAB):
        if is_tab:
            run.extend(OxmlElement("w:tab") for _ in chars)
        else:
            run_text = OxmlElement("w:t")
            run_text.set(qn("xml:space"), "preserve")
            run_text.text = "".join(chars)
            run.append(run_text)

    paragraph.append(run)
