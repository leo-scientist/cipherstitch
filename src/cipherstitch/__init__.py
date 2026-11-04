"""Hide files inside images, audio, documents and text, with optional authenticated encryption."""

from importlib.metadata import version

from cipherstitch.errors import (
    CapacityError,
    CipherStitchError,
    InvalidFileNameError,
    NoHiddenDataError,
    NotAvailableError,
    UnsupportedFormatError,
)
from cipherstitch.service import capacity, hide_file, reveal_file

__version__ = version("cipherstitch")

__all__ = [
    "CapacityError",
    "CipherStitchError",
    "InvalidFileNameError",
    "NoHiddenDataError",
    "NotAvailableError",
    "UnsupportedFormatError",
    "__version__",
    "capacity",
    "hide_file",
    "reveal_file",
]
