import struct
from pathlib import PurePath, PurePosixPath, PureWindowsPath

import pytest

from cipherstitch import payload
from cipherstitch.errors import InvalidFileNameError, NoHiddenDataError
from cipherstitch.payload import HiddenFile

FILE = HiddenFile(name="звіт.txt", data=b"\x00\x01binary\xff")


def test_record_roundtrip_and_overhead() -> None:
    record = payload.pack_record(FILE)
    assert len(record) == len(FILE.data) + payload.record_overhead(FILE.name)
    assert payload.unpack_record(record) == FILE


@pytest.mark.parametrize("name", ["", ".", "..", "../evil", "a/b", "a\\b", "nul\0", "x" * 256])
def test_rejects_unsafe_names_on_pack(name: str) -> None:
    with pytest.raises(InvalidFileNameError):
        payload.pack_record(HiddenFile(name=name, data=b""))


@pytest.mark.parametrize(
    "name",
    [
        f"a{chr(0x09)}b",
        f"a{chr(0x7F)}b",
        f"a{chr(0x85)}b",
        f"x{chr(0x202E)}gpj.exe",
        f"x{chr(0x2066)}y",
        f"zero{chr(0x200B)}width",
        f"{chr(0xFEFF)}bom",
        f"tag{chr(0xE0041)}",
        f"soft{chr(0xAD)}hyphen",
    ],
    ids=[
        "tab",
        "delete",
        "next-line",
        "bidi-override",
        "bidi-isolate",
        "zero-width-space",
        "byte-order-mark",
        "tag",
        "soft-hyphen",
    ],
)
def test_rejects_control_and_format_characters(name: str) -> None:
    with pytest.raises(InvalidFileNameError):
        payload.validate_file_name(name)


def test_rejects_a_name_that_is_not_utf8() -> None:
    name = (b"caf" + bytes([0xE9]) + b".txt").decode("utf-8", "surrogateescape")  # as on Linux

    with pytest.raises(InvalidFileNameError):
        payload.validate_file_name(name)

    with pytest.raises(InvalidFileNameError):
        payload.record_overhead(name)


@pytest.mark.parametrize("character", '<>:"|?*')
def test_rejects_characters_windows_refuses(character: str) -> None:
    with pytest.raises(InvalidFileNameError):
        payload.validate_file_name(f"a{character}b.txt")


@pytest.mark.parametrize(
    "name",
    [
        "CON",
        "con.txt",
        "Nul.tar.gz",
        "nul .txt",
        "PRN",
        "aux.c",
        "CONIN$",
        "COM1",
        "lpt9.log",
        f"COM{chr(0xB9)}",
        f"lpt{chr(0xB3)}.txt",
    ],
)
def test_rejects_windows_device_names(name: str) -> None:
    with pytest.raises(InvalidFileNameError):
        payload.validate_file_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "a.",
        "a ",
        "report.txt.",
        "report.txt ",
        f"a{chr(0xA0)}",
        f"a{chr(0x3000)}",
        f"report.txt{chr(0x2003)}",
    ],
    ids=["dot", "space", "dot-after-suffix", "space-after-suffix", "nbsp", "ideographic", "em"],
)
def test_rejects_a_trailing_dot_or_blank(name: str) -> None:
    """Every space separator (Zs) counts as a blank, not only the ASCII space."""
    with pytest.raises(InvalidFileNameError):
        payload.validate_file_name(name)


@pytest.mark.parametrize(
    "name",
    [" ", chr(0xA0), chr(0x3000), f"{chr(0x2003)} {chr(0x202F)}", chr(0x1680), chr(0x205F)],
    ids=["space", "nbsp", "ideographic", "mixed", "ogham", "mathematical"],
)
def test_rejects_a_name_of_blanks_alone(name: str) -> None:
    with pytest.raises(InvalidFileNameError):
        payload.validate_file_name(name)

    record = struct.pack(">H", len(name.encode())) + name.encode()

    with pytest.raises(InvalidFileNameError):
        payload.unpack_record(record)


@pytest.mark.parametrize(
    "name",
    [
        "CONSOLE.txt",
        "com10.txt",
        "nullable",
        "lpt.txt",
        " leading.txt",
        f"{chr(0x3000)}leading.txt",
        "a b",
        f"a{chr(0xA0)}b",
        "a.b.c",
        "звіт 2026.txt",
        "x" * 255,
    ],
)
def test_accepts_portable_names(name: str) -> None:
    payload.validate_file_name(name)


@pytest.mark.parametrize("name", [".hidden", ".zshenv", ".env.local", ". a"])
def test_rejects_a_leading_dot(name: str) -> None:
    """Revealed into a home folder, .zshenv would be a file the shell runs."""
    with pytest.raises(InvalidFileNameError):
        payload.validate_file_name(name)

    with pytest.raises(InvalidFileNameError, match=r"\. Rename the file and hide it again$"):
        payload.pack_record(HiddenFile(name=name, data=b""))

    record = struct.pack(">H", len(name)) + name.encode()

    with pytest.raises(InvalidFileNameError):
        payload.unpack_record(record)


def test_reveal_refuses_a_disguised_name() -> None:
    name = f"x{chr(0x202E)}gpj.exe".encode()

    with pytest.raises(InvalidFileNameError):
        payload.unpack_record(struct.pack(">H", len(name)) + name)


def test_rejects_path_traversal_from_untrusted_image() -> None:
    name = b"../../.bashrc"

    with pytest.raises(InvalidFileNameError):
        payload.unpack_record(struct.pack(">H", len(name)) + name)


@pytest.mark.parametrize(
    ("directory", "name"),
    [
        (PureWindowsPath("D:/out"), "C:evil.txt"),
        (PureWindowsPath("C:/out"), "C:evil.txt"),
        (PureWindowsPath("C:/out"), "C:.."),
    ],
)
def test_windows_drive_names_are_refused_twice(directory: PurePath, name: str) -> None:
    with pytest.raises(InvalidFileNameError):
        payload.validate_file_name(name)

    with pytest.raises(InvalidFileNameError):
        payload.output_path(directory, name)


@pytest.mark.parametrize(
    ("directory", "name", "expected"),
    [
        (PureWindowsPath("D:/out"), "звіт.txt", PureWindowsPath("D:/out/звіт.txt")),
        (PureWindowsPath("D:"), "a.txt", PureWindowsPath("D:a.txt")),
        (PurePosixPath("out"), "C:a.txt", PurePosixPath("out/C:a.txt")),
    ],
)
def test_output_path_keeps_the_file_in_the_directory(
    directory: PurePath, name: str, expected: PurePath
) -> None:
    assert payload.output_path(directory, name) == expected


@pytest.mark.parametrize(
    ("record", "error"),
    [
        (b"\x00", NoHiddenDataError),
        (b"\x00\x10abc", NoHiddenDataError),
        (b"\x00\x02\xff\xfe", InvalidFileNameError),
    ],
    ids=["no-name-length", "name-overflows-record", "invalid-utf8"],
)
def test_rejects_malformed_record(record: bytes, error: type[Exception]) -> None:
    with pytest.raises(error):
        payload.unpack_record(record)
