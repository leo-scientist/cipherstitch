"""extension_testing's checks: what they catch, and that they leave the loaded extensions as
they were. The examples pass them in test_examples."""

import os
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image

from cipherstitch import container, extension_loader, extension_testing, service
from cipherstitch.catalog import CarrierKind, CipherId
from cipherstitch.errors import ExtensionError, NoHiddenDataError
from cipherstitch.extensions import Bits, CostSpec, Domain, MethodSpec, Order, Tuning, Units
from cipherstitch.payload import HiddenFile
from tests.extension_fixtures.costs.ones import jpeg_ones
from tests.extension_fixtures.parts import CIPHER, COST, METHOD, ChaChaCopy
from tests.extension_fixtures.working.byte_lsb import ByteChannel, EmbeddedBytes

from .conftest import ImageFactory, use_extensions


class Forgetful(ByteChannel):
    """Writes nothing."""

    def _written(self, writes: Sequence[tuple[Units, Bits]]) -> EmbeddedBytes:
        return EmbeddedBytes(self._values.tobytes())


class Greedy(ByteChannel):
    """Claims a byte more than it holds."""

    def __init__(self, path: Path, tuning: Tuning) -> None:
        super().__init__(path, tuning)
        self.unit_count += 8


def _spec(channel: type[ByteChannel], order: Order = Order.SEQUENTIAL) -> MethodSpec:
    return MethodSpec("checked", 220, "Checked", CarrierKind.DOCUMENT, (".fxb",), channel, order)


@pytest.fixture
def cover(tmp_path: Path) -> Path:
    path = tmp_path / "cover.fxb"
    path.write_bytes(os.urandom(2048))

    return path


@pytest.mark.parametrize("order", list(Order))
def test_a_working_method_passes(cover: Path, order: Order) -> None:
    extension_testing.check_method(_spec(ByteChannel, order), cover)


def test_a_working_cipher_passes() -> None:
    extension_testing.check_cipher(replace(CIPHER, cipher=ChaChaCopy()))


def test_the_rules_come_first(cover: Path) -> None:
    with pytest.raises(AssertionError, match=r"breaks the rules:\nmethod 'rule-method': the wire"):
        extension_testing.check_method(replace(METHOD, wire_id=999), cover)

    with pytest.raises(AssertionError, match=r"breaks the rules:\ncipher 'fernet': the id fernet"):
        extension_testing.check_cipher(replace(CIPHER, id="fernet"))


def test_a_payload_that_doesnt_come_back_is_caught(cover: Path) -> None:
    with pytest.raises(AssertionError, match=r"^Revealing a plain payload failed: Couldn't reveal"):
        extension_testing.check_method(_spec(Forgetful), cover)


def test_a_capacity_the_channel_cant_hold_is_caught(cover: Path) -> None:
    message = (
        r"^Hiding a plain payload of \d+ bytes failed: The extension test_extension_testing "
        r"failed: ValueError"
    )

    with pytest.raises(AssertionError, match=message):
        extension_testing.check_method(_spec(Greedy), cover)


def test_a_cover_too_small_is_named(tmp_path: Path) -> None:
    small = tmp_path / "small.fxb"
    small.write_bytes(bytes(200))

    with pytest.raises(AssertionError, match=r"^Hiding a plain payload failed: small\.fxb is too"):
        extension_testing.check_method(_spec(ByteChannel), small)


def test_one_byte_more_must_be_refused(monkeypatch: pytest.MonkeyPatch, cover: Path) -> None:
    def one_less(*args: object) -> int:
        return container.max_data_size(*args) - 1  # type: ignore[arg-type]

    monkeypatch.setattr(extension_testing, "container", SimpleNamespace(max_data_size=one_less))

    with pytest.raises(AssertionError, match=r"of \d+ bytes raised no CapacityError$"):
        extension_testing.check_method(_spec(ByteChannel), cover)


def test_a_cover_that_already_holds_a_payload_is_caught(
    monkeypatch: pytest.MonkeyPatch, cover: Path, tmp_path: Path
) -> None:
    loaded = use_extensions(monkeypatch, "working")
    embedded = tmp_path / "embedded.fxb"
    options = service.HideOptions(CarrierKind.DOCUMENT, "byte-lsb", CipherId.NONE, is_research=True)
    service.hide(cover, HiddenFile("a.txt", b"already here"), embedded, options)
    spec = replace(_spec(ByteChannel), id="byte-lsb", wire_id=200)

    with pytest.raises(AssertionError, match=r"^Revealing the untouched cover with byte-lsb found"):
        extension_testing.check_method(spec, embedded)

    assert extension_loader.load() is loaded  # the folder's extensions are back


def test_the_checked_spec_is_loaded_alone(monkeypatch: pytest.MonkeyPatch, cover: Path) -> None:
    use_extensions(monkeypatch, "working")
    seen: list[tuple[str, ...]] = []
    reveal = service.reveal

    def spy(*args: object, **kwargs: object) -> service.RevealResult:
        seen.append(tuple(m.entry.id for m in extension_loader.load().methods))

        return reveal(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "reveal", spy)
    extension_testing.check_method(_spec(ByteChannel), cover)

    assert set(seen) == {("checked",)}
    assert extension_loader.find_method("checked") is None


def test_other_bytes_are_caught(monkeypatch: pytest.MonkeyPatch, cover: Path) -> None:
    reveal = service.reveal

    def altered(*args: object, **kwargs: object) -> service.RevealResult:
        result = reveal(*args, **kwargs)  # type: ignore[arg-type]
        result.path.write_bytes(b"other")

        return result

    monkeypatch.setattr(service, "reveal", altered)

    with pytest.raises(AssertionError, match=r"^Revealing a plain payload gave back other bytes"):
        extension_testing.check_method(_spec(ByteChannel), cover)


def test_a_failure_to_find_nothing_is_reported(
    monkeypatch: pytest.MonkeyPatch, cover: Path
) -> None:
    reveal = service.reveal

    def fails_on_the_cover(path: Path, *args: object, **kwargs: object) -> service.RevealResult:
        if path == cover:
            raise ExtensionError("checked", "ValueError: boom")

        return reveal(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "reveal", fails_on_the_cover)
    message = r"^Revealing the untouched cover with checked failed: The extension checked failed"

    with pytest.raises(AssertionError, match=message):
        extension_testing.check_method(_spec(ByteChannel), cover)


def test_a_working_cost_function_passes(make_image: ImageFactory) -> None:
    extension_testing.check_cost(COST, make_image())


@pytest.mark.parametrize("suffix", [".jpg", ".png"])
def test_a_working_jpeg_cost_function_passes(tmp_path: Path, suffix: str) -> None:
    """A PNG is a precover, which DCT compresses first."""
    pytest.importorskip("jpeglib")
    rng = np.random.default_rng(seed=2)
    cover = tmp_path / f"cover{suffix}"
    Image.fromarray(rng.integers(0, 256, size=(96, 128, 3), dtype=np.uint8)).save(cover)
    extension_testing.check_cost(CostSpec("ones", "Ones", Domain.JPEG, jpeg_ones), cover)


def _nan(colors: npt.NDArray[np.uint8], rate: float) -> npt.NDArray[np.float32]:
    return np.full(colors.shape, np.nan, dtype=np.float32)


def test_a_cost_function_is_checked_by_the_rules_then_on_the_cover(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    cover = make_image()

    with pytest.raises(AssertionError, match=r"breaks the rules:\ncost function 'hill': the id"):
        extension_testing.check_cost(replace(COST, id="hill"), cover)

    nan = (
        r"^Computing the costs of cover\.png failed: The extension test_extension_testing "
        r"failed: its costs hold NaN$"
    )

    with pytest.raises(AssertionError, match=nan):
        extension_testing.check_cost(replace(COST, costs=_nan), cover)

    text = tmp_path / "cover.txt"
    text.write_text("not a picture", encoding="utf-8")

    with pytest.raises(AssertionError, match=r"^Computing the costs of cover\.txt failed: "):
        extension_testing.check_cost(COST, text)


@pytest.mark.parametrize(
    ("finds", "message"),
    [
        (False, r"in Standard mode failed: No hidden data found"),
        (True, r"in Standard mode gave back other bytes$"),
    ],
)
def test_a_standard_reveal_that_misses_the_payload_is_caught(
    monkeypatch: pytest.MonkeyPatch, make_image: ImageFactory, finds: bool, message: str
) -> None:
    reveal = service.reveal

    def research_only(*args: object, **kwargs: object) -> service.RevealResult:
        if kwargs.get("research") is not None:
            return reveal(*args, **kwargs)  # type: ignore[arg-type]

        if not finds:
            raise NoHiddenDataError("No hidden data found")

        result = reveal(*args, **kwargs)  # type: ignore[arg-type]
        result.path.write_bytes(b"other")

        return result

    monkeypatch.setattr(service, "reveal", research_only)

    with pytest.raises(AssertionError, match=f"^Revealing a payload encrypted with .*{message}"):
        extension_testing.check_cost(COST, make_image())
