"""Control (Cc) and format (Cf) characters, which no shown text may hold: they hide in it or
reorder it, as U+202E makes "x<U+202E>gpj.exe" show as "xexe.jpg". One fixed table, as of
Unicode 16, so a text passes or fails alike on every Python version (``unicodedata`` follows
the interpreter's Unicode). File names (``payload.py``), extension texts and profile texts
are checked against it."""

HIDDEN = frozenset(
    chr(code)
    for code in (
        *range(0x00, 0x20),
        *range(0x7F, 0xA0),
        0xAD,
        *range(0x600, 0x606),
        0x61C,
        0x6DD,
        0x70F,
        0x890,
        0x891,
        0x8E2,
        0x180E,
        *range(0x200B, 0x2010),
        *range(0x202A, 0x202F),
        *range(0x2060, 0x2065),
        *range(0x2066, 0x2070),
        0xFEFF,
        *range(0xFFF9, 0xFFFC),
        0x110BD,
        0x110CD,
        *range(0x13430, 0x13440),
        *range(0x1BCA0, 0x1BCA4),
        *range(0x1D173, 0x1D17B),
        0xE0001,
        *range(0xE0020, 0xE0080),
    )
)


def has_hidden(text: str) -> bool:
    return not HIDDEN.isdisjoint(text)
