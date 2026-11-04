import json
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cipherstitch import hidden_text
from cipherstitch.catalog import CipherId, MethodId
from cipherstitch.errors import InvalidProfileError
from cipherstitch.params import profile
from cipherstitch.params.profile import Profile
from cipherstitch.params.settings import Settings

FAST = Profile(
    Settings.of({"stc.h": 4, "stc.kept": 6}),
    name="Fast STC",
    note="Speed test for 12 MP images",
    method=MethodId.LSB,
    cipher=CipherId.CHACHA20,
)
NOON = datetime(2026, 11, 2, 15, 40, tzinfo=UTC)


def _document(**fields: object) -> bytes:
    document = {"format": "cipherstitch-profile", "version": 1, "defaults": 1, **fields}

    return json.dumps(document).encode()


def test_a_profile_stores_only_the_changes_and_reads_back(tmp_path: Path) -> None:
    text = profile.dumps(FAST, app="CipherStitch 1.0", created=NOON)

    assert json.loads(text) == {
        "format": "cipherstitch-profile",
        "version": 1,
        "name": "Fast STC",
        "note": "Speed test for 12 MP images",
        "created": "2026-11-02T15:40:00Z",
        "app": "CipherStitch 1.0",
        "defaults": 1,
        "method": "lsb",
        "cipher": "chacha20-poly1305",
        "params": {"stc.h": 4, "stc.kept": 6},
    }

    path = tmp_path / f"fast{profile.SUFFIX}"
    profile.write(path, FAST, app="CipherStitch 1.0")

    assert profile.read(path) == FAST


def test_a_profile_saved_with_a_byte_order_mark_reads(tmp_path: Path) -> None:
    """Windows Notepad starts a UTF-8 file with one."""
    path = tmp_path / f"fast{profile.SUFFIX}"
    text = profile.dumps(FAST, app="CipherStitch 1.0")
    path.write_bytes(chr(0xFEFF).encode() + text.encode())

    assert profile.read(path) == FAST


def test_method_cipher_name_and_note_are_optional() -> None:
    assert profile.parse(_document(), "p") == Profile(Settings())
    assert profile.parse(_document(params={"stc.h": 6}), "p") == Profile(Settings())  # a default


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe",
        b"{not json",
        b"[]",
        json.dumps({"format": "other", "version": 1, "defaults": 1}).encode(),
        json.dumps({"format": "cipherstitch-profile", "defaults": 1}).encode(),
        _document(version=True),
        _document(defaults=0),
        _document(params=[]),
        _document(name=4),
        _document(method=3),
        _document(params={"stc.h\u001b[2J": 4}),  # shown in a warning: no escapes
        b"[" * 30000 + b"]" * 30000,  # nested past the stack, under the size limit
    ],
    ids=[
        "utf-8",
        "json",
        "list",
        "format",
        "no-version",
        "bool-version",
        "defaults",
        "params",
        "name",
        "method-type",
        "key",
        "nesting",
    ],
)
def test_anything_else_is_not_a_profile(data: bytes) -> None:
    with pytest.raises(InvalidProfileError, match=r"^p is not a CipherStitch profile$"):
        profile.parse(data, "p")


@pytest.mark.parametrize("field", ["version", "defaults"])
def test_a_newer_profile_asks_for_an_update(field: str) -> None:
    with pytest.raises(InvalidProfileError, match="made by a newer CipherStitch"):
        profile.parse(_document(**{field: 2}), "p")


def test_values_are_checked_like_the_editor_checks_them() -> None:
    with pytest.raises(InvalidProfileError, match=r"stc\.h.* must be at most 8 in p$"):
        profile.parse(_document(params={"stc.h": 9}), "p")

    with pytest.raises(InvalidProfileError, match=r"dct\.huff.* must be one of: match, .* in p$"):
        profile.parse(_document(params={"dct.huff": "later"}), "p")

    unknown = r"^p names a method this CipherStitch doesn't know, 'img-lsb' \(an extension"

    with pytest.raises(InvalidProfileError, match=unknown):
        profile.parse(_document(method="img-lsb"), "p")


def test_unknown_parameters_are_named_for_hiding_to_leave_out_and_revealing_to_refuse() -> None:
    """Only a newer CipherStitch adds a parameter to a built-in group."""
    read = profile.parse(_document(params={"stc.h": 4, "stc.new": 1, "dct.new": True}), "p")

    assert read.settings == Settings.of({"stc.h": 4})
    assert read.unknown == ("dct.new", "stc.new")
    assert profile.unknown_warning(read) == (
        "Left out the parameters this version of CipherStitch doesn't know (dct.new, stc.new)"
    )

    with pytest.raises(InvalidProfileError) as refused:
        profile.refuse_unknown(read, "p")

    assert str(refused.value) == (
        "p sets parameters this version of CipherStitch doesn't know (dct.new, stc.new). "
        "Update CipherStitch to reveal with it"
    )
    assert profile.unknown_warning(FAST) is None
    profile.refuse_unknown(FAST, "p")


def test_a_group_no_built_in_has_may_come_from_a_missing_extension() -> None:
    read = profile.parse(_document(params={"stc.new": 1, "zz.new": 1}), "p")

    assert read.unknown == ("stc.new", "zz.new")
    assert profile.unknown_warning(read) == (
        "Left out the parameters this version of CipherStitch doesn't know (stc.new, zz.new). "
        "An extension that isn't installed may add them"
    )

    with pytest.raises(InvalidProfileError) as refused:
        profile.refuse_unknown(read, "p")

    assert str(refused.value) == (
        "p sets parameters this version of CipherStitch doesn't know (stc.new, zz.new). "
        "Install the extension that adds them or update CipherStitch to reveal with it"
    )


def test_a_huge_file_is_not_read_whole(tmp_path: Path) -> None:
    path = tmp_path / "big.csprofile"
    path.write_bytes(b" " * (profile.MAX_SIZE + 1))

    with pytest.raises(InvalidProfileError, match="too large"):
        profile.read(path)


def test_the_name_and_the_note_have_limits() -> None:
    longest = profile.parse(_document(name="n" * 80, note="t" * 2000), "p")

    assert (len(longest.name), len(longest.note)) == (80, 2000)
    assert (profile.MAX_NAME_LENGTH, profile.MAX_NOTE_LENGTH) == (80, 2000)

    with pytest.raises(InvalidProfileError, match=r"^p has a name longer than 80 characters$"):
        profile.parse(_document(name="n" * 81), "p")

    with pytest.raises(InvalidProfileError, match=r"^p has a note longer than 2000 characters$"):
        profile.parse(_document(note="t" * 2001), "p")


@pytest.mark.parametrize(
    ("field", "text"),
    [
        ("name", "Fast\x1b[2J STC"),  # a terminal escape
        ("name", "Fast" + chr(0x202E) + "CTS"),  # a right-to-left override
        ("note", "Speed test" + chr(10)),
        ("note", "Speed" + chr(0x200B) + "test"),
    ],
    ids=["escape", "override", "line-feed", "zero-width"],
)
def test_the_name_and_the_note_are_plain_text(field: str, text: str) -> None:
    """The app shows them as they are, as it does every extension text."""
    with pytest.raises(InvalidProfileError, match=rf"^p has a {field} with a control or format"):
        profile.parse(_document(**{field: text}), "p")


def test_the_hidden_table_holds_every_control_and_format_character() -> None:
    """One fixed table on every Python version: at least this interpreter's Unicode."""
    every = (chr(code) for code in range(0x110000))
    categories = {char for char in every if unicodedata.category(char) in ("Cc", "Cf")}

    assert categories <= hidden_text.HIDDEN
    assert hidden_text.has_hidden("a" + chr(0x200D) + "b")
    assert not hidden_text.has_hidden("Fast STC, 12 MP")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            b'{"format": "cipherstitch-profile", "version": 1, "defaults": 1, "version": 1}',
            "p repeats the key version",
        ),
        (
            b'{"format": "cipherstitch-profile", "version": 1, "defaults": 1, '
            b'"params": {"stc.h": 4, "stc.kept": 6, "stc.h": 9}}',
            "p repeats the key stc.h",
        ),
        (
            b'{"format": "cipherstitch-profile", "version": 1, "defaults": 1, '
            b'"\\u001b[2J": 1, "\\u001b[2J": 2}',
            "p repeats a key",  # a key that could carry an escape is not shown
        ),
    ],
    ids=["top-level", "params", "unshown"],
)
def test_a_repeated_key_is_refused(data: bytes, message: str) -> None:
    """Not read with the last value, as json.loads does by default."""
    with pytest.raises(InvalidProfileError, match=rf"^{message}$"):
        profile.parse(data, "p")
