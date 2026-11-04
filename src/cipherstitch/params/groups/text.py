"""Documents and text: the object stream, DOCX whitespace and zero-width characters."""

from cipherstitch.catalog import MethodId
from cipherstitch.methods import object_stream, whitespace
from cipherstitch.params.model import Group, Kind, Option, Param, Scope, experimental
from cipherstitch.params.model import ParamType as T

F, E = Kind.FORMAT, Kind.EMBEDDER


OBJECT = Group(
    "obj",
    "Object in PDF or DOCX",
    "file-text",
    "How large the object may grow and where it goes.",
    (
        Param(
            "limit",
            "Soft limit",
            "SOFT_LIMIT",
            T.INT,
            object_stream.SOFT_LIMIT // 1024,
            E,
            minimum=8,
            maximum=object_stream.MAX_LIMIT // 1024,
            step=8,
            unit="KiB",
            note="The most the object holds. Reveal reads objects of any allowed size.",
        ),
        Param(
            "pad",
            "Random-length padding",
            "padded",
            T.BOOL,
            True,
            E,
            note="Off: the object's size gives away the payload's size.",
        ),
        Param(
            "loc",
            "Object location",
            "/PieceInfo · printerSettings1.bin · XMP · custom.xml",
            T.ENUM,
            "standard",
            F,
            options=(
                Option("standard", "Page piece or printer settings"),
                experimental("metadata", "Metadata stream"),
            ),
            note="Metadata stream stores the object as base64 text in a property of the PDF's "
            "XMP or the DOCX's custom properties, so it holds three quarters of the soft limit. "
            "Word lists custom properties with the document's properties, and PDF readers show "
            "XMP.",
        ),
    ),
    scope=Scope(frozenset({MethodId.OBJECT_STREAM})),
)

WHITESPACE = Group(
    "ws",
    "DOCX whitespace",
    "paragraph",
    "Trailing whitespace at the end of paragraphs.",
    (
        Param(
            "bpp",
            "Bits per paragraph",
            "BITS_PER_PARAGRAPH",
            T.INT,
            whitespace.BITS_PER_PARAGRAPH,
            E,
            minimum=8,
            maximum=whitespace.MAX_BITS_PER_PARAGRAPH,
            step=8,
            note="Longer trailing runs hold more but are easier to notice. Reveal reads runs of "
            "any allowed length.",
        ),
        Param(
            "sym",
            "Symbols",
            "NO_BREAK_SPACE · TAB",
            T.ENUM,
            "space-nbsp",
            F,
            options=(
                Option("space-nbsp", "Space and no-break space"),
                experimental("space-tab", "Space and tab"),
            ),
            note="Space and tab writes a tab for 1. Each tab jumps to the next tab stop, so a "
            "long run of them can wrap and add blank lines after a paragraph. Covers whose "
            "paragraphs already end in tabs are refused.",
        ),
    ),
    scope=Scope(frozenset({MethodId.WHITESPACE})),
)

ZERO_WIDTH = Group(
    "zw",
    "Zero-width characters",
    "text-aa",
    "The symbol set and where symbols may go.",
    (
        Param(
            "sym",
            "Symbol set",
            "SYMBOL_SETS",
            T.ENUM,
            4,
            F,
            options=(
                Option(4, "4 symbols (2 bits)"),
                Option(2, "2 symbols (1 bit)"),
                experimental(8, "8 symbols (3 bits)"),
            ),
            note="2 symbols are U+200B and U+200C. 8 symbols add U+2061, U+2062, U+2064 and "
            "U+FEFF to the four. Changes the capacity and which apps strip the symbols.",
        ),
        Param(
            "rules",
            "Insertion rules",
            "insertion_points · relaxed_points",
            T.ENUM,
            "strict",
            F,
            options=(
                Option("strict", "Strict"),
                experimental("relaxed", "Relaxed"),
            ),
            note="Relaxed rules put a symbol after any character but a carriage return, unless "
            "a combining mark comes next. They add capacity but break rendering, links and "
            "Markdown.",
        ),
    ),
    scope=Scope(frozenset({MethodId.ZERO_WIDTH})),
)
