"""A payload stored as base64 text in one property of a document's metadata: a PDF's XMP or
a DOCX's custom properties (docs/format.md#stream-channels)."""

import base64
import re

# Neutral on purpose: no tool's name, and no other application's property.
NAME = "DocumentState"
_BYTES_PER_GROUP = 3
_CHARACTERS_PER_GROUP = 4
# XML Schema's base64Binary allows whitespace between the characters.
_WHITESPACE = re.compile(r"[ \t\r\n]+")


def decoded_size(characters: int) -> int:
    """The most bytes ``characters`` of base64 hold."""
    return characters // _CHARACTERS_PER_GROUP * _BYTES_PER_GROUP


def encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decoded(text: str, limit: int) -> bytes:
    """Up to ``limit`` bytes of the base64 ``text``, decoding no more of it than that takes;
    empty if it is not base64, which no writer stores."""
    groups = -(-limit // _BYTES_PER_GROUP)
    compact = _WHITESPACE.sub("", text)[: groups * _CHARACTERS_PER_GROUP]

    try:
        return base64.b64decode(compact, validate=True)[:limit]
    except ValueError:  # binascii.Error, or characters beyond ASCII
        return b""
