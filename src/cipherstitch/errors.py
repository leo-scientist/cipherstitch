"""Exceptions raised by cipherstitch."""


class CipherStitchError(Exception):
    """Base class for all cipherstitch errors."""


class CapacityError(CipherStitchError):
    """The payload does not fit into the carrier."""


class NoHiddenDataError(CipherStitchError):
    """No hidden data was found, or the password is wrong (deliberately indistinguishable).

    ``hint`` says how to install the ciphers that could not be tried, if any.
    """

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message if hint is None else f"{message}. {hint}")
        self.hint = hint


class UnsupportedFormatError(CipherStitchError):
    """The carrier or output format is not supported, or the carrier file is malformed."""


class InvalidFileNameError(CipherStitchError):
    """The hidden file name is empty, too long or unsafe."""


class NotAvailableError(CipherStitchError):
    """The chosen options cannot run: an extra is missing, or the combination cannot work."""


class OperationCancelledError(CipherStitchError):
    """The user cancelled a running operation."""


class InvalidParameterError(CipherStitchError):
    """A Research-mode parameter is unknown, of the wrong type, out of range, or does not work
    with the others."""


class InvalidProfileError(CipherStitchError):
    """A file is not a profile this version can read, or its values cannot be used."""


class DecryptionError(CipherStitchError):
    """A sealed block failed authentication: wrong key, wrong cipher or tampered data."""


class ExtensionError(CipherStitchError):
    """Code from the extension ``extension`` raised a foreign exception or broke the contract
    (docs/architecture.md#extensions). The foreign exception, if any, is the ``__cause__``."""

    def __init__(self, extension: str, problem: str) -> None:
        super().__init__(f"The extension {extension} failed: {problem}")
        self.extension = extension
