"""Hidden file records (docs/format.md#hidden-file-record) and file-name safety."""

import struct
from dataclasses import dataclass
from pathlib import PurePath

from cipherstitch.errors import InvalidFileNameError, NoHiddenDataError
from cipherstitch.hidden_text import HIDDEN

MAX_NAME_BYTES = 255
NOT_FOUND_MESSAGE = (
    "Couldn't reveal anything. The password may be wrong, the file may have been changed after "
    "hiding, or it may not contain a hidden payload. CipherStitch can't tell these apart without "
    "the right password"
)

_NAME_LENGTH = struct.Struct(">H")
# One rule on every platform and Python version (docs/security.md#handling-files).
_SEPARATORS_AND_RESERVED = frozenset('/\\<>:"|?*')  # Windows refuses all of them in a name
# HIDDEN: control and format characters, which hide in a name or reorder it.
_UNSAFE_CHARACTERS = _SEPARATORS_AND_RESERVED | HIDDEN
# Windows devices, also with an extension ("nul.txt"); the digits include ¹, ² and ³.
_DEVICE_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(
            f"{port}{digit}"
            for port in ("COM", "LPT")
            for digit in "123456789" + chr(0xB9) + chr(0xB2) + chr(0xB3)
        ),
    }
)
# Space separators (Zs, fixed as of Unicode 16): Windows drops a trailing space, so the file
# would get another name, and a name of blanks alone, or ending in one, passes for another.
_BLANKS = "".join(
    chr(code) for code in (0x20, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000)
)
_TRAILING = (".", *_BLANKS)
# A hidden file, such as .zshenv revealed into a home folder: a shell would run it.
_LEADING = "."


@dataclass(frozen=True, slots=True)
class HiddenFile:
    name: str
    data: bytes


def record_overhead(name: str) -> int:
    return _NAME_LENGTH.size + len(_encoded(name))


def validate_file_name(name: str) -> None:
    is_unsafe = (
        not name.strip(_BLANKS)
        or name.startswith(_LEADING)
        or name.endswith(_TRAILING)
        or not _UNSAFE_CHARACTERS.isdisjoint(name)
        or name.partition(".")[0].rstrip(" ").upper() in _DEVICE_NAMES
        or len(_encoded(name)) > MAX_NAME_BYTES
    )

    if is_unsafe:
        raise _unsafe_name_error(name)


def output_path[P: PurePath](directory: P, name: str) -> P:
    """``directory / name``, refused unless ``name`` stays one plain component there (on
    Windows, ``C:x`` does not). :func:`validate_file_name` refuses such names too, and this
    check guards the path that is written."""
    target = directory / name

    if target.name != name:
        raise _unsafe_name_error(name)

    return target


def validate_name_to_hide(name: str) -> None:
    """:func:`validate_file_name`, with what to do about a file that is about to be hidden."""
    try:
        validate_file_name(name)
    except InvalidFileNameError as exc:
        raise InvalidFileNameError(f"{exc}. Rename the file and hide it again") from exc


def pack_record(file: HiddenFile) -> bytes:
    validate_name_to_hide(file.name)
    name = _encoded(file.name)

    return _NAME_LENGTH.pack(len(name)) + name + file.data


def unpack_record(record: bytes) -> HiddenFile:
    if len(record) < _NAME_LENGTH.size:
        raise NoHiddenDataError("Hidden data is truncated")

    (name_length,) = _NAME_LENGTH.unpack_from(record)
    name_end = _NAME_LENGTH.size + name_length

    if name_end > len(record):
        raise NoHiddenDataError("Hidden data is truncated")

    try:
        name = record[_NAME_LENGTH.size : name_end].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidFileNameError("Hidden file name is not valid UTF-8") from exc

    validate_file_name(name)

    return HiddenFile(name=name, data=record[name_end:])


def _encoded(name: str) -> bytes:
    try:
        return name.encode("utf-8")
    except UnicodeEncodeError as exc:  # lone surrogates: Python's form of a non-UTF-8 name
        raise _unsafe_name_error(name) from exc


def _unsafe_name_error(name: str) -> InvalidFileNameError:
    return InvalidFileNameError(f"Unsafe or invalid file name: {name!r}")
