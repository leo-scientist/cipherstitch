import errno
import os
import re
import signal
import sys
import wave
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO, Any

import numpy as np
import pytest
from PIL import Image

from cipherstitch import methods, payload, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.errors import (
    CapacityError,
    CipherStitchError,
    InvalidFileNameError,
    NoHiddenDataError,
    NotAvailableError,
    UnsupportedFormatError,
)
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile

from .conftest import ImageFactory, skip_unless_method_installed

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
PLAIN = service.HideOptions(cipher=CipherId.NONE, is_research=True)


@pytest.mark.parametrize("suffix", [".png", ".bmp", ".tif", ".tiff", ".PNG"])
@pytest.mark.parametrize("password", [None, PASSWORD])
def test_hide_and_reveal_roundtrip(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    suffix: str,
    password: str | None,
) -> None:
    embedded = tmp_path / f"embedded{suffix}"
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    service.hide_file(make_image(), secret_file, embedded, password, research=True)
    revealed = service.reveal_file(embedded, out_dir, password)

    assert revealed == out_dir / secret_file.name
    assert revealed.read_bytes() == secret_file.read_bytes()


def test_a_jpeg_cover_needs_lossy_covers_allowed_for_lsb(
    make_image: ImageFactory, secret_file: Path, tmp_path: Path
) -> None:
    skip_unless_method_installed(MethodId.DCT)  # Research's default for a JPEG, which writes .jpg
    cover, embedded = make_image("cover.jpg"), tmp_path / "embedded.png"
    secret = HiddenFile(secret_file.name, secret_file.read_bytes())
    lossy = Settings.of({"limg.lossy": True})
    options = service.HideOptions(cipher=CipherId.NONE, is_research=True, settings=lossy)

    with pytest.raises(UnsupportedFormatError, match="Output must be one of"):
        service.hide_file(cover, secret_file, embedded, research=True)

    service.hide(cover, secret, embedded, options)

    assert service.reveal_file(embedded, tmp_path / "out").read_bytes() == secret.data


def test_unicode_file_name_and_file_over_32_kib(make_image: ImageFactory, tmp_path: Path) -> None:
    """Lengths beyond 15 bits (files over 32 KiB) and non-ASCII names round-trip."""
    secret = tmp_path / "Звіт 2026.bin"
    secret.write_bytes(bytes(range(256)) * 160)
    embedded = tmp_path / "embedded.png"
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    service.hide_file(make_image(size=(480, 360)), secret, embedded, research=True)

    assert service.reveal_file(embedded, out_dir).read_bytes() == secret.read_bytes()


def test_rejects_lossy_output(make_image: ImageFactory, secret_file: Path, tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFormatError):
        service.hide_file(
            make_image("cover.bmp"), secret_file, tmp_path / "embedded.jpg", research=True
        )


def test_rejects_file_that_does_not_fit(make_image: ImageFactory, tmp_path: Path) -> None:
    cover = make_image(size=(8, 8))
    secret = tmp_path / "big.bin"
    secret.write_bytes(bytes(service.capacity(cover, research=True)))

    with pytest.raises(CapacityError):
        service.hide_file(cover, secret, tmp_path / "embedded.png", research=True)


@pytest.mark.parametrize("cipher", [CipherId.AES_GCM, CipherId.NONE])
def test_a_carrier_smaller_than_the_framing_is_over_full(
    make_image: ImageFactory, tmp_path: Path, cipher: CipherId
) -> None:
    """Even an empty file does not fit: the estimate says so, and hiding names the carrier
    and the method rather than sizes the user never chose. Nearly all of this picture is
    flat, which leaves the capacity a few bytes."""
    cover = make_image(size=(32, 32))

    with Image.open(cover) as image:
        pixels = np.asarray(image).copy()

    pixels[2:] = 90
    Image.fromarray(pixels).save(cover)
    options = service.HideOptions(cipher=cipher, password=PASSWORD, is_research=True)
    estimate = service.estimate(cover, "a.txt", 0, options)

    assert estimate.available
    assert estimate.share is not None
    assert estimate.share > 1
    assert estimate.excess == estimate.required - estimate.available > 0

    with pytest.raises(CapacityError) as error:
        service.hide(cover, HiddenFile("a.txt", b""), tmp_path / "out.png", options)

    assert str(error.value) == "cover.png is too small to hide a file with LSB"


def test_reveal_makes_the_output_folder(
    make_image: ImageFactory, secret_file: Path, tmp_path: Path
) -> None:
    embedded = tmp_path / "embedded.png"
    service.hide_file(make_image(), secret_file, embedded, research=True)

    revealed = service.reveal_file(embedded, tmp_path / "out" / "notes")

    assert revealed == tmp_path / "out" / "notes" / secret_file.name
    assert revealed.read_bytes() == secret_file.read_bytes()


class _FullDisk:
    """A file whose disk fills up after its first bytes."""

    def __init__(self, file: IO[Any]) -> None:
        self._file = file

    def __enter__(self) -> "_FullDisk":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._file.close()

    def write(self, data: bytes) -> int:
        self._file.write(data[:10])
        raise OSError(errno.ENOSPC, "No space left on device")


def test_a_failed_reveal_leaves_no_partial_file(
    make_image: ImageFactory, secret_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedded = tmp_path / "embedded.png"
    service.hide_file(make_image(), secret_file, embedded, research=True)
    output_dir = tmp_path / "out"
    opened = Path.open

    def open_on_a_full_disk(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        file = opened(path, mode, *args, **kwargs)

        return _FullDisk(file) if mode == "xb" else file

    monkeypatch.setattr(Path, "open", open_on_a_full_disk)

    with pytest.raises(OSError, match="No space left"):
        service.reveal_file(embedded, output_dir)

    assert list(output_dir.iterdir()) == []


def test_hide_writes_an_output_name_near_the_length_limit(
    make_image: ImageFactory, secret_file: Path, tmp_path: Path
) -> None:
    output = tmp_path / f"{'a' * 240}.png"
    service.hide_file(make_image(), secret_file, output, research=True)

    assert service.reveal_file(output, tmp_path / "out").read_bytes() == secret_file.read_bytes()


@pytest.mark.parametrize("is_relative", [False, True])
def test_reveal_never_overwrites(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    is_relative: bool,
) -> None:
    embedded = tmp_path / "embedded.png"
    service.hide_file(make_image(), secret_file, embedded, research=True)
    secret_file.write_bytes(b"newer")
    monkeypatch.chdir(tmp_path)
    output_dir = Path() if is_relative else tmp_path
    message = f"{secret_file.name} already exists in {tmp_path}. Choose another folder"

    with pytest.raises(CipherStitchError, match=re.escape(message)):
        service.reveal_file(embedded, output_dir)

    assert secret_file.read_bytes() == b"newer"


def test_hide_refuses_a_name_that_is_not_utf8(make_image: ImageFactory, tmp_path: Path) -> None:
    cover = make_image()
    output = tmp_path / "embedded.png"
    name = (b"caf" + bytes([0xE9]) + b".txt").decode("utf-8", "surrogateescape")  # as on Linux
    options = service.hide_options(cover, output, CipherId.NONE, is_research=True)

    with pytest.raises(InvalidFileNameError):
        service.estimate(cover, name, 4, options)

    with pytest.raises(InvalidFileNameError):
        service.hide(cover, HiddenFile(name, b"data"), output, options)

    assert not output.exists()


def test_reveal_from_clean_image(make_image: ImageFactory, tmp_path: Path) -> None:
    with pytest.raises(NoHiddenDataError):
        service.reveal_file(make_image(), tmp_path)


def test_reveal_with_wrong_password(
    make_image: ImageFactory, secret_file: Path, tmp_path: Path
) -> None:
    embedded = tmp_path / "embedded.png"
    service.hide_file(make_image(), secret_file, embedded, PASSWORD, research=True)

    with pytest.raises(NoHiddenDataError):
        service.reveal_file(embedded, tmp_path, "guess")


def test_text_carrier_roundtrip_with_default_method(secret_file: Path, tmp_path: Path) -> None:
    cover = tmp_path / "cover.txt"
    cover.write_text("Every word here can carry two bits. " * 400, encoding="utf-8")
    embedded = tmp_path / "embedded.txt"
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    service.hide_file(cover, secret_file, embedded, PASSWORD, research=True)

    assert service.capacity(cover, research=True) > len(secret_file.read_bytes())
    assert service.reveal_file(embedded, out_dir, PASSWORD).read_bytes() == secret_file.read_bytes()


def test_hide_file_needs_a_supported_carrier(secret_file: Path, tmp_path: Path) -> None:
    (tmp_path / "archive.zip").write_bytes(b"data")

    with pytest.raises(UnsupportedFormatError):
        service.hide_file(
            tmp_path / "archive.zip", secret_file, tmp_path / "out.png", research=True
        )


def test_reveal_rejects_formats_no_method_writes(make_image: ImageFactory, tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFormatError, match="can't carry a payload"):
        service.reveal_file(make_image("photo.webp"), tmp_path)


def _noise_wav(path: Path) -> Path:
    samples = np.random.default_rng(seed=0).integers(-3000, 3000, size=48_000, dtype=np.int16)

    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(8000)
        file.writeframes(samples.tobytes())

    return path


def test_a_reveal_that_finds_nothing_names_the_extras_it_could_not_try(
    secret_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without reedsolo, audio LSB still reads a WAV, but phase coding and echo hiding can't:
    either may hold the payload."""
    cover = _noise_wav(tmp_path / "song.wav")
    embedded = tmp_path / "embedded.wav"
    service.hide_file(cover, secret_file, embedded, research=True)
    monkeypatch.setitem(sys.modules, "reedsolo", None)  # its import fails
    hint = "Phase coding or echo hiding needs the ecc extra, see Installation in README.md"

    with pytest.raises(NoHiddenDataError) as error:
        service.reveal_file(cover, tmp_path / "out")

    assert str(error.value) == f"{payload.NOT_FOUND_MESSAGE}. {hint}"
    assert service.reveal_file(embedded, tmp_path / "out").name == secret_file.name


def test_a_method_the_registry_lacks_is_named(
    make_image: ImageFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "unavailable_reason", lambda *_args: None)
    monkeypatch.setattr(methods, "get", lambda _method: None)
    options = service.HideOptions(method=MethodId.LSB)

    with pytest.raises(NotAvailableError, match=r"^The method lsb is not available$"):
        service.hide(make_image(), HiddenFile("a.txt", b"a"), tmp_path / "out.png", options)


def test_hide_options_refuse_a_missing_output_folder(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    """Saving would fail only after the password, the key derivation and the embedding."""
    output = tmp_path / "nodir" / "out.png"

    with pytest.raises(CipherStitchError, match="there is no such folder"):
        service.hide_options(make_image(), output, CipherId.NONE, is_research=True)


@pytest.fixture
def fifo(tmp_path: Path) -> Iterator[Path]:
    """A named pipe named like a cover. Opening it to read blocks until something writes to
    it, so the alarm fails a test that reaches an opener instead of hanging the run."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("no named pipes on this platform")

    path = tmp_path / "cover.png"
    os.mkfifo(path)

    def opened(_signal: int, _frame: object) -> None:
        raise TimeoutError("something opened the pipe")

    previous = signal.signal(signal.SIGALRM, opened)
    signal.alarm(5)

    try:
        yield path
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.parametrize(
    "entry_point",
    [
        lambda p: service.describe_carrier(p, CarrierKind.IMAGE),
        lambda p: service.estimate(p, "a.txt", 10, PLAIN),
        lambda p: service.detection_risks(PLAIN, p),
        lambda p: service.hide_options(p, p.with_name("out.png"), CipherId.NONE, is_research=True),
        lambda p: service.hide(p, HiddenFile("a.txt", b"a"), p.with_name("out.png"), PLAIN),
        lambda p: service.reveal(p, p.parent),
        lambda p: service.mode_capacity(p),
        lambda p: service.capacity(p, research=True),
    ],
    ids=[
        "describe_carrier",
        "estimate",
        "detection_risks",
        "hide_options",
        "hide",
        "reveal",
        "mode_capacity",
        "capacity",
    ],
)
def test_a_carrier_that_is_not_a_regular_file_is_refused_before_any_opener_runs(
    fifo: Path, entry_point: Callable[[Path], object]
) -> None:
    with pytest.raises(UnsupportedFormatError, match=r"^cover\.png is not a regular file$"):
        entry_point(fifo)


def test_the_reasons_name_a_carrier_that_is_not_a_regular_file(fifo: Path) -> None:
    reason = "cover.png is not a regular file"

    assert service.unavailable_reason(service.HideOptions(), fifo) == reason
    assert service.unavailable_methods(CarrierKind.IMAGE, fifo)[MethodId.LSB] == reason


def test_a_folder_is_refused_as_a_carrier_and_a_missing_file_is_still_missing(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "cover.png"
    folder.mkdir()

    with pytest.raises(UnsupportedFormatError, match=r"^cover\.png is a folder, not a file$"):
        service.describe_carrier(folder, CarrierKind.IMAGE)

    with pytest.raises(UnsupportedFormatError, match=r"^cover\.png is a folder, not a file$"):
        service.mode_capacity(folder)

    with pytest.raises(FileNotFoundError):
        service.describe_carrier(tmp_path / "gone.png", CarrierKind.IMAGE)

    with pytest.raises(FileNotFoundError):
        service.mode_capacity(tmp_path / "gone.png")


def test_the_file_to_hide_must_be_a_regular_file(
    make_image: ImageFactory, tmp_path: Path, fifo: Path
) -> None:
    cover, output = make_image("photo.png"), tmp_path / "out.png"
    folder = tmp_path / "notes"
    folder.mkdir()

    with pytest.raises(CipherStitchError, match=r"^notes is a folder, not a file$"):
        service.hide_file(cover, folder, output, research=True)

    with pytest.raises(CipherStitchError, match=r"^cover\.png is not a regular file$"):
        service.read_payload(fifo)

    assert not output.exists()


def test_read_payload_checks_the_name_before_reading(tmp_path: Path) -> None:
    secret = tmp_path / ".zshenv"
    secret.write_text("echo hello\n", encoding="utf-8")

    with pytest.raises(InvalidFileNameError, match=r"\. Rename the file and hide it again$"):
        service.read_payload(secret)

    note = tmp_path / "note.txt"
    note.write_bytes(b"hello")

    assert service.read_payload(note) == HiddenFile("note.txt", b"hello")


def test_an_output_path_that_is_a_folder_is_refused_before_any_work(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    output = tmp_path / "out.png"
    output.mkdir()
    message = r"^Can't save as out\.png: there is a folder of that name$"

    with pytest.raises(CipherStitchError, match=message):
        service.hide_options(make_image(), output, CipherId.NONE, is_research=True)


@pytest.mark.parametrize("below", ["", "out"], ids=["the-folder", "its-parent"])
def test_reveal_names_a_file_where_the_output_folder_should_be(
    make_image: ImageFactory, secret_file: Path, tmp_path: Path, below: str
) -> None:
    embedded = tmp_path / "embedded.png"
    service.hide_file(make_image(), secret_file, embedded, research=True)
    message = rf"^{re.escape(str(secret_file))} is a file, not a folder$"

    with pytest.raises(CipherStitchError, match=message):
        service.reveal_file(embedded, secret_file / below)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
def test_hide_replaces_the_target_of_a_symlinked_output_and_keeps_the_link(
    make_image: ImageFactory, secret_file: Path, tmp_path: Path
) -> None:
    target = tmp_path / "target.png"
    target.write_bytes(b"old")
    link = tmp_path / "link.png"
    link.symlink_to(target)

    service.hide_file(make_image(), secret_file, link, research=True)

    assert link.is_symlink()
    assert link.resolve() == target.resolve()
    assert target.read_bytes() != b"old"
    assert service.reveal_file(target, tmp_path / "out").read_bytes() == secret_file.read_bytes()
    assert not list(tmp_path.glob("*.partial*"))


@pytest.mark.parametrize("research", [False, True], ids=["standard", "research"])
def test_hide_file_refuses_an_empty_password(
    make_image: ImageFactory, secret_file: Path, tmp_path: Path, research: bool
) -> None:
    """An empty password is one, not the absence of one: it meets the strength check."""
    output = tmp_path / "out.png"

    with pytest.raises(CipherStitchError, match="strong password"):
        service.hide_file(make_image(), secret_file, output, "", research=research)

    assert not output.exists()


def test_a_rewrite_that_keeps_the_size_and_the_mtime_is_read_again(tmp_path: Path) -> None:
    """``cp -p`` and ``rsync -t`` keep the mtime. A BMP's size follows its dimensions alone,
    and a flat picture has no room, a noisy one plenty."""
    cover = tmp_path / "cover.bmp"
    Image.fromarray(np.full((64, 64, 3), 128, np.uint8)).save(cover)
    before = cover.stat()
    flat = service.estimate(cover, "a", 0, PLAIN).available

    noise = np.random.default_rng(seed=0).integers(0, 256, (64, 64, 3), dtype=np.uint8)
    Image.fromarray(noise).save(cover)
    os.utime(cover, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = cover.stat()

    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)

    noisy = service.estimate(cover, "a", 0, PLAIN).available

    assert flat is not None
    assert noisy is not None
    assert noisy > flat
