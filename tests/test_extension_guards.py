"""The guards around an extension's code, and the container running a fixture extension's
method and cipher through them."""

import os
from pathlib import Path
from typing import Any

import pytest

from cipherstitch import container, methods
from cipherstitch.catalog import CipherId, MethodKey
from cipherstitch.container import UnitOrder
from cipherstitch.errors import (
    CapacityError,
    DecryptionError,
    ExtensionError,
    NoHiddenDataError,
    UnsupportedFormatError,
)
from cipherstitch.extension_guards import (
    GuardedChannel,
    GuardedCipher,
    GuardedEmbedded,
    described,
    guarded_opener,
)
from cipherstitch.methods import Embedded, Tuning
from cipherstitch.payload import HiddenFile
from tests.extension_fixtures.parts import ChaChaCopy

from .conftest import use_extensions

FILE = HiddenFile(name="таємниця.txt", data=b"top secret " * 30)
PASSWORD = "secret"  # noqa: S105
KEY = bytes(32)


class _Faulty:
    """A channel, cipher and embedded carrier whose every call returns ``result``, or raises
    ``error``."""

    key_size = 32

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self._result = result
        self._error = error

    def capacity(self, prefix_size: int) -> Any:
        return self._call()

    def stored_limit(self, prefix_size: int) -> Any:
        return self._call()

    def read(self, prefix_size: int, size: int, seed: bytes | None) -> Any:
        return self._call()

    def write(self, prefix: bytes, body: bytes, seed: bytes | None) -> Any:
        return self._call()

    def save(self, path: Path) -> Any:
        return self._call()

    def sealed_size(self, size: int) -> Any:
        return self._call()

    def seal(self, key: bytes, counter: int, data: bytes) -> Any:
        return self._call()

    def unseal(self, key: bytes, counter: int, data: bytes) -> Any:
        return self._call()

    def _call(self) -> Any:
        if self._error is not None:
            raise self._error

        return self._result


class _Limits(_Faulty):
    """A channel whose capacity and stored limit differ."""

    def __init__(self, capacity: int, stored_limit: int) -> None:
        super().__init__()
        self._capacity = capacity
        self._stored_limit = stored_limit

    def capacity(self, prefix_size: int) -> int:
        return self._capacity

    def stored_limit(self, prefix_size: int) -> int:
        return self._stored_limit


def _channel(result: Any = None, error: BaseException | None = None) -> GuardedChannel:
    return GuardedChannel("faulty", _Faulty(result, error))


def _cipher(result: Any = None, error: BaseException | None = None) -> GuardedCipher:
    return GuardedCipher("faulty", "faulty-cipher", 32, _Faulty(result, error))


CHANNEL_CALLS = {
    "capacity": lambda channel: channel.capacity(0),
    "stored_limit": lambda channel: channel.stored_limit(0),
    "read": lambda channel: channel.read(0, 16, None),
    "write": lambda channel: channel.write(b"", b"", None),
}
CIPHER_CALLS = {
    "sealed_size": lambda cipher: cipher.sealed_size(4),
    "seal": lambda cipher: cipher.seal(KEY, 0, b"data"),
    "unseal": lambda cipher: cipher.unseal(KEY, 0, b"data"),
}


@pytest.mark.parametrize("call", list(CHANNEL_CALLS))
def test_a_foreign_exception_from_a_channel_becomes_an_extension_error(call: str) -> None:
    error = TypeError("unsupported operand")

    with pytest.raises(ExtensionError) as raised:
        CHANNEL_CALLS[call](_channel(error=error))

    assert str(raised.value) == "The extension faulty failed: TypeError: unsupported operand"
    assert raised.value.extension == "faulty"
    assert raised.value.__cause__ is error


@pytest.mark.parametrize("call", list(CIPHER_CALLS))
def test_a_foreign_exception_from_a_cipher_becomes_an_extension_error(call: str) -> None:
    with pytest.raises(ExtensionError, match=r"^The extension faulty failed: KeyError: 'k'$"):
        CIPHER_CALLS[call](_cipher(error=KeyError("k")))


def _noted() -> ValueError:
    error = ValueError("bad\n  value")
    error.add_note("a note the extension added")

    return error


class _UnprintableError(Exception):
    def __str__(self) -> str:
        raise RuntimeError("no text")


@pytest.mark.parametrize(
    ("error", "text"),
    [
        (_noted(), "ValueError: bad value"),
        (ValueError(), "ValueError"),
        (CapacityError("too small"), "cipherstitch.errors.CapacityError: too small"),
        (
            _UnprintableError(),
            "tests.test_extension_guards._UnprintableError: <exception str() failed>",
        ),
    ],
)
def test_a_failure_reads_on_one_line_without_notes(error: Exception, text: str) -> None:
    assert described(error) == text


def test_a_noted_failure_keeps_its_message() -> None:
    with pytest.raises(ExtensionError) as raised:
        CHANNEL_CALLS["read"](_channel(error=_noted()))

    assert str(raised.value) == "The extension faulty failed: ValueError: bad value"


def test_the_opener_and_the_saved_carrier_are_guarded(tmp_path: Path) -> None:
    def broken(path: Path, tuning: Tuning) -> container.Channel[Embedded]:
        raise ValueError(f"can't read {path.name}")

    with pytest.raises(ExtensionError, match=r"ValueError: can't read cover\.fxb"):
        guarded_opener("faulty", broken)(tmp_path / "cover.fxb", methods.STANDARD)

    with pytest.raises(ExtensionError, match="OSError: disk full"):
        GuardedEmbedded("faulty", _Faulty(error=OSError("disk full"))).save(tmp_path / "out")


def test_a_save_must_write_the_file(tmp_path: Path) -> None:
    """The service moves what save wrote into place: a missing file would surface as the
    service's OSError, not as the extension's failure."""

    class Writer:
        def save(self, path: Path) -> None:
            path.write_bytes(b"carrier")

    GuardedEmbedded("fine", Writer()).save(tmp_path / "written")

    assert (tmp_path / "written").read_bytes() == b"carrier"

    with pytest.raises(ExtensionError) as raised:
        GuardedEmbedded("faulty", _Faulty()).save(tmp_path / "out")

    assert str(raised.value) == "The extension faulty failed: save wrote no file"
    assert raised.value.__cause__ is None
    assert not (tmp_path / "out").exists()

    with pytest.raises(ExtensionError, match="save wrote no file"):
        GuardedEmbedded("faulty", _Faulty()).save(tmp_path)  # a folder is no file either


@pytest.mark.parametrize(
    "error",
    [
        CapacityError("too small"),
        UnsupportedFormatError("not a carrier"),
        MemoryError(),
        KeyboardInterrupt(),
    ],
)
@pytest.mark.parametrize("call", list(CHANNEL_CALLS))
def test_cipherstitch_errors_and_base_exceptions_pass(call: str, error: BaseException) -> None:
    with pytest.raises(type(error)):
        CHANNEL_CALLS[call](_channel(error=error))


def test_only_a_decryption_error_passes_unseal() -> None:
    with pytest.raises(DecryptionError):
        _cipher(error=DecryptionError("Authentication failed")).unseal(KEY, 0, b"data")

    with pytest.raises(ExtensionError, match="CapacityError: odd"):
        _cipher(error=CapacityError("odd")).unseal(KEY, 0, b"data")

    with pytest.raises(CapacityError):
        _cipher(error=CapacityError("fine")).seal(KEY, 0, b"data")


@pytest.mark.parametrize(
    ("call", "result", "problem"),
    [
        ("capacity", -1, "capacity returned -1, not a size"),
        ("capacity", "3", "capacity returned '3', not a size"),
        ("capacity", True, "capacity returned True, not a size"),
        ("stored_limit", 2.5, "stored_limit returned 2.5, not a size"),
        ("read", "text", "read returned str for at most 16 bytes"),
        ("read", bytes(17), "read returned 17 bytes for at most 16 bytes"),
        ("write", b"carrier", "write returned bytes, which has no save method"),
    ],
)
def test_a_channel_result_that_breaks_the_contract_is_refused(
    call: str, result: object, problem: str
) -> None:
    with pytest.raises(ExtensionError) as raised:
        CHANNEL_CALLS[call](_channel(result))

    assert str(raised.value) == f"The extension faulty failed: {problem}"
    assert raised.value.__cause__ is None


def test_the_stored_limit_is_never_below_the_capacity() -> None:
    assert GuardedChannel("faulty", _Limits(10, 12)).stored_limit(0) == 12

    with pytest.raises(ExtensionError, match="stored_limit returned 9, less than the capacity 10"):
        GuardedChannel("faulty", _Limits(10, 9)).stored_limit(0)


@pytest.mark.parametrize(
    ("call", "result", "problem"),
    [
        ("sealed_size", 3, "sealed_size(4) returned 3, less than the input"),
        ("sealed_size", None, "sealed_size returned None, not a size"),
        ("unseal", "data", "unseal returned str"),
    ],
)
def test_a_cipher_result_that_breaks_the_contract_is_refused(
    call: str, result: object, problem: str
) -> None:
    with pytest.raises(ExtensionError) as raised:
        CIPHER_CALLS[call](_cipher(result))

    assert str(raised.value) == f"The extension faulty failed: {problem}"


def test_seal_must_write_exactly_the_sealed_size() -> None:
    class Padded(ChaChaCopy):
        def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
            return super().seal(key, counter, data) + b"!"

    cipher = GuardedCipher("padded", "padded", 32, Padded())

    with pytest.raises(ExtensionError, match="seal returned 21 bytes, not the 20 bytes of"):
        cipher.seal(KEY, 0, b"data")


def test_a_guarded_cipher_goes_by_its_spec_id() -> None:
    cipher = GuardedCipher("copy", "copy-cipher", 32, ChaChaCopy())
    sealed = cipher.seal(KEY, 1, b"data")

    assert (cipher.id, cipher.key_size, len(sealed)) == ("copy-cipher", 32, 20)
    assert cipher.unseal(KEY, 1, sealed) == b"data"

    with pytest.raises(DecryptionError):
        cipher.unseal(KEY, 0, sealed)


def _hide(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cipher_id: str, order: UnitOrder
) -> tuple[Path, MethodKey]:
    use_extensions(monkeypatch, "working")
    embedding = methods.get("byte-lsb")
    assert embedding is not None
    cover = tmp_path / "cover.fxb"
    cover.write_bytes(os.urandom(8192))
    encrypted = cipher_id != CipherId.NONE
    embedded = container.hide(
        embedding.open(cover, methods.STANDARD),
        FILE,
        method="byte-lsb",
        cipher_id=cipher_id,
        password=PASSWORD if encrypted else None,
        order=embedding.body_order(order, encrypted=encrypted),
    )
    output = tmp_path / "embedded.fxb"
    embedded.save(output)

    return output, embedding.method


@pytest.mark.parametrize("cipher_id", [CipherId.NONE, CipherId.AES_GCM, "chacha-copy"])
@pytest.mark.parametrize("order", list(UnitOrder))
def test_a_fixture_extension_hides_and_reveals_in_every_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cipher_id: str, order: UnitOrder
) -> None:
    output, method = _hide(monkeypatch, tmp_path, cipher_id, order)
    [(opener, candidates)] = methods.for_reveal(output, method)
    password = None if cipher_id == CipherId.NONE else PASSWORD
    groups = [(opener(output, methods.STANDARD), candidates)]
    revealed = container.reveal(groups, password, cipher_id=cipher_id)

    assert revealed == container.Revealed(FILE, "byte-lsb", cipher_id)


def test_an_extension_cipher_is_tried_only_when_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Standard's reveal tries the built-in ciphers alone (ciphers.encrypting)."""
    output, method = _hide(monkeypatch, tmp_path, "chacha-copy", UnitOrder.KEYED)
    [(opener, candidates)] = methods.for_reveal(output, method)
    groups = [(opener(output, methods.STANDARD), candidates)]

    with pytest.raises(NoHiddenDataError):
        container.reveal(groups, PASSWORD)

    assert container.reveal(groups, PASSWORD, cipher_id="chacha-copy").cipher == "chacha-copy"
