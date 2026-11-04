"""Text carriers: UTF-8 files, read and written byte for byte (line endings included)."""

from dataclasses import dataclass
from pathlib import Path

from cipherstitch.errors import UnsupportedFormatError

# A long novel takes about 1 MiB. Revealing from a text takes some 60 times its size in memory.
_MAX_SIZE = 16 << 20


def read(path: Path) -> str:
    if path.stat().st_size > _MAX_SIZE:
        raise UnsupportedFormatError(f"{path.name} is too large to read")

    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedFormatError(f"{path.name} is not UTF-8 text") from exc


def describe(path: Path) -> str:
    """E.g. ``12,480 characters · TXT``."""
    return f"{len(read(path)):,} characters · {path.suffix[1:].upper()}"


@dataclass(frozen=True, slots=True)
class EmbeddedText:
    text: str

    def save(self, path: Path) -> None:
        path.write_bytes(self.text.encode("utf-8"))
